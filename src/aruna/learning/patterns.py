"""Menemukan pola dari sejarah, tanpa mengarang sebab (PASAL 12.2, 12.3).

Modul ini mengiris data historis dan mengukur hasilnya per irisan. Ia
**menemukan korelasi**, dan tidak satu baris pun di sini menyebutnya sebab -
tidak ada `cause`, tidak ada `because`, tidak ada `explains`. Pembedaan itu
bukan kerapian bahasa: "BTC menang 84% saat TRENDING" dan "TRENDING membuat BTC
menang" adalah dua klaim yang sangat berbeda, dan yang kedua tidak bisa
disimpulkan dari data pengamatan sebanyak apa pun.

**Bahaya sebenarnya modul ini bukan gagal menemukan pola - tapi menemukan
terlalu banyak.**

Dengan lima dimensi dan sejarah tiga hari, jumlah irisan yang bisa dibentuk
jauh melebihi jumlah prediksi yang ada untuk mengisinya. Iris cukup halus dan
setiap dataset akan menghasilkan irisan bermenang-100%; itu sifat pembagian,
bukan penemuan. Dua hal yang menahannya:

* :data:`~aruna.learning.evidence.MIN_SAMPLE` - irisan di bawahnya tidak pernah
  menyandang kesimpulan, hanya angkanya;
* :meth:`~aruna.learning.evidence.Evidence.beats` - membandingkan BATAS BAWAH
  selang dengan baseline, bukan titik tengahnya. Sebuah irisan 3-dari-3
  berhenti di batas bawah 44%, dan 44% tidak mengalahkan apa pun.

Yang dilaporkan tetap semuanya, termasuk irisan yang sample-nya belum cukup.
Menyaringnya keluar akan membuat operator melihat daftar berisi hanya pola yang
kuat, dan menyimpulkan tidak ada pola lain yang sedang diamati.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aruna.learning.evidence import Evidence, EvidenceLevel

#: Dimensi yang boleh diiris, dan urutannya menentukan bentuk `pattern_key`.
#:
#: Sengaja dibatasi pada apa yang benar-benar tersimpan per prediksi. Spec
#: PASAL 12.2 juga menyebut volume, volatilitas, funding dan open interest;
#: ketiganya belum tersimpan sebagai kolom per-signal, dan mengarang irisan
#: dari kolom yang tidak ada akan menghasilkan pola yang terlihat rapi dan
#: dihitung dari None. Ditambahkan ketika kolomnya ada, bukan sebelumnya.
DIMENSI = ("market", "symbol", "horizon", "direction", "regime", "quality_band")

#: Kombinasi irisan yang dihitung. Bukan seluruh himpunan kuasa dari DIMENSI.
#:
#: Enam dimensi menghasilkan 63 kombinasi; pada sejarah yang berisi ribuan
#: prediksi itu berarti puluhan ribu irisan, hampir semuanya berisi satu atau
#: dua sample. Yang dipilih di bawah adalah irisan yang bisa ditindaklanjuti -
#: tiap satunya menjawab pertanyaan yang benar-benar ditanyakan operator saat
#: memutuskan apakah menuruti sebuah signal.
KOMBINASI: tuple[tuple[str, ...], ...] = (
    ("market",),
    ("regime",),
    ("direction",),
    ("horizon",),
    ("symbol",),
    ("symbol", "direction"),
    ("symbol", "horizon"),
    ("regime", "direction"),
    ("horizon", "direction"),
    ("symbol", "horizon", "direction"),
    ("symbol", "regime", "direction"),
    ("quality_band",),
    ("quality_band", "direction"),
)


@dataclass(frozen=True, slots=True)
class Observation:
    """Satu prediksi yang sudah diskor, diringkas ke dimensi yang diiris.

    Sengaja tidak membawa harga, bar, atau apa pun yang berukuran besar: modul
    ini menghitung proporsi, dan menyeret data pasar melewatinya adalah cara
    Phase 12 menggembungkan memori tanpa ada yang meminta (PASAL 12.23).
    """

    market: str
    symbol: str
    horizon: str
    direction: str
    regime: str
    quality_band: str
    won: bool


@dataclass(frozen=True, slots=True)
class Pattern:
    """Satu irisan dan hasilnya. Korelasi, dinyatakan sebagai korelasi."""

    key: str
    dimensions: dict[str, str]
    evidence: Evidence
    #: Baseline yang dibandingkan - biasanya win rate keseluruhan. Disimpan
    #: bersama polanya karena "84% itu bagus" tidak berarti apa-apa tanpa
    #: menyebut dibanding apa.
    baseline: float
    #: Benar hanya kalau SELURUH selang di atas baseline dan sample-nya cukup.
    beats_baseline: bool = False
    worse_than_baseline: bool = False

    @property
    def level(self) -> EvidenceLevel:
        return self.evidence.level

    @property
    def sample_size(self) -> int:
        return self.evidence.total

    def line(self) -> str:
        """Satu baris untuk operator. Sample size selalu ikut."""
        irisan = " + ".join(f"{v}" for v in self.dimensions.values())
        tanda = ""
        if self.beats_baseline:
            tanda = "  (di atas rata-rata)"
        elif self.worse_than_baseline:
            tanda = "  (di bawah rata-rata)"
        return f"{irisan}: {self.evidence.label()}{tanda}"

    def to_row(self, *, model_version: str, computed_at: datetime) -> dict[str, Any]:
        """Bentuk baris untuk ``discovered_patterns``."""
        bawah, atas = self.evidence.interval
        rate = self.evidence.win_rate
        return {
            "pattern_key": self.key,
            "dimensions": self.dimensions,
            "wins": self.evidence.wins,
            "losses": self.evidence.losses,
            "sample_size": self.evidence.total,
            # Dibulatkan ke skala kolomnya - DECIMAL(6,5). Sebuah proporsi
            # lahir dari pembagian dan membawa lima belas angka di belakang
            # koma; MySQL membulatkannya sendiri lalu mengeluh pada tiap baris.
            # Cacat yang sama pernah memenuhi log lewat `expected_net_pnl`.
            "win_rate": None if rate is None else round(rate, 5),
            "ci_low": round(bawah, 5),
            "ci_high": round(atas, 5),
            "evidence": self.evidence.level.value,
            "beats_baseline": self.beats_baseline,
            "model_version": model_version,
            "computed_at": computed_at,
        }


@dataclass(frozen=True, slots=True)
class Discovery:
    """Seluruh hasil satu kali pencarian pola."""

    patterns: tuple[Pattern, ...] = field(default_factory=tuple)
    baseline: Evidence = field(default_factory=lambda: Evidence(0, 0))

    @property
    def conclusive(self) -> tuple[Pattern, ...]:
        """Yang sample-nya cukup untuk menyandang kesimpulan."""
        return tuple(p for p in self.patterns if p.evidence.conclusive)

    @property
    def notable(self) -> tuple[Pattern, ...]:
        """Yang berbeda dari rata-rata secara meyakinkan - dua arah.

        Yang memburuk ikut, dan itu bukan kelengkapan: daftar yang hanya
        memuat irisan yang unggul adalah cherry picking dengan nama lain
        (PASAL 11.21), dan irisan yang konsisten kalah justru yang paling
        berguna untuk diketahui sebelum menuruti sebuah signal.
        """
        return tuple(
            p for p in self.patterns
            if p.beats_baseline or p.worse_than_baseline
        )

    def summary(self) -> str:
        kuat = sum(1 for p in self.patterns if p.level is EvidenceLevel.STRONG)
        cukup = len(self.conclusive)
        return (
            f"{len(self.patterns)} irisan diperiksa, {cukup} bersample cukup, "
            f"{kuat} kuat, {len(self.notable)} berbeda dari rata-rata "
            f"(baseline {self.baseline.label()})"
        )


def _key(dimensi: dict[str, str]) -> str:
    return "|".join(f"{k}={dimensi[k]}" for k in sorted(dimensi))


def discover(
    observations: Iterable[Observation],
    *,
    combinations: Sequence[Sequence[str]] = KOMBINASI,
) -> Discovery:
    """Iris sejarah menurut ``combinations`` dan ukur hasil tiap irisan.

    Baseline-nya adalah win rate KESELURUHAN dari observasi yang sama, bukan
    50%. Pertanyaan yang berguna bukan "apakah irisan ini lebih baik dari
    lemparan koin" tapi "apakah mengetahui irisan ini memperbaiki tebakan
    dibanding tidak tahu apa-apa" - dan jawabannya diukur terhadap performa
    ARUNA sendiri.

    Sebuah sistem yang win rate keseluruhannya 24% akan menemukan banyak irisan
    yang "mengalahkan koin"-nya sendiri secara terbalik; membandingkan terhadap
    rata-rata sendiri membuat angkanya berarti apa pun keadaan keseluruhannya.
    """
    semua = list(observations)
    baseline = Evidence(
        wins=sum(1 for o in semua if o.won),
        losses=sum(1 for o in semua if not o.won),
    )
    dasar = baseline.win_rate

    hasil: list[Pattern] = []
    for kombinasi in combinations:
        ember: dict[tuple[str, ...], list[Observation]] = {}
        for o in semua:
            kunci = tuple(getattr(o, d) for d in kombinasi)
            ember.setdefault(kunci, []).append(o)

        for kunci, anggota in ember.items():
            dimensi = dict(zip(kombinasi, kunci, strict=True))
            bukti = Evidence(
                wins=sum(1 for o in anggota if o.won),
                losses=sum(1 for o in anggota if not o.won),
            )
            hasil.append(
                Pattern(
                    key=_key(dimensi),
                    dimensions=dimensi,
                    evidence=bukti,
                    baseline=dasar if dasar is not None else 0.0,
                    # Tanpa baseline tidak ada yang bisa dikalahkan. Itu terjadi
                    # ketika belum ada satu pun prediksi terskor, dan jawaban
                    # yang benar saat itu adalah "belum tahu", bukan "tidak".
                    beats_baseline=(
                        bukti.beats(dasar) if dasar is not None else False
                    ),
                    worse_than_baseline=(
                        bukti.worse_than(dasar) if dasar is not None else False
                    ),
                )
            )

    # Diurutkan dari sample terbesar, bukan dari win rate tertinggi. Daftar yang
    # diurutkan menurut win rate menaruh setiap kebetulan 3-dari-3 di puncak
    # halaman - persis tempat mata operator jatuh lebih dulu.
    hasil.sort(key=lambda p: (-p.sample_size, p.key))
    return Discovery(patterns=tuple(hasil), baseline=baseline)


def quality_band(score: float | None) -> str:
    """Kelompokkan signal quality menjadi pita yang bisa diiris.

    Pita, bukan angka mentah: mengiris pada nilai kontinu menghasilkan satu
    irisan per prediksi, dan satu irisan per prediksi selalu bermenang 100%
    atau 0%.
    """
    if score is None:
        return "UNKNOWN"
    if score >= 0.8:
        return "0.8-1.0"
    if score >= 0.6:
        return "0.6-0.8"
    if score >= 0.4:
        return "0.4-0.6"
    return "0.0-0.4"


__all__ = [
    "DIMENSI",
    "KOMBINASI",
    "Discovery",
    "Observation",
    "Pattern",
    "discover",
    "quality_band",
]
