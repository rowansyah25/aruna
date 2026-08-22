"""Gerak pasar sesudah ARUNA memilih diam (PASAL 14.32, 14.33).

Sistem yang hanya menilai signal yang dikirimnya menilai separuh dari apa yang
dilakukannya. ARUNA memutuskan NO SIGNAL jauh lebih sering daripada LONG atau
SHORT, dan sampai sekarang tidak satu pun dari keputusan itu pernah dinilai
benar atau salah - diam yang tidak pernah dinilai adalah tempat paling nyaman
untuk sebuah sistem bersembunyi.

``silence.evaluate`` sudah ada sejak lama dan tidak pernah dipanggil, karena
bahannya tidak ada: ``move_pct`` menuntut harga sesudah horizon lewat, dan
signal yang ditahan tidak pernah diresolusi. Berkas ini menguji yang menyusun
bahan itu.

**Yang paling berbahaya di sini adalah angka yang salah, bukan angka yang
hilang.** "Diam ARUNA benar 90%" adalah kalimat yang sangat meyakinkan dan
sangat sulit dibantah, dan PASAL 14.33 justru melarang memakainya untuk
menurunkan ambang. Karena itu tiap jalur yang tidak bisa dihitung memulangkan
``None`` - BELUM BISA DINILAI - dan bukan nol.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.db.repositories.diam import DiamRepository

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
AWAL = NOW - timedelta(days=1)


class _Db:
    """Menangkap SQL dan argumennya, memulangkan baris yang disiapkan."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.sql = ""
        self.args: tuple = ()

    async def fetch(self, sql: str, *args):
        self.sql = sql
        self.args = args
        return self.rows


def _baris(**kw) -> dict:
    dasar = {
        "signal_id": "abc",
        "symbol": "BTC/USDT",
        "withheld_reason": "quality gate: quality 56/100 di bawah 60",
        "reference_price": Decimal("100"),
        "tertinggi": Decimal("101"),
        "terendah": Decimal("99"),
        "resolves_at": NOW - timedelta(hours=1),
        "bar": 12,
    }
    return dasar | kw


async def _jalankan(rows: list[dict]):
    db = _Db(rows)
    hasil = await DiamRepository(db).diam(start=AWAL, end=NOW, now=NOW)
    return db, hasil


class TestKueriNya:
    @pytest.mark.asyncio
    async def test_hanya_yang_ditahan(self) -> None:
        """Signal yang TERBIT dinilai lewat outcome-nya sendiri. Ikut
        menghitungnya di sini akan mencampur dua pertanyaan yang berbeda."""
        db, _ = await _jalankan([])

        assert "published = FALSE" in db.sql

    @pytest.mark.asyncio
    async def test_hanya_bar_yang_sudah_tutup(self) -> None:
        """SPEC 24: bar yang masih terbentuk bukan bukti. High-nya masih bisa
        naik, dan sebuah "kesempatan terlewat" yang dihitung dari bar berjalan
        bisa berubah jadi bukan kesempatan lima menit kemudian."""
        db, _ = await _jalankan([])

        assert "is_closed = TRUE" in db.sql

    @pytest.mark.asyncio
    async def test_jendelanya_dibatasi_horizon_signalnya(self) -> None:
        """Gerak sesudah horizon lewat bukan kesempatan yang terlewat - ARUNA
        tidak pernah mengklaim apa pun tentang waktu itu."""
        db, _ = await _jalankan([])

        assert "c.open_time >= g.locked_at" in db.sql
        assert "c.close_time <= g.resolves_at" in db.sql


