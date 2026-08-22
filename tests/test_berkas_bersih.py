"""Berkas sumber yang bisa dibaca alat, bukan cuma bisa dijalankan Python.

Lahir dari kegagalan nyata, 2026-08-22: ``src/aruna/scenario/__init__.py``
tertulis dengan BOM UTF-8, dan seluruh suite tetap hijau **kecuali satu test SQL
yang gagal dengan pesan tentang karakter tak tercetak**. Bentuk kegagalannya
yang jadi masalah, bukan kegagalannya:

* Python mengimpornya tanpa keluhan - mesin impor mengenali BOM dan
  membuangnya, jadi tidak ada satu pun test perilaku yang merah.
* :func:`ast.parse` atas teks yang sama menolaknya. Tiap penjaga berbasis AST
  di repo ini - dan Phase 16 sendiri menambah enam - membaca berkas lewat
  ``read_text`` lalu ``ast.parse``, jadi semuanya rapuh terhadap satu byte yang
  tak terlihat di editor mana pun.
* Yang akhirnya berteriak adalah ``test_sql_hygiene``, yang tidak ada
  hubungannya dengan encoding. Pembaca berikutnya akan mencari kesalahan SQL.

Penjaga ini menamai masalahnya langsung, dan mencakup ``tests/`` juga - dua
berkas test di sana ternyata ber-BOM sejak lama tanpa satu pun penjaga
menyentuhnya, karena ``test_sql_hygiene`` hanya memindai ``src/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

AKAR = Path(__file__).resolve().parent.parent
SRC = AKAR / "src"
TESTS = AKAR / "tests"

BOM = b"\xef\xbb\xbf"


def _berkas() -> list[Path]:
    return sorted(SRC.rglob("*.py")) + sorted(TESTS.rglob("*.py"))


def test_ada_berkas_yang_dipindai() -> None:
    """Penjaga yang berjalan atas nol berkas lulus tanpa memeriksa apa pun -
    bentuk kegagalan yang paling sulit terlihat."""
    assert len(_berkas()) > 50


def test_tidak_ada_bom() -> None:
    kena = [
        str(b.relative_to(AKAR))
        for b in _berkas()
        if b.read_bytes().startswith(BOM)
    ]

    assert not kena, (
        "BOM UTF-8 di awal berkas. Python mengimpornya diam-diam, tapi "
        "`ast.parse` menolaknya - jadi tiap penjaga berbasis AST di repo ini "
        "patah tanpa satu pun test perilaku merah. Penyebab paling sering: "
        "berkas ditulis lewat PowerShell `Set-Content`/`Out-File`, yang "
        "menambahkan BOM secara bawaan.\n  " + "\n  ".join(kena)
    )


def test_semua_berkas_bisa_di_ast_parse() -> None:
    """Yang sebenarnya dituntut. BOM cuma satu cara melanggarnya - berkas
    dengan encoding campur atau baris nol byte melanggarnya juga, dan
    keduanya gagal dengan gejala yang sama membingungkannya."""
    gagal: list[str] = []
    for b in _berkas():
        try:
            ast.parse(b.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as galat:
            gagal.append(f"{b.relative_to(AKAR)}: {type(galat).__name__}: {galat}")

    assert not gagal, "\n  ".join(gagal)


class TestPenjaganyaMenggigit:
    """Penjaga yang tidak pernah dibuktikan menolak apa pun adalah penjaga yang
    lulus atas apa saja. Dibuktikan di berkas sementara, bukan dengan mengotori
    pohon kerja."""

    def test_bom_terdeteksi(self, tmp_path) -> None:
        p = tmp_path / "contoh.py"
        p.write_bytes(BOM + b'"""docstring."""\n')

        assert p.read_bytes().startswith(BOM)

    def test_ast_parse_memang_menolak_bom(self, tmp_path) -> None:
        """Ini yang membuat BOM berbahaya sekaligus tak terlihat: `import`
        menerimanya, `ast.parse` tidak."""
        p = tmp_path / "contoh.py"
        p.write_bytes(BOM + b"x = 1\n")

        with pytest.raises(SyntaxError):
            ast.parse(p.read_text(encoding="utf-8"))

    def test_tanpa_bom_lolos(self, tmp_path) -> None:
        p = tmp_path / "contoh.py"
        p.write_bytes(b"x = 1\n")

        ast.parse(p.read_text(encoding="utf-8"))
        assert not p.read_bytes().startswith(BOM)
