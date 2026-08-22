"""Gerbang terakhir: tiga keluaran, tidak lebih (bagian 8, 25, 27).

Terukur 2026-08-21: ``WAIT`` tersimpan sebagai keputusan pada 3.871 dari 6.441
sesi council dan 5.981 dari 10.494 baris ``signal_snapshots``.

**Yang tidak berubah karena modul ini: apa yang operator lihat.**
``notify.verdict.PUBLIC_DECISION`` sudah memetakan ``WAIT`` ke ``"NO SIGNAL"``
sejak lama, jadi kata itu tidak pernah sampai ke Telegram. Yang berubah adalah
catatan yang tersimpan berhenti membawa keputusan keempat - dan dengan itu,
setiap kueri yang mengelompokkan per keputusan berhenti membelah "diam" menjadi
dua ember yang artinya sama bagi pembacanya.

**``WAIT`` tetap sah sebagai suara agent.** Bagian 25 mengizinkan uncertainty
internal, dan membuangnya dari kosakata agent akan memaksa tiap agent berpihak
pada tiap tick - menghasilkan sistem yang lebih percaya diri, bukan lebih
pintar.

**Sebabnya dipindahkan, bukan dibuang.** ``council/veto.py`` mengeja bedanya:
NO_SIGNAL berarti *input tidak bisa dipercaya*, WAIT berarti *tidak ada setup
sekarang*. Meruntuhkan keduanya menjadi satu keputusan menghapus keterangan itu
kecuali ia pindah ke tempat lain, dan :class:`SebabDiam` adalah tempatnya.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aruna.core.enums import Decision

__all__ = ["FINAL", "Final", "SebabDiam", "finalkan"]


#: Satu-satunya keputusan yang boleh tersimpan atau terkirim (bagian 25).
FINAL: frozenset[Decision] = frozenset(
    {Decision.BUY, Decision.SELL, Decision.NO_SIGNAL}
)


class SebabDiam(StrEnum):
    """Kenapa keputusannya NO SIGNAL.

    Empat sebab, dan keempatnya **bisa ditentukan** dari keadaan yang benar-
    benar ada saat finalisasi. Bagian 8 menyebut daftar yang lebih panjang -
    bukti lemah, regime tidak jelas, data basi, risk/reward tidak layak - tapi
    tidak semuanya terbaca di titik ini, dan menebaknya akan menghasilkan
    klasifikasi yang terlihat kaya dan tidak bisa dipercaya.
    """

    #: Veto ditegakkan sesudah ditinjau (SPEC 19).
    DIBLOKIR_VETO = "DIBLOKIR_VETO"
    #: Mesin no-trade memblokir (SPEC 33).
    DIBLOKIR_NO_TRADE = "DIBLOKIR_NO_TRADE"
    #: Analisis berjalan wajar dan tidak menemukan sisi - bekas ``WAIT``.
    TIDAK_ADA_SETUP = "TIDAK_ADA_SETUP"
    #: NO_SIGNAL yang datang bukan dari kedua gerbang di atas: inputnya sendiri
    #: yang tidak bisa dipercaya.
    INPUT_TAK_TERPERCAYA = "INPUT_TAK_TERPERCAYA"


@dataclass(frozen=True, slots=True)
class Final:
    """Keputusan yang boleh keluar, dan kenapa kalau ia diam."""

    keputusan: Decision
    sebab: SebabDiam | None = None


def finalkan(
    keputusan: Decision,
    *,
    diblokir_veto: bool = False,
    diblokir_no_trade: bool = False,
) -> Final:
    """Keputusan final yang dijamin salah satu dari tiga.

    Menjamin **kosakata**, bukan arah: yang berarah lewat apa adanya. Finalizer
    yang bisa membalik arah bukan finalizer melainkan mesin keputusan kedua,
    dan tidak seorang pun memintanya.

    Tidak menyentuh angka keyakinan - itu milik
    :mod:`aruna.learning.kalibrator` (bagian 9). Dua modul yang menyentuh angka
    yang sama adalah dua yang suatu saat tidak sepakat.
    """
    if keputusan.is_directional:
        return Final(keputusan=keputusan)

    if diblokir_veto:
        # Veto lebih dulu daripada no-trade ketika keduanya menyala: SPEC 19
        # berjalan sebelum SPEC 33, dan yang dilaporkan adalah yang
        # menghentikan keputusan lebih dulu.
        sebab = SebabDiam.DIBLOKIR_VETO
    elif diblokir_no_trade:
        sebab = SebabDiam.DIBLOKIR_NO_TRADE
    elif keputusan is Decision.WAIT:
        sebab = SebabDiam.TIDAK_ADA_SETUP
    else:
        sebab = SebabDiam.INPUT_TAK_TERPERCAYA

    return Final(keputusan=Decision.NO_SIGNAL, sebab=sebab)
