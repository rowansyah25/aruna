"""PASAL 14.41: korelasi yang tidak pernah dihitung tidak pernah sampai.

Terukur di produksi 2026-08-21: ``CORRELATION_RISK`` hilang pada **keempat
puluh** amatan ``decision.observed``. Sebabnya bukan pembacanya —
``PembacaPembelajaran._correlation`` sudah dirangkai di ``app.py``, dan sejak
restart terakhir ia tidak melempar satu kali pun. Sebabnya tabel
``correlations`` **kosong: nol baris**. Satu-satunya yang pernah mengisinya
adalah perintah CLI ``aruna correlate``, yang dijalankan manusia — dan tidak
ada manusia yang menjalankannya tiap jam.

Ini kelas cacat yang sama dengan delapan modul ``aruna.decision`` yang pernah
diam: mesinnya ada sejak Phase 4, pembacanya ada sejak kemarin, dan di antara
keduanya tidak ada yang menghasilkan apa pun.

Berkas ini menguji **penghasilnya**, dan satu hal lagi yang lebih mudah salah:
bahwa yang ditulis penghasil itu persis yang ditanyakan pembacanya. Matriks yang
tersimpan di interval lain akan membuat tabelnya terisi, lognya terlihat sibuk,
dan keputusannya tetap tidak membaca apa-apa.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from aruna.core.enums import Horizon, Market

NOW = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)


# ---- palsu yang bentuknya mengikuti yang sungguhan ----------------------
#
# `FakeStop` pernah menaruh harga di bidang yang salah dan meloloskan bug ke
# produksi. Ketiga palsu di bawah meniru tanda tangan aslinya persis:
# `universe.assets(market=..., enabled_only=...)`,
# `market_data.candles(asset_id, interval, limit=...)`,
# `store.save(matrix, market=...)` dan `store.latest(market, interval, limit=...)`.


class _Aset:
    def __init__(self, id_: int, symbol: str) -> None:
        self.id = id_
        self.symbol = symbol


class _Universe:
    def __init__(self, aset: list[_Aset]) -> None:
        self._aset = aset

    async def assets(self, *, market: Market, enabled_only: bool = True) -> list[_Aset]:
        return list(self._aset)


def _baris(n: int, *, awal: float, langkah: float) -> list[dict[str, Any]]:
    """Candle tersimpan, sebentuk baris repositori yang sungguhan."""
    keluar = []
    for i in range(n):
        harga = awal + langkah * i
        buka = NOW - timedelta(hours=4 * (n - i))
        keluar.append({
            "open_time": buka,
            "close_time": buka + timedelta(hours=4),
            "open": Decimal(str(harga)),
            "high": Decimal(str(harga + 1)),
            "low": Decimal(str(harga - 1)),
            "close": Decimal(str(harga + 0.5)),
            "volume": Decimal("10"),
        })
    return keluar


class _MarketData:
    def __init__(self, per_aset: dict[int, list[dict[str, Any]]]) -> None:
        self._per_aset = per_aset
        self.diminta: list[tuple[int, Horizon]] = []

    async def candles(
        self, asset_id: int, interval: Horizon, *, limit: int = 500,
        closed_only: bool = True,
    ) -> list[dict[str, Any]]:
        self.diminta.append((asset_id, interval))
        return list(self._per_aset.get(asset_id, []))[:limit]


class _Store:
    """Meniru ``CorrelationRepository``: menyimpan per (pasar, interval).

    ``latest`` memanggil ``market.value`` seperti SQL yang sungguhan, supaya
    pemanggil yang mengoper string tetap gagal di sini — itu persis kegagalan
    yang sudah pernah terjadi di produksi.
    """

    def __init__(self) -> None:
        self.isi: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def save(self, matrix: Any, *, market: Market) -> int:
        kunci = (market.value, matrix.interval)
        self.isi[kunci] = [
            {
                "left_symbol": p.left,
                "right_symbol": p.right,
                "coefficient": p.coefficient,
                "overlap": p.overlap,
                "strength": p.strength,
                "as_of": p.last or matrix.computed_at,
            }
            for p in matrix.pairs
        ]
        return len(matrix.pairs)

    async def latest(
        self, market: Market, interval: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        return self.isi.get((market.value, interval), [])[:limit]


def _penyegar(**ganti: Any):
    from aruna.upkeep.korelasi import PenyegarKorelasi

    aset = [_Aset(1, "BTC/USDT"), _Aset(2, "ETH/USDT"), _Aset(3, "SOL/USDT")]
    per_aset = {
        1: _baris(40, awal=100, langkah=1.0),
        2: _baris(40, awal=50, langkah=0.5),
        3: _baris(40, awal=20, langkah=-0.2),
    }
    kw: dict[str, Any] = {
        "universe": _Universe(aset),
        "market_data": _MarketData(per_aset),
        "store": _Store(),
    }
    kw.update(ganti)
    return PenyegarKorelasi(**kw), kw


class TestPenyegarnyaSendiri:
    @pytest.mark.asyncio
    async def test_menyimpan_pasangan_dari_candle_tersimpan(self) -> None:
        penyegar, kw = _penyegar()

        hasil = await penyegar.refresh(now=NOW)

        assert hasil, "tidak ada pasar yang dihitung"
        assert hasil[0].stored == 3  # tiga aset -> tiga pasangan
        assert kw["store"].isi

    @pytest.mark.asyncio
    async def test_aset_dengan_candle_terlalu_sedikit_dilewati(self) -> None:
        """§13.26: korelasi dari sepuluh bar adalah angka yang terlihat seperti
        pengukuran dan bukan pengukuran. Yang tipis dilewati dan **disebut**,
        bukan diikutkan diam-diam."""
        from aruna.upkeep.korelasi import MIN_CANDLE

        aset = [_Aset(1, "BTC/USDT"), _Aset(2, "ETH/USDT"), _Aset(3, "SOL/USDT")]
        per_aset = {
            1: _baris(40, awal=100, langkah=1.0),
            2: _baris(40, awal=50, langkah=0.5),
            3: _baris(MIN_CANDLE - 1, awal=20, langkah=-0.2),
        }
        penyegar, _ = _penyegar(
            universe=_Universe(aset), market_data=_MarketData(per_aset)
        )

        hasil = await penyegar.refresh(now=NOW)

        assert hasil[0].stored == 1  # dua aset -> satu pasangan
        assert any("SOL/USDT" in d for d in hasil[0].dilewati)

    @pytest.mark.asyncio
    async def test_pasar_yang_tidak_menawarkan_horizonnya_dilewati(self) -> None:
        """Terukur pada siklus pertama sesudah restart 2026-08-21: pasar kedua
        memulangkan ``korelasi.tidak_cukup_aset`` dengan nol aset. Bukan data
        yang kurang - IDX **tidak punya bar 4h sama sekali**, dan
        ``horizons_for_market`` menyebutnya langsung.

        Memintanya tiap jam menghasilkan peringatan yang tidak akan pernah bisa
        diperbaiki oleh data, dan peringatan yang selalu ada berhenti dibaca -
        lalu ia menutupi peringatan yang berarti sesuatu.

        **Horizonnya disebut di sini, bukan dipinjam dari ``HORIZON_KEPUTUSAN``.**
        Versi pertama mengandalkan bawaannya, dan bawaan itu pindah ke 1d pada
        2026-08-25 - interval yang IDX justru PUNYA, jadi testnya merah tanpa
        satu pun aturan berubah. Yang diuji "pasar tanpa horizonnya dilewati",
        dan itu butuh horizon yang memang tidak ditawarkan pasar itu.
        """
        penyegar, _ = _penyegar(
            markets=(Market.CRYPTO, Market.IDX), interval=Horizon.H4
        )

        hasil = await penyegar.refresh(now=NOW)

        assert [h.market for h in hasil] == [Market.CRYPTO]

    @pytest.mark.asyncio
    async def test_kurang_dari_dua_aset_tidak_menyimpan_apa_pun(self) -> None:
        """Matriks satu aset adalah matriks kosong. Menyimpannya akan menimpa
        baris kemarin yang masih berarti dengan ketiadaan hari ini."""
        aset = [_Aset(1, "BTC/USDT")]
        penyegar, kw = _penyegar(
            universe=_Universe(aset),
            market_data=_MarketData({1: _baris(40, awal=100, langkah=1.0)}),
        )

        hasil = await penyegar.refresh(now=NOW)

        assert hasil[0].stored == 0
        assert kw["store"].isi == {}


class TestSampaiKePembacanya:
    """Yang ditulis penghasil harus persis yang ditanyakan pembacanya."""

    @pytest.mark.asyncio
    async def test_yang_disimpan_terbaca_oleh_snapshot_pembelajaran(self) -> None:
        from aruna.learning.snapshot import PembacaPembelajaran

        penyegar, kw = _penyegar()
        await penyegar.refresh(now=NOW)

        pembaca = PembacaPembelajaran(correlation=kw["store"])
        hasil = await pembaca.baca(market=Market.CRYPTO, interval=penyegar.interval)

        assert hasil.correlation, (
            "tersimpan, tapi tidak terbaca - penghasil dan pembaca memakai "
            "kunci yang berbeda"
        )

    @pytest.mark.asyncio
    async def test_kelengkapan_menyebut_correlation_risk_hadir(self) -> None:
        """Ujung yang sebenarnya diukur di produksi (PASAL 14.41)."""
        from aruna.decision.integration import Masukan
        from aruna.futures.service import _kelengkapan_fase
        from aruna.learning.snapshot import PembacaPembelajaran

        penyegar, kw = _penyegar()
        await penyegar.refresh(now=NOW)
        pembaca = PembacaPembelajaran(correlation=kw["store"])
        pembelajaran = await pembaca.baca(
            market=Market.CRYPTO, interval=penyegar.interval
        )

        class _Note:
            pass

        note = _Note()
        note.pembelajaran = pembelajaran

        laporan = _kelengkapan_fase(
            context=None, verdict=None, plan=None, note=note
        )

        assert Masukan.CORRELATION_RISK.value not in laporan["integrasi_hilang"]


class TestDiJalurHidup:
    """Berkas ini menguji pemanggilnya, bukan yang dipanggil."""

    def _loop(self, korelasi: Any, **ganti: Any):
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop

        return UpkeepLoop(
            refresher=None,
            resolver=None,
            korelasi=korelasi,
            settings=UpkeepSettings(enabled=False, **ganti),
        )

    @pytest.mark.asyncio
    async def test_siklus_upkeep_menghitung_korelasi(self) -> None:
        class _Palsu:
            def __init__(self) -> None:
                self.dipanggil = 0

            async def refresh(self, *, now: datetime | None = None) -> tuple:
                self.dipanggil += 1
                return ()

        palsu = _Palsu()
        await self._loop(palsu).cycle(now=NOW)

        assert palsu.dipanggil == 1

    @pytest.mark.asyncio
    async def test_kegagalannya_tidak_menghentikan_siklus(self) -> None:
        """Korelasi adalah bukti tambahan, bukan syarat hidup. Siklus yang mati
        karenanya berarti candle yang tidak disegarkan dan sinyal yang tidak
        dinilai - kerusakan yang jauh lebih besar daripada yang dijaganya."""
        class _Meledak:
            async def refresh(self, *, now: datetime | None = None) -> tuple:
                raise RuntimeError("korelasi gagal")

        loop = self._loop(_Meledak())
        stats = await loop.cycle(now=NOW)

        assert stats.cycles == 1
        assert stats.correlation_failures == 1

    @pytest.mark.asyncio
    async def test_tidak_dihitung_ulang_sebelum_cadence(self) -> None:
        """Dua puluh aset kali empat ratus bar tiap menit adalah menghitung
        ulang jawaban yang sama: bar 4h berubah tiap empat jam."""
        class _Palsu:
            def __init__(self) -> None:
                self.dipanggil = 0

            async def refresh(self, *, now: datetime | None = None) -> tuple:
                self.dipanggil += 1
                return ()

        palsu = _Palsu()
        loop = self._loop(palsu, correlation_interval_sec=3600.0)

        await loop.cycle(now=NOW)
        await loop.cycle(now=NOW + timedelta(minutes=5))

        assert palsu.dipanggil == 1

    def test_aplikasi_mengoper_penyegarnya_ke_loop(self) -> None:
        """Test di atas membangun loop-nya sendiri, jadi tidak satu pun bisa
        membuktikan bahwa **aplikasi** benar-benar mengoper penyegarnya.

        Diperiksa lewat AST, bukan lewat pencarian teks: versi pertama test ini
        berbunyi ``"korelasi=" in sumber`` dan tetap hijau ketika barisnya
        dikomentari - sebuah penjaga yang membaca komentar sebagai kode.
        """
        import ast

        from aruna import app

        pohon = ast.parse(inspect.getsource(app))
        panggilan = [
            n for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "UpkeepLoop"
        ]

        assert panggilan, "UpkeepLoop tidak dibangun di app.py"
        assert any(
            k.arg == "korelasi" for c in panggilan for k in c.keywords
        ), "loop upkeep dibangun tanpa penyegar korelasi"

    def test_aplikasi_membangun_penyegarnya(self) -> None:
        """Argumen yang dioper tapi selalu ``None`` adalah rangkaian yang putus
        di tempat yang tidak terlihat dari daftar argumennya."""
        from aruna.app import ArunaApplication
        from aruna.core.config import Settings
        from aruna.upkeep.korelasi import PenyegarKorelasi

        app = object.__new__(ArunaApplication)
        app.settings = Settings()
        app.universe = _Universe([])
        app.market_data = _MarketData({})
        app.correlation_store = _Store()

        assert isinstance(app._build_korelasi(), PenyegarKorelasi)


class TestHorizonnyaSama:
    def test_horizon_yang_dihitung_sama_dengan_yang_direncanakan_futures(self) -> None:
        """Korelasi 1h yang tersimpan rapi sementara futures merencanakan 4h
        adalah tabel terisi yang tidak pernah terbaca - persis keadaan yang
        rencana ini perbaiki, cuma pindah satu interval."""
        from aruna.supervisor import default_children
        from aruna.upkeep.korelasi import HORIZON_KEPUTUSAN

        futures = next(
            c for c in default_children("BTCUSDT", hours=4.0)
            if c.name == "futures-loop"
        )
        args = list(futures.args)
        horizon = args[args.index("--horizon") + 1]

        assert horizon == HORIZON_KEPUTUSAN.value
