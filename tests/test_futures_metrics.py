"""Funding dan open interest disimpan, lalu benar-benar dibaca (bagian 16.2).

**Dua dari tiga belas pemicu tidak pernah bisa menyala** karena angkanya tidak
ada di mana pun: `futures-loop` mengambil keduanya dari Binance REST tiap
siklus, memakainya untuk rencananya, lalu membuangnya.
`futures_plans.funding_cost_pct` bukan gantinya - ia biaya turunan atas horizon
sebuah rencana, bukan rate-nya. Diperiksa langsung di skema 2026-08-22.

Yang paling mudah patah di sini bukan penyimpanannya melainkan **jembatan
antar-prosesnya**: yang menulis hidup di `futures-loop`, yang membaca hidup di
`aruna run`, dan keduanya memakai bentuk simbol yang berbeda. Jembatan yang
salah membuat pemicunya diam tanpa satu pun galat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.db.repositories.futures_metrics import (
    BacaanFutures,
    FuturesMetricsRepository,
)
from aruna.db.repositories.konteks_pemicu import KonteksPemicuRepository, _kanonik
from aruna.futures.funding import EXTREME_RATE
from aruna.futures.openinterest import SIGNIFICANT_PCT
from aruna.scenario.pemicu import Peristiwa, deteksi
from aruna.upkeep.skenario import _konteks_untuk

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


class _Db:
    def __init__(self, baris=None, hasil_execute: int = 1) -> None:
        self.baris = baris or []
        self._hasil = hasil_execute
        self.sql: list[tuple[str, tuple]] = []

    async def execute(self, sql, *args) -> int:
        self.sql.append((sql, args))
        return self._hasil

    async def fetch(self, sql, *args):
        self.sql.append((sql, args))
        return list(self.baris)


class _Funding:
    def __init__(self, rate) -> None:
        self.rate = Decimal(str(rate))
        self.funding_time = NOW
        self.next_funding_time = NOW + timedelta(hours=8)


class _OI:
    def __init__(self, nilai) -> None:
        self.open_interest = Decimal(str(nilai))
        self.notional = None


class _Snapshot:
    def __init__(self, funding=None, oi=None, symbol: str = "BTCUSDT") -> None:
        self.symbol = symbol
        self.captured_at = NOW
        self.funding = funding
        self.open_interest = oi


@pytest.mark.asyncio
class TestMenyimpan:
    async def test_menulis_funding_dan_oi(self) -> None:
        db = _Db()
        ok = await FuturesMetricsRepository(db).simpan(
            _Snapshot(_Funding("0.0002"), _OI("1000"))
        )

        assert ok
        sql = db.sql[0][0]
        assert "funding_rate" in sql
        assert "open_interest" in sql

    async def test_snapshot_tanpa_keduanya_tidak_ditulis(self) -> None:
        """Baris yang seluruh isinya NULL menambah panjang deret tanpa menambah
        satu pun jawaban."""
        db = _Db()

        assert not await FuturesMetricsRepository(db).simpan(_Snapshot())
        assert db.sql == []

    async def test_insert_ignore_menahan_deret_ganda(self) -> None:
        """Siklus yang dijalankan dua kali pada stempel yang sama tidak boleh
        melahirkan deret ganda."""
        db = _Db()
        await FuturesMetricsRepository(db).simpan(_Snapshot(_Funding("0.0002")))

        assert "INSERT IGNORE" in db.sql[0][0]

    async def test_rate_disimpan_sebagai_pecahan_bukan_persen(self) -> None:
        """`EXTREME_RATE` bukan persen. Menyimpan persen di sini menghasilkan
        selisih seratus kali yang tidak akan melempar apa pun."""
        db = _Db()
        await FuturesMetricsRepository(db).simpan(_Snapshot(_Funding("0.0002")))

        assert Decimal("0.0002") in db.sql[0][1]


@pytest.mark.asyncio
class TestMembacaDeret:
    async def test_perubahan_oi_dihitung_dari_dua_titik(self) -> None:
        """Anomali open interest adalah PERUBAHAN, dan perubahan butuh dua
        titik. Satu baris yang ditimpa terus tidak pernah bisa menjawabnya."""
        db = _Db([
            {"symbol": "BTCUSDT", "captured_at": NOW,
             "funding_rate": Decimal("0.0003"), "open_interest": Decimal("110")},
            {"symbol": "BTCUSDT", "captured_at": NOW - timedelta(minutes=15),
             "funding_rate": Decimal("0.0001"), "open_interest": Decimal("100")},
        ])

        hasil = await FuturesMetricsRepository(db).terbaru(
            sekarang=NOW, umur_maksimum=timedelta(hours=1)
        )

        assert hasil["BTCUSDT"].funding_rate == Decimal("0.0003")
        assert hasil["BTCUSDT"].perubahan_oi_pct == Decimal("10")

    async def test_satu_titik_tidak_menghasilkan_perubahan(self) -> None:
        """Nol yang dikarang akan terbaca sebagai "open interest datar", dan itu
        pernyataan yang tidak pernah dibuat siapa pun."""
        db = _Db([
            {"symbol": "BTCUSDT", "captured_at": NOW,
             "funding_rate": Decimal("0.0003"), "open_interest": Decimal("110")},
        ])

        hasil = await FuturesMetricsRepository(db).terbaru(
            sekarang=NOW, umur_maksimum=timedelta(hours=1)
        )

        assert hasil["BTCUSDT"].perubahan_oi_pct is None

    async def test_pembagi_nol_tidak_meledak(self) -> None:
        db = _Db([
            {"symbol": "BTCUSDT", "captured_at": NOW,
             "funding_rate": None, "open_interest": Decimal("110")},
            {"symbol": "BTCUSDT", "captured_at": NOW - timedelta(minutes=15),
             "funding_rate": None, "open_interest": Decimal("0")},
        ])

        hasil = await FuturesMetricsRepository(db).terbaru(
            sekarang=NOW, umur_maksimum=timedelta(hours=1)
        )

        assert hasil["BTCUSDT"].perubahan_oi_pct is None

    async def test_dibatasi_umur(self) -> None:
        db = _Db([])
        await FuturesMetricsRepository(db).terbaru(
            sekarang=NOW, umur_maksimum=timedelta(hours=1)
        )

        assert ">= %s" in db.sql[0][0]


class TestJembatanSimbol:
    """Yang paling mudah patah, dan patahnya paling sunyi.

    Yang menulis hidup di `futures-loop` dengan bentuk venue (``BTCUSDT``); yang
    membaca hidup di `aruna run` dengan bentuk kanonik (``BTC/USDT``). Kalau
    keduanya tidak diterjemahkan, pemicunya diam selamanya tanpa satu pun galat.
    """

    @pytest.mark.parametrize(
        ("venue", "kanonik"),
        [
            ("BTCUSDT", "BTC/USDT"),
            ("ETHUSDT", "ETH/USDT"),
            ("SOLBUSD", "SOL/BUSD"),
            ("BTCUSDC", "BTC/USDC"),
            ("BTC/USDT", "BTC/USDT"),
        ],
    )
    def test_diterjemahkan(self, venue, kanonik) -> None:
        assert _kanonik(venue) == kanonik

    def test_busd_tidak_tersobek_jadi_usd(self) -> None:
        """Quote diperiksa dari yang terpanjang. Kalau tidak, ``SOLBUSD``
        menjadi ``SOLB/USD`` - simbol yang tidak pernah cocok dengan apa pun."""
        assert _kanonik("SOLBUSD") == "SOL/BUSD"

    def test_yang_tak_dikenali_dipulangkan_apa_adanya(self) -> None:
        """Simbol yang tidak cocok lebih baik daripada simbol yang dipotong
        salah - yang pertama diam, yang kedua cocok dengan aset yang keliru."""
        assert _kanonik("ANEH") == "ANEH"


@pytest.mark.asyncio
class TestPemicuYangDulunyaMati:
    class _Metrik:
        def __init__(self, bacaan) -> None:
            self._b = bacaan

        async def terbaru(self, *, sekarang, umur_maksimum):
            return {"BTCUSDT": self._b}

    class _Hasil:
        symbol = "BTC/USDT"
        events = ()
        scanned = True

    async def _konteks(self, bacaan):
        db = _Db([])
        repo = KonteksPemicuRepository(db, metrik=self._Metrik(bacaan))
        hasil = await repo.terbaru(sekarang=NOW)
        return _konteks_untuk(self._Hasil(), hasil.get("BTC/USDT"))

    async def test_anomali_funding_menyala(self) -> None:
        k = await self._konteks(BacaanFutures(funding_rate=EXTREME_RATE))

        assert Peristiwa.ANOMALI_FUNDING in deteksi(k)

    async def test_anomali_open_interest_menyala(self) -> None:
        k = await self._konteks(BacaanFutures(perubahan_oi_pct=SIGNIFICANT_PCT))

        assert Peristiwa.ANOMALI_OPEN_INTEREST in deteksi(k)

    async def test_bacaan_biasa_tetap_diam(self) -> None:
        k = await self._konteks(
            BacaanFutures(
                funding_rate=EXTREME_RATE / 2, perubahan_oi_pct=SIGNIFICANT_PCT / 2
            )
        )

        assert deteksi(k) == frozenset()

    async def test_metrik_gagal_tidak_menghentikan_konteks(self) -> None:
        """Kegagalannya mengecilkan deteksi, tidak menghentikannya."""

        class _Rusak:
            async def terbaru(self, *, sekarang, umur_maksimum):
                raise RuntimeError("database jatuh")

        repo = KonteksPemicuRepository(_Db([]), metrik=_Rusak())

        assert await repo.terbaru(sekarang=NOW) == {}


class TestTerpasang:
    def test_futures_service_menerima_metrik(self) -> None:
        import inspect

        from aruna.futures.service import FuturesPlanService

        assert "metrik" in inspect.signature(FuturesPlanService.__init__).parameters

    def test_cli_futures_loop_mengoper_metrik(self) -> None:
        """Penulisnya hidup di `futures-loop`. Tanpa baris ini seluruh tabel
        tetap kosong dan kedua pemicunya tetap mati."""
        import ast
        import inspect

        from aruna import cli

        pohon = ast.parse(inspect.getsource(cli))
        for n in ast.walk(pohon):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "FuturesPlanService"
                and any(kw.arg == "metrik" for kw in n.keywords)
            ):
                return

        pytest.fail("metrik tidak dioper ke FuturesPlanService di cli.py")

    def test_ada_aturan_retensi(self) -> None:
        """Tabel tanpa aturan retensi tumbuh selamanya - persis bagaimana
        `market_snapshots` menjadi 62% basis data."""
        from aruna.upkeep.retensi import DILINDUNGI, RENCANA

        assert any(r.tabel == "futures_metrics" for r in RENCANA)
        assert "futures_metrics" not in DILINDUNGI
