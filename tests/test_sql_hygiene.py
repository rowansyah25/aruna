"""Pola SQL yang harus tidak pernah kembali masuk.

Ini pemeriksaan atas teks sumber, dan biasanya itu bentuk test yang lemah -
mencocokkan potongan kata tidak bisa membedakan "dipanggil" dari "sekadar
ditulis". Di sini kelemahan itu tidak berlaku, karena cacatnya **memang** ada
di tingkat teks: satu bentuk sintaks yang ditolak MySQL versi mendatang.

Yang dijaga: ``VALUES(kolom)`` di dalam klausa ``ON DUPLICATE KEY UPDATE``.
MySQL 8.0.20 menandainya usang, dan memperingatkan **sekali per baris yang
disentuh**. Tautan berita ditulis ulang setiap siklus ingest untuk setiap item
dikali setiap aset yang disebutnya, jadi satu siklus menghasilkan ratusan baris
peringatan yang identik.

Kerugian sesungguhnya bukan berisiknya, melainkan apa yang dikubur oleh berisik
itu. Log yang penuh satu peringatan yang sama membuat operator berhenti
membacanya, dan peringatan yang tidak dibaca sama saja dengan tidak ada. Itu
kegagalan yang persis sama seperti alert kesehatan yang menyala tiap menit.

Penggantinya: alias baris. Untuk ``INSERT ... VALUES`` bentuknya
``VALUES (...) AS baru``; untuk ``INSERT ... SELECT`` tidak ada klausa VALUES
yang bisa dialiaskan, jadi SELECT-nya dibungkus jadi derived table dan tabel
itu yang diberi alias.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

#: ``VALUES(kolom)`` - fungsi yang usang. Sengaja tidak cocok dengan
#: ``VALUES (%s, %s)`` biasa, yang merupakan klausa VALUES dan tetap sah.
FUNGSI_VALUES = re.compile(r"\bVALUES\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)")


def _berkas_python() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _baris_prosa(sumber: str) -> set[int]:
    """Nomor baris yang berisi docstring atau komentar, bukan kode.

    Versi pertama pemindai ini menandai docstring di ``news.py`` yang justru
    **menjelaskan** kenapa bentuk usang itu dihindari. Menghapus penjelasannya
    supaya pemindai diam adalah arah yang terbalik: yang tersisa adalah SQL
    tanpa alasan, dan orang berikutnya menulis ulang bentuk lamanya karena
    tidak ada yang memberi tahu kenapa tidak boleh.

    Docstring dikenali lewat ``ast`` - bukan "string bertanda kutip tiga",
    karena SQL di berkas ini juga ditulis begitu. Yang membedakan docstring
    adalah posisinya sebagai pernyataan pertama sebuah modul, kelas, atau
    fungsi, dan hanya ``ast`` yang tahu itu.
    """
    baris: set[int] = set()

    pohon = ast.parse(sumber)
    simpul = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(pohon):
        if not isinstance(node, simpul):
            continue
        badan = getattr(node, "body", None)
        if not badan:
            continue
        awal = badan[0]
        if isinstance(awal, ast.Expr) and isinstance(awal.value, ast.Constant) \
                and isinstance(awal.value.value, str):
            baris.update(range(awal.lineno, (awal.end_lineno or awal.lineno) + 1))

    for tok in tokenize.generate_tokens(io.StringIO(sumber).readline):
        if tok.type == tokenize.COMMENT:
            baris.add(tok.start[0])

    return baris


def test_ada_yang_dipindai() -> None:
    """Penjaga untuk penjaganya sendiri.

    Kalau pola direktorinya salah, ``rglob`` mengembalikan kosong dan semua
    test di berkas ini lulus tanpa memeriksa apa pun.
    """
    berkas = _berkas_python()
    assert len(berkas) > 50, f"hanya {len(berkas)} berkas terpindai - path salah?"
    assert any("repositories" in str(b) for b in berkas)


def _temuan() -> list[str]:
    hasil: list[str] = []
    for berkas in _berkas_python():
        sumber = berkas.read_text(encoding="utf-8")
        prosa = _baris_prosa(sumber)
        hasil += [
            f"{berkas.relative_to(SRC)}:{n}: {baris.strip()}"
            for n, baris in enumerate(sumber.splitlines(), 1)
            if n not in prosa and FUNGSI_VALUES.search(baris)
        ]
    return hasil


def test_prosa_tidak_ikut_dipindai() -> None:
    """Docstring yang menjelaskan bentuk usang bukan pelanggaran."""
    sumber = 'def f():\n    """pakai VALUES(symbol) itu usang."""\n    return 1\n'
    assert _baris_prosa(sumber) == {2}

    kode = 'SQL = """\nON DUPLICATE KEY UPDATE a = VALUES(a)\n"""\n'
    assert 2 not in _baris_prosa(kode), "SQL bukan docstring - harus tetap dipindai"


def test_tidak_ada_fungsi_values_yang_usang() -> None:
    temuan = _temuan()
    assert not temuan, (
        "VALUES(kolom) usang sejak MySQL 8.0.20 dan memperingatkan sekali per "
        "baris. Pakai alias:\n  " + "\n  ".join(temuan)
    )


class TestPolanya:
    """Pola itu sendiri harus membedakan yang salah dari yang benar."""

    def test_menangkap_bentuk_usang(self) -> None:
        assert FUNGSI_VALUES.search("ON DUPLICATE KEY UPDATE symbol = VALUES(symbol)")
        assert FUNGSI_VALUES.search("UPDATE a = VALUES( a ), b = VALUES(b)")

    def test_membiarkan_klausa_values_biasa(self) -> None:
        """``INSERT INTO t VALUES (%s)`` sah dan tidak boleh ikut tertangkap."""
        assert not FUNGSI_VALUES.search("INSERT INTO t (a, b) VALUES (%s, %s)")
        assert not FUNGSI_VALUES.search("VALUES (%s)")

    def test_membiarkan_bentuk_alias(self) -> None:
        assert not FUNGSI_VALUES.search(
            "VALUES (%s, %s) AS baru ON DUPLICATE KEY UPDATE a = baru.a"
        )
