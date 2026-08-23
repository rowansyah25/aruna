"""ARUNA menganalisis, tidak mengeksekusi (bagian 17.1).

**Dijaga AST, bukan pencarian teks**, dan itu bukan kerapian melainkan
keharusan. Kata "order", "posisi", dan "LONG" muncul berkali-kali di dalam
docstring yang justru MENJELASKAN larangannya - pencarian teks sudah tiga kali
tersandung prosanya sendiri di proyek ini, lalu dilonggarkan sampai berhenti
menjaga apa pun.

Yang diperiksa **nama yang didefinisikan dan metode yang dipanggil**, bukan
kata yang muncul. Sebuah komentar yang menyebut `place_order` untuk menerangkan
kenapa ia tidak ada tidak boleh menjatuhkan test ini; sebuah pemanggilan
`broker.place_order()` harus.
"""

from __future__ import annotations

import ast
from pathlib import Path

AKAR = Path(__file__).resolve().parent.parent / "src" / "aruna" / "router"

#: Kosakata yang tidak boleh ada di paket router.
#:
#: Kata kerjanya, bukan kata bendanya. `position` dan `order` sebagai kata
#: benda muncul sah di analisis - "open interest", "urutan bacaan" - dan
#: melarangnya berarti melarang menjelaskan pasar. Yang dilarang perbuatannya.
TERLARANG = frozenset({
    "buy",
    "sell",
    "place_order",
    "submit_order",
    "cancel_order",
    "set_leverage",
    "open_position",
    "close_position",
    "transfer",
    "withdraw",
})


def _berkas() -> list[Path]:
    return sorted(AKAR.rglob("*.py"))


def _nama_dan_panggilan(berkas: Path) -> set[str]:
    pohon = ast.parse(berkas.read_text(encoding="utf-8"))
    keluar: set[str] = set()
    for n in ast.walk(pohon):
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            keluar.add(n.name.lower())
        elif isinstance(n, ast.Call):
            if isinstance(n.func, ast.Attribute):
                keluar.add(n.func.attr.lower())
            elif isinstance(n.func, ast.Name):
                keluar.add(n.func.id.lower())
    return keluar


class TestRouterTidakMengeksekusi:
    def test_tidak_ada_kosakata_eksekusi(self) -> None:
        for berkas in _berkas():
            langgar = _nama_dan_panggilan(berkas) & TERLARANG

            assert not langgar, (
                f"{berkas.name} mendefinisikan atau memanggil {sorted(langgar)}. "
                "ARUNA menganalisis, tidak mengeksekusi (bagian 17.1)."
            )

    def test_penjaganya_benar_benar_punya_berkas_untuk_dijaga(self) -> None:
        """Penjaga yang menyapu direktori kosong hijau selamanya. Kalau paket
        router suatu hari dipindah, test ini yang memberitahu - bukan diam
        sambil terlihat lulus."""
        assert len(_berkas()) >= 5

    def test_penjaganya_membedakan_prosa_dari_kode(self) -> None:
        """**Ini yang membuat penjaga AST layak dipakai.** Docstring di
        `kecocokan.py` dan `putusan.py` memang menyebut kata-kata terlarang
        untuk menerangkan kenapa mereka tidak ada. Pencarian teks akan MERAH
        di situ, lalu dilonggarkan, lalu berhenti menjaga apa pun.

        Test ini membuktikan bedanya terbaca: sebuah modul yang menyebut
        `place_order` di dalam prosa tetap lolos, dan yang benar-benar
        memanggilnya tidak.
        """
        prosa = ast.parse('"""Tidak ada place_order di sini."""\nx = 1\n')
        kode = ast.parse("broker.place_order()\n")

        def nama(pohon: ast.Module) -> set[str]:
            return {
                n.func.attr.lower()
                for n in ast.walk(pohon)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            }

        assert not nama(prosa) & TERLARANG
        assert nama(kode) & TERLARANG
