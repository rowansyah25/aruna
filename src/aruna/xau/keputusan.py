"""Gerbang XAU: dari usulan dewan menjadi ``BUY`` / ``SELL`` / ``NO SIGNAL``.

**Tiap penolakan membawa angka penyebabnya.**  Sebuah `NO SIGNAL` yang hanya
berbunyi "ditolak" tidak bisa dibantah dan tidak bisa dipelajari; enam bulan
kemudian tidak ada yang tahu apakah gerbangnya terlalu ketat atau pasarnya
memang sepi.  :class:`SinyalXau` karena itu selalu membawa :attr:`rekap` dan
:attr:`geometri` yang sudah sempat dihitung, bahkan - terutama - saat menolak.

**Gerbang spread sengaja tidak ada di daftar ini.**  Diukur 2026-08-27: Twelve
Data tidak menerbitkan bid/ask untuk XAU/USD, jadi ``spread_bps`` selalu
``None``.  Sebuah gerbang yang membandingkan ``None`` dengan ambang akan selalu
lolos sambil terlihat aktif, dan laporan akan menyebutnya "lulus" - lebih buruk
daripada tidak ada gerbang sama sekali.  :attr:`SinyalXau.spread_diukur`
menyatakan keadaan sebenarnya supaya laporan bisa berkata **tidak aktif**.

Yang menegakkan lantai dua ATR adalah berkas ini, bukan
:mod:`aruna.xau.geometri` - supaya angka yang menyebabkan penolakan
(``target_atr``) ikut tersimpan bersama penolakannya.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from aruna.agents.deliberation import Deliberation
from aruna.core.enums import Decision
from aruna.xau.bukti import BuktiXau
from aruna.xau.cooldown import Cooldown
from aruna.xau.geometri import MIN_TARGET_ATR, Geometri, rakit_geometri
from aruna.xau.suara import RekapSuara, ke_keputusan_xau, rekap

#: Kontradiksi maksimum yang masih diloloskan.
#:
#: 0,5 berarti minoritas paling banyak seperempat dari yang bersuara: tiga
#: setuju satu menentang lolos, dua setuju dua menentang tidak.  Diukur di
#: antara yang BERSUARA saja - agen yang abstain tidak dihitung sebagai
#: perselisihan.
MAX_KONTRADIKSI = 0.5

#: Risk-reward minimum.
#:
#: Ditegakkan atas RR yang dihitung dari level struktur sungguhan, bukan dari
#: kelipatan ATR - lihat :mod:`aruna.xau.geometri`.  Kalau targetnya dikarang,
#: gerbang ini akan lolos setiap kali sambil terlihat bekerja.
MIN_RR = 1.5


@dataclass(frozen=True, slots=True)
class SinyalXau:
    """Satu keputusan XAU, dengan seluruh angka yang menghasilkannya."""

    keputusan: Decision
    setup_id: str
    #: Terisi hanya saat menolak.  Kosong berarti ada sinyal.
    alasan: str | None = None
    rekap: RekapSuara | None = None
    geometri: Geometri | None = None
    confidence: float | None = None
    #: ``False`` berarti gerbang spread TIDAK AKTIF, bukan lulus.
    spread_diukur: bool = False

    @property
    def ada_sinyal(self) -> bool:
        return self.keputusan.is_directional


def setup_id_untuk(symbol: str, arah: Decision, target: Decimal) -> str:
    """Penanda satu gagasan, untuk DIBACA - bukan untuk dibandingkan.

    Sengaja TIDAK memuat waktu, supaya baris yang tersimpan bisa dikelompokkan
    per gagasan saat dibaca kembali.

    **Cooldown TIDAK memakai penanda ini.**  Membandingkan teks selalu punya
    cacat batas: dua target berdekatan bisa menghasilkan penanda berbeda -
    yang persis merugikan operator 2026-08-28 - dan membulatkannya ke ember
    hanya memindahkan batasnya, tidak menghapusnya.  Yang membandingkan JARAK
    adalah :class:`~aruna.xau.cooldown.Cooldown`.
    """
    return f"{symbol}:{arah.value}:{target:.2f}"


def putuskan(
    *,
    symbol: str,
    arah: Decision,
    confidence: float,
    rekap_suara: RekapSuara | None,
    geometri: Geometri | None,
    saat: datetime,
    spread_bps: Decimal | None = None,
    cooldown: Cooldown | None = None,
) -> SinyalXau:
    """Terapkan gerbang XAU.  Berhenti di penolakan pertama."""
    setup = (
        setup_id_untuk(symbol, arah, geometri.target)
        if geometri is not None and arah.is_directional
        else f"{symbol}:{arah.value}:-"
    )
    umum = {
        "setup_id": setup,
        "rekap": rekap_suara,
        "geometri": geometri,
        "confidence": confidence,
        "spread_diukur": spread_bps is not None,
    }

    def tolak(alasan: str) -> SinyalXau:
        return SinyalXau(keputusan=Decision.NO_SIGNAL, alasan=alasan, **umum)

    if not arah.is_directional:
        return tolak("dewan tidak mengusulkan arah")

    if rekap_suara is None or rekap_suara.kontradiksi is None:
        return tolak("tidak ada agen yang mengambil arah")

    if rekap_suara.kontradiksi > MAX_KONTRADIKSI:
        return tolak(
            f"kontradiksi {rekap_suara.kontradiksi:.2f} di atas "
            f"{MAX_KONTRADIKSI:.2f} ({rekap_suara.setuju} setuju, "
            f"{rekap_suara.menentang} menentang)"
        )

    if geometri is None:
        return tolak("tidak ada level struktur di arah tujuan; jaraknya tak diketahui")

    if geometri.target_atr < MIN_TARGET_ATR:
        return tolak(
            f"target {geometri.target_atr:.2f} ATR di bawah lantai "
            f"{MIN_TARGET_ATR} ATR - satu ATR adalah pergerakan khas"
        )

    if geometri.rr < MIN_RR:
        return tolak(f"RR {geometri.rr:.2f} di bawah {MIN_RR}")

    # Dibandingkan berdasarkan JARAK target, bukan kecocokan penanda: level
    # struktur bergeser sepersekian poin tiap bar, dan penanda yang berbeda
    # karena itu membuat satu gagasan lolos berkali-kali. Lihat
    # `aruna.xau.cooldown`.
    if cooldown is not None and cooldown.tertahan(
        arah.value, geometri.target, saat, geometri.atr
    ):
        sisa = cooldown.sisa(arah.value, geometri.target, saat, geometri.atr)
        return tolak(f"setup ini baru dikabarkan; jeda tersisa {sisa}")

    if cooldown is not None:
        cooldown.catat(arah.value, geometri.target, saat)
    return SinyalXau(keputusan=arah, alasan=None, **umum)


def putuskan_dari_dewan(
    deliberation: Deliberation,
    bukti: BuktiXau,
    harga: Decimal,
    *,
    symbol: str = "XAU/USD",
    spread_bps: Decimal | None = None,
    cooldown: Cooldown | None = None,
    bobot: dict[str, float] | None = None,
) -> SinyalXau:
    """Jalur produksi: hasil dewan → sinyal XAU.

    ``bobot`` adalah keandalan terukur per agen dari koreksi diri.  Ia
    menimbang kontradiksi: perbedaan pendapat dari agen yang terbukti membaca
    pasar dengan benar berbobot lebih.  Kosong sampai sepuluh hasil pertama
    terselesaikan, dan saat kosong tiap suara bernilai sama.
    """
    arah = ke_keputusan_xau(deliberation.outcome)
    berarah = arah.is_directional
    return putuskan(
        symbol=symbol,
        arah=arah,
        confidence=deliberation.confidence,
        rekap_suara=rekap(deliberation, arah, bobot) if berarah else None,
        geometri=rakit_geometri(bukti, arah, harga) if berarah else None,
        saat=deliberation.decided_at,
        spread_bps=spread_bps,
        cooldown=cooldown,
    )


__all__ = [
    "MAX_KONTRADIKSI",
    "MIN_RR",
    "SinyalXau",
    "putuskan",
    "putuskan_dari_dewan",
    "setup_id_untuk",
]
