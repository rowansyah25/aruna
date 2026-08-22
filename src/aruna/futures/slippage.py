"""Slippage stress test (FUTURES SPEC 30).

A setup that only works at a perfect fill does not work. This walks the trade
through worsening fills and reports where the edge disappears - which is a
property of the setup worth knowing before the position exists, not after.

Slippage is applied to **both ends and against the position at each**: the entry
fills worse than intended and the exit fills worse again. A stress test that
charged it once, or charged it symmetrically, would understate exactly the
scenario it exists to explore.

The stop is stressed too, and that is the part most often forgotten. A stop is
not a guaranteed price - it becomes a market order when touched, and in the
conditions that trigger stops it fills further away. So the loss under stress is
larger than the risk budget, and this module says by how much.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aruna.futures.models import PositionSide
from aruna.futures.risk import MIN_NET_REWARD_RATIO

#: The ladder FUTURES SPEC 30 names, in percent.
SCENARIOS: tuple[tuple[str, Decimal], ...] = (
    ("normal", Decimal("0.00")),
    ("+0.1%", Decimal("0.10")),
    ("+0.3%", Decimal("0.30")),
    ("+0.5%", Decimal("0.50")),
)

#: Net reward below this multiple of the *planned* risk is not worth taking.
#:
#: The same floor the economics applies, imported rather than restated. Two
#: copies of one threshold drift the moment either is tuned, and the failure is
#: silent and absurd: the stress test reporting "survives" for a setup the
#: economics has already called not worth taking.
MIN_NET_RR = MIN_NET_REWARD_RATIO


@dataclass(frozen=True, slots=True)
class SlippageScenario:
    label: str
    slippage_pct: Decimal
    effective_entry: Decimal
    effective_stop: Decimal
    effective_target: Decimal
    #: Loss if the stop fills here - larger than the planned risk.
    realised_risk: Decimal
    realised_reward: Decimal
    net_reward: Decimal
    net_rr: Decimal | None
    survives: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.label,
            "slippage_pct": str(self.slippage_pct),
            "effective_entry": str(self.effective_entry),
            "effective_stop": str(self.effective_stop),
            "effective_target": str(self.effective_target),
            "realised_risk": str(self.realised_risk),
            "realised_reward": str(self.realised_reward),
            "net_reward": str(self.net_reward),
            "net_rr": str(self.net_rr) if self.net_rr is not None else None,
            "survives": self.survives,
        }


@dataclass(frozen=True, slots=True)
class SlippageReport:
    scenarios: tuple[SlippageScenario, ...]
    #: The worst fill the setup still survives, or None if even a clean fill
    #: does not work.
    breaks_at: str | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def survives_all(self) -> bool:
        return all(s.survives for s in self.scenarios)

    @property
    def survives_clean_fill_only(self) -> bool:
        # `bool(...)` rather than the bare `and` chain: with no scenarios the
        # chain returns the empty tuple, and a falsy non-bool leaking out of a
        # property annotated `-> bool` is how a caller ends up printing "()".
        return bool(
            self.scenarios
            and self.scenarios[0].survives
            and not self.survives_all
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenarios": [s.to_dict() for s in self.scenarios],
            "survives_all": self.survives_all,
            "breaks_at": self.breaks_at,
            "findings": list(self.findings),
            "note": (
                "slippage dibebankan di entry dan exit, dan melawan posisi di "
                "keduanya. Stop juga ikut di-stress: stop berubah jadi market "
                "order begitu tersentuh, jadi kerugian yang terjadi melebihi "
                "risiko yang direncanakan (FUTURES SPEC 30)"
            ),
        }


def stress_slippage(
    *,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    quantity: Decimal,
    side: PositionSide,
    fee_pct: Decimal = Decimal("0.05"),
    funding_cost_pct: Decimal = Decimal(0),
    scenarios: tuple[tuple[str, Decimal], ...] = SCENARIOS,
) -> SlippageReport:
    """Walk the trade through worsening fills (FUTURES SPEC 30)."""
    if quantity <= 0 or entry <= 0:
        return SlippageReport(
            scenarios=(),
            findings=("tidak ada posisi untuk di-stress",),
        )

    is_long = side is PositionSide.LONG
    planned_risk = abs(entry - stop) * quantity
    results: list[SlippageScenario] = []
    findings: list[str] = []

    for label, pct in scenarios:
        drift = entry * pct / 100

        # Every fill goes against the position: worse entry, worse stop, worse
        # exit at the target.
        #
        # The target used to be left unstressed, which quietly made the reward
        # leg cheaper than the loss leg - and the report still announced "a 0.5%
        # adverse fill on both ends". On a long at 50,000 with a 51,400 target
        # that turned a 0.85 net R:R into a 1.10 and said the setup survived.
        # Every target in a band roughly 250 wide was declared robust to a fill
        # it had never been tested against.
        #
        # A take-profit limit order does not slip the way a stop-market does;
        # what it does instead is fail to fill, and the position exits somewhere
        # worse. Charging the drift models that at the same magnitude, which is
        # the point of a stress ladder.
        eff_entry = entry + drift if is_long else entry - drift
        eff_stop = stop - drift if is_long else stop + drift
        eff_target = target - drift if is_long else target + drift

        realised_risk = abs(eff_entry - eff_stop) * quantity
        gross_reward = abs(eff_target - eff_entry) * quantity

        notional = eff_entry * quantity
        costs = notional * (fee_pct * 2 + funding_cost_pct) / 100
        net = gross_reward - costs

        # Measured against the *planned* risk, because that is the number the
        # position was sized to and the one a trader believes they are risking.
        net_rr = (net / planned_risk).quantize(Decimal("0.01")) if planned_risk else None
        survives = net_rr is not None and net_rr >= MIN_NET_RR

        results.append(
            SlippageScenario(
                label=label,
                slippage_pct=pct,
                effective_entry=eff_entry,
                effective_stop=eff_stop,
                effective_target=eff_target,
                realised_risk=realised_risk,
                realised_reward=gross_reward,
                net_reward=net,
                net_rr=net_rr,
                survives=survives,
            )
        )

    breaks_at = next((s.label for s in results if not s.survives), None)

    if results and not results[0].survives:
        findings.append(
            "setup ini tidak jalan bahkan pada fill yang bersih: edge-nya "
            "sudah hilang sebelum slippage diperhitungkan"
        )
    elif breaks_at:
        findings.append(
            f"edge hilang pada slippage {breaks_at} - setup ini butuh fill "
            "yang bagus supaya layak diambil, dan kondisi yang menggerakkan "
            "harga adalah kondisi yang memperburuk fill"
        )
    else:
        findings.append(
            "edge bertahan di setiap skenario pada tangga ini, termasuk fill "
            "0.5% yang merugikan di kedua ujung"
        )

    worst = results[-1] if results else None
    if worst and planned_risk > 0:
        overshoot = (worst.realised_risk / planned_risk - 1) * 100
        if overshoot > 0:
            findings.append(
                f"pada {worst.label} stop terisi {overshoot:.0f}% di luar "
                "kerugian yang direncanakan - stop adalah market order begitu "
                "tersentuh, bukan harga yang terjamin"
            )

    return SlippageReport(
        scenarios=tuple(results), breaks_at=breaks_at, findings=tuple(findings)
    )


__all__ = ["MIN_NET_RR", "SCENARIOS", "SlippageReport", "SlippageScenario", "stress_slippage"]
