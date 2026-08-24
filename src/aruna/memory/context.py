"""Bukti historis yang dibaca Phase 14 (PASAL 15.20, 15.30, 15.41, 15.42, 15.45).

**Modul ini tidak memutuskan apa pun, dan bentuknya yang menjaga itu.** Tidak
ada bidang ``decision``, tidak ada ``arah_disarankan``, tidak ada apa pun yang
bisa dibaca pemanggil berikutnya sebagai jawaban. Yang dipulangkan adalah
ringkasan, klasifikasi pengaruh, dan jejak siapa saja yang dipakai - PASAL
15.42 menyatakan keputusan final tetap milik Phase 14.

**Terhadap apa "mendukung" diukur, dan kenapa itu bukan lima puluh persen.**
Terukur 2026-08-21 atas 8.366 ingatan sungguhan:

===================  =========
BUY                  49,7%
SELL                 **14,6%**
WAIT                 0,0% (5.030 kasus)
seluruh yang berarah 43,4%
===================  =========

Menilai konteks terhadap titik netral 50% akan menyebut hampir setiap konteks
SHORT sebagai CONTRARY - bukan karena buktinya, melainkan karena titik
bandingnya dikarang. Karena itu ``dasar`` adalah argumen **wajib**, dengan
alasan yang sama seperti ``as_of`` di repositori: sebuah bawaan yang tampak
masuk akal akan dipakai tanpa sadar, dan hasilnya salah tanpa meninggalkan
jejak.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from aruna.memory.outcome import EJAAN_ARAH, Ringkasan, ringkas
from aruna.memory.record import Ingatan
from aruna.memory.similarity import Kemiripan

#: Selisih poin persen dari win rate dasar sebelum sebuah konteks boleh disebut
#: mendukung atau melawan.
#:
#: Sepuluh: pada sampel empat puluhan - ukuran yang benar-benar keluar dari
#: pencarian hari ini - sepuluh poin adalah empat kasus. Di bawah itu yang
#: dibaca adalah derau, dan derau yang diberi nama "SUPPORTIVE" adalah
#: confirmation bias dengan angka di belakangnya (PASAL 15.38).
MARGIN_PENGARUH = 10

#: Berapa banyak id yang dibawa jejak audit. PASAL 14.30 pernah menghasilkan
#: satu baris log 6.000 karakter di proyek ini; lima ratus id di satu baris
#: adalah bentuk yang sama. Jumlah seluruhnya tetap dilaporkan lewat
#: ``ringkasan.total``, jadi yang hilang cuma daftarnya - bukan angkanya.
MAX_JEJAK_ID = 20

#: Dipinjam dari :data:`~aruna.memory.outcome.ARAH`, bukan disalin lagi. Peta
#: ini pernah ada dua kali dengan isi yang identik; peta ketiga - yang dibutuhkan
#: Phase 18 - membuat salinannya jadi tiga tempat yang harus tetap sepakat.
_ARAH = EJAAN_ARAH


class Pengaruh(StrEnum):
    """Bagaimana sejarah berdiri terhadap keputusan yang sedang dibuat.

    ``SUPPORTIVE`` adalah PASAL 15.36 (memory-based opportunity) dan
    ``CONTRARY`` adalah PASAL 15.35 (memory-based warning) - keduanya **bukti**,
    dan tidak satu pun otomatis menjadi NO SIGNAL. Keputusan finalnya tetap
    lewat Phase 14 (PASAL 15.42).
    """

    SUPPORTIVE = "SUPPORTIVE"
    CONTRARY = "CONTRARY"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True, slots=True)
class KonteksHistoris:
    """Hasil mesin konteks (PASAL 15.30) - apa yang ingatan punya untuk
    dikatakan, dan seberapa banyak.

    Beku: konteks yang bisa disunting sesudah disusun berarti jejak auditnya
    tidak membuktikan apa pun tentang keputusan yang memakainya.
    """

    ringkasan: Ringkasan
    pengaruh: Pengaruh
    #: 0-100 (PASAL 15.45). **Bukan probabilitas profit.** Ia mengukur berapa
    #: banyak bukti yang relevan tersedia, bukan seberapa mungkin harga naik.
    kontribusi: int
    memory_ids: tuple[str, ...]
    as_of: datetime
    catatan: tuple[str, ...] = field(default_factory=tuple)
    #: Konteks lintas aset (PASAL 15.18), atau ``None``.
    lintas: Any = None
    #: Pola Phase 12 yang menerangkan kondisi ini (PASAL 15.16), atau ``None``.
    pola: Any = None
    #: Hasil historis pada keadaan berita sekarang (PASAL 15.15), atau ``None``.
    peristiwa: Any = None
    #: ``True`` kalau sejarah sebenarnya berpendapat, tapi pendapatnya tidak
    #: diberi bobot karena timeframe ini terukur tidak membantu (PASAL 15.44).
    #:
    #: Dibedakan dari ``NEUTRAL`` biasa dengan sengaja: "sejarah tidak
    #: berpendapat" dan "pendapat sejarah sengaja tidak dipakai di sini" adalah
    #: dua hal yang sangat berbeda, dan menyatukannya membuat gerbang ini tidak
    #: terlihat oleh siapa pun yang membaca keputusannya.
    digerbangi: bool = False


def susun(
    *,
    arah_sekarang: str,
    cocok: Sequence[tuple[Ingatan, Kemiripan]],
    dasar: Ringkasan,
    as_of: datetime,
    catatan: Sequence[str] = (),
    lintas: Any = None,
    pola: Any = None,
    peristiwa: Any = None,
    manfaat: Any = None,
) -> KonteksHistoris:
    """Susun bukti historis untuk keputusan yang sedang dibuat.

    ``catatan`` datang dari pemanggil: pemotongan kandidat terjadi di
    repositori, dan lapisan murni ini tidak bisa mengetahuinya sendiri. Sampel
    yang dipotong tanpa diberitahukan terbaca persis seperti sampel yang utuh.

    ``manfaat`` adalah putusan PASAL 15.44 untuk timeframe ini
    (:class:`aruna.memory.manfaat.Manfaat`), atau ``None`` kalau pemanggil
    tidak punya. Ketika putusannya bilang ingatan tidak membantu di sini,
    pengaruhnya dipaksa ``NEUTRAL`` - **tapi ringkasan, kasus, dan seluruh
    bukti lain tetap disusun dan tetap dikirim ke operator** (PASAL 15.20,
    15.38). Yang digerbangi adalah bobotnya terhadap keputusan, bukan haknya
    untuk dilihat.

    ``None`` tidak menggerbangi apa pun: pemanggil yang belum tahu bukan
    pemanggil yang sudah mengukur. Yang memutuskan "belum terbukti berarti
    tidak dipakai" adalah :attr:`Manfaat.dipakai`, di tempat angkanya ada.
    """
    ringkasan = ringkas(cocok)
    arah = _ARAH.get(str(arah_sekarang).strip().upper())

    pengaruh = Pengaruh.NEUTRAL
    if ringkasan.cukup and arah is not None:
        milik_kita = ringkasan.win_rate.get(arah)
        milik_dasar = dasar.win_rate.get(arah)
        if milik_kita is not None and milik_dasar is not None:
            selisih = milik_kita - milik_dasar
            if selisih >= MARGIN_PENGARUH:
                pengaruh = Pengaruh.SUPPORTIVE
            elif selisih <= -MARGIN_PENGARUH:
                pengaruh = Pengaruh.CONTRARY

    # Gerbang PASAL 15.44, dipasang SESUDAH pengaruhnya dihitung dengan
    # sengaja: `digerbangi` hanya berarti sesuatu kalau sejarah memang punya
    # pendapat untuk ditahan. Menggerbangi lebih awal akan menandai setiap
    # keputusan yang sejarahnya memang diam.
    digerbangi = False
    catatan = tuple(catatan)
    if manfaat is not None and not manfaat.dipakai:
        if pengaruh is not Pengaruh.NEUTRAL:
            digerbangi = True
            pengaruh = Pengaruh.NEUTRAL
        catatan = (*catatan, manfaat.alasan())

    return KonteksHistoris(
        ringkasan=ringkasan,
        pengaruh=pengaruh,
        digerbangi=digerbangi,
        kontribusi=_kontribusi(ringkasan),
        memory_ids=tuple(
            ingatan.signal_id for ingatan, _ in cocok[:MAX_JEJAK_ID]
        ),
        as_of=as_of,
        catatan=tuple(catatan),
        lintas=lintas,
        pola=pola,
        peristiwa=peristiwa,
    )


def _kontribusi(ringkasan: Ringkasan) -> int:
    """Berapa banyak bukti relevan yang tersedia (PASAL 15.45), 0-100.

    Dua faktor, dan keduanya bisa dijelaskan kepada operator: seberapa besar
    sampelnya terhadap ambang kecukupan, dan seberapa mirip kasus-kasusnya.
    Sampel yang belum cukup memberi **nol** - bukan angka kecil, karena angka
    kecil tetap ikut dibaca sebagai bukti.

    Sengaja **tidak** memakai win rate. Kontribusi yang naik ketika sejarah
    lebih sering menang adalah probabilitas profit yang menyamar - dan PASAL
    15.45 mengejanya: ini bukan probability profit.
    """
    from aruna.memory.outcome import SAMPEL_MINIMUM

    if not ringkasan.cukup:
        return 0
    cukup = min(1.0, ringkasan.total / (SAMPEL_MINIMUM * 5))
    rendah, tinggi = ringkasan.rentang_similarity
    mirip = ((rendah + tinggi) / 2) / 100
    return round(cukup * mirip * 100)


__all__ = [
    "MARGIN_PENGARUH",
    "MAX_JEJAK_ID",
    "KonteksHistoris",
    "Pengaruh",
    "susun",
]
