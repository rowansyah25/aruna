"""Analisis lintas timeframe (PASAL 14.4 - 14.8).

Enam timeframe dibaca bersamaan, masing-masing dengan keputusan internalnya
sendiri. PASAL 14.4 menutup satu godaan besar: *"ARUNA tidak boleh menganggap
semua timeframe harus sama."* Perbedaan antar timeframe adalah keadaan normal
pasar, bukan kesalahan yang harus dihilangkan.

**Arahnya datang dari SATU timeframe: horizon keputusannya.** Bukan dari suara
terbanyak. PASAL 14.7 melarangnya dengan kalimat yang tidak bisa ditafsirkan
dua cara: *"ARUNA tidak boleh mencampurkan semua timeframe menjadi satu tanpa
konteks."* Suara terbanyak persis pencampuran itu - ia mengubah lima pembacaan
yang menjawab lima pertanyaan berbeda menjadi satu angka yang tidak menjawab
satu pun. Contoh di PASAL 14.7 menegaskannya: 5m SHORT, 10m SHORT, 15m LONG,
horizon 15 menit, final **LONG**.

**Yang lain bukan pemilih; mereka konteks.** PASAL 14.5 memberi keduanya
pekerjaan yang berbeda - timeframe tinggi memberi konteks tren, timeframe
rendah memberi waktu masuk - jadi perlawanan dari keduanya bukan hal yang
sama. Perlawanan dari bawah adalah **pullback**; perlawanan dari atas adalah
**melawan arus**. Yang pertama sering wajar, yang kedua jarang. Melaporkan
keduanya sebagai "3 timeframe menolak" menghapus perbedaan yang justru paling
berguna.

**Timeframe yang tidak bisa diurutkan ditolak, bukan ditaruh di belakang.**
Tinggi dan rendah adalah seluruh isi PASAL 14.5, dan urutan yang ditebak dari
teks akan menaruh "10m" di bawah "5m". Urutannya diambil dari
:data:`aruna.decision.lifecycle.HORIZON` supaya hanya ada satu daftar
timeframe yang dikenal ARUNA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any

from aruna.decision.lifecycle import HORIZON
from aruna.decision.score import Arah


class TimeframeError(ValueError):
    """Timeframe yang tidak dikenal, atau peta yang tidak konsisten."""


def urutan(interval: str) -> timedelta:
    """Panjang satu timeframe, untuk membandingkan tinggi-rendah."""
    d = HORIZON.get(interval)
    if d is None:
        raise TimeframeError(
            f"timeframe {interval!r} tidak dikenal; tinggi-rendahnya tidak "
            f"bisa ditentukan, dan menebaknya akan menaruh '10m' di bawah '5m'"
        )
    return d


class Posisi(StrEnum):
    """Letak sebuah pembacaan terhadap horizon keputusannya."""

    HIGHER = "lebih tinggi"
    HORIZON = "horizon keputusan"
    LOWER = "lebih rendah"

    @property
    def job(self) -> str:
        """Pekerjaannya menurut PASAL 14.5."""
        return {
            Posisi.HIGHER: "konteks tren",
            Posisi.HORIZON: "keputusan",
            Posisi.LOWER: "waktu masuk",
        }[self]


class Kelas(StrEnum):
    """Kelas horizon PASAL 14.6."""

    SCALP = "SCALP"
    SHORT_INTRADAY = "SHORT INTRADAY"
    INTRADAY = "INTRADAY"
    SWING = "SWING"


#: Batas atas tiap kelas, inklusif, terurut dari yang terpendek (PASAL 14.6).
#:
#: **Dua batasnya bertumpang tindih di spesifikasi**, dan itu diselesaikan di
#: sini alih-alih dibiarkan: "SHORT INTRADAY: 15-60 minutes" berbagi 60 menit
#: dengan "INTRADAY: 1-4 hours", dan "INTRADAY" berbagi 4 jam dengan "SWING:
#: 4H-Daily". Aturannya satu, dipakai di kedua tempat: **di batas bersama,
#: kelas yang lebih pendek yang menang.** Kelas yang lebih pendek menyiratkan
#: masa berlaku yang lebih pendek, dan keputusan yang mati terlalu cepat lebih
#: mudah diperbaiki daripada yang hidup terlalu lama.
BATAS: tuple[tuple[timedelta, Kelas], ...] = (
    (timedelta(minutes=10), Kelas.SCALP),
    (timedelta(minutes=60), Kelas.SHORT_INTRADAY),
    (timedelta(hours=4), Kelas.INTRADAY),
    (timedelta(days=1), Kelas.SWING),
)

#: Batas bawah tabel PASAL 14.6. Di bawah ini tidak ada kelas yang disebutkan.
TERPENDEK = timedelta(minutes=5)


def classify(interval: str) -> Kelas | None:
    """Kelas horizon sebuah timeframe (PASAL 14.6).

    ``None`` untuk yang di luar tabel - 1m dan 3m di bawahnya, mingguan di
    atasnya. Tidak dibulatkan ke kelas terdekat: menyebut keputusan satu menit
    sebagai SCALP memberinya nama yang tidak pernah ditulis di PASAL 14.6, dan
    nama yang salah lebih menyesatkan daripada tidak ada nama.
    """
    d = urutan(interval)
    if d < TERPENDEK:
        return None
    for batas, kelas in BATAS:
        if d <= batas:
            return kelas
    return None


@dataclass(frozen=True, slots=True)
class Bacaan:
    """Keputusan internal satu timeframe (PASAL 14.4)."""

    interval: str
    decision: Arah
    #: Bukti yang menopangnya - "volume konfirmasi breakout". PASAL 14.8
    #: meminta bukti pendukung dan bukti penentang disebut, bukan disimpulkan.
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        urutan(self.interval)

    def line(self) -> str:
        dasar = f"{self.interval}: {self.decision.mark} {self.decision.value}"
        if not self.evidence:
            return dasar
        return f"{dasar} - {', '.join(self.evidence)}"


@dataclass(frozen=True, slots=True)
class Lintas:
    """Seluruh pembacaan timeframe, beserta horizon yang menentukan arahnya."""

    horizon: str
    readings: tuple[Bacaan, ...]
    #: Rezim pasar. PASAL 14.8 memintanya ikut disebut saat ada konflik.
    regime: str | None = None

    def __post_init__(self) -> None:
        urutan(self.horizon)
        seen: set[str] = set()
        for b in self.readings:
            if b.interval in seen:
                raise TimeframeError(
                    f"{b.interval} dibaca dua kali; dua keputusan untuk satu "
                    f"timeframe berarti salah satunya akan diabaikan diam-diam"
                )
            seen.add(b.interval)

    # ---- letak ---------------------------------------------------------

    def posisi(self, interval: str) -> Posisi:
        d, h = urutan(interval), urutan(self.horizon)
        if d > h:
            return Posisi.HIGHER
        if d < h:
            return Posisi.LOWER
        return Posisi.HORIZON

    @property
    def at_horizon(self) -> Bacaan | None:
        for b in self.readings:
            if b.interval == self.horizon:
                return b
        return None

    # ---- keputusan -----------------------------------------------------

    @property
    def decision(self) -> Arah:
        """Arah final: keputusan timeframe horizonnya, dan hanya itu.

        Tidak ada penghitungan suara di sini, dan itu disengaja. Lihat catatan
        modul: suara terbanyak adalah pencampuran yang PASAL 14.7 larang.
        """
        b = self.at_horizon
        return b.decision if b is not None else Arah.NO_SIGNAL

    @property
    def reason(self) -> str:
        """Kenapa arahnya begitu - termasuk kenapa tidak ada arah."""
        b = self.at_horizon
        if b is None:
            return (
                f"timeframe horizon {self.horizon} tidak dianalisis; "
                f"timeframe lain tidak menggantikannya"
            )
        if b.decision is Arah.NO_SIGNAL:
            return f"{self.horizon} tidak memberi arah"
        lawan = self.opposing
        if not lawan:
            return f"{self.horizon} {b.decision.value} tanpa perlawanan"
        return (
            f"{self.horizon} {b.decision.value} mendominasi "
            f"{len(lawan)} timeframe yang berlawanan"
        )

    # ---- konflik (PASAL 14.8) -----------------------------------------

    @property
    def others(self) -> tuple[Bacaan, ...]:
        return tuple(b for b in self.readings if b.interval != self.horizon)

    @property
    def supporting(self) -> tuple[Bacaan, ...]:
        arah = self.decision
        if arah is Arah.NO_SIGNAL:
            return ()
        return tuple(b for b in self.others if b.decision is arah)

    @property
    def opposing(self) -> tuple[Bacaan, ...]:
        """Timeframe yang memberi arah BERLAWANAN.

        Yang NO SIGNAL tidak dihitung melawan. Timeframe yang tidak menemukan
        arah tidak sedang menentang apa pun; memasukkannya ke daftar penentang
        membuat pasar yang sepi terbaca sebagai pasar yang bertengkar.
        """
        arah = self.decision
        if arah is Arah.NO_SIGNAL:
            return ()
        return tuple(
            b for b in self.others
            if b.decision is not arah and b.decision is not Arah.NO_SIGNAL
        )

    @property
    def pullbacks(self) -> tuple[Bacaan, ...]:
        """Perlawanan dari BAWAH horizon (PASAL 14.5).

        *"Jangan langsung menyimpulkan SHORT hanya karena 5m bearish."*
        """
        return tuple(
            b for b in self.opposing if self.posisi(b.interval) is Posisi.LOWER
        )

    @property
    def against_trend(self) -> tuple[Bacaan, ...]:
        """Perlawanan dari ATAS horizon - konteks tren yang tidak sejalan.

        Jauh lebih berat daripada pullback, dan dipisahkan justru karena itu.
        """
        return tuple(
            b for b in self.opposing if self.posisi(b.interval) is Posisi.HIGHER
        )

    @property
    def conflicted(self) -> bool:
        return bool(self.opposing)

    # ---- laporan -------------------------------------------------------

    def report(self) -> list[str]:
        """Blok MULTI-TIMEFRAME (PASAL 14.4, 14.8), sebagai baris.

        PASAL 14.8 meminta lima hal saat ada konflik: timeframe mana yang
        dominan, mengapa, bukti yang mendukung, bukti yang melawan, dan rezim
        pasar. Kelimanya dicetak - sebuah penjelasan konflik yang kehilangan
        salah satunya membuat pembacanya menebak bagian yang hilang.
        """
        baris = ["📊 MULTI-TIMEFRAME", ""]
        for b in sorted(self.readings, key=lambda x: urutan(x.interval)):
            p = self.posisi(b.interval)
            tanda = "◀" if p is Posisi.HORIZON else " "
            baris.append(f"  {tanda} {b.line()}  ({p.job})")

        baris += ["", f"  DOMINAN: {self.horizon} - {Posisi.HORIZON.job}"]
        baris.append(f"  ALASAN: {self.reason}")
        if self.regime:
            baris.append(f"  REZIM: {self.regime}")

        if self.supporting:
            baris += ["", "  Yang mendukung:"]
            baris += [f"    + {b.line()}" for b in self.supporting]
        if self.pullbacks:
            baris += ["", "  Yang melawan dari timeframe lebih rendah (pullback):"]
            baris += [f"    - {b.line()}" for b in self.pullbacks]
        if self.against_trend:
            # Ditaruh terakhir dan diberi kalimatnya sendiri: ini satu-satunya
            # perlawanan yang tidak bisa dijelaskan sebagai gerakan sementara.
            baris += ["", "  Yang melawan dari timeframe lebih tinggi:"]
            baris += [f"    ⚠️ {b.line()}" for b in self.against_trend]
            baris.append("    Konteks tren tidak sejalan - ini bukan pullback.")
        return baris


def reading_from_structure(interval: str, structure: Any) -> Bacaan:
    """Keputusan internal satu timeframe dari strukturnya (PASAL 14.4).

    **Tren dulu, penembusan menyusul.** Tren menyatakan ke mana harga sudah
    bergerak; penembusan menyatakan bahwa batas yang menahannya baru saja
    jebol. Keduanya berarah, tapi tren yang jelas mengalahkan penembusan -
    sebuah breakout ke atas di dalam downtrend lebih sering pantulan daripada
    pembalikan, dan memperlakukannya sebagai LONG akan membeli setiap
    pantulan.

    Yang tersisa: kalau trennya belum menentukan, penembusan **boleh**
    memutuskan sendiri. Sebuah range yang jebol ke atas adalah kabar tentang
    arah, dan mengabaikannya berarti diam selama seluruh awal setiap tren.

    Buktinya disebut namanya, bukan disimpulkan pembaca - PASAL 14.8 meminta
    bukti yang mendukung dan yang melawan disebutkan.
    """
    tren = str(getattr(getattr(structure, "trend", None), "value", "") or "")
    tembus = str(getattr(getattr(structure, "breakout", None), "value", "") or "")

    if tren == "UPTREND":
        return Bacaan(interval, Arah.LONG, (f"struktur {interval} uptrend",))
    if tren == "DOWNTREND":
        return Bacaan(interval, Arah.SHORT, (f"struktur {interval} downtrend",))

    arah_tembus = {
        "BREAKOUT_UP": Arah.LONG,
        "BREAKOUT_DOWN": Arah.SHORT,
        # Penembusan palsu adalah bukti ke arah sebaliknya - lihat catatan yang
        # sama di aruna.decision.context_readings.
        "FALSE_BREAKOUT_UP": Arah.SHORT,
        "FALSE_BREAKOUT_DOWN": Arah.LONG,
    }.get(tembus)
    if arah_tembus is not None:
        return Bacaan(
            interval, arah_tembus, (f"{interval} {tembus.lower().replace('_', ' ')}",)
        )

    alasan = f"{interval} tanpa tren maupun penembusan"
    return Bacaan(interval, Arah.NO_SIGNAL, (alasan,))


__all__ = [
    "BATAS",
    "TERPENDEK",
    "Bacaan",
    "Kelas",
    "Lintas",
    "Posisi",
    "TimeframeError",
    "classify",
    "reading_from_structure",
    "urutan",
]
