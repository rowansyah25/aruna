"""Decision Score dan ambangnya (PASAL 14.16, 14.17).

Satu angka bertanda yang merangkum bukti berarah. Yang diuji di sini bukan
aritmetikanya - itu penjumlahan - melainkan empat keputusan yang menentukan
apakah angkanya berguna: tidak dinormalkan, risiko hanya bisa mengurangi,
bukti tipis bukan skor, dan ambang yang mustahil ditolak.
"""

from __future__ import annotations

import pytest

from aruna.decision import (
    ARAH,
    DEFAULT_THRESHOLD,
    MAX_ARAH,
    MAX_PENALTI,
    MIN_COVERAGE,
    PENALTI,
    Arah,
    ThresholdError,
    check_threshold,
    points_of,
    score,
)

#: Contoh PASAL 14.16, apa adanya: seluruh bukti berarah penuh mendukung LONG,
#: dengan potongan risiko dan berita penuh.
CONTOH_PASAL = {
    "trend": 1.0,
    "structure": 1.0,
    "momentum": 1.0,
    "volume": 1.0,
    "agreement": 1.0,
    "history": 1.0,
    "risk": 1.0,
    "news": 1.0,
}

#: Tiga komponen terbesar: 50 dari 81 poin, cukup untuk melewati cakupan
#: minimum tanpa mendekati ambang bawaan.
TIGA = ("trend", "structure", "momentum")


def tiga(nilai: float) -> dict[str, float]:
    return dict.fromkeys(TIGA, nilai)


class TestContohSpesifikasi:
    def test_contoh_pasal_1416_menghasilkan_69_dan_long(self) -> None:
        """PASAL 14.16 menuliskan hasilnya: 18+14+12+18+10+9 -8 -4 = 69, LONG.

        Kalau bobot di tabel bergeser, angka ini bergeser bersamanya - dan
        pergeseran diam-diam pada bobot adalah cara paling halus mengubah kapan
        ARUNA berpendapat.
        """
        s = score(CONTOH_PASAL)

        assert s.raw == 81.0
        assert s.value == 69.0
        assert s.decision is Arah.LONG

    def test_bobotnya_persis_seperti_di_pasal(self) -> None:
        assert points_of("trend") == 18.0
        assert points_of("structure") == 18.0
        assert points_of("momentum") == 14.0
        assert points_of("volume") == 12.0
        assert points_of("agreement") == 10.0
        assert points_of("history") == 9.0
        assert points_of("risk") == 8.0
        assert points_of("news") == 4.0

    def test_totalnya_konsisten_dengan_tabelnya(self) -> None:
        assert MAX_ARAH == sum(b.points for b in ARAH) == 81.0
        assert MAX_PENALTI == sum(b.points for b in PENALTI) == 12.0

    def test_kunci_tak_dikenal_bernilai_nol(self) -> None:
        assert points_of("tidak ada faktor ini") == 0.0


class TestTidakDinormalkan:
    def test_bukti_sebagian_menghasilkan_skor_sebagian(self) -> None:
        """Ini beda pokok dengan risk score, yang membagi dengan bobot terukur.

        Tiga komponen penuh menghasilkan 50, bukan 100. Kalau dinormalkan,
        tiga komponen yang kebetulan searah akan terbaca sebagai kasus yang
        bulat - dan 50 di bawah ambang menjadi LONG.
        """
        s = score(tiga(1.0))

        assert s.value == 50.0
        assert s.decision is Arah.NO_SIGNAL

    def test_kunci_tak_dikenal_diabaikan_diam(self) -> None:
        """Tabelnya bisa bertambah; pemanggil lama tidak boleh pecah."""
        bacaan = tiga(1.0) | {"faktor_masa_depan": 1.0}

        assert score(bacaan).value == score(tiga(1.0)).value

    def test_nilai_di_luar_rentang_dijepit(self) -> None:
        assert score(tiga(9.0)).value == score(tiga(1.0)).value
        assert score(tiga(-9.0)).value == score(tiga(-1.0)).value


