"""Tiap PASAL Phase 14 dan Phase 15 harus bisa dicari di kode.

Bukan formalitas. Sebuah pasal yang mekanismenya ada tapi nomornya tidak
disebut di mana pun **tidak bisa diaudit**: tidak ada yang bisa mem-grep-nya
untuk membuktikan ia dihormati, dan pembaca berikutnya tidak punya cara
menemukan di mana ia dijalankan.

Terukur 2026-08-21 sebelum berkas ini ada: Phase 14 menyebut 39 dari 44 pasal,
Phase 15 menyebut 33 dari 49. Yang hilang bukan yang belum dibangun - 15.29
adalah ``cari``+``bandingkan``, 15.30 adalah ``KonteksHistoris``, 15.35/15.36
adalah CONTRARY/SUPPORTIVE. Semuanya berjalan, dan tidak satu pun bisa
ditemukan lewat nomornya.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

AKAR = Path(__file__).resolve().parents[1]
POLA = re.compile(r"PASAL\s+(\d\d)\.(\d\d?)")


def _disebut() -> dict[int, set[int]]:
    ada: dict[int, set[int]] = {14: set(), 15: set()}
    for folder in ("src", "tests", "migrations"):
        akar = AKAR / folder
        if not akar.exists():
            continue
        for berkas in akar.rglob("*"):
            if berkas.suffix not in {".py", ".sql"}:
                continue
            if "__pycache__" in str(berkas):
                continue
            teks = berkas.read_text(encoding="utf-8", errors="ignore")
            for fase, nomor in POLA.findall(teks):
                f = int(fase)
                if f in ada:
                    ada[f].add(int(nomor))
    return ada


@pytest.fixture(scope="module")
def disebut() -> dict[int, set[int]]:
    return _disebut()


class TestCakupanPasal:
    @pytest.mark.parametrize(("fase", "batas"), [(14, 44), (15, 49)])
    def test_setiap_pasal_bisa_dicari(
        self, disebut: dict[int, set[int]], fase: int, batas: int
    ) -> None:
        hilang = sorted(set(range(1, batas + 1)) - disebut[fase])

        assert not hilang, (
            f"PASAL {fase}.x tanpa jangkar di kode: "
            + ", ".join(f"{fase}.{n}" for n in hilang)
            + " — mekanismenya boleh ada, tapi tanpa nomornya tidak ada yang "
            "bisa membuktikannya"
        )

    def test_tidak_ada_nomor_di_luar_pasalnya(
        self, disebut: dict[int, set[int]]
    ) -> None:
        """Nomor yang tidak ada di SPEC berarti salah ketik - dan salah ketik
        di jangkar audit membuat pasal yang benar terlihat tertutup."""
        assert max(disebut[14]) <= 44
        assert max(disebut[15]) <= 49
