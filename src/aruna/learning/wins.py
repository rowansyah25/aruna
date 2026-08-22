"""Belajar dari yang menang, bukan hanya dari yang kalah (PASAL 12.17).

Sistem ini sudah punya loss autopsy sejak Phase 8, dan tidak punya padanannya
untuk kemenangan. Ketimpangan itu terdengar seperti kehati-hatian dan bukan:
sebuah sistem yang hanya memeriksa kekalahannya belajar untuk berhenti
melakukan hal-hal, tidak pernah belajar untuk melakukan lebih banyak hal yang
berhasil - dan daftar larangannya tumbuh setiap minggu sampai tidak ada yang
tersisa untuk dikerjakan.

Ada juga sebab yang lebih halus. Pertanyaan "kenapa ini kalah" dijawab per
kejadian, dan jawabannya hampir selalu tersedia - selalu ada sesuatu yang
terlihat salah kalau dicari sesudah tahu hasilnya. Pertanyaan "kondisi apa yang
KONSISTEN menghasilkan menang" tidak bisa dijawab dari satu kejadian sama
sekali. Ia menuntut perbandingan, dan perbandingan menuntut sample - jadi modul
ini secara alami lebih tahan terhadap pola yang dikarang belakangan.

**Bukan cermin sempurna dari autopsy, dan sengaja.** Autopsy membedah SATU
kekalahan. Modul ini tidak membedah satu kemenangan; ia mencari kondisi yang
berulang. Membedah satu kemenangan akan menghasilkan cerita yang meyakinkan
tentang keberuntungan, dan cerita semacam itu jauh lebih berbahaya daripada
tidak punya cerita.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from aruna.learning.evidence import Evidence
from aruna.learning.patterns import Observation, Pattern, discover

#: Kondisi hanya dilaporkan sebagai "andal" kalau ia menang lebih sering
#: daripada rata-rata DAN sample-nya cukup. Ambangnya diambil dari selang, bukan
#: dari titik tengah - lihat ``Evidence.beats``.
#:
#: Tidak ada ambang win rate absolut di sini dengan sengaja. Sebuah sistem yang
#: rata-rata menang 12% punya kondisi-kondisi berguna di sekitar 25%, dan
#: sebuah ambang tetap seperti "di atas 60%" akan menyatakan tidak ada yang
#: berguna sambil membuang justru yang paling informatif.


@dataclass(frozen=True, slots=True)
class WinningCondition:
    """Satu kondisi yang berulang kali berada di sisi yang menang."""

    pattern: Pattern

    @property
    def sample_size(self) -> int:
        return self.pattern.sample_size

    def line(self) -> str:
        return self.pattern.line()


@dataclass(frozen=True, slots=True)
class WinStudy:
    """Apa yang bisa dipelajari dari kemenangan yang sudah terjadi."""

    reliable: tuple[WinningCondition, ...] = field(default_factory=tuple)
    #: Kondisi yang justru konsisten KALAH. Ikut dilaporkan di sini, di modul
    #: bernama "wins", karena keduanya adalah jawaban atas pertanyaan yang sama
    #: - "kondisi apa yang berulang" - dan memisahkannya ke laporan lain
    #: membuat operator harus membuka dua tempat untuk satu keputusan.
    avoid: tuple[WinningCondition, ...] = field(default_factory=tuple)
    baseline: Evidence = field(default_factory=lambda: Evidence(0, 0))
    #: Berapa kemenangan yang ada sama sekali. Nol adalah kabar penting, dan
    #: laporan yang hanya berisi daftar kosong tidak menyampaikannya.
    total_wins: int = 0

    @property
    def learnable(self) -> bool:
        return bool(self.reliable or self.avoid)

    def summary(self) -> str:
        if self.baseline.total == 0:
            return "belum ada prediksi terskor untuk dipelajari"
        if not self.learnable:
            return (
                f"{self.total_wins} kemenangan dari {self.baseline.total} "
                "prediksi, dan belum satu pun kondisi yang berulang cukup "
                "sering untuk disebut andal"
            )
        return (
            f"{self.total_wins} kemenangan dari {self.baseline.total} prediksi; "
            f"{len(self.reliable)} kondisi lebih baik dari rata-rata, "
            f"{len(self.avoid)} lebih buruk "
            f"(rata-rata {self.baseline.label()})"
        )


def study(observations: Iterable[Observation]) -> WinStudy:
    """Cari kondisi yang berulang kali menang, dan yang berulang kali kalah.

    Dibangun di atas :func:`~aruna.learning.patterns.discover` dan bukan dengan
    pengirisan sendiri: dua mesin yang mengiris data yang sama dengan aturan
    sample yang berbeda akan menghasilkan dua win rate untuk irisan yang sama,
    dan tidak ada cara memilih mana yang benar.
    """
    semua = list(observations)
    penemuan = discover(semua)

    andal = tuple(
        WinningCondition(p) for p in penemuan.patterns if p.beats_baseline
    )
    hindari = tuple(
        WinningCondition(p) for p in penemuan.patterns if p.worse_than_baseline
    )
    return WinStudy(
        reliable=andal,
        avoid=hindari,
        baseline=penemuan.baseline,
        total_wins=sum(1 for o in semua if o.won),
    )


__all__ = ["WinStudy", "WinningCondition", "study"]
