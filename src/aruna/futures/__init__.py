"""Crypto perpetual futures (FUTURES SPEC).

An extension of ARUNA, not a separate system: the council, the prediction lock,
the outcome engine, the learning layer and the approval gate are all reused, so
a futures finding and a spot finding are directly comparable.

**ARUNA remains an analyst.** FUTURES SPEC 3 and 50: no order is ever placed,
no leverage or margin setting is ever changed, no funds are ever moved. The
Binance adapter cannot reach an execution endpoint by construction.

The provider is not importable-and-usable everywhere: ``fapi.binance.com`` is
blocked from Indonesian networks, and the adapter reports that rather than
routing around it.
"""

from aruna.futures.funding import (
    FundingAnalysis,
    FundingBias,
    FundingTrend,
    analyse_funding,
)
from aruna.futures.horizon import (
    HorizonProjection,
    project_to_horizon,
    settlements_in,
)
from aruna.futures.integrity import IntegrityReport, IntegrityVerdict
from aruna.futures.integrity import check as check_integrity
from aruna.futures.learning import (
    FuturesLearningReport,
    PlanOutcome,
    PlanResult,
    PriceBar,
    daily_report,
    score_plan,
)
from aruna.futures.leverage import (
    LeverageDecision,
    LeverageOption,
    advise_margin_mode,
    recommend_leverage,
    stress_test,
)
from aruna.futures.liquidation import (
    BufferScore,
    CascadeReport,
    CascadeRisk,
    Liquidation,
    buffer_score,
    detect_cascade,
    liquidation_price,
)
from aruna.futures.models import (
    ContractSpec,
    FundingRate,
    FuturesSnapshot,
    InstrumentType,
    LiquidationEvent,
    LongShortRatio,
    MarginMode,
    MarkPrice,
    OpenInterest,
    PositionSide,
    side_of,
)
from aruna.futures.openinterest import (
    FlowRead,
    OpenInterestAnalysis,
    analyse_open_interest,
)
from aruna.futures.orderbook import (
    LiquidityVerdict,
    OrderBookDepth,
    assess_liquidity,
)
from aruna.futures.plan import (
    FORBIDDEN_CLAIMS,
    ForbiddenClaim,
    FuturesPlan,
    PlanVerdict,
    build_plan,
    render,
)
from aruna.futures.regime import (
    FuturesRegime,
    FuturesRegimeVerdict,
    classify_futures_regime,
)
from aruna.futures.risk import (
    PositionSize,
    TradeEconomics,
    position_size,
    trade_economics,
)
from aruna.futures.slippage import SlippageReport, stress_slippage
from aruna.futures.stops import StopLoss, TakeProfit, stop_loss, take_profit

__all__ = [
    "FORBIDDEN_CLAIMS",
    "BufferScore",
    "CascadeReport",
    "CascadeRisk",
    "ContractSpec",
    "FlowRead",
    "ForbiddenClaim",
    "FundingAnalysis",
    "FundingBias",
    "FundingRate",
    "FundingTrend",
    "FuturesLearningReport",
    "FuturesPlan",
    "FuturesRegime",
    "FuturesRegimeVerdict",
    "FuturesSnapshot",
    "HorizonProjection",
    "InstrumentType",
    "IntegrityReport",
    "IntegrityVerdict",
    "LeverageDecision",
    "LeverageOption",
    "Liquidation",
    "LiquidationEvent",
    "LiquidityVerdict",
    "LongShortRatio",
    "MarginMode",
    "MarkPrice",
    "OpenInterest",
    "OpenInterestAnalysis",
    "OrderBookDepth",
    "PlanOutcome",
    "PlanResult",
    "PlanVerdict",
    "PositionSide",
    "PositionSize",
    "PriceBar",
    "SlippageReport",
    "StopLoss",
    "TakeProfit",
    "TradeEconomics",
    "advise_margin_mode",
    "analyse_funding",
    "analyse_open_interest",
    "assess_liquidity",
    "buffer_score",
    "build_plan",
    "check_integrity",
    "classify_futures_regime",
    "daily_report",
    "detect_cascade",
    "liquidation_price",
    "position_size",
    "project_to_horizon",
    "recommend_leverage",
    "render",
    "score_plan",
    "settlements_in",
    "side_of",
    "stop_loss",
    "stress_slippage",
    "stress_test",
    "take_profit",
    "trade_economics",
]
