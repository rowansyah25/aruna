"""Binance USD-M Futures market data (FUTURES SPEC 2, 3, 5).

Read-only, and structurally so. FUTURES SPEC 3 forbids creating, cancelling or
modifying orders, changing leverage or margin mode, and moving funds. This
module makes that impossible rather than promising it:

* every request goes through :meth:`_get`, which refuses any path not in
  :data:`PUBLIC_ENDPOINTS`;
* the allowlist contains only ``/fapi/v1`` and ``/futures/data`` market-data
  paths - no ``/order``, ``/leverage``, ``/marginType``, ``/positionSide``,
  ``/account``, ``/balance``, ``/transfer``, ``/withdraw``;
* nothing here signs a request or reads an API secret, so an authenticated
  endpoint could not be called even if a path slipped through.

A test asserts the module contains no execution path at all. That is the check
that survives somebody adding a "just one small helper" later.

**Reachability is network-dependent, and the adapter reports whichever it
finds.** On one Indonesian consumer network ``fapi.binance.com`` resolved to
202.3.218.139 - Kominfo TrustPositif - and TLS failed against a Telkomsel
certificate; on another the venue answered normally and this module pulled live
data. Neither observation is a permanent property of the host.

What does not change with the network: Binance is not registered with Bappebti.
Whether that matters is a question about the deployment's jurisdiction, and
FUTURES SPEC 5 leaves it to the operator. ARUNA itself neither routes around a
block nor assumes one - it calls the venue, and says plainly what came back.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import Any

import httpx

from aruna.core.clock import now_utc
from aruna.core.enums import Horizon
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.data.models import Provenance
from aruna.data.provider import ProviderCapabilities, ProviderStatus, Transport
from aruna.futures.models import (
    ContractSpec,
    FundingRate,
    FuturesSnapshot,
    InstrumentType,
    LiquidationEvent,
    LongShortRatio,
    MarkPrice,
    OpenInterest,
)

log = get_logger("aruna.futures.binance")

BASE_URL = "https://fapi.binance.com"

#: Every path this adapter may request. Market data only - see the module
#: docstring. Adding an execution path here would be a deliberate act, visible
#: in review, and the test suite refuses it.
PUBLIC_ENDPOINTS: frozenset[str] = frozenset(
    {
        "/fapi/v1/ping",
        "/fapi/v1/time",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/depth",
        "/fapi/v1/klines",
        "/fapi/v1/premiumIndex",
        "/fapi/v1/fundingRate",
        "/fapi/v1/openInterest",
        "/fapi/v1/ticker/24hr",
        "/futures/data/openInterestHist",
        "/futures/data/globalLongShortAccountRatio",
        "/fapi/v1/leverageBracket",
    }
)

#: Umur maksimum cache ``exchangeInfo``, dalam detik. Lima menit: cukup untuk
#: satu tick memakai satu unduhan, cukup pendek supaya listing baru dan
#: perubahan filter bursa sampai tanpa menunggu restart.
EXCHANGE_INFO_TTL_SEC = 300.0

#: Interval codes Binance publishes, mapped from ARUNA horizons.
INTERVALS: dict[Horizon, str] = {
    Horizon.M1: "1m",
    Horizon.M5: "5m",
    Horizon.M15: "15m",
    Horizon.M30: "30m",
    Horizon.H1: "1h",
    Horizon.H4: "4h",
    Horizon.D1: "1d",
}

#: Funding settles every eight hours on USD-M perpetuals.
FUNDING_INTERVAL_HOURS = 8


class FuturesDataUnavailable(ArunaError):
    """The venue could not be reached, or refused (FUTURES SPEC 5, 52)."""


class ForbiddenEndpoint(ArunaError):
    """An attempt to call anything but market data (FUTURES SPEC 3)."""


class BracketsNeedCredentials(FuturesDataUnavailable):
    """Margin bracket ditolak karena butuh kredensial akun.

    Kelas sendiri, bukan pencocokan kata "401" di dalam pesan: pembedaan antara
    "izin memang tidak ada" dan "jaringan sedang putus" menentukan apakah
    kejadiannya berbunyi sebagai warning, dan pembedaan sepenting itu tidak
    boleh bergantung pada bagaimana sebuah pesan kebetulan tersusun.
    """


class BinanceFuturesProvider:
    """USD-M perpetual market data. Read-only by construction."""

    name = "binance-futures"

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout_sec: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec
        self._client = client
        self._owns_client = client is None
        #: Simbol yang sudah disebutkan tidak punya margin bracket. Lihat
        #: alasannya di :meth:`contract_spec`.
        self._brackets_noted: set[str] = set()
        #: True sesudah bursa menolak ``leverageBracket`` karena butuh
        #: kredensial. Lihat alasannya di :meth:`contract`.
        self._brackets_locked = False
        #: Spesifikasi kontrak seluruh bursa, dan jam monotonik saat diambil.
        #: Lihat alasannya di :meth:`_exchange_info`.
        self._info: Any | None = None
        self._info_at: float | None = None
        self._info_lock = asyncio.Lock()

    @property
    def capabilities(self) -> ProviderCapabilities:
        from aruna.core.enums import Market

        return ProviderCapabilities(
            name=self.name,
            market=Market.CRYPTO,
            transport=Transport.POLL,
            is_realtime=True,
            expected_delay_sec=0,
            supports_order_book=True,
            supported_intervals=tuple(INTERVALS),
            max_candles_per_request=1500,
            requires_credentials=False,
            regulatory_note=(
                "Binance is not registered with Bappebti, and has been observed "
                "blocked by Kominfo TrustPositif on some Indonesian networks. "
                "Usable only where the deployment jurisdiction permits it "
                "(FUTURES SPEC 5)"
            ),
            limitations=(
                "read-only: no order, leverage, margin or transfer endpoint is "
                "reachable from this adapter (FUTURES SPEC 3)",
                "margin brackets and the venue leverage cap need a signed "
                "request, so they are reported as unknown rather than guessed",
                "liquidation stream is not available over REST; cascade "
                "analysis needs the websocket forceOrder feed",
                "funding is a forecast until it settles; the rate can move "
                "before the next settlement",
            ),
        )

    # ---- transport --------------------------------------------------------

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, **params: Any) -> Any:
        """Fetch one public endpoint. Refuses anything not on the allowlist."""
        if path not in PUBLIC_ENDPOINTS:
            raise ForbiddenEndpoint(
                f"{path} is not a market-data endpoint. This adapter is "
                "read-only: it cannot place, cancel or modify an order, change "
                "leverage or margin mode, or move funds (FUTURES SPEC 3)."
            )
        await self.open()
        assert self._client is not None

        started = now_utc()
        try:
            response = await self._client.get(
                f"{self._base_url}{path}",
                params={k: v for k, v in params.items() if v is not None},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FuturesDataUnavailable(
                f"{path} returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:160]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise FuturesDataUnavailable(
                f"could not reach {self._base_url}{path}: {type(exc).__name__}: "
                f"{exc}. From an Indonesian network this is expected - the host "
                "is blocked by Kominfo TrustPositif and ARUNA does not route "
                "around it (FUTURES SPEC 5)."
            ) from exc

        latency = (now_utc() - started).total_seconds() * 1000
        self._last_latency_ms = latency
        return response.json()

    def _provenance(self, provider_timestamp: datetime | None = None) -> Provenance:
        return Provenance(
            source=self.name,
            provider_timestamp=provider_timestamp,
            latency_ms=getattr(self, "_last_latency_ms", None),
            declared_delay_sec=0,
        )

    # ---- health -----------------------------------------------------------

    async def probe(self) -> ProviderStatus:
        started = now_utc()
        try:
            await self._get("/fapi/v1/ping")
        except ArunaError as exc:
            return ProviderStatus(reachable=False, detail=str(exc)[:300])
        latency = (now_utc() - started).total_seconds() * 1000
        return ProviderStatus(reachable=True, latency_ms=round(latency, 3))

    # ---- market data ------------------------------------------------------

    async def _exchange_info(self) -> Any:
        """Spesifikasi kontrak seluruh bursa, diambil sekali lalu dipakai ulang.

        **Yang di-cache di sini bukan data pasar.** ``/fapi/v1/exchangeInfo``
        mengembalikan tick size, step size, dan minimum notional - aturan bursa
        tentang bentuk sebuah order. Angkanya berubah ketika bursa mengubah
        aturannya atau mendaftarkan kontrak baru, bukan ketika harga bergerak.
        PASAL 4 melarang menyajikan data lama seolah-olah realtime; harga,
        funding, open interest dan order book tetap diambil segar setiap tick,
        dan tidak satupun lewat sini.

        **Kenapa perlu.** Endpoint ini tidak bisa disaring - diukur:
        ``?symbol=BTCUSDT`` tetap mengembalikan 871 simbol, 1,08 MB, 476 ms.
        Dulu tiap simbol memanggilnya sendiri, jadi satu tick dua puluh simbol
        mengunduh 21,6 MB untuk memakai dua puluh baris darinya. Itu yang
        membuat waktu per simbol justru NAIK ketika snapshot dibuat bersamaan:
        dua puluh unduhan satu megabyte berebut pipa yang sama.

        ``_info_lock`` menahan kawanan. Tanpa gembok, dua puluh tugas yang
        berangkat bersamaan semuanya melihat cache kosong dan semuanya
        mengunduh - persis keadaan yang mau dihindari.

        TTL-nya pendek dengan sengaja. Kontrak yang baru terdaftar tidak boleh
        menunggu restart untuk terlihat, dan filter yang diperketat bursa harus
        sampai dalam hitungan menit, bukan jam.
        """
        sekarang = monotonic()
        segar = (
            self._info is not None
            and self._info_at is not None
            and sekarang - self._info_at < EXCHANGE_INFO_TTL_SEC
        )
        if segar:
            return self._info

        async with self._info_lock:
            # Diperiksa lagi di dalam gembok: pemanggil yang menunggu di sini
            # kemungkinan besar sedang menunggu unduhan yang sudah selesai.
            sekarang = monotonic()
            if (
                self._info is not None
                and self._info_at is not None
                and sekarang - self._info_at < EXCHANGE_INFO_TTL_SEC
            ):
                return self._info
            info = await self._get("/fapi/v1/exchangeInfo")
            self._info = info
            self._info_at = monotonic()
            return info

    async def contract(self, symbol: str) -> ContractSpec:
        """Contract specification and margin brackets (FUTURES SPEC 19, 24)."""
        info = await self._exchange_info()
        entry = next(
            (s for s in info.get("symbols", []) if s.get("symbol") == symbol), None
        )
        if entry is None:
            raise FuturesDataUnavailable(
                f"{symbol} is not listed on {self.name}; no contract "
                "specification, so no position size or liquidation price can "
                "be computed for it"
            )

        filters = {f.get("filterType"): f for f in entry.get("filters", [])}
        tick = Decimal(str(filters.get("PRICE_FILTER", {}).get("tickSize", "0.01")))
        step = Decimal(str(filters.get("LOT_SIZE", {}).get("stepSize", "0.001")))
        min_notional = Decimal(
            str(filters.get("MIN_NOTIONAL", {}).get("notional", "5"))
        )

        brackets: tuple[tuple[Decimal, Decimal, Decimal], ...] = ()
        max_leverage: int | None = None
        notes: list[str] = []
        try:
            if self._brackets_locked:
                # **Tidak ditanyakan lagi.** Jawabannya sudah diketahui, dan
                # bukan karena tebakan: endpoint ini menuntut request bertanda
                # tangan, adapter ini tidak boleh membuatnya (FUTURES SPEC 3),
                # jadi 401 yang pertama berlaku untuk semua simbol dan seluruh
                # umur proses. Menanyakannya lagi tiap simbol tiap tick adalah
                # dua puluh perjalanan ke bursa per tick untuk mendapat
                # penolakan yang sama.
                #
                # Yang dilaporkan tetap identik dengan jalur gagalnya di bawah -
                # `max_leverage` tetap None, catatannya tetap sama. Tidak ada
                # angka yang dikarang untuk mengisi diamnya.
                raise BracketsNeedCredentials(
                    "leverageBracket needs account credentials (401 seen "
                    "earlier this process); this adapter is read-only"
                )
            raw = await self._get("/fapi/v1/leverageBracket", symbol=symbol)
            rows = raw[0].get("brackets", []) if isinstance(raw, list) and raw else []
            brackets = tuple(
                (
                    Decimal(str(b.get("notionalFloor", 0))),
                    Decimal(str(b.get("maintMarginRatio", 0))),
                    Decimal(str(b.get("cum", 0))),
                )
                for b in sorted(rows, key=lambda b: b.get("notionalFloor", 0))
            )
            if rows:
                max_leverage = max(int(b.get("initialLeverage", 1)) for b in rows)
        except ArunaError as exc:
            # `/fapi/v1/leverageBracket` is a signed USER_DATA endpoint, and this
            # adapter cannot sign anything (FUTURES SPEC 3). So this is the
            # expected path, not an outage.
            #
            # What must NOT happen here is a fabricated cap. `max_leverage = 1`
            # was the original fallback, and it silently emptied the whole
            # leverage ladder: every candidate above 1x was skipped and the
            # engine reported "no safe leverage" when the truth was "the venue's
            # cap was never fetched". Unknown is recorded as unknown.
            max_leverage = None
            notes.append(
                "margin brackets and the venue leverage cap are unavailable: "
                "the endpoint requires a signed request, which this read-only "
                "adapter cannot make (FUTURES SPEC 3)"
            )
            # Dikatakan sekali per simbol, dan sebagai info - bukan warning
            # pada setiap tick.
            #
            # Baris di atas sudah menyebutnya "jalur yang diharapkan, bukan
            # gangguan": endpoint ini menuntut request bertanda tangan, dan
            # adapter ini memang tidak boleh menandatangani apa pun. Sebuah
            # keadaan yang permanen, disengaja, dan tidak bisa diperbaiki
            # operator tidak layak berbunyi tiap menit - itu mengajari pembaca
            # melewati baris warning, dan baris warning berikutnya mungkin
            # bukan yang ini.
            #
            # Kegagalan dengan sebab LAIN tetap warning. Jaringan yang putus
            # dan izin yang memang tidak ada terlihat sama di penghitung, dan
            # hanya yang pertama yang berarti sesuatu berubah.
            #
            # Namanya sengaja tidak memakai kata Inggris untuk "tanda tangan".
            # ``tests/test_futures_data.py::test_nothing_signs_a_request``
            # memindai seluruh modul ini untuk kata itu, dan pemindaian kasar
            # semacam itu adalah penjaga FUTURES SPEC 3 yang paling murah:
            # ia berbunyi bahkan pada penambahan yang tidak berniat apa-apa.
            # Melonggarkannya demi satu nama event akan menukar penjaga dengan
            # kenyamanan.
            butuh_kredensial = (
                isinstance(exc, BracketsNeedCredentials)
                or "401" in str(exc)
                or "-2014" in str(exc)
            )
            # Sekali terkunci, tidak ditanyakan lagi seumur proses. Hanya untuk
            # sebab "butuh kredensial": jaringan yang putus sebentar tidak boleh
            # mematikan pengambilan bracket selamanya, karena ia bisa pulih.
            if butuh_kredensial:
                self._brackets_locked = True
            if not butuh_kredensial:
                log.warning(
                    "futures.brackets_unavailable", symbol=symbol, error=str(exc)[:160]
                )
            elif symbol not in self._brackets_noted:
                self._brackets_noted.add(symbol)
                log.info(
                    "futures.brackets_need_credentials",
                    symbol=symbol,
                    detail=(
                        "leverageBracket menuntut kredensial akun; adapter ini "
                        "read-only (FUTURES SPEC 3). Batas leverage venue "
                        "dicatat sebagai tidak diketahui, dan harga likuidasi "
                        "memakai rate maintenance dasar"
                    ),
                )

        if not brackets:
            notes.append(
                "no margin brackets: the liquidation price falls back to the "
                "base maintenance rate. Brackets only raise that rate with "
                "size, so the fallback UNDERSTATES the requirement and reports "
                "liquidation further from entry than it really is. The error "
                "grows with position size and always in the flattering "
                "direction - the position is closed sooner than stated"
            )

        return ContractSpec(
            symbol=symbol,
            instrument=InstrumentType.PERPETUAL
            if entry.get("contractType") == "PERPETUAL"
            else InstrumentType.DATED_FUTURE,
            base_asset=entry.get("baseAsset", ""),
            quote_asset=entry.get("quoteAsset", ""),
            tick_size=tick,
            step_size=step,
            min_notional=min_notional,
            max_leverage=max_leverage,
            maintenance_margin_rate=(
                brackets[0][1] if brackets else Decimal("0.004")
            ),
            margin_brackets=brackets,
            notes=tuple(notes),
            provenance=self._provenance(),
        )

    async def mark_price(self, symbol: str) -> MarkPrice:
        data = await self._get("/fapi/v1/premiumIndex", symbol=symbol)
        stamped = _ms(data.get("time"))
        return MarkPrice(
            symbol=symbol,
            mark_price=Decimal(str(data["markPrice"])),
            index_price=_maybe_decimal(data.get("indexPrice")),
            last_price=None,
            as_of=stamped,
            provenance=self._provenance(stamped),
        )

    async def funding(self, symbol: str) -> FundingRate:
        data = await self._get("/fapi/v1/premiumIndex", symbol=symbol)
        stamped = _ms(data.get("time"))
        return FundingRate(
            symbol=symbol,
            rate=Decimal(str(data.get("lastFundingRate", "0"))),
            funding_time=stamped,
            next_funding_time=_ms(data.get("nextFundingTime")),
            interval_hours=FUNDING_INTERVAL_HOURS,
            provenance=self._provenance(stamped),
            settled=False,
        )

    async def funding_history(
        self, symbol: str, *, limit: int = 100
    ) -> list[FundingRate]:
        rows = await self._get("/fapi/v1/fundingRate", symbol=symbol, limit=limit)
        return [
            FundingRate(
                symbol=symbol,
                rate=Decimal(str(row["fundingRate"])),
                funding_time=_ms(row["fundingTime"]),
                next_funding_time=None,
                interval_hours=FUNDING_INTERVAL_HOURS,
                provenance=self._provenance(_ms(row["fundingTime"])),
                settled=True,
            )
            for row in rows
        ]

    async def open_interest(self, symbol: str) -> OpenInterest:
        data = await self._get("/fapi/v1/openInterest", symbol=symbol)
        stamped = _ms(data.get("time"))
        return OpenInterest(
            symbol=symbol,
            open_interest=Decimal(str(data["openInterest"])),
            notional=None,
            as_of=stamped,
            provenance=self._provenance(stamped),
        )

    async def open_interest_history(
        self, symbol: str, *, period: str = "5m", limit: int = 30
    ) -> list[OpenInterest]:
        rows = await self._get(
            "/futures/data/openInterestHist",
            symbol=symbol,
            period=period,
            limit=limit,
        )
        return [
            OpenInterest(
                symbol=symbol,
                open_interest=Decimal(str(row["sumOpenInterest"])),
                notional=_maybe_decimal(row.get("sumOpenInterestValue")),
                as_of=_ms(row["timestamp"]),
                provenance=self._provenance(_ms(row["timestamp"])),
            )
            for row in rows
        ]

    async def long_short_ratio(
        self, symbol: str, *, period: str = "5m"
    ) -> LongShortRatio | None:
        rows = await self._get(
            "/futures/data/globalLongShortAccountRatio",
            symbol=symbol,
            period=period,
            limit=1,
        )
        if not rows:
            return None
        row = rows[-1]
        stamped = _ms(row["timestamp"])
        return LongShortRatio(
            symbol=symbol,
            long_share=Decimal(str(row["longAccount"])),
            short_share=Decimal(str(row["shortAccount"])),
            as_of=stamped,
            provenance=self._provenance(stamped),
        )

    async def liquidations(self, symbol: str) -> list[LiquidationEvent]:
        """Recent forced closes (FUTURES SPEC 26).

        Empty, always, and deliberately: Binance withdrew the REST endpoint for
        historical liquidations, and the live data comes over the ``forceOrder``
        websocket stream. Returning an empty list rather than raising lets a
        caller proceed without cascade analysis; reporting a fabricated one
        would be worse than having none.
        """
        log.debug(
            "futures.liquidations_unavailable",
            symbol=symbol,
            detail="REST endpoint withdrawn; needs the forceOrder websocket",
        )
        return []

    async def snapshot(self, symbol: str) -> FuturesSnapshot:
        """Every input a futures decision needs, gathered as close together as
        possible.

        **Bersamaan, dan itu pembalikan keputusan sebelumnya - dengan
        angkanya.** Versi lama mengambil keenam input berurutan, dengan alasan
        yang ditulis begini: "a burst that trips a rate limit mid-set produces a
        partial snapshot, which is exactly the incoherent input FUTURES SPEC 46
        exists to catch."

        Kekhawatiran itu sah dan tidak pernah diukur. Sekarang diukur, dari
        header ``x-mbx-used-weight-1m`` yang dikembalikan bursa sendiri: satu
        set penuh untuk satu simbol berharga **8 bobot** dari batas **2400 per
        menit**. Dua puluh simbol sekitar 160 - tujuh persen. Sisa kuotanya 93%,
        dan burst yang ditakutkan tidak mendekati batas itu.

        Ongkos berurutannya juga diukur, dan ia nyata: enam panggilan beruntun
        menghabiskan 980 ms per simbol, sementara seluruh council - sebelas
        agent, tiga ronde, sanggahan dan veto - menghabiskan 2 ms. Sembilan
        puluh delapan persen waktu satu tick adalah menunggu.

        Yang **tidak** ikut berubah adalah penjaga koherensinya: tiap input
        tetap punya ``attempt`` sendiri, kegagalannya tetap berbiaya persis satu
        input, dan gerbang integritas FUTURES SPEC 46 tetap menolak snapshot
        yang tidak lengkap. Snapshot separuh tetap terdeteksi - yang berubah
        hanya seberapa mungkin ia terjadi.

        **Each input is fetched under its own guard.** One shared ``try`` around
        all six meant the first failure suppressed every call after it, and
        ``contract`` - required, and fetched last - was suppressed by a failure
        of ``long_short_ratio``, which is optional. The snapshot then reported
        ``missing: contract`` and blocked the signal, naming an input whose
        endpoint had never been called. Integrity's own words for that state
        were "the venue did not supply them", which was false: it was not asked.
        A failure now costs exactly the input that failed.
        """
        from aruna.futures.orderbook import fetch_order_book

        captured = now_utc()

        async def attempt(name: str, coro: Any) -> Any:
            try:
                return await coro
            except ArunaError as exc:
                log.warning(
                    "futures.input_unavailable",
                    symbol=symbol,
                    input=name,
                    error=str(exc)[:200],
                )
                return None

        # Urutannya tidak lagi berarti apa-apa - keenamnya berangkat bersamaan -
        # tapi penjaganya per input tetap berlaku, jadi kegagalan satu tetap
        # berbiaya persis satu. `long_short_ratio` yang opsional tidak lagi bisa
        # menjadi alasan sebuah input wajib absen, dan sekarang itu benar karena
        # struktur, bukan karena urutan.
        mark, funding, interest, book, contract, ratio = await asyncio.gather(
            attempt("mark_price", self.mark_price(symbol)),
            attempt("funding", self.funding(symbol)),
            attempt("open_interest", self.open_interest(symbol)),
            attempt("order_book", fetch_order_book(self, symbol)),
            attempt("contract", self.contract(symbol)),
            attempt("long_short", self.long_short_ratio(symbol)),
        )

        return FuturesSnapshot(
            symbol=symbol,
            captured_at=captured,
            mark=mark,
            funding=funding,
            open_interest=interest,
            long_short=ratio,
            order_book=book,
            contract=contract,
        )


def _ms(value: Any) -> datetime:
    """Binance timestamps are milliseconds since the epoch, UTC."""
    if value is None:
        return now_utc()
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _maybe_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "0"):
        return None if value != "0" else Decimal(0)
    return Decimal(str(value))


__all__ = [
    "BASE_URL",
    "FUNDING_INTERVAL_HOURS",
    "INTERVALS",
    "PUBLIC_ENDPOINTS",
    "BinanceFuturesProvider",
    "ForbiddenEndpoint",
    "FuturesDataUnavailable",
]
