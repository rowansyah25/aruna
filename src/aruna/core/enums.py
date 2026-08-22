"""ARUNA domain vocabulary.

Every value here is written to the database as text, so the *values* are part
of the storage contract: rename a member freely, but changing a value requires
a migration.

Sections referenced below are sections of the ARUNA master specification.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum

# ---------------------------------------------------------------------------
# SPEC 1 - markets
# ---------------------------------------------------------------------------


class Market(StrEnum):
    """The only two markets ARUNA covers.

    Forex was removed from the specification entirely.  There is deliberately
    no ``FOREX`` member, and :func:`parse_market` rejects the string, so a
    stray config value cannot quietly reintroduce it.
    """

    CRYPTO = "CRYPTO"
    IDX = "IDX"


#: Rejected market names, kept explicit so the error message can say why.
FORBIDDEN_MARKETS: frozenset[str] = frozenset({"FOREX", "FX", "CURRENCY", "FOREIGN_EXCHANGE"})


def parse_market(raw: str) -> Market:
    """Parse a market name, refusing the removed forex market by name."""
    value = raw.strip().upper()
    if value in FORBIDDEN_MARKETS:
        raise ValueError(
            f"market {value!r} is not supported: forex was removed from ARUNA. "
            f"Valid markets: {', '.join(m.value for m in Market)}"
        )
    try:
        return Market(value)
    except ValueError:
        raise ValueError(
            f"unknown market {raw!r}. Valid markets: {', '.join(m.value for m in Market)}"
        ) from None


# ---------------------------------------------------------------------------
# SPEC 2, 3, 31 - analysis modes, kept separate per market so that scoring for
# one market can never be reused for the other ("Jangan mencampur scoring").
# ---------------------------------------------------------------------------


class AnalysisMode(StrEnum):
    CRYPTO_SHORT_TERM = "CRYPTO_SHORT_TERM"
    CRYPTO_INTRADAY = "CRYPTO_INTRADAY"
    CRYPTO_SWING = "CRYPTO_SWING"
    CRYPTO_LONG_TERM = "CRYPTO_LONG_TERM"
    IDX_TRADING = "IDX_TRADING"
    IDX_INVESTMENT = "IDX_INVESTMENT"

    @property
    def market(self) -> Market:
        return Market.CRYPTO if self.name.startswith("CRYPTO_") else Market.IDX


# ---------------------------------------------------------------------------
# SPEC 2, 3, 10 - horizons
#
# The specification writes both "1M = 1 menit" (crypto) and "1M = 1 bulan"
# (IDX investment).  Storing both as the literal string "1M" would make the
# outcome tables ambiguous, so minutes use ``m`` and months use ``mo``.
# ---------------------------------------------------------------------------


class Horizon(StrEnum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M10 = "10m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    D3 = "3d"
    D5 = "5d"
    W1 = "1w"
    MO1 = "1mo"
    MO3 = "3mo"
    MO6 = "6mo"
    Y1 = "1y"

    @property
    def duration(self) -> timedelta:
        """Nominal wall-clock length of the horizon.

        Calendar horizons are approximated (month = 30d, year = 365d).  Outcome
        evaluation for IDX must additionally respect the exchange calendar
        (SPEC 23); this property is only the nominal span.
        """
        return _HORIZON_DURATION[self]

    @property
    def label(self) -> str:
        """Human label, disambiguating minutes from months."""
        return _HORIZON_LABEL[self]


_HORIZON_DURATION: dict[Horizon, timedelta] = {
    Horizon.M1: timedelta(minutes=1),
    Horizon.M3: timedelta(minutes=3),
    Horizon.M5: timedelta(minutes=5),
    Horizon.M10: timedelta(minutes=10),
    Horizon.M15: timedelta(minutes=15),
    Horizon.M30: timedelta(minutes=30),
    Horizon.H1: timedelta(hours=1),
    Horizon.H4: timedelta(hours=4),
    Horizon.D1: timedelta(days=1),
    Horizon.D3: timedelta(days=3),
    Horizon.D5: timedelta(days=5),
    Horizon.W1: timedelta(weeks=1),
    Horizon.MO1: timedelta(days=30),
    Horizon.MO3: timedelta(days=90),
    Horizon.MO6: timedelta(days=180),
    Horizon.Y1: timedelta(days=365),
}

_HORIZON_LABEL: dict[Horizon, str] = {
    Horizon.M1: "1 minute",
    Horizon.M3: "3 minutes",
    Horizon.M5: "5 minutes",
    Horizon.M10: "10 minutes",
    Horizon.M15: "15 minutes",
    Horizon.M30: "30 minutes",
    Horizon.H1: "1 hour",
    Horizon.H4: "4 hours",
    Horizon.D1: "1 day",
    Horizon.D3: "3 days",
    Horizon.D5: "5 days",
    Horizon.W1: "1 week",
    Horizon.MO1: "1 month",
    Horizon.MO3: "3 months",
    Horizon.MO6: "6 months",
    Horizon.Y1: "1 year",
}

#: SPEC 2 - crypto horizons.
CRYPTO_HORIZONS: tuple[Horizon, ...] = (
    Horizon.M1,
    Horizon.M3,
    Horizon.M5,
    Horizon.M10,
    Horizon.M15,
    Horizon.M30,
    Horizon.H1,
    Horizon.H4,
    Horizon.D1,
    Horizon.W1,
)

#: SPEC 3 - IDX trading horizons.
IDX_TRADING_HORIZONS: tuple[Horizon, ...] = (Horizon.D1, Horizon.D3, Horizon.D5, Horizon.W1)

#: SPEC 3 - IDX investment horizons ("1Y+" is modelled as the 1y floor).
IDX_INVESTMENT_HORIZONS: tuple[Horizon, ...] = (
    Horizon.MO1,
    Horizon.MO3,
    Horizon.MO6,
    Horizon.Y1,
)

HORIZONS_BY_MODE: dict[AnalysisMode, tuple[Horizon, ...]] = {
    AnalysisMode.CRYPTO_SHORT_TERM: (Horizon.M1, Horizon.M3, Horizon.M5, Horizon.M10),
    AnalysisMode.CRYPTO_INTRADAY: (Horizon.M15, Horizon.M30, Horizon.H1, Horizon.H4),
    AnalysisMode.CRYPTO_SWING: (Horizon.H4, Horizon.D1),
    AnalysisMode.CRYPTO_LONG_TERM: (Horizon.D1, Horizon.W1),
    AnalysisMode.IDX_TRADING: IDX_TRADING_HORIZONS,
    AnalysisMode.IDX_INVESTMENT: IDX_INVESTMENT_HORIZONS,
}


def horizons_for_market(market: Market) -> tuple[Horizon, ...]:
    if market is Market.CRYPTO:
        return CRYPTO_HORIZONS
    return IDX_TRADING_HORIZONS + IDX_INVESTMENT_HORIZONS


# ---------------------------------------------------------------------------
# SPEC 48 - what ARUNA is allowed to say
# ---------------------------------------------------------------------------


class Decision(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    NO_SIGNAL = "NO_SIGNAL"
    UNKNOWN_MARKET = "UNKNOWN_MARKET"

    @property
    def is_directional(self) -> bool:
        return self in (Decision.BUY, Decision.SELL)


class Stance(StrEnum):
    """How one agent responds to another during cross-protest (SPEC 14)."""

    SUPPORT = "SUPPORT"
    OBJECT = "OBJECT"
    COUNTER_PROPOSE = "COUNTER_PROPOSE"
    ACCEPT_CORRECTION = "ACCEPT_CORRECTION"


# ---------------------------------------------------------------------------
# SPEC 12 - council roster
# ---------------------------------------------------------------------------


class AgentRole(StrEnum):
    ARUNA_ANALYST = "ARUNA_ANALYST"
    PROSECUTOR = "PROSECUTOR"
    TECHNICAL = "TECHNICAL"
    STRUCTURE = "STRUCTURE"
    MOMENTUM = "MOMENTUM"
    VOLUME = "VOLUME"
    REVERSAL = "REVERSAL"
    NEWS = "NEWS"
    FUNDAMENTAL = "FUNDAMENTAL"
    RISK = "RISK"
    REGIME = "REGIME"
    COUNCIL_JUDGE = "COUNCIL_JUDGE"


#: SPEC 12/48 - no agent is permanently bullish, bearish, or in opposition.
#: Every role may return BUY, SELL, or WAIT, including the prosecutor.
UNRESTRICTED_AGENT_DECISIONS: frozenset[Decision] = frozenset(
    {Decision.BUY, Decision.SELL, Decision.WAIT}
)


class CouncilRound(StrEnum):
    """SPEC 14 - the mandatory cross-protest rounds."""

    INDEPENDENT = "ROUND_1_INDEPENDENT"
    PROTEST = "ROUND_2_PROTEST"
    REBUTTAL = "ROUND_3_REBUTTAL"
    ADVERSARIAL_REVIEW = "ROUND_4_ADVERSARIAL_REVIEW"


# ---------------------------------------------------------------------------
# SPEC 9 - market regime
# ---------------------------------------------------------------------------


class Regime(StrEnum):
    #: Tren tanpa arah. **Tidak lagi dihasilkan classifier** sejak taksonomi
    #: berarah masuk (bagian 2 spec), tapi tetap di sini karena 9.897 baris
    #: `market_memories` dan puluhan ribu `signal_snapshots` memuatnya.
    #: Membuangnya membuat setiap pembacaan baris lama meledak.
    TRENDING = "TRENDING"
    #: Tren dengan arahnya. Terukur 2026-08-21: di `TRENDING`, BUY menang
    #: 49,8% dan SELL menang 13,8% - satu regime yang menampung keduanya
    #: membuat bobot agent per-regime tidak bisa membedakan keduanya.
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    #: Tembus ke atas.
    BREAKOUT = "BREAKOUT"
    #: Tembus ke bawah. Dulu ikut tercatat `BREAKOUT`.
    BREAKDOWN = "BREAKDOWN"
    REVERSAL = "REVERSAL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    NEWS_SHOCK = "NEWS_SHOCK"
    ACCUMULATION = "ACCUMULATION"
    DISTRIBUTION = "DISTRIBUTION"
    UNCERTAIN = "UNCERTAIN"
    ANOMALY = "ANOMALY"

    @property
    def keluarga(self) -> Regime:
        """Bentuk kasarnya, untuk mencocokkan baris lama.

        Baris yang ditulis sebelum taksonomi berarah ada menyimpan `TRENDING`
        dan `BREAKOUT` tanpa arah. Mesin kemiripan memberi dimensi REGIME
        bobot 4; menolak kecocokan lintas generasi berarti membuang seluruh
        korpus ingatan yang ada.
        """
        return _KELUARGA_REGIME.get(self, self)

    @property
    def naik(self) -> bool | None:
        """Arahnya, atau ``None`` kalau regime ini memang tak berarah.

        ``None`` dan bukan ``False``: "tidak berarah" dan "arahnya turun"
        adalah dua hal yang sangat berbeda, dan menyatukannya membuat setiap
        RANGING terbaca bearish.
        """
        return _ARAH_REGIME.get(self)


_KELUARGA_REGIME: dict[Regime, Regime] = {
    Regime.TRENDING_BULLISH: Regime.TRENDING,
    Regime.TRENDING_BEARISH: Regime.TRENDING,
    Regime.BREAKDOWN: Regime.BREAKOUT,
}

_ARAH_REGIME: dict[Regime, bool] = {
    Regime.TRENDING_BULLISH: True,
    Regime.TRENDING_BEARISH: False,
    Regime.BREAKOUT: True,
    Regime.BREAKDOWN: False,
}


class IdxSession(StrEnum):
    PRE_MARKET = "PRE_MARKET"
    OPENING = "OPENING"
    MID_SESSION = "MID_SESSION"
    CLOSING = "CLOSING"
    AFTER_MARKET = "AFTER_MARKET"


class CryptoSession(StrEnum):
    ASIA = "ASIA"
    EUROPE = "EUROPE"
    US = "US"
    H24 = "24H"


# ---------------------------------------------------------------------------
# SPEC 5 - data quality
# ---------------------------------------------------------------------------


class DataQuality(StrEnum):
    OK = "OK"
    STALE = "STALE"

    #: Angka yang sama, dengan stempel waktu provider yang juga sama.
    #:
    #: Ini bukan cacat data. Ini pernyataan tentang cadence pemanggilnya:
    #: ARUNA bertanya lagi sebelum providernya menghasilkan apa pun yang baru,
    #: jadi yang diterima adalah observasi yang sama dibaca ulang. Diukur pada
    #: Yahoo/IDX: 1269 panggilan dalam enam jam menghasilkan 301 stempel waktu
    #: berbeda, artinya satu observasi dibaca rata-rata empat kali.
    #:
    #: Dibedakan dari :attr:`DUPLICATE` karena keduanya menuntut tindakan yang
    #: berlawanan. Yang ini diperbaiki dengan memanggil lebih jarang; yang itu
    #: diperbaiki dengan memeriksa providernya.
    REPEATED_READ = "REPEATED_READ"

    #: Angka yang sama berulang kali **tanpa** stempel waktu provider.
    #:
    #: Tanpa stempel, umur data tidak bisa diukur, jadi :attr:`STALE` tidak
    #: pernah bisa menyala - dan pengulangan adalah satu-satunya petunjuk bahwa
    #: feed-nya mungkin beku. Di situlah kelas ini masih berarti.
    DUPLICATE = "DUPLICATE"
    MISSING = "MISSING"
    TIMESTAMP_MISMATCH = "TIMESTAMP_MISMATCH"
    ABNORMAL_PRICE = "ABNORMAL_PRICE"
    ABNORMAL_SPREAD = "ABNORMAL_SPREAD"
    PROVIDER_DISCONNECTED = "PROVIDER_DISCONNECTED"
    LATENCY_SPIKE = "LATENCY_SPIKE"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def blocks_signal(self) -> bool:
        """SPEC 5: invalid data yields NO SIGNAL, never a guessed one."""
        return self is not DataQuality.OK


# ---------------------------------------------------------------------------
# SPEC 33 - reasons ARUNA declines to trade
# ---------------------------------------------------------------------------


class NoTradeReason(StrEnum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_HORIZON = "CONFLICTING_HORIZON"
    HIGH_UNCERTAINTY = "HIGH_UNCERTAINTY"
    EXTREME_VOLATILITY = "EXTREME_VOLATILITY"
    BAD_LIQUIDITY = "BAD_LIQUIDITY"
    NEWS_SHOCK = "NEWS_SHOCK"
    STALE_DATA = "STALE_DATA"
    ABNORMAL_SPREAD = "ABNORMAL_SPREAD"
    MARKET_HALT = "MARKET_HALT"
    UNKNOWN_REGIME = "UNKNOWN_REGIME"
    MODEL_ANOMALY = "MODEL_ANOMALY"
    NO_EDGE = "NO_EDGE"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"


# ---------------------------------------------------------------------------
# SPEC 18, 19 - veto
# ---------------------------------------------------------------------------


class VetoReason(StrEnum):
    # Shared
    DATA_OUTAGE = "DATA_OUTAGE"
    STALE_PRICE = "STALE_PRICE"
    SEVERE_ANOMALY = "SEVERE_ANOMALY"
    SYSTEM_INTEGRITY_FAILURE = "SYSTEM_INTEGRITY_FAILURE"
    # Crypto
    EXTREME_SPREAD = "EXTREME_SPREAD"
    EXTREME_VOLATILITY = "EXTREME_VOLATILITY"
    CRITICAL_SECURITY_EVENT = "CRITICAL_SECURITY_EVENT"
    # IDX
    TRADING_HALT = "TRADING_HALT"
    CRITICAL_CORPORATE_ACTION_UNCERTAINTY = "CRITICAL_CORPORATE_ACTION_UNCERTAINTY"


class VetoReviewOutcome(StrEnum):
    """SPEC 19 - a veto is not final until reviewed."""

    VETO_UPHELD_NO_TRADE = "VETO_UPHELD_NO_TRADE"
    VETO_REJECTED = "VETO_REJECTED"


# ---------------------------------------------------------------------------
# SPEC 25 - loss autopsy taxonomy
# ---------------------------------------------------------------------------


class LossCause(StrEnum):
    FALSE_BREAKOUT = "FALSE_BREAKOUT"
    REVERSAL = "REVERSAL"
    WRONG_REGIME = "WRONG_REGIME"
    NEWS_SHOCK = "NEWS_SHOCK"
    VOLUME_ANOMALY = "VOLUME_ANOMALY"
    LIQUIDITY_ISSUE = "LIQUIDITY_ISSUE"
    LATE_ENTRY = "LATE_ENTRY"
    BAD_RISK_REWARD = "BAD_RISK_REWARD"
    HORIZON_ERROR = "HORIZON_ERROR"
    DATA_PROBLEM = "DATA_PROBLEM"
    MODEL_ERROR = "MODEL_ERROR"
    OTHER = "OTHER"


# ---------------------------------------------------------------------------
# SPEC 7 - fundamental verdict (IDX investment model)
# ---------------------------------------------------------------------------


class ValuationVerdict(StrEnum):
    UNDERVALUED = "UNDERVALUED"
    FAIR_VALUE = "FAIR_VALUE"
    OVERVALUED = "OVERVALUED"
    UNCERTAIN = "UNCERTAIN"


# ---------------------------------------------------------------------------
# SPEC 37 - champion / shadow
# ---------------------------------------------------------------------------


class ModelRole(StrEnum):
    CHAMPION = "CHAMPION"
    SHADOW = "SHADOW"
    RETIRED = "RETIRED"
    CANDIDATE = "CANDIDATE"


# ---------------------------------------------------------------------------
# Operational
# ---------------------------------------------------------------------------


class HealthStatus(StrEnum):
    """Ordered worst-last so that aggregation is a simple max()."""

    UP = "UP"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"

    @property
    def severity(self) -> int:
        return _HEALTH_SEVERITY[self]

    @property
    def is_operational(self) -> bool:
        return self in (HealthStatus.UP, HealthStatus.DISABLED)


_HEALTH_SEVERITY: dict[HealthStatus, int] = {
    HealthStatus.DISABLED: 0,
    HealthStatus.UP: 1,
    HealthStatus.UNKNOWN: 2,
    HealthStatus.DEGRADED: 3,
    HealthStatus.DOWN: 4,
}


class EventSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class TradingModeFlag(StrEnum):
    """SPEC 46 - the MVP has exactly one legal value here."""

    PAPER = "PAPER"
    REAL = "REAL"


__all__ = [
    "CRYPTO_HORIZONS",
    "FORBIDDEN_MARKETS",
    "HORIZONS_BY_MODE",
    "IDX_INVESTMENT_HORIZONS",
    "IDX_TRADING_HORIZONS",
    "UNRESTRICTED_AGENT_DECISIONS",
    "AgentRole",
    "AnalysisMode",
    "CouncilRound",
    "CryptoSession",
    "DataQuality",
    "Decision",
    "EventSeverity",
    "HealthStatus",
    "Horizon",
    "IdxSession",
    "LossCause",
    "Market",
    "ModelRole",
    "NoTradeReason",
    "Regime",
    "Stance",
    "TradingModeFlag",
    "ValuationVerdict",
    "VetoReason",
    "VetoReviewOutcome",
    "horizons_for_market",
    "parse_market",
]
