"""Technical indicators (SPEC 6).

Pure Python over a few hundred bars - no numpy. The arrays are small, and
keeping the runtime dependency surface honest is worth more here than
microseconds.

Every function returns a :class:`Reading` rather than a float, so a caller
always sees how much data backed the number. When there is not enough,
:meth:`Reading.insufficient` is returned - not a zero, not an extrapolation.
"""

from __future__ import annotations

import math
from itertools import pairwise
from statistics import median

from aruna.analysis.reading import Reading
from aruna.analysis.series import CandleSeries

# Standard periods. Configurable at the call site; these are the conventional
# defaults so results are comparable to any other charting tool.
DEFAULT_RSI_PERIOD = 14
DEFAULT_ATR_PERIOD = 14
DEFAULT_BB_PERIOD = 20
DEFAULT_BB_STDDEV = 2.0
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------


def sma_values(values: tuple[float, ...] | list[float], period: int) -> list[float]:
    """Simple moving average over a whole series, oldest first."""
    if period <= 0 or len(values) < period:
        return []
    out: list[float] = []
    window = sum(values[:period])
    out.append(window / period)
    for index in range(period, len(values)):
        window += values[index] - values[index - period]
        out.append(window / period)
    return out


def ema_values(values: tuple[float, ...] | list[float], period: int) -> list[float]:
    """Exponential moving average, seeded with the first SMA.

    Seeding with an SMA rather than the first price is the conventional
    treatment; starting from a single price makes the early values depend
    heavily on an arbitrary bar.
    """
    if period <= 0 or len(values) < period:
        return []
    multiplier = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    out = [ema]
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
        out.append(ema)
    return out


def sma(series: CandleSeries, period: int) -> Reading:
    values = sma_values(series.closes, period)
    if not values:
        return Reading.insufficient(f"sma_{period}", have=len(series), need=period)
    return Reading(
        name=f"sma_{period}",
        value=values[-1],
        sample_size=len(series),
        required=period,
    )


def ema(series: CandleSeries, period: int) -> Reading:
    values = ema_values(series.closes, period)
    if not values:
        return Reading.insufficient(f"ema_{period}", have=len(series), need=period)
    return Reading(
        name=f"ema_{period}",
        value=values[-1],
        sample_size=len(series),
        required=period,
    )


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------


def rsi(series: CandleSeries, period: int = DEFAULT_RSI_PERIOD) -> Reading:
    """Wilder's RSI.  0..100; above 70 conventionally overbought, below 30 over-sold.

    Those thresholds are conventions, not verdicts - SPEC 6 treats the value as
    evidence, and a market can stay 'overbought' for a long trend.
    """
    need = period + 1
    if not series.has(need):
        return Reading.insufficient("rsi", have=len(series), need=need)

    closes = series.closes
    gains, losses = [], []
    for previous, current in pairwise(closes):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    # Wilder smoothing: seed with a simple mean, then smooth.
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for index in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period

    if avg_loss == 0:
        value = 100.0 if avg_gain > 0 else 50.0
        detail = "no losses in window" if avg_gain > 0 else "flat window"
    else:
        rs = avg_gain / avg_loss
        value = 100.0 - (100.0 / (1.0 + rs))
        detail = ""

    return Reading(
        name="rsi",
        value=value,
        sample_size=len(series),
        required=need,
        detail=detail,
        components={"avg_gain": avg_gain, "avg_loss": avg_loss},
    )