class TestGerakTerjauh:
    @pytest.mark.asyncio
    async def test_naik_lebih_jauh_jadi_positif(self) -> None:
        _, hasil = await _jalankan([
            _baris(tertinggi=Decimal("105"), terendah=Decimal("99"))
        ])

        assert hasil[0].move_pct == Decimal("5")

    @pytest.mark.asyncio
    async def test_turun_lebih_jauh_jadi_negatif(self) -> None:
        """Salah tanda di sini melaporkan kesempatan LONG yang terlewat pada
        pasar yang justru jatuh - dan itu persis kesalahan yang akan membuat
        seseorang melonggarkan ambang ke arah yang salah."""
        _, hasil = await _jalankan([
            _baris(tertinggi=Decimal("101"), terendah=Decimal("93"))
        ])

        assert hasil[0].move_pct == Decimal("-7")

    @pytest.mark.asyncio
    async def test_yang_dipakai_yang_terjauh_bukan_yang_terakhir(self) -> None:
        """Pasar yang naik 8% lalu kembali ke titik awal menawarkan kesempatan
        yang sungguhan. Menilainya dari harga penutup akan melaporkan diam yang
        benar atas gerakan yang jelas ada."""
        _, hasil = await _jalankan([
            _baris(tertinggi=Decimal("108"), terendah=Decimal("99.5"))
        ])

        assert hasil[0].move_pct == Decimal("8")

    @pytest.mark.asyncio
    async def test_ambangnya_dari_modul_silence(self) -> None:
        """Ambang "layak diambil" tinggal di satu tempat. Menyalinnya ke sini
        akan menghasilkan dua angka yang bisa berselisih diam-diam."""
        from aruna.decision.silence import GERAK_BERARTI_PCT, Vonis

        _, hasil = await _jalankan([
            _baris(tertinggi=100 + GERAK_BERARTI_PCT, terendah=Decimal("100")),
            _baris(
                signal_id="def",
                tertinggi=Decimal("100")
                + GERAK_BERARTI_PCT / 2,
                terendah=Decimal("100"),
            ),
        ])

        assert hasil[0].verdict is Vonis.MISSED
        assert hasil[1].verdict is Vonis.CORRECT


class TestYangTidakBisaDinilai:
    @pytest.mark.asyncio
    async def test_horizon_belum_lewat(self) -> None:
        """Menghitungnya sekarang berarti menilai prediksi yang masa berlakunya
        belum habis - dan akurasinya akan membaik hanya karena waktu berjalan."""
        from aruna.decision.silence import Vonis

        _, hasil = await _jalankan([
            _baris(resolves_at=NOW + timedelta(hours=3))
        ])

        assert hasil[0].move_pct is None
        assert hasil[0].verdict is Vonis.UNKNOWN

    @pytest.mark.asyncio
    async def test_tanpa_bar_sama_sekali(self) -> None:
        from aruna.decision.silence import Vonis

        _, hasil = await _jalankan([
            _baris(tertinggi=None, terendah=None, bar=0)
        ])

        assert hasil[0].move_pct is None
        assert hasil[0].verdict is Vonis.UNKNOWN

    @pytest.mark.asyncio
    async def test_harga_acuan_kosong(self) -> None:
        _, hasil = await _jalankan([_baris(reference_price=None)])

        assert hasil[0].move_pct is None

    @pytest.mark.asyncio
    async def test_harga_acuan_nol_tidak_membagi_nol(self) -> None:
        """Nol adalah harga yang tidak mungkin, dan pembagiannya menjatuhkan
        seluruh laporan harian - bukan cuma satu barisnya."""
        _, hasil = await _jalankan([_baris(reference_price=Decimal("0"))])

        assert hasil[0].move_pct is None

    @pytest.mark.asyncio
    async def test_yang_tidak_terukur_tetap_muncul_di_daftar(self) -> None:
        """Membuangnya akan membuat penyebutnya mengecil, dan akurasi diam naik
        setiap kali ada yang tidak bisa diukur - persis arah kesalahan yang
        paling menguntungkan ARUNA."""
        _, hasil = await _jalankan([
            _baris(resolves_at=NOW + timedelta(hours=3)),
            _baris(signal_id="def"),
        ])

        assert len(hasil) == 2


class TestBentuknyaCocokDenganSilence:
    @pytest.mark.asyncio
    async def test_menghasilkan_diam_yang_bisa_dinilai(self) -> None:
        from aruna.decision.silence import Diam, evaluate

        _, hasil = await _jalankan([
            _baris(tertinggi=Decimal("110"), terendah=Decimal("100")),
            _baris(signal_id="def", tertinggi=Decimal("100.1"),
                   terendah=Decimal("100")),
            _baris(signal_id="ghi", resolves_at=NOW + timedelta(hours=3)),
        ])

        assert all(isinstance(d, Diam) for d in hasil)

        lap = evaluate(hasil)

        assert lap.evidence.total == 2
        assert len(lap.missed) == 1
        assert lap.unknown == 1

    @pytest.mark.asyncio
    async def test_alasannya_terbawa_bukan_dibuang(self) -> None:
        """PASAL 14.33 minta sebabnya dicari, bukan angka totalnya. Tanpa
        alasan, laporannya cuma bilang "ada yang terlewat" tanpa satu pun
        petunjuk gerbang mana yang menahannya."""
        _, hasil = await _jalankan([
            _baris(withheld_reason="cooldown sesudah kalah")
        ])

        assert hasil[0].reason == "cooldown sesudah kalah"

    @pytest.mark.asyncio
    async def test_simbolnya_terbawa(self) -> None:
        _, hasil = await _jalankan([_baris(symbol="ETH/USDT")])

        assert hasil[0].symbol == "ETH/USDT"


