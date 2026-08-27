"""Jatah kredit Twelve Data, di satu tempat.

Paket gratis memberi 800 kredit per hari dan 8 per menit, dan satu permintaan
``time_series`` berharga satu kredit berapa pun bar yang dikembalikannya.
Angka itu hidup hanya di sini: adapter yang menebar konstanta jatah ke beberapa
berkas akan kehilangan salah satunya saat pemakaiannya berubah.

**Ditolak di sisi kita, bukan ditunggu sampai venue menjawab 429.**  Menunggu
429 berarti kredit itu sudah terpakai hanya untuk diberitahu bahwa kredit
habis - dan adapter sengaja mengeluarkan 429 dari himpunan yang diulang
``HttpFetcher`` supaya yang pertama sampai utuh ke sini alih-alih dihabiskan
oleh tiga percobaan ulang ke dalam rate limit yang sedang aktif.
"""

from __future__ import annotations

from datetime import date, datetime


class KreditHarian:
    """Penghitung dua lapis: per menit dan per hari.

    Waktu dioper masuk, tidak dibaca dari jam sistem, supaya perilakunya bisa
    diuji tanpa menunggu satu menit berlalu - dan supaya satu siklus keputusan
    memakai satu bacaan jam yang sama dari awal sampai akhir.
    """

    def __init__(self, *, per_hari: int = 800, per_menit: int = 8) -> None:
        self._per_hari = per_hari
        self._per_menit = per_menit
        self._hari: date | None = None
        self._terpakai_hari = 0
        self._menit: datetime | None = None
        self._terpakai_menit = 0

    def _gulung(self, saat: datetime) -> None:
        """Reset penghitung yang jendelanya sudah berganti.

        Dibandingkan (``!=``), bukan dikurangkan: jam yang terkoreksi MUNDUR
        tetap menghasilkan jendela yang berbeda, jadi ia menggulung sekali dan
        berhenti - bukan membuka celah kredit gratis dengan selisih negatif.
        """
        hari = saat.date()
        if hari != self._hari:
            self._hari = hari
            self._terpakai_hari = 0
        menit = saat.replace(second=0, microsecond=0)
        if menit != self._menit:
            self._menit = menit
            self._terpakai_menit = 0

    def minta(self, saat: datetime) -> bool:
        """Ambil satu kredit.  ``False`` berarti jatah habis, bukan galat."""
        self._gulung(saat)
        if self._terpakai_hari >= self._per_hari:
            return False
        if self._terpakai_menit >= self._per_menit:
            return False
        self._terpakai_hari += 1
        self._terpakai_menit += 1
        return True

    def sisa(self, saat: datetime) -> int:
        """Kredit harian yang tersisa.  Untuk pesan galat dan pemantauan."""
        self._gulung(saat)
        return max(0, self._per_hari - self._terpakai_hari)


__all__ = ["KreditHarian"]
