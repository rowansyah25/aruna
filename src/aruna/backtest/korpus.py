"""Korpus keputusan lintas regime, dibangun dari candle yang sudah tersimpan.

**Kenapa modul ini ada.** Sampai 2026-08-25 setiap pertanyaan tentang "agen mana
yang benar-benar menyumbang" berhenti di tempat yang sama: korpus keputusan
ARUNA yang hidup hanya delapan hari, seluruhnya satu regime naik. Temuan apa pun
darinya menguap begitu diuji - pita momentum yang terlihat +6,0 poin ternyata
+13,1 lalu -1,9, dan REVERSAL yang terlihat sebagai satu-satunya agen bekerja
(+6,1) ternyata +5,3 lalu -0,5.

Yang tidak disadari selama itu: **candle 1d-nya menyimpan 506 hari** - tujuh
belas bulan yang memuat satu bulan naik, tujuh turun, dan sembilan menyamping.
Council bisa diputar ulang di atasnya, dan hasilnya korpus 9.805 keputusan
dengan 88.245 opini agen.

**Ini bukan backtest PnL.** :class:`~aruna.backtest.engine.BacktestEngine`
menjawab "berapa hasilnya kalau dijalankan"; modul ini menjawab "siapa yang
benar, dan seberapa sering, dibandingkan pasar yang sama". Yang pertama butuh
aturan exit, ukuran posisi, dan biaya; yang kedua tidak - dan mencampurnya
membuat kesimpulan tentang AGEN bergantung pada pilihan stop.

**Berita dan fundamental sengaja kosong**, persis seperti ``BacktestEngine``:
keduanya tidak tersedia point-in-time, dan memakai yang hari ini adalah
look-ahead paling parah - berita justru sebab harga bergerak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from aruna.agents.context import DecisionContext
from aruna.analysis.engine import AnalysisEngine
from aruna.backtest.window import MIN_BARS, Bar, Window
from aruna.core.enums import Horizon, Market
from aruna.core.logging import get_logger
from aruna.council.session import Council

log = get_logger("aruna.backtest.korpus")

#: Berapa bar ke depan dipakai menilai satu keputusan.
#:
#: Satu, dan itu bukan pilihan gaya: horizon keputusan futures adalah 1d (lihat
#: :data:`~aruna.upkeep.korelasi.HORIZON_KEPUTUSAN`), jadi satu bar 1d ke depan
#: adalah persis jendela yang keputusannya klaim.
BAR_KE_DEPAN = 1


@dataclass(frozen=True, slots=True)
class Opini:
    """Satu agen, satu keputusan, dan apa yang pasar lakukan sesudahnya."""

    symbol: str
    pada: datetime
    agen: str
    arah: str
    keyakinan: float
    council: str
    gerak_pct: float

    @property
    def berarah(self) -> bool:
        return self.arah in ("BUY", "SELL")

    @property
    def benar(self) -> bool | None:
        """``None`` untuk yang tak berarah - tidak ada sisi untuk benar."""
        if not self.berarah:
            return None
        return (self.gerak_pct > 0) if self.arah == "BUY" else (self.gerak_pct < 0)


@dataclass(slots=True)
class Korpus:
    """Kumpulan opini, beserta keputusan uniknya."""

    opini: list[Opini] = field(default_factory=list)
    gagal: int = 0

    @property
    def keputusan(self) -> dict[tuple[str, datetime], float]:
        """Gerak pasar per keputusan, tanpa duplikasi antar agen.

        Garis dasar HARUS dihitung dari sini, bukan dari :attr:`opini`: satu
        keputusan muncul sekali per agen, jadi menghitungnya per opini akan
        menimbang berlebih keputusan yang agennya kebetulan banyak bersuara.
        """
        return {(o.symbol, o.pada): o.gerak_pct for o in self.opini}

    @property
    def garis_dasar(self) -> float | None:
        """Seberapa sering pasar naik - pembanding untuk setiap klaim edge."""
        gerak = list(self.keputusan.values())
        if not gerak:
            return None
        return sum(1 for g in gerak if g > 0) / len(gerak)

    def edge(self, agen: str, arah: str) -> tuple[float | None, int]:
        """Keunggulan agen ini atas garis dasar, dalam POIN persen.

        Bukan akurasi. Akurasi tidak bisa dinilai siapa pun tanpa garis
        dasarnya: 58% dari agen yang selalu bilang BUY di pasar yang naik 58%
        adalah nol sumbangan.
        """
        dasar = self.garis_dasar
        bagian = [o for o in self.opini if o.agen == agen and o.arah == arah]
        if dasar is None or not bagian:
            return None, 0
        benar = sum(1 for o in bagian if o.benar) / len(bagian)
        harap = dasar if arah == "BUY" else 1 - dasar
        return (benar - harap) * 100, len(bagian)


def _bar(rows: list[dict[str, Any]]) -> list[Bar]:
    return [
        Bar(
            open_time=r["open_time"].replace(tzinfo=UTC),
            close_time=r["close_time"].replace(tzinfo=UTC),
            open=float(r["open"]), high=float(r["high"]),
            low=float(r["low"]), close=float(r["close"]),
            volume=float(r["volume"]),
            close_price=Decimal(str(r["close"])),
        )
        for r in rows
    ]


def bangun(
    candles: dict[str, list[dict[str, Any]]],
    *,
    interval: Horizon = Horizon.D1,
    market: Market = Market.CRYPTO,
    maju: int = BAR_KE_DEPAN,
    council: Council | None = None,
) -> Korpus:
    """Putar ulang council di tiap bar tersimpan, catat opini tiap agen.

    ``candles`` dipetakan simbol -> baris candle terurut. Diterima sebagai
    argumen alih-alih dibaca sendiri supaya modul ini tidak menyentuh database:
    pemanggilnya yang memutuskan berapa dalam riwayat yang dibaca, dan test bisa
    memberi deret buatan tanpa MySQL.
    """
    engine = AnalysisEngine()
    dewan = council or Council()
    korpus = Korpus()

    for symbol, rows in candles.items():
        if len(rows) < MIN_BARS + maju + 1:
            continue
        bars = _bar(rows)
        window = Window(bars=bars, market=market, symbol=symbol, interval=interval)
        tutup = [b.close for b in bars]

        for i in range(MIN_BARS, len(bars) - maju):
            saat = bars[i].close_time
            try:
                technical = engine.analyse(window.series_at(saat))
                verdict = dewan.convene(
                    DecisionContext(
                        market=market, symbol=symbol, interval=interval,
                        as_of=technical.as_of, state=window.state_at(saat),
                        technical=technical,
                        # Lihat docstring modul: keduanya tidak tersedia
                        # point-in-time.
                        news=(), fundamentals=None, valuation=None,
                        trading_allowed=True,
                    )
                )
            except Exception:  # noqa: BLE001 - satu bar buruk bukan alasan berhenti
                korpus.gagal += 1
                continue

            if tutup[i] <= 0:
                korpus.gagal += 1
                continue
            gerak = (tutup[i + maju] - tutup[i]) / tutup[i] * 100
            korpus.opini.extend(
                Opini(
                    symbol=symbol,
                    pada=saat,
                    agen=str(getattr(o.role, "value", o.role)),
                    arah=str(getattr(o.decision, "value", o.decision)),
                    keyakinan=float(o.confidence or 0.0),
                    council=str(
                        getattr(verdict.decision, "value", verdict.decision)
                    ),
                    gerak_pct=gerak,
                )
                for o in verdict.opinions
            )

    log.info(
        "korpus.dibangun",
        opini=len(korpus.opini),
        keputusan=len(korpus.keputusan),
        gagal=korpus.gagal,
    )
    return korpus


__all__ = [
    "BAR_KE_DEPAN",
    "Korpus",
    "Opini",
    "bangun",
]
