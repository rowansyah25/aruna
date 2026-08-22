"""Siapa saja yang ada di pasar, dan bagaimana masing-masing bereaksi.

Yang dijaga di sini **tandanya**, bukan angkanya. Pangsa dan reaktivitas adalah
kebijakan - menggesernya menggeser bobot skenario, dan itu bisa diperdebatkan.
Tandanya tidak bisa: penyedia likuiditas yang mengejar arah alih-alih menyerap
mengubah pasar yang meredam menjadi pasar yang memperkuat, dan tiap lintasan
akan meledak alih-alih mereda.
"""

from __future__ import annotations

import pytest

from aruna.scenario.kohort import KOHORT, SATURASI, Kohort, aliran


def _dasar(**kw) -> dict:
    return {
        "gerak_terakhir": 0.0,
        "jarak_kumulatif": 0.0,
        "ketidakseimbangan": 0.0,
        "kedalaman": 1.0,
        "dorongan_berita": 0.0,
        "paksa": 0.0,
    } | kw


def _cari(nama: str) -> Kohort:
    return next(k for k in KOHORT if k.nama == nama)


class TestRoster:
    def test_pangsanya_menjumlah_satu(self) -> None:
        """Kohort yang pangsanya tidak menjumlah satu berarti sebagian aliran
        pasar tidak diwakili siapa pun - dan yang tidak terwakili tidak pernah
        muncul di lintasan mana pun."""
        assert sum(k.pangsa for k in KOHORT) == pytest.approx(1.0)

    def test_enam_kohort(self) -> None:
        assert len(KOHORT) == 6

    def test_namanya_unik(self) -> None:
        assert len({k.nama for k in KOHORT}) == len(KOHORT)

    def test_tiap_arah_valid(self) -> None:
        assert all(k.tanda in (1, -1) for k in KOHORT)

    def test_ada_yang_mengejar_dan_ada_yang_melawan(self) -> None:
        """Pasar yang seluruhnya mengejar tidak pernah berbalik; yang seluruhnya
        melawan tidak pernah menembus. Keduanya menghasilkan satu keluarga
        skenario saja."""
        assert any(k.tanda == 1 for k in KOHORT)
        assert any(k.tanda == -1 for k in KOHORT)


class TestTandaReaksinya:
    def test_pengikut_tren_searah(self) -> None:
        naik = aliran(_cari("pengikut_tren"), **_dasar(gerak_terakhir=1.0))
        turun = aliran(_cari("pengikut_tren"), **_dasar(gerak_terakhir=-1.0))

        assert naik > 0
        assert turun < 0

    def test_pengikut_tren_juga_mengejar_arah_yang_terbentuk(self) -> None:
        """Kohort yang hanya melihat tick terakhir tidak bisa menghasilkan
        tren, karena gerak ronde terakhir selalu meluruh. Terukur: momentum nol
        di seluruh roster menghasilkan 28 dari 36 lintasan `Sideways`."""
        tanpa_jarak = aliran(_cari("pengikut_tren"), **_dasar(gerak_terakhir=0.2))
        dengan_jarak = aliran(
            _cari("pengikut_tren"), **_dasar(gerak_terakhir=0.2, jarak_kumulatif=1.0)
        )

        assert dengan_jarak > tanpa_jarak

    def test_pembalik_melawan_jarak(self) -> None:
        """Memudarkan jarak dari titik awal, bukan gerak terakhir - kalau ia
        memudarkan gerak terakhir, ia tidak bisa dibedakan dari pembuat pasar."""
        jauh_naik = aliran(_cari("pembalik"), **_dasar(jarak_kumulatif=2.0))

        assert jauh_naik < 0

    def test_pembuat_pasar_meredam(self) -> None:
        """Penyedia likuiditas menyerap ketidakseimbangan, tidak mengejarnya."""
        timpang_beli = aliran(_cari("pembuat_pasar"), **_dasar(ketidakseimbangan=1.0))

        assert timpang_beli < 0

    def test_peredamnya_melemah_saat_kedalaman_tipis(self) -> None:
        """Yang membuat pasar tenang dan pasar panik berperilaku berbeda
        terhadap aliran yang sama. Tanpa ini, kaskade tidak mungkin."""
        tebal = abs(aliran(_cari("pembuat_pasar"), **_dasar(ketidakseimbangan=1.0, kedalaman=1.0)))
        tipis = abs(aliran(_cari("pembuat_pasar"), **_dasar(ketidakseimbangan=1.0, kedalaman=0.3)))

        assert tipis < tebal

    def test_pemegang_hampir_tidak_bergerak(self) -> None:
        """Ada karena tanpanya seluruh pasar reaktif, dan pasar yang seluruhnya
        reaktif tidak pernah menghasilkan rentang sepi."""
        pemegang = abs(aliran(_cari("pemegang"), **_dasar(gerak_terakhir=1.0)))
        tren = abs(aliran(_cari("pengikut_tren"), **_dasar(gerak_terakhir=1.0)))

        assert pemegang < tren / 5


