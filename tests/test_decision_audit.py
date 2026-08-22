"""Daftar periksa sebelum terbit (PASAL 14.18, 14.25).

Kegagalan yang dijaga di sini: daftar periksa yang memperlakukan "tidak tahu"
sebagai "aman". Ia akan meloloskan justru signal yang paling sedikit diketahui
tentangnya, dan melakukannya dengan tampilan yang meyakinkan - semua barisnya
bercentang.
"""

from __future__ import annotations

from aruna.decision import Arah, Butir, Nilai, audit
from aruna.decision.audit import BERARAH

#: Empat belas butir PASAL 14.25, ditulis ulang dari spesifikasinya.
#:
#: Sengaja diketik ulang alih-alih diturunkan dari ``Butir``: daftar yang
#: dibangun dari kodenya sendiri akan tetap cocok betapa pun banyak butir yang
#: hilang.
PASAL_14_25 = [
    "data valid",
    "data segar",
    "rezim pasar",
    "analisis multi-timeframe",
    "analisis agent",
    "protes",
    "council",
    "signal quality",
    "strategi historis",
    "analisis risiko",
    "risk/reward",
    "syarat pembatalan",
    "masa berlaku",
    "horizon keputusan",
]

SEMUA_LULUS = dict.fromkeys(Butir, True)


class TestKelengkapan:
    def test_empat_belas_butir_persis_seperti_pasalnya(self) -> None:
        assert [b.value for b in Butir] == PASAL_14_25

    def test_butir_pasal_1418_semuanya_termasuk(self) -> None:
        """PASAL 14.18 menyebut sebelas butir; PASAL 14.25 menambah tiga."""
        sebelas = {
            Butir.DATA, Butir.FRESHNESS, Butir.REGIME, Butir.MTF,
            Butir.AGENTS, Butir.PROTEST, Butir.COUNCIL, Butir.QUALITY,
            Butir.RISK, Butir.INVALIDATION, Butir.HORIZON,
        }
        tambahan = {Butir.STRATEGY, Butir.RR, Butir.EXPIRATION}

        assert sebelas | tambahan == set(Butir)
        assert len(sebelas) == 11

    def test_daftarnya_selalu_lengkap_apa_pun_masukannya(self) -> None:
        """Butir yang hilang dari daftar periksa adalah butir yang tidak pernah
        menahan apa pun."""
        a = audit({})

        assert len(a.values) == len(Butir)
        assert len(a.unknowns) == len(Butir)

    def test_urutannya_tetap(self) -> None:
        assert [b for b, _ in audit({}).values] == list(Butir)


class TestBelumDinilaiBukanLulus:
    def test_butir_yang_hilang_menahan_terbit(self) -> None:
        kurang = dict(SEMUA_LULUS)
        del kurang[Butir.RISK]

        a = audit(kurang)

        assert not a.may_publish
        assert a.unknowns == (Butir.RISK,)
        assert a.failures == ()

    def test_none_sama_dengan_hilang(self) -> None:
        kurang = dict(SEMUA_LULUS)
        del kurang[Butir.RISK]

        assert audit(SEMUA_LULUS | {Butir.RISK: None}).values == audit(kurang).values

    def test_gagal_dan_belum_dinilai_dilaporkan_terpisah(self) -> None:
        """Yang satu berarti setup-nya salah, yang lain berarti ada lapisan
        yang tidak berjalan. Meleburnya menyembunyikan perbedaan itu."""
        a = audit(SEMUA_LULUS | {Butir.RISK: False, Butir.MTF: None})

        assert a.failures == (Butir.RISK,)
        assert a.unknowns == (Butir.MTF,)
        assert not a.may_publish

    def test_tiga_keadaan_bukan_dua(self) -> None:
        assert {n.value for n in Nilai} == {"LULUS", "GAGAL", "BELUM DINILAI"}


