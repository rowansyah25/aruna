"""Kenapa sebuah keputusan salah, bukan apa yang terjadi (bagian 12).

`loss_autopsies` sudah menyimpan bukti yang kaya: regime saat keputusan dibuat,
keadaan berita, tingkat risiko, keyakinan, agent yang mendukung dan yang
dibungkam, keberatan yang tak terjawab, veto yang ditolak, dan gerak merugikan
terjauh. Yang tidak ada adalah **namanya**.

:data:`aruna.learning.autopsy.FAILURE_HYPOTHESES` memetakan tiga
``outcome_class`` ke prosa, dan ketiganya menjawab *apa yang terjadi* - bacaan
salah sejak awal, bacaan benar tapi keluarnya tidak, arah benar pada skala waktu
yang lebih panjang. Bagian 12 minta yang lain, dan dari sebelas kategori yang ia
sebut hanya ``TIMING_ERROR`` punya padanan.

**Yang modul ini sengaja tidak lakukan: menebak.** ``FUNDING_DISTORTION``,
``OI_MISREAD``, dan ``LIQUIDITY_EVENT`` ada di kosakata karena bagian 12
menyebutnya - tapi autopsy spot tidak menyimpan funding, open interest, maupun
spread, jadi menghasilkannya dari sini berarti mengarang. Mereka menunggu bukti
yang belum ada, dan sebuah test menyebut mereka supaya penambahnya nanti
menemukan kategorinya sudah siap.

Yang tidak bisa ditentukan menjadi ``OTHER``. ``OTHER`` yang jujur lebih
berguna daripada kategori yang terdengar kaya dan salah - sistem yang
mengklasifikasi setiap kekalahan dengan yakin akan menghasilkan pola yang
seluruhnya buatan sendiri.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

__all__ = ["KEYAKINAN_TINGGI", "RISIKO_MODEL_KELIPATAN", "SebabKalah", "klasifikasi"]


#: Keyakinan yang dianggap tinggi. Terukur 2026-08-21: pita >=90% menang 47,7%
#: sementara pita <50% menang 55,2% - makin yakin, makin sering salah.
KEYAKINAN_TINGGI = 0.9

#: Berapa kali lipat gerak merugikan harus melebihi gerak yang diperkirakan
#: sebelum modelnya - bukan arahnya - yang disebut salah.
RISIKO_MODEL_KELIPATAN = 3.0

#: Regime yang arahnya bertentangan dengan keputusan.
_LAWAN: dict[str, set[str]] = {
    "BUY": {"TRENDING_BEARISH", "BREAKDOWN"},
    "LONG": {"TRENDING_BEARISH", "BREAKDOWN"},
    "SELL": {"TRENDING_BULLISH", "BREAKOUT"},
    "SHORT": {"TRENDING_BULLISH", "BREAKOUT"},
}

#: Keadaan berita yang dianggap guncangan, dalam bentuk kata.
_BERITA_BURUK = {"NEGATIVE", "NEWS_SHOCK", "SHOCK"}

#: `news_state` tersimpan sebagai PROSA, bukan enum. Terukur 2026-08-21 pada
#: 1.433 autopsy: 957 berbunyi ``NO_RECENT_NEWS`` dan sisanya berbentuk
#: ``"2 item(s): 1+ / 0- / 1 unreadable"``. Mencocokkannya dengan kata
#: ``NEGATIVE`` tidak akan pernah kena, dan `NEWS_SHOCK` mustahil menyala -
#: cacat yang hanya terlihat karena klasifikasinya dijalankan atas data nyata.
_POLA_NEGATIF = re.compile(r"(\d+)\s*-")

#: Regime tembusan - kalau harga berbalik sesudahnya, tembusannya palsu.
_TEMBUSAN = {"BREAKOUT", "BREAKDOWN"}


class SebabKalah(StrEnum):
    """Kategori bagian 12, lengkap - termasuk yang belum terjangkau bukti."""

    WRONG_REGIME = "WRONG_REGIME"
    BAD_TECHNICAL_SIGNAL = "BAD_TECHNICAL_SIGNAL"
    NEWS_SHOCK = "NEWS_SHOCK"
    FALSE_BREAKOUT = "FALSE_BREAKOUT"
    LIQUIDITY_EVENT = "LIQUIDITY_EVENT"
    FUNDING_DISTORTION = "FUNDING_DISTORTION"
    OI_MISREAD = "OI_MISREAD"
    AGENT_OVERCONFIDENCE = "AGENT_OVERCONFIDENCE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    RISK_MODEL_ERROR = "RISK_MODEL_ERROR"
    TIMING_ERROR = "TIMING_ERROR"
    OTHER = "OTHER"

    @property
    def penjelasan(self) -> str:
        """Satu kalimat. Kategori tanpa penjelasan memaksa pembacanya menebak
        artinya, dan itu mengembalikan masalah yang modul ini tutup."""
        return _PENJELASAN[self]


_PENJELASAN: dict[SebabKalah, str] = {
    SebabKalah.WRONG_REGIME: (
        "keputusan melawan regime yang terbaca saat itu - arahnya bertentangan "
        "dengan kondisi pasar yang sistem sendiri catat"
    ),
    SebabKalah.BAD_TECHNICAL_SIGNAL: (
        "bacaan teknikal yang mendukung keputusan ini tidak bertahan; agent "
        "yang menentang ternyata benar"
    ),
    SebabKalah.NEWS_SHOCK: (
        "keadaan berita sudah buruk saat keputusan dibuat, dan pasar bergerak "
        "mengikuti berita itu, bukan mengikuti bacaan teknikalnya"
    ),
    SebabKalah.FALSE_BREAKOUT: (
        "level tertembus lalu harga kembali - tembusannya tidak bertahan, dan "
        "keputusan mengikutinya"
    ),
    SebabKalah.LIQUIDITY_EVENT: (
        "likuiditas menguap saat posisi berjalan; belum bisa ditentukan dari "
        "autopsy spot karena spread saat itu tidak tersimpan"
    ),
    SebabKalah.FUNDING_DISTORTION: (
        "funding rate memiringkan hasil; hanya berlaku di jalur futures dan "
        "tidak tersimpan pada autopsy spot"
    ),
    SebabKalah.OI_MISREAD: (
        "arah open interest dibaca terbalik; hanya berlaku di jalur futures "
        "dan tidak tersimpan pada autopsy spot"
    ),
    SebabKalah.AGENT_OVERCONFIDENCE: (
        "keyakinan tinggi tanpa keadaan yang menerangkan kekalahannya - "
        "terukur, pita keyakinan tertinggi justru paling sering salah"
    ),
    SebabKalah.INSUFFICIENT_DATA: (
        "sistem sudah menandai datanya berisiko dan tetap berpendapat"
    ),
    SebabKalah.RISK_MODEL_ERROR: (
        "arahnya tidak sepenuhnya salah, tapi seberapa jauh harga bisa melawan "
        "jauh melebihi yang diperkirakan"
    ),
    SebabKalah.TIMING_ERROR: (
        "arah benar pada skala waktu yang lebih panjang daripada horizon yang "
        "dipilih"
    ),
    SebabKalah.OTHER: (
        "bukti yang tersimpan tidak cukup untuk menyebut satu sebab; "
        "menamainya lebih rinci berarti mengarang"
    ),
}


def klasifikasi(autopsy: Any) -> SebabKalah:
    """Sebab yang paling khusus yang bukti tersimpan benar-benar dukung.

    Urutannya menurun dari yang paling menerangkan. Guncangan berita menang
    atas keyakinan tinggi karena keyakinan tinggi saat berita buruk memang
    seharusnya kalah - menyebutnya "agent terlalu yakin" akan menyalahkan
    lapisan yang salah.
    """
    from aruna.signals.models import OutcomeClass

    regime = _teks(getattr(autopsy, "regime", None))
    arah = _teks(getattr(autopsy, "direction", None))
    kelas = getattr(autopsy, "outcome_class", None)
    berbalik = kelas is OutcomeClass.RIGHT_THEN_REVERSED

    if _berita_buruk(getattr(autopsy, "news_state", None)):
        return SebabKalah.NEWS_SHOCK

    if regime in _LAWAN.get(arah, set()):
        return SebabKalah.WRONG_REGIME

    if regime in _TEMBUSAN and berbalik:
        return SebabKalah.FALSE_BREAKOUT

    if kelas is OutcomeClass.RIGHT_DIRECTION_BAD_TIMING:
        return SebabKalah.TIMING_ERROR

    if _model_risiko_meleset(autopsy):
        return SebabKalah.RISK_MODEL_ERROR

    if _teks(getattr(autopsy, "risk_level", None)) in {"HIGH", "CRITICAL"}:
        return SebabKalah.INSUFFICIENT_DATA

    # Agent yang menentang ternyata benar: keberatannya menamai titik butanya
    # dengan tepat, dan itu lebih menerangkan daripada "terlalu yakin".
    if getattr(autopsy, "unanswered_objections", ()) or getattr(
        autopsy, "dissenters", ()
    ):
        return SebabKalah.BAD_TECHNICAL_SIGNAL

    if float(getattr(autopsy, "confidence", 0.0) or 0.0) >= KEYAKINAN_TINGGI:
        return SebabKalah.AGENT_OVERCONFIDENCE

    return SebabKalah.OTHER


def _berita_buruk(nilai: Any) -> bool:
    """Apakah keadaan berita saat keputusan memuat berita negatif.

    Menerima dua bentuk karena keduanya benar-benar ada: kata seperti
    ``NEGATIVE``, dan prosa seperti ``"2 item(s): 1+ / 0- / 1 unreadable"``
    tempat angka sebelum tanda minus adalah jumlah berita negatif.
    """
    teks = _teks(nilai)
    if not teks:
        return False
    if teks in _BERITA_BURUK:
        return True
    cocok = _POLA_NEGATIF.search(teks)
    return bool(cocok and int(cocok.group(1)) > 0)


def _model_risiko_meleset(autopsy: Any) -> bool:
    """Gerak merugikan jauh melebihi gerak yang diperkirakan.

    Arahnya tidak disalahkan di sini - yang disalahkan adalah perkiraan
    seberapa jauh harga bisa melawan sebelum kembali.
    """
    diperkirakan = getattr(autopsy, "predicted_move_pct", None)
    merugikan = getattr(autopsy, "max_adverse_pct", None)
    if diperkirakan is None or merugikan is None or diperkirakan == 0:
        return False
    return abs(float(merugikan)) >= abs(float(diperkirakan)) * RISIKO_MODEL_KELIPATAN


def _teks(nilai: Any) -> str:
    """Nilai sebagai teks huruf besar, memaafkan enum maupun ``None``."""
    if nilai is None:
        return ""
    return str(getattr(nilai, "value", nilai)).strip().upper()
