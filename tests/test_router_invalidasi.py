"""Peralihan pilihan router dicatat, bukan disimpulkan (bagian 17.26, 17.28).

Contoh operator 2026-08-23, dan test pertama di bawah adalah contoh itu apa
adanya::

    TRENDING UP  ->  Trend Following dipilih
         |
    SIDEWAYS     ->  Trend Following TIDAK LAGI COCOK
                     Mean Reversion menjadi kandidat

**Yang diuji di sini bukan perilakunya.** Perilakunya sudah ada sebelum modul
invalidasi: fase router memilih ulang tiap siklus, jadi begitu rezimnya RANGING
maka STR-001 memang jatuh dan STR-004 naik - dan `test_router_fase` yang
membuktikannya. Yang diuji di sini JEJAKNYA, karena adaptasi yang tidak bisa
dilihat tidak bisa dibuktikan terjadi.
"""

from __future__ import annotations

from aruna.router.invalidasi import (
    AlasanInvalid,
    PilihanSebelumnya,
    kenapa_berganti,
)
from aruna.router.kecocokan import Kecocokan
from aruna.router.putusan import AlasanKosong, PutusanRouter
from aruna.router.rezim import PetaRezim

SEMUA = frozenset({"STR-001", "STR-003", "STR-004", "STR-006"})


def _peta(regime: str | None = "RANGING") -> PetaRezim:
    return PetaRezim(regime, 85.0, (), (), ())


def _putusan(kode: str | None) -> PutusanRouter:
    if kode is None:
        return PutusanRouter(
            None, None, "skor tertinggi 44", AlasanKosong.TAK_ADA_YANG_COCOK
        )
    return PutusanRouter(Kecocokan(kode, 88, (), 900), None, "", None, "RANGING")


class TestContohOperator:
    def test_trend_following_gugur_saat_rezim_jadi_sideways(self) -> None:
        """Contoh operator 2026-08-23 apa adanya. STR-001 menyukai TRENDING;
        begitu rezimnya RANGING ia tidak lagi memimpin - dan sebabnya harus
        DISEBUT, bukan sekadar hilang dari baris berikutnya."""
        alasan = kenapa_berganti(
            PilihanSebelumnya("STR-001", "TRENDING"),
            putusan=_putusan("STR-004"),
            peta=_peta("RANGING"),
            boleh_memimpin=SEMUA,
        )

        assert any("STR-001 tidak lagi memimpin" in a for a in alasan)
        assert any(AlasanInvalid.REZIM_BERGANTI in a for a in alasan)
        assert any("TRENDING -> RANGING" in a for a in alasan)
        assert any("sekarang STR-004" in a for a in alasan)

    def test_gugur_menjadi_tidak_ada_juga_dicatat(self) -> None:
        """Champion yang digantikan NONE adalah peralihan yang paling perlu
        terlihat - dan yang paling mudah terbaca sebagai "fasenya mati"."""
        alasan = kenapa_berganti(
            PilihanSebelumnya("STR-001", "TRENDING"),
            putusan=_putusan(None),
            peta=_peta("HIGH_VOLATILITY"),
            boleh_memimpin=SEMUA,
        )

        assert any("sekarang tidak ada" in a for a in alasan)


