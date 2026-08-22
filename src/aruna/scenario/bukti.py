"""Apa yang Phase 16 serahkan ke Phase 14 (bagian 16.1, 16.18).

Bagian 16.18 menutup pasalnya dengan kalimat yang tidak menyisakan tafsiran:
*"Phase 16 tidak menghasilkan FINAL LONG atau FINAL SHORT. Phase 16
menghasilkan SCENARIO EVIDENCE. Final decision tetap berada di Phase 14."*

Kelas di bawah adalah satu-satunya bentuk yang meninggalkan paket ini, dan ia
tidak punya bidang arah - bukan karena lupa, melainkan karena kehadiran satu
bidang bernama ``direction`` akan membuat seluruh pasal di atas tidak berlaku
dalam satu baris. Penjaganya ada di `tests/test_scenario_bukti.py` dan ia
memeriksa seluruh paket, bukan hanya berkas ini: pintu yang dijaga tidak berarti
kalau dindingnya berlubang.

**Label melekat, tidak bisa dilepas.** Bagian 16.1 menuntut tiap keluaran
berlabel ``SIMULATION EVIDENCE``, bukan ``FACT`` dan bukan ``GUARANTEED
PREDICTION``. Labelnya ditulis oleh :meth:`BuktiSkenario.to_dict`, bukan
disimpan sebagai bidang yang bisa diisi pemanggil - bidang yang bisa diisi bisa
diisi salah.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aruna.scenario.adapter import HasilAdapter, StatusSimulasi
from aruna.scenario.banding import Perbandingan, bandingkan
from aruna.scenario.models import CATATAN_BOBOT, LABEL_BUKTI, Skenario
from aruna.scenario.pemicu import Peristiwa

__all__ = [
    "BuktiSkenario",
    "susun_bukti",
]


@dataclass(frozen=True, slots=True)
class BuktiSkenario:
    """Bukti skenario untuk satu aset pada satu waktu.

    Perhatikan apa yang **tidak** ada: arah, rekomendasi, ukuran posisi, harga
    masuk, harga target, stop. Tiap satu di antaranya akan membuat Phase 16
    menjadi mesin keputusan kedua, dan bagian 16.18 menaruh keputusan itu
    seluruhnya di Phase 14.
    """

    market: str
    asset: str
    timestamp: datetime
    pemicu: tuple[str, ...]
    skenario: tuple[Skenario, ...]
    perbandingan: Perbandingan
    #: Keadaan mesin eksternal. ``DEGRADED`` normal - MiroFish belum ada.
    status_eksternal: StatusSimulasi
    catatan_eksternal: str = ""

    @property
    def label(self) -> str:
        """Bagian 16.1. Properti, bukan bidang: yang bisa diisi bisa diisi salah."""
        return LABEL_BUKTI

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": LABEL_BUKTI,
            "market": self.market,
            "asset": self.asset,
            "timestamp": self.timestamp.isoformat(),
            "pemicu": list(self.pemicu),
            "skenario": [s.to_dict() for s in self.skenario],
            "perbandingan": self.perbandingan.to_dict(),
            "bobot_catatan": CATATAN_BOBOT,
            "simulasi_eksternal": {
                "status": self.status_eksternal.value,
                "catatan": self.catatan_eksternal,
            },
        }


def susun_bukti(
    *,
    market: str,
    asset: str,
    pada: datetime,
    pemicu: frozenset[Peristiwa],
    internal: tuple[Skenario, ...],
    eksternal: HasilAdapter,
) -> BuktiSkenario:
    """Gabungkan skenario internal dan eksternal menjadi satu bukti.

    Skenario eksternal hanya ikut kalau :attr:`HasilAdapter.terpakai` - yaitu
    hanya pada ``OK``. Hasil dari simulasi yang kehabisan waktu atau meledak
    dibuang di sini, sekali, alih-alih di tiap pemanggil (bagian 16.13).

    Perbandingannya dihitung atas gabungan, bukan atas masing-masing: dua
    himpunan yang dibandingkan terpisah menghasilkan dua "teratas" yang bisa
    bertentangan, dan bagian 16.9 minta penilaian atas seluruh skenario.
    """
    semua = internal + (eksternal.skenario if eksternal.terpakai else ())

    return BuktiSkenario(
        market=market,
        asset=asset,
        timestamp=pada,
        pemicu=tuple(sorted(p.value for p in pemicu)),
        skenario=semua,
        perbandingan=bandingkan(semua),
        status_eksternal=eksternal.status,
        catatan_eksternal=eksternal.catatan,
    )
