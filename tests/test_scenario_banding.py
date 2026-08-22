"""Membandingkan skenario satu sama lain (bagian 16.9, 16.10).

Yang dijaga paling keras di sini satu kalimat bagian 16.9: penilaian atas
**seluruh** skenario, bukan atas yang terbaik. Tiap test di kelas pertama
dirancang supaya lulus kalau seluruhnya dibaca dan gagal kalau hanya yang
teratas - itu sebabnya risiko HIGH sengaja ditaruh pada skenario berbobot
paling rendah.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.scenario.banding import AMBANG_DOMINAN, bandingkan
from aruna.scenario.models import Invalidasi, Kerapuhan, Skenario

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def _s(nama: str, bobot: int, *, risiko="MEDIUM", syarat=2) -> Skenario:
    return Skenario(
        scenario_id=f"s-{nama}",
        market="CRYPTO",
        asset="BTC/USDT",
        timestamp=NOW,
        nama=nama,
        deskripsi="uji",
        kondisi_awal=("k",),
        pemicu="BREAKOUT_BESAR",
        perkembangan=("a", "b"),
        invalidasi=Invalidasi(syarat=tuple(f"syarat {i}" for i in range(syarat))),
        risiko=risiko,
        keyakinan=bobot / 100,
        bobot=bobot,
        bukti=("b",),
        versi_simulasi="internal-1",
    )


class TestSeluruhBukanYangTeratas:
    """Bagian 16.9 mengejanya, dan ini yang paling mudah dilanggar."""

    def test_risiko_diambil_dari_yang_tertinggi_bukan_dari_teratas(self) -> None:
        """HIGH sengaja ditaruh pada yang berbobot paling rendah. Membaca
        risiko dari skenario teratas akan memulangkan MEDIUM - dan bobot rendah
        bukan alasan mengabaikan risiko, karena bobot bukan probabilitas
        (bagian 16.6)."""
        hasil = bandingkan((
            _s("Bullish Continuation", 60),
            _s("Bearish Reversal", 30),
            _s("Liquidation Cascade", 10, risiko="HIGH"),
        ))

        assert hasil.risiko == "HIGH"

    def test_kerapuhan_menyala_dari_skenario_mana_pun(self) -> None:
        """Yang rapuh ditaruh di posisi terbawah. Satu skenario
        bergantung-satu-benang sudah cukup membuat pembacaan atas himpunan itu
        menyesatkan."""
        hasil = bandingkan((
            _s("Bullish Continuation", 60, syarat=3),
            _s("Bearish Reversal", 30, syarat=2),
            _s("False Breakout", 10, syarat=1),
        ))

        assert hasil.kerapuhan is Kerapuhan.RAPUH
        assert hasil.jumlah_rapuh == 1

    def test_jumlahnya_seluruh_skenario(self) -> None:
        hasil = bandingkan((_s("a", 50), _s("b", 30), _s("c", 20)))

        assert hasil.jumlah == 3

    def test_himpunan_kokoh_kalau_tidak_ada_yang_rapuh(self) -> None:
        hasil = bandingkan((_s("a", 50, syarat=2), _s("b", 50, syarat=4)))

        assert hasil.kerapuhan is Kerapuhan.KOKOH
        assert hasil.jumlah_rapuh == 0


class TestDominansiTipisAdalahKonflik:
    def test_selisih_di_bawah_ambang_disebut_konflik(self) -> None:
        """40/35/25: pemenangnya ada, tapi pasarnya tidak bisa dibaca."""
        hasil = bandingkan((_s("a", 40), _s("b", 35), _s("c", 25)))

        assert hasil.konflik
        assert hasil.jarak == 5

    def test_selisih_di_atas_ambang_bukan_konflik(self) -> None:
        """80/12/8: pemenangnya sama-sama ada, tapi pasarnya jelas."""
        hasil = bandingkan((_s("a", 80), _s("b", 12), _s("c", 8)))

        assert not hasil.konflik
        assert hasil.jarak == 68

    def test_tepat_di_ambang_bukan_konflik(self) -> None:
        hasil = bandingkan((_s("a", 40), _s("b", 40 - AMBANG_DOMINAN)))

        assert not hasil.konflik

    def test_seri_sempurna_adalah_konflik(self) -> None:
        hasil = bandingkan((_s("a", 50), _s("b", 50)))

        assert hasil.konflik
        assert hasil.jarak == 0

    def test_teratas_tetap_dilaporkan_saat_konflik(self) -> None:
        """Konflik bukan alasan menyembunyikan angkanya - pembacanya berhak
        tahu bahwa yang unggul unggul tipis, bukan bahwa tidak ada apa-apa."""
        hasil = bandingkan((_s("a", 40), _s("b", 35)))

        assert hasil.teratas is not None
        assert hasil.teratas.nama == "a"


class TestTeratasYangRapuh:
    """Keadaan paling menyesatkan yang bisa dihasilkan mesin ini."""

    def test_ditandai_terpisah(self) -> None:
        hasil = bandingkan((
            _s("a", 70, syarat=1),
            _s("b", 20, syarat=3),
            _s("c", 10, syarat=3),
        ))

        assert hasil.teratas_rapuh

    def test_teratas_kokoh_walau_ada_yang_rapuh(self) -> None:
        """Dibedakan dari `kerapuhan` himpunan: yang ini soal apakah angka yang
        paling menarik perhatian justru yang paling mudah runtuh."""
        hasil = bandingkan((_s("a", 70, syarat=3), _s("b", 30, syarat=1)))

        assert hasil.kerapuhan is Kerapuhan.RAPUH
        assert not hasil.teratas_rapuh


class TestTidakMenganjurkan:
    """Bagian 16.18: keputusan tetap milik Phase 14."""

    def test_tidak_ada_bidang_pemenang_atau_arah(self) -> None:
        """Nama bidang seperti `pemenang` atau `rekomendasi` akan dibaca
        sebagai anjuran oleh pembaca berikutnya."""
        hasil = bandingkan((_s("a", 60), _s("b", 40)))
        punya = {n for n in dir(hasil) if not n.startswith("_")}

        assert not (punya & {
            "pemenang", "rekomendasi", "direction", "decision", "arah", "aksi",
        })

    def test_bobot_tetap_dilabeli_relatif(self) -> None:
        hasil = bandingkan((_s("a", 60), _s("b", 40)))

        assert "relatif" in hasil.to_dict()["bobot_catatan"].lower()


class TestHimpunanKosong:
    def test_tidak_melempar(self) -> None:
        """Bagian 16.12: siklus tetap berjalan saat simulasi tidak menghasilkan
        apa-apa. Lemparan di sini akan menjatuhkannya."""
        hasil = bandingkan(())

        assert hasil.teratas is None
        assert hasil.jumlah == 0

    def test_risikonya_unknown_bukan_low(self) -> None:
        """SPEC 4: "tidak ada data" dan "risikonya rendah" adalah dua hal yang
        sangat berbeda."""
        assert bandingkan(()).risiko == "UNKNOWN"


class TestStabil:
    def test_seri_dipecah_dengan_nama_bukan_urutan_datang(self) -> None:
        """Dua skenario berbobot sama akan bertukar tempat menurut urutan mesin
        menghasilkannya, dan `teratas` yang berubah-ubah membuat evaluasi
        bagian 16.19 mengukur dua hal berbeda di bawah satu nama."""
        maju = bandingkan((_s("Zeta", 50), _s("Alpha", 50)))
        mundur = bandingkan((_s("Alpha", 50), _s("Zeta", 50)))

        assert maju.teratas.nama == mundur.teratas.nama == "Alpha"


class TestSatuSkenario:
    def test_jaraknya_bobotnya_sendiri(self) -> None:
        """Tidak ada pesaing berarti jaraknya sejauh bobotnya dari nol - bukan
        nol, yang akan terbaca sebagai seri."""
        hasil = bandingkan((_s("a", 100),))

        assert hasil.jarak == 100
        assert not hasil.konflik


@pytest.mark.parametrize("risiko", ["LOW", "MEDIUM", "HIGH"])
def test_risiko_tunggal_diteruskan_apa_adanya(risiko) -> None:
    assert bandingkan((_s("a", 100, risiko=risiko),)).risiko == risiko