class TestSampaiKeLaporanHarian:
    """Penjaga penyambungan.

    ``silence.evaluate`` sudah ada berbulan-bulan dan tidak pernah dipanggil
    sekali pun. Membangun repositorinya tanpa penjaga ini akan menghasilkan
    keadaan yang persis sama, satu lapisan lebih dalam.
    """

    def _service(self, rows: list[dict]):
        from aruna.notify.daily_service import DailyReportService

        svc = DailyReportService.__new__(DailyReportService)
        svc.diam_repo = DiamRepository(_Db(rows))
        return svc

    @pytest.mark.asyncio
    async def test_laporannya_terbentuk(self) -> None:
        svc = self._service([
            _baris(tertinggi=Decimal("110"), terendah=Decimal("100")),
            _baris(signal_id="def", tertinggi=Decimal("100.1"),
                   terendah=Decimal("100")),
        ])

        lap = await svc._silence(AWAL, NOW, NOW)

        assert lap is not None
        assert lap.evidence.total == 2

    @pytest.mark.asyncio
    async def test_tanpa_repositori_bukan_kegagalan(self) -> None:
        """Pemanggil lama menghasilkan laporan tanpa bagian ini, bukan laporan
        yang gagal."""
        from aruna.notify.daily_service import DailyReportService

        svc = DailyReportService.__new__(DailyReportService)
        svc.diam_repo = None

        assert await svc._silence(AWAL, NOW, NOW) is None

    @pytest.mark.asyncio
    async def test_kueri_gagal_jadi_none_bukan_nol(self, monkeypatch) -> None:
        """Sebuah "AKURASI NO SIGNAL: 0%" yang lahir dari kueri yang gagal
        terbaca persis seperti ARUNA yang selalu salah diam - kesimpulan yang
        jauh lebih dramatis daripada kegagalannya sendiri."""
        from types import SimpleNamespace

        from aruna.notify import daily_service as modul
        from aruna.notify.daily_service import DailyReportService

        dicatat: list[str] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(exception=lambda n, **k: dicatat.append(n)),
        )

        class _Meledak:
            async def diam(self, **kw):
                raise RuntimeError("database putus")

        svc = DailyReportService.__new__(DailyReportService)
        svc.diam_repo = _Meledak()

        assert await svc._silence(AWAL, NOW, NOW) is None
        assert dicatat == ["daily.silence_failed"]

    def test_build_memanggilnya(self) -> None:
        import inspect

        from aruna.notify.daily_service import DailyReportService

        sumber = inspect.getsource(DailyReportService.build)

        assert "silence=await self._silence(" in sumber

    def test_app_menyediakan_repositorinya(self) -> None:
        """Tanpa baris ini, ``diam_repo`` selamanya ``None`` di produksi dan
        seluruh test di atas tetap hijau."""
        import inspect

        from aruna.app import ArunaApplication

        sumber = inspect.getsource(ArunaApplication._build_daily)

        assert "diam_repo=DiamRepository(self.db)" in sumber

    def test_render_mencetaknya(self) -> None:
        from datetime import datetime as _dt

        from aruna.decision.silence import Diam, evaluate
        from aruna.notify.daily import DailyReport, render_daily

        lap = evaluate([
            Diam("BTC/USDT", "quality gate", Decimal("5.0")),
            Diam("ETH/USDT", "quality gate", Decimal("0.3")),
        ])
        teks = render_daily(
            DailyReport(date=_dt(2026, 8, 20, tzinfo=UTC), markets=(),
                        silence=lap)
        )

        assert "AKURASI NO SIGNAL" in teks
        assert "PASAL 14.33" in teks

    def test_tanpa_hitungan_bagiannya_tidak_dicetak(self) -> None:
        """``None`` berarti belum terhitung. Mencetak "0%" untuk itu adalah
        angka yang dikarang (§13.26)."""
        from datetime import datetime as _dt

        from aruna.notify.daily import DailyReport, render_daily

        teks = render_daily(
            DailyReport(date=_dt(2026, 8, 20, tzinfo=UTC), markets=())
        )

        assert "AKURASI NO SIGNAL" not in teks
