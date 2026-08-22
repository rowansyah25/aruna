"""Apakah diamnya ARUNA itu benar? (PASAL 14.32, 14.33)

Sistem yang hanya menilai signal yang dikirimnya menilai separuh dari apa yang
dilakukannya. ARUNA memutuskan NO SIGNAL jauh lebih sering daripada LONG atau
SHORT - terukur pada tick dua puluh simbol: lima belas tanpa arah, lima ditolak
karena imbalannya lebih kecil dari risikonya - dan tidak satupun dari lima belas
itu pernah dinilai benar atau salah.

Diam yang tidak pernah dinilai adalah tempat paling nyaman untuk sebuah sistem
bersembunyi. Ia tidak pernah kalah, karena ia tidak pernah tercatat bertaruh.

**Dua jenis diam, dan hanya satu yang benar:**

* pasar memang tidak bergerak - diam itu tepat;
* pasar bergerak jauh ke satu arah - diam itu **kesempatan yang hilang**.

PASAL 14.33 memberi peringatan yang harus dibaca bersama modul ini: *"jangan
langsung menurunkan threshold hanya untuk mengejar missed opportunity."*
Sebuah sistem yang mengejar setiap gerakan yang terlewat akan berhenti diam
sama sekali, dan berhenti diam adalah cara tercepat mengubah analis menjadi
mesin yang selalu punya pendapat.

Jadi modul ini **mengukur**, dan berhenti di situ. Ia tidak menyarankan ambang
baru, dan tidak punya jalur untuk mengubah satu pun.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from aruna.learning.evidence import Evidence

#: Gerak yang dianggap "layak diambil", sebagai persen.
#:
#: Dua persen dalam satu horizon. Angkanya harus melebihi ongkos bolak-balik -
#: terukur di sistem ini sekitar 3,67 pada rencana futures tipikal, yang pada
#: harga menengah berada di bawah satu persen - supaya sebuah "kesempatan"
#: benar-benar menyisakan sesuatu sesudah biaya.
#:
#: Di bawah ambang ini, gerak apa pun bukan kesempatan yang hilang; ia derau
#: yang kebetulan searah.
GERAK_BERARTI_PCT = Decimal("2.0")


class Vonis(StrEnum):
    """Nasib satu keputusan NO SIGNAL."""

    #: Pasar tidak menawarkan apa-apa. Diam itu tepat.
    CORRECT = "NO SIGNAL CORRECT"
    #: Pasar bergerak jauh. Diam itu kehilangan.
    MISSED = "MISSED OPPORTUNITY"
    #: Belum bisa dinilai - horizonnya belum lewat, atau harganya tidak ada.
    UNKNOWN = "BELUM BISA DINILAI"


@dataclass(frozen=True, slots=True)
class Diam:
    """Satu keputusan NO SIGNAL beserta apa yang terjadi sesudahnya."""

    symbol: str
    reason: str
    #: Gerak terjauh dari harga acuan selama horizon, sebagai persen bertanda.
    #: Positif berarti naik. ``None`` berarti belum terukur.
    move_pct: Decimal | None = None

    @property
    def verdict(self) -> Vonis:
        if self.move_pct is None:
            return Vonis.UNKNOWN
        return (
            Vonis.MISSED
            if abs(self.move_pct) >= GERAK_BERARTI_PCT
            else Vonis.CORRECT
        )

    @property
    def missed_direction(self) -> str | None:
        """Arah yang terlewat, kalau memang terlewat.

        Berguna untuk pertanyaan yang berbeda dari 'berapa banyak': sebuah
        sistem yang melewatkan hampir semua gerakan NAIK punya masalah yang
        berbeda dari yang melewatkan keduanya sama rata.
        """
        if self.verdict is not Vonis.MISSED or self.move_pct is None:
            return None
        return "LONG" if self.move_pct > 0 else "SHORT"

    def line(self) -> str:
        if self.move_pct is None:
            return f"{self.symbol}: {Vonis.UNKNOWN.value}"
        return (
            f"{self.symbol}: {self.verdict.value} "
            f"({self.move_pct:+.2f}%) - {self.reason}"
        )


@dataclass(frozen=True, slots=True)
class Laporan:
    """Seberapa sering diamnya ARUNA benar."""

    evidence: Evidence = field(default_factory=lambda: Evidence(0, 0))
    unknown: int = 0
    #: Kesempatan yang terlewat, terurut dari gerak terbesar.
    missed: tuple[Diam, ...] = field(default_factory=tuple)
    #: Alasan yang paling sering menyertai kesempatan yang terlewat.
    #: Ini yang PASAL 14.33 minta dicari sebabnya - bukan angka totalnya.
    reasons: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    @property
    def accuracy(self) -> float | None:
        """Bagian NO SIGNAL yang ternyata benar. None kalau belum ada."""
        return self.evidence.win_rate

    def summary(self) -> str:
        if self.evidence.total == 0:
            return f"belum ada NO SIGNAL yang bisa dinilai ({self.unknown} menunggu)"
        inti = f"diam benar {self.evidence.label(noun='benar')}"
        if not self.evidence.conclusive:
            return inti
        if self.missed:
            return f"{inti}; {len(self.missed)} kesempatan terlewat"
        return inti

    def report(self) -> list[str]:
        baris = ["⚪ AKURASI NO SIGNAL", "", f"  {self.summary()}"]
        if self.unknown:
            baris.append(f"  {self.unknown} belum bisa dinilai (horizon belum lewat)")
        if self.missed:
            baris += ["", "  Kesempatan yang terlewat:"]
            baris += [f"    {d.line()}" for d in self.missed[:5]]
            sisa = len(self.missed) - 5
            if sisa > 0:
                baris.append(f"    (+{sisa} lagi)")
        if self.reasons:
            baris += ["", "  Alasan diam yang paling sering melewatkan gerak:"]
            baris += [f"    {n}x  {a}" for a, n in self.reasons[:4]]
        baris += [
            "",
            "  PASAL 14.33: ini pengukuran, BUKAN izin menurunkan ambang.",
            "  Sistem yang mengejar setiap gerak yang terlewat berhenti diam,",
            "  dan berhenti diam mengubah analis menjadi mesin berpendapat.",
        ]
        return baris


def evaluate(decisions: Iterable[Diam]) -> Laporan:
    """Nilai sekumpulan keputusan NO SIGNAL.

    Yang belum bisa dinilai TIDAK dihitung benar. Ia dipisahkan dan
    dilaporkan - memasukkannya ke penyebut membuat akurasi naik setiap kali
    ARUNA menambah keputusan yang horizonnya belum lewat, yang berarti angka
    itu membaik hanya karena waktu berjalan.
    """
    semua = list(decisions)
    benar = [d for d in semua if d.verdict is Vonis.CORRECT]
    terlewat = [d for d in semua if d.verdict is Vonis.MISSED]
    belum = sum(1 for d in semua if d.verdict is Vonis.UNKNOWN)

    hitung: dict[str, int] = {}
    for d in terlewat:
        hitung[d.reason] = hitung.get(d.reason, 0) + 1

    return Laporan(
        evidence=Evidence(wins=len(benar), losses=len(terlewat)),
        unknown=belum,
        missed=tuple(
            sorted(terlewat, key=lambda d: -abs(d.move_pct or Decimal(0)))
        ),
        reasons=tuple(sorted(hitung.items(), key=lambda kv: -kv[1])),
    )


__all__ = ["GERAK_BERARTI_PCT", "Diam", "Laporan", "Vonis", "evaluate"]
