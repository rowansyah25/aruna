"""PASAL 15.44: apakah memory benar-benar membantu - diuji, bukan diasumsikan.

Pasalnya meminta perbandingan **keputusan dengan memory** melawan **keputusan
tanpa memory**. Memory baru mulai mempengaruhi keputusan hari ini, jadi tidak
ada satu pun hasil yang bisa diatribusikan kepadanya - menunggu berbulan-bulan
adalah satu jawaban.

Jawaban yang lain, dan yang PASAL 15.40 justru wajibkan, adalah **simulasi
historis**: untuk tiap keputusan lama, hitung konteks yang WAKTU ITU tersedia,
lalu bandingkan hasilnya. Ingatan yang resolusinya terjadi sesudah keputusan
itu dibuat tidak boleh ikut - dan itu satu-satunya hal yang membuat angka ini
berarti sama sekali.

Kalau memory menambah sesuatu, keputusan yang sejarahnya SUPPORTIVE harus
berbeda hasilnya dari yang CONTRARY. Kalau tidak berbeda, itu juga jawaban -
dan PASAL 15.44 mengejanya: jangan memaksakan penggunaan memory.
"""

from __future__ import annotations

import pytest

from aruna.memory.context import Pengaruh
from aruna.memory.evaluasi import (
    SELISIH_BERARTI,
    Evaluasi,
    evaluasi_pengaruh,
)
from aruna.memory.record import Hasil


def _pasangan(pengaruh: Pengaruh, menang: int, kalah: int):
    return (
        [(pengaruh, Hasil.WIN)] * menang + [(pengaruh, Hasil.LOSS)] * kalah
    )


class TestPerbandingannya:
    def test_supportive_lebih_baik_disebut_membantu(self) -> None:
        hasil = evaluasi_pengaruh(
            _pasangan(Pengaruh.SUPPORTIVE, 70, 30)
            + _pasangan(Pengaruh.CONTRARY, 30, 70)
        )

        assert hasil.selisih == 40
        assert hasil.membantu is True

    def test_selisih_kecil_bukan_bukti(self) -> None:
        """Dua poin di antara dua kelompok seratus adalah derau. Menyebutnya
        "memory membantu" adalah membaca derau sebagai temuan - dan itu persis
        yang PASAL 15.44 coba cegah."""
        hasil = evaluasi_pengaruh(
            _pasangan(Pengaruh.SUPPORTIVE, 51, 49)
            + _pasangan(Pengaruh.CONTRARY, 49, 51)
        )

        assert hasil.membantu is False

    def test_contrary_lebih_baik_juga_temuan(self) -> None:
        """Kalau yang dilawan sejarah justru lebih sering benar, itu bukan
        kegagalan pengukuran - itu hasilnya, dan menyembunyikannya akan
        membiarkan memory dipakai ke arah yang salah."""
        hasil = evaluasi_pengaruh(
            _pasangan(Pengaruh.SUPPORTIVE, 30, 70)
            + _pasangan(Pengaruh.CONTRARY, 70, 30)
        )

        assert hasil.selisih == -40
        assert hasil.membantu is False
        assert hasil.terbalik is True

    def test_sampel_tipis_menolak_menyimpulkan(self) -> None:
        hasil = evaluasi_pengaruh(
            _pasangan(Pengaruh.SUPPORTIVE, 3, 2)
            + _pasangan(Pengaruh.CONTRARY, 2, 3)
        )

        assert hasil.cukup is False
        assert hasil.membantu is False

    def test_tanpa_salah_satu_kelompok_tidak_bisa_dibandingkan(self) -> None:
        """Seribu kasus SUPPORTIVE tanpa satu pun CONTRARY tidak membandingkan
        apa pun."""
        hasil = evaluasi_pengaruh(_pasangan(Pengaruh.SUPPORTIVE, 500, 500))

        assert hasil.cukup is False
        assert hasil.selisih is None

    def test_netral_tidak_ikut_perbandingan(self) -> None:
        """NEUTRAL berarti memory tidak berpendapat. Memasukkannya ke salah
        satu sisi akan mengukur sesuatu yang lain."""
        hasil = evaluasi_pengaruh(
            _pasangan(Pengaruh.SUPPORTIVE, 70, 30)
            + _pasangan(Pengaruh.CONTRARY, 30, 70)
            + _pasangan(Pengaruh.NEUTRAL, 0, 500)
        )

        assert hasil.selisih == 40

    def test_kosong_bukan_nol(self) -> None:
        hasil = evaluasi_pengaruh([])

        assert hasil.selisih is None
        assert hasil.cukup is False


class TestKalimatnya:
    def test_menyebut_kedua_sisi_dan_jumlahnya(self) -> None:
        kalimat = evaluasi_pengaruh(
            _pasangan(Pengaruh.SUPPORTIVE, 70, 30)
            + _pasangan(Pengaruh.CONTRARY, 30, 70)
        ).ringkas()

        assert "70%" in kalimat
        assert "30%" in kalimat
        assert "100" in kalimat

    def test_sampel_tipis_mengatakan_belum_bisa(self) -> None:
        kalimat = evaluasi_pengaruh(
            _pasangan(Pengaruh.SUPPORTIVE, 3, 2)
        ).ringkas().lower()

        assert "belum" in kalimat

    def test_tidak_menjanjikan_apa_pun(self) -> None:
        kalimat = evaluasi_pengaruh(
            _pasangan(Pengaruh.SUPPORTIVE, 70, 30)
            + _pasangan(Pengaruh.CONTRARY, 30, 70)
        ).ringkas().lower()

        for terlarang in ("pasti", "peluang profit", "chance", "prediksi"):
            assert terlarang not in kalimat


class TestBentuknya:
    def test_ambangnya_masuk_akal(self) -> None:
        """Selisih yang lebih kecil dari ini tidak bisa dibedakan dari derau
        pada sampel yang ARUNA punya."""
        assert 5 <= SELISIH_BERARTI <= 20

    def test_bekunya_dijaga(self) -> None:
        from dataclasses import FrozenInstanceError

        hasil = evaluasi_pengaruh(_pasangan(Pengaruh.SUPPORTIVE, 70, 30))

        with pytest.raises(FrozenInstanceError):
            hasil.mendukung_menang = 0  # type: ignore[misc]

    def test_bentuknya_evaluasi(self) -> None:
        assert isinstance(evaluasi_pengaruh([]), Evaluasi)