class TestPenaltiHanyaMengurangi:
    def test_risiko_rendah_tidak_menambah_poin(self) -> None:
        """Risiko rendah bukan bukti bahwa harga akan naik; ia hanya berarti
        kalau salah, salahnya lebih murah."""
        tanpa = score(tiga(1.0))
        nol = score(tiga(1.0) | {"risk": 0.0})
        negatif = score(tiga(1.0) | {"risk": -1.0})

        assert tanpa.value == nol.value == negatif.value == 50.0

    def test_potongan_tidak_pernah_membalik_arah(self) -> None:
        """Risiko tinggi tidak mengubah kasus LONG yang lemah menjadi kasus
        SHORT - itu mengarang arah dari ketiadaan bukti arah."""
        s = score(tiga(0.1) | {"risk": 1.0, "news": 1.0})

        assert s.raw == 5.0
        assert s.value == 0.0
        assert s.decision is Arah.NO_SIGNAL

    def test_potongan_memakan_besaran_pada_kasus_short(self) -> None:
        s = score(tiga(-1.0) | {"risk": 1.0, "news": 1.0})

        assert s.raw == -50.0
        assert s.value == -38.0

    def test_potongan_nol_tidak_dicetak(self) -> None:
        s = score(tiga(1.0) | {"risk": 0.0})
        assert s.penalties == ()


class TestCakupan:
    def test_bukti_tipis_bukan_skor(self) -> None:
        """Skor +36 dari dua dari enam komponen terbaca sama dengan +36 dari
        enam-enamnya, jadi yang dikembalikan bukan angka."""
        s = score({"trend": 1.0, "structure": 1.0}, threshold=20.0)

        assert s.coverage == pytest.approx(36 / 81)
        assert s.coverage < MIN_COVERAGE
        assert s.value is None
        assert s.decision is Arah.NO_SIGNAL

    def test_gerbang_cakupan_benar_benar_menahan_sesuatu(self) -> None:
        """Skor mentah yang sama, cakupan yang berbeda, vonis yang berbeda.

        Keduanya bernilai 36 mentah. Yang cakupannya cukup menjadi LONG pada
        ambang 20; yang tipis ditahan. Tanpa kontras ini, gerbang cakupan bisa
        saja tidak pernah mengubah satu keputusan pun.
        """
        tipis = score({"trend": 1.0, "structure": 1.0}, threshold=20.0)
        cukup = score(
            {"trend": 1.0, "structure": 1.0, "momentum": 0.0}, threshold=20.0
        )

        assert cukup.value == 36.0
        assert cukup.decision is Arah.LONG
        assert tipis.value is None
        assert tipis.decision is Arah.NO_SIGNAL

    def test_cakupan_cukup_menghasilkan_angka(self) -> None:
        s = score(tiga(1.0))

        assert s.coverage == pytest.approx(50 / 81)
        assert s.coverage >= MIN_COVERAGE
        assert s.value is not None

    def test_yang_tidak_terukur_disebut_namanya(self) -> None:
        s = score(tiga(1.0))

        assert "volume" in s.unknown
        assert "risiko" in s.unknown
        assert "berita" in s.unknown

    def test_tanpa_bacaan_sama_sekali(self) -> None:
        s = score({})

        assert s.value is None
        assert s.coverage == 0.0
        assert s.decision is Arah.NO_SIGNAL


