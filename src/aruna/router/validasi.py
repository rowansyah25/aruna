"""Apakah pilihan router bertahan lintas periode (bagian 17.41 - 17.43).

**Mesinnya tidak dibangun di sini.** :mod:`aruna.backtest.walkforward` sudah
punya pembagi periode, penghitung fold, holdout yang dijaga, dan putusan
KONSISTEN/TIDAK KONSISTEN yang sudah dipikirkan. Modul ini hanya memberinya
bahan: hasil sinyal yang teratribusi ke pilihan router.

Rencana Phase 17 menunda bagian ini dengan alasan "menyambungkannya adalah
pekerjaan tersendiri dengan gerbangnya sendiri". Gerbangnya ternyata sudah ada -
``MIN_FOLD_SAMPLE`` dan ``MIN_FOLDS`` - dan keduanya menolak berbicara ketika
sampelnya kurang, yang persis perilaku yang dituntut.

Apa yang sebenarnya diukur
==========================

**Konsistensi lintas periode, bukan penjagaan terhadap overfitting**, dan
catatan mesin itu sendiri menyatakannya: ARUNA tidak mencocokkan parameter apa
pun. Router memilih dari katalog tetap memakai aturan tetap; tidak ada yang
disetel ke data. Jadi fold yang berbeda-beda tidak berarti "kelebihan
mencocokkan" melainkan **aturannya berperilaku sangat berbeda di pasar yang
berbeda** - dan itu justru yang bagian 17.42 minta diketahui.

Ia menjadi penjaga overfitting nanti, ketika parameter router mulai dipilih atas
kekuatan angka backtest. Holdout-nya disiapkan sekarang justru karena holdout
yang dibuat SESUDAH titik itu tidak bernilai apa-apa.

Yang tidak boleh
================

Per-strategi dipisahkan, dan tidak dijumlahkan diam-diam menjadi satu angka
router. Sebuah router yang benar di satu strategi dan salah di dua lainnya
punya rata-rata yang terlihat wajar, dan rata-rata itu tidak menggambarkan satu
pun dari ketiganya - pelajaran yang sama dengan ``regime=ALL`` di Task 3.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from aruna.backtest.walkforward import (
    Fold,
    FoldResult,
    Split,
    WalkForwardReport,
    split_period,
)

__all__ = [
    "LIPATAN",
    "bagi_hasil",
    "laporan_per_strategi",
    "susun_split",
]


#: Berapa fold yang periode router dibagi.
#:
#: Empat, sama dengan bawaan :func:`~aruna.backtest.walkforward.split_period` -
#: dan meminjamnya disengaja. ``MIN_FOLDS`` di sana adalah tiga, jadi empat
#: memberi satu fold cadangan sebelum putusannya berhenti bisa dibuat sama
#: sekali.
LIPATAN = 4


def susun_split(baris: Any, *, folds: int = LIPATAN) -> Split | None:
    """Bagi rentang waktu yang ADA menjadi fold, atau ``None``.

    Rentangnya diambil dari data, bukan dari jam sekarang: periode yang
    membentang ke masa depan menghasilkan fold-fold kosong di ujung, dan
    fold kosong terbaca sebagai "sampelnya kurang" - keluhan yang benar atas
    sebab yang salah.

    ``None`` ketika belum ada dua titik waktu yang berbeda. Itu jawaban yang
    sah: sebuah periode yang seluruhnya satu saat tidak bisa dibagi.
    """
    saat = sorted(w for w in (_waktu(r) for r in baris) if w is not None)
    if len(saat) < 2 or saat[0] == saat[-1]:
        return None
    # Ujungnya dilewatkan satu satuan terkecil, dan itu bukan kerapian.
    # Ember foldnya setengah terbuka (`start <= t < end`) supaya satu hasil
    # tidak pernah masuk dua fold - dan dengan `end` tepat di titik terakhir,
    # hasil TERAKHIR jatuh di luar seluruh ember. Ditemukan test 2026-08-24:
    # dua puluh baris masuk, sembilan belas terhitung.
    return split_period(
        saat[0], saat[-1] + timedelta.resolution, folds=folds
    )


def bagi_hasil(baris: Any, *, split: Split) -> WalkForwardReport:
    """Hitung tiap fold dari hasil yang teratribusi ke pilihan router.

    Holdout dihitung TERPISAH dan tidak pernah ikut ke dalam fold - itu
    seluruh gunanya. Melaporkannya bersama yang lain akan menghabiskan
    satu-satunya data yang belum tersentuh.
    """
    laporan = WalkForwardReport(
        results=[_isi(f, baris) for f in split.folds],
        holdout=_isi(split.holdout, baris),
    )
    return laporan


def laporan_per_strategi(
    baris: Any, *, folds: int = LIPATAN
) -> dict[str, WalkForwardReport]:
    """Satu laporan per strategi, tidak dijumlahkan.

    **Dipisahkan dengan sengaja.** Router yang benar di satu strategi dan salah
    di dua lainnya punya rata-rata yang terlihat wajar, dan rata-rata itu tidak
    menggambarkan satu pun dari ketiganya.

    Split-nya dihitung **sekali dari seluruh baris**, bukan per strategi:
    fold yang batasnya berbeda-beda membuat "fold 2" berarti periode yang
    berbeda untuk tiap strategi, dan laporannya tidak bisa disandingkan.
    """
    split = susun_split(baris, folds=folds)
    if split is None:
        return {}

    per_kode: dict[str, list[Any]] = {}
    for r in baris:
        kode = str(r.get("champion") or "").strip()
        if kode:
            per_kode.setdefault(kode, []).append(r)
    return {
        kode: bagi_hasil(anggota, split=split)
        for kode, anggota in sorted(per_kode.items())
    }


def _isi(fold: Fold, baris: Any) -> FoldResult:
    anggota = [r for r in baris if _dalam(fold, _waktu(r))]
    menang = sum(1 for r in anggota if r.get("result") == "WIN")
    tuntas = sum(1 for r in anggota if r.get("result") in ("WIN", "LOSS"))
    pnl = sum(
        (Decimal(str(r.get("net_pnl") or 0)) for r in anggota), Decimal(0)
    )
    return FoldResult(
        fold=fold,
        # `resolved` hanya yang benar-benar menang atau kalah. Sinyal yang
        # belum tuntas bukan prediksi yang salah - ia prediksi yang belum
        # dinilai, dan menghitungnya menurunkan akurasi tiap fold yang
        # kebetulan memuat banyak posisi terbuka.
        resolved=tuntas,
        correct=menang,
        published=len(anggota),
        net_pnl=str(pnl),
    )


def _dalam(fold: Fold, saat: datetime | None) -> bool:
    # Batas atas eksklusif supaya sebuah hasil tidak pernah masuk dua fold.
    return saat is not None and fold.start <= saat < fold.end


def _waktu(r: Any) -> datetime | None:
    saat = r.get("resolved_at")
    return saat if isinstance(saat, datetime) else None
