"""Technical analysis (PHASE 3).

SPEC 6 is explicit that indicators are **evidence, not absolute truth**.  That
shapes the whole package: every computation returns a :class:`Reading` carrying
its value *and* how much data produced it, so a later council agent can weigh a
20-bar RSI differently from a 200-bar one instead of treating both as facts.

SPEC 24 is enforced structurally - :class:`CandleSeries` refuses unclosed bars,
so an indicator physically cannot read a price that has not settled yet.
"""

from aruna.analysis.engine import AnalysisEngine, TechnicalSnapshot
from aruna.analysis.reading import Reading
from aruna.analysis.series import CandleSeries

__all__ = [
    "AnalysisEngine",
    "CandleSeries",
    "Reading",
    "TechnicalSnapshot",
]
