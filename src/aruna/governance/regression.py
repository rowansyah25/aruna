"""Model baru tidak boleh membeli satu angka dengan angka lain (PASAL 12.11).

Contoh dari spec-nya sudah cukup untuk menjelaskan seluruh modul ini:

    Win Rate:  +4%
    Drawdown: +20%
    Regression: FAILED

Sebuah model yang menaikkan win rate sambil melipatgandakan drawdown-nya bukan
model yang lebih baik; ia model yang mengambil risiko lebih besar, dan risiko
yang lebih besar menaikkan win rate secara gratis sampai hari ia tidak.

**Kenapa penjaga ini harus terpisah dari perbandingan biasa.** Perbandingan
menjawab "mana yang lebih baik" dan selalu punya jawaban - salah satu pasti
lebih tinggi. Penjaga ini menjawab pertanyaan berbeda: "apakah ada yang RUSAK".
Keduanya bisa berjawab "model baru" dan "ya" sekaligus, dan sistem yang hanya
menanyakan yang pertama tidak akan pernah tahu.

**Arah tiap metrik ditulis, tidak ditebak.** Naiknya win rate itu baik;
naiknya drawdown itu buruk. Sebuah penjaga yang menyimpulkan arah dari nama
kolom akan sesekali salah, dan kesalahannya adalah mempromosikan model yang
lebih buruk dengan laporan yang mengatakan lebih baik.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Direction(StrEnum):
    """Ke arah mana sebuah metrik dianggap membaik."""

    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"


@dataclass(frozen=True, slots=True)
class Metric:
    """Satu metrik yang ikut menentukan promosi."""

    name: str
    direction: Direction
    #: Pemburukan yang masih boleh terjadi tanpa menggagalkan promosi.
    #:
    #: Nol untuk metrik yang tidak boleh mundur sama sekali. Tidak nol untuk
    #: metrik yang selalu bergetar sedikit antar pengukuran - menuntut nol pada
    #: angka yang berisik berarti tidak ada model yang akan pernah lulus, dan
    #: penjaga yang tidak pernah meloloskan apa pun akan dimatikan orang.
    tolerance: float = 0.0
    #: Metrik yang kerusakannya menggagalkan promosi walau metrik lain membaik.
    critical: bool = True

    def worsened_by(self, before: float, after: float) -> float:
        """Seberapa jauh metrik ini memburuk. Nol atau negatif berarti membaik."""
        if self.direction is Direction.HIGHER_IS_BETTER:
            return before - after
        return after - before


#: Metrik bawaan yang diperiksa pada tiap promosi.
#:
#: ``max_drawdown`` dan ``calibration_error`` ada di sini karena keduanya
#: adalah cara paling umum sebuah model terlihat membaik sambil memburuk:
#: yang pertama dibayar dengan risiko, yang kedua dengan keyakinan yang tidak
#: ditopang hasil (PASAL 12.18).
DEFAULT_METRICS: tuple[Metric, ...] = (
    Metric("win_rate", Direction.HIGHER_IS_BETTER, tolerance=0.0),
    Metric("net_pnl", Direction.HIGHER_IS_BETTER, tolerance=0.0),
    Metric("max_drawdown", Direction.LOWER_IS_BETTER, tolerance=0.02),
    Metric("calibration_error", Direction.LOWER_IS_BETTER, tolerance=0.02),
    Metric(
        "out_of_sample_win_rate", Direction.HIGHER_IS_BETTER, tolerance=0.0
    ),
    # Sample size bukan prestasi, tapi menyusutnya berarti angka-angka di atas
    # diukur dari lebih sedikit bukti - dan itu pemburukan yang tidak terlihat
    # di satu pun metrik lain.
    Metric("sample_size", Direction.HIGHER_IS_BETTER, tolerance=0.0,
           critical=False),
)


@dataclass(frozen=True, slots=True)
class MetricChange:
    name: str
    before: float
    after: float
    worsened: float
    tolerated: bool
    critical: bool

    @property
    def broke(self) -> bool:
        return self.worsened > 0 and not self.tolerated

    def line(self) -> str:
        arah = "+" if self.after >= self.before else ""
        selisih = self.after - self.before
        vonis = ""
        if self.broke:
            vonis = "  <- RUSAK" if self.critical else "  <- mundur"
        elif self.worsened > 0:
            vonis = "  (masih dalam toleransi)"
        return f"{self.name}: {self.before:.4g} -> {self.after:.4g} ({arah}{selisih:.4g}){vonis}"


@dataclass(frozen=True, slots=True)
class RegressionReport:
    """Hasil satu pemeriksaan regresi."""

    changes: tuple[MetricChange, ...] = field(default_factory=tuple)
    #: Metrik yang diminta diperiksa tapi tidak ada angkanya di salah satu sisi.
    #:
    #: Dilaporkan, tidak dianggap lulus. Sebuah metrik yang hilang adalah
    #: metrik yang tidak diukur, dan memperlakukan "tidak diukur" sebagai
    #: "tidak memburuk" adalah cara paling mudah melewati penjaga ini
    #: (PASAL 4: tidak diukur bukan nol).
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def broken(self) -> tuple[MetricChange, ...]:
        return tuple(c for c in self.changes if c.broke and c.critical)

    @property
    def passed(self) -> bool:
        """Lulus hanya kalau tidak ada yang rusak DAN tidak ada yang hilang."""
        return not self.broken and not self.missing

    @property
    def verdict(self) -> str:
        return "PASSED" if self.passed else "FAILED"

    @property
    def recommendation(self) -> str:
        return "PROMOTE" if self.passed else "DO NOT PROMOTE"

    def report(self) -> str:
        baris = [f"REGRESSION: {self.verdict}", ""]
        baris += [f"  {c.line()}" for c in self.changes]
        if self.missing:
            baris += [
                "",
                "  tidak terukur, jadi tidak bisa dinyatakan aman:",
                *(f"    {n}" for n in self.missing),
            ]
        if self.broken:
            baris += [
                "",
                "  yang rusak:",
                *(f"    {c.name}" for c in self.broken),
            ]
        baris += ["", f"  {self.recommendation}"]
        return "\n".join(baris)


def check(
    champion: Mapping[str, float | None],
    challenger: Mapping[str, float | None],
    *,
    metrics: Iterable[Metric] = DEFAULT_METRICS,
) -> RegressionReport:
    """Bandingkan CHAMPION dengan CHALLENGER pada tiap metrik.

    Menerima ``None`` sebagai "tidak terukur" dan melaporkannya sebagai hilang,
    bukan sebagai nol. Selisih terhadap nol yang dikarang akan menghasilkan
    pemburukan atau perbaikan raksasa yang tidak berarti apa-apa.
    """
    perubahan: list[MetricChange] = []
    hilang: list[str] = []

    for m in metrics:
        sebelum = champion.get(m.name)
        sesudah = challenger.get(m.name)
        if sebelum is None or sesudah is None:
            hilang.append(m.name)
            continue
        memburuk = m.worsened_by(float(sebelum), float(sesudah))
        perubahan.append(
            MetricChange(
                name=m.name,
                before=float(sebelum),
                after=float(sesudah),
                worsened=memburuk,
                tolerated=memburuk <= m.tolerance,
                critical=m.critical,
            )
        )

    return RegressionReport(
        changes=tuple(perubahan), missing=tuple(hilang)
    )


__all__ = [
    "DEFAULT_METRICS",
    "Direction",
    "Metric",
    "MetricChange",
    "RegressionReport",
    "check",
]
