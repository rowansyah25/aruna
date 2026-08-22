"""Satu tick futures atas dua puluh simbol harus selesai di bawah lima detik.

Yang diuji di sini bukan detiknya - jam dinding tergantung jaringan, dan sebuah
test yang gagal ketika koneksi operator sedang lambat adalah test yang akan
diabaikan. Yang diuji adalah **bentuk** yang membuat detik itu mungkin, dan tiap
satunya sudah pernah salah:

* spesifikasi kontrak diunduh sekali, bukan sekali per simbol (1,08 MB kali dua
  puluh);
* adapter bursa hidup lebih lama dari satu tick, kalau tidak cache di atas lahir
  kosong tiap tick dan tidak berguna;
* ``leverageBracket`` tidak ditanyakan lagi sesudah bursa menjawab "butuh
  kredensial", karena jawabannya tidak akan berubah;
* argumen council ditulis berkelompok, bukan satu baris per perjalanan;
* penarikan candle bersamaan, penulisannya **tidak** - versi yang menulis
  bersamaan menghasilkan deadlock InnoDB pada enam dari dua puluh simbol;
* penyegaran candle tumpang tindih dengan pengambilan snapshot, tapi tidak ada
  candle yang dibaca sebelum ia selesai.

Angka ukurannya, untuk pembaca berikutnya: 8,24 detik sebelum, 3,4-3,8 detik
sesudah, dengan lantai jaringan terukur 0,98 detik untuk seratus permintaan.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, Provenance

NOW = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# exchangeInfo: satu unduhan, bukan satu per simbol
# ---------------------------------------------------------------------------


class _KlienBursa:
    """Klien HTTP palsu yang menghitung tiap endpoint yang diminta."""

    def __init__(self, *, bracket_gagal: str = "401") -> None:
        self.hitung: dict[str, int] = {}
        self.bracket_gagal = bracket_gagal

    async def get(self, url: str, params: Any = None) -> httpx.Response:
        kunci = url.rsplit("/", 1)[-1]
        self.hitung[kunci] = self.hitung.get(kunci, 0) + 1
        permintaan = httpx.Request("GET", url)

        if "leverageBracket" in url:
            if self.bracket_gagal == "401":
                raise httpx.HTTPStatusError(
                    "401",
                    request=permintaan,
                    response=httpx.Response(
                        401,
                        request=permintaan,
                        text='{"code":-2014,"msg":"API-key format invalid."}',
                    ),
                )
            raise httpx.ConnectError("connection reset by peer")

        # Sengaja menunggu satu putaran event loop: tanpa ini, pemanggil
        # pertama menyelesaikan unduhannya sebelum yang kedua sempat berjalan,
        # dan test tidak akan pernah menyentuh keadaan yang justru diuji -
        # dua puluh pemanggil yang berangkat bersamaan ke cache kosong.
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            request=permintaan,
            json={
                "symbols": [
                    {
                        "symbol": s,
                        "contractType": "PERPETUAL",
                        "baseAsset": s.removesuffix("USDT"),
                        "quoteAsset": "USDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                            {"filterType": "MIN_NOTIONAL", "notional": "5"},
                        ],
                    }
                    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT")
                ]
            },
        )


def _provider(klien: _KlienBursa) -> Any:
    from aruna.futures.binance import BinanceFuturesProvider

    return BinanceFuturesProvider(client=klien)


SIMBOL = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT")


class TestSpesifikasiKontrakDiunduhSekali:
    """``/fapi/v1/exchangeInfo`` mengembalikan SELURUH bursa - terukur 1,08 MB,
    871 simbol - dan tidak bisa disaring: ``?symbol=BTCUSDT`` tetap
    mengembalikan semuanya. Dua puluh simbol yang memanggilnya sendiri-sendiri
    adalah 21,6 MB per tick untuk memakai dua puluh baris."""

    @pytest.mark.asyncio
    async def test_dua_puluh_pemanggil_bersamaan_satu_unduhan(self) -> None:
        klien = _KlienBursa()
        provider = _provider(klien)

        await asyncio.gather(*(provider.contract(s) for s in SIMBOL))

        assert klien.hitung["exchangeInfo"] == 1, klien.hitung

    @pytest.mark.asyncio
    async def test_semua_pemanggil_tetap_dapat_spesifikasinya(self) -> None:
        """Penghematan tidak boleh dibayar dengan jawaban yang hilang."""
        klien = _KlienBursa()
        provider = _provider(klien)

        hasil = await asyncio.gather(*(provider.contract(s) for s in SIMBOL))

        assert [c.symbol for c in hasil] == list(SIMBOL)
        assert all(c.tick_size == Decimal("0.01") for c in hasil)

    def test_ttl_nya_terbatas_dan_pendek(self) -> None:
        """Umur cache-nya harus angka yang berarti, dan test ini yang menjaganya.

        Test kedaluwarsa di bawah memajukan jam sebesar ``EXCHANGE_INFO_TTL_SEC``
        itu sendiri, jadi ia lulus untuk umur berapa pun - termasuk umur yang
        praktis tak terbatas. Ia menguji mekanismenya, bukan angkanya.

        Angkanya adalah pertanyaan kebenaran, bukan kecepatan: kontrak yang baru
        terdaftar dan filter yang bursa perketat harus sampai dalam hitungan
        menit tanpa menunggu restart. Batas atas satu jam adalah pernyataan
        seberapa lama ARUNA boleh memakai aturan bursa yang mungkin sudah usang.
        """
        from aruna.futures.binance import EXCHANGE_INFO_TTL_SEC

        assert 60.0 <= EXCHANGE_INFO_TTL_SEC <= 3600.0, EXCHANGE_INFO_TTL_SEC

    @pytest.mark.asyncio
    async def test_cache_kedaluwarsa_lalu_diambil_lagi(self, monkeypatch) -> None:
        """Kontrak yang baru terdaftar tidak boleh menunggu restart."""
        from aruna.futures import binance as modul

        klien = _KlienBursa()
        provider = _provider(klien)

        jam = [1000.0]
        monkeypatch.setattr(modul, "monotonic", lambda: jam[0])

        await provider.contract("BTCUSDT")
        jam[0] += modul.EXCHANGE_INFO_TTL_SEC + 1
        await provider.contract("BTCUSDT")

        assert klien.hitung["exchangeInfo"] == 2, klien.hitung

    @pytest.mark.asyncio
    async def test_di_dalam_ttl_tidak_diambil_lagi(self, monkeypatch) -> None:
        from aruna.futures import binance as modul

        klien = _KlienBursa()
        provider = _provider(klien)

        jam = [1000.0]
        monkeypatch.setattr(modul, "monotonic", lambda: jam[0])

        await provider.contract("BTCUSDT")
        jam[0] += modul.EXCHANGE_INFO_TTL_SEC - 1
        await provider.contract("BTCUSDT")

        assert klien.hitung["exchangeInfo"] == 1, klien.hitung


class TestBracketTidakDitanyakanLagi:
    """``leverageBracket`` menuntut request bertanda tangan, dan adapter ini
    tidak boleh membuatnya (FUTURES SPEC 3). Penolakan pertama karena itu
    berlaku untuk semua simbol seumur proses."""

    @pytest.mark.asyncio
    async def test_penolakan_kredensial_mengunci_seumur_proses(self) -> None:
        klien = _KlienBursa(bracket_gagal="401")
        provider = _provider(klien)

        for s in SIMBOL:
            await provider.contract(s)

        assert klien.hitung["leverageBracket"] == 1, klien.hitung

    @pytest.mark.asyncio
    async def test_kegagalan_jaringan_tidak_mengunci(self) -> None:
        """Jaringan yang putus sebentar bisa pulih; izin yang tidak ada tidak.
        Menyamakan keduanya akan mematikan pengambilan bracket selamanya karena
        satu kedipan koneksi."""
        klien = _KlienBursa(bracket_gagal="jaringan")
        provider = _provider(klien)

        for s in SIMBOL:
            await provider.contract(s)

        assert klien.hitung["leverageBracket"] == len(SIMBOL), klien.hitung

    @pytest.mark.asyncio
    async def test_yang_dilaporkan_sama_persis_di_kedua_jalur(self) -> None:
        """Kunci itu penghematan perjalanan, bukan perubahan jawaban.

        Kalau simbol kedua dan seterusnya melaporkan sesuatu yang berbeda dari
        simbol pertama - batas leverage yang dikarang, catatan yang hilang -
        maka penghematannya dibayar dengan kebohongan.
        """
        klien = _KlienBursa(bracket_gagal="401")
        provider = _provider(klien)

        pertama = await provider.contract("BTCUSDT")
        kedua = await provider.contract("ETHUSDT")

        assert pertama.max_leverage is None
        assert kedua.max_leverage is None
        assert kedua.margin_brackets == ()
        assert any("signed request" in n for n in kedua.notes), kedua.notes


# ---------------------------------------------------------------------------
# backfill: tarik bersamaan, tulis bergiliran
# ---------------------------------------------------------------------------


def _candle(symbol: str, menit: int) -> Candle:
    saat = datetime(2026, 8, 18, 0, menit, tzinfo=UTC)
    return Candle(
        market=Market.CRYPTO,
        symbol=symbol,
        interval=Horizon.M1,
        open_time=saat,
        # Harus lebih lambat dari `open_time`: bar dengan lebar nol ditolak
        # gerbang mutu sebagai OHLC tidak koheren, dan test ini akan mengukur
        # penolakan itu alih-alih mengukur bentuk penulisannya.
        close_time=saat.replace(second=59),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("10"),
        provenance=Provenance(source="fake", provider_timestamp=saat),
    )


class _Jejak:
    """Penghitung berapa tugas berada di dalam sebuah bagian pada satu saat."""

    def __init__(self) -> None:
        self.sekarang = 0
        self.puncak = 0

    async def masuk(self, tidur: float = 0.01) -> None:
        self.sekarang += 1
        self.puncak = max(self.puncak, self.sekarang)
        await asyncio.sleep(tidur)
        self.sekarang -= 1


class _ProviderCandle:
    name = "fake-candles"
    market = Market.CRYPTO

    def __init__(self, tarik: _Jejak) -> None:
        self._tarik = tarik

    @property
    def capabilities(self) -> Any:
        from aruna.data.provider import ProviderCapabilities, Transport

        return ProviderCapabilities(
            name=self.name,
            market=Market.CRYPTO,
            transport=Transport.POLL,
            is_realtime=True,
            expected_delay_sec=0,
            supports_order_book=False,
            supported_intervals=(Horizon.M1,),
            max_candles_per_request=1000,
            requires_credentials=False,
            regulatory_note="test double",
        )

    async def fetch_candles(
        self, symbol: str, interval: Horizon, *, limit: int
    ) -> list[Candle]:
        await self._tarik.masuk()
        return [_candle(symbol, 0), _candle(symbol, 1)]


class _StoreCandle:
    def __init__(self, tulis: _Jejak) -> None:
        self._tulis = tulis
        self.ditulis: list[int] = []

    async def upsert_candles(self, asset_id: int, candles: list[Candle]) -> int:
        await self._tulis.masuk(tidur=0.005)
        self.ditulis.append(asset_id)
        return len(candles)

    async def record_provider_event(self, **kwargs: Any) -> int:
        return 1


class _AsetPalsu:
    def __init__(self, id_: int, symbol: str) -> None:
        self.id = id_
        self.symbol = symbol


class _UniversePalsu:
    def __init__(self, assets: list[Any]) -> None:
        self._assets = assets

    async def assets(self, *, market: Market, enabled_only: bool = True) -> list[Any]:
        return self._assets


def _ingestor_candle(tarik: _Jejak, tulis: _Jejak, jumlah: int = 8) -> Any:
    from aruna.core.config import DataSettings
    from aruna.data.ingest import MarketIngestor

    aset = [_AsetPalsu(i + 1, f"SYM{i}/USDT") for i in range(jumlah)]
    return MarketIngestor(
        provider=_ProviderCandle(tarik),
        universe=_UniversePalsu(aset),
        store=_StoreCandle(tulis),
        settings=DataSettings(_env_file=None),
    )


class TestPenarikanBersamaanPenulisanBergiliran:
    """Bentuk ini dipilih sesudah bentuk yang lebih naif gagal.

    ``_backfill_one`` yang seluruhnya dijalankan bersamaan menghasilkan
    ``OperationalError 1213: Deadlock found`` pada enam dari dua puluh simbol.
    Yang ditukar bukan pesan error - simbol yang gagal disegarkan tetap
    dianalisis, dari bar lama.
    """

    @pytest.mark.asyncio
    async def test_penarikan_tumpang_tindih(self) -> None:
        tarik, tulis = _Jejak(), _Jejak()
        ingestor = _ingestor_candle(tarik, tulis)

        await ingestor.backfill((Horizon.M1,), quiet=True, detect_gaps=False)

        assert tarik.puncak > 1, (
            "penarikan berurutan: sembilan puluh persen waktu satu tick adalah "
            "menunggu jaringan, dan menunggu bisa dilakukan berbarengan"
        )

    @pytest.mark.asyncio
    async def test_penulisan_tidak_pernah_tumpang_tindih(self) -> None:
        """Ini yang menahan deadlock-nya, dan ia harus benar karena struktur -
        bukan karena dicoba lagi sampai berhasil."""
        tarik, tulis = _Jejak(), _Jejak()
        ingestor = _ingestor_candle(tarik, tulis)

        await ingestor.backfill((Horizon.M1,), quiet=True, detect_gaps=False)

        assert tulis.puncak == 1, (
            f"{tulis.puncak} penulisan candle berjalan bersamaan; itu bentuk "
            "yang menghasilkan deadlock InnoDB pada enam dari dua puluh simbol"
        )

    @pytest.mark.asyncio
    async def test_semua_aset_tetap_tersimpan(self) -> None:
        tarik, tulis = _Jejak(), _Jejak()
        ingestor = _ingestor_candle(tarik, tulis, jumlah=8)

        hasil = await ingestor.backfill(
            (Horizon.M1,), quiet=True, detect_gaps=False
        )

        assert ingestor._store.ditulis == list(range(1, 9))
        assert hasil.candles == 16
        assert hasil.failures == []

    @pytest.mark.asyncio
    async def test_urutannya_tetap_urutan_pekerjaan(self) -> None:
        """Bukan urutan selesainya jaringan: log yang dibaca dua kali harus
        sama, dan ``result.failures`` tidak boleh berpindah baris."""
        tarik, tulis = _Jejak(), _Jejak()
        ingestor = _ingestor_candle(tarik, tulis, jumlah=6)

        await ingestor.backfill((Horizon.M1,), quiet=True, detect_gaps=False)

        assert ingestor._store.ditulis == sorted(ingestor._store.ditulis)


# ---------------------------------------------------------------------------
# council: argumen ditulis berkelompok
# ---------------------------------------------------------------------------


class _DbPalsu:
    """Mencatat tiap pernyataan SQL yang lewat, dengan berapa baris sekaligus."""

    def __init__(self) -> None:
        self.jalan: list[tuple[str, int]] = []
        self._id = 0

    def _catat(self, sql: str, baris: int) -> None:
        ringkas = " ".join(sql.split())[:60]
        self.jalan.append((ringkas, baris))

    async def execute(self, sql: str, *args: Any) -> int:
        self._catat(sql, 1)
        return 1

    async def insert(self, sql: str, *args: Any) -> int:
        self._catat(sql, 1)
        self._id += 1
        return self._id

    async def executemany(self, sql: str, args: Any) -> int:
        self._catat(sql, len(list(args)))
        return len(list(args))

    @asynccontextmanager
    async def write_lock(
        self, table: str, *, timeout: float = 30
    ) -> AsyncIterator[None]:
        """Antrean penulis, sebentuk dengan ``Database.write_lock``.

        ``save`` lewat sini sekarang: dua proses ARUNA menulis ``council_votes``
        dan baris mereka bertetangga di indeks uniknya. Tidak dicatat di
        ``jalan`` dengan sengaja - yang dihitung berkas ini adalah perjalanan ke
        MySQL, dan antrean bukan salah satunya.
        """
        yield

    def perjalanan(self, potongan: str) -> list[tuple[str, int]]:
        return [(s, n) for s, n in self.jalan if potongan in s]


class TestArgumenCouncilDitulisBerkelompok:
    """Terukur pada tick dua puluh simbol: ``save`` menghabiskan 1206 ms
    rata-rata dan 24,1 detik kalau dijumlah - biaya terbesar seluruh tick, di
    atas jaringan bursa. Sebagian besarnya adalah antrean koneksi, bukan
    MySQL."""

    def _verdict(self, jumlah_agent: int = 11) -> Any:
        """Verdict tiruan dengan bentuk yang dibaca ``save``.

        Dibangun di sini dan bukan dari ``CouncilVerdict`` yang sungguhan
        karena yang diuji adalah berapa perjalanan ke database sebuah simpanan
        menghabiskan, bukan apakah council menyusun verdict-nya dengan benar -
        itu diuji di tempat lain, terhadap tipe aslinya.
        """
        from types import SimpleNamespace as N

        from aruna.core.enums import AgentRole, Decision

        peran = list(AgentRole)
        opinions = [
            N(
                role=peran[i % len(peran)],
                decision=Decision.BUY,
                confidence=0.6,
                abstained=False,
                reasoning=("karena begitu",),
                evidence=(),
            )
            for i in range(jumlah_agent)
        ]
        return N(
            market="CRYPTO",
            symbol="BTC/USDT",
            interval="4h",
            as_of=NOW,
            decided_at=NOW,
            decision=Decision.BUY,
            confidence=0.6,
            participating=jumlah_agent,
            opinions=opinions,
            rounds_run=(1, 2, 3),
            notes=("catatan",),
            risk=N(overall=N(value="MEDIUM")),
            no_trade=N(blocked=False, reasons=()),
            veto=N(vetoes=(), upheld=(), reviews=()),
            protest=N(
                objections=(),
                supports=(),
                rebuttals=(),
                disagreement=0.2,
            ),
            judgement=N(
                decision=Decision.BUY,
                confidence=0.6,
                buy_weight=0.7,
                sell_weight=0.3,
                minority_prevailed=False,
                reasoning=("begitu",),
                weights=(),
                unavailable_factors=(),
            ),
        )

    @pytest.mark.asyncio
    async def test_suara_agent_satu_perjalanan(self) -> None:
        from aruna.db.repositories.council import CouncilRepository

        db = _DbPalsu()
        verdict = self._verdict(jumlah_agent=11)
        await CouncilRepository(db, phase=10).save(1, verdict)

        masuk = db.perjalanan("INSERT INTO council_votes")
        assert len(masuk) == 1, masuk
        assert masuk[0][1] == 11, masuk

    @pytest.mark.asyncio
    async def test_jumlah_perjalanan_tidak_tumbuh_dengan_jumlah_agent(self) -> None:
        """Yang diuji adalah bentuknya: dua belas agent tidak boleh berharga
        dua belas perjalanan lebih dari satu agent."""
        from aruna.db.repositories.council import CouncilRepository

        sedikit, banyak = _DbPalsu(), _DbPalsu()
        await CouncilRepository(sedikit, phase=10).save(1, self._verdict(2))
        await CouncilRepository(banyak, phase=10).save(1, self._verdict(12))

        assert len(sedikit.jalan) == len(banyak.jalan), (
            f"{len(sedikit.jalan)} perjalanan untuk 2 agent, "
            f"{len(banyak.jalan)} untuk 12"
        )

    @pytest.mark.asyncio
    async def test_tidak_ada_suara_yang_hilang(self) -> None:
        """PASAL 11.21: sisi yang kalah dalam sebuah argumen tetap disimpan."""
        from aruna.db.repositories.council import CouncilRepository

        db = _DbPalsu()
        await CouncilRepository(db, phase=10).save(1, self._verdict(7))

        masuk = db.perjalanan("INSERT INTO council_votes")
        assert sum(n for _, n in masuk) == 7, masuk


# ---------------------------------------------------------------------------
# tick: penyegaran tumpang tindih, tapi tidak ada candle dibaca sebelum selesai
# ---------------------------------------------------------------------------


class _Urutan:
    def __init__(self) -> None:
        self.peristiwa: list[str] = []

    def catat(self, apa: str) -> None:
        self.peristiwa.append(apa)


class _IngestorLambat:
    def __init__(self, urutan: _Urutan) -> None:
        self._urutan = urutan

    async def backfill(self, intervals: Any, **kwargs: Any) -> Any:
        from aruna.data.ingest import IngestResult

        self._urutan.catat("segar:mulai")
        await asyncio.sleep(0.05)
        self._urutan.catat("segar:selesai")
        return IngestResult(market=Market.CRYPTO, provider="fake")


class _IngestPalsu:
    def __init__(self, urutan: _Urutan) -> None:
        self._ingestor = _IngestorLambat(urutan)

    def ingestor(self, market: Market) -> Any:
        return self._ingestor


class _ProviderSnapshot:
    def __init__(self, urutan: _Urutan) -> None:
        self._urutan = urutan

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def snapshot(self, symbol: str) -> Any:
        self._urutan.catat(f"snapshot:{symbol}")
        await asyncio.sleep(0.01)
        return None


class _DeliberasiPalsu:
    def __init__(self, urutan: _Urutan) -> None:
        self._urutan = urutan

    async def build_context(self, asset: Any, market: Any, horizon: Any) -> Any:
        self._urutan.catat("baca_candle")
        # None membuat `_plan_one` menolak simbolnya dengan ArunaError, yang
        # dicatat sebagai kesalahan per simbol dan bukan menggagalkan tick.
        # Itu cukup: yang diuji adalah KAPAN baris ini tercapai.
        return None


class _UniverseSatuAset:
    async def find(self, market: Any, symbol: str) -> Any:
        return _AsetPalsu(1, symbol)


class TestPenyegaranTumpangTindihTapiBerpagar:
    """Dua sifat yang harus benar bersamaan, dan mudah menukar satu demi yang
    lain: penyegaran candle berjalan berbarengan dengan pengambilan snapshot,
    **dan** tidak ada candle yang dibaca sebelum penyegaran itu selesai."""

    def _service(self, urutan: _Urutan) -> Any:
        from aruna.futures.service import FuturesPlanService

        return FuturesPlanService(
            deliberation=_DeliberasiPalsu(urutan),
            council=None,
            store=None,
            universe=_UniverseSatuAset(),
            provider=_ProviderSnapshot(urutan),
            ingest=_IngestPalsu(urutan),
        )

    async def _jalankan(self) -> list[str]:
        urutan = _Urutan()
        await self._service(urutan).plan(
            ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            horizon=Horizon.H4,
            equity=Decimal("10000"),
        )
        return urutan.peristiwa

    @pytest.mark.asyncio
    async def test_snapshot_berjalan_sebelum_penyegaran_selesai(self) -> None:
        """Kalau ini gagal, tick membayar penyegaran dan snapshot berturut-turut
        padahal keduanya hanya menunggu jaringan yang berbeda."""
        peristiwa = await self._jalankan()

        selesai = peristiwa.index("segar:selesai")
        snapshot_awal = next(
            i for i, p in enumerate(peristiwa) if p.startswith("snapshot:")
        )
        assert snapshot_awal < selesai, peristiwa

    @pytest.mark.asyncio
    async def test_tidak_ada_candle_dibaca_sebelum_penyegaran_selesai(self) -> None:
        """Gerbangnya. Mencabutnya tidak membuat apa pun error - ia hanya
        membuat council membaca bar tick sebelumnya, diam-diam."""
        peristiwa = await self._jalankan()

        selesai = peristiwa.index("segar:selesai")
        baca = [i for i, p in enumerate(peristiwa) if p == "baca_candle"]
        assert baca, peristiwa
        assert min(baca) > selesai, peristiwa

    @pytest.mark.asyncio
    async def test_tiap_simbol_tetap_sampai_ke_pembacaan(self) -> None:
        peristiwa = await self._jalankan()

        assert peristiwa.count("baca_candle") == 3, peristiwa


class TestAdapterBursaHidupLintasTick:
    """Cache spesifikasi kontrak dan kunci bracket disimpan di adapter. Adapter
    yang dibuat ulang tiap tick membuat keduanya lahir kosong tiap tick -
    terukur: ``contract`` tetap 1,16 detik rata-rata padahal cache-nya sudah
    ada dan bekerja."""

    def _service(self) -> Any:
        from aruna.futures.service import FuturesPlanService

        return FuturesPlanService(
            deliberation=None, council=None, store=None, universe=None
        )

    @pytest.mark.asyncio
    async def test_adapter_yang_sama_dipakai_lagi(self) -> None:
        service = self._service()
        try:
            pertama = await service._venue()
            kedua = await service._venue()
            assert pertama is kedua
        finally:
            await service.aclose()

    @pytest.mark.asyncio
    async def test_aclose_menutup_yang_dimiliki_sendiri(self) -> None:
        """Adapter yang hidup lebih lama juga bisa bocor lebih lama."""
        service = self._service()
        provider = await service._venue()
        assert provider._client is not None

        await service.aclose()

        assert provider._client is None
        assert service._owned is None

    @pytest.mark.asyncio
    async def test_adapter_yang_disuntikkan_tidak_disentuh(self) -> None:
        """Pemanggil yang membawa providernya sendiri memilikinya sendiri."""
        from aruna.futures.service import FuturesPlanService

        urutan = _Urutan()
        punya_pemanggil = _ProviderSnapshot(urutan)
        service = FuturesPlanService(
            deliberation=None,
            council=None,
            store=None,
            universe=None,
            provider=punya_pemanggil,
        )

        assert await service._venue() is punya_pemanggil
        await service.aclose()
        assert service._owned is None
