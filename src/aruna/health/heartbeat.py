"""Denyut, dan berapa lama ARUNA sempat mati (PASAL 14.38 sebagai HEALTH ALERT).

Ada satu kesalahan baca yang paling mahal di sistem seperti ini, dan ia tidak
melibatkan satu pun angka yang salah: **operator membaca diam sebagai "tidak
ada setup", padahal ARUNA sedang mati.** Keduanya terlihat persis sama di
layar - tidak ada pesan.

**Pesan perpisahan tidak bisa memperbaikinya, dan terukur begitu.** Pada
2026-08-19 log mencatat ``aruna.stopped`` 22 kali dan ``telegram.stopped``
**nol** kali: pesan "ARUNA berhenti" hanya lewat pada penghentian yang rapi,
sementara yang berbahaya - proses dibunuh paksa, crash, listrik mati - tidak
pernah sampai ke jalur itu. Sebuah jaring pengaman yang cuma bekerja pada kasus
yang tidak berbahaya mengajari pembacanya bahwa diam tanpa pesan berarti hidup.

**Jadi yang melapor bukan yang mati, melainkan yang bangun.** Proses yang hidup
membandingkan denyut terakhir dengan sekarang, dan kalau jaraknya cukup jauh
ia mengatakan **jendela mana yang tidak menghasilkan apa-apa**. Laporannya
terlambat - ia baru datang saat ARUNA kembali - dan itu diakui apa adanya.
Yang tidak bisa dilakukan proses yang sudah mati adalah berbicara.

**Ambangnya diturunkan dari irama sistem, bukan dikarang.** Loop futures
merencanakan tiap 900 detik. Jeda yang melebihi itu berarti setidaknya satu
siklus perencanaan penuh tidak pernah terjadi - dan itulah definisi yang tepat
dari "diam yang bukan pendapat". Di bawahnya, paling banter satu siklus
terlambat.

**Yang TIDAK bisa diukur, disebut juga.** Berapa lama ARUNA biasanya mati tidak
bisa dihitung dari log yang ada: 33 dari 38 ``aruna.started`` berasal dari
perintah CLI berumur pendek, dan proses yang diawasi tidak pernah mencatat
akhirnya sendiri. Angka "median mati 18 menit" yang sempat muncul dari log itu
mengukur jeda antar perintah, bukan waktu mati ARUNA. Denyut inilah yang
membuat pengukuran itu mungkin untuk pertama kalinya.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from aruna.core.clock import isoformat

#: Kunci di ``app_state``. Nilainya ``{"at": "<iso8601 UTC>"}``.
HEARTBEAT_KEY = "aruna_heartbeat"

#: Jeda terpendek yang layak dilaporkan, dalam detik.
#:
#: Sembilan ratus detik: satu siklus perencanaan futures penuh. Di bawahnya,
#: paling banter satu siklus terlambat; di atasnya, setidaknya satu siklus tidak
#: pernah terjadi sama sekali - dan jendela itulah yang bisa salah dibaca
#: sebagai "tidak ada setup".
#:
#: Restart rutin tidak akan pernah menyentuhnya: yang tercepat terukur 10,1
#: detik. Sebuah restart yang benar-benar memakan lima belas menit **memang**
#: pantas diberitahukan.
MIN_GAP_SEC = 900.0

#: Aktor yang tercatat di ``app_state`` untuk tiap denyut.
ACTOR = "aruna-heartbeat"


@dataclass(frozen=True, slots=True)
class Jeda:
    """Jendela waktu yang tidak menghasilkan apa pun."""

    since: datetime
    until: datetime

    @property
    def seconds(self) -> float:
        return (self.until - self.since).total_seconds()

    @property
    def duration(self) -> timedelta:
        return self.until - self.since

    @property
    def reportable(self) -> bool:
        return self.seconds >= MIN_GAP_SEC

    def human(self) -> str:
        total = int(self.seconds)
        jam, sisa = divmod(total, 3600)
        menit = sisa // 60
        if jam and menit:
            return f"{jam} jam {menit} menit"
        if jam:
            return f"{jam} jam"
        return f"{menit} menit"

    def line(self) -> str:
        """Kalimat yang menyebut **jendelanya**, bukan hanya durasinya.

        "Mati 3 jam" memberi tahu operator sesuatu tentang mesinnya. "Antara
        14:02 dan 17:14 tidak ada analisis" memberi tahu apa yang harus ia
        lakukan dengan ingatannya tentang jam-jam itu.
        """
        return (
            f"⚠️ ARUNA MATI {self.human()}\n\n"
            f"Antara {self.since:%H:%M} dan {self.until:%H:%M} tidak ada satu "
            f"pun analisis yang dijalankan.\n\n"
            f"Diam di jendela itu BUKAN 'tidak ada setup' - ia ketiadaan "
            f"ARUNA. Jangan membacanya sebagai pendapat."
        )


def gap_of(last: datetime | None, now: datetime) -> Jeda | None:
    """Jeda sejak denyut terakhir, atau ``None`` kalau tidak ada yang bisa
    dikatakan.

    ``None`` pada dua keadaan yang berbeda dan sama-sama benar:

    * **belum pernah ada denyut** - ini pemasangan baru, bukan waktu mati.
      Melaporkan "mati sejak awal waktu" pada penyalaan pertama adalah alarm
      yang isinya hanya kekosongan basis data;
    * **jam mundur** - denyut terakhir berada di masa depan. Itu masalah jam,
      bukan waktu mati, dan "ARUNA mati -3 jam" lebih buruk daripada diam.
      Mesin ini pernah terukur berjalan 12,1 detik di belakang jam bursa, jadi
      jam yang bergeser bukan kemungkinan teoretis di sini.
    """
    if last is None or now <= last:
        return None
    return Jeda(since=last, until=now)


async def beat(state: Any, now: datetime) -> None:
    """Tulis denyut. Kegagalannya sengaja tidak ditangkap di sini.

    Pemanggilnya - loop upkeep - sudah membungkus tiap langkah tick-nya, dan
    penanganan kedua di sini hanya akan menyembunyikan denyut yang berhenti
    ditulis. Denyut yang gagal diam-diam menghasilkan laporan waktu mati yang
    mengarang jendelanya.
    """
    await state.set(HEARTBEAT_KEY, {"at": isoformat(now)}, actor=ACTOR)


async def last_beat(state: Any) -> datetime | None:
    """Denyut terakhir yang tersimpan, atau ``None``."""
    tersimpan = await state.get(HEARTBEAT_KEY)
    if not tersimpan:
        return None
    teks = tersimpan.get("at")
    if not teks:
        return None
    try:
        return datetime.fromisoformat(str(teks).replace("Z", "+00:00"))
    except ValueError:
        # Nilai yang tidak bisa dibaca diperlakukan seperti tidak ada denyut,
        # bukan seperti denyut di tahun nol. Yang kedua akan mengirim alarm
        # "mati 2026 tahun" pada satu baris basis data yang rusak.
        return None


async def check(state: Any, now: datetime) -> Jeda | None:
    """Baca denyut terakhir dan laporkan jedanya kalau layak dilaporkan."""
    jeda = gap_of(await last_beat(state), now)
    return jeda if jeda is not None and jeda.reportable else None


__all__ = [
    "ACTOR",
    "HEARTBEAT_KEY",
    "MIN_GAP_SEC",
    "Jeda",
    "beat",
    "check",
    "gap_of",
    "last_beat",
]
