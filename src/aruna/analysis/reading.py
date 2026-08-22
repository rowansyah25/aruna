"""The unit of evidence.

SPEC 6: indicators are evidence, not truth.  A bare float cannot express that -
it looks equally authoritative whether it came from 200 bars or 3.  Every
computation therefore returns a :class:`Reading`, which carries how much data
produced it and whether that was enough.

A reading that is not :attr:`Reading.reliable` must never be treated as a
finding.  It is not an error either: "not enough data yet" is a legitimate,
honest answer (SPEC 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Reading:
    """One indicator value plus the context needed to weigh it."""

    name: str
    value: float | None = None
    #: Bars that actually fed this computation.
    sample_size: int = 0
    #: Bars the indicator needs before its value means anything.
    required: int = 0
    detail: str = ""
    #: Secondary outputs (MACD signal line, Bollinger bands, ...).
    components: dict[str, float] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.value is not None

    @property
    def reliable(self) -> bool:
        """True when there was enough data for the value to mean something."""
        return self.value is not None and self.sample_size >= self.required

    @property
    def confidence(self) -> float:
        """Rough 0..1 weight from sample adequacy.

        Deliberately crude: it expresses "how much data backs this", not "how
        likely this is to be right". Predictive weighting is the council's job,
        informed by measured calibration - inventing a probability here would
        be exactly the false precision SPEC 6 warns against.
        """
        if self.value is None or self.required <= 0:
            return 0.0
        if self.sample_size >= self.required * 2:
            return 1.0
        if self.sample_size < self.required:
            return 0.0
        # Between "just enough" and "comfortably enough", scale 0.5 -> 1.0.
        span = self.required
        return 0.5 + 0.5 * ((self.sample_size - self.required) / span)

    @classmethod
    def insufficient(cls, name: str, *, have: int, need: int) -> Reading:
        return cls(
            name=name,
            value=None,
            sample_size=have,
            required=need,
            detail=f"needs {need} bars, has {have}",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "sample_size": self.sample_size,
            "required": self.required,
            "reliable": self.reliable,
            "detail": self.detail or None,
            "components": self.components or None,
        }


__all__ = ["Reading"]
