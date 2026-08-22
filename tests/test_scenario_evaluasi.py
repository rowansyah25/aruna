"""Menilai skenario terhadap apa yang benar-benar terjadi (bagian 16.19).

Yang dijaga paling keras di sini bukan ketiga putusannya, melainkan dua hal yang
menentukan apakah angkanya berarti sama sekali:

* **Invalidasi yang terpicu dinilai terpisah.** Skenario yang menyebutkan syarat
  batalnya lalu syarat itu terjadi adalah simulasi yang **bekerja**. Menyatukan
  ini dengan skenario yang meleset menghasilkan angka yang naik ketika skenario
  berhenti menyebutkan syarat batalnya - persis arah yang salah.
* **Ambang sampel.** Bagian 16.19 menutup dengan larangan mengubah model karena
  satu kegagalan, dan angka dari sepuluh kasus mengukur keberuntungan.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.scenario.evaluasi import (
    MINIMUM_DINILAI,
    Putusan,
    nilai_satu,
    ringkas,
)
from aruna.scenario.models import HasilSkenario, Invalidasi, Skenario

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def _skenario(langkah: int = 3, syarat: int = 2) -> Skenario:
    return Skenario(
        scenario_id="s-1",
        market="CRYPTO",
        asset="BTC/USDT",
        timestamp=NOW,
        nama="Bullish Continuation",
        deskripsi="uji",
        kondisi_awal=("k",),
        pemicu="BREAKOUT_BESAR",
        perkembangan=tuple(f"langkah {i}" for i in range(langkah)),
        invalidasi=Invalidasi(syarat=tuple(f"syarat {i}" for i in range(syarat))),
        risiko="MEDIUM",
        keyakinan=0.5,
        bobot=50,
        bukti=("b",),
        versi_simulasi="internal-1",
    )


def _putusan(hasil: HasilSkenario, *, diinvalidasi=False, n=1) -> tuple[Putusan, ...]:
    return tuple(
        Putusan(
            scenario_id=f"s-{i}",
            hasil=hasil,
            diinvalidasi=diinvalidasi,
            alasan="uji",
        )
        for i in range(n)
    )


class TestTigaPutusan:
    """Bagian 16.19."""

    def test_semua_langkah_terjadi_berarti_benar(self) -> None:
        p = nilai_satu(_skenario(), perkembangan_terjadi=(True, True, True))

        assert p.hasil is HasilSkenario.BENAR

    def test_tidak_ada_langkah_terjadi_berarti_salah(self) -> None:
        p = nilai_satu(_skenario(), perkembangan_terjadi=(False, False, False))

        assert p.hasil is HasilSkenario.SALAH

    def test_sebagian_langkah_terjadi_berarti_sebagian(self) -> None:
        """Rantai konsekuensi (bagian 16.8) memang bisa benar separuh: dua
        langkah pertama terjadi, yang ketiga tidak. Memaksanya jadi
        benar-atau-salah membuang keterangan paling berguna tentang di mana
        rantainya putus."""
        p = nilai_satu(_skenario(), perkembangan_terjadi=(True, True, False))

        assert p.hasil is HasilSkenario.SEBAGIAN
        assert "2/3" in p.alasan


class TestInvalidasiDinilaiTerpisah:
    """Kegagalan yang berbeda jenis, bukan kegagalan yang lebih ringan."""

    def test_invalidasi_terpicu_berarti_salah(self) -> None:
        p = nilai_satu(_skenario(), invalidasi_terpicu=("syarat 0",))

        assert p.hasil is HasilSkenario.SALAH

    def test_tapi_ditandai_diinvalidasi(self) -> None:
        p = nilai_satu(_skenario(), invalidasi_terpicu=("syarat 0",))

        assert p.diinvalidasi

    def test_gagal_jujur_dibedakan(self) -> None:
        """Skenario yang salah setelah memperingatkan, dan yang salah tanpa
        peringatan, menuntut tindakan berbeda."""
        jujur = nilai_satu(_skenario(), invalidasi_terpicu=("syarat 0",))
        meleset = nilai_satu(_skenario(), perkembangan_terjadi=(False, False, False))

        assert jujur.gagal_jujur
        assert not meleset.gagal_jujur

    def test_invalidasi_diperiksa_sebelum_perkembangan(self) -> None:
        """Skenario yang batal sudah tidak berlaku; memeriksa perkembangannya
        setelah itu menilai gambaran yang syaratnya sendiri sudah runtuh."""
        p = nilai_satu(
            _skenario(),
            invalidasi_terpicu=("syarat 0",),
            perkembangan_terjadi=(True, True, True),
        )

        assert p.hasil is HasilSkenario.SALAH
        assert p.diinvalidasi

    def test_alasannya_menyebut_syarat_mana(self) -> None:
        p = nilai_satu(_skenario(), invalidasi_terpicu=("syarat 1",))

        assert "syarat 1" in p.alasan

    def test_ringkasan_menghitung_yang_diinvalidasi_terpisah(self) -> None:
        campur = _putusan(HasilSkenario.SALAH, diinvalidasi=True, n=3) + _putusan(
            HasilSkenario.SALAH, n=2
        )

        a = ringkas(campur)

        assert a.salah == 5
        assert a.diinvalidasi == 3


class TestBelumBukanSalah:
    def test_horizon_belum_selesai(self) -> None:
        p = nilai_satu(_skenario(), horizon_selesai=False)

        assert p.hasil is HasilSkenario.BELUM

    def test_tanpa_bahan_pemeriksaan(self) -> None:
        """Penilaian yang tidak punya bahan bukan skenario yang salah -
        menyebutnya SALAH menghukum simulasi karena datanya yang hilang."""
        p = nilai_satu(_skenario(), perkembangan_terjadi=())

        assert p.hasil is HasilSkenario.BELUM

    def test_belum_tidak_masuk_penyebut(self) -> None:
        """Memasukkannya membuat akurasi turun tiap kali simulasi baru
        dijalankan - sebelum satu pun hasilnya diketahui."""
        a = ringkas(
            _putusan(HasilSkenario.BENAR, n=5) + _putusan(HasilSkenario.BELUM, n=95)
        )

        assert a.dinilai == 5


class TestAmbangSampel:
    """Bagian 16.19: jangan mengubah model karena satu simulation failure."""

    def test_di_bawah_ambang_akurasinya_none(self) -> None:
        a = ringkas(_putusan(HasilSkenario.BENAR, n=MINIMUM_DINILAI - 1))

        assert not a.cukup_sampel
        assert a.akurasi is None

    def test_none_bukan_nol(self) -> None:
        """SPEC 4: "belum tahu" dan "buruk" adalah dua hal yang sangat berbeda,
        dan nol yang dilaporkan akan dibaca sebagai mesin yang tidak pernah
        benar."""
        a = ringkas(_putusan(HasilSkenario.SALAH, n=3))

        assert a.akurasi is None
        assert a.akurasi != 0.0

    def test_satu_kegagalan_tidak_menghasilkan_angka(self) -> None:
        """Kalimat penutup bagian 16.19, diuji langsung."""
        a = ringkas(_putusan(HasilSkenario.SALAH, n=1))

        assert a.akurasi is None
        assert a.akurasi_longgar is None

    def test_di_ambang_akurasinya_keluar(self) -> None:
        a = ringkas(_putusan(HasilSkenario.BENAR, n=MINIMUM_DINILAI))

        assert a.cukup_sampel
        assert a.akurasi == 1.0

    def test_ambangnya_dipinjam_bukan_dipilih_ulang(self) -> None:
        """Pertanyaannya identik dengan PASAL 15.44 - berapa kasus sebelum
        sebuah angka berhenti mengukur keberuntungan. Dua ambang berbeda untuk
        pertanyaan yang sama akan melenceng."""
        from aruna.upkeep.manfaat import MINIMUM_TERSEDIA

        assert MINIMUM_DINILAI == MINIMUM_TERSEDIA

    def test_minimumnya_ikut_dilaporkan(self) -> None:
        """Angka yang ditahan tanpa menyebut ambangnya membuat pembacanya tidak
        tahu berapa lama lagi harus menunggu."""
        d = ringkas(_putusan(HasilSkenario.BENAR, n=3)).to_dict()

        assert d["minimum"] == MINIMUM_DINILAI
        assert d["cukup_sampel"] is False


class TestDuaAngkaBerdampingan:
    def test_akurasi_ketat_tidak_menghitung_sebagian(self) -> None:
        a = ringkas(
            _putusan(HasilSkenario.BENAR, n=100)
            + _putusan(HasilSkenario.SEBAGIAN, n=100)
        )

        assert a.akurasi == 0.5

    def test_akurasi_longgar_menghitungnya(self) -> None:
        a = ringkas(
            _putusan(HasilSkenario.BENAR, n=100)
            + _putusan(HasilSkenario.SEBAGIAN, n=100)
        )

        assert a.akurasi_longgar == 1.0

    def test_keduanya_dilaporkan_bersama(self) -> None:
        """Satu angka yang menghitung SEBAGIAN sebagai benar akan selalu
        terlihat lebih baik, dan yang memilih angka mana yang dikutip adalah
        orang yang sedang membela mesinnya."""
        d = ringkas(_putusan(HasilSkenario.BENAR, n=MINIMUM_DINILAI)).to_dict()

        assert "akurasi" in d
        assert "akurasi_longgar" in d


class TestVersiTidakDicampur:
    def test_versinya_ikut_di_ringkasan(self) -> None:
        """Hasil dua mesin yang dijumlah jadi satu angka tidak mengatakan apa
        pun tentang keduanya."""
        a = ringkas(_putusan(HasilSkenario.BENAR, n=3), versi="internal-1")

        assert a.versi == "internal-1"

    def test_bawaannya_unknown_bukan_kosong(self) -> None:
        assert ringkas(()).versi == "UNKNOWN"


class TestRingkasanKosong:
    def test_tidak_melempar(self) -> None:
        a = ringkas(())

        assert a.dinilai == 0
        assert a.akurasi is None


@pytest.mark.parametrize(
    ("terjadi", "diharapkan"),
    [
        ((True,), HasilSkenario.BENAR),
        ((False,), HasilSkenario.SALAH),
        ((True, False), HasilSkenario.SEBAGIAN),
        ((False, True), HasilSkenario.SEBAGIAN),
        ((True, True, True, True), HasilSkenario.BENAR),
    ],
)
def test_rantai_panjang_apa_pun(terjadi, diharapkan) -> None:
    p = nilai_satu(_skenario(langkah=len(terjadi)), perkembangan_terjadi=terjadi)

    assert p.hasil is diharapkan
