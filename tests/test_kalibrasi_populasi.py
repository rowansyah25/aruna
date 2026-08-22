"""Laporan yang DILAPORKAN dan yang DITERAPKAN harus mengukur populasi sama.

Terukur di produksi 2026-08-21, dan hanya pengukuran produksi yang
menemukannya: `review()` menyaring `published`, `measured_history()` tidak.
Akibatnya laporan yang disimpan dan dilaporkan ke operator dihitung dari
populasi yang berbeda dengan laporan yang benar-benar menggerakkan keputusan -
**dan yang salah justru yang menggerakkan keputusan.**

Selisihnya bukan halus: keyakinan 46% dipetakan menjadi 19% alih-alih 53%.
"""

from __future__ import annotations

import ast
import inspect
from textwrap import dedent

from aruna.learning.service import SAMPEL_KALIBRASI, _klaim_terkalibrasi

BARIS = [
    {"direction": "BUY", "published": True},
    {"direction": "SELL", "published": True},
    {"direction": "BUY", "published": False},
    {"direction": "WAIT", "published": True},
    {"direction": "NO_SIGNAL", "published": True},
]


class TestSaringannya:
    def test_hanya_berarah_dan_diterbitkan(self) -> None:
        hasil = _klaim_terkalibrasi(BARIS)

        assert len(hasil) == 2
        assert all(r["direction"] in ("BUY", "SELL") for r in hasil)
        assert all(r["published"] for r in hasil)

    def test_yang_ditahan_tidak_dinilai(self) -> None:
        """Putusan yang lock-nya tolak bukan klaim. Menilainya berarti mengukur
        sistem terhadap sesuatu yang justru ia menolak mengatakannya."""
        hasil = _klaim_terkalibrasi([{"direction": "BUY", "published": False}])

        assert hasil == []

    def test_tanpa_arah_tidak_dinilai(self) -> None:
        """Tidak ada sisi untuk benar atau salah."""
        assert _klaim_terkalibrasi([{"direction": "WAIT", "published": True}]) == []

    def test_baris_lama_tanpa_kolom_published_dianggap_diterbitkan(self) -> None:
        """Baris yang lebih tua dari kolomnya tidak boleh hilang diam-diam dari
        pengukuran."""
        assert len(_klaim_terkalibrasi([{"direction": "BUY"}])) == 1


class TestSatuTempatSaja:
    """Dua saringan yang harus tetap sepakat adalah dua yang suatu saat tidak -
    dan di sini 'suatu saat' itu sudah terjadi."""

    def _pohon(self, fn):
        return ast.parse(dedent(inspect.getsource(fn)))

    def _pemanggil(self, fn) -> set[str]:
        return {
            n.func.id
            for n in ast.walk(self._pohon(fn))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

    def test_review_memakai_saringan_bersama(self) -> None:
        from aruna.learning.service import LearningService

        assert "_klaim_terkalibrasi" in self._pemanggil(LearningService.review)

    def test_measured_history_memakai_saringan_bersama(self) -> None:
        """Yang ini yang dulu berbeda, dan yang ini yang menggerakkan
        keputusan."""
        from aruna.learning.service import LearningService

        assert "_klaim_terkalibrasi" in self._pemanggil(
            LearningService.measured_history
        )

    def test_tidak_ada_saringan_tangan_yang_tersisa(self) -> None:
        """Penjaga terhadap salinan ketiga yang muncul kemudian."""
        from aruna.learning import service

        pohon = ast.parse(inspect.getsource(service))
        for n in ast.walk(pohon):
            if not isinstance(n, ast.ListComp):
                continue
            teks = ast.dump(n)
            if '"BUY"' in teks or "'BUY'" in teks:
                # Satu-satunya yang boleh ada adalah yang di dalam
                # `_klaim_terkalibrasi` itu sendiri.
                induk = [
                    f.name for f in ast.walk(pohon)
                    if isinstance(f, ast.FunctionDef)
                    and any(x is n for x in ast.walk(f))
                ]
                assert induk == ["_klaim_terkalibrasi"], induk


class TestSampelnyaCukup:
    def test_batas_cukup_besar_untuk_keempat_pita(self) -> None:
        """Terukur: pada 500 hanya 67 baris lolos dan tiga dari empat pita
        kekurangan sampel. Batas yang terlalu kecil tidak membuat kalibrasi
        salah - ia membuatnya tidak ada, lalu diam."""
        assert SAMPEL_KALIBRASI >= 5000

    def test_measured_history_mengoper_batasnya(self) -> None:
        """Konstanta yang ada tapi tidak dioper sama saja dengan tidak ada."""
        from aruna.learning.service import LearningService

        pohon = ast.parse(
            dedent(inspect.getsource(LearningService.measured_history))
        )
        nama = {n.id for n in ast.walk(pohon) if isinstance(n, ast.Name)}

        assert "SAMPEL_KALIBRASI" in nama

    def test_setelan_upkeep_ikut_besar(self) -> None:
        """Fase harian memakai `review_limit`; kalau ia tetap 500, pengukuran
        yang tersimpan tetap tipis meski yang diterapkan sudah tebal."""
        from aruna.core.config import UpkeepSettings

        assert UpkeepSettings().review_limit >= 5000
