"""News and fundamental agents (SPEC 8, 7, 12)."""

from __future__ import annotations

from aruna.agents.base import Agent, AgentOpinion, EvidenceRef, abstain, scale_confidence
from aruna.agents.context import DecisionContext
from aruna.core.claims import mask_forbidden
from aruna.core.enums import AgentRole, Decision, Market, ValuationVerdict
from aruna.news.models import Importance, Sentiment


class NewsAgent(Agent):
    """Weighs recent, asset-linked news.

    Two deliberate restraints:

    * Items whose sentiment is UNKNOWN are **counted but not voted**. The
      lexicon returning UNKNOWN means it had nothing to go on, and treating
      that as neutral would turn "I cannot tell" into evidence.
    * Confidence is capped hard. The sentiment behind it is a keyword lexicon
      (see :mod:`aruna.news.classify`), and an agent must not present a
      stronger view than its inputs can support.
    """

    role = AgentRole.NEWS

    #: The lexicon is crude, so this agent can never be more than half sure.
    MAX_CONFIDENCE = 0.5

    def __init__(self, *, window_hours: int = 24) -> None:
        self._window = window_hours

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        items = context.recent_news(hours=self._window)
        if not items:
            return abstain(
                self.role,
                f"tidak ada news yang terkait aset ini dalam {self._window} jam "
                "terakhir",
            )

        weights = {
            Importance.CRITICAL: 3.0,
            Importance.HIGH: 2.0,
            Importance.MEDIUM: 1.0,
            Importance.LOW: 0.5,
        }
        bull = bear = 0.0
        unreadable = 0
        reasons: list[str] = []
        evidence: list[EvidenceRef] = []

        for item in items[:12]:
            weight = weights[item.importance] * max(item.sentiment_confidence, 0.1)
            evidence.append(
                EvidenceRef(
                    key=f"news:{item.fingerprint}",
                    value=item.sentiment_confidence,
                    sample_size=1,
                    reliable=item.sentiment is not Sentiment.UNKNOWN,
                    detail=f"{item.category.value}/{item.sentiment.value}",
                )
            )
            if item.sentiment is Sentiment.POSITIVE:
                bull += weight
            elif item.sentiment is Sentiment.NEGATIVE:
                bear += weight
            elif item.sentiment is Sentiment.UNKNOWN:
                unreadable += 1
                continue

            if item.importance in (Importance.CRITICAL, Importance.HIGH):
                # Judul RSS ditulis orang lain, dan ARUNA tidak mengatur
                # isinya. Satu judul yang kebetulan berbunyi "guaranteed
                # profit" akan lolos ke sini, lalu disalin protest._examine ke
                # detail objection, lalu dibaca _guard() di jalur push - yang
                # menolak SELURUH log perdebatan. Efeknya: sebuah headline dari
                # internet bisa membungkam log perdebatan, tanpa jejak selain
                # satu baris exception. Frasanya disensor di sini, dan sensornya
                # kelihatan supaya kutipannya tidak dipalsukan (SPEC 51).
                reasons.append(
                    f"{item.importance.value} {item.category.value}: "
                    f"{mask_forbidden(item.title[:70])} ({item.sentiment.value})"
                )

        readable = len(items) - unreadable
        if readable == 0:
            return abstain(
                self.role,
                f"{len(items)} news ditemukan tapi tidak satu pun terbaca "
                "sentimennya",
            )

        margin = bull - bear
        total = bull + bear
        if total <= 0 or abs(margin) < 0.5:
            decision, raw = Decision.WAIT, 0.0
            reasons.append("sentimen news berimbang")
        else:
            decision = Decision.BUY if margin > 0 else Decision.SELL
            raw = min(1.0, abs(margin) / max(total, 1.0))

        reasons.append(
            f"{readable} dari {len(items)} news punya sentimen yang terbaca"
        )
        if unreadable:
            reasons.append(
                f"{unreadable} news tidak terbaca oleh leksikon - tidak dihitung"
            )

        quality = min(1.0, readable / 5) * self.MAX_CONFIDENCE
        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=scale_confidence(raw, evidence_quality=quality),
            reasoning=tuple(reasons),
            evidence=tuple(evidence),
            horizon=context.interval,
        )


class FundamentalAgent(Agent):
    """IDX valuation (SPEC 7).

    Abstains on crypto: there are no earnings, book value or dividends to
    value, and running the model anyway would produce shaped noise.

    **Undervalued is never an automatic BUY (SPEC 7).** A cheap valuation is
    treated as one input on a long horizon, capped well below certainty, and
    the agent says so in its reasoning. There are good reasons a company is
    cheap and this agent cannot tell which applies.
    """

    role = AgentRole.FUNDAMENTAL

    #: Valuation is a slow signal; it must never dominate a short-horizon call.
    MAX_CONFIDENCE = 0.45

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        if context.market is not Market.IDX:
            return abstain(
                self.role,
                "fundamental tidak berlaku untuk crypto - tidak ada earnings "
                "atau book value",
            )

        report = context.valuation
        if report is None:
            return abstain(self.role, "tidak ada data fundamental untuk simbol ini")
        if report.verdict is ValuationVerdict.UNCERTAIN:
            return abstain(
                self.role,
                "valuasi belum meyakinkan (cakupan metrik "
                f"{report.coverage * 100:.0f}%)",
            )

        reasons = [f"valuasi {report.verdict.value}", *report.reasons[:4]]
        reasons.extend(f"kekhawatiran: {c}" for c in report.concerns[:3])

        if report.verdict is ValuationVerdict.UNDERVALUED:
            decision = Decision.BUY
            reasons.append(
                "murah menurut ukuran harga - satu input valuasi, bukan signal BUY"
            )
        elif report.verdict is ValuationVerdict.OVERVALUED:
            decision = Decision.SELL
            reasons.append("mahal menurut ukuran harga")
        else:
            decision = Decision.WAIT
            reasons.append("harga wajar - tidak ada edge dari valuasi")

        # Concerns pull confidence down: a cheap company with shrinking
        # earnings and heavy debt is cheap for a reason.
        penalty = min(0.5, 0.15 * len(report.concerns))
        quality = report.coverage * (1.0 - penalty) * self.MAX_CONFIDENCE

        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=scale_confidence(report.confidence, evidence_quality=quality),
            reasoning=tuple(reasons),
            evidence=(
                EvidenceRef(
                    key="valuation",
                    value=report.confidence,
                    sample_size=report.metrics_used,
                    reliable=report.coverage >= 0.33,
                    detail=report.verdict.value,
                ),
            ),
            # Valuation speaks to the investment horizon, not this bar.
            horizon=None,
        )


__all__ = ["FundamentalAgent", "NewsAgent"]
