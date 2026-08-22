"""Funding rate dan open interest yang disimpan sebagai deret (bagian 16.2).

Dua dari tiga belas pemicu bagian 16.2 tidak pernah bisa menyala karena
angkanya tidak ada di mana pun: ``futures-loop`` mengambil keduanya dari Binance
REST tiap siklus, memakainya di memori, lalu membuangnya.

**Deret, bukan potret.** Anomali open interest adalah PERUBAHAN, dan perubahan
butuh dua titik. Satu baris yang ditimpa terus tidak akan pernah bisa menjawab
"naik berapa persen".

**Yang ditulis hanya yang dibaca.** Bukan seluruh :class:`FuturesSnapshot` -
pelajaran Phase 15.1 yang dibayar dengan 216 MB kolom yang tidak punya satu pun
pembaca.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from aruna.core.logging import get_logger
from aruna.db.pool import Database
from aruna.db.types import as_utc, to_mysql_datetime

log = get_logger(__name__)

__all__ = ["BacaanFutures", "FuturesMetricsRepository"]


class BacaanFutures:
    """Funding dan perubahan open interest terbaru untuk satu simbol."""

    __slots__ = ("funding_rate", "perubahan_oi_pct")

    def __init__(
        self,
        funding_rate: Decimal | None = None,
        perubahan_oi_pct: Decimal | None = None,
    ) -> None:
        self.funding_rate = funding_rate
        self.perubahan_oi_pct = perubahan_oi_pct


class FuturesMetricsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def simpan(self, snapshot: Any) -> bool:
        """Satu baris dari satu ``FuturesSnapshot``.

        ``INSERT IGNORE``: siklus yang dijalankan dua kali pada stempel yang
        sama tidak boleh melahirkan deret ganda, dan yang menahannya kunci
        UNIQUE di database - bukan pemeriksaan di sini yang bisa kalah balapan.

        Memulangkan ``False`` alih-alih melempar ketika snapshotnya tidak punya
        satu pun dari keduanya: baris yang seluruh isinya NULL menambah
        panjang deret tanpa menambah satu pun jawaban.
        """
        funding = getattr(snapshot, "funding", None)
        oi = getattr(snapshot, "open_interest", None)
        if funding is None and oi is None:
            return False

        n = await self._db.execute(
            "INSERT IGNORE INTO futures_metrics "
            "(symbol, captured_at, funding_rate, funding_time, next_funding_at, "
            " open_interest, oi_notional) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            snapshot.symbol,
            to_mysql_datetime(snapshot.captured_at),
            getattr(funding, "rate", None),
            to_mysql_datetime(funding.funding_time) if funding else None,
            to_mysql_datetime(funding.next_funding_time)
            if funding and funding.next_funding_time
            else None,
            getattr(oi, "open_interest", None),
            getattr(oi, "notional", None),
        )
        return bool(n)

    async def terbaru(
        self, *, sekarang: datetime, umur_maksimum: timedelta
    ) -> dict[str, BacaanFutures]:
        """Bacaan terbaru per simbol, berikut perubahan open interest-nya.

        Dua baris terakhir per simbol, karena perubahan butuh pembanding. Yang
        lebih tua dari ``umur_maksimum`` diperlakukan tidak terbaca - bacaan
        funding dari enam jam lalu bukan funding sekarang.
        """
        batas = to_mysql_datetime(sekarang - umur_maksimum)
        baris = await self._db.fetch(
            "SELECT symbol, captured_at, funding_rate, open_interest "
            "FROM futures_metrics WHERE captured_at >= %s "
            "ORDER BY symbol, captured_at DESC",
            batas,
        )

        riwayat: dict[str, list[dict[str, Any]]] = {}
        for r in baris:
            daftar = riwayat.setdefault(r["symbol"], [])
            if len(daftar) < 2:
                r["captured_at"] = as_utc(r["captured_at"])
                daftar.append(r)

        keluar: dict[str, BacaanFutures] = {}
        for simbol, v in riwayat.items():
            keluar[simbol] = BacaanFutures(
                funding_rate=v[0]["funding_rate"],
                perubahan_oi_pct=_perubahan(v),
            )
        return keluar


def _perubahan(riwayat: list[dict[str, Any]]) -> Decimal | None:
    """Perubahan open interest dalam persen, atau ``None``.

    ``None`` untuk tiap keadaan yang tidak bisa dihitung - satu bacaan saja,
    nilai yang hilang, atau pembagi nol. Nol yang dikarang di sini akan
    terbaca sebagai "open interest datar", dan itu pernyataan yang tidak
    pernah dibuat siapa pun.
    """
    if len(riwayat) < 2:
        return None
    baru, lama = riwayat[0].get("open_interest"), riwayat[1].get("open_interest")
    if baru is None or lama is None or Decimal(lama) <= 0:
        return None
    return (Decimal(baru) - Decimal(lama)) / Decimal(lama) * 100
