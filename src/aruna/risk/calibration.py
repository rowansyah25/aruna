"""Apakah risk score-nya benar? (PASAL 13.28, 13.29)

Sebuah skor risiko yang tidak pernah dibandingkan dengan hasil adalah pendapat
yang memakai angka. Modul ini menjawab satu pertanyaan: **apakah kategori
risiko yang ARUNA berikan benar-benar memisahkan yang menang dari yang kalah?**

    LOW  : win rate 91%
    HIGH : win rate 63%

Kalau urutannya begitu, skornya bekerja. Kalau HIGH menang lebih sering
daripada LOW, ia bukan sekadar meleset - ia terbalik, dan setiap keputusan yang
dipandu olehnya lebih buruk daripada tanpa panduan.

**Sample-nya dijaga gerbang yang sama dengan Phase 12.** PASAL 13.29 menyebutnya
eksplisit: "jangan mengubah risk model hanya berdasarkan satu outcome, gunakan
sample yang cukup". Jadi tiap kategori membawa :class:`~aruna.learning.evidence.
Evidence`-nya sendiri, dan kategori bersample tipis tidak pernah menyandang
kesimpulan.

**Perbandingan tidak boleh melintasi versi bobot.** Skor 30 dari bobot lama dan
30 dari bobot baru adalah dua angka berbeda yang kebetulan tertulis sama;
menggabungkannya ke dalam satu win rate membuat kalibrasi mengukur campuran dua
model.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from aruna.learning.evidence import Evidence
from aruna.risk.score import RiskLevel

#: Urutan kategori dari paling aman ke paling berisiko. Dipakai memeriksa
#: apakah win rate-nya menurun searah - itulah bentuk yang membuktikan skornya
#: berarti.
URUTAN: tuple[RiskLevel, ...] = (
    RiskLevel.VERY_LOW,
    RiskLevel.LOW,
    RiskLevel.MEDIUM,
    RiskLevel.HIGH,
    RiskLevel.VERY_HIGH,
)


@dataclass(frozen=True, slots=True)
class Hasil:
    """Satu prediksi risiko beserta hasil perdagangannya."""

    category: RiskLevel
    won: bool
    risk_model_version: str


@dataclass(frozen=True, slots=True)
class Bucket:
    category: RiskLevel
    evidence: Evidence

    def line(self) -> str:
        return f"{self.category.mark} {self.category.value:<10} {self.evidence.label()}"


@dataclass(frozen=True, slots=True)
class Laporan:
    """Kalibrasi risiko untuk satu versi bobot."""

    risk_model_version: str
    buckets: tuple[Bucket, ...] = field(default_factory=tuple)

    @property
    def conclusive(self) -> tuple[Bucket, ...]:
        return tuple(b for b in self.buckets if b.evidence.conclusive)

    @property
    def inverted(self) -> tuple[tuple[RiskLevel, RiskLevel], ...]:
        """Pasangan kategori yang urutannya TERBALIK, dan terbukti terbalik.

        Terbukti berarti selang keduanya tidak bertindihan: dua win rate yang
        rentangnya beririsan belum bisa dibedakan, dan menyebutnya terbalik
        akan mengubah setiap kebisingan menjadi temuan.
        """
        cukup = {b.category: b.evidence for b in self.conclusive}
        salah: list[tuple[RiskLevel, RiskLevel]] = []
        for i, aman in enumerate(URUTAN):
            for bahaya in URUTAN[i + 1:]:
                a, b = cukup.get(aman), cukup.get(bahaya)
                if a is None or b is None:
                    continue
                # Yang lebih aman seharusnya menang LEBIH sering. Terbalik
                # kalau batas ATAS-nya masih di bawah batas BAWAH yang berisiko.
                if a.interval[1] < b.interval[0]:
                    salah.append((aman, bahaya))
        return tuple(salah)

    @property
    def usable(self) -> bool:
        """Cukup kategori bersample memadai untuk mengatakan apa pun."""
        return len(self.conclusive) >= 2

    def summary(self) -> str:
        if not self.usable:
            punya = len(self.conclusive)
            return (
                f"kalibrasi belum bisa dinilai: hanya {punya} kategori "
                "bersample cukup (butuh 2)"
            )
        if self.inverted:
            aman, bahaya = self.inverted[0]
            return (
                f"⚠️ KALIBRASI TERBALIK: {aman.value} menang lebih JARANG "
                f"daripada {bahaya.value}"
            )
        return f"kalibrasi wajar pada {len(self.conclusive)} kategori"

    def report(self) -> list[str]:
        baris = [
            "🛡 KALIBRASI RISIKO",
            "",
            f"Versi bobot: {self.risk_model_version}",
            "",
        ]
        baris += [f"  {b.line()}" for b in self.buckets]
        baris += ["", f"  {self.summary()}"]
        if self.inverted:
            baris += [
                "",
                "  Skor yang terbalik lebih buruk daripada tidak ada skor:",
                "  ia memandu ke arah yang salah dengan percaya diri.",
                "  PASAL 13.29 - ini temuan, bukan izin mengubah bobot.",
            ]
        return baris


def calibrate(results: Iterable[Hasil], *, risk_model_version: str) -> Laporan:
    """Bandingkan kategori risiko dengan hasil sebenarnya.

    Hanya hasil dari ``risk_model_version`` yang ikut. Menggabungkan versi
    berarti mengukur campuran dua model, dan campuran itu tidak menggambarkan
    satu pun dari keduanya.
    """
    ember: dict[RiskLevel, list[bool]] = {}
    for h in results:
        if h.risk_model_version != risk_model_version:
            continue
        ember.setdefault(h.category, []).append(h.won)

    buckets = [
        Bucket(
            category=k,
            evidence=Evidence(
                wins=sum(1 for w in ember[k] if w),
                losses=sum(1 for w in ember[k] if not w),
            ),
        )
        for k in URUTAN
        if k in ember
    ]
    return Laporan(risk_model_version=risk_model_version, buckets=tuple(buckets))


__all__ = ["URUTAN", "Bucket", "Hasil", "Laporan", "calibrate"]
