"""Tiga pemicu bagian 16.2 yang datanya sudah ada tapi tidak pernah dibaca.

**Terukur 2026-08-22.** Aku sempat melaporkan tujuh dari tiga belas pemicu
"butuh data dari jalur atau proses lain" - dan untuk tiga di antaranya itu
salah. Jalur keputusan menghitung regime, skor mutu, dan selisih pendapat
council setiap kali ia berjalan, lalu menuliskannya ke ``signal_snapshots``
(14.449 baris) dan ``council_sessions`` (8.688 baris). Yang tidak ada cuma
pembacanya.

Yang **tetap** tanpa sumber, diperiksa langsung di skema: funding rate dan open
interest tidak ada di tabel mana pun. ``futures_plans.funding_cost_pct`` adalah
biaya turunan atas horizon sebuah rencana, bukan rate-nya. Likuidasi dan konflik
lintas-pasar sama.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.core.enums import Regime
from aruna.db.repositories.konteks_pemicu import (
    UMUR_MAKSIMUM,
    KonteksKeputusan,
    KonteksPemicuRepository,
)
from aruna.scenario.pemicu import Peristiwa, deteksi
from aruna.upkeep.skenario import _konteks_untuk

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


class _Db:
    """Satu daftar baris per kueri, dipulangkan berurutan."""

    def __init__(self, *jawaban) -> None:
        self._jawaban = list(jawaban)
        self.sql: list[str] = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return self._jawaban.pop(0) if self._jawaban else []


@pytest.mark.asyncio
class TestMembacaKeadaanKeputusan:
    async def test_perubahan_dari_keadaan_mapan_dilaporkan(self) -> None:
        """Dua bacaan sebelumnya sepakat, lalu berganti. Itu yang bagian 16.2
        sebut *major* regime change."""
        db = _Db(
            [
                {"symbol": "BTC/USDT", "regime": "TRENDING_BEARISH", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "TRENDING_BULLISH", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "TRENDING_BULLISH", "locked_at": NOW},
            ],
            [],
            [],
        )

        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        assert hasil["BTC/USDT"].regime_sekarang == "TRENDING_BEARISH"
        assert hasil["BTC/USDT"].regime_sebelumnya == "TRENDING_BULLISH"

    async def test_kedipan_satu_bar_tidak_dilaporkan(self) -> None:
        """**Bug produksi, 2026-08-22.** Classifier 15m berkedip antar bar
        bersebelahan. Dengan dua bacaan, tiap kedipan dihitung sebagai
        perubahan: `PERUBAHAN_REGIME` menyala pada **empat puluh dari empat
        puluh sembilan** simulasi - dan pemicu yang menyala untuk hampir semua
        aset membatalkan bagian 16.2 alih-alih memenuhinya.

        Di sini keadaan sebelumnya TIDAK mapan (dua bacaan sebelumnya berbeda),
        jadi tidak ada yang bisa disebut berpindah dari apa.
        """
        db = _Db(
            [
                {"symbol": "BTC/USDT", "regime": "TRENDING_BEARISH", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "TRENDING_BULLISH", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
            ],
            [],
            [],
        )

        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        assert hasil["BTC/USDT"].regime_sebelumnya is None
        assert not deteksi(_konteks_untuk(_Hasil(), hasil["BTC/USDT"]))

    async def test_riwayat_terlalu_pendek_tidak_dilaporkan(self) -> None:
        """Kurang dari tiga bacaan berarti belum bisa dibedakan antara
        perubahan dan kedipan - dan yang tidak bisa dibedakan tidak
        dilaporkan."""
        db = _Db(
            [
                {"symbol": "BTC/USDT", "regime": "TRENDING_BEARISH", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "TRENDING_BULLISH", "locked_at": NOW},
            ],
            [],
            [],
        )

        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        assert hasil["BTC/USDT"].regime_sebelumnya is None

    async def test_dua_bacaan_terakhir_berurutan_bukan_dua_yang_berbeda(self) -> None:
        """**Bug produksi, 2026-08-22.** Versi pertama mengumpulkan dua regime
        BERBEDA terakhir dalam jendela satu jam, tanpa peduli kapan
        peralihannya terjadi - sehingga regime yang berubah lima puluh menit
        lalu terus menyalakan pemicu tiap siklus sampai bacaan lamanya
        kedaluwarsa. `PERUBAHAN_REGIME` menyala pada **lima belas dari lima
        belas** simulasi.

        Yang benar: dua bacaan terakhir berurutan. Kalau keduanya sama, tidak
        ada perubahan - walau ada regime lain di riwayat yang lebih jauh.
        """
        db = _Db(
            [
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "BREAKOUT", "locked_at": NOW},
            ],
            [],
            [],
        )

        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        # Versi lama memulangkan (RANGING, BREAKOUT) dari data ini - dua regime
        # BERBEDA terakhir - dan menyalakan pemicu atas peralihan yang sudah
        # lewat. Aturan sekarang menolaknya dua kali: bacaan diambil berurutan,
        # dan keadaan sebelumnya harus mapan.
        assert hasil["BTC/USDT"].regime_sekarang == "RANGING"
        assert hasil["BTC/USDT"].regime_sebelumnya is None
        assert not deteksi(_konteks_untuk(_Hasil(), hasil["BTC/USDT"]))

    async def test_uncertain_dirapatkan_bukan_dihitung_sebagai_regime(
        self,
    ) -> None:
        """**Bug produksi, 2026-08-22.** `UNCERTAIN` adalah 15,1% bacaan 15m,
        dan memperlakukannya sebagai regime membuat "RANGING -> tidak tahu"
        terbaca sebagai perpindahan rezim. Terukur pada 500 titik aset-bar:
        15,4% menyala, turun ke 9,4% setelah bacaan itu dibuang.

        Di sini yang tersisa setelah dirapatkan adalah RANGING, RANGING,
        RANGING - tidak ada perubahan sama sekali.
        """
        db = _Db(
            [
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "UNCERTAIN", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
            ],
            [],
            [],
        )

        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        # Mapan DAN tidak berubah - bukan tak terbaca. Bedanya ada di sini:
        # kalau UNCERTAIN masih terhitung, `regime_sebelumnya` akan menjadi
        # UNCERTAIN dan pemicunya menyala.
        assert hasil["BTC/USDT"].regime_sekarang == "RANGING"
        assert hasil["BTC/USDT"].regime_sebelumnya == "RANGING"
        assert not deteksi(_konteks_untuk(_Hasil(), hasil["BTC/USDT"]))

    async def test_perpindahan_lewat_uncertain_tetap_terbaca(self) -> None:
        """**Ini yang membedakan MERAPATKAN dari MENEKAN**, dan alasan aturannya
        bukan sekadar "buang peralihan yang menyentuh UNCERTAIN".

        Diambil dari produksi 2026-08-22, ETH/USDT: BREAKOUT sesudah REVERSAL
        yang mapan, dengan satu bacaan bingung di tengahnya. Pasarnya benar-benar
        berpindah; yang di tengah cuma sesaat alat ukurnya kehilangan pijakan.
        Menekan akan membunuh pemicu yang benar.
        """
        db = _Db(
            [
                {"symbol": "ETH/USDT", "regime": "BREAKOUT", "locked_at": NOW},
                {"symbol": "ETH/USDT", "regime": "UNCERTAIN", "locked_at": NOW},
                {"symbol": "ETH/USDT", "regime": "REVERSAL", "locked_at": NOW},
                {"symbol": "ETH/USDT", "regime": "REVERSAL", "locked_at": NOW},
            ],
            [],
            [],
        )

        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        assert hasil["ETH/USDT"].regime_sekarang == "BREAKOUT"
        assert hasil["ETH/USDT"].regime_sebelumnya == "REVERSAL"
        assert Peristiwa.PERUBAHAN_REGIME in deteksi(
            _konteks_untuk(_Hasil(), hasil["ETH/USDT"])
        )

    async def test_semuanya_uncertain_berarti_tak_terbaca(self) -> None:
        """Aset yang classifier-nya tidak pernah yakin tidak boleh terbaca
        sebagai aset yang regime-nya tenang. Tak terbaca, bukan nol."""
        db = _Db(
            [
                {"symbol": "INJ/USDT", "regime": "UNCERTAIN", "locked_at": NOW},
                {"symbol": "INJ/USDT", "regime": "UNCERTAIN", "locked_at": NOW},
                {"symbol": "INJ/USDT", "regime": "UNCERTAIN", "locked_at": NOW},
            ],
            [],
            [],
        )

        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        assert "INJ/USDT" not in hasil

    async def test_uncertain_disaring_sesudah_dinormalkan(self) -> None:
        """Menyaring di SQL akan memutuskan sebelum `.strip().upper()` berjalan,
        dan dua tempat yang memutuskan hal yang sama dengan aturan berbeda
        adalah bug yang menunggu giliran."""
        db = _Db(
            [
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": " uncertain ", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
            ],
            [],
            [],
        )

        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        # Tanpa normalisasi lebih dulu, " uncertain " lolos sebagai regime
        # tersendiri, `regime_sebelumnya` menjadi " UNCERTAIN ", dan pemicunya
        # menyala atas bacaan yang seharusnya tidak terbaca.
        assert hasil["BTC/USDT"].regime_sebelumnya == "RANGING"
        assert not deteksi(_konteks_untuk(_Hasil(), hasil["BTC/USDT"]))

    async def test_regime_yang_diam_tidak_menyalakan_apa_pun(self) -> None:
        """Ujung yang sebenarnya dijaga: pemicu yang menyala untuk seluruh aset
        membatalkan bagian 16.2 alih-alih memenuhinya."""
        db = _Db(
            [
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "RANGING", "locked_at": NOW},
                {"symbol": "BTC/USDT", "regime": "TRENDING_BULLISH", "locked_at": NOW},
            ],
            [],
            [],
        )
        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        k = _konteks_untuk(_Hasil(), hasil["BTC/USDT"])

        assert Peristiwa.PERUBAHAN_REGIME not in deteksi(k)

    async def test_hanya_satu_horizon_yang_dibandingkan(self) -> None:
        """`signal_snapshots` memuat 15m, 1h, dan 1d bercampur. Membandingkan
        regime 15m dengan regime 1d menghasilkan "perubahan" yang sebenarnya
        cuma perbedaan timeframe - dan itu menyala hampir selalu."""
        db = _Db([], [], [])
        await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        assert "horizon_code" in db.sql[0]

    async def test_mutu_dan_disagreement_terbaca(self) -> None:
        db = _Db(
            [],
            [{"symbol": "ETH/USDT", "signal_quality": 42}],
            [{"symbol": "ETH/USDT", "disagreement": 0.71}],
        )

        hasil = await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        assert hasil["ETH/USDT"].mutu == 42
        assert hasil["ETH/USDT"].disagreement == pytest.approx(0.71)

    async def test_dibatasi_umur(self) -> None:
        """Regime dari enam jam lalu bukan regime sekarang, dan memakainya
        membuat pemicu menyala atas keadaan yang sudah lewat."""
        db = _Db([], [], [])
        await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        assert timedelta(hours=2) >= UMUR_MAKSIMUM
        assert all(">= %s" in s for s in db.sql), "tiap kueri harus dibatasi waktu"

    async def test_satu_kueri_per_keperluan_bukan_per_simbol(self) -> None:
        """Fase skenario berjalan tiap siklus atas dua puluh aset; tiga kueri
        bisa diterima, enam puluh tidak."""
        db = _Db([], [], [])
        await KonteksPemicuRepository(db).terbaru(sekarang=NOW)

        assert len(db.sql) == 3


class _Hasil:
    def __init__(self, symbol: str = "BTC/USDT") -> None:
        self.symbol = symbol
        self.events = ()
        self.scanned = True


class TestPemicuYangDulunyaMati:
    """Inti seluruh perubahan: ketiganya sebelumnya tidak pernah bisa menyala."""

    def test_perubahan_regime_menyala(self) -> None:
        k = _konteks_untuk(
            _Hasil(),
            KonteksKeputusan(
                regime_sekarang="TRENDING_BEARISH",
                regime_sebelumnya="TRENDING_BULLISH",
            ),
        )

        assert Peristiwa.PERUBAHAN_REGIME in deteksi(k)

    def test_ketidakpastian_menyala(self) -> None:
        from aruna.signals.quality import MIN_QUALITY

        k = _konteks_untuk(_Hasil(), KonteksKeputusan(mutu=MIN_QUALITY - 1))

        assert Peristiwa.KETIDAKPASTIAN_TINGGI in deteksi(k)

    def test_selisih_pendapat_menyala(self) -> None:
        from aruna.scenario.pemicu import AMBANG_SELISIH_TAJAM

        k = _konteks_untuk(
            _Hasil(), KonteksKeputusan(disagreement=AMBANG_SELISIH_TAJAM)
        )

        assert Peristiwa.SELISIH_PENDAPAT_TAJAM in deteksi(k)

    def test_tanpa_konteks_ketiganya_tetap_diam(self) -> None:
        """Aset yang belum punya keputusan cukup baru tidak boleh menyalakan
        apa pun."""
        menyala = deteksi(_konteks_untuk(_Hasil(), None))

        assert menyala == frozenset()


class TestTidakTerbacaBukanNol:
    def test_mutu_none_tidak_menyalakan_ketidakpastian(self) -> None:
        """``mutu=None`` berarti tidak ada keputusan yang cukup baru;
        ``mutu=0`` berarti keputusannya buruk sekali. Menyamakannya membuat tiap
        aset yang belum pernah diputuskan terbaca sebagai sangat tidak pasti."""
        k = _konteks_untuk(_Hasil(), KonteksKeputusan(mutu=None))

        assert Peristiwa.KETIDAKPASTIAN_TINGGI not in deteksi(k)

    def test_disagreement_none_tidak_menyalakan(self) -> None:
        k = _konteks_untuk(_Hasil(), KonteksKeputusan(disagreement=None))

        assert Peristiwa.SELISIH_PENDAPAT_TAJAM not in deteksi(k)

    def test_regime_lawas_diperlakukan_tak_terbaca(self) -> None:
        """Baris lama memuat nilai yang sudah tidak ada di enum - taksonomi
        berarah masuk 2026-08-21. Satu baris lawas tidak boleh menjatuhkan
        deteksi pemicu."""
        k = _konteks_untuk(
            _Hasil(),
            KonteksKeputusan(regime_sekarang="REGIME_YANG_SUDAH_DIHAPUS"),
        )

        assert k.regime_sekarang is None
        assert deteksi(k) == frozenset()

    def test_regime_yang_dikenal_diterjemahkan(self) -> None:
        k = _konteks_untuk(_Hasil(), KonteksKeputusan(regime_sekarang="RANGING"))

        assert k.regime_sekarang is Regime.RANGING


class TestTerpasangDiProduksi:
    def test_app_mengoper_konteks(self) -> None:
        """Bug aslinya bukan logika yang salah melainkan pembaca yang tidak
        ada. Penjaga AST supaya ia tidak hilang lagi."""
        import ast
        import inspect

        from aruna import app

        pohon = ast.parse(inspect.getsource(app))
        for n in ast.walk(pohon):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "PenyimulasiSkenario"
            ):
                assert any(kw.arg == "konteks" for kw in n.keywords)
                return

        pytest.fail("PenyimulasiSkenario tidak dirakit di app.py")


@pytest.mark.asyncio
class TestMenyalaTanpaPeristiwaPemindai:
    """**Bug produksi, 2026-08-22.** Sebelum ketiga pemicu ini disambungkan,
    setiap pemicu lahir dari peristiwa pemindai - jadi `r.events` tidak pernah
    kosong. Dua tempat mengandalkan itu diam-diam:

    * kunci satu-simulasi-per-bar memakai ``max(e.at for e in r.events)`` dan
      melempar ``max() iterable argument is empty``. Delapan
      `upkeep.scenario_failed` dalam lima menit.
    * kondisi pertanyaan disusun dari ``e.detail``, jadi kosong - dan
      `susun_pertanyaan` menolak pertanyaan tanpa kondisi (bagian 16.4).
      Pemicunya menyala lalu ditolak di langkah berikutnya, dan yang terlihat
      di log cuma "masukan ditolak".

    Seluruh test yang kutulis sebelumnya lolos: semuanya memberi peristiwa
    pemindai, bentuk yang tidak pernah bisa memperlihatkan keduanya.
    """

    @staticmethod
    def _sepi(symbol: str = "BTC/USDT"):
        from aruna.scanner.events import ScanResult

        return ScanResult(symbol=symbol, events=(), usable_bars=50, scanned=True)

    class _Konteks:
        def __init__(self, keputusan) -> None:
            self._k = keputusan

        async def terbaru(self, *, sekarang):
            return {"BTC/USDT": self._k}

    async def test_regime_berubah_tanpa_peristiwa_tetap_menyimulasikan(self) -> None:
        from aruna.upkeep.skenario import PenyimulasiSkenario

        konteks = self._Konteks(
            KonteksKeputusan(
                regime_sekarang="TRENDING_BEARISH",
                regime_sebelumnya="TRENDING_BULLISH",
            )
        )
        keluar = await PenyimulasiSkenario(konteks=konteks).jalankan(
            [self._sepi()], now=NOW
        )

        assert keluar.menyala == 1
        assert len(keluar.bukti) == 1, "pemicunya menyala tapi tidak ada skenario"

    async def test_kondisinya_menyebut_angkanya(self) -> None:
        """"mutu 42 di bawah ambang 60" bisa diperiksa; "mutu rendah" tidak."""
        from aruna.signals.quality import MIN_QUALITY
        from aruna.upkeep.skenario import PenyimulasiSkenario

        konteks = self._Konteks(KonteksKeputusan(mutu=MIN_QUALITY - 18))
        keluar = await PenyimulasiSkenario(konteks=konteks).jalankan(
            [self._sepi()], now=NOW
        )

        kondisi = " ".join(keluar.bukti[0].skenario[0].kondisi_awal)
        assert str(MIN_QUALITY - 18) in kondisi
        assert str(MIN_QUALITY) in kondisi

    async def test_selisih_pendapat_masuk_ke_kondisi(self) -> None:
        from aruna.scenario.pemicu import AMBANG_SELISIH_TAJAM
        from aruna.upkeep.skenario import PenyimulasiSkenario

        konteks = self._Konteks(
            KonteksKeputusan(disagreement=AMBANG_SELISIH_TAJAM + 0.1)
        )
        keluar = await PenyimulasiSkenario(konteks=konteks).jalankan(
            [self._sepi()], now=NOW
        )

        kondisi = " ".join(keluar.bukti[0].skenario[0].kondisi_awal)
        assert "selisih pendapat" in kondisi

    async def test_kunci_per_bar_tetap_bekerja_tanpa_peristiwa(self) -> None:
        """Yang melempar `max() is empty`."""
        from aruna.upkeep.skenario import PenyimulasiSkenario

        konteks = self._Konteks(KonteksKeputusan(mutu=10))
        p = PenyimulasiSkenario(konteks=konteks)

        pertama = await p.jalankan([self._sepi()], now=NOW)
        kedua = await p.jalankan([self._sepi()], now=NOW)

        assert len(pertama.bukti) == 1
        assert kedua.sudah_disimulasikan == 1


@pytest.mark.asyncio
class TestKegagalanMengecilkanBukanMenghentikan:
    async def test_konteks_gagal_tetap_menyisakan_pemicu_pemindai(self) -> None:
        """Fase yang mati total karena satu kueri gagal menukar sebagian bukti
        dengan tidak ada bukti sama sekali."""
        from aruna.scanner.events import EventKind, ScanResult, SignificantEvent
        from aruna.upkeep.skenario import PenyimulasiSkenario

        class _KonteksRusak:
            async def terbaru(self, *, sekarang):
                raise RuntimeError("database jatuh")

        peristiwa = SignificantEvent(
            symbol="BTC/USDT",
            kind=EventKind.BREAKOUT,
            severity=3.0,
            detail="tembusan",
            at=NOW,
            evidence={"measured": 1.0, "threshold": 0.33},
        )
        hasil = ScanResult(
            symbol="BTC/USDT", events=(peristiwa,), usable_bars=50, scanned=True
        )

        keluar = await PenyimulasiSkenario(konteks=_KonteksRusak()).jalankan(
            [hasil], now=NOW
        )

        assert keluar.menyala == 1
