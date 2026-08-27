"""Satu gagasan, satu sinyal.

Bar M5 tutup 288 kali sehari.  Sebuah gagasan yang sama - arah yang sama
menuju level yang sama - akan lolos gerbang berkali-kali berturut-turut selama
level itu bertahan, dan operator menerima sinyal berulang untuk risiko yang
sama.

**Dibandingkan berdasarkan JARAK, bukan kecocokan penanda.**  Versi pertama
modul ini mencocokkan ``setup_id`` sebagai teks, dan itu gagal dengan mahal.
Diukur dari kerugian nyata operator 2026-08-28: tiga SELL terbit dalam 45
menit untuk gagasan yang sama - target 4595,80 lalu 4597,11 lalu 4597,51 - dan
ketiganya kena stop.  Penandanya berbeda hanya karena level struktur bergeser
sepersekian poin tiap bar.

Membulatkan ke ember tidak memperbaikinya: dua nilai berdekatan tetap bisa
jatuh di sisi berlawanan garis pembagi, dan 4597,11 dengan 4597,51 persis
begitu.  Setiap perbandingan berbasis teks punya cacat batas ini.  Yang tidak
punya adalah bertanya "seberapa jauh", dan menjawabnya dalam ATR.

Waktu dioper masuk, tidak dibaca dari jam sistem - sama seperti
:class:`~aruna.data.forex.budget.KreditHarian`, supaya satu siklus keputusan
memakai satu bacaan jam dari awal sampai akhir.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

#: Jeda bawaan antara dua sinyal untuk gagasan yang sama.
#:
#: Satu jam adalah dua belas bar M5.  Lebih pendek dari ini dan satu gagasan
#: bisa terkirim berkali-kali sebelum pasar sempat menjawabnya sama sekali.
JEDA_BAWAAN = timedelta(hours=1)

#: Seberapa dekat dua target harus berada untuk dianggap gagasan yang SAMA.
#:
#: Setengah ATR: level yang bergerak kurang dari itu adalah level yang sama
#: dibaca ulang pada bar berikutnya, bukan gagasan baru.  Diukur pada kasus
#: yang merugikan operator - 4595,80 sampai 4597,51 adalah rentang 1,71 pada
#: ATR sekitar 4, yaitu 0,43 ATR.
JARAK_SAMA_ATR = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class _Catatan:
    arah: str
    target: Decimal
    saat: datetime


class Cooldown:
    """Ingatan pendek tentang gagasan yang baru saja dikabarkan."""

    def __init__(self, jeda: timedelta = JEDA_BAWAAN) -> None:
        self._jeda = jeda
        self._catatan: list[_Catatan] = []

    def _cocok(
        self, arah: str, target: Decimal, atr: Decimal | None, saat: datetime
    ) -> _Catatan | None:
        """Catatan yang masih dalam jeda DAN targetnya cukup dekat."""
        batas = (atr * JARAK_SAMA_ATR) if atr and atr > 0 else Decimal("0")
        for c in self._catatan:
            if c.arah != arah:
                continue
            if not (timedelta(0) <= saat - c.saat < self._jeda):
                continue
            if abs(c.target - target) <= batas:
                return c
        return None

    def tertahan(
        self,
        arah: str,
        target: Decimal,
        saat: datetime,
        atr: Decimal | None = None,
    ) -> bool:
        """``True`` kalau gagasan ini baru saja dikabarkan."""
        return self._cocok(arah, target, atr, saat) is not None

    def catat(self, arah: str, target: Decimal, saat: datetime) -> None:
        """Catat bahwa gagasan ini baru dikabarkan.

        Dipanggil HANYA saat sinyal benar-benar terbit.  Mencatat penolakan
        juga akan membuat satu NO SIGNAL membungkam sinyal sungguhan yang
        menyusul semenit kemudian.
        """
        self._catatan.append(_Catatan(arah=arah, target=target, saat=saat))
        # Buang yang sudah lewat jeda supaya daftarnya tidak tumbuh selamanya
        # pada proses yang hidup berhari-hari.
        batas = saat - self._jeda
        self._catatan = [c for c in self._catatan if c.saat > batas]

    def sisa(
        self,
        arah: str,
        target: Decimal,
        saat: datetime,
        atr: Decimal | None = None,
    ) -> timedelta | None:
        """Sisa jeda, atau ``None`` kalau tidak sedang menahan."""
        cocok = self._cocok(arah, target, atr, saat)
        if cocok is None:
            return None
        return self._jeda - (saat - cocok.saat)


__all__ = ["JARAK_SAMA_ATR", "JEDA_BAWAAN", "Cooldown"]
