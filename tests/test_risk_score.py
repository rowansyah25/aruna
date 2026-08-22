"""Risk Score 0-100 (PASAL 13.2, 13.22, 13.26).

Yang diuji berulang kali dari sudut berbeda adalah satu sifat: **skor yang
dihitung dari data yang tidak ada bukan skor.**

PASAL 13.26 melarang mengarang risk score. Cara paling halus melanggarnya
bukan menulis angka acak - ia mengisi faktor yang tidak terukur dengan nol,
lalu menghitung rata-rata yang terlihat sah. Hasilnya selalu masuk akal, selalu
lebih rendah dari kebenarannya, dan tidak ada satu baris pun yang berbohong.
"""

from __future__ import annotations

import pytest

from aruna.risk import FAKTOR, MIN_COVERAGE, RiskLevel, assess, categorise


def _semua(nilai: float) -> dict[str, float]:
    return {f.key: nilai for f in FAKTOR}


class TestKategoriSesuaiSpec:
    @pytest.mark.parametrize(
        "skor,harapan",
        [
            (0, RiskLevel.VERY_LOW), (20, RiskLevel.VERY_LOW),
            (21, RiskLevel.LOW), (40, RiskLevel.LOW),
            (41, RiskLevel.MEDIUM), (60, RiskLevel.MEDIUM),
            (61, RiskLevel.HIGH), (80, RiskLevel.HIGH),
            (81, RiskLevel.VERY_HIGH), (100, RiskLevel.VERY_HIGH),
        ],
    )
    def test_ambangnya_persis_seperti_tertulis(self, skor, harapan) -> None:
        assert categorise(skor) is harapan

    def test_tanpa_skor_bukan_risiko_tertinggi(self) -> None:
        """'Tidak tahu' dan 'sangat berbahaya' menuntut tindakan berbeda dari
        operator; menyamakannya membuang pembedaan itu."""
        assert categorise(None) is RiskLevel.UNKNOWN
        assert categorise(None) is not RiskLevel.VERY_HIGH


class TestFaktorHilangTidakDihitungNol:
    """Inti PASAL 13.26."""

    def test_cakupan_tipis_menolak_memberi_angka(self) -> None:
        hasil = assess({"volatility": 10.0, "spread": 5.0})

        assert hasil.score is None
        assert hasil.level is RiskLevel.UNKNOWN
        assert not hasil.usable

    def test_yang_hilang_disebut_namanya(self) -> None:
        """Faktor yang hilang dari laporan terbaca seperti faktor yang aman."""
        hasil = assess({"volatility": 10.0})

        assert len(hasil.unknown) == len(FAKTOR) - 1
        assert "jarak likuidasi" in hasil.unknown

    def test_hilang_tidak_menurunkan_skor(self) -> None:
        """Kalau yang hilang dihitung nol, membuang faktor akan selalu membuat
        setup terlihat lebih aman - dan cara termudah menurunkan risk score
        menjadi berhenti mengukur."""
        lengkap = _semua(80.0)
        sebagian = {
            f.key: 80.0 for f in FAKTOR[: int(len(FAKTOR) * 0.75)]
        }

        a = assess(lengkap)
        b = assess(sebagian)

        assert a.usable and b.usable
        assert b.score == pytest.approx(a.score, abs=0.1)

    def test_cakupan_selalu_dilaporkan(self) -> None:
        hasil = assess(_semua(30.0))
        assert hasil.coverage == pytest.approx(1.0)
        assert "100%" in hasil.line()

    def test_tepat_di_ambang_cakupan_masih_menolak(self) -> None:
        """Batasnya diuji, bukan diasumsikan."""
        from aruna.risk.score import _TOTAL_WEIGHT

        dipakai: dict[str, float] = {}
        bobot = 0.0
        for f in FAKTOR:
            if (bobot + f.weight) / _TOTAL_WEIGHT >= MIN_COVERAGE:
                break
            dipakai[f.key] = 50.0
            bobot += f.weight

        assert assess(dipakai).score is None


class TestSkorMencerminkanBobot:
    def test_semua_rendah_itu_risiko_rendah(self) -> None:
        assert assess(_semua(10.0)).level is RiskLevel.VERY_LOW

    def test_semua_tinggi_itu_risiko_tinggi(self) -> None:
        assert assess(_semua(90.0)).level is RiskLevel.VERY_HIGH

    def test_faktor_berbobot_besar_lebih_berpengaruh(self) -> None:
        """Jarak likuidasi berbobot 3.0, spread 1.0. Menaikkan yang pertama
        harus menggerakkan skor lebih jauh."""
        dasar = _semua(20.0)
        berat = dasar | {"liquidation_distance": 100.0}
        ringan = dasar | {"spread": 100.0}

        assert assess(berat).score > assess(ringan).score

    def test_nilai_di_luar_rentang_dijepit(self) -> None:
        """Pembacaan yang salah kalibrasi tidak boleh menghasilkan skor di luar
        0-100 - angka seperti itu tidak punya kategori."""
        hasil = assess(_semua(999.0))
        assert hasil.score is not None
        assert 0 <= hasil.score <= 100

    def test_kunci_asing_diabaikan_bukan_meledak(self) -> None:
        """Tabel faktor bisa bertambah; pemanggil lama tidak boleh pecah."""
        hasil = assess(_semua(30.0) | {"faktor_yang_belum_ada": 90.0})
        assert hasil.usable


