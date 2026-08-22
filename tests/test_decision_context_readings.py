"""Dari analisis Phase 3 dan council ke komponen Decision Score (PASAL 14.16).

Yang tahu apakah sebuah angka mendukung LONG atau SHORT adalah lapisan yang
mengukurnya. Yang diuji di sini adalah terjemahannya - dan terutama tempat
terjemahan itu paling mudah membalik tanda.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aruna.decision import Arah, score
from aruna.decision.context_readings import (
    RSI_PENUH,
    TEMBUS,
    TREN,
    VOLUME_PENUH,
    readings_from_analysis,
)


@dataclass(frozen=True)
class Nilai:
    value: str


@dataclass(frozen=True)
class Struktur:
    trend: Nilai
    breakout: Nilai


@dataclass(frozen=True)
class Baca:
    value: float | None
    usable: bool = True


@dataclass(frozen=True)
class Split:
    setuju: tuple[str, ...] = ()
    kontra: tuple[str, ...] = ()
    abstain: tuple[str, ...] = ()


def struktur(tren: str = "UPTREND", tembus: str = "NONE") -> Struktur:
    return Struktur(Nilai(tren), Nilai(tembus))


class TestTren:
    def test_naik_dan_turun_berlawanan_tanda(self) -> None:
        assert readings_from_analysis(structure=struktur("UPTREND"))["trend"] == 1.0
        assert readings_from_analysis(structure=struktur("DOWNTREND"))["trend"] == -1.0

    def test_range_adalah_pengukuran_bukan_ketidaktahuan(self) -> None:
        assert readings_from_analysis(structure=struktur("RANGE"))["trend"] == 0.0

    def test_undetermined_tidak_terukur(self) -> None:
        """Penganalisisnya tidak bisa memutuskan, dan itu bukan nol."""
        assert "trend" not in readings_from_analysis(structure=struktur("UNDETERMINED"))

    def test_undetermined_sengaja_tidak_ada_di_peta(self) -> None:
        assert "UNDETERMINED" not in TREN


class TestPenembusan:
    def test_penembusan_palsu_dibalik_tandanya(self) -> None:
        """Penembusan ke atas yang gagal adalah bukti bearish, bukan bukti
        bullish yang lemah."""
        assert TEMBUS["FALSE_BREAKOUT_UP"] == -1.0
        assert TEMBUS["FALSE_BREAKOUT_DOWN"] == 1.0
        assert TEMBUS["BREAKOUT_UP"] == 1.0

    def test_retest_dan_rejection_tidak_dipaksa_berarah(self) -> None:
        """Sebuah retest bisa mendahului lanjutan maupun pembalikan. Memilih
        arah untuknya menghasilkan angka yang separuh waktunya terbalik."""
        for keadaan in ("RETEST", "REJECTION"):
            assert keadaan not in TEMBUS
            hasil = readings_from_analysis(structure=struktur(tembus=keadaan))
            assert "structure" not in hasil

    def test_tanpa_penembusan_adalah_nol_bukan_hilang(self) -> None:
        assert readings_from_analysis(structure=struktur())["structure"] == 0.0


class TestMomentum:
    def test_rsi_di_atas_lima_puluh_mendukung_long(self) -> None:
        hasil = readings_from_analysis(readings={"rsi": Baca(70.0)})

        assert hasil["momentum"] == pytest.approx(1.0)

    def test_rsi_di_bawah_lima_puluh_mendukung_short(self) -> None:
        assert readings_from_analysis(readings={"rsi": Baca(30.0)})["momentum"] == (
            pytest.approx(-1.0)
        )

    def test_lima_puluh_itu_netral(self) -> None:
        assert readings_from_analysis(readings={"rsi": Baca(50.0)})["momentum"] == 0.0

    def test_ekstrem_dijepit_bukan_meluber(self) -> None:
        assert readings_from_analysis(readings={"rsi": Baca(100.0)})["momentum"] == 1.0
        assert readings_from_analysis(readings={"rsi": Baca(0.0)})["momentum"] == -1.0

    def test_ambangnya_menempatkan_konvensi_di_ujung(self) -> None:
        """30 dan 70 adalah konvensi yang sudah dipakai seluruh sistem ini."""
        assert 50 + RSI_PENUH == 70
        assert 50 - RSI_PENUH == 30

    def test_sampel_kurang_tidak_dipakai(self) -> None:
        hasil = readings_from_analysis(readings={"rsi": Baca(70.0, usable=False)})

        assert "momentum" not in hasil

    def test_nilai_kosong_tidak_dipakai(self) -> None:
        assert "momentum" not in readings_from_analysis(readings={"rsi": Baca(None)})


class TestVolumeAdalahKonfirmasi:
    def test_volume_naik_menguatkan_tren_naik(self) -> None:
        hasil = readings_from_analysis(
            structure=struktur("UPTREND"),
            readings={"volume_trend": Baca(VOLUME_PENUH)},
        )

        assert hasil["volume"] == pytest.approx(1.0)

    def test_volume_naik_menguatkan_tren_turun_ke_arah_turun(self) -> None:
        """Volume yang naik tidak mengatakan harga akan naik; ia mengatakan
        gerakan yang sedang terjadi didukung."""
        hasil = readings_from_analysis(
            structure=struktur("DOWNTREND"),
            readings={"volume_trend": Baca(VOLUME_PENUH)},
        )

        assert hasil["volume"] == pytest.approx(-1.0)

    def test_volume_turun_melemahkan_trennya(self) -> None:
        hasil = readings_from_analysis(
            structure=struktur("UPTREND"),
            readings={"volume_trend": Baca(-VOLUME_PENUH)},
        )

        assert hasil["volume"] == pytest.approx(-1.0)

    def test_tanpa_tren_volume_tidak_menyumbang(self) -> None:
        """Bukan menyumbang nol - tidak menyumbang."""
        hasil = readings_from_analysis(
            structure=struktur("UNDETERMINED"),
            readings={"volume_trend": Baca(80.0)},
        )

        assert "volume" not in hasil

    def test_di_dalam_range_volume_juga_tidak_menyumbang(self) -> None:
        hasil = readings_from_analysis(
            structure=struktur("RANGE"), readings={"volume_trend": Baca(80.0)}
        )

        assert "volume" not in hasil


class TestKesepakatanCouncil:
    def test_bulat_pada_long_menyumbang_positif(self) -> None:
        hasil = readings_from_analysis(
            decision=Arah.LONG, split=Split(setuju=("a", "b", "c"))
        )

        assert hasil["agreement"] == 1.0

    def test_bulat_pada_short_menyumbang_negatif(self) -> None:
        """Lupa membalik tandanya akan membuat council yang bulat pada SHORT
        menyumbang poin untuk LONG."""
        hasil = readings_from_analysis(
            decision=Arah.SHORT, split=Split(setuju=("a", "b", "c"))
        )

        assert hasil["agreement"] == -1.0

    def test_terbelah_menyumbang_sedikit(self) -> None:
        hasil = readings_from_analysis(
            decision=Arah.LONG, split=Split(setuju=("a", "b"), kontra=("c",))
        )

        assert hasil["agreement"] == pytest.approx(1 / 3)

    def test_seluruhnya_abstain_bukan_kesepakatan(self) -> None:
        """Agent yang tidak punya bukti tidak sedang setuju maupun menolak."""
        hasil = readings_from_analysis(
            decision=Arah.LONG, split=Split(abstain=("a", "b", "c"))
        )

        assert "agreement" not in hasil

    def test_no_signal_tidak_punya_kesepakatan_berarah(self) -> None:
        hasil = readings_from_analysis(
            decision=Arah.NO_SIGNAL, split=Split(setuju=("a",))
        )

        assert "agreement" not in hasil


class TestPotongan:
    def test_risiko_dan_berita_dibawa_dari_phase_13(self) -> None:
        """Dua tempat yang menghitung risiko dari bahan yang sama adalah dua
        tempat yang harus tetap sepakat, dan mereka tidak akan."""
        hasil = readings_from_analysis(risk_score=80.0, news_risk=40.0)

        assert hasil["risk"] == pytest.approx(0.8)
        assert hasil["news"] == pytest.approx(0.4)

    def test_potongan_dijepit_di_nol(self) -> None:
        assert readings_from_analysis(risk_score=-10.0)["risk"] == 0.0

    def test_yang_tidak_diberikan_tidak_muncul(self) -> None:
        assert "risk" not in readings_from_analysis()
        assert "news" not in readings_from_analysis()


class TestBersamaPenjumlahnya:
    def test_bacaan_lengkap_menghasilkan_skor_yang_bisa_dipakai(self) -> None:
        bacaan = readings_from_analysis(
            structure=struktur("UPTREND", "BREAKOUT_UP"),
            readings={"rsi": Baca(70.0), "volume_trend": Baca(VOLUME_PENUH)},
            decision=Arah.LONG,
            split=Split(setuju=("a", "b", "c")),
            risk_score=25.0,
            news_risk=0.0,
        )
        s = score(bacaan)

        assert s.usable
        assert s.decision is Arah.LONG
        assert "strategi historis" in s.unknown

    def test_bacaan_kosong_tidak_bisa_dinilai(self) -> None:
        s = score(readings_from_analysis())

        assert not s.usable
        assert s.decision is Arah.NO_SIGNAL

    def test_cakupan_tanpa_tren_dan_volume_masih_terlaporkan(self) -> None:
        """Kasus paling sering di produksi: struktur belum menentukan tren.
        Yang penting bukan lolos atau tidak, melainkan cakupannya dilaporkan
        apa adanya alih-alih diisi."""
        bacaan = readings_from_analysis(
            structure=struktur("UNDETERMINED", "NONE"),
            readings={"rsi": Baca(70.0)},
            decision=Arah.LONG,
            split=Split(setuju=("a", "b")),
        )
        s = score(bacaan)

        assert "tren" in s.unknown
        assert "volume" in s.unknown
        assert 0.0 < s.coverage < 1.0
