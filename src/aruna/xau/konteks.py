"""Kolam bukti beku untuk satu keputusan XAU.

**Intervalnya M5, dan itu bukan pilihan gaya.**  Spec menetapkan keputusan
final berasal dari XAUUSD M5; M15, H1, dan H4 masuk sebagai konteks lewat
:class:`~aruna.xau.bukti.BuktiXau`, bukan sebagai kandidat keputusan.  Satu
keputusan, satu timeframe yang bertanggung jawab atasnya.

**`as_of` datang dari bukti, bukan dari snapshot.**  `Snapshot.captured_at`
adalah kapan ARUNA bertanya; `BuktiXau.as_of` adalah kapan pasar terakhir
bicara.  Yang menentukan sah tidaknya sebuah keputusan adalah yang kedua -
memakai yang pertama akan membuat keputusan terlihat segar hanya karena
permintaannya baru dikirim, bahkan kalau bar terakhirnya dari sejam lalu.

**Yang tidak diterbitkan venue diteruskan sebagai ``None``.**  Diukur
2026-08-27: Twelve Data tidak menerbitkan bid/ask untuk XAU/USD, jadi
``spread_bps`` selalu ``None`` dan gerbang spread tidak menyala.  ``None``
diteruskan apa adanya - tidak ditaksir dari range candle, karena range adalah
pergerakan harga sementara spread adalah biaya transaksi.  Menyamakan keduanya
akan membuat gerbangnya menolak justru saat pasar bergerak.

Volume juga ``None``, bukan ``0``.  Valas spot tidak menerbitkannya; sebuah nol
di sini akan terbaca sebagai "likuiditas terukur dan hasilnya kosong", dan agen
volume akan menilai kekeringan yang tidak pernah ada.
"""

from __future__ import annotations

from aruna.agents.context import DecisionContext, MarketState
from aruna.core.enums import Horizon, Market
from aruna.data.models import Snapshot
from aruna.xau.bukti import BuktiXau


def rakit_konteks(
    bukti: BuktiXau,
    snapshot: Snapshot,
    *,
    trading_allowed: bool = True,
) -> DecisionContext:
    """Satu keputusan XAU, dengan seluruh buktinya dan tanpa isian karangan."""
    state = MarketState(
        last_price=snapshot.last_price,
        bid=snapshot.bid,
        ask=snapshot.ask,
        spread_bps=snapshot.spread_bps,
        # Valas spot tidak menerbitkan volume: None berarti tidak diukur.
        volume_24h=None,
        session=snapshot.session,
        market_open=snapshot.market_open,
        is_realtime=snapshot.provenance.is_realtime,
        data_quality=snapshot.quality.value,
        quality_detail=snapshot.quality_detail,
        source=snapshot.provenance.source,
    )
    return DecisionContext(
        market=Market.FOREX,
        symbol=snapshot.symbol,
        interval=Horizon.M5,
        as_of=bukti.as_of,
        state=state,
        technical=bukti.m5,
        trading_allowed=trading_allowed,
    )


__all__ = ["rakit_konteks"]
