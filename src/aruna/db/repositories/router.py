"""Penyimpanan pilihan router (bagian 17.9, 17.27, 17.44, 17.52).

**Satu baris per PILIHAN, bukan per perhitungan.** Router berjalan tiap siklus
atas dua puluh aset; menyimpan tiap peringkat berarti mengulang pelajaran
``market_snapshots``, yang menjadi 62% basis data ini dengan nol pembaca.

**Penolakan ikut tersimpan, dan itu bukan kelengkapan melainkan keharusan.**
Nol karena tidak ada strategi yang cocok dan nol karena fasenya mati terlihat
sama persis dari luar - yang pertama normal sementara yang kedua bug. Baris
tanpa ``alasan_kosong`` tidak bisa membedakan keduanya.

Dan penolakan akan sering terjadi. Diukur 2026-08-23 sebelum router menyala:
1.860 dari 9.437 bacaan 15m berlabel ``UNCERTAIN``, 453 ``HIGH_VOLATILITY`` dan
49 ``ANOMALY`` tanpa strategi mana pun, ditambah tiap aset yang cuma punya satu
horizon segar - keyakinan tertingginya 48 sementara ambangnya 50.

**Tidak pernah ditulis ulang** (bagian 17.27). Rezim berganti sesudah sebuah
pilihan tercatat adalah hal biasa; mengubah catatannya membuat seluruh evaluasi
Phase 12 mengukur keputusan yang tidak pernah diambil siapa pun. ``INSERT
IGNORE``, bukan ``ON DUPLICATE KEY UPDATE``: siklus yang berjalan dua kali pada
bar yang sama tidak menghasilkan dua baris, dan yang **pertama** yang bertahan.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from aruna.core.enums import Market
from aruna.db.types import to_mysql_datetime
from aruna.router.label import VERSI_ROUTER
from aruna.router.putusan import PutusanRouter
from aruna.router.rezim import PetaRezim

__all__ = ["LEBAR_ALASAN_KOSONG", "RouterRepository"]


#: Lebar kolom ``alasan_kosong``, disebut supaya pemotongannya disengaja.
#:
#: MySQL dalam mode ketat **menolak seluruh barisnya** ketika sebuah string
#: kepanjangan - jadi pilihan yang alasannya panjang tidak tersimpan sama
#: sekali, dan yang hilang justru baris yang paling perlu dibaca. Dipotong di
#: sini dengan penanda, bukan diserahkan kepada database.
LEBAR_ALASAN_KOSONG = 255


def _potong(teks: str, lebar: int) -> str:
    if len(teks) <= lebar:
        return teks
    return teks[: lebar - 1] + "…"


class RouterRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def simpan(
        self,
        putusan: PutusanRouter,
        *,
        asset_id: int,
        market: Market,
        symbol: str,
        peta: PetaRezim,
        dipilih_pada: datetime,
        stabilitas: float | None,
    ) -> int:
        """Catat satu pilihan router, termasuk ketika tidak ada yang dipilih.

        ``dipilih_pada`` dioper, tidak dibaca dari jam di dalam sini. Yang
        kedua membuat pilihan tidak bisa diuji ulang, dan membuat replay
        Phase 9 menulis stempel hari ini pada keputusan tahun lalu.

        ``stabilitas`` boleh ``None`` - riwayatnya terlalu pendek untuk diukur.
        Disimpan NULL dan bukan nol: nol berarti rezimnya berkedip terus, dan
        itu kesimpulan yang jauh lebih dramatis daripada "baru satu bacaan".
        """
        champion = putusan.champion
        challenger = putusan.challenger
        return await self._db.execute(
            "INSERT IGNORE INTO router_pilihan "
            "(asset_id, market_code, symbol, dipilih_pada, regime_primary, "
            " regime_confidence, regime_stability, interval_hilang, champion, "
            " champion_skor, challenger, challenger_skor, alasan_kosong, "
            " alasan, versi_router) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            asset_id,
            market.value,
            symbol,
            to_mysql_datetime(dipilih_pada),
            peta.primary,
            round(peta.primary_confidence, 3),
            None if stabilitas is None else round(stabilitas, 3),
            ",".join(peta.interval_hilang) or None,
            None if champion is None else champion.kode,
            None if champion is None else champion.skor,
            None if challenger is None else challenger.kode,
            None if challenger is None else challenger.skor,
            _potong(putusan.alasan_kosong, LEBAR_ALASAN_KOSONG) or None,
            json.dumps(list(putusan.alasan), ensure_ascii=False)
            if putusan.alasan
            else None,
            VERSI_ROUTER,
        )
