"""Market regime classification (SPEC 9).

Turns the indicator and structure evidence into one of the SPEC 9 regimes.

Three commitments shape this module:

* **UNCERTAIN is a real answer.** When the evidence is thin or contradictory,
  the honest output is UNCERTAIN, not the closest-looking label. A confidently
  wrong regime would propagate into every downstream weighting.
* **Rules, not a model.** These are transparent thresholds a human can argue
  with. SPEC 36 requires model changes to go through research, backtest,
  walk-forward and human approval; a learned regime classifier is that kind of
  change, and it belongs in a later phase - not smuggled in here.
* **Evidence is weighed, not counted.** Readings that lack sample size are
  skipped rather than treated as zero, so a 20-bar series cannot outvote the
  fact that it is only 20 bars.

NEWS_SHOCK is deliberately never returned: it requires news, which arrives in
PHASE 4. Returning it from price action alone would be a guess wearing a
specific label.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.analysis.reading import Reading
from aruna.analysis.structure import BreakoutState, StructureReport, TrendStructure
from aruna.core.enums import Regime

#: Thresholds. Named so they can be argued with and tuned in one place.
#: Ambang volatilitas, sebagai RASIO terhadap kebiasaan aset itu sendiri.
#:
#: Bebas skala dengan sengaja: 1,5 berarti hal yang sama di 15m maupun 1d, di
#: BTC maupun di aset yang baru terdaftar. Pendahulunya - `HIGH_VOL_ATR_PCT =
#: 3.0` dan `LOW_VOL_ATR_PCT = 0.5` dalam persen harga - tidak punya sifat itu:
#: terukur 2026-08-21, yang pertama tidak pernah tercapai di 15m dan tercapai
#: pada 89,6% bar 1d, yang kedua benar untuk lebih dari separuh bar 15m.
HIGH_VOL_RASIO = 1.5
LOW_VOL_RASIO = 0.7
COMPRESSION_RATIO = 0.7
EXPANSION_RATIO = 1.4
ANOMALY_VOLUME_RATIO = 4.0
ANOMALY_MOVE_PCT = 12.0
ACCUMULATION_VOLUME_PCT = 25.0
TREND_MOMENTUM_PCT = 1.5


@dataclass(frozen=True, slots=True)
class RegimeVerdict:
    regime: Regime
    #: 0..1, derived from how much reliable evidence backed the call.
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    #: Regimes that also had support, in order. A regime call is rarely
    #: unanimous, and hiding the runners-up would overstate certainty.
    alternatives: tuple[str, ...] = field(default_factory=tuple)
    evidence_used: int = 0
    evidence_available: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 3),
            "reasons": list(self.reasons),
            "alternatives": list(self.alternatives),
            "evidence_used": self.evidence_used,
            "evidence_available": self.evidence_available,
        }


def _usable(reading: Reading | None) -> float | None:
    """A reading's value only if it had enough data behind it."""
    if reading is None or not reading.reliable:
        return None
    return reading.value


