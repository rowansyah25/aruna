"""Kapan masuk, dan syarat apa yang harus terpenuhi (PASAL 14.19, 14.20).

Arah dan waktu adalah dua pertanyaan yang berbeda, dan menjawabnya sebagai satu
pertanyaan menghasilkan dua kesalahan sekaligus: setup LONG yang bagus dibuang
karena harganya sedang tanggung, atau harga tanggung dikejar karena arahnya
benar.

PASAL 14.19 memisahkan keduanya: *"decision tetap LONG, tetapi timing entry
belum optimal."* Jadi timing di modul ini **tidak pernah mengubah arah**.
:meth:`Rencana.final` mengembalikan arah yang sama apa pun timing-nya - itulah
bentuk yang diminta kalimat penutup PASAL 14.19: *"Jika sistem membutuhkan
keputusan final tanpa menunggu: LONG / SHORT / NO SIGNAL."*

**Kenapa kata-katanya bahasa Indonesia, bukan "WAIT FOR PULLBACK".** PASAL 1
dan 15 melarang kosakata internal keluar, dan :func:`aruna.notify.verdict
.guard_public` menolak setiap pesan yang memuat kata utuh ``WAIT`` - penjaga
itu ada karena ``WAIT`` sebagai *keputusan* adalah penundaan yang menyamar
sebagai jawaban. Menuliskan timing dalam bahasa Inggris apa adanya akan
membuat setiap pesan dengan entry tertunda ditolak penjaganya sendiri.
Konsepnya PASAL 14.19 dipertahankan utuh; yang berubah hanya bahasanya, dan
seluruh pesan ARUNA memang berbahasa Indonesia.

**Timing yang bukan "masuk sekarang" WAJIB punya syarat (PASAL 14.20).**
"Tunggu pullback" tanpa zona harga bukan nasihat - ia perintah menunggu sesuatu
yang tidak disebutkan. Rencana seperti itu ditolak di sini, bukan dikirim
dengan bagian yang kosong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from aruna.decision.lifecycle import Umur
from aruna.decision.score import Arah


class Timing(StrEnum):
    """PASAL 14.19, dalam kosakata yang boleh keluar.

    ``NO SIGNAL`` sengaja **tidak** ada di sini meskipun disebut di daftar
    PASAL 14.19: ia keputusan, bukan waktu masuk. Menaruhnya di enum ini
    membuat sebuah keputusan NO SIGNAL bisa membawa timing - dan pertanyaan
    "kapan masuk" tidak punya jawaban ketika tidak ada yang dimasuki. Itu
    persis pencampuran yang PASAL 14.19 tulis untuk dihindari.
    """

    NOW = "MASUK SEKARANG"
    PULLBACK = "TUNGGU PULLBACK"
    BREAKOUT = "TUNGGU KONFIRMASI BREAKOUT"
    REJECTION = "TUNGGU KONFIRMASI REJECTION"

    @property
    def immediate(self) -> bool:
        return self is Timing.NOW


class TimingError(ValueError):
    """Rencana masuk yang tidak bisa dijalankan siapa pun."""


@dataclass(frozen=True, slots=True)
class Syarat:
    """Syarat PASAL 14.20: apa yang harus terjadi sebelum entry berlaku.

    Harus bisa diperiksa. Sebuah syarat yang berbunyi "tunggu momen yang lebih
    baik" tidak pernah terpenuhi dan tidak pernah gagal - ia hanya membuat
    signal menggantung sampai kedaluwarsa.
    """

    #: Batas bawah zona harga yang ditunggu.
    zone_low: Decimal | None = None
    zone_high: Decimal | None = None
    #: Konfirmasi yang harus muncul, dalam satu kalimat.
    confirmation: str = ""

    def __post_init__(self) -> None:
        if self.zone_low is not None and self.zone_high is not None:
            if self.zone_low > self.zone_high:
                raise TimingError(
                    f"zona harga terbalik: {self.zone_low} > {self.zone_high}"
                )
        elif (self.zone_low is None) != (self.zone_high is None):
            # Setengah zona bukan zona. "Harga kembali ke 64.000-" akan dibaca
            # sebagai apa pun yang di bawahnya, termasuk nol.
            raise TimingError("zona harga hanya punya satu sisi")
        if self.zone_low is None and not self.confirmation.strip():
            raise TimingError(
                "syarat kosong: sebutkan zona harga, konfirmasi, atau keduanya"
            )

    def line(self) -> str:
        bagian: list[str] = []
        if self.zone_low is not None and self.zone_high is not None:
            bagian.append(f"harga kembali ke {self.zone_low:,} - {self.zone_high:,}")
        if self.confirmation.strip():
            bagian.append(self.confirmation.strip())
        return " DAN ".join(bagian)


@dataclass(frozen=True, slots=True)
class Rencana:
    """Arah, waktu masuknya, dan syaratnya - sebagai satu benda yang konsisten.

    Konsistensinya dijaga di :meth:`__post_init__` alih-alih dipercayakan ke
    pemanggil. Kombinasi yang tidak masuk akal - NO SIGNAL yang membawa zona
    harga, "tunggu pullback" tanpa zona - tidak pernah terbentuk, jadi tidak
    ada lapisan di bawah yang perlu memeriksanya lagi.
    """

    decision: Arah
    timing: Timing | None = None
    condition: Syarat | None = None

    def __post_init__(self) -> None:
        if self.decision is Arah.NO_SIGNAL:
            if self.timing is not None or self.condition is not None:
                raise TimingError(
                    "NO SIGNAL tidak punya waktu masuk - tidak ada yang dimasuki"
                )
            return
        if self.timing is None:
            raise TimingError(f"{self.decision.value} tanpa waktu masuk")
        if self.timing.immediate:
            if self.condition is not None:
                raise TimingError(
                    "MASUK SEKARANG yang bersyarat bukan masuk sekarang"
                )
        elif self.condition is None:
            # PASAL 14.20. Tanpa ini, pesannya menyuruh operator menunggu
            # sesuatu yang tidak pernah disebutkan.
            raise TimingError(
                f"{self.timing.value} wajib menyebut syaratnya (PASAL 14.20)"
            )

    def final(self) -> Arah:
        """Keputusan tanpa menunggu (PASAL 14.19).

        Timing tidak pernah mengubah arah. Sebuah sistem yang menurunkan LONG
        menjadi NO SIGNAL karena harganya sedang tanggung sedang menjawab
        pertanyaan yang berbeda dari yang ditanyakan.
        """
        return self.decision

    @property
    def waiting(self) -> bool:
        return self.timing is not None and not self.timing.immediate

    def line(self) -> str:
        if self.timing is None:
            return f"{self.decision.mark} {self.decision.value}"
        return (
            f"{self.decision.mark} {self.decision.value} - "
            f"{self.timing.value}"
        )

    def report(
        self, umur: Umur | None = None, now: datetime | None = None
    ) -> list[str]:
        """Blok ENTRY TIMING, sebagai baris."""
        baris = ["⏱ WAKTU MASUK", "", f"  {self.line()}"]
        if self.waiting:
            baris += [
                "",
                "  Arahnya tidak berubah; yang belum pas waktunya.",
            ]
        if self.condition is not None:
            baris += ["", "  Syarat yang harus terpenuhi:", f"    {self.condition.line()}"]
            # PASAL 14.20: "Jika kondisi tidak terjadi: Signal dapat EXPIRE."
            # Disebut bersama syaratnya, bukan di blok lain - syarat tanpa
            # batas waktu terbaca seperti syarat yang berlaku selamanya.
            if umur is not None and now is not None:
                baris.append(f"    Kalau tidak terjadi: {umur.line(now)}")
            else:
                baris.append("    Kalau tidak terjadi, signal ini kedaluwarsa.")
        return baris


__all__ = ["Rencana", "Syarat", "Timing", "TimingError"]
