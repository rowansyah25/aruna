"""How a locked prediction is published (SPEC 21).

SPEC 21 requires the prediction to be stated **before** the outcome is known,
in full: direction, confidence, entry, target, horizon, and the reasoning it
rests on. That is the whole point - a forecast that is only described after the
fact cannot be wrong, and therefore cannot be evaluated.

Two things the block below always states plainly:

* **PAPER TRADE.** No order exists anywhere in ARUNA (SPEC 46).
* **What is missing.** When ATR was unavailable there is no target, and the
  block says so rather than printing a number that was never computed.
"""

from __future__ import annotations

from aruna.core.clock import isoformat
from aruna.signals.models import LockedSignal
from aruna.signals.multihorizon import MultiHorizonView


def format_signal(
    signal: LockedSignal, *, view: MultiHorizonView | None = None
) -> str:
    """The SPEC 21 publication block for one locked prediction."""
    lines = [
        "ARUNA SIGNAL",
        f"{signal.symbol}  ({signal.market.value})",
        "",
        f"DIRECTION:   {signal.direction.value}",
        f"CONFIDENCE:  {signal.confidence * 100:.0f}%",
        f"HORIZON:     {signal.horizon.value}",
        f"ENTRY:       {signal.entry_price}",
    ]

    if signal.target_price is not None and signal.expected_move_pct is not None:
        lines.append(f"TARGET:      {signal.target_price}")
        lines.append(f"EXPECTED:    {signal.expected_move_pct:+.2f}%")
    elif signal.is_directional:
        lines.append("TARGET:      NOT AVAILABLE - ATR could not be measured")
        lines.append("EXPECTED:    NOT AVAILABLE")

    lines += [
        f"RESOLVES:    {isoformat(signal.resolves_at)}",
        "",
        f"REGIME:      {signal.regime or 'UNKNOWN'}",
        f"RISK:        {signal.risk_level or 'UNKNOWN'}",
        f"NEWS:        {signal.news_state or 'NOT CHECKED'}",
        "",
        "REASONING",
    ]
    lines += [f"  - {line}" for line in signal.reasoning[:8]]

    if view is not None and len(view.views) > 1:
        lines += ["", "HORIZONS"]
        lines += [f"  {v.summary()}" for v in view.views]
        lines.append(f"  SCOPE: {view.scope()}")
        if view.conflicted:
            lines.append("  Horizons disagree; SPEC 10 does not force them to agree.")

    lines += [
        "",
        f"LOCKED AT:   {isoformat(signal.locked_at)}",
        f"DATA AS OF:  {isoformat(signal.as_of)}",
        f"SIGNAL ID:   {signal.signal_id}",
        f"FINGERPRINT: {signal.fingerprint[:16]}",
        "",
        "This prediction is locked and will not be edited (SPEC 20).",
        "PAPER TRADE - ARUNA places no orders (SPEC 46).",
    ]
    if signal.supersedes:
        lines.append(f"Supersedes {signal.supersedes}, which stays on record unchanged.")
    return "\n".join(lines)


__all__ = ["format_signal"]
