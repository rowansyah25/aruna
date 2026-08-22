"""Apakah memory benar-benar membantu (PASAL 15.44).

Pasalnya meminta perbandingan keputusan **dengan** memory melawan keputusan
**tanpa** memory. Memory baru mulai mempengaruhi keputusan pada 2026-08-21,
jadi belum ada satu pun hasil yang bisa diatribusikan kepadanya - menunggu
berbulan-bulan adalah satu jawaban yang sah.

Jawaban yang lain, dan yang PASAL 15.40 justru wajibkan, adalah **simulasi
historis**: untuk tiap keputusan lama, hitung konteks yang **waktu itu**
tersedia, lalu bandingkan hasilnya. Ingatan yang resolusinya terjadi sesudah
keputusan itu dibuat tidak boleh ikut - dan itu satu-satunya hal yang membuat
angkanya berarti sama sekali. Tanpa disiplin ``as_of``, evaluasi ini akan
selalu melaporkan bahwa memory sangat membantu, dan angkanya akan naik justru
ketika kebocorannya makin parah.

**Yang dibandingkan hanya SUPPORTIVE melawan CONTRARY.** ``NEUTRAL`` berarti
memory tidak berpendapat; memasukkannya ke salah satu sisi akan mengukur
sesuatu yang lain dan menyebutnya kontribusi memory.

Dan hasil apa pun adalah hasil. Kalau selisihnya kecil, memory tidak menambah
apa-apa - PASAL 15.44 mengejanya: *jangan memaksakan penggunaan memory*. Kalau
selisihnya **terbalik**, itu temuan yang lebih penting lagi, dan
menyembunyikannya akan membiarkan memory dipakai ke arah yang salah.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from aruna.memory.context import Pengaruh
from aruna.memory.record import Hasil

#: Selisih poin persen sebelum sebuah perbedaan layak disebut ada.
#:
#: Sepuluh, sama dengan :data:`aruna.memory.context.MARGIN_PENGARUH`: yang lebih
#: kecil tidak bisa dibedakan dari derau pada sampel yang ARUNA punya, dan derau
#: yang diberi nama "memory membantu" adalah persis yang PASAL 15.44 cegah.
SELISIH_BERARTI = 10

#: Kasus minimum di **tiap** sisi. Perbandingan dua populasi menuntut keduanya
#: punya isi - seribu kasus di satu sisi dan nol di sisi lain tidak
#: membandingkan apa pun.
SAMPEL_SISI = 30


@dataclass(frozen=True, slots=True)
class Evaluasi:
    """Hasil perbandingan, beserta kejujuran tentang sampelnya."""

    mendukung_menang: int
    mendukung_kalah: int
    melawan_menang: int
    melawan_kalah: int

    @property
    def mendukung_total(self) -> int:
        return self.mendukung_menang + self.mendukung_kalah

    @property
    def melawan_total(self) -> int:
        return self.melawan_menang + self.melawan_kalah

    @property
    def cukup(self) -> bool:
        return (
            self.mendukung_total >= SAMPEL_SISI
            and self.melawan_total >= SAMPEL_SISI
        )

    @property
    def mendukung_pct(self) -> int | None:
        if not self.mendukung_total:
            return None
        return round(self.mendukung_menang * 100 / self.mendukung_total)

    @property
    def melawan_pct(self) -> int | None:
        if not self.melawan_total:
            return None
        return round(self.melawan_menang * 100 / self.melawan_total)

    @property
    def selisih(self) -> int | None:
        """Poin persen; positif berarti sejarah yang mendukung lebih sering benar."""
        if self.mendukung_pct is None or self.melawan_pct is None:
            return None
        return self.mendukung_pct - self.melawan_pct

    @property
    def membantu(self) -> bool:
        selisih = self.selisih
        return bool(self.cukup and selisih is not None and selisih >= SELISIH_BERARTI)

    @property
    def terbalik(self) -> bool:
        """Yang **dilawan** sejarah justru lebih sering benar.

        Bukan kegagalan pengukuran - itu hasilnya, dan yang paling perlu
        diketahui dari semuanya.
        """
        selisih = self.selisih
        return bool(
            self.cukup and selisih is not None and selisih <= -SELISIH_BERARTI
        )

    def ringkas(self) -> str:
        """Satu baris untuk operator."""
        if not self.cukup:
            return (
                f"kontribusi memory: belum bisa dinilai "
                f"(SUPPORTIVE {self.mendukung_total}, "
                f"CONTRARY {self.melawan_total}; butuh {SAMPEL_SISI} tiap sisi)"
            )
        arah = (
            "memory membantu" if self.membantu
            else "memory berlawanan" if self.terbalik
            else "memory tidak menambah apa-apa"
        )
        return (
            f"{arah}: SUPPORTIVE {self.mendukung_pct}% dari "
            f"{self.mendukung_total}, CONTRARY {self.melawan_pct}% dari "
            f"{self.melawan_total} (selisih {self.selisih:+d} poin)"
        )


def evaluasi_pengaruh(
    pasangan: Sequence[tuple[Pengaruh, Hasil]],
) -> Evaluasi:
    """Bandingkan hasil keputusan berdasarkan pengaruh memory (PASAL 15.44).

    ``pasangan`` adalah (pengaruh yang **waktu itu** terhitung, nasib
    keputusannya). Yang menjamin "waktu itu" bukan fungsi ini melainkan
    pemanggilnya - lihat catatan modul tentang ``as_of``.
    """
    hitung = {
        (Pengaruh.SUPPORTIVE, Hasil.WIN): 0,
        (Pengaruh.SUPPORTIVE, Hasil.LOSS): 0,
        (Pengaruh.CONTRARY, Hasil.WIN): 0,
        (Pengaruh.CONTRARY, Hasil.LOSS): 0,
    }
    for pengaruh, hasil in pasangan:
        kunci = (pengaruh, hasil)
        if kunci in hitung:
            hitung[kunci] += 1

    return Evaluasi(
        mendukung_menang=hitung[(Pengaruh.SUPPORTIVE, Hasil.WIN)],
        mendukung_kalah=hitung[(Pengaruh.SUPPORTIVE, Hasil.LOSS)],
        melawan_menang=hitung[(Pengaruh.CONTRARY, Hasil.WIN)],
        melawan_kalah=hitung[(Pengaruh.CONTRARY, Hasil.LOSS)],
    )


__all__ = ["SAMPEL_SISI", "SELISIH_BERARTI", "Evaluasi", "evaluasi_pengaruh"]
