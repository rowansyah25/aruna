"""Bentuk keputusan final (PASAL 14.2, 14.43).

ARUNA wajib memberi keputusan yang jelas: **LONG, SHORT, atau NO SIGNAL**.
``WAIT`` dilarang - bukan karena katanya buruk, melainkan karena ia
mengembalikan pertanyaannya kepada operator. "Tunggu" tidak memberitahu apa pun
tentang apa yang sedang dilihat ARUNA, dan operator yang menerimanya berada di
tempat yang persis sama dengan sebelum bertanya.

Yang boleh menunggu adalah **waktu masuknya**. PASAL 14.43 memberi contohnya
sendiri: Decision LONG, Entry Timing WAIT FOR PULLBACK. Itu keputusan plus
syarat; ``WAIT`` sendirian adalah ketiadaan keputusan yang berpakaian seperti
keputusan.

**Modul ini menolak, bukan menerjemahkan.** Sebuah lapisan yang masih
mengeluarkan ``WAIT`` harus terlihat sebagai kesalahan, bukan diam-diam
dibetulkan di hilir - karena yang diam-diam dibetulkan tidak pernah diperbaiki.
Menerjemahkan ``WAIT`` menjadi ``NO SIGNAL`` di sini akan menyembunyikan
sumbernya selamanya, dan keduanya berarti hal yang berbeda: yang pertama
berarti belum diputuskan, yang kedua berarti sudah diputuskan untuk tidak
mengambil posisi.
"""

from __future__ import annotations

from aruna.decision.score import Arah
from aruna.decision.timing import Rencana, Syarat, Timing


class FinalError(ValueError):
    """Keputusan final yang bukan LONG, SHORT, atau NO SIGNAL."""


#: Token yang pernah muncul sebagai "keputusan" di sistem ini dan tidak satu
#: pun menjawab pertanyaan operator.
#:
#: ``FLAT`` ikut di sini dengan sengaja: itu bentuk ``WAIT`` di jalur futures,
#: dan ia truthy - sebuah nilai yang ada, sah, dan artinya persis "tidak
#: berarah". Kelas kesalahan yang sama dengan ``confidence=0``, dan sudah empat
#: kali menghasilkan cacat di sistem ini.
TERLARANG: frozenset[str] = frozenset({"WAIT", "FLAT", "HOLD", "NETRAL"})

_PETA: dict[str, Arah] = {
    "BUY": Arah.LONG,
    "LONG": Arah.LONG,
    "SELL": Arah.SHORT,
    "SHORT": Arah.SHORT,
    "NO_SIGNAL": Arah.NO_SIGNAL,
    "NO SIGNAL": Arah.NO_SIGNAL,
}


def arah_dari(raw: object) -> Arah:
    """Ubah apa pun yang dikeluarkan lapisan bawah menjadi satu dari tiga.

    Menerima :class:`~aruna.decision.score.Arah`, ``str``, atau objek apa pun
    yang punya ``.value`` - tiga bentuk yang benar-benar beredar di sistem ini
    (``Decision``, ``PositionSide``, dan kalimat mentah dari log).
    """
    if isinstance(raw, Arah):
        return raw
    teks = str(getattr(raw, "value", raw) or "").strip().upper()
    if teks in TERLARANG:
        raise FinalError(
            f"{teks!r} bukan keputusan final - PASAL 14.43 hanya mengizinkan "
            "LONG, SHORT, atau NO SIGNAL. Penundaan masuk ke entry timing."
        )
    if teks not in _PETA:
        raise FinalError(f"keputusan tidak dikenali: {teks!r}")
    return _PETA[teks]


def finalize(
    raw: object,
    *,
    timing: Timing | None = None,
    condition: Syarat | None = None,
) -> Rencana:
    """Keputusan final beserta waktu masuknya, kalau ada arahnya.

    Sisa aturannya dijaga :class:`~aruna.decision.timing.Rencana` sendiri dan
    **tidak diulang di sini**: arah wajib punya waktu masuk (PASAL 14.19), dan
    waktu masuk yang menunggu wajib menyebut syaratnya (PASAL 14.20).
    Mengulangnya di sini akan menghasilkan dua tempat yang bisa berselisih -
    dan yang kalah dalam perselisihan seperti itu selalu yang tidak diuji.

    Karena itu pemanggil bisa menerima ``FinalError`` **atau** ``TimingError``;
    keduanya ``ValueError``.
    """
    arah = arah_dari(raw)
    if arah is Arah.NO_SIGNAL and (timing is not None or condition is not None):
        raise FinalError(
            "NO SIGNAL tidak punya waktu masuk - posisinya tidak diambil"
        )
    return Rencana(decision=arah, timing=timing, condition=condition)


__all__ = ["TERLARANG", "FinalError", "arah_dari", "finalize"]
