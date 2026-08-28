"""Twelve Data untuk XAU/USD, M5 saja.

**Kenapa cuma M5.**  ``MarketDataProvider.fetch_candles`` mewajibkan adapter
menolak interval yang tidak didukung, karena resampling adalah keputusan
pemanggil yang harus eksplisit.  M15, H1, dan H4 dirakit di
``aruna.xau.timeframes`` dari bar M5 yang sama - nol kredit tambahan, dan
mustahil ada dua timeframe yang tidak sinkron karena semuanya satu sumber.
Meminta H1 langsung ke venue akan membuka kebocoran yang tidak terlihat
seperti kebocoran: bar H1 yang ditarik sedetik lebih lambat bisa memuat
pergerakan yang belum ada di M5 saat keputusan diambil.

**Volume selalu nol.**  Valas spot tidak menerbitkan volume, dan Twelve Data
tidak mengarangnya.  ``Candle.volume`` wajib terisi, jadi nilainya ``0`` - dan
keterbatasan itu dinyatakan di ``capabilities.limitations`` supaya fitur apa
pun yang membacanya ketahuan salah sejak awal.  ``quote_volume`` dan
``trade_count`` opsional, jadi keduanya ``None``: tidak diukur, bukan nol.

**429 tidak diulang.**  Adapter ini yang memiliki kebijakan kredit, jadi 429
pertama harus sampai utuh ke :class:`KreditHarian` alih-alih dihabiskan oleh
tiga percobaan ulang ke dalam rate limit yang sedang aktif.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from aruna.core.clock import now_utc
from aruna.core.config import DataSettings
from aruna.core.enums import DataQuality, Horizon, Market
from aruna.core.errors import DataSourceUnavailableError
from aruna.data.forex.budget import KreditHarian
from aruna.data.http import RETRY_STATUSES, HttpFetcher
from aruna.data.models import Candle, Provenance, Quote, Snapshot
from aruna.data.provider import (
    MarketDataProvider,
    ProviderCapabilities,
    ProviderStatus,
    Transport,
)

#: Nama adapter.  Sama dengan kunci di ``registry.PROVIDERS`` dan dengan apa
#: yang ditulis ke kolom provenance, supaya baris data bisa ditelusuri balik
#: ke adapter yang menulisnya.
SOURCE = "twelvedata"

BASE_URL = "https://api.twelvedata.com"

#: Satu-satunya interval yang diminta dari venue.
_INTERVAL_VENUE = "5min"

#: Bar per permintaan.  Batas endpoint adalah 5000 dan satu permintaan tetap
#: berharga satu kredit berapa pun isinya, jadi meminta kurang dari maksimum
#: membuang jatah tanpa menghemat apa pun.
MAX_BAR_PER_PERMINTAAN = 5000

#: 429 dikeluarkan dari daftar ulang - lihat docstring modul.  Ditulis sebagai
#: pengurangan dari ``RETRY_STATUSES`` supaya yang dikecualikan terbaca sebagai
#: pengecualian, bukan sebagai daftar baru yang kebetulan lebih pendek.
RETRY_TANPA_429 = RETRY_STATUSES - {429}


class TwelveDataForexProvider(MarketDataProvider):
    """Adapter XAU/USD.  Satu simbol, satu interval, satu jatah kredit."""

    def __init__(
        self,
        settings: DataSettings,
        *,
        api_key: str = "",
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._api_key = api_key
        self._fetcher = HttpFetcher(
            source=SOURCE,
            base_url=base_url,
            timeout_sec=settings.request_timeout_sec,
            max_retries=settings.max_retries,
            transport=transport,
        )
        self._budget = KreditHarian()

    # ---- kemampuan ------------------------------------------------------

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=SOURCE,
            market=Market.FOREX,
            transport=Transport.POLL,
            is_realtime=False,
            expected_delay_sec=60,
            supports_order_book=False,
            supported_intervals=(Horizon.M5,),
            max_candles_per_request=MAX_BAR_PER_PERMINTAAN,
            requires_credentials=True,
            regulatory_note=(
                "Twelve Data Basic plan; redistribusi data dibatasi lisensi mereka"
            ),
            limitations=(
                "valas spot tidak menerbitkan volume: Candle.volume selalu 0 "
                "dan tidak boleh dipakai sebagai fitur",
                "hanya M5 yang diminta dari venue; M15/H1/H4 dirakit lokal "
                "lewat aruna.xau.timeframes",
                "jatah paket gratis 800 kredit/hari dan 8/menit",
                "bid/ask belum tentu diterbitkan; saat kosong Quote.spread_bps "
                "bernilai None, yang berarti TIDAK DIUKUR",
            ),
        )

    # ---- daur hidup -----------------------------------------------------

    async def open(self) -> None:
        await self._fetcher.open()

    async def close(self) -> None:
        await self._fetcher.close()

    async def status(self) -> ProviderStatus:
        try:
            payload = await self._get_json(
                "/time_series",
                {"symbol": "XAU/USD", "interval": _INTERVAL_VENUE, "outputsize": "1"},
            )
        except DataSourceUnavailableError as exc:
            return ProviderStatus(reachable=False, detail=str(exc))
        if payload.get("status") == "error":
            return ProviderStatus(
                reachable=False, detail=str(payload.get("message", ""))
            )
        return ProviderStatus(reachable=True, server_time=now_utc())

    # ---- simbol ---------------------------------------------------------

    def provider_symbol(self, symbol: str) -> str:
        """``XAU/USD`` sudah bentuk yang dipakai Twelve Data."""
        return symbol.strip().upper()

    # ---- HTTP -----------------------------------------------------------

    async def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """Ambil JSON dengan 429 dibiarkan sampai utuh ke sini.

        Sengaja lewat ``get_response`` dan bukan ``get_json``: yang terakhir
        tidak membuka ``retry_statuses``, jadi 429-nya akan diulang tiga kali -
        menghabiskan tepat kredit yang venue keluhkan.  ``get_response``
        mengembalikan respons apa pun statusnya, jadi statusnya diperiksa di
        sini.
        """
        response, _latency = await self._fetcher.get_response(
            path,
            params={**params, "apikey": self._api_key},
            retry_statuses=RETRY_TANPA_429,
        )
        if response.status_code >= 400:
            raise DataSourceUnavailableError(
                f"{SOURCE} HTTP {response.status_code} untuk {path}: "
                f"{response.text[:120]!r}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DataSourceUnavailableError(
                f"{SOURCE} membalas non-JSON untuk {path}: {response.text[:120]!r}"
            ) from exc

    def _belanja_kredit(self, saat: datetime) -> None:
        if not self._budget.minta(saat):
            raise DataSourceUnavailableError(
                f"jatah kredit {SOURCE} habis (sisa hari ini "
                f"{self._budget.sisa(saat)}); menolak tanpa menembak venue"
            )

    @staticmethod
    def _tolak_kalau_venue_menolak(payload: dict[str, Any]) -> None:
        if payload.get("status") == "error":
            raise DataSourceUnavailableError(
                f"{SOURCE} menolak: {payload.get('code')} {payload.get('message')}"
            )

    # ---- data -----------------------------------------------------------

    async def fetch_candles(
        self,
        symbol: str,
        interval: Horizon,
        *,
        limit: int = 500,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        if interval is not Horizon.M5:
            raise ValueError(
                f"{SOURCE} hanya melayani {Horizon.M5.value}; "
                f"{interval.value} dirakit di aruna.xau.timeframes"
            )

        saat = now_utc()
        self._belanja_kredit(saat)

        params = {
            "symbol": self.provider_symbol(symbol),
            "interval": _INTERVAL_VENUE,
            "outputsize": str(min(limit, MAX_BAR_PER_PERMINTAAN)),
            "timezone": "UTC",
        }
        if start is not None:
            params["start_date"] = start.strftime("%Y-%m-%d %H:%M:%S")
        if end is not None:
            params["end_date"] = end.strftime("%Y-%m-%d %H:%M:%S")

        payload = await self._get_json("/time_series", params)
        self._tolak_kalau_venue_menolak(payload)

        provenance = Provenance(source=SOURCE, provider_timestamp=saat)
        # Venue mengirim terbaru dulu; kontrak ABC minta terlama dulu.
        return [
            self._ke_candle(row, symbol, provenance, saat)
            for row in reversed(payload.get("values") or [])
        ]

    def _ke_candle(
        self,
        row: dict[str, str],
        symbol: str,
        provenance: Provenance,
        sekarang: datetime,
    ) -> Candle:
        try:
            open_time = datetime.strptime(
                row["datetime"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=UTC)
            harga = {k: Decimal(row[k]) for k in ("open", "high", "low", "close")}
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise DataSourceUnavailableError(
                f"bar {SOURCE} tidak terbaca: {row!r}"
            ) from exc

        close_time = open_time + Horizon.M5.duration
        return Candle(
            market=Market.FOREX,
            symbol=symbol,
            interval=Horizon.M5,
            open_time=open_time,
            close_time=close_time,
            open=harga["open"],
            high=harga["high"],
            low=harga["low"],
            close=harga["close"],
            # Valas spot tidak punya volume; dinyatakan di
            # capabilities.limitations dan diuji supaya tak dipakai jadi fitur.
            volume=Decimal(0),
            quote_volume=None,
            trade_count=None,
            provenance=provenance,
            # **Dihitung, tidak diasumsikan.**  Baris ini pernah berbunyi
            # `is_closed=True` tanpa syarat, dan itu cacat data yang nyata:
            # Twelve Data mengembalikan bar yang SEDANG BERJALAN sebagai nilai
            # terbaru, jadi tiap keputusan berdiri di atas bar yang high, low,
            # dan close-nya masih akan berubah.
            #
            # Terukur di produksi 2026-08-28: bar terbaru punya close_time 0,7
            # menit DI MASA DEPAN dan tetap ditandai tutup.
            #
            # Seluruh mesin di hilir sudah siap menanganinya - `CandleSeries`
            # menyaring bar terbuka dan melaporkannya lewat
            # `excluded_open_bars`, `resample_candles` membuangnya lewat
            # `require_closed` - jadi yang rusak bukan penanganannya melainkan
            # kebenaran yang disuapkan ke sana.
            is_closed=close_time <= sekarang,
        )

    async def fetch_quote(self, symbol: str) -> Quote:
        saat = now_utc()
        self._belanja_kredit(saat)

        payload = await self._get_json(
            "/quote", {"symbol": self.provider_symbol(symbol)}
        )
        self._tolak_kalau_venue_menolak(payload)

        try:
            harga = Decimal(payload["close"])
        except (KeyError, InvalidOperation) as exc:
            raise DataSourceUnavailableError(
                f"quote {SOURCE} tidak terbaca: {payload!r}"
            ) from exc

        # bid/ask sengaja None kalau venue tidak menerbitkannya: Quote.spread_bps
        # sudah mengembalikan None untuk itu, dan itulah "tidak diukur".  Tidak
        # pernah ditaksir dari range candle.
        return Quote(
            market=Market.FOREX,
            symbol=symbol,
            price=harga,
            provenance=Provenance(source=SOURCE, provider_timestamp=saat),
            bid=_desimal_opsional(payload.get("bid")),
            ask=_desimal_opsional(payload.get("ask")),
        )

    async def fetch_snapshot(self, symbol: str) -> Snapshot:
        """Pandangan satu titik waktu.

        ``session`` dan ``market_open`` sengaja ``None``: keduanya baru diisi
        di Rencana 4 (sesi ASIA/LONDON/NEW YORK/OVERLAP).  ``None`` di sini
        berarti BELUM DIUKUR, dan sampai rencana itu selesai gerbang XAU
        memakai ``aruna.xau.kelayakan.periksa_kelayakan`` - bukan
        ``Snapshot.tradeable`` - supaya tak ada keputusan yang berdiri di atas
        bidang yang belum diisi siapa pun.
        """
        quote = await self.fetch_quote(symbol)
        return Snapshot(
            market=Market.FOREX,
            symbol=symbol,
            captured_at=quote.provenance.server_timestamp,
            last_price=quote.price,
            provenance=quote.provenance,
            quality=DataQuality.OK,
            bid=quote.bid,
            ask=quote.ask,
            spread_bps=quote.spread_bps,
            session=None,
            market_open=None,
        )


def _desimal_opsional(nilai: object) -> Decimal | None:
    """``None`` untuk yang tidak terbit - tidak pernah ``0`` sebagai gantinya."""
    if nilai is None or nilai == "":
        return None
    try:
        return Decimal(str(nilai))
    except InvalidOperation:
        return None


__all__ = [
    "BASE_URL",
    "MAX_BAR_PER_PERMINTAAN",
    "RETRY_TANPA_429",
    "SOURCE",
    "TwelveDataForexProvider",
]
