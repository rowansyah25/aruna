"""Korpus ingatan yang boleh dilihat pada satu saat, dibaca sekali (PASAL 15.39).

**Kenapa modul ini ada.** Faktor ``historical`` bagian 18.4 punya bobot tiga -
terbesar kedua di antara faktor bernilai - dan terukur 2026-08-24 ia **tidak
pernah terukur** di jalur spot: ``signals/service.py`` mengoper
``accuracy=None, sample=0`` secara harfiah pada 300 dari 300 snapshot terakhir.
Bahannya ada sejak lama; yang tidak ada pembacanya.

Jalur futures sudah punya pembaca, tapi ia **metode privat** di dalam
``FuturesService`` dan menyeret muatan yang hanya dipakai pesan futures - pola
Phase 12, dimensi teknikal per simbol, jatah manfaat. Menyalinnya ke jalur spot
akan menghasilkan dua pembaca korpus yang harus tetap sepakat soal timeframe
mana yang dipinjam, berapa kandidat yang diambil, dan kapan hasilnya dianggap
cukup. Yang di sini hanya mengambil **korpusnya**, dan kedua jalur memakai
aturan yang sama.

**Biayanya diukur, bukan ditaksir** (2026-08-24, korpus 15m berisi 6.000):

* kueri 63 ms dan pembangunan ``Ingatan`` 75 ms - keduanya sekali per TTL;
* ``bandingkan`` satu sidik terhadap seluruh korpus **99 ms**, dan itu per
  sinyal: dua puluh aset kali tiga horizon menjadi ~5,9 detik per bar 15 menit.
  Nol koma enam persen dari barnya.

**Yang tidak boleh disalahpahami dari modul ini:** ia sering memulangkan "tidak
terukur", dan itu jawaban yang benar. Terukur atas tiga puluh sidik nyata,
sampel yang benar-benar DINILAI di arah yang diambil mencapai ambang
``historical_factor`` hanya pada 4 dari 30 untuk LONG dan **0 dari 30** untuk
SHORT - karena 72% korpusnya berhasil ``NEUTRAL`` (keputusan tak berarah tidak
punya menang atau kalah) dan karena ARUNA hampir tidak pernah SHORT. Itu
keadaan sistemnya, bukan cacat modul ini, dan menurunkan ambangnya supaya angka
ini lebih sering muncul adalah menukar kejujuran dengan tampilan.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any

from aruna.core.logging import get_logger
from aruna.memory.outcome import EJAAN_ARAH, SAMPEL_MINIMUM, Ringkasan, ringkas
from aruna.memory.similarity import AMBANG_MIRIP, bandingkan

log = get_logger("aruna.memory.korpus")

#: Berapa ingatan yang boleh diambil dalam satu pembacaan.
#:
#: Tinggal di sini dan bukan di ``futures/service.py`` sejak 2026-08-24: dua
#: jalur membaca korpus yang sama, dan batas yang berbeda di keduanya berarti
#: dua ukuran sampel di bawah satu nama "rekam jejak".
MEMORY_KANDIDAT = 6000

#: Umur cache korpus. Lima menit, sama dengan
#: :data:`aruna.learning.snapshot.CACHE_TTL_SEC` - dan disamakan dengan sengaja:
#: keduanya menjawab "seberapa basi boleh sebuah bacaan bersama sebelum
#: dibaca ulang", dan dua jawaban berbeda untuk satu pertanyaan cuma menambah
#: hal yang harus diingat.
KORPUS_TTL_SEC = 300.0


@dataclass(frozen=True, slots=True)
class Korpus:
    """Ingatan yang boleh dilihat, beserta keterangan tentang batasnya."""

    daftar: tuple[Any, ...]
    timeframe: str
    #: Timeframe ini dipinjam karena yang diminta belum punya cukup ingatan.
    dipinjam: bool = False
    #: Kandidatnya mencapai :data:`MEMORY_KANDIDAT`; yang tertua terpotong, dan
    #: jumlah sampel yang dilaporkan menjadi batas bawah - bukan jumlah
    #: sebenarnya.
    terpotong: bool = False
    as_of: datetime | None = None

    def __len__(self) -> int:
        return len(self.daftar)


class PembacaKorpus:
    """Membaca korpus sekali per TTL, bukan sekali per sinyal.

    Yang berbeda per sinyal hanyalah kemiripannya, dan itu perhitungan murni
    tanpa database - persis alasan ``FuturesService`` membaca sekali per tick.
    """

    def __init__(self, repo: Any, *, ttl_sec: float = KORPUS_TTL_SEC) -> None:
        self._repo = repo
        self._ttl = ttl_sec
        self._cache: dict[tuple[str, str], tuple[float, Any]] = {}

    async def baca(
        self, *, market: Any, horizon: Any, as_of: datetime
    ) -> Korpus | None:
        """Korpus untuk pasar dan horizon ini, atau ``None``.

        ``None`` ketika tidak ada repositori, tidak ada timeframe yang punya
        cukup ingatan, atau pembacaannya gagal. Ketiganya berarti sama bagi
        pemanggilnya: rekam jejaknya **tidak terukur**, bukan buruk.
        """
        if self._repo is None:
            return None
        pasar = str(getattr(market, "value", market))
        kunci = (pasar, str(getattr(horizon, "value", horizon)))
        sekarang = monotonic()
        simpan = self._cache.get(kunci)
        if simpan is not None and sekarang - simpan[0] < self._ttl:
            return await simpan[1]

        # Yang disimpan tugasnya, bukan hasilnya - lihat
        # `PembacaPembelajaran.baca`: dua puluh pemanggil sampai di cache
        # sebelum ada satu pun yang selesai mengisinya, dan cache yang hanya
        # menyimpan hasil tidak menahan serbuan itu sama sekali.
        tugas = asyncio.ensure_future(self._susun(pasar, horizon, as_of))
        self._cache[kunci] = (sekarang, tugas)
        try:
            return await tugas
        except Exception:
            self._cache.pop(kunci, None)
            log.exception("korpus.baca_gagal", market=pasar)
            return None

    async def _susun(
        self, pasar: str, horizon: Any, as_of: datetime
    ) -> Korpus | None:
        from aruna.db.repositories.memory import ingatan_dari_baris
        from aruna.memory.lookup import horizon_ingatan

        tersedia = await self._repo.hitung_per_timeframe(
            as_of=as_of, market=pasar
        )
        timeframe, dipinjam = horizon_ingatan(
            horizon, tersedia=tersedia, minimum=SAMPEL_MINIMUM
        )
        if timeframe is None:
            return None
        rows, terpotong = await self._repo.cari_terhitung(
            as_of=as_of, market=pasar, timeframe=timeframe,
            limit=MEMORY_KANDIDAT,
        )
        return Korpus(
            daftar=tuple(ingatan_dari_baris(r) for r in rows),
            timeframe=timeframe,
            dipinjam=dipinjam,
            terpotong=terpotong,
            as_of=as_of,
        )


def serupa(korpus: Any, sidik: Any) -> Ringkasan:
    """Ringkasan kasus yang **mirip** kondisi sekarang (PASAL 15.10).

    Ambangnya :data:`~aruna.memory.similarity.AMBANG_MIRIP`, dipinjam bukan
    disalin: "mirip" harus berarti satu hal di seluruh sistem, atau dua laporan
    tentang ingatan yang sama akan menyebut jumlah kasus yang berbeda.
    """
    daftar = getattr(korpus, "daftar", ()) or ()
    cocok = [
        (i, m)
        for i in daftar
        if (m := bandingkan(sidik, i.sidik)).skor >= AMBANG_MIRIP
    ]
    return ringkas(cocok)


def rekam_jejak(ringkasan: Any, arah: Any) -> tuple[float | None, int]:
    """Akurasi kasus serupa untuk ARAH yang diambil, dan sampelnya.

    Untuk :func:`~aruna.signals.quality.historical_factor`, yang menerima
    akurasi 0..1 dan jumlah sampel.

    **Per arah, bukan keseluruhan.** Rekam jejak LONG di kondisi ini tidak
    mengatakan apa pun tentang SHORT, dan meratakannya menghasilkan angka yang
    bukan rekam jejak salah satunya.

    **Sampelnya yang DINILAI, bukan yang cocok.**
    :attr:`~aruna.memory.outcome.Ringkasan.per_arah` menghitung seluruh kasus di
    arah itu termasuk yang hasilnya ``NEUTRAL`` - memakainya akan melaporkan
    rekam jejak lebih tebal daripada yang benar-benar ada. Terukur 2026-08-24:
    satu sidik nyata mencocokkan 161 ingatan, dan hanya **11** di antaranya
    pernah menang atau kalah.

    Gerbang :attr:`~aruna.memory.outcome.Ringkasan.cukup` dihormati lebih dulu:
    Phase 15 menolak mengubah korpus setipis itu menjadi persen, dan mutu yang
    memakainya diam-diam akan menerbitkan angka yang pemiliknya sendiri tolak
    cetak. Ambang kedua - ``needed`` milik ``historical_factor`` - menjawab
    pertanyaan yang berbeda: bukan "boleh disebut?" melainkan "cukup untuk
    dinilai?".
    """
    if ringkasan is None or not getattr(ringkasan, "cukup", False):
        return None, 0
    kunci = EJAAN_ARAH.get(
        str(getattr(arah, "value", arah) or "").strip().upper()
    )
    if kunci is None:
        return None, 0
    persen = (getattr(ringkasan, "win_rate", None) or {}).get(kunci)
    sampel = int((getattr(ringkasan, "dinilai", None) or {}).get(kunci, 0))
    return (None if persen is None else float(persen) / 100.0), sampel


__all__ = [
    "KORPUS_TTL_SEC",
    "MEMORY_KANDIDAT",
    "Korpus",
    "PembacaKorpus",
    "rekam_jejak",
    "serupa",
]