class TestSebabnyaDibedakan:
    def test_status_diperiksa_sebelum_rezim(self) -> None:
        """**Urutannya menentukan, dan bukan sekadar rapi.** Strategi yang
        statusnya diturunkan operator tidak lagi memimpin apa pun rezimnya.
        Melaporkan "rezim berganti" untuknya menyesatkan pembacanya ke arah
        yang salah - ia akan memeriksa pasar padahal yang berubah katalognya.
        """
        alasan = kenapa_berganti(
            PilihanSebelumnya("STR-999", "TRENDING"),
            putusan=_putusan("STR-004"),
            peta=_peta("RANGING"),
            boleh_memimpin=SEMUA,
        )

        assert any(AlasanInvalid.STATUS_BERUBAH in a for a in alasan)
        assert not any(AlasanInvalid.REZIM_BERGANTI in a for a in alasan)

    def test_rezim_sama_tapi_champion_berganti(self) -> None:
        """Skornya yang bergeser, bukan pasarnya. Paling sering karena
        stabilitas atau keyakinan rezim berubah - dan menyebutnya "rezim
        berganti" akan mengarang perpindahan yang tidak terjadi."""
        alasan = kenapa_berganti(
            PilihanSebelumnya("STR-001", "RANGING"),
            putusan=_putusan("STR-004"),
            peta=_peta("RANGING"),
            boleh_memimpin=SEMUA,
        )

        assert any(AlasanInvalid.TIDAK_LAGI_TERBAIK in a for a in alasan)

    def test_tiap_sebab_punya_nilai_sendiri(self) -> None:
        nilai = [str(a) for a in AlasanInvalid]

        assert len(set(nilai)) == len(nilai)


class TestDiamKetikaMemangTidakBerganti:
    def test_champion_yang_sama_tidak_menghasilkan_kalimat(self) -> None:
        """Baris yang mengumumkan peralihan tiap siklus akan membuat peralihan
        yang sungguhan tidak terlihat."""
        alasan = kenapa_berganti(
            PilihanSebelumnya("STR-004", "RANGING"),
            putusan=_putusan("STR-004"),
            peta=_peta("RANGING"),
            boleh_memimpin=SEMUA,
        )

        assert alasan == ()

    def test_belum_ada_pilihan_sebelumnya(self) -> None:
        assert kenapa_berganti(
            None, putusan=_putusan("STR-004"), peta=_peta(),
            boleh_memimpin=SEMUA,
        ) == ()

    def test_dulu_tidak_ada_sekarang_pun_tidak(self) -> None:
        """Dua NONE berturut-turut bukan peralihan. Menyebutnya peralihan akan
        membuat aset yang memang tidak pernah punya strategi terlihat
        berganti-ganti terus."""
        assert kenapa_berganti(
            PilihanSebelumnya(None, "UNCERTAIN"),
            putusan=_putusan(None),
            peta=_peta("UNCERTAIN"),
            boleh_memimpin=SEMUA,
        ) == ()

    def test_dulu_tidak_ada_sekarang_ada_bukan_kegugurannya(self) -> None:
        """Aset yang baru mendapat champion pertamanya tidak sedang menggugurkan
        siapa pun."""
        assert kenapa_berganti(
            PilihanSebelumnya(None, "UNCERTAIN"),
            putusan=_putusan("STR-004"),
            peta=_peta("RANGING"),
            boleh_memimpin=SEMUA,
        ) == ()


class TestTidakMenilaiUlang:
    def test_tidak_memanggil_mesin_kecocokan(self) -> None:
        """**Batas yang disengaja.** Menghitung sendiri "apakah STR-001 masih
        cocok" berarti aturan kecocokan KEDUA yang harus selamanya sepakat
        dengan `kecocokan.nilai` - dan dua aturan yang harus tetap sepakat
        sudah beberapa kali jadi bug di proyek ini.

        Yang dilakukan modul ini murni pembukuan atas apa yang sudah
        diputuskan.
        """
        import ast
        import inspect

        from aruna.router import invalidasi

        pohon = ast.parse(inspect.getsource(invalidasi))
        dipanggil = {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

        assert "nilai" not in dipanggil
        assert "pilih" not in dipanggil
        assert "susun_peta" not in dipanggil

    def test_kelayakan_dioper_bukan_dihitung(self) -> None:
        """`boleh_memimpin` datang dari `peringkat.kandidat_layak`. Menghitung
        ulang di sini berarti aturan status yang kedua."""
        import inspect

        from aruna.router.invalidasi import kenapa_berganti as f

        assert "boleh_memimpin" in inspect.signature(f).parameters
