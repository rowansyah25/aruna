"""Decision Score dan ambangnya (PASAL 14.16, 14.17).

Satu angka bertanda yang merangkum seluruh bukti berarah: positif condong
LONG, negatif condong SHORT, dan yang di antara ambangnya adalah **NO SIGNAL**.

PASAL 14.16 menutup penafsiran yang paling mudah terjadi terhadap angka
seperti ini: *"Decision Score bukan probabilitas profit dan tidak boleh
disebut sebagai jaminan kemenangan."* Sebuah "69" yang dicetak sendirian akan
dibaca sebagai 69 persen oleh siapa pun yang membacanya cepat, jadi setiap
kalimat yang dihasilkan modul ini membawa penyangkalannya sendiri - bukan
sebagai catatan kaki di tempat lain yang bisa terpisah dari angkanya.

Empat keputusan yang menentukan apakah angka ini berguna:

**1. Skornya TIDAK dinormalkan terhadap bukti yang tersedia.** Ini beda pokok
dengan :mod:`aruna.risk.score`, yang membagi dengan bobot terukur karena ia
menanyakan *"seberapa berisiko, rata-ratanya"*. Modul ini menanyakan
*"seberapa banyak bukti, totalnya"*, dan membagi dengan bobot terukur akan
mengubah dua faktor yang kebetulan searah menjadi kasus yang bulat. Satu
faktor terukur bernilai penuh menghasilkan 18, bukan 100.

**2. Risiko dan berita hanya bisa mengurangi.** Keduanya bukan bukti tentang
arah - setup yang berbahaya tidak menjadi alasan untuk SHORT, dan pasar yang
sepi berita bukan alasan untuk LONG. Keduanya memotong besaran menuju nol dan
tidak pernah membalik tandanya.

**3. Faktor yang tidak terukur tidak dihitung nol dengan diam.** Nol di sini
memang berarti netral - arah yang aman - tapi skor +62 dari tiga dari enam
faktor tetap terbaca sama dengan +62 dari enam-enamnya. Cakupannya dilaporkan,
dan di bawah :data:`MIN_COVERAGE` yang dikembalikan adalah "tidak bisa
dinilai", bukan angka.

**4. Ambangnya bisa diatur, tapi tidak bisa dibuat mustahil.** PASAL 14.17
meminta threshold configurable dan mengingatkan ia bukan hukum universal.
Ambang di atas total bobot yang mungkin akan membuat ARUNA diam selamanya
tanpa pernah melaporkan kesalahan apa pun, jadi ambang seperti itu ditolak di
sini alih-alih menjadi kesunyian yang tidak ada sebabnya.

Kalibrasi ambang dilakukan dari performa historis (PASAL 14.17) - dan tetap
lewat jalur proposal yang disetujui manusia (PASAL 11.16, 12.26). Modul ini
tidak punya jalur untuk mengubah ambangnya sendiri.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Arah(StrEnum):
    """PASAL 14.2 dan 14.43. Tiga keluaran, tidak lebih."""

    LONG = "LONG"
    SHORT = "SHORT"
    NO_SIGNAL = "NO SIGNAL"

    @property
    def mark(self) -> str:
        return {Arah.LONG: "🟢", Arah.SHORT: "🔴", Arah.NO_SIGNAL: "🟡"}[self]


@dataclass(frozen=True, slots=True)
class Bobot:
    """Satu komponen skor: namanya, dan berapa poin maksimum yang bisa
    disumbangkannya."""

    key: str
    label: str
    points: float


#: Komponen berarah. Nilainya -1.0 sampai +1.0, di mana **positif berarti
#: mendukung LONG**.
#:
#: Bobotnya persis contoh PASAL 14.16, dan itu disengaja: contoh di spesifikasi
#: adalah satu-satunya kalibrasi yang ada sekarang. Belum ada cukup outcome
#: untuk mengkalibrasinya terhadap performa historis, dan menyebut tebakan
#: sebagai hasil kalibrasi lebih berbahaya daripada tebakan yang diakui.
#:
#: Struktur pasar dan tren diberi bobot terbesar karena keduanya menyatakan ke
#: mana harga sudah bergerak; volume dan momentum menyatakan seberapa yakin
#: gerakan itu, dan itu pertanyaan yang lebih kecil.
ARAH: tuple[Bobot, ...] = (
    Bobot("trend", "tren", 18.0),
    Bobot("structure", "struktur pasar", 18.0),
    Bobot("momentum", "momentum", 14.0),
    Bobot("volume", "volume", 12.0),
    Bobot("agreement", "kesepakatan agent", 10.0),
    Bobot("history", "strategi historis", 9.0),
)

#: Komponen yang **hanya bisa mengurangi**. Nilainya 0.0 sampai 1.0, di mana
#: 1.0 berarti pengurangan penuh.
#:
#: Tidak ada anggota di sini yang boleh menambah poin. Risiko rendah bukan
#: bukti bahwa harga akan naik; ia hanya berarti kalau salah, salahnya lebih
#: murah. Menjadikannya bonus akan membuat setup yang aman dan tanpa arah
#: melewati ambang hanya karena tidak ada yang mengkhawatirkan.
PENALTI: tuple[Bobot, ...] = (
    Bobot("risk", "risiko", 8.0),
    Bobot("news", "berita", 4.0),
)

#: Skor terbesar yang mungkin: seluruh bukti berarah sepakat, tanpa potongan.
MAX_ARAH = sum(b.points for b in ARAH)

#: Potongan terbesar yang mungkin.
MAX_PENALTI = sum(b.points for b in PENALTI)

#: Ambang bawaan PASAL 14.17: LONG di ``>= +60``, SHORT di ``<= -60``.
#:
#: Enam puluh dari delapan puluh satu berarti sekitar tiga perempat seluruh
#: bukti berarah harus sejalan sebelum ARUNA berpendapat. Itu ambang yang
#: tinggi, dan konsekuensinya harus disebut terus terang: dengan ambang ini
#: NO SIGNAL akan tetap menjadi keluaran yang paling sering. Yang menilai
#: apakah itu benar bukan modul ini melainkan :mod:`aruna.decision.silence`,
#: yang mengukur seberapa sering diam itu tepat.
DEFAULT_THRESHOLD = 60.0

#: Cakupan minimum sebelum sebuah angka boleh disebut Decision Score.
#:
#: Sama dengan :data:`aruna.risk.score.MIN_COVERAGE`, dan alasannya sama: skor
#: dari sedikit komponen lebih menggambarkan komponen mana yang kebetulan
#: tersedia daripada keadaan pasarnya.
MIN_COVERAGE = 0.60


class ThresholdError(ValueError):
    """Ambang yang tidak mungkin dipenuhi, atau yang meloloskan segalanya."""


def check_threshold(threshold: float) -> float:
    """Tolak ambang yang membuat keluarannya tidak ada artinya.

    Dua arah kegagalan, dan yang pertama jauh lebih sulit terlihat:

    * ambang **di atas** :data:`MAX_ARAH` tidak akan pernah tercapai. ARUNA
      diam selamanya, tidak ada yang salah di log mana pun, dan sebabnya satu
      angka di berkas konfigurasi;
    * ambang **nol atau negatif** meloloskan setiap skor, termasuk nol. Setiap
      simbol menghasilkan arah, dan arahnya ditentukan pembulatan.
    """
    if threshold <= 0:
        raise ThresholdError(
            f"ambang {threshold} meloloskan setiap skor termasuk nol; "
            f"ARUNA akan berpendapat tentang segalanya"
        )
    if threshold > MAX_ARAH:
        raise ThresholdError(
            f"ambang {threshold} di atas skor tertinggi yang mungkin "
            f"({MAX_ARAH:.0f}); ARUNA tidak akan pernah berpendapat"
        )
    return float(threshold)


@dataclass(frozen=True, slots=True)
class Skor:
    """Decision Score beserta apa yang menyusunnya."""

    #: ``None`` kalau cakupannya terlalu tipis untuk disebut skor.
    value: float | None
    decision: Arah
    threshold: float
    coverage: float
    #: Skor sebelum potongan. Dipisahkan supaya terlihat berapa yang hilang
    #: karena risiko dan berita, bukan karena buktinya lemah.
    raw: float | None = None
    #: Sumbangan tiap komponen berarah, terurut dari yang terbesar.
    contributions: tuple[tuple[str, float], ...] = field(default_factory=tuple)
    #: Potongan yang dikenakan, terurut dari yang terbesar.
    penalties: tuple[tuple[str, float], ...] = field(default_factory=tuple)
    #: Komponen yang tidak terukur. Disebut namanya.
    unknown: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.value is not None

    def line(self) -> str:
        """Satu baris, dan penyangkalannya ikut di dalamnya.

        Angkanya tidak pernah berdiri sendiri. "69" yang dicetak tanpa apa-apa
        di sebelahnya akan dibaca sebagai 69 persen, dan PASAL 14.16 melarang
        persis pembacaan itu.
        """
        if not self.usable:
            return (
                f"{Arah.NO_SIGNAL.mark} {Arah.NO_SIGNAL.value} - bukti terlalu "
                f"tipis untuk dinilai ({self.coverage:.0%} komponen terukur, "
                f"butuh {MIN_COVERAGE:.0%})"
            )
        return (
            f"{self.decision.mark} {self.decision.value} "
            f"(skor {self.value:+.0f} dari maksimum {MAX_ARAH:.0f}, "
            f"ambang {self.threshold:+.0f})"
        )

    def report(self) -> list[str]:
        """Blok DECISION SCORE, sebagai baris."""
        baris = ["🧮 DECISION SCORE", "", f"  {self.line()}"]
        if self.contributions:
            baris += ["", "  Yang menyusunnya:"]
            baris += [f"    {n:+6.1f}  {lab}" for lab, n in self.contributions]
        if self.penalties:
            baris += ["", "  Yang memotongnya:"]
            baris += [f"    {-n:6.1f}  {lab}" for lab, n in self.penalties]
        if self.unknown:
            # Disebut, tidak didiamkan. Komponen yang hilang dari laporan
            # terbaca seperti komponen yang netral, dan netral terbaca seperti
            # sudah diperiksa.
            baris += ["", "  Tidak terukur:"]
            baris += [f"    ?      {lab}" for lab in self.unknown]
        baris += [
            "",
            "  PASAL 14.16: skor ini BUKAN peluang profit dan bukan jaminan",
            "  kemenangan. Ia ringkasan bukti, dan bukti bisa salah.",
        ]
        return baris


def score(
    readings: Mapping[str, float | None],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> Skor:
    """Gabungkan pembacaan komponen menjadi satu keputusan berarah.

    Komponen di :data:`ARAH` dibaca sebagai -1.0..+1.0 (positif mendukung
    LONG); komponen di :data:`PENALTI` sebagai 0.0..1.0. ``None`` berarti tidak
    terukur. Kunci yang tidak dikenal diabaikan dengan diam - tabelnya bisa
    bertambah, dan pemanggil lama tidak boleh pecah karenanya.

    Penyeragaman arah dilakukan pemanggil, bukan di sini. Yang tahu apakah
    ADX 34 mendukung LONG atau SHORT adalah lapisan yang mengukurnya; menebaknya
    di sini akan sesekali membalik tanda pada komponen dengan bobot terbesar.
    """
    batas = check_threshold(threshold)

    terukur: list[tuple[Bobot, float]] = []
    hilang: list[str] = []
    for b in ARAH:
        nilai = readings.get(b.key)
        if nilai is None:
            hilang.append(b.label)
            continue
        terukur.append((b, max(-1.0, min(1.0, float(nilai)))))

    coverage = (
        sum(b.points for b, _ in terukur) / MAX_ARAH if MAX_ARAH else 0.0
    )

    potongan: list[tuple[Bobot, float]] = []
    for b in PENALTI:
        nilai = readings.get(b.key)
        if nilai is None:
            hilang.append(b.label)
            continue
        # Dijepit ke 0.0 di bawah, bukan ke -1.0. Potongan negatif akan menjadi
        # bonus, dan bonus dari risiko rendah persis yang dilarang di atas.
        potongan.append((b, max(0.0, min(1.0, float(nilai)))))

    if coverage < MIN_COVERAGE:
        return Skor(
            value=None,
            decision=Arah.NO_SIGNAL,
            threshold=batas,
            coverage=coverage,
            unknown=tuple(hilang),
        )

    mentah = sum(b.points * v for b, v in terukur)
    dipotong = sum(b.points * v for b, v in potongan)

    # Potongan memakan BESARAN, bukan nilai bertanda. Mengurangi langsung dari
    # skor akan membuat risiko tinggi mendorong kasus LONG yang lemah menjadi
    # kasus SHORT - dan itu mengarang arah dari ketiadaan bukti arah.
    besaran = max(0.0, abs(mentah) - dipotong)
    akhir = besaran if mentah > 0 else -besaran if mentah < 0 else 0.0

    if akhir >= batas:
        arah = Arah.LONG
    elif akhir <= -batas:
        arah = Arah.SHORT
    else:
        arah = Arah.NO_SIGNAL

    return Skor(
        value=round(akhir, 1),
        decision=arah,
        threshold=batas,
        coverage=coverage,
        raw=round(mentah, 1),
        contributions=tuple(
            sorted(
                ((b.label, round(b.points * v, 1)) for b, v in terukur),
                key=lambda p: -abs(p[1]),
            )
        ),
        penalties=tuple(
            sorted(
                ((b.label, round(b.points * v, 1)) for b, v in potongan if v > 0),
                key=lambda p: -p[1],
            )
        ),
        unknown=tuple(hilang),
    )


def points_of(key: str) -> float:
    """Bobot satu komponen. Nol untuk kunci yang tidak dikenal."""
    for b in (*ARAH, *PENALTI):
        if b.key == key:
            return b.points
    return 0.0


__all__ = [
    "ARAH",
    "DEFAULT_THRESHOLD",
    "MAX_ARAH",
    "MAX_PENALTI",
    "MIN_COVERAGE",
    "PENALTI",
    "Arah",
    "Bobot",
    "Skor",
    "ThresholdError",
    "check_threshold",
    "points_of",
    "score",
]
