"""Apa yang boleh masuk ke simulasi (bagian 16.3, 16.14).

Bagian 16.3 menaruh dua kalimat berdampingan: *"MiroFish hanya menerima data
yang telah divalidasi"* dan *"JANGAN memasukkan raw API dump yang tidak
diperlukan."* Yang pertama tentang **mutu**, yang kedua tentang **jumlah**, dan
keduanya dijaga di sini karena keduanya gagal dengan cara yang sama: simulasi
tetap berjalan, tetap menghasilkan skenario yang rapi, dan tidak ada satu pun
tanda bahwa masukannya cacat.

**Mutu.** Data ber-:class:`~aruna.core.enums.DataQuality` selain ``OK``
ditolak, dan ambangnya dipinjam bulat-bulat dari
:attr:`~aruna.core.enums.DataQuality.blocks_signal` - garis yang sudah dipakai
SPEC 5 untuk menolak sinyal. Kalau sebuah bacaan tidak cukup baik untuk
melahirkan sinyal, ia juga tidak cukup baik untuk melahirkan skenario; ambang
kedua yang lebih longgar di sini berarti ARUNA menolak bertindak atas data itu
sambil tetap bersedia bernalar panjang lebar di atasnya.

**Jumlah.** Bidang yang tidak disebut bagian 16.3 dibuang, bukan diteruskan.
Muatan yang melewati :data:`BATAS_BYTE` ditolak dengan pesan yang menyebut
ukurannya - angka itu ada di pesannya supaya yang membacanya tahu seberapa jauh
melewati batas, bukan cuma bahwa ia melewatinya.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aruna.core.enums import DataQuality

__all__ = [
    "BATAS_BYTE",
    "BIDANG_DIIZINKAN",
    "Masukan",
    "MasukanDitolak",
    "susun_masukan",
]


#: Sebelas bidang bagian 16.3, dan tidak satu pun lebih.
#:
#: Diurutkan seperti di spec supaya daftar ini bisa diadu langsung dengannya.
#: ``scenario_question`` ikut di sini karena bagian 16.3 memang menaruhnya
#: sebagai masukan - pertanyaannya bagian dari apa yang disimulasikan, bukan
#: pembungkus di luarnya.
BIDANG_DIIZINKAN = (
    "market_summary",
    "recent_price_structure",
    "volume",
    "market_regime",
    "validated_news",
    "financial_reports",
    "macro_events",
    "sentiment_summary",
    "futures_metrics",
    "agent_analysis_summary",
    "scenario_question",
)

#: Batas ukuran muatan JSON, dalam byte (bagian 16.14).
#:
#: **Kebijakan, bukan pengukuran** - dan ditulis begitu supaya tidak ada yang
#: mengutipnya sebagai temuan. Bagian 16.14 melarang "API overload" dan "LLM
#: token explosion" tanpa menyebut angka, jadi angkanya harus dipilih, dan yang
#: dipilih menyatakan satu hal yang bisa dibantah: enam puluh empat kilobyte
#: adalah puluhan ribu kata. Ringkasan pasar yang tidak muat di dalamnya bukan
#: ringkasan - ia dump yang menyamar, persis yang bagian 16.3 larang.
#:
#: Diukur pada JSON yang ter-encode, bukan pada objek Python-nya: yang membebani
#: API dan token adalah yang dikirim, bukan yang ada di memori.
BATAS_BYTE = 64 * 1024


class MasukanDitolak(ValueError):
    """Masukan yang tidak boleh disimulasikan, berikut alasannya.

    Melempar dan tidak memulangkan ``None``: masukan yang ditolak diam-diam
    akan berlanjut sebagai simulasi di atas nol bidang, dan nol bidang tetap
    menghasilkan tiga skenario dasar (bagian 16.5) yang terlihat sama meyakinkan
    dengan yang punya bukti.
    """


@dataclass(frozen=True, slots=True)
class Masukan:
    """Muatan yang sudah lolos mutu dan ukuran."""

    bidang: dict[str, Any]
    #: Bidang yang dibuang karena tidak disebut bagian 16.3. Disimpan supaya
    #: pemanggil yang mengira sesuatu ikut terkirim bisa memergokinya.
    dibuang: tuple[str, ...] = ()
    ukuran_byte: int = 0

    def ke_json(self) -> str:
        return json.dumps(self.bidang, ensure_ascii=False, sort_keys=True, default=str)


def _ukuran(bidang: dict[str, Any]) -> int:
    return len(
        json.dumps(bidang, ensure_ascii=False, sort_keys=True, default=str).encode()
    )


def susun_masukan(
    mentah: dict[str, Any], *, mutu: DataQuality = DataQuality.OK
) -> Masukan:
    """Muatan bersih, atau :class:`MasukanDitolak`.

    Urutannya disengaja: mutu diperiksa **sebelum** penyaringan bidang. Data
    basi yang disaring rapi tetap data basi, dan memeriksanya belakangan berarti
    menghabiskan kerja pada muatan yang akan ditolak juga.
    """
    if mutu.blocks_signal:
        raise MasukanDitolak(
            f"mutu data {mutu.value} tidak layak disimulasikan (bagian 16.3): "
            f"skenario yang lahir dari data cacat tetap terbaca rapi, dan "
            f"kerapiannya yang menyesatkan"
        )

    bidang = {k: v for k, v in mentah.items() if k in BIDANG_DIIZINKAN}
    dibuang = tuple(sorted(k for k in mentah if k not in BIDANG_DIIZINKAN))

    ukuran = _ukuran(bidang)
    if ukuran > BATAS_BYTE:
        raise MasukanDitolak(
            f"muatan {ukuran} byte melewati batas {BATAS_BYTE} byte "
            f"(bagian 16.14): ringkasan yang tidak muat bukan ringkasan"
        )

    return Masukan(bidang=bidang, dibuang=dibuang, ukuran_byte=ukuran)
