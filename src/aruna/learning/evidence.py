"""Berapa banyak yang cukup untuk boleh menyimpulkan (PASAL 12.3).

Ini modul terpenting Phase 12, dan bukan karena rumitnya - ia sembilan puluh
baris aritmetika - tapi karena ia satu-satunya yang berdiri antara "ARUNA
belajar" dan "ARUNA mengarang".

**Angka yang memicu modul ini.** Saat Phase 12 dibangun, seluruh sejarah ARUNA
berumur tiga hari: 1.196 prediksi terskor, 104 terpublikasi, dan irisan
terbesar - satu simbol, satu timeframe, satu arah - berisi **delapan belas**
prediksi. Spec-nya mencontohkan 250 dan 2.840. Jarak antara keduanya adalah
jarak antara pola dan kebetulan.

Tiga dari tiga WIN adalah 100% win rate, dan ia juga hasil paling mungkin dari
melempar koin tiga kali yang kebetulan sisi yang sama. Sebuah sistem yang
melaporkan angka pertama tanpa menyebut yang kedua tidak berbohong pada satu
baris pun, dan tetap menyesatkan setiap pembacanya.

**Yang dipakai: selang Wilson, bukan proporsi telanjang.**

Proporsi telanjang (menang dibagi total) tidak punya cara mengatakan seberapa
yakin ia. Selang Wilson punya: ia mengembalikan rentang yang masuk akal untuk
win rate SEBENARNYA, dan rentang itu melebar sendiri ketika sample-nya kecil.
Pada 3 dari 3, ia menjawab "antara 31% dan 100%" - kalimat yang jujur, dan
kalimat yang tidak akan ditulis siapa pun yang hanya melihat angka 100%.

Dipilih di atas selang normal biasa karena selang normal rusak persis di tempat
yang paling berbahaya: pada sample kecil dan pada proporsi dekat 0 atau 1, ia
menghasilkan batas di bawah nol atau di atas satu. Wilson tidak pernah.

**Yang TIDAK dilakukan modul ini.** Ia tidak menyembunyikan angka. Sample kecil
tetap dilaporkan lengkap - berapa menang, berapa kalah, berapa totalnya - hanya
saja tanpa kesimpulan yang menempel padanya. PASAL 11.21 melarang menyembunyikan
kekalahan, dan meredam sample kecil dengan cara membuangnya akan menghapus
kekalahan bersamanya.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

#: Sample minimum sebelum sebuah irisan boleh menyandang kesimpulan apa pun.
#:
#: Tiga puluh, dan angkanya bukan tradisi kosong: di bawahnya selang Wilson
#: untuk win rate di sekitar 50% masih lebih lebar dari ±18 poin persentase -
#: cukup lebar untuk memuat "sedikit lebih baik dari koin" dan "jauh lebih baik
#: dari koin" sekaligus, yang berarti angkanya belum bisa membedakan keduanya.
MIN_SAMPLE = 30

#: Sample minimum sebelum sebuah irisan boleh disebut KUAT.
#:
#: Seratus. Pada jumlah ini selang Wilson menyempit ke sekitar ±10 poin, yang
#: cukup untuk membedakan strategi yang berguna dari yang tidak - tapi masih
#: jauh dari cukup untuk membedakan 84% dari 88%, dan modul ini tidak akan
#: pernah berpura-pura bisa.
STRONG_SAMPLE = 100

#: Tingkat kepercayaan selangnya: 95%. z untuk dua sisi.
Z_95 = 1.959963984540054


class EvidenceLevel(StrEnum):
    """Seberapa jauh sebuah angka boleh dibawa."""

    #: Terlalu sedikit untuk menyimpulkan apa pun. Angkanya tetap dilaporkan.
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    #: Cukup untuk menjadi petunjuk, belum cukup untuk mengubah apa pun.
    SUGGESTIVE = "SUGGESTIVE"
    #: Cukup untuk menopang sebuah proposal.
    STRONG = "STRONG"


def wilson_interval(wins: int, total: int, *, z: float = Z_95) -> tuple[float, float]:
    """Selang Wilson untuk proporsi kemenangan.

    Mengembalikan (batas bawah, batas atas), keduanya di [0, 1].

    ``total = 0`` mengembalikan (0.0, 1.0) - seluruh rentang - karena itulah
    yang benar: tanpa satu pun pengamatan, setiap win rate sama masuk akalnya.
    Mengembalikan (0, 0) akan membaca seperti "terukur nol persen".
    """
    if total <= 0:
        return 0.0, 1.0
    if wins < 0 or wins > total:
        raise ValueError(f"wins={wins} di luar rentang total={total}")

    p = wins / total
    z2 = z * z
    penyebut = 1.0 + z2 / total
    tengah = (p + z2 / (2.0 * total)) / penyebut
    lebar = (
        z
        * math.sqrt(p * (1.0 - p) / total + z2 / (4.0 * total * total))
        / penyebut
    )
    return max(0.0, tengah - lebar), min(1.0, tengah + lebar)


@dataclass(frozen=True, slots=True)
class Evidence:
    """Satu irisan data beserta seberapa jauh ia boleh dipercaya.

    Dibawa oleh setiap angka Phase 12 yang pernah sampai ke mata operator.
    Sebuah win rate tanpa objek ini menempel adalah angka tanpa sample size,
    dan PASAL 12.3 melarangnya muncul.
    """

    wins: int
    losses: int

    @property
    def total(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float | None:
        """Proporsi menang, atau None kalau tidak ada apa-apa untuk dibagi.

        None dan bukan 0.0: nol dari nol berarti "belum ada yang diukur", dan
        mencetaknya sebagai 0% berarti melaporkan kekalahan total yang tidak
        pernah terjadi (PASAL 4).
        """
        return self.wins / self.total if self.total else None

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.wins, self.total)

    @property
    def level(self) -> EvidenceLevel:
        if self.total < MIN_SAMPLE:
            return EvidenceLevel.INSUFFICIENT_SAMPLE
        if self.total < STRONG_SAMPLE:
            return EvidenceLevel.SUGGESTIVE
        return EvidenceLevel.STRONG

    @property
    def conclusive(self) -> bool:
        """Boleh dipakai untuk menopang sebuah kesimpulan."""
        return self.level is not EvidenceLevel.INSUFFICIENT_SAMPLE

    def beats(self, baseline: float) -> bool:
        """Benar hanya kalau SELURUH selangnya di atas ``baseline``.

        Inilah bentuk yang membuat "84% menang" berarti sesuatu. Membandingkan
        titik tengahnya dengan baseline akan menyatakan 3-dari-3 mengalahkan
        koin; membandingkan batas bawahnya tidak, karena batas bawah 3-dari-3
        adalah 31%.

        Sample yang belum cukup selalu False, apa pun angkanya.
        """
        if not self.conclusive:
            return False
        return self.interval[0] > baseline

    def worse_than(self, baseline: float) -> bool:
        """Cermin :meth:`beats`, dan ia harus ada.

        Sebuah gerbang yang hanya bisa memastikan "lebih baik" akan membuat
        pemburukan selalu terlihat belum pasti - dan strategi yang memburuk
        akan hidup selamanya di bawah keraguan yang menguntungkannya.
        """
        if not self.conclusive:
            return False
        return self.interval[1] < baseline

    def label(self, *, noun: str = "menang") -> str:
        """Satu baris untuk operator, lengkap dengan sample-nya.

        Sample size selalu ikut. PASAL 12.3 meminta setiap analisis
        menampilkannya, dan tempat paling aman untuk menaruh kewajiban itu
        adalah di dalam satu-satunya fungsi yang mencetak angkanya.

        ``noun`` ada karena kelas ini menghitung dua hal yang bentuknya sama
        dan artinya berbeda: proporsi perdagangan yang MENANG, dan proporsi
        panggilan agent yang BENAR. Keduanya "berhasil dibagi total", dan
        mencetak keduanya dengan kata "menang" membuat baris
        ``STRUCTURE: 13% menang`` terbaca sebagai win rate perdagangan agent
        itu - angka yang tidak ada, karena agent tidak berdagang.
        """
        if self.total == 0:
            return "belum ada data (0 sample)"
        rate = self.win_rate or 0.0
        bawah, atas = self.interval
        inti = (
            f"{rate:.0%} {noun} ({self.wins}/{self.total}), "
            f"sebenarnya antara {bawah:.0%} dan {atas:.0%}"
        )
        if self.level is EvidenceLevel.INSUFFICIENT_SAMPLE:
            return f"{inti} - SAMPLE BELUM CUKUP (butuh {MIN_SAMPLE})"
        return inti


def pooled(items: list[Evidence]) -> Evidence:
    """Gabungkan beberapa irisan menjadi satu.

    Dijumlahkan mentah, bukan dirata-rata dari win rate-nya: merata-ratakan
    persentase memberi bobot yang sama kepada irisan berisi tiga sample dan
    irisan berisi tiga ratus, dan itu cara paling halus membuat kebetulan
    terlihat seperti pola.
    """
    return Evidence(
        wins=sum(i.wins for i in items),
        losses=sum(i.losses for i in items),
    )


__all__ = [
    "MIN_SAMPLE",
    "STRONG_SAMPLE",
    "Evidence",
    "EvidenceLevel",
    "pooled",
    "wilson_interval",
]