class TestGerbang:
    def test_semua_lulus_boleh_terbit(self) -> None:
        """"Jika semua PASS: PUBLISH." """
        assert audit(SEMUA_LULUS).may_publish

    def test_satu_gagal_menahan_seluruhnya(self) -> None:
        """PASAL 14.18: "Jika salah satu komponen penting gagal: NO SIGNAL." """
        for b in Butir:
            a = audit(SEMUA_LULUS | {b: False})
            assert not a.may_publish, b

    def test_gerbang_hanya_bisa_menahan(self) -> None:
        """Daftar periksa yang lulus tidak menciptakan bukti yang tidak ada."""
        lolos = audit(SEMUA_LULUS)

        assert lolos.verdict(Arah.LONG) is Arah.LONG
        assert lolos.verdict(Arah.NO_SIGNAL) is Arah.NO_SIGNAL

    def test_gagal_menurunkan_arah_menjadi_no_signal(self) -> None:
        gagal = audit(SEMUA_LULUS | {Butir.RISK: False})

        assert gagal.verdict(Arah.LONG) is Arah.NO_SIGNAL
        assert gagal.verdict(Arah.SHORT) is Arah.NO_SIGNAL

    def test_daftar_kosong_tidak_menerbitkan_apa_pun(self) -> None:
        assert audit({}).verdict(Arah.LONG) is Arah.NO_SIGNAL


class TestButirYangTidakBerlaku:
    """Keputusan tanpa arah tidak punya entry untuk dijadikan pangkal.

    Terukur di jalur hidup: dari dua puluh simbol, tujuh yang punya arah
    semuanya punya syarat pembatalan, dan tiga belas yang tidak punya arah
    tidak satu pun - korelasi sempurna.
    """

    def test_tiga_butir_berarah(self) -> None:
        assert set(BERARAH) == {Butir.RR, Butir.INVALIDATION, Butir.EXPIRATION}

    def test_tanpa_arah_butir_itu_tidak_menahan(self) -> None:
        kurang = {b: True for b in Butir if b not in BERARAH}

        assert audit(kurang, directional=False).may_publish

    def test_dengan_arah_butir_itu_tetap_menahan(self) -> None:
        kurang = {b: True for b in Butir if b not in BERARAH}

        assert not audit(kurang, directional=True).may_publish

    def test_bawaannya_yang_paling_ketat(self) -> None:
        """Kelonggaran yang aktif secara bawaan adalah kelonggaran yang
        menyebar tanpa ada yang memilih."""
        kurang = {b: True for b in Butir if b not in BERARAH}

        assert not audit(kurang).may_publish

    def test_gagal_tetap_menahan_walau_tidak_berlaku(self) -> None:
        """Yang dilonggarkan adalah pertanyaan yang tidak punya makna, bukan
        jawaban yang buruk."""
        a = audit(SEMUA_LULUS | {Butir.RR: False}, directional=False)

        assert not a.may_publish
        assert Butir.RR in a.failures

    def test_yang_tidak_berlaku_bukan_yang_belum_dinilai(self) -> None:
        kurang = {b: True for b in Butir if b not in BERARAH}
        a = audit(kurang, directional=False)

        assert a.unknowns == ()
        assert set(a.inapplicable) == set(BERARAH)

    def test_laporannya_membedakan_keduanya(self) -> None:
        kurang = {b: True for b in Butir if b not in BERARAH}
        teks = "\n".join(audit(kurang, directional=False).report())

        assert "Tidak berlaku untuk keputusan tanpa arah" in teks
        assert "bukan berarti aman" not in teks


class TestKalimat:
    def test_lolos_menyebut_jumlahnya(self) -> None:
        assert "14/14" in audit(SEMUA_LULUS).line()

    def test_tertahan_menyebut_kenapa(self) -> None:
        """Gerbang yang berkata "audit gagal" tidak mengajarkan apa pun."""
        teks = "\n".join(audit(SEMUA_LULUS | {Butir.RISK: False}).report())

        assert "TIDAK DITERBITKAN" in teks
        assert "analisis risiko" in teks

    def test_yang_belum_dinilai_disebut_bukan_aman(self) -> None:
        kurang = dict(SEMUA_LULUS)
        del kurang[Butir.MTF]

        teks = "\n".join(audit(kurang).report())

        assert "bukan berarti aman" in teks
        assert "analisis multi-timeframe" in teks

    def test_setiap_butir_tercetak(self) -> None:
        teks = "\n".join(audit(SEMUA_LULUS).report())

        for b in Butir:
            assert b.value in teks
