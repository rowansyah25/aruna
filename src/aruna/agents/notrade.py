"""No-trade engine (SPEC 33).

Checks every condition the specification names and returns the reasons a
position must not be taken. Ends with the closing rule of SPEC 33: **if there
is no edge, WAIT.**

This runs *before* and *independently of* the agents. A council that has
deliberated its way to a confident BUY on stale data is still wrong, and the
no-trade engine is what stops that being expressible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.agents.context import DecisionContext
from aruna.agents.risk import RiskAssessment, RiskLevel, assess_risk
from aruna.core.enums import Decision, Market, NoTradeReason, Regime

#: Batas keyakinan sebelum sebuah putusan boleh disebut punya edge.
#:
#: **Kalimat aslinya - "di bawah ini bukan edge, melainkan lemparan koin dengan
#: angka menempel" - diukur pada 2026-08-25 dan TIDAK benar.** Yang di atas
#: ambang sama-sama lemparan koin.
#:
#: Diukur atas 10.795 putusan hakim (diambil dari `judge_decisions`, yaitu
#: SEBELUM no-trade memblokir - `council_sessions` tidak bisa dipakai karena
#: gerbang ini menghapus persis baris yang dibutuhkan untuk menilainya), gerak
#: satu bar ke depan, dibandingkan koin berkomposisi arah yang sama::
#:
#:     0,00-0,10   n=2699   edge -3,9
#:     0,10-0,20   n=2046   edge -2,7
#:     0,20-0,35   n=2078   edge -0,1   <- pita terbaik, dan ia diblokir
#:     0,35-0,50   n=1123   edge -1,9
#:     0,50-0,70   n=1286   edge -1,2
#:     0,70-1,01   n=1563   edge -3,8   <- pita paling yakin, dan paling buruk
#:
#:     di bawah 0,35: edge -2,4      di atas 0,35: edge -2,4
#:
#: Keduanya identik. Keyakinan bukan cuma gagal memisahkan edge - urutannya
#: sedikit TERBALIK, dan pita paling percaya diri justru paling sering salah.
#:
#: **Angkanya tetap 0,35, dan itu keputusan yang diukur juga.** Melonggarkannya
#: hanya berguna kalau di baliknya ada sesuatu yang gerbang lain bisa saring;
#: tidak ada. Dari yang dibungkam ambang ini, yang kesepakatan agennya mencapai
#: lima hanya 12 kasus, dan edge-nya -9,2. Yang dilepas bukan sinyal yang
#: tertahan, melainkan lebih banyak koin.
#:
#: Jadi ia dipertahankan sebagai PEMBATAS VOLUME yang tidak merugikan, bukan
#: sebagai penyaring edge. Siapa pun yang menyetel angka ini nanti harus tahu
#: bedanya: menaikkannya mengurangi sinyal tanpa menaikkan mutu, dan
#: menurunkannya menambah sinyal tanpa menaikkan mutu. Yang terbukti memisahkan
#: adalah KESEPAKATAN agen - lihat `aruna.futures.plan.MIN_SEPAKAT`.
MIN_EDGE_CONFIDENCE = 0.35

#: Fewer independent agents than this and there is nothing to weigh.
MIN_PARTICIPATING_AGENTS = 3


@dataclass(frozen=True, slots=True)
class NoTradeVerdict:
    reasons: tuple[NoTradeReason, ...] = field(default_factory=tuple)
    details: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return bool(self.reasons)

    @property
    def decision(self) -> Decision:
        """What ARUNA must output when blocked.

        NO_SIGNAL when the inputs themselves are untrustworthy - there is
        nothing to say. WAIT when the data is sound but the setup is not there.
        """
        if not self.reasons:
            return Decision.WAIT
        untrustworthy = {
            NoTradeReason.STALE_DATA,
            NoTradeReason.MARKET_HALT,
            NoTradeReason.ABNORMAL_SPREAD,
            NoTradeReason.MODEL_ANOMALY,
        }
        return (
            Decision.NO_SIGNAL
            if any(reason in untrustworthy for reason in self.reasons)
            else Decision.WAIT
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "blocked": self.blocked,
            "decision": self.decision.value,
            "reasons": [r.value for r in self.reasons],
            "details": list(self.details),
        }

    def summary(self) -> str:
        if not self.blocked:
            return "tidak ada kondisi yang memblokir"
        return ", ".join(r.value for r in self.reasons)


def evaluate_no_trade(
    context: DecisionContext,
    *,
    risk: RiskAssessment | None = None,
    council_confidence: float | None = None,
    participating_agents: int | None = None,
    horizon_conflict: bool = False,
) -> NoTradeVerdict:
    """Every SPEC 33 condition, checked against the available evidence."""
    reasons: list[NoTradeReason] = []
    details: list[str] = []

    def block(reason: NoTradeReason, detail: str) -> None:
        if reason not in reasons:
            reasons.append(reason)
        details.append(detail)

    state = context.state
    assessment = risk or assess_risk(context)

    # ---- kill switch (SPEC 40) -------------------------------------------
    if not context.trading_allowed:
        block(
            NoTradeReason.KILL_SWITCH_ACTIVE,
            "operator mengaktifkan kill switch",
        )

    # ---- data integrity (SPEC 5) -----------------------------------------
    if state.data_quality != "OK":
        detail = state.quality_detail or state.data_quality
        if state.data_quality in ("STALE", "DUPLICATE", "MISSING"):
            block(NoTradeReason.STALE_DATA, f"data market {state.data_quality}: {detail}")
        elif state.data_quality in ("ABNORMAL_SPREAD",):
            block(NoTradeReason.ABNORMAL_SPREAD, detail)
        else:
            block(NoTradeReason.MODEL_ANOMALY, f"kualitas data {state.data_quality}: {detail}")

    # ---- venue availability (SPEC 3) --------------------------------------
    if context.market is Market.IDX and state.market_open is False:
        block(
            NoTradeReason.MARKET_HALT,
            f"exchange tutup ({state.session or 'di luar sesi'})",
        )

    # ---- risk (SPEC 32) ---------------------------------------------------
    for factor in assessment.blocking:
        mapping = {
            "volatility": NoTradeReason.EXTREME_VOLATILITY,
            "spread": NoTradeReason.ABNORMAL_SPREAD,
            "liquidity": NoTradeReason.BAD_LIQUIDITY,
            "market_halt": NoTradeReason.MARKET_HALT,
            "data_quality": NoTradeReason.STALE_DATA,
            "event": NoTradeReason.MODEL_ANOMALY,
            "gap": NoTradeReason.EXTREME_VOLATILITY,
        }
        block(
            mapping.get(factor.name, NoTradeReason.HIGH_UNCERTAINTY),
            f"{factor.name}: {factor.detail}",
        )
    if assessment.of("liquidity") and assessment.of("liquidity").level is RiskLevel.HIGH:
        block(NoTradeReason.BAD_LIQUIDITY, assessment.of("liquidity").detail)

    # ---- regime (SPEC 9, 33) ----------------------------------------------
    verdict = context.regime
    if verdict is None:
        block(NoTradeReason.UNKNOWN_REGIME, "tidak ada klasifikasi regime yang tersedia")
    elif verdict.regime is Regime.UNCERTAIN:
        block(NoTradeReason.UNKNOWN_REGIME, "regime tidak bisa diklasifikasi")
    elif verdict.regime is Regime.ANOMALY:
        block(NoTradeReason.MODEL_ANOMALY, "regime terklasifikasi ANOMALY")
    elif verdict.regime is Regime.NEWS_SHOCK:
        block(NoTradeReason.NEWS_SHOCK, "regime news shock")

    # ---- evidence sufficiency (SPEC 33) -----------------------------------
    if not context.has_technical:
        block(NoTradeReason.INSUFFICIENT_EVIDENCE, "tidak ada reading technical yang reliabel")
    if participating_agents is not None and participating_agents < MIN_PARTICIPATING_AGENTS:
        block(
            NoTradeReason.INSUFFICIENT_EVIDENCE,
            f"hanya {participating_agents} agent yang membentuk pandangan, "
            f"butuh {MIN_PARTICIPATING_AGENTS}",
        )

    if horizon_conflict:
        block(
            NoTradeReason.CONFLICTING_HORIZON,
            "horizon saling bertentangan soal arah (SPEC 10)",
        )

    # ---- the closing rule (SPEC 33): no edge means WAIT --------------------
    if council_confidence is not None and council_confidence < MIN_EDGE_CONFIDENCE:
        block(
            NoTradeReason.NO_EDGE,
            f"confidence {council_confidence:.2f} di bawah batas "
            f"{MIN_EDGE_CONFIDENCE:.2f} - tidak ada edge yang bisa dibuktikan",
        )

    return NoTradeVerdict(reasons=tuple(reasons), details=tuple(details))


__all__ = [
    "MIN_EDGE_CONFIDENCE",
    "MIN_PARTICIPATING_AGENTS",
    "NoTradeVerdict",
    "evaluate_no_trade",
]