class TestVetoFaktorTunggal:
    """Rata-rata tertimbang bisa meredam faktor yang mematikan.

    Terukur sebelum veto ini ada: likuidasi 90 dan leverage 80 dengan sisanya
    aman menghasilkan **32/100 LOW**. Aritmetikanya benar dan kesimpulannya
    berbahaya - likuidasi yang terlalu dekat menghabiskan modal, bukan
    menurunkan ekspektasi, dan modal yang habis tidak bisa dikompensasi oleh
    spread yang sempit.
    """

    def test_kasus_yang_dulu_lolos_sekarang_ditahan(self) -> None:
        hasil = assess(_semua(20.0) | {
            "liquidation_distance": 90.0, "leverage": 80.0
        })

        assert hasil.vetoed
        assert hasil.level is RiskLevel.VERY_HIGH

    def test_angkanya_tidak_dipalsukan(self) -> None:
        """Menaikkan skornya supaya cocok dengan vonisnya akan menyembunyikan
        hal yang paling perlu dilihat: rata-ratanya memang rendah, dan rata-rata
        yang rendah tidak menyelamatkan setup ini."""
        hasil = assess(_semua(20.0) | {"liquidation_distance": 90.0})

        assert hasil.score is not None
        assert hasil.score < 50, hasil.score
        assert "rata-rata" in hasil.line()

    def test_data_tak_dipercaya_juga_membatalkan(self) -> None:
        """PASAL 13.26: signal ditahan kalau data penting tidak bisa dipercaya -
        risiko yang dihitung dari data yang tidak dipercaya adalah angka tentang
        tidak ada."""
        assert assess(_semua(20.0) | {"data_quality": 90.0}).vetoed

    def test_di_bawah_ambang_tidak_membatalkan(self) -> None:
        """Veto yang sering berbunyi berhenti dibedakan dari skor biasa, dan
        yang hilang justru kemampuannya menyela."""
        hasil = assess(_semua(20.0) | {"liquidation_distance": 84.0})

        assert not hasil.vetoed
        assert hasil.level is not RiskLevel.VERY_HIGH

    def test_faktor_biasa_tidak_pernah_membatalkan(self) -> None:
        """Spread selebar apa pun bukan alasan membatalkan - ia mahal, bukan
        mematikan."""
        assert not assess(_semua(20.0) | {"spread": 100.0}).vetoed
        assert not assess(_semua(20.0) | {"funding": 100.0}).vetoed

    def test_faktor_fatal_yang_tidak_terukur_tidak_membatalkan(self) -> None:
        """Tidak terukur bukan buruk. Membatalkan karena ketiadaan data akan
        membuat setiap rencana bercakupan tipis terlihat mematikan."""
        bacaan = _semua(20.0)
        del bacaan["liquidation_distance"]

        assert not assess(bacaan).vetoed

    def test_vetonya_disebut_paling_atas(self) -> None:
        """Daftar 'yang meringankan' yang dibaca lebih dulu akan melunakkan
        vonis yang justru tidak boleh dilunakkan."""
        laporan = assess(
            _semua(20.0) | {"liquidation_distance": 90.0}
        ).report()
        teks = "\n".join(laporan)

        assert "TIDAK LAYAK DIAMBIL" in teks
        assert teks.index("TIDAK LAYAK") < teks.index("Yang meringankan")

    def test_hanya_dua_faktor_yang_bisa_membatalkan(self) -> None:
        """Penjaga untuk daftarnya sendiri: veto yang tumbuh diam-diam akan
        mengubah pemeriksa risiko menjadi penolak segalanya."""
        from aruna.risk.score import FATAL

        assert set(FATAL) == {"liquidation_distance", "data_quality"}


class TestPenjelasannya:
    """PASAL 13.22: setiap signal harus bisa menjelaskan alasan risk score."""

    def test_yang_memberatkan_disebut(self) -> None:
        hasil = assess(_semua(20.0) | {"volatility": 95.0})
        assert any("volatilitas" in t for t in hasil.concerns)

    def test_yang_meringankan_juga_disebut(self) -> None:
        """Laporan yang hanya memuat kekhawatiran membuat setiap setup terbaca
        buruk, dan operator berhenti membedakannya."""
        assert assess(_semua(10.0)).comforts

    def test_diurutkan_menurut_sumbangan_bukan_nilai(self) -> None:
        """Faktor bernilai 90 berbobot 1.0 menyumbang lebih sedikit daripada
        bernilai 60 berbobot 3.0; daftar yang mengurutkan menurut nilai menaruh
        yang salah di puncak."""
        hasil = assess(
            _semua(20.0) | {"spread": 95.0, "liquidation_distance": 70.0}
        )
        assert hasil.concerns
        assert "likuidasi" in hasil.concerns[0], hasil.concerns

    def test_laporan_menyebut_yang_tidak_terukur(self) -> None:
        hasil = assess({f.key: 20.0 for f in FAKTOR[:-3]})
        teks = "\n".join(hasil.report())
        assert "Tidak terukur" in teks

    def test_laporan_tanpa_skor_mengatakan_kenapa(self) -> None:
        teks = assess({"volatility": 10.0}).line()
        assert "TIDAK BISA DINILAI" in teks
        assert "60%" in teks


class TestTetapAnalystOnly:
    """PASAL 13.1."""

    def test_tidak_ada_jalur_eksekusi_di_paket_risk(self) -> None:
        import inspect

        from aruna.risk import score

        sumber = inspect.getsource(score).lower()
        for terlarang in (
            "create_order", "place_order", "cancel_order", "set_leverage",
            "buy(", "sell(", "withdraw", "transfer",
        ):
            assert terlarang not in sumber, terlarang

    def test_modulnya_murni_tanpa_jaringan_atau_database(self) -> None:
        """Aturan risiko harus bisa diuji tanpa MySQL dan tanpa bursa - uji
        yang mahal adalah uji yang lama-lama tidak dijalankan."""
        import inspect

        from aruna.risk import score

        sumber = inspect.getsource(score)
        for terlarang in ("httpx", "await ", "async def", "Database", "SELECT"):
            assert terlarang not in sumber, terlarang