class TestBerungkit:
    """Sumber kaskade, dan satu-satunya kohort yang punya keadaan."""

    def test_diam_sebelum_terlempar(self) -> None:
        """Nol bukan karena tidak punya pendapat - nol karena posisinya belum
        tersentuh. Itulah yang membuat kaskade datang mendadak alih-alih
        menumpuk perlahan."""
        diam = aliran(_cari("berungkit"), **_dasar(gerak_terakhir=1.0, paksa=0.0))

        assert diam == 0.0

    def test_bergerak_sesudah_terlempar(self) -> None:
        lempar = aliran(_cari("berungkit"), **_dasar(paksa=-0.34))

        assert lempar != 0.0

    def test_searah_tanda_paksanya(self) -> None:
        """``paksa`` bertanda, dan tandanya ditentukan `kerumunan.jalankan`
        menurut kolam mana yang terlempar - yang melawan guncangan mempercepat
        arahnya, yang searah guncangan justru membalikkannya."""
        turun = aliran(_cari("berungkit"), **_dasar(paksa=-0.34))
        naik = aliran(_cari("berungkit"), **_dasar(paksa=0.34))

        assert turun < 0
        assert naik > 0

    def test_tidak_memandang_harga_saat_terlempar(self) -> None:
        """Penutupan paksa adalah order pasar. Besarnya tidak bergantung pada
        seberapa jauh harga sudah bergerak - itu yang membedakannya dari
        penjualan sukarela."""
        kecil = aliran(
            _cari("berungkit"), **_dasar(gerak_terakhir=-0.1, paksa=-0.34)
        )
        besar = aliran(
            _cari("berungkit"), **_dasar(gerak_terakhir=-5.0, paksa=-0.34)
        )

        assert kecil == besar

    def test_besarnya_sebanding_kolam_yang_tersisa(self) -> None:
        """Kolam yang menipis menghasilkan aliran paksa yang mengecil - itu
        yang membuat kaskade berhenti alih-alih berlari tanpa batas."""
        penuh = abs(aliran(_cari("berungkit"), **_dasar(paksa=-0.34)))
        sisa = abs(aliran(_cari("berungkit"), **_dasar(paksa=-0.05)))

        assert sisa < penuh


class TestSaturasi:
    """Modal pengejar habis; modal yang memudarkan tidak."""

    def test_pengejar_jenuh(self) -> None:
        """Pedagang tren tidak membeli dua belas kali lebih keras karena harga
        naik dua belas ATR - pada suatu titik ia sudah sepenuhnya masuk."""
        sedang = aliran(_cari("pengikut_tren"), **_dasar(gerak_terakhir=SATURASI))
        ekstrem = aliran(
            _cari("pengikut_tren"), **_dasar(gerak_terakhir=SATURASI * 10)
        )

        assert sedang == ekstrem

    def test_pemudar_tidak_jenuh(self) -> None:
        """Asimetri inilah yang membuat tren berhenti sendiri. Kalau peredam
        ikut jenuh, pengejar yang jenuh tetap mengalahkannya selamanya dan
        lintasan berlari sampai batas penjaga - terukur pada versi pertama:
        satu lintasan berakhir di +12,54 ATR."""
        sedang = abs(aliran(_cari("pembalik"), **_dasar(jarak_kumulatif=SATURASI)))
        ekstrem = abs(
            aliran(_cari("pembalik"), **_dasar(jarak_kumulatif=SATURASI * 10))
        )

        assert ekstrem > sedang * 5


class TestPemburuBerita:
    def test_diam_tanpa_berita(self) -> None:
        assert aliran(
            _cari("pemburu_berita"), **_dasar(gerak_terakhir=3.0)
        ) == 0.0

    def test_bergerak_searah_dorongan(self) -> None:
        assert aliran(
            _cari("pemburu_berita"), **_dasar(dorongan_berita=1.0)
        ) > 0
        assert aliran(
            _cari("pemburu_berita"), **_dasar(dorongan_berita=-1.0)
        ) < 0

    def test_tidak_terpengaruh_harga(self) -> None:
        """Berita yang bereaksi terhadap harga bukan berita - ia teknikal
        dengan nama lain."""
        a = aliran(_cari("pemburu_berita"), **_dasar(dorongan_berita=1.0))
        b = aliran(
            _cari("pemburu_berita"),
            **_dasar(dorongan_berita=1.0, gerak_terakhir=-4.0, jarak_kumulatif=9.0),
        )

        assert a == b


class TestPasarDiamSaatTidakAdaRangsangan:
    def test_semua_nol_pada_keadaan_awal(self) -> None:
        """Pasar tanpa rangsangan tidak boleh bergerak sendiri. Aliran bukan-nol
        di sini berarti lintasan punya arah bawaan - dan arah bawaan adalah
        tebakan arah yang menyamar sebagai simulasi (bagian 16.18)."""
        for k in KOHORT:
            assert aliran(k, **_dasar()) == 0.0, k.nama
