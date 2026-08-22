"""Apa saja yang boleh keluar lewat Telegram (PASAL 14.38).

Tujuh jenis pesan, dan tidak lebih. PASAL 14.38 menutupnya dengan daftar
larangan yang menyebut bentuk kegagalannya apa adanya: *"Jangan mengirim setiap
candle, setiap polling, setiap agent calculation, setiap protest, setiap API
request, setiap internal score."*

**Yang dilindungi bukan kuota, melainkan perhatian.** Prinsipnya sudah tertulis
di :mod:`aruna.futures.notify` dan berlaku di sini juga: sebuah notifikasi yang
selalu berbunyi diabaikan persis ketika ia akhirnya penting. Setiap pesan yang
tidak masuk daftar ini membeli sedikit perhatian operator dan tidak
mengembalikan apa pun.

**Balasan perintah bukan urusan pasal ini.** ``/plans``, ``/council``,
``/health`` - semuanya dikirim karena operator baru saja memintanya, dan pesan
yang diminta tidak bisa menjadi spam. Yang diatur di sini adalah apa yang
datang **tanpa diminta**.

**Peristiwa proses bukan peristiwa pasar.** "ARUNA menyala" tidak memberi
operator satu pun keputusan untuk diambil: kalau ia menyala, signal akan datang
sendiri. Itu sebabnya ia tidak ada di daftar - dan terukur, ia berbunyi lima
kali dalam tujuh jam pada 2026-08-19, semuanya dari restart rutin.

"ARUNA berhenti" berbeda, dan dibaca sebagai :attr:`Jenis.HEALTH` - diamnya
sistem yang tidak diketahui adalah diam yang salah dibaca sebagai "tidak ada
setup". Tapi baca peringatannya di :data:`CATATAN_MATI` sebelum
mengandalkannya.
"""

from __future__ import annotations

from enum import StrEnum


class Jenis(StrEnum):
    """Tujuh jenis pesan yang boleh berangkat tanpa diminta (PASAL 14.38)."""

    SIGNAL = "FINAL SIGNAL"
    WIN = "WIN"
    LOSS = "LOSS"
    #: Hanya yang penting. Sebuah NO SIGNAL untuk tiap simbol tiap tick adalah
    #: sembilan puluh enam pesan sehari yang tidak mengubah satu keputusan pun.
    NO_SIGNAL = "IMPORTANT NO SIGNAL"
    HEALTH = "HEALTH ALERT"
    PROPOSAL = "MODEL PROPOSAL"
    DAILY = "DAILY REPORT"


#: Kenapa "ARUNA berhenti" tidak boleh diandalkan sebagai pemberitahuan mati.
#:
#: Terukur pada 2026-08-19: ``aruna.stopped`` tercatat 22 kali, dan
#: ``telegram.stopped`` **nol** kali. Pesannya hanya berangkat pada penghentian
#: yang rapi, sementara yang sesungguhnya perlu diketahui - proses dibunuh
#: paksa, crash, listrik mati - justru tidak pernah sampai ke jalur itu.
#:
#: Sebuah jaring pengaman yang hanya bekerja pada kasus yang tidak berbahaya
#: lebih buruk daripada tidak ada: ia mengajari pembacanya bahwa diam tanpa
#: pesan berarti ARUNA masih hidup. Pemberitahuan mati yang benar harus datang
#: dari luar proses yang mati - denyut yang berhenti, bukan pesan perpisahan.
#:
#: **Itu belum dibangun.** Disebut di sini supaya ketiadaannya tercatat, bukan
#: ditemukan kembali oleh siapa pun yang mengandalkan pesan yang tidak datang.
CATATAN_MATI = (
    "pesan 'ARUNA berhenti' hanya terkirim pada penghentian yang rapi; "
    "crash dan pembunuhan paksa tidak melewatinya"
)

#: Yang DILARANG dikirim, dengan kata-kata PASAL 14.38 sendiri.
#:
#: Disimpan sebagai data dan bukan sebagai paragraf komentar karena ia dipakai:
#: test kelengkapan membandingkan daftar ini dengan pasalnya, dan daftar yang
#: hidup di komentar tidak bisa dibandingkan dengan apa pun.
DILARANG: tuple[str, ...] = (
    "setiap candle",
    "setiap polling",
    "setiap agent calculation",
    "setiap protest",
    "setiap API request",
    "setiap internal score",
)


class ChannelError(ValueError):
    """Pesan yang tidak punya tempat di daftar PASAL 14.38."""


def allow(jenis: Jenis) -> Jenis:
    """Penjaga untuk jalur kirim yang tidak diminta.

    Sengaja menerima :class:`Jenis` dan bukan teks bebas: sebuah penjaga yang
    menebak jenis pesan dari isinya akan sesekali salah menebak, dan yang
    salah tebak adalah pesan yang paling tidak biasa - yaitu yang paling
    mungkin penting. Pemanggil menyebutkan jenisnya, dan penyebutan itu yang
    diperiksa.
    """
    if not isinstance(jenis, Jenis):
        raise ChannelError(
            f"{jenis!r} bukan salah satu dari tujuh jenis PASAL 14.38; "
            f"proses internal tidak dikirim ke Telegram"
        )
    return jenis


__all__ = ["CATATAN_MATI", "DILARANG", "ChannelError", "Jenis", "allow"]
