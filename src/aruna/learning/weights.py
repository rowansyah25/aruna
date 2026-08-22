"""Usulan perubahan bobot agent, dan bobot yang benar-benar berlaku (PASAL 11.11).

Sebelum modul ini ada, bobot agent **menyesuaikan dirinya sendiri**.
``AgentRecord.multiplier`` menghitung ``1.0 + (accuracy - 0.5)``, dijepit pada
0,7-1,2, dan ``MeasuredHistory.reliability`` menyerahkannya langsung ke judge.
Begitu seorang agent melewati dua puluh lima opini terskor, bobotnya berubah -
tanpa proposal, tanpa backtest, tanpa satu pun manusia menyetujuinya.

PASAL 11.11 melarangnya dengan contoh yang persis ini:

    Agent 4:  1.00 -> 1.10
    Namun: ARUNA TIDAK BOLEH menerapkan perubahan secara otomatis.

dan PASAL 11.16 mengulanginya: **AUTO MODEL MODIFICATION dilarang**.

Belum meledak hanya karena belum ada agent yang cukup sampel. Cacat yang
menunggu ambang untuk menyala tetap cacat, dan yang ini akan menyala diam-diam
- bobot bergeser, putusan council ikut bergeser, dan tidak ada satu baris pun
yang menyatakan sesuatu telah berubah.

Modul ini memisahkan dua hal yang selama ini satu:

* **Yang diukur** - akurasi agent, dan bobot yang MENURUT pengukuran itu pantas.
  Ini tetap dihitung, tetap dilaporkan, dan tidak berlaku atas apa pun.
* **Yang berlaku** - bobot yang seorang manusia setujui. Bawaannya 1,0 untuk
  semua agent, dan tetap 1,0 sampai ada yang menyetujui perubahannya.

Perbedaan itu bukan formalitas. Bobot yang bergerak sendiri mengikuti akurasi
jangka pendek adalah sistem yang mengejar kebisingan: dua puluh lima observasi
cukup untuk membedakan agent bagus dari agent buruk hanya kalau pasarnya tidak
berubah, dan pasar selalu berubah. Manusia yang menyetujui bisa bertanya
"apakah dua puluh lima ini datang dari satu minggu yang aneh?" - dan angka
tidak bisa menanyakan itu pada dirinya sendiri.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aruna.core.enums import AgentRole

#: Bobot bawaan. Setiap agent mulai setara, dan tetap setara sampai seorang
#: manusia memutuskan sebaliknya.
DEFAULT_WEIGHT = 1.0

#: Perubahan di bawah ini tidak diusulkan. Sebuah proposal 1,00 -> 1,02 meminta
#: perhatian manusia untuk pergeseran yang tidak akan mengubah satu pun putusan,
#: dan proposal yang tidak berarti melatih penyetujunya menyetujui tanpa membaca.
MIN_PROPOSAL_DELTA = 0.05


@dataclass(frozen=True, slots=True)
class ApprovedWeights:
    """Bobot yang benar-benar dipakai judge.

    Bawaannya kosong, dan kosong berarti semua agent 1,0 - bukan "tidak ada
    data". Sebuah roster tanpa bobot yang disetujui adalah roster yang setara,
    dan itu keadaan yang sah serta bisa dipertahankan.
    """

    weights: dict[str, float] = field(default_factory=dict)

    def for_role(self, role: AgentRole | str) -> float:
        key = role.value if isinstance(role, AgentRole) else str(role)
        return float(self.weights.get(key, DEFAULT_WEIGHT))

    @property
    def any_adjusted(self) -> bool:
        return any(w != DEFAULT_WEIGHT for w in self.weights.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": dict(sorted(self.weights.items())),
            "adjusted": self.any_adjusted,
            "default": DEFAULT_WEIGHT,
        }


@dataclass(frozen=True, slots=True)
class WeightProposal:
    """Satu usulan perubahan bobot, beserta bukti yang melahirkannya."""

    role: str
    current: float
    proposed: float
    accuracy: float
    sample: int

    @property
    def delta(self) -> float:
        return round(self.proposed - self.current, 4)

    def summary(self) -> str:
        arah = "naik" if self.delta > 0 else "turun"
        return (
            f"{self.role}: {self.current:.2f} -> {self.proposed:.2f} "
            f"({arah} {abs(self.delta):.2f}) - akurasi {self.accuracy:.0%} "
            f"dari {self.sample} opini terskor"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "current": self.current,
            "proposed": self.proposed,
            "delta": self.delta,
            "accuracy": self.accuracy,
            "sample": self.sample,
            "summary": self.summary(),
        }


def propose_weights(
    records: Any,
    approved: ApprovedWeights | None = None,
    *,
    min_delta: float = MIN_PROPOSAL_DELTA,
) -> tuple[WeightProposal, ...]:
    """Usulkan perubahan bobot dari rekam jejak (PASAL 11.11).

    **Hanya mengusulkan.** Tidak ada satu pun jalur dari fungsi ini ke bobot
    yang berlaku; yang berlaku hanya berubah lewat persetujuan manusia.

    Agent yang sampelnya belum cukup tidak diusulkan sama sekali - bukan
    diusulkan dengan catatan kecil. ``AgentRecord.multiplier`` sudah
    mengembalikan ``None`` untuk mereka, dan mengusulkan perubahan dari
    ``None`` berarti mengarang angka untuk agent yang belum terukur, yang
    persis dilarang PASAL 11.16.
    """
    approved = approved or ApprovedWeights()
    out: list[WeightProposal] = []

    for record in records or ():
        diukur = record.multiplier
        akurasi = record.accuracy
        if diukur is None or akurasi is None:
            continue

        sekarang = approved.for_role(record.role)
        if abs(diukur - sekarang) < min_delta:
            continue

        out.append(WeightProposal(
            role=record.role.value,
            current=round(sekarang, 4),
            proposed=round(float(diukur), 4),
            accuracy=float(akurasi),
            sample=int(record.scored),
        ))

    # Yang paling jauh dari bobot berlakunya lebih dulu: itu yang paling
    # berdampak kalau disetujui, dan yang paling layak dibaca lebih dulu kalau
    # penyetujunya hanya sempat membaca satu.
    return tuple(sorted(out, key=lambda p: -abs(p.delta)))


__all__ = [
    "DEFAULT_WEIGHT",
    "MIN_PROPOSAL_DELTA",
    "ApprovedWeights",
    "WeightProposal",
    "propose_weights",
]