class TestAmbang:
    def test_ambang_di_atas_maksimum_ditolak(self) -> None:
        """Ambang yang tidak mungkin tercapai membuat ARUNA diam selamanya
        tanpa satu pun baris log yang salah."""
        with pytest.raises(ThresholdError, match="tidak akan pernah"):
            check_threshold(MAX_ARAH + 0.1)

    def test_ambang_nol_atau_negatif_ditolak(self) -> None:
        for buruk in (0.0, -1.0, -60.0):
            with pytest.raises(ThresholdError, match="setiap skor"):
                check_threshold(buruk)

    def test_ambang_buruk_ditolak_lewat_score_juga(self) -> None:
        with pytest.raises(ThresholdError):
            score(CONTOH_PASAL, threshold=999.0)

    def test_tepat_di_ambang_sudah_berarah(self) -> None:
        """``>=``, bukan ``>``: sebuah skor yang persis sama dengan ambangnya
        sudah memenuhi syarat yang ditulis PASAL 14.17."""
        assert score(CONTOH_PASAL, threshold=69.0).decision is Arah.LONG
        assert score(CONTOH_PASAL, threshold=69.1).decision is Arah.NO_SIGNAL

    def test_ambang_berlaku_simetris(self) -> None:
        naik = score(CONTOH_PASAL)
        turun = score({k: -v for k, v in CONTOH_PASAL.items()} | {"risk": 1.0, "news": 1.0})

        assert naik.decision is Arah.LONG
        assert turun.decision is Arah.SHORT
        assert naik.value == -turun.value

    def test_ambang_bawaan_bisa_dicapai(self) -> None:
        assert 0 < DEFAULT_THRESHOLD <= MAX_ARAH

    def test_ambang_yang_dipakai_ikut_dilaporkan(self) -> None:
        assert score(CONTOH_PASAL, threshold=30.0).threshold == 30.0


class TestKalimat:
    def test_angkanya_tidak_pernah_berdiri_sebagai_persen(self) -> None:
        """PASAL 14.16: skor bukan probabilitas profit. "69" yang dicetak
        sendirian akan dibaca sebagai 69 persen."""
        teks = score(CONTOH_PASAL).line()

        assert "69%" not in teks
        assert "+69" in teks
        assert "maksimum 81" in teks

    def test_laporannya_membawa_penyangkalannya(self) -> None:
        teks = "\n".join(score(CONTOH_PASAL).report())

        assert "BUKAN peluang profit" in teks
        assert "bukan jaminan" in teks

    def test_tidak_ada_klaim_terlarang_pasal_51(self) -> None:
        teks = "\n".join(score(CONTOH_PASAL).report()).lower()

        for terlarang in ("pasti profit", "pasti naik", "pasti turun", "100% win"):
            assert terlarang not in teks

    def test_bukti_tipis_mengatakan_kenapa(self) -> None:
        teks = score({"trend": 1.0}).line()

        assert "NO SIGNAL" in teks
        assert "terlalu tipis" in teks

    def test_sumbangan_terurut_dari_yang_terbesar(self) -> None:
        s = score(CONTOH_PASAL)
        angka = [abs(n) for _, n in s.contributions]

        assert angka == sorted(angka, reverse=True)
        assert s.contributions[0][0] in ("tren", "struktur pasar")


class TestKosakata:
    def test_tiga_keluaran_tidak_lebih(self) -> None:
        assert {a.value for a in Arah} == {"LONG", "SHORT", "NO SIGNAL"}

    def test_cocok_dengan_kosakata_publik(self) -> None:
        """Dua tempat menuliskan kata yang sama; kalau salah satunya bergeser,
        pesan keluar dan keputusan internal berhenti sejalan."""
        from aruna.notify import verdict

        assert Arah.LONG.value == verdict.LONG
        assert Arah.SHORT.value == verdict.SHORT
        assert Arah.NO_SIGNAL.value == verdict.NO_SIGNAL


class TestKonfigurasi:
    def test_batas_atas_setting_sama_dengan_maksimum_skor(self) -> None:
        """Nilainya ditulis harfiah di ``core.config`` supaya lapisan core
        tidak bergantung ke atas. Test inilah yang membuat pergeseran salah
        satunya berbunyi."""
        from aruna.core.config import DecisionSettings

        field = DecisionSettings.model_fields["threshold"]
        atas = [
            m.le for m in field.metadata if getattr(m, "le", None) is not None
        ]

        assert atas == [MAX_ARAH]

    def test_ambang_bawaannya_sama(self) -> None:
        from aruna.core.config import DecisionSettings

        assert DecisionSettings().threshold == DEFAULT_THRESHOLD

    def test_ambang_mustahil_ditolak_saat_start(self) -> None:
        from pydantic import ValidationError

        from aruna.core.config import DecisionSettings

        with pytest.raises(ValidationError):
            DecisionSettings(threshold=MAX_ARAH + 1)
        with pytest.raises(ValidationError):
            DecisionSettings(threshold=0)
