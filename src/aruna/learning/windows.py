"""Membandingkan kinerja lintas jendela waktu (PASAL 11.20).

Pasal ini meminta empat jendela - hari ini, tujuh hari, tiga puluh hari,
sepanjang waktu - dan sembilan dimensi di dalamnya: aset terbaik dan terburuk,
timeframe, rezim pasar, rentang signal quality, dan kinerja LONG melawan SHORT.

**Jendelanya bersarang, dan itu mengubah cara membacanya.** Hari ini ada di
dalam tujuh hari, yang ada di dalam tiga puluh hari, yang ada di dalam
sepanjang waktu. Jadi "hari ini 80%" dan "tiga puluh hari 50%" bukan dua
pengukuran yang bertentangan - yang pertama ada di dalam yang kedua, dan
biasanya terdiri dari lima observasi melawan dua ratus.

Konsekuensinya harus diterima, bukan disiasati: **jendela "hari ini" hampir
selalu akan melaporkan sampel yang belum cukup.** Itu benar, bukan kerusakan.
Sistem yang mencetak win rate harian dari empat perdagangan sedang menawarkan
angka yang berubah drastis setiap kali satu perdagangan selesai - dan angka
yang bergerak begitu adalah yang paling sering dijadikan dasar keputusan,
justru karena ia terasa hidup.

**Yang membuat perbandingan ini berguna bukan angkanya, tapi selisihnya.**
Aset yang 70% sepanjang waktu dan 40% dalam tiga puluh hari terakhir sedang
memberi tahu sesuatu yang tidak bisa dikatakan angka mana pun sendirian. Tapi
selisih antara dua angka berisik lebih berisik lagi, jadi selisih hanya dihitung
ketika **kedua** sisinya punya cukup sampel.

Ambang sampelnya sama dengan rincian keandalan agent, dan sengaja: sebuah
"aset terbaik" dari tiga perdagangan bukan aset terbaik, dengan alasan yang
persis sama seperti "Trending: 96%" dari lima observasi bukan pengukuran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aruna.learning.breakdown import MIN_CELL_SAMPLE

#: Jendela yang dibandingkan, beserta panjangnya dalam hari.
#: ``None`` berarti sepanjang waktu.
WINDOWS: tuple[tuple[str, int | None], ...] = (
    ("today", 1),
    ("7d", 7),
    ("30d", 30),
    ("all", None),
)

#: Batas rentang signal quality. Lima ember, bukan sepuluh: sepuluh membelah
#: sampel dua kali lebih tipis untuk menjawab pertanyaan yang sama.
QUALITY_BANDS: tuple[tuple[str, int, int], ...] = (
    ("0-59", 0, 59),
    ("60-69", 60, 69),
    ("70-79", 70, 79),
    ("80-89", 80, 89),
    ("90-100", 90, 100),
)


def quality_band(score: Any) -> str:
    """Ember untuk satu skor kualitas, atau ``UNKNOWN`` kalau tidak ada."""
    if score is None:
        return "UNKNOWN"
    try:
        value = int(score)
    except (TypeError, ValueError):
        return "UNKNOWN"
    for label, low, high in QUALITY_BANDS:
        if low <= value <= high:
            return label
    return "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Cell:
    """Satu nilai dimensi dalam satu jendela."""

    key: str
    wins: int = 0
    losses: int = 0
    #: Posisi yang belum selesai. Informasi saja, tidak masuk win rate
    #: (PASAL 3 spec Daily Report, dan alasan yang sama berlaku di sini).
    active: int = 0

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    @property
    def sufficient(self) -> bool:
        return self.decided >= MIN_CELL_SAMPLE

    @property
    def win_rate(self) -> float | None:
        """``None`` sampai selnya punya cukup posisi yang selesai."""
        if not self.sufficient or self.decided == 0:
            return None
        return self.wins / self.decided

    @property
    def needs(self) -> int:
        return max(0, MIN_CELL_SAMPLE - self.decided)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "wins": self.wins,
            "losses": self.losses,
            "active": self.active,
            "decided": self.decided,
            "win_rate": None if self.win_rate is None else round(self.win_rate, 4),
            "status": "MEASURED" if self.sufficient else "INSUFFICIENT_SAMPLE",
            "needs": self.needs,
        }


@dataclass(frozen=True, slots=True)
class WindowReport:
    """Satu dimensi dalam satu jendela."""

    window: str
    dimension: str
    cells: tuple[Cell, ...] = field(default_factory=tuple)

    @property
    def measured(self) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.sufficient)

    def get(self, key: str) -> Cell | None:
        return next((c for c in self.cells if c.key == key), None)

    def best(self) -> Cell | None:
        """Terbaik di antara yang **terukur** saja.

        Sel tipis tidak ikut diperingkat. Papan peringkat yang memasukkannya
        selalu dimenangkan sel dengan data paling sedikit - di situlah seratus
        persen paling mudah terjadi.
        """
        return max(self.measured, key=lambda c: c.win_rate or 0.0, default=None)

    def worst(self) -> Cell | None:
        return min(self.measured, key=lambda c: c.win_rate or 0.0, default=None)

    def to_dict(self) -> dict[str, Any]:
        best, worst = self.best(), self.worst()
        return {
            "window": self.window,
            "dimension": self.dimension,
            "cells": [c.to_dict() for c in self.cells],
            "measured": len(self.measured),
            "best": best.key if best else None,
            "worst": worst.key if worst else None,
        }


def build_window(rows: Any, *, dimension: str, window: str) -> WindowReport:
    """Kumpulkan baris ``(key, result)`` menjadi sel.

    ``result`` yang bukan ``WIN`` maupun ``LOSS`` dihitung sebagai ``active``
    dan **tidak** masuk penyebut. Memasukkan posisi yang belum selesai ke salah
    satu sisi adalah cara termudah membuat angka terlihat lebih baik daripada
    kenyataannya.
    """
    tallies: dict[str, list[int]] = {}
    for row in rows or ():
        key = str(row.get("key") or "UNKNOWN")
        tally = tallies.setdefault(key, [0, 0, 0])
        hasil = str(row.get("result") or "").upper()
        if hasil == "WIN":
            tally[0] += 1
        elif hasil == "LOSS":
            tally[1] += 1
        else:
            tally[2] += 1

    cells = tuple(
        Cell(key=key, wins=w, losses=lo, active=a)
        for key, (w, lo, a) in sorted(tallies.items())
    )
    return WindowReport(window=window, dimension=dimension, cells=cells)


@dataclass(frozen=True, slots=True)
class Shift:
    """Perubahan satu sel antara dua jendela."""

    key: str
    recent: float
    baseline: float
    recent_n: int
    baseline_n: int

    @property
    def delta(self) -> float:
        return round(self.recent - self.baseline, 4)

    def summary(self) -> str:
        arah = "naik" if self.delta > 0 else "turun"
        return (
            f"{self.key}: {self.baseline:.0%} -> {self.recent:.0%} "
            f"({arah} {abs(self.delta):.0%}) - "
            f"{self.recent_n} vs {self.baseline_n} posisi selesai"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "recent": round(self.recent, 4),
            "baseline": round(self.baseline, 4),
            "delta": self.delta,
            "recent_n": self.recent_n,
            "baseline_n": self.baseline_n,
            "summary": self.summary(),
        }


#: Selisih di bawah ini tidak dilaporkan sebagai pergeseran.
MIN_SHIFT = 0.10


def shifts(
    recent: WindowReport, baseline: WindowReport, *, min_shift: float = MIN_SHIFT
) -> tuple[Shift, ...]:
    """Sel yang kinerjanya bergeser antara dua jendela (PASAL 11.20).

    Dihitung **hanya** ketika kedua sisinya punya cukup sampel. Selisih antara
    dua angka berisik lebih berisik daripada masing-masingnya, dan sebuah
    "turun tiga puluh persen" yang lahir dari lima observasi melawan dua ratus
    akan terbaca sebagai temuan padahal ia kebisingan.

    Ingat jendelanya bersarang: ``recent`` ada di DALAM ``baseline``, jadi
    selisihnya meremehkan perubahan sesungguhnya - periode terakhir ikut
    menarik garis dasarnya sendiri. Itu arah yang aman: ia membuat pergeseran
    terlihat lebih kecil, bukan lebih besar.
    """
    out: list[Shift] = []
    for cell in recent.measured:
        lawan = baseline.get(cell.key)
        if lawan is None:
            continue
        # Satu penjaga, bukan dua. Versi pertama juga memeriksa
        # `lawan.sufficient` - dan itu tumpang tindih penuh, karena sel yang
        # sampelnya kurang selalu mengembalikan win_rate None. Dua pemeriksaan
        # yang tidak bisa dibedakan satu kasus pun berarti salah satunya tidak
        # pernah diuji, dan yang tidak diuji bebas menjadi salah diam-diam.
        a, b = cell.win_rate, lawan.win_rate
        if a is None or b is None or abs(a - b) < min_shift:
            continue
        out.append(Shift(
            key=cell.key, recent=a, baseline=b,
            recent_n=cell.decided, baseline_n=lawan.decided,
        ))
    return tuple(sorted(out, key=lambda s: -abs(s.delta)))


__all__ = [
    "MIN_SHIFT",
    "QUALITY_BANDS",
    "WINDOWS",
    "Cell",
    "Shift",
    "WindowReport",
    "build_window",
    "quality_band",
    "shifts",
]