def macd(
    series: CandleSeries,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> Reading:
    """MACD line, signal line, and histogram."""
    need = slow + signal
    if not series.has(need):
        return Reading.insufficient("macd", have=len(series), need=need)

    fast_ema = ema_values(series.closes, fast)
    slow_ema = ema_values(series.closes, slow)
    # The fast EMA starts earlier; align both to the slow EMA's first bar.
    offset = len(fast_ema) - len(slow_ema)
    macd_line = [f - s for f, s in zip(fast_ema[offset:], slow_ema, strict=True)]

    signal_line = ema_values(macd_line, signal)
    if not signal_line:
        return Reading.insufficient("macd", have=len(series), need=need)

    latest_macd = macd_line[-1]
    latest_signal = signal_line[-1]
    histogram = latest_macd - latest_signal

    return Reading(
        name="macd",
        value=latest_macd,
        sample_size=len(series),
        required=need,
        components={
            "signal": latest_signal,
            "histogram": histogram,
            "previous_histogram": (
                macd_line[-2] - signal_line[-2] if len(signal_line) > 1 else histogram
            ),
        },
    )


def momentum(series: CandleSeries, period: int = 10) -> Reading:
    """Percentage change over ``period`` bars."""
    need = period + 1
    if not series.has(need):
        return Reading.insufficient("momentum", have=len(series), need=need)
    past = series.closes[-need]
    if past == 0:
        return Reading("momentum", None, len(series), need, detail="zero reference price")
    change = (series.last_close - past) / past * 100.0
    return Reading(
        name="momentum",
        value=change,
        sample_size=len(series),
        required=need,
        detail=f"{period}-bar change",
    )


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


def true_ranges(series: CandleSeries) -> list[float]:
    """True range per bar, from the second bar onward."""
    ranges: list[float] = []
    for index in range(1, len(series)):
        high, low = series.highs[index], series.lows[index]
        previous_close = series.closes[index - 1]
        ranges.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
    return ranges


def atr(series: CandleSeries, period: int = DEFAULT_ATR_PERIOD) -> Reading:
    """Average true range (Wilder), in price units."""
    need = period + 1
    if not series.has(need):
        return Reading.insufficient("atr", have=len(series), need=need)

    ranges = true_ranges(series)
    value = sum(ranges[:period]) / period
    for entry in ranges[period:]:
        value = (value * (period - 1) + entry) / period

    percent = (value / series.last_close * 100.0) if series.last_close else 0.0

    # ATR sekarang dibanding kebiasaan deret ini sendiri (median true range
    # atas seluruh window). Tanpa ini yang tersedia hanya angka mutlak, dan
    # angka mutlak berskala dengan timeframe: terukur 2026-08-21, ATR% maksimum
    # di 15m adalah 2,15% sementara di 1d medianya 5,65%. Satu ambang persen
    # tidak pernah bisa benar untuk keduanya.
    #
    # Median, bukan rata-rata: satu bar liar menaikkan rata-rata dan membuat
    # lonjakan berikutnya terlihat biasa - persis kebalikan dari yang dicari.
    khas = median(ranges) if ranges else 0.0
    relatif = (value / khas) if khas else 1.0

    return Reading(
        name="atr",
        value=value,
        sample_size=len(series),
        required=need,
        components={"atr_pct": percent, "atr_relatif": relatif},
        detail=f"{percent:.2f}% of last close ({relatif:.2f}x kebiasaannya)",
    )


def bollinger(
    series: CandleSeries,
    period: int = DEFAULT_BB_PERIOD,
    stddev: float = DEFAULT_BB_STDDEV,
) -> Reading:
    """Bollinger bands.  ``value`` is %B: where price sits across the band."""
    if not series.has(period):
        return Reading.insufficient("bollinger", have=len(series), need=period)

    window = series.closes[-period:]
    middle = sum(window) / period
    variance = sum((price - middle) ** 2 for price in window) / period
    deviation = math.sqrt(variance)
    upper = middle + stddev * deviation
    lower = middle - stddev * deviation

    width = upper - lower
    percent_b = (series.last_close - lower) / width if width > 0 else 0.5
    bandwidth = (width / middle * 100.0) if middle else 0.0

    return Reading(
        name="bollinger",
        value=percent_b,
        sample_size=len(series),
        required=period,
        components={
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "bandwidth_pct": bandwidth,
        },
        detail=f"bandwidth {bandwidth:.2f}%",
    )


def realised_volatility(series: CandleSeries, period: int = 20) -> Reading:
    """Standard deviation of bar-to-bar returns, in percent."""
    need = period + 1
    if not series.has(need):
        return Reading.insufficient("realised_volatility", have=len(series), need=need)

    closes = series.closes[-need:]
    returns = [
        (current - previous) / previous * 100.0
        for previous, current in pairwise(closes)
        if previous
    ]
    if not returns:
        return Reading.insufficient("realised_volatility", have=len(series), need=need)

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return Reading(
        name="realised_volatility",
        value=math.sqrt(variance),
        sample_size=len(series),
        required=need,
        detail="stddev of bar returns, %",
    )


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


def vwap(series: CandleSeries, period: int | None = None) -> Reading:
    """Volume weighted average price.

    Only meaningful where reported volume is real. Returns unavailable rather
    than falling back to an unweighted mean when volume is absent - a VWAP
    without volume is just an average wearing a misleading name (SPEC 6).
    """
    window = series if period is None else series.tail(period)
    need = period or 1
    if len(window) < need:
        return Reading.insufficient("vwap", have=len(series), need=need)

    typical = window.typical_prices
    total_volume = sum(window.volumes)
    if total_volume <= 0:
        return Reading(
            name="vwap",
            value=None,
            sample_size=len(window),
            required=need,
            detail="no reported volume; VWAP is undefined",
        )

    weighted = sum(p * v for p, v in zip(typical, window.volumes, strict=True))
    value = weighted / total_volume
    distance = (series.last_close - value) / value * 100.0 if value else 0.0
    return Reading(
        name="vwap",
        value=value,
        sample_size=len(window),
        required=need,
        components={"distance_pct": distance},
        detail=f"price {distance:+.2f}% vs vwap",
    )


def volume_anomaly(series: CandleSeries, period: int = 20) -> Reading:
    """Latest bar's volume as a multiple of its recent average.

    ``value`` is the ratio: 1.0 is typical, 3.0 is three times normal.
    """
    need = period + 1
    if not series.has(need):
        return Reading.insufficient("volume_anomaly", have=len(series), need=need)

    history = series.volumes[-need:-1]
    average = sum(history) / len(history)
    latest = series.volumes[-1]

    if average <= 0:
        return Reading(
            name="volume_anomaly",
            value=None,
            sample_size=len(series),
            required=need,
            detail="no volume history to compare against",
        )

    ratio = latest / average
    if ratio >= 3.0:
        detail = "extreme volume spike"
    elif ratio >= 2.0:
        detail = "elevated volume"
    elif ratio <= 0.3:
        detail = "unusually thin volume"
    else:
        detail = "volume within normal range"

    return Reading(
        name="volume_anomaly",
        value=ratio,
        sample_size=len(series),
        required=need,
        components={"latest": latest, "average": average},
        detail=detail,
    )


def volume_trend(series: CandleSeries, period: int = 20) -> Reading:
    """Recent average volume against the prior window - accumulation evidence."""
    need = period * 2
    if not series.has(need):
        return Reading.insufficient("volume_trend", have=len(series), need=need)

    recent = series.volumes[-period:]
    prior = series.volumes[-need:-period]
    recent_avg = sum(recent) / len(recent)
    prior_avg = sum(prior) / len(prior)
    if prior_avg <= 0:
        return Reading(
            name="volume_trend", value=None, sample_size=len(series), required=need,
            detail="no prior volume to compare against",
        )

    change = (recent_avg - prior_avg) / prior_avg * 100.0
    return Reading(
        name="volume_trend",
        value=change,
        sample_size=len(series),
        required=need,
        detail=f"recent {period}-bar volume {change:+.1f}% vs prior",
    )


__all__ = [
    "atr",
    "bollinger",
    "ema",
    "ema_values",
    "macd",
    "momentum",
    "realised_volatility",
    "rsi",
    "sma",
    "sma_values",
    "true_ranges",
    "volume_anomaly",
    "volume_trend",
    "vwap",
]
