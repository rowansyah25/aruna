"""Daur hidup strategi, dan batas wewenang ARUNA atasnya (PASAL 12.15, 12.20).

Lima status, dan ia terbelah dua oleh satu pertanyaan: **apakah mengubahnya
mengubah perilaku ARUNA?**

*Pengamatan* - ``ACTIVE``, ``DEGRADED``, ``UNDER_REVIEW`` - adalah label yang
menggambarkan apa yang terukur. ARUNA boleh memasangnya sendiri; itu justru
pekerjaannya. Sebuah strategi yang jelas memburuk dan tetap berlabel "ACTIVE"
adalah catatan yang tidak menggambarkan kenyataan.

*Keputusan* - ``SUSPENDED``, ``RETIRED`` - mengeluarkan strategi dari
pertimbangan sama sekali. Itu perubahan pada apa yang ARUNA lakukan, bukan pada
apa yang ARUNA catat, dan PASAL 12.20 menaruhnya di tangan operator. ARUNA
mengusulkan; operator memutuskan.

**Kenapa pembedaan itu bukan formalitas.** Sebuah sistem yang boleh
menonaktifkan strateginya sendiri akan, pada data tiga hari, menonaktifkan
hampir semuanya - dan yang tersisa adalah yang kebetulan belum cukup diuji
untuk terlihat buruk. Penyempitan itu terjadi diam-diam, terlihat seperti
pembelajaran, dan tidak ada yang bisa membedakannya dari kelumpuhan.

**Yang tidak dilakukan modul ini: menghapus.** PASAL 12.15 melarangnya, dan
alasannya bukan kearsipan. Strategi yang dihapus membawa serta seluruh sejarah
kekalahannya, dan katalog yang hanya berisi yang masih dipakai akan selalu
terbaca seperti kumpulan ide bagus (PASAL 11.21).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from aruna.learning.evidence import Evidence
from aruna.learning.strategies import UNMAPPED, StrategyStatus

#: Status yang boleh ARUNA pasang sendiri: label pengamatan.
AUTO_ASSIGNABLE = (
    StrategyStatus.ACTIVE,
    StrategyStatus.DEGRADED,
    StrategyStatus.UNDER_REVIEW,
)

#: Status yang menuntut keputusan operator: ia mengubah perilaku.
NEEDS_APPROVAL = (StrategyStatus.SUSPENDED, StrategyStatus.RETIRED)

#: Sample sebelum sebuah strategi boleh dinaikkan ke UNDER_REVIEW.
#:
#: Dua ratus. DEGRADED sudah menuntut bukti yang meyakinkan; UNDER_REVIEW
#: adalah pernyataan yang lebih keras - "ini cukup diukur untuk pantas
#: dipertimbangkan dihentikan" - dan pernyataan yang lebih keras menuntut lebih
#: banyak bukti, bukan hanya pengulangan bukti yang sama.
REVIEW_SAMPLE = 200


@dataclass(frozen=True, slots=True)
class Assessment:
    """Satu strategi, statusnya sekarang, dan status yang buktinya dukung."""

    code: str
    current: StrategyStatus
    proposed: StrategyStatus
    reason: str
    evidence: Evidence
    net_pnl: Decimal = Decimal(0)

    @property
    def changed(self) -> bool:
        return self.proposed is not self.current

    @property
    def needs_approval(self) -> bool:
        """Perubahan ini mengubah perilaku, jadi operator yang memutuskan."""
        return self.proposed in NEEDS_APPROVAL

    @property
    def applicable(self) -> bool:
        """Boleh ditulis ARUNA tanpa bertanya."""
        return self.changed and not self.needs_approval

    def line(self) -> str:
        panah = f"{self.current.value} -> {self.proposed.value}"
        if not self.changed:
            panah = self.current.value
        tanda = "  [BUTUH PERSETUJUAN]" if self.needs_approval else ""
        return (
            f"{self.code}: {panah}{tanda}\n"
            f"    {self.reason}\n"
            f"    {self.evidence.label()}, bersih {self.net_pnl:.2f}"
        )


@dataclass(frozen=True, slots=True)
class LifecycleReport:
    assessments: tuple[Assessment, ...] = field(default_factory=tuple)

    @property
    def to_apply(self) -> tuple[Assessment, ...]:
        return tuple(a for a in self.assessments if a.applicable)

    @property
    def to_propose(self) -> tuple[Assessment, ...]:
        return tuple(
            a for a in self.assessments if a.changed and a.needs_approval
        )

    def summary(self) -> str:
        return (
            f"{len(self.assessments)} strategi dinilai, "
            f"{len(self.to_apply)} label diperbarui, "
            f"{len(self.to_propose)} menunggu keputusan Anda"
        )


def assess(
    code: str,
    current: StrategyStatus,
    evidence: Evidence,
    *,
    baseline: float | None,
    net_pnl: Decimal = Decimal(0),
) -> Assessment:
    """Status apa yang bukti dukung untuk satu strategi.

    **Pemulihan diperiksa lebih dulu, dan itu disengaja.** Sebuah daur hidup
    yang hanya bisa turun akan, diberi waktu cukup, menandai setiap strategi
    sebagai memburuk - karena setiap strategi sesekali melewati periode buruk,
    dan tidak ada jalan kembali. Label yang hanya bergerak satu arah berhenti
    menjadi pengukuran dan menjadi jam.
    """
    def _hasil(status: StrategyStatus, alasan: str) -> Assessment:
        return Assessment(
            code=code, current=current, proposed=status,
            reason=alasan, evidence=evidence, net_pnl=net_pnl,
        )

    # Penampung untuk prediksi yang tidak cocok strategi mana pun bukan
    # strategi, dan daur hidup tidak berlaku padanya. Labelnya UNDER_REVIEW
    # permanen - sebuah pernyataan tentang kelengkapan katalog, bukan tentang
    # performanya - dan versi pertama fungsi ini menimpanya menjadi ACTIVE
    # karena penampung itu memang tidak punya data. Yang hilang bukan sekadar
    # label: besarnya penampung adalah ukuran seberapa lengkap katalog ini,
    # dan mengubahnya menjadi "strategi aktif biasa" menghapus tanda bahwa ada
    # yang belum terpetakan.
    if code == UNMAPPED.code:
        return _hasil(current, "penampung, bukan strategi; daur hidup tidak berlaku")

    # Yang sudah dikeluarkan operator tidak dinilai ulang oleh ARUNA. Menaikkan
    # kembali sesuatu yang operator tangguhkan berarti membatalkan keputusannya
    # dengan pengukuran - dan penangguhan yang bisa dibatalkan mesin bukan
    # penangguhan.
    if current in NEEDS_APPROVAL:
        return _hasil(current, "dikeluarkan operator; ARUNA tidak menilainya ulang")

    if not evidence.conclusive:
        if current is StrategyStatus.ACTIVE:
            return _hasil(current, "sample belum cukup untuk menilai")
        return _hasil(
            StrategyStatus.ACTIVE,
            "sample belum cukup untuk menahan label sebelumnya",
        )

    if baseline is None:
        return _hasil(current, "belum ada rata-rata untuk membandingkan")

    memburuk = evidence.worse_than(baseline)

    if not memburuk:
        if current is StrategyStatus.ACTIVE:
            return _hasil(current, "tidak terukur lebih buruk dari rata-rata")
        return _hasil(
            StrategyStatus.ACTIVE,
            "tidak lagi terukur lebih buruk dari rata-rata",
        )

    # Terukur lebih buruk, dan meyakinkan.
    if evidence.total >= REVIEW_SAMPLE:
        return _hasil(
            StrategyStatus.UNDER_REVIEW,
            f"lebih buruk dari rata-rata pada {evidence.total} sample; "
            "cukup diukur untuk pantas dipertimbangkan dihentikan",
        )
    return _hasil(
        StrategyStatus.DEGRADED,
        "terukur lebih buruk dari rata-rata",
    )


def evaluate(
    rows: Iterable[dict],
    *,
    baseline: float | None,
) -> LifecycleReport:
    """Nilai seluruh katalog.

    ``rows`` berisi ``code``, ``status``, ``wins``, ``losses``, dan opsional
    ``net_pnl``. Strategi tanpa satu pun hasil ikut dinilai - dan jawabannya
    "sample belum cukup", yang berbeda dari tidak muncul sama sekali.
    """
    hasil: list[Assessment] = []
    for r in rows:
        kode = str(r.get("code") or r.get("strategy_code") or "")
        if not kode:
            continue
        try:
            status = StrategyStatus(str(r.get("status") or "ACTIVE"))
        except ValueError:
            status = StrategyStatus.ACTIVE
        hasil.append(
            assess(
                kode,
                status,
                Evidence(
                    wins=int(r.get("wins") or 0),
                    losses=int(r.get("losses") or 0),
                ),
                baseline=baseline,
                net_pnl=Decimal(str(r.get("net_pnl") or 0)),
            )
        )
    hasil.sort(key=lambda a: a.code)
    return LifecycleReport(assessments=tuple(hasil))


__all__ = [
    "AUTO_ASSIGNABLE",
    "NEEDS_APPROVAL",
    "REVIEW_SAMPLE",
    "Assessment",
    "LifecycleReport",
    "assess",
    "evaluate",
]
