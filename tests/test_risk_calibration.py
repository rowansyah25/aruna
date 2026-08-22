"""Kalibrasi risiko (PASAL 13.27-13.29).

Sebuah skor risiko yang tidak pernah dibandingkan dengan hasil adalah pendapat
yang memakai angka. Yang diuji: apakah kategori risiko benar-benar memisahkan
yang menang dari yang kalah - **dan apakah ARUNA tahu ketika ia tidak tahu.**
"""

from __future__ import annotations

from aruna.risk.calibration import Hasil, calibrate
from aruna.risk.score import RiskLevel

VERSI = "risk-13.0"


def _hasil(kategori: RiskLevel, menang: int, kalah: int, versi=VERSI):
    return (
        [Hasil(kategori, True, versi) for _ in range(menang)]
        + [Hasil(kategori, False, versi) for _ in range(kalah)]
    )


class TestUrutanYangSehat:
    def test_risiko_rendah_menang_lebih_sering(self) -> None:
        lap = calibrate(
            _hasil(RiskLevel.LOW, 90, 10) + _hasil(RiskLevel.HIGH, 60, 40),
            risk_model_version=VERSI,
        )

        assert lap.usable
        assert lap.inverted == ()
        assert "wajar" in lap.summary()

    def test_terbalik_terdeteksi(self) -> None:
        """Skor yang terbalik lebih buruk daripada tidak ada skor - ia memandu
        ke arah yang salah dengan percaya diri."""
        lap = calibrate(
            _hasil(RiskLevel.LOW, 20, 80) + _hasil(RiskLevel.HIGH, 85, 15),
            risk_model_version=VERSI,
        )

        assert lap.inverted
        assert "TERBALIK" in lap.summary()

    def test_selisih_kecil_bukan_terbalik(self) -> None:
        """Dua win rate yang selangnya beririsan belum bisa dibedakan;
        menyebutnya terbalik mengubah setiap kebisingan menjadi temuan."""
        lap = calibrate(
            _hasil(RiskLevel.LOW, 48, 52) + _hasil(RiskLevel.HIGH, 52, 48),
            risk_model_version=VERSI,
        )

        assert lap.inverted == ()


class TestSampleDijaga:
    """PASAL 13.29: 'gunakan sample yang cukup'."""

    def test_kategori_bersample_tipis_tidak_menyimpulkan(self) -> None:
        lap = calibrate(
            _hasil(RiskLevel.LOW, 3, 0) + _hasil(RiskLevel.HIGH, 0, 2),
            risk_model_version=VERSI,
        )

        assert not lap.usable
        assert "belum bisa dinilai" in lap.summary()

    def test_tipis_tidak_bisa_menuduh_terbalik(self) -> None:
        """Sample di bawah ambang tidak boleh menuduh, **walau selangnya
        terpisah.**

        Versi pertama memakai 0-dari-3 lawan 2-dari-2. Itu tidak menguji apa
        pun: selang sample sekecil itu selalu bertindihan, jadi pemeriksaan
        selangnya sendiri yang menahannya - dan mencabut saringan sample
        tidak mengubah apa-apa.

        Dua puluh lima lawan dua puluh lima memberi selang yang benar-benar
        terpisah (0-13% lawan 87-100%) sementara keduanya masih di bawah
        ambang tiga puluh. Di situlah saringan sample-nya yang bekerja.
        """
        lap = calibrate(
            _hasil(RiskLevel.LOW, 0, 25) + _hasil(RiskLevel.HIGH, 25, 0),
            risk_model_version=VERSI,
        )

        assert lap.inverted == ()

    def test_angkanya_tetap_dilaporkan(self) -> None:
        """Diredam kesimpulannya, bukan disembunyikan datanya."""
        lap = calibrate(_hasil(RiskLevel.LOW, 3, 0), risk_model_version=VERSI)
        teks = "\n".join(lap.report())

        assert "3/3" in teks
        assert "SAMPLE BELUM CUKUP" in teks


class TestVersiTidakDicampur:
    def test_versi_lain_tidak_ikut(self) -> None:
        """Skor 30 dari bobot lama dan 30 dari bobot baru adalah dua angka
        berbeda yang kebetulan tertulis sama."""
        campur = (
            _hasil(RiskLevel.LOW, 90, 10)
            + _hasil(RiskLevel.LOW, 0, 500, versi="risk-lama")
        )
        lap = calibrate(campur, risk_model_version=VERSI)

        assert lap.buckets[0].evidence.total == 100

    def test_versi_yang_tidak_ada_menghasilkan_kosong(self) -> None:
        lap = calibrate(
            _hasil(RiskLevel.LOW, 90, 10), risk_model_version="risk-lain"
        )
        assert lap.buckets == ()
        assert not lap.usable


class TestRiwayatnyaImmutable:
    """PASAL 13.27, ditegakkan database."""

    def test_migrasinya_menolak_update(self) -> None:
        import pathlib

        sql = (
            pathlib.Path(__file__).resolve().parent.parent
            / "migrations" / "0027_risk_history.sql"
        ).read_text(encoding="utf-8")

        assert "CREATE TRIGGER risk_history_no_update" in sql
        assert "SIGNAL SQLSTATE '45000'" in sql

    def test_skor_kosong_wajib_berkategori_unknown(self) -> None:
        """Dua kolom yang saling bertentangan adalah dua kebenaran tentang satu
        penilaian, dan pembacanya tidak punya cara memilih."""
        import pathlib

        sql = (
            pathlib.Path(__file__).resolve().parent.parent
            / "migrations" / "0027_risk_history.sql"
        ).read_text(encoding="utf-8")

        assert "risk_history_unknown_has_no_score" in sql

    def test_versi_bobot_ikut_disimpan(self) -> None:
        """Tanpa ia, kalibrasi tidak bisa menolak mencampur dua model."""
        import pathlib

        sql = (
            pathlib.Path(__file__).resolve().parent.parent
            / "migrations" / "0027_risk_history.sql"
        ).read_text(encoding="utf-8")

        assert "risk_model_version" in sql
