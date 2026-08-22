"""Keandalan agent, dirinci per rezim, timeframe, dan aset (PASAL 11.2).

Keandalan keseluruhan sudah diukur sejak SPEC 30 dan bekerja. Yang diminta
PASAL 11.2 dan belum ada adalah rinciannya - contoh yang operator tulis
sendiri berbunyi begini:

    Agent 1
    Overall:         91%
    Trending:        96%
    Sideways:        73%
    High Volatility: 88%

Rincian itu berguna justru karena bisa berbeda tajam: agent yang hebat di
pasar bertren dan buruk di pasar menyamping adalah agent yang bobotnya
seharusnya bergantung pada rezim, dan angka keseluruhan 91% menyembunyikan
persis fakta itu.

**Dan justru di situ letak bahayanya, karena ambang sampel ikut terbelah.**

Seorang agent dengan dua puluh lima opini terskor - cukup untuk dinilai
keseluruhan - yang tersebar di lima rezim punya lima observasi per rezim.
"Trending: 96%" dari lima observasi bukan pengukuran; ia satu lemparan koin
yang kebetulan jatuh bagus, dicetak dengan dua desimal supaya terlihat seperti
hasil penelitian. Membelah sampel selalu memperbesar godaan itu, karena
angkanya jadi lebih ekstrem justru saat datanya jadi lebih tipis.

Karena itu ambang sampel di sini berlaku **per sel**, bukan sekali di tingkat
agent. Sel yang belum cukup dilaporkan sebagai ``INSUFFICIENT_SAMPLE`` dengan
berapa lagi yang dibutuhkan - bukan dihilangkan, karena sel yang hilang
terbaca sebagai "tidak ada masalah di sana".

**Hanya opini berarah yang dinilai.** Agent yang abstain tidak menyatakan apa
pun, dan agent yang bilang tidak-ada-posisi tidak bisa benar atau salah
terhadap pergerakan harga - keduanya bukan tebakan yang meleset, dan
menghitungnya akan menghukum sikap menahan diri.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Observasi minimum per sel sebelum akurasinya boleh disebut.
#:
#: Lebih rendah daripada ambang keseluruhan (25) dengan sengaja: menuntut dua
#: puluh lima observasi PER rezim PER agent berarti rincian ini tidak akan
#: pernah melaporkan apa pun. Lima belas cukup untuk menahan satu-dua kebetulan
#: dan masih bisa dicapai - tapi ia tetap ambang, dan sel di bawahnya tetap
#: diam soal akurasinya.
MIN_CELL_SAMPLE = 15


@dataclass(frozen=True, slots=True)
class Cell:
    """Satu agent pada satu nilai dimensi."""

    agent: str
    key: str
    votes: int = 0
    correct: int = 0

    @property
    def sufficient(self) -> bool:
        return self.votes >= MIN_CELL_SAMPLE

    @property
    def accuracy(self) -> float | None:
        """``None`` sampai selnya punya cukup observasi.

        Bukan nol, dan bukan angka mentah yang diberi tanda bintang. Sebuah
        akurasi yang dicetak akan dibaca dan diingat, apa pun peringatan di
        sebelahnya; satu-satunya cara jujur menyampaikan "belum tahu" adalah
        tidak menyebutkan angkanya.
        """
        if not self.sufficient or self.votes == 0:
            return None
        return self.correct / self.votes

    @property
    def needs(self) -> int:
        return max(0, MIN_CELL_SAMPLE - self.votes)

    @property
    def status(self) -> str:
        return "MEASURED" if self.sufficient else "INSUFFICIENT_SAMPLE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "key": self.key,
            "votes": self.votes,
            "correct": self.correct,
            "accuracy": None if self.accuracy is None else round(self.accuracy, 4),
            "status": self.status,
            "needs": self.needs,
        }


@dataclass(frozen=True, slots=True)
class Breakdown:
    """Satu dimensi - rezim, timeframe, atau aset - untuk semua agent."""

    dimension: str
    cells: tuple[Cell, ...] = field(default_factory=tuple)

    @property
    def measured(self) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.sufficient)

    def for_agent(self, agent: str) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.agent == agent)

    def best(self) -> Cell | None:
        """Sel terbaik di antara yang **terukur**.

        Sel yang belum cukup sampel tidak ikut diperingkat, betapapun tinggi
        angka mentahnya. Papan peringkat yang memasukkannya akan selalu
        dimenangkan oleh sel yang datanya paling sedikit - karena di situlah
        seratus persen paling mudah terjadi.
        """
        return max(self.measured, key=lambda c: c.accuracy or 0.0, default=None)

    def worst(self) -> Cell | None:
        return min(self.measured, key=lambda c: c.accuracy or 0.0, default=None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "cells": [c.to_dict() for c in self.cells],
            "measured": len(self.measured),
            "total": len(self.cells),
            "note": (
                f"akurasi disebut hanya untuk sel dengan minimal "
                f"{MIN_CELL_SAMPLE} observasi"
            ),
        }


def build_breakdown(rows: Any, *, dimension: str) -> Breakdown:
    """Kumpulkan baris ``(agent, key, correct)`` menjadi sel.

    Setiap baris adalah satu opini **berarah** seorang agent dalam satu sesi
    yang sudah diskor. ``correct`` adalah apakah agent itu benar tentang arah
    pasar - bukan apakah ia sepakat dengan council. Seorang agent yang
    menentang council dan ternyata benar harus tercatat benar; menilainya dari
    kesepakatan akan mengukur kepatuhan, bukan keandalan.
    """
    tallies: dict[tuple[str, str], list[int]] = {}
    for row in rows or ():
        agent = str(row.get("agent") or "?")
        key = str(row.get("key") or "UNKNOWN")
        tally = tallies.setdefault((agent, key), [0, 0])
        tally[0] += 1
        if row.get("correct"):
            tally[1] += 1

    cells = tuple(
        Cell(agent=agent, key=key, votes=votes, correct=correct)
        for (agent, key), (votes, correct) in sorted(tallies.items())
    )
    return Breakdown(dimension=dimension, cells=cells)


__all__ = ["MIN_CELL_SAMPLE", "Breakdown", "Cell", "build_breakdown"]
