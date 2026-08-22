"""Laporan harian ARUNA, format persis (PASAL 1-13 spec Daily Report).

Satu pesan per hari, pukul 00:00, dan formatnya ditentukan operator sampai ke
emoji dan pemisahnya. Modul ini tidak memilih tata letak; ia mengisi tata letak
yang sudah ditetapkan.

**Tidak ada angka yang dikarang di sini.** Setiap hitungan datang dari baris
yang tersimpan, dan kategori tanpa hasil menulis ``N/A`` - bukan ``0.00%``,
yang terbaca seperti nol persen kemenangan dari sekian percobaan, padahal
tidak ada percobaan sama sekali. ``NaN``, ``null``, ``undefined`` dan
``Infinity`` tidak pernah bisa terbentuk karena pembaginya diperiksa sebelum
dipakai, bukan setelahnya (PASAL 5).

**ACTIVE tidak pernah masuk win rate** (PASAL 3, 6). Signal yang belum selesai
belum menang dan belum kalah; memasukkannya ke salah satu sisi adalah cara
paling mudah membuat angka terlihat lebih baik daripada kenyataannya, dan cara
itu ditutup di sini dengan menghitung penyebut dari ``win + loss`` saja.

**ARUNA MENGANALISIS SAJA.** Laporan ini catatan, bukan ajakan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Pemisah antar-section. Dua puluh U+2501, seperti di template.
RULE = "━" * 20

#: Nama bulan versi template: huruf besar, bahasa Inggris. Tidak diambil dari
#: ``strftime``: locale mesin bisa mengubahnya menjadi "Agustus" atau "août",
#: dan format yang berubah mengikuti pengaturan sistem bukan format yang
#: ditentukan.
_MONTHS = (
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
)


def _date_line(moment: datetime) -> str:
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"


def _pct(value: float | None) -> str:
    """Persentase dua desimal, atau ``N/A`` kalau tidak ada yang diukur."""
    return "N/A" if value is None else f"{value:.2f}%"


@dataclass(frozen=True, slots=True)
class Tally:
    """Hitungan satu kategori.

    ``total`` disimpan terpisah dari ``win + loss + active`` karena sebuah
    signal bisa berakhir INVALIDATED, EXPIRED atau UNRESOLVED (PASAL 4). Empat
    angka itu tidak selalu berjumlah ``total``, dan memaksanya berjumlah akan
    menyembunyikan signal yang tidak pernah terjawab.
    """

    total: int = 0
    win: int = 0
    loss: int = 0
    active: int = 0

    @property
    def decided(self) -> int:
        return self.win + self.loss

    @property
    def win_rate(self) -> float | None:
        """``None`` kalau belum ada yang selesai - bukan nol (PASAL 5)."""
        return None if self.decided == 0 else self.win / self.decided * 100

    @property
    def loss_rate(self) -> float | None:
        return None if self.decided == 0 else self.loss / self.decided * 100


@dataclass(frozen=True, slots=True)
class MarketBlock:
    """Satu pasar, dengan rinciannya per arah."""

    title: str
    icon: str
    tally: Tally = field(default_factory=Tally)
    long: Tally | None = None
    short: Tally | None = None


@dataclass(frozen=True, slots=True)
class AgentScore:
    name: str
    win_rate: float


@dataclass(frozen=True, slots=True)
class Component:
    label: str
    icon: str
    status: str
    healthy: bool = True


@dataclass(frozen=True, slots=True)
class SelfCorrection:
    loss_analyzed: int = 0
    pattern_detected: int = 0
    correction_proposed: int = 0
    correction_approved: int = 0
    correction_applied: int = 0
    model_version: str = "ARUNA v1.0"


@dataclass(frozen=True, slots=True)
class CouncilScore:
    correct: int = 0
    incorrect: int = 0

    @property
    def accuracy(self) -> float | None:
        decided = self.correct + self.incorrect
        return None if decided == 0 else self.correct / decided * 100


@dataclass(frozen=True, slots=True)
class DailyReport:
    """Semua yang masuk laporan harian, sudah dihitung."""

    date: datetime
    markets: tuple[MarketBlock, ...]
    agents: tuple[AgentScore, ...] = ()
    council: CouncilScore = field(default_factory=CouncilScore)
    correction: SelfCorrection = field(default_factory=SelfCorrection)
    components: tuple[Component, ...] = ()
    uptime: str = "-"
    #: Akurasi diam (PASAL 14.32, 14.33). ``None`` berarti belum terhitung -
    #: dibedakan dari laporan kosong, yang berarti sudah dihitung dan tidak ada
    #: satu pun NO SIGNAL hari itu.
    silence: Any = None
    #: Keadaan ingatan pasar (PASAL 15.43). ``None`` berarti belum terhitung -
    #: dibedakan dari nol, yang berarti sudah dihitung dan tidak ada ingatan
    #: baru hari itu. Alasan yang sama persis dengan ``silence`` di atas.
    memory: Any = None

    @property
    def overall(self) -> Tally:
        """Jumlah seluruh pasar.

        Dihitung dari blok-bloknya, tidak diterima sebagai masukan terpisah.
        Total yang dioper sendiri bisa tidak cocok dengan rinciannya - dan
        laporan yang bagian bawahnya membantah bagian atasnya tidak bisa
        dipercaya di kedua bagian.
        """
        return Tally(
            total=sum(m.tally.total for m in self.markets),
            win=sum(m.tally.win for m in self.markets),
            loss=sum(m.tally.loss for m in self.markets),
            active=sum(m.tally.active for m in self.markets),
        )


def _tally_lines(tally: Tally, *, rate_prefix: str = "") -> list[str]:
    return [
        "📊 Total Signal:",
        f"{tally.total}",
        "",
        "🟢 WIN:",
        f"{tally.win}",
        "",
        "🔴 LOSS:",
        f"{tally.loss}",
        "",
        "🟡 ACTIVE:",
        f"{tally.active}",
        "",
        f"📈 {rate_prefix}Win Rate:",
        _pct(tally.win_rate),
        "",
        f"📉 {rate_prefix}Loss Rate:",
        _pct(tally.loss_rate),
    ]


def _side_lines(icon: str, label: str, tally: Tally) -> list[str]:
    return [
        "",
        f"{icon} {label}:",
        f"{tally.total}",
        "",
        "   ├─ 🟢 WIN:",
        f"   {tally.win}",
        "",
        "   └─ 🔴 LOSS:",
        f"   {tally.loss}",
    ]


def _market_lines(block: MarketBlock) -> list[str]:
    lines = [RULE, f"{block.icon} {block.title}", RULE, ""]
    lines += _tally_lines(block.tally)
    if block.long is not None:
        lines += _side_lines("🟢", "LONG", block.long)
    # SHORT hanya muncul kalau pasar itu memang punya call turun. Spot dan
    # saham Indonesia di contoh operator tidak punya - tapi blok ini tetap
    # dicetak untuk pasar mana pun yang punya, karena menyembunyikannya akan
    # menghapus sebagian catatan dari laporan yang gunanya justru mencatat.
    if block.short is not None:
        lines += _side_lines("🔴", "SHORT", block.short)
    lines.append("")
    return lines


#: Medali untuk tiga teratas. Sisanya tidak dirangking di pesan ini.
_MEDALS = ("🥇", "🥈", "🥉")


def _agent_lines(agents: tuple[AgentScore, ...]) -> list[str]:
    lines = [RULE, "🤖 AGENT PERFORMANCE", RULE, ""]
    if not agents:
        # Dikatakan, bukan dikosongkan. Bagian yang hilang tanpa keterangan
        # terbaca seperti pesan yang terpotong (PASAL 5).
        lines += [
            "Belum ada agent dengan cukup opini terskor untuk dirangking.",
            "",
        ]
        return lines

    ranked = sorted(agents, key=lambda a: a.win_rate, reverse=True)
    for medal, agent in zip(_MEDALS, ranked, strict=False):
        lines += [f"{medal} {agent.name}:", f"{agent.win_rate:.2f}% Win Rate", ""]

    # Yang terbawah disebut hanya kalau ia bukan salah satu yang barusan
    # dipuji. Dengan dua agent, "terbaik" dan "terburuk" adalah orang yang
    # sama disebut dua kali, dan itu bukan informasi.
    if len(ranked) > len(_MEDALS):
        lowest = ranked[-1]
        lines += ["⚠️ Lowest:", f"{lowest.name} — {lowest.win_rate:.2f}%", ""]
    return lines


def render_daily(report: DailyReport) -> str:
    """Susun pesan harian, persis seperti template."""
    lines = [
        "📊 ARUNA DAILY PERFORMANCE",
        RULE,
        "",
        "📅 DATE:",
        _date_line(report.date),
        "",
        "⏱ PERIOD:",
        # EN DASH, bukan hyphen. Template operator memakainya, dan format ini
        # ditetapkan sampai ke simbolnya - jadi lint yang menyarankan "-"
        # sedang menyarankan agar pesannya menyimpang dari yang diminta.
        "00:00 – 23:59",  # noqa: RUF001
        "",
    ]

    for block in report.markets:
        lines += _market_lines(block)

    overall = report.overall
    lines += [RULE, "🏆 TOTAL PERFORMANCE", RULE, ""]
    lines += _tally_lines(overall, rate_prefix="Overall ")
    lines.append("")

    lines += _agent_lines(report.agents)

    lines += [
        RULE,
        "🧠 COUNCIL PERFORMANCE",
        RULE,
        "",
        "✅ Correct:",
        f"{report.council.correct}",
        "",
        "❌ Incorrect:",
        f"{report.council.incorrect}",
        "",
        "📊 Accuracy:",
        _pct(report.council.accuracy),
        "",
    ]

    c = report.correction
    lines += [
        RULE,
        "🧠 SELF-CORRECTION",
        RULE,
        "",
        "🔎 Loss Analyzed:",
        f"{c.loss_analyzed}",
        "",
        "🔍 Pattern Detected:",
        f"{c.pattern_detected}",
        "",
        "💡 Correction Proposed:",
        f"{c.correction_proposed}",
        "",
        "✅ Correction Approved:",
        f"{c.correction_approved}",
        "",
        "🔧 Correction Applied:",
        f"{c.correction_applied}",
        "",
        "🤖 Current Model:",
        c.model_version,
        "",
    ]

    # PASAL 14.32/14.33. Ditaruh sesudah SELF-CORRECTION dan sebelum status
    # sistem, karena ia bagian dari penilaian ARUNA atas dirinya - bukan
    # keterangan mesin.
    #
    # ``None`` berarti belum terhitung dan tidak dicetak sama sekali. Sebuah
    # "AKURASI NO SIGNAL: 0%" yang lahir dari ketiadaan hitungan terbaca persis
    # seperti ARUNA yang selalu salah diam.
    if report.silence is not None:
        lines += [RULE, *report.silence.report(), ""]

    # PASAL 15.43, tepat sesudah blok diam: keduanya jawaban ARUNA atas
    # pertanyaan "apa yang kamu tahu tentang dirimu sendiri", bukan keterangan
    # mesin. ``None`` tidak dicetak sama sekali - lihat catatan di bidangnya.
    if report.memory is not None:
        lines += [RULE, *report.memory.report()]

    lines += [RULE, "⚙️ SYSTEM STATUS", RULE, ""]
    for component in report.components:
        mark = "🟢" if component.healthy else "🔴"
        lines += [
            f"{component.icon} {component.label}:",
            f"{mark} {component.status}",
            "",
        ]
    lines += ["⏱ Uptime:", report.uptime, "", RULE, ""]

    lines += ["🤖 ARUNA ANALYST ONLY", "⚡ EXECUTION: USER"]
    return "\n".join(lines)


__all__ = [
    "RULE",
    "AgentScore",
    "Component",
    "CouncilScore",
    "DailyReport",
    "MarketBlock",
    "SelfCorrection",
    "Tally",
    "render_daily",
]
