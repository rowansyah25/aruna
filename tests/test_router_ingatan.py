"""Ingatan Phase 15 sebagai bukti bagi router (bagian 17.20).

**Ditunda di rencana awal, dan alasannya masih separuh benar.** Yang mahal
adalah sapuan kemiripannya - lima ribu ingatan per (pasar, timeframe), tiga
belas dimensi berbobot di Python, tiap aset tiap siklus. Modul ini mengajukan
pertanyaan yang jauh lebih sempit dan terindeks, dan **tidak mengaku** sebagai
mesin kemiripan PASAL 15.

Yang dijaga paling keras di sini dua hal: ingatan tidak boleh memutuskan ulang
apa yang gerbang PASAL 15.44 sudah putuskan, dan ia tidak boleh membalik
peringkat.
"""

from __future__ import annotations

from aruna.memory.context import Pengaruh
from aruna.router.ingatan import (
    INTERVAL_INGATAN,
    MINIMUM_INGATAN,
    BacaanIngatan,
    pengaruh,
)


def _b(menang: int, total: int) -> BacaanIngatan:
    return BacaanIngatan(menang=menang, total=total)


class TestGerbangManfaatDihormati:
    """PASAL 15.44 sudah memutuskan; router membacanya, bukan memutuskan ulang."""

    def test_timeframe_yang_belum_terbukti_tidak_diberi_bobot(self) -> None:
        """Terukur 2026-08-23 di `app_state['memory_manfaat']`::

            1h  mendukung 123W/88L = 58,3%   melawan 32W/23L = 58,2%

        Kasus yang didukung ingatan dan yang dilawannya berakhir sama saja.
        Memberi bobot di sana adalah memaksakan penggunaan memory atas bukti
        yang menyatakan ia tidak menambah apa-apa.
        """
        sikap, skala, alasan = pengaruh(_b(90, 100), dipakai=False)

        assert sikap is Pengaruh.NEUTRAL
        assert skala == 1.0
        assert "15.44" in alasan

    def test_hanya_15m_yang_terbukti_membedakan(self) -> None:
        """15m: mendukung 53,3% melawan 38,7% - selisih 14,6 poin. Itu satu-
        satunya timeframe yang gerbangnya buka, dan konstanta ini menyebutnya
        alih-alih membiarkan pemanggil menebak."""
        assert INTERVAL_INGATAN == "15m"


class TestMenskalakanBukanMemihak:
    def test_skalanya_seragam_jadi_peringkat_tak_bisa_terbalik(self) -> None:
        """**Ini yang membuatnya aman.** Ingatan mencatat hasil per KONDISI -
        simbol, timeframe, rezim - bukan per strategi. Ia tidak punya apa pun
        untuk dikatakan tentang STR-001 melawan STR-004.

        Penskalaan yang seragam tidak bisa membalik peringkat, jadi ingatan
        tidak akan pernah menaikkan strategi yang kalah di atas yang menang.
        Yang berubah adalah APAKAH ada yang cukup layak, bukan SIAPA.
        """
        _, buruk, _ = pengaruh(_b(2, 40), dipakai=True)
        _, baik, _ = pengaruh(_b(38, 40), dipakai=True)

        # Dua skor berbeda, diskalakan pengali yang sama, urutannya tetap.
        for skala in (buruk, baik):
            assert (50 + (75 - 50) * skala) > (50 + (60 - 50) * skala)

    def test_ingatan_buruk_menarik_ke_netral(self) -> None:
        _, skala, _ = pengaruh(_b(2, 40), dipakai=True)

        assert skala < 1.0

    def test_ingatan_baik_mendorong_sedikit(self) -> None:
        _, skala, _ = pengaruh(_b(38, 40), dipakai=True)

        assert skala > 1.0

    def test_ingatan_adalah_bukti_bukan_veto(self) -> None:
        """PASAL 15.42: keputusan finalnya tetap lewat Phase 14. Kondisi yang
        sejarahnya paling buruk sekalipun hanya menurunkan seperlima jarak dari
        netral - kalau lebih, ingatan berhenti jadi bukti dan mulai memerintah.
        """
        _, terburuk, _ = pengaruh(_b(0, 100), dipakai=True)
        _, terbaik, _ = pengaruh(_b(100, 100), dipakai=True)

        assert 0.8 <= terburuk < 1.0
        assert 1.0 < terbaik <= 1.2


class TestDiamKetikaBelumTahu:
    def test_sampel_kurang_tidak_membobot(self) -> None:
        """Diam berarti belum terbukti, bukan terbukti baik - disiplin yang
        sama dengan gerbang manfaat itu sendiri."""
        sikap, skala, alasan = pengaruh(_b(19, 19), dipakai=True)

        assert sikap is Pengaruh.NEUTRAL
        assert skala == 1.0
        assert str(MINIMUM_INGATAN) in alasan

    def test_tanpa_bacaan_sama_sekali(self) -> None:
        sikap, skala, _ = pengaruh(None, dipakai=True)

        assert sikap is Pengaruh.NEUTRAL
        assert skala == 1.0

    def test_dekat_netral_tidak_berkata_apa_pun(self) -> None:
        """Selisih kecil pada sampel kecil adalah derau. Bereaksi padanya
        membuat router bergoyang mengikuti kebisingan."""
        sikap, skala, _ = pengaruh(_b(21, 40), dipakai=True)

        assert sikap is Pengaruh.NEUTRAL
        assert skala == 1.0

    def test_win_rate_kosong_bukan_nol(self) -> None:
        """Kondisi yang belum pernah terjadi dan yang selalu berakhir buruk
        adalah dua hal yang sangat berbeda."""
        assert _b(0, 0).win_rate is None
        assert _b(0, 40).win_rate == 0.0


class TestKosakatanyaDipinjam:
    def test_memakai_pengaruh_phase_15_bukan_yang_baru(self) -> None:
        """Dua kosakata untuk satu gagasan menghasilkan laporan yang tidak bisa
        disandingkan - dan operator yang harus mengingat mana yang mana."""
        sikap_baik, _, _ = pengaruh(_b(38, 40), dipakai=True)
        sikap_buruk, _, _ = pengaruh(_b(2, 40), dipakai=True)

        assert sikap_baik is Pengaruh.SUPPORTIVE
        assert sikap_buruk is Pengaruh.CONTRARY

    def test_alasannya_selalu_menyebut_angkanya(self) -> None:
        """Skor yang bergeser tanpa angka tidak bisa dibantah."""
        for bacaan in (_b(38, 40), _b(2, 40), _b(21, 40), _b(5, 5)):
            _, _, alasan = pengaruh(bacaan, dipakai=True)

            assert any(c.isdigit() for c in alasan), alasan
