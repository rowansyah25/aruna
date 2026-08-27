"""Satu setup, satu sinyal.

Bar M5 tutup 288 kali sehari. Sebuah setup yang sama - arah yang sama menuju
level yang sama - akan lolos gerbang berkali-kali berturut-turut selama level
itu bertahan, dan operator menerima dua puluh pesan tentang satu gagasan.

Yang ditahan adalah SETUP, bukan simbol dan bukan arah. Kalau harga menembus
dan level targetnya berganti, itu gagasan baru dan boleh bicara segera.
Menahan per simbol akan membungkam gagasan baru; menahan per bar tidak
membungkam apa pun.

Waktu dioper masuk, tidak dibaca dari jam sistem - sama seperti
:class:`~aruna.data.forex.budget.KreditHarian`, dan dengan alasan yang sama:
satu siklus keputusan harus memakai satu bacaan jam dari awal sampai akhir.
"""

from __future__ import annotations

from datetime import datetime, timedelta

#: Jeda bawaan antara dua sinyal untuk setup yang sama.
#:
#: Satu jam adalah dua belas bar M5. Lebih pendek dari ini dan satu gagasan
#: bisa terkirim berkali-kali sebelum pasar sempat menjawabnya sama sekali.
JEDA_BAWAAN = timedelta(hours=1)


class Cooldown:
    """Ingatan pendek tentang setup yang baru saja dikabarkan."""

    def __init__(self, jeda: timedelta = JEDA_BAWAAN) -> None:
        self._jeda = jeda
        self._terakhir: dict[str, datetime] = {}

    def tertahan(self, setup_id: str, saat: datetime) -> bool:
        """``True`` kalau setup ini baru saja dikabarkan."""
        sebelumnya = self._terakhir.get(setup_id)
        if sebelumnya is None:
            return False
        return saat - sebelumnya < self._jeda

    def catat(self, setup_id: str, saat: datetime) -> None:
        """Catat bahwa setup ini baru dikabarkan.

        Dipanggil HANYA saat sinyal benar-benar terbit. Mencatat penolakan
        juga akan membuat satu NO SIGNAL membungkam sinyal sungguhan yang
        menyusul semenit kemudian.
        """
        self._terakhir[setup_id] = saat

    def sisa(self, setup_id: str, saat: datetime) -> timedelta | None:
        """Sisa jeda, atau ``None`` kalau tidak sedang menahan."""
        sebelumnya = self._terakhir.get(setup_id)
        if sebelumnya is None:
            return None
        lewat = saat - sebelumnya
        return self._jeda - lewat if lewat < self._jeda else None


__all__ = ["JEDA_BAWAAN", "Cooldown"]
