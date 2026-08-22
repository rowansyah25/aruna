"""Bagian 🧠 MARKET MEMORY di laporan harian (PASAL 15.43, 15.44).

**Yang tidak dihitung di sini, dan kenapa.** PASAL 15.44 meminta perbandingan
keputusan *dengan* memory melawan keputusan *tanpa* memory. Perbandingan itu
butuh keputusan yang cukup banyak untuk dibandingkan - dan terukur 2026-08-21,
jalur futures baru punya **17** hasil yang benar-benar menang atau kalah dari
182 rencana yang diresolusi.

Menghitung "memory contribution low" dari tujuh belas kasus akan menghasilkan
angka percaya diri tanpa dasar, dan itu persis yang PASAL 15.44 coba cegah.
Jadi yang disiapkan adalah bahannya, plus penolakan yang eksplisit -
:func:`bisa_dibandingkan` - supaya perbandingannya tidak berjalan diam-diam di
atas sampel yang belum ada.

Diamnya pun disebut di laporan. Tidak menyebutkan apa pun akan terbaca seolah
perbandingannya sudah dilakukan dan hasilnya biasa saja.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Berapa hasil yang dibutuhkan sebelum perbandingan dengan/tanpa memory boleh
#: dihitung sama sekali.
#:
#: Jauh di atas :data:`aruna.memory.outcome.SAMPEL_MINIMUM`: membandingkan DUA
#: populasi menuntut lebih banyak daripada meringkas satu - tiap sisi butuh
#: sampelnya sendiri, dan selisih di antara keduanya butuh ruang untuk terlihat
#: di atas derau.
SAMPEL_PERBANDINGAN = 100


def bisa_dibandingkan(hasil_tersedia: int) -> bool:
    """Apakah perbandingan dengan/tanpa memory sudah boleh dihitung."""
    return hasil_tersedia >= SAMPEL_PERBANDINGAN


@dataclass(frozen=True, slots=True)
class IngatanHarian:
    """Keadaan ingatan pada satu hari, seringkas mungkin (PASAL 15.43)."""

    baru: int
    total: int
    per_timeframe: dict[str, int]
    per_mutu: dict[str, int]
    #: Ingatan yang hasilnya menang atau kalah. **Dipisahkan dari ``total``
    #: dengan sengaja**: delapan ribu ingatan yang sebagian kedaluwarsa bukan
    #: delapan ribu pelajaran, dan angka yang tidak memisahkan keduanya membuat
    #: korpus terdengar lebih tebal daripada yang bisa dipakai.
    bisa_mengajari: int
    rentang: tuple[datetime, datetime] | None
    #: Hasil evaluasi PASAL 15.44, atau ``None`` kalau belum dijalankan.
    #:
    #: Terukur 2026-08-21 atas 1.671 keputusan historis dengan disiplin
    #: ``as_of``: SUPPORTIVE 43%, CONTRARY 40%, selisih **+3 poin** - di bawah
    #: ambang, jadi memory belum menambah apa pun yang bisa diukur. Itu
    #: hasilnya, dan pasalnya justru meminta ARUNA mendeteksinya.
    evaluasi: Any = None

    def report(self) -> list[str]:
        """Baris laporan harian. Ringkas - PASAL 15.43 melarang menampilkan
        seluruh memory, dan blok yang panjang berhenti dibaca."""
        baris = ["🧠 MARKET MEMORY", ""]
        baris += [f"🆕 Ingatan baru hari ini: {self.baru}"]
        baris += [f"📚 Total ingatan: {self.total}"]
        baris += [f"🎓 Yang bisa mengajari: {self.bisa_mengajari}"]

        if self.per_timeframe:
            isi = " | ".join(
                f"{tf} {n}" for tf, n in sorted(self.per_timeframe.items())
            )
            baris.append(f"⏱ Per timeframe: {isi}")
        if self.per_mutu:
            isi = " | ".join(
                f"{m} {n}" for m, n in sorted(self.per_mutu.items())
            )
            baris.append(f"⭐ Mutu: {isi}")
        if self.rentang:
            awal, akhir = self.rentang
            baris.append(f"📆 Rentang: {awal:%d %b} – {akhir:%d %b}")  # noqa: RUF001

        # PASAL 15.44, dinyatakan dan bukan didiamkan.
        #
        # Evaluasinya sendiri yang berbicara kalau sudah dijalankan - termasuk
        # ketika jawabannya "tidak menambah apa-apa". Pasalnya mengejanya:
        # jangan memaksakan penggunaan memory.
        if self.evaluasi is not None:
            baris.append(f"⚖️ {self.evaluasi.ringkas()}")
        elif not bisa_dibandingkan(self.bisa_mengajari):
            baris.append(
                f"⚖️ Kontribusi memory: belum bisa dinilai "
                f"({self.bisa_mengajari}/{SAMPEL_PERBANDINGAN} hasil)"
            )
        else:
            baris.append("⚖️ Kontribusi memory: belum dihitung")
        baris.append("")
        return baris


__all__ = ["SAMPEL_PERBANDINGAN", "IngatanHarian", "bisa_dibandingkan"]