def classify_regime(
    *,
    structure: StructureReport,
    atr: Reading | None = None,
    momentum: Reading | None = None,
    rsi: Reading | None = None,
    bollinger: Reading | None = None,
    compression: Reading | None = None,
    volume_anomaly: Reading | None = None,
    volume_trend: Reading | None = None,
) -> RegimeVerdict:
    """Classify the current regime from available evidence."""
    candidates = [atr, momentum, rsi, bollinger, compression, volume_anomaly, volume_trend]
    available = sum(1 for r in candidates if r is not None)
    used = sum(1 for r in candidates if r is not None and r.reliable)

    # `atr_pct` sengaja tidak dibaca di sini lagi. Ia tetap ada di `Reading`
    # untuk dilaporkan ke operator, tapi klasifikasi memakai rasio: terukur
    # 2026-08-21, ambang persen mutlak tidak pernah tercapai di 15m dan
    # tercapai pada 89,6% bar 1d.
    atr_relatif = (
        atr.components.get("atr_relatif") if atr and atr.reliable else None
    )
    move = _usable(momentum)
    band_pct = _usable(bollinger)
    squeeze = _usable(compression)
    vol_ratio = _usable(volume_anomaly)
    vol_change = _usable(volume_trend)

    scores: dict[Regime, float] = {}
    reasons: dict[Regime, list[str]] = {}

    def vote(regime: Regime, weight: float, reason: str) -> None:
        scores[regime] = scores.get(regime, 0.0) + weight
        reasons.setdefault(regime, []).append(reason)

    # ---- anomaly first: it disqualifies every other reading ---------------
    if vol_ratio is not None and vol_ratio >= ANOMALY_VOLUME_RATIO:
        vote(Regime.ANOMALY, 3.0, f"volume {vol_ratio:.1f}x rata-rata terakhirnya")
    if move is not None and abs(move) >= ANOMALY_MOVE_PCT:
        vote(Regime.ANOMALY, 3.0, f"pergerakan {move:+.1f}% sepanjang window momentum")

    # ---- volatility -------------------------------------------------------
    # Dibandingkan kebiasaan deret ini sendiri, bukan persen harga mutlak.
    #
    # Terukur 2026-08-21 atas 7.700 pengamatan per interval: `HIGH_VOL_ATR_PCT
    # = 3.0` tidak pernah tercapai di 15m (maksimum 2,15%), tercapai sekali di
    # 1h, dan tercapai pada 89,6% bar 1d. Ambang volatilitas yang tidak
    # berskala dengan timeframe bukan pendeteksi volatilitas - ia pendeteksi
    # timeframe. `LOW_VOL_ATR_PCT = 0.5` rusak dengan cara yang sama: median
    # 15m adalah 0,445, jadi lebih dari separuh bar 15m otomatis "tenang".
    #
    # Rasio dipilih daripada ambang per-timeframe karena ambang per-timeframe
    # yang dipas-paskan ke sebaran hari ini adalah overfitting terhadap enam
    # hari pasar naik pada dua puluh aset kripto - yang bagian 32 larang.
    if atr_relatif is not None:
        if atr_relatif >= HIGH_VOL_RASIO:
            vote(
                Regime.HIGH_VOLATILITY,
                2.0,
                f"ATR {atr_relatif:.2f}x kebiasaan asetnya",
            )
        elif atr_relatif <= LOW_VOL_RASIO:
            vote(
                Regime.LOW_VOLATILITY,
                2.0,
                f"ATR hanya {atr_relatif:.2f}x kebiasaan asetnya",
            )

    # ---- structure --------------------------------------------------------
    if structure.reliable:
        # Arahnya sudah di tangan di sini, dan dulu dibuang: kedua cabang
        # memilih `TRENDING` yang sama (bagian 2 spec). Terukur 2026-08-21:
        # di regime `TRENDING`, BUY menang 49,8% dan SELL menang 13,8% - satu
        # ember yang menampung keduanya membuat bobot agent per-regime tidak
        # bisa membedakan tren naik dari tren turun.
        if structure.trend is TrendStructure.UPTREND:
            vote(Regime.TRENDING_BULLISH, 2.5, "higher high dan higher low")
        elif structure.trend is TrendStructure.DOWNTREND:
            vote(Regime.TRENDING_BEARISH, 2.5, "lower high dan lower low")
        elif structure.trend is TrendStructure.RANGE:
            vote(Regime.RANGING, 2.0, "urutan swing-nya campur aduk")

        if structure.breakout is BreakoutState.BREAKOUT_UP:
            vote(Regime.BREAKOUT, 3.0, structure.detail or "level tertembus ke atas")
        elif structure.breakout is BreakoutState.BREAKOUT_DOWN:
            vote(Regime.BREAKDOWN, 3.0, structure.detail or "level jebol ke bawah")
        elif structure.breakout in (
            BreakoutState.FALSE_BREAKOUT_UP,
            BreakoutState.FALSE_BREAKOUT_DOWN,
            BreakoutState.REJECTION,
        ):
            vote(Regime.REVERSAL, 2.5, "harga ditolak di sebuah level")

    # ---- momentum and stretch --------------------------------------------
    if move is not None and abs(move) >= TREND_MOMENTUM_PCT:
        # Tanda `move` adalah arahnya. Memilih `TRENDING` tanpa arah di sini
        # akan membuat suara momentum bertabrakan dengan suara struktur di atas
        # dan menghidupkan kembali ember tanpa arah lewat pintu belakang.
        berarah = (
            Regime.TRENDING_BULLISH if move > 0 else Regime.TRENDING_BEARISH
        )
        vote(berarah, 1.5, f"pergerakan berarah {move:+.2f}%")

    stretch = _usable(rsi)
    if stretch is not None:
        if stretch >= 70:
            vote(Regime.REVERSAL, 1.0, f"RSI {stretch:.0f} tertarik terlalu ke atas")
        elif stretch <= 30:
            vote(Regime.REVERSAL, 1.0, f"RSI {stretch:.0f} tertarik terlalu ke bawah")
        elif 45 <= stretch <= 55:
            vote(Regime.RANGING, 1.0, f"RSI {stretch:.0f} netral")

    if band_pct is not None and 0.35 <= band_pct <= 0.65:
        vote(Regime.RANGING, 1.0, "harga berada di tengah band")

    # ---- compression / expansion -----------------------------------------
    if squeeze is not None:
        if squeeze <= COMPRESSION_RATIO:
            vote(Regime.LOW_VOLATILITY, 1.5, f"range menyempit ke {squeeze:.2f}x")
            vote(Regime.ACCUMULATION, 1.0, "range yang makin rapat")
        elif squeeze >= EXPANSION_RATIO:
            vote(Regime.HIGH_VOLATILITY, 1.5, f"range melebar ke {squeeze:.2f}x")

    # ---- accumulation / distribution -------------------------------------
    if vol_change is not None and structure.reliable:
        rising = vol_change >= ACCUMULATION_VOLUME_PCT
        if rising and structure.trend is TrendStructure.RANGE:
            vote(Regime.ACCUMULATION, 2.0, f"volume {vol_change:+.0f}% di dalam range")
        elif rising and structure.trend is TrendStructure.DOWNTREND:
            vote(
                Regime.DISTRIBUTION,
                2.0,
                f"volume {vol_change:+.0f}% saat harga melemah",
            )

    # ---- verdict ----------------------------------------------------------
    if not scores:
        return RegimeVerdict(
            regime=Regime.UNCERTAIN,
            confidence=0.0,
            reasons=("tidak ada bukti andal yang tersedia",),
            evidence_used=used,
            evidence_available=available,
        )

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    winner, top_score = ranked[0]

    # A near-tie is not a verdict. Reporting the leader as if it were would
    # manufacture certainty the evidence does not support.
    if len(ranked) > 1 and top_score - ranked[1][1] < 0.5:
        return RegimeVerdict(
            regime=Regime.UNCERTAIN,
            confidence=0.2,
            reasons=(
                f"{winner.value} dan {ranked[1][0].value} sama kuat dukungannya",
                *reasons.get(winner, []),
            ),
            alternatives=tuple(r.value for r, _ in ranked[:3]),
            evidence_used=used,
            evidence_available=available,
        )

    # Confidence blends margin of victory with how much evidence was usable.
    total = sum(scores.values())
    share = top_score / total if total else 0.0
    coverage = (used / available) if available else 0.0
    confidence = round(min(1.0, share * 0.6 + coverage * 0.4), 3)

    return RegimeVerdict(
        regime=winner,
        confidence=confidence,
        reasons=tuple(reasons.get(winner, [])),
        alternatives=tuple(r.value for r, _ in ranked[1:3]),
        evidence_used=used,
        evidence_available=available,
    )


__all__ = ["RegimeVerdict", "classify_regime"]
