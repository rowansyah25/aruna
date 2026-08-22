"""Learning from outcomes (PHASE 8).

Loss autopsy, successful objections, counterfactuals, ghost signals,
calibration and agent reliability.

Everything here reads the record and reports; nothing here rewrites a
prediction. The service lives in its own module because it depends on the
repository layer, which imports these types.
"""

from aruna.learning.autopsy import (
    Autopsy,
    ObjectionRecord,
    perform_autopsy,
    successful_objections,
)
from aruna.learning.calibration import (
    Bucket,
    CalibrationReport,
    calibrate,
)
from aruna.learning.counterfactual import (
    Counterfactual,
    GhostSignal,
    counterfactual,
    ghost_signal,
    reclassify_with_lookahead,
    summarise_ghosts,
)
from aruna.learning.reliability import (
    AgentRecord,
    ReliabilityReport,
    build_reliability,
)

__all__ = [
    "AgentRecord",
    "Autopsy",
    "Bucket",
    "CalibrationReport",
    "Counterfactual",
    "GhostSignal",
    "ObjectionRecord",
    "ReliabilityReport",
    "build_reliability",
    "calibrate",
    "counterfactual",
    "ghost_signal",
    "perform_autopsy",
    "reclassify_with_lookahead",
    "successful_objections",
    "summarise_ghosts",
]
