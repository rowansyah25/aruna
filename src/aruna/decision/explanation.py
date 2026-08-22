"""Kenapa keputusannya begitu (PASAL 14.29).

*"Gunakan evidence, bukan kalimat generik. DILARANG: 'Market terlihat bagus.'"*

Kalimat generik bukan sekadar tidak berguna - ia **berbahaya dengan cara yang
sulit terlihat**. Ia terbaca seperti alasan, jadi operator yang membacanya
merasa sudah diberi tahu sesuatu, dan berhenti bertanya. Sebuah keputusan tanpa
alasan sama sekali setidaknya jujur tentang apa yang tidak diketahuinya.

**Penjaga utamanya bukan daftar kata terlarang.** Daftar kata bisa dihindari
tanpa sengaja oleh siapa pun yang menulis kalimat kosong dengan kata lain.
Yang menjaga di sini adalah **sumber**: tiap alasan wajib menyebut dari mana ia
datang - struktur, volume, timeframe, strategi, rezim, risiko, agent, atau
data. Kalimat "market terlihat bagus" tidak punya sumber, dan itulah yang
membuatnya kosong. Daftar frasa terlarang tetap ada sebagai jaring kedua, dan
diperlakukan sebagai jaring kedua.

**Dua alasan dari sumber yang sama adalah satu alasan yang diulang.** "Struktur
15m bullish" dan "struktur masih utuh" terdengar seperti dua bukti; keduanya
satu pengamatan yang ditulis dua kali. Karena itu yang dihitung adalah jumlah
**sumber yang berbeda**, bukan jumlah kalimat.

**Bukti yang melawan ikut dibawa.** PASAL 14.8 sudah memintanya untuk konflik
timeframe; di sini berlaku umum. Penjelasan yang hanya memuat hal-hal yang
mendukung bukan penjelasan - ia pembelaan, dan pembelaan menyembunyikan persis
bagian yang paling perlu dibaca operator sebelum mempertaruhkan uangnya.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from aruna.core.claims import find_forbidden
from aruna.decision.score import Arah


class Sumber(StrEnum):
    """Dari mana sebuah alasan datang.

    Wajib disebut, dan itu penjaganya. Sebuah kalimat yang tidak bisa
    ditempelkan ke salah satu dari ini bukan bukti - ia kesan.
    """

    STRUKTUR = "struktur pasar"
    VOLUME = "volume"
    MOMENTUM = "momentum"
    TIMEFRAME = "timeframe lain"
    STRATEGI = "strategi historis"
    REZIM = "rezim pasar"
    RISIKO = "risiko"
    AGENT = "agent"
    DATA = "mutu data"


#: Frasa yang menandakan kalimat kosong (PASAL 14.29).
#:
#: **Jaring kedua, bukan penjaga utama.** Daftar seperti ini selalu bisa
#: dihindari - "market terlihat menjanjikan" lolos dari "terlihat bagus" tanpa
#: menjadi lebih berisi. Yang benar-benar menjaga adalah kewajiban menyebut
#: :class:`Sumber`. Daftar ini menangkap kasus yang paling sering, dan tidak
#: berpura-pura menangkap semuanya.
KOSONG: tuple[str, ...] = (
    "terlihat bagus",
    "kelihatan bagus",
    "looks good",
    "sepertinya bagus",
    "kayaknya",
    "feeling",
    "bagus banget",
    "menjanjikan",
    "promising",
)

#: Sumber berbeda paling sedikit yang membuat sesuatu layak disebut alasan.
#:
#: Dua, bukan satu: satu pengamatan adalah klaim, dan sebuah keputusan yang
#: berdiri di atas satu klaim runtuh bersama klaim itu. Dan bukan tiga meskipun
#: contoh PASAL 14.29 memakai tiga - ambang tiga akan menolak kasus dua-sumber
#: yang jujur, dan yang terjadi berikutnya adalah alasan ketiga yang ditulis
#: hanya untuk memenuhi hitungan.
MIN_SUMBER = 2


class ExplanationError(ValueError):
    """Penjelasan yang tidak menjelaskan apa pun."""


def check_text(text: str, *, label: str = "alasan") -> str:
    """Tolak kalimat yang kosong, generik, atau memuat klaim terlarang.

    Dipisahkan menjadi fungsi supaya aturan yang sama berlaku di mana pun ARUNA
    menulis kalimat penjelas - termasuk alasan pembalikan di PASAL 14.36, yang
    kalau boleh berbunyi "market berubah" akan membuat seluruh syarat pembalikan
    menjadi formalitas.
    """
    bersih = text.strip()
    if not bersih:
        raise ExplanationError(f"{label} tanpa isi")
    rendah = bersih.lower()
    kosong = [f for f in KOSONG if f in rendah]
    if kosong:
        raise ExplanationError(
            f"kalimat kosong: {kosong[0]!r} bukan bukti (PASAL 14.29)"
        )
    terlarang = find_forbidden(bersih)
    if terlarang:
        # PASAL 51. Ini teks tulisan ARUNA sendiri, jadi yang benar adalah
        # menolak - bukan menyensor. Menyensor di sini akan menyembunyikan
        # bug di lapisan yang menyusun kalimatnya.
        raise ExplanationError(
            f"klaim terlarang {terlarang[0]!r} di dalam {label} (PASAL 51)"
        )
    return bersih


@dataclass(frozen=True, slots=True)
class Alasan:
    """Satu bukti, beserta asalnya."""

    source: Sumber
    text: str

    def __post_init__(self) -> None:
        check_text(self.text, label=f"alasan dari {self.source.value}")

    def line(self) -> str:
        return f"{self.text.strip()} ({self.source.value})"


@dataclass(frozen=True, slots=True)
class Penjelasan:
    """KENAPA LONG / KENAPA SHORT / KENAPA NO SIGNAL (PASAL 14.29)."""

    decision: Arah
    reasons: tuple[Alasan, ...]
    #: Bukti yang melawan keputusannya. Boleh kosong - tidak setiap setup punya
    #: penentang - tapi tidak boleh dibuang kalau ada.
    against: tuple[Alasan, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        sumber = {a.source for a in self.reasons}
        if len(sumber) < MIN_SUMBER:
            raise ExplanationError(
                f"{self.title()} berdiri di atas {len(sumber)} sumber; "
                f"butuh {MIN_SUMBER} sumber berbeda (PASAL 14.29). Dua kalimat "
                f"dari sumber yang sama adalah satu alasan yang diulang"
            )

    def title(self) -> str:
        return f"KENAPA {self.decision.value}"

    @property
    def sources(self) -> tuple[Sumber, ...]:
        """Sumber yang dipakai, tanpa pengulangan, dalam urutan kemunculan."""
        terlihat: list[Sumber] = []
        for a in self.reasons:
            if a.source not in terlihat:
                terlihat.append(a.source)
        return tuple(terlihat)

    def report(self) -> list[str]:
        baris = [f"🧠 {self.title()}", ""]
        baris += [f"  + {a.line()}" for a in self.reasons]
        if self.against:
            # Ditaruh di blok yang sama, bukan di lampiran terpisah. Bukti
            # yang melawan dan harus dicari sendiri sama saja dengan bukti
            # yang tidak disebutkan.
            baris += ["", "  Yang melawan:"]
            baris += [f"  - {a.line()}" for a in self.against]
        return baris


__all__ = [
    "KOSONG",
    "MIN_SUMBER",
    "Alasan",
    "ExplanationError",
    "Penjelasan",
    "Sumber",
    "check_text",
]
