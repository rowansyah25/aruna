"""Menyuapi pemindai dengan bar yang benar-benar tersimpan (PASAL 14, 15).

Lapisan tipis, dan tipisnya disengaja: :func:`aruna.scanner.events.scan` murni
- masuk bar, keluar peristiwa - supaya seluruh aritmetikanya bisa diuji tanpa
database. Yang tidak murni hanya pembacaannya, dan itu semua ada di sini.

Interval yang dipindai adalah yang paling halus yang benar-benar dijaga
mutakhir oleh refresher. Memindai interval yang tidak disegarkan berarti
membaca bar kemarin dan menyebutnya peristiwa hari ini.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aruna.core.enums import Horizon, Market
from aruna.core.logging import get_logger
from aruna.scanner.events import MIN_BASELINE_BARS, ScanResult, ScanThresholds, scan_symbol

log = get_logger("aruna.scanner.service")

#: Berapa bar dibaca per simbol. Cukup di atas :data:`MIN_BASELINE_BARS` supaya
#: garis dasarnya tidak dibentuk dari jumlah minimum - sebuah rata-rata dari
#: dua puluh bar sah, tapi rapuh terhadap satu pencilan.
LOOKBACK_BARS = MIN_BASELINE_BARS * 3


class ScannerService:
    """Baca bar terbaru per simbol, jalankan pemindai, kembalikan hasilnya."""

    def __init__(
        self,
        *,
        universe: Any,
        market_data: Any,
        market: Market = Market.CRYPTO,
        interval: Horizon = Horizon.M15,
        thresholds: ScanThresholds | None = None,
    ) -> None:
        self._universe = universe
        self._market_data = market_data
        self._market = market
        self._interval = interval
        self._thresholds = thresholds or ScanThresholds()

    async def scan(self, moment: datetime | None = None) -> list[ScanResult]:
        """Satu pemindaian atas seluruh aset aktif di market ini.

        Kegagalan satu simbol tidak menghentikan sisanya, dan simbol yang gagal
        dibaca dilaporkan sebagai ``scanned=False`` dengan alasannya - bukan
        dihilangkan dari daftar, yang akan membuat pemindaian yang separuh
        gagal terbaca seperti pemindaian yang bersih.
        """
        assets = await self._universe.assets(market=self._market)
        out: list[ScanResult] = []
        for asset in assets:
            try:
                bars = await self._market_data.candles(
                    asset.id, self._interval, limit=LOOKBACK_BARS, closed_only=True
                )
            except Exception as exc:  # noqa: BLE001 - one symbol must not stop the rest
                log.warning(
                    "scanner.read_failed",
                    symbol=asset.symbol,
                    error=f"{type(exc).__name__}: {exc}"[:160],
                )
                out.append(
                    ScanResult(
                        symbol=asset.symbol,
                        events=(),
                        usable_bars=0,
                        scanned=False,
                        reason=f"bar tidak terbaca: {type(exc).__name__}",
                    )
                )
                continue
            out.append(
                scan_symbol(asset.symbol, bars, thresholds=self._thresholds)
            )
        return out


__all__ = ["LOOKBACK_BARS", "ScannerService"]
