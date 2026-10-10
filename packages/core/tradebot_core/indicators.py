"""Deterministic Decimal indicators over consecutive, closed OHLCV candles.

Results always align with input candles. ``None`` means insufficient history;
an optional final open candle also receives ``None`` and never enters a
calculation. Missing intervals, duplicate timestamps and interior open candles
are rejected rather than silently bridged. Decimal precision is fixed at 34.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Literal, Sequence

from .models import Candle, validate_candles


Source = Literal["open", "high", "low", "close", "volume", "hl2", "hlc3"]
_ZERO = Decimal(0)
_ONE = Decimal(1)
_HUNDRED = Decimal(100)
_PRECISION = 34
_CONTEXT = Context(prec=_PRECISION, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class MACDValue:
    line: Decimal
    signal: Decimal | None
    histogram: Decimal | None


@dataclass(frozen=True)
class BollingerBands:
    middle: Decimal
    upper: Decimal
    lower: Decimal
    standard_deviation: Decimal


@dataclass(frozen=True)
class StochasticValue:
    k: Decimal
    d: Decimal | None


@dataclass(frozen=True)
class DonchianChannels:
    upper: Decimal
    middle: Decimal
    lower: Decimal


@dataclass(frozen=True)
class ADXValue:
    plus_di: Decimal
    minus_di: Decimal
    adx: Decimal | None


@dataclass(frozen=True)
class KeltnerChannels:
    middle: Decimal
    upper: Decimal
    lower: Decimal


@dataclass(frozen=True)
class SupertrendValue:
    value: Decimal
    direction: Literal[-1, 1]
    upper: Decimal
    lower: Decimal


@dataclass(frozen=True)
class PivotPoints:
    pivot: Decimal
    r1: Decimal
    r2: Decimal
    r3: Decimal
    s1: Decimal
    s2: Decimal
    s3: Decimal


def _period(value: int, name: str = "period") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_decimal(value: Decimal | int | str, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be a finite positive Decimal, integer or string")
    try:
        result = Decimal(value)
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise ValueError(f"{name} must be a finite positive decimal") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{name} must be a finite positive decimal")
    return result


def _closed(candles: Sequence[Candle]) -> list[Candle]:
    validate_candles(candles, allow_open_last=True)
    return list(candles[:-1] if candles and not candles[-1].closed else candles)


def _source_values(candles: Sequence[Candle], source: Source) -> list[Decimal]:
    if source not in {"open", "high", "low", "close", "volume", "hl2", "hlc3"}:
        raise ValueError("unsupported price source")
    if source == "hl2":
        return [(c.high + c.low) / 2 for c in candles]
    if source == "hlc3":
        return [(c.high + c.low + c.close) / 3 for c in candles]
    return [getattr(c, source) for c in candles]


def _aligned(values: list, candles: Sequence[Candle]) -> list:
    return values + [None] * (len(candles) - len(values))


def _sma_values(values: Sequence[Decimal | None], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = []
    window: deque[Decimal] = deque()
    total = _ZERO
    for value in values:
        if value is None:
            window.clear()
            total = _ZERO
            result.append(None)
            continue
        window.append(value)
        total += value
        if len(window) > period:
            total -= window.popleft()
        result.append(total / period if len(window) == period else None)
    return result


def _ema_values(values: Sequence[Decimal | None], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = []
    seed: list[Decimal] = []
    average: Decimal | None = None
    alpha = Decimal(2) / (period + 1)
    for value in values:
        if value is None:
            seed.clear()
            average = None
        elif average is None:
            seed.append(value)
            if len(seed) == period:
                average = sum(seed, _ZERO) / period
        else:
            average = average + alpha * (value - average)
        result.append(average)
    return result


def _wilder_values(values: Sequence[Decimal | None], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = []
    seed: list[Decimal] = []
    average: Decimal | None = None
    for value in values:
        if value is None:
            seed.clear()
            average = None
        elif average is None:
            seed.append(value)
            if len(seed) == period:
                average = sum(seed, _ZERO) / period
        else:
            average = (average * (period - 1) + value) / period
        result.append(average)
    return result


def sma(candles: Sequence[Candle], period: int = 20, source: Source = "close") -> list[Decimal | None]:
    """Simple mean; first value at index ``period - 1``."""
    _period(period)
    with localcontext(_CONTEXT):
        return _aligned(_sma_values(_source_values(_closed(candles), source), period), candles)


def ema(candles: Sequence[Candle], period: int = 20, source: Source = "close") -> list[Decimal | None]:
    """EMA seeded with a full SMA, then alpha = 2 / (period + 1)."""
    _period(period)
    with localcontext(_CONTEXT):
        return _aligned(_ema_values(_source_values(_closed(candles), source), period), candles)


def wma(candles: Sequence[Candle], period: int = 20, source: Source = "close") -> list[Decimal | None]:
    """Linear weights 1..period, with highest weight on the newest candle."""
    _period(period)
    with localcontext(_CONTEXT):
        values = _source_values(_closed(candles), source)
        divisor = Decimal(period * (period + 1)) / 2
        result = [
            None if index + 1 < period else sum(
                (value * weight for weight, value in enumerate(values[index + 1 - period:index + 1], 1)),
                _ZERO,
            ) / divisor
            for index in range(len(values))
        ]
        return _aligned(result, candles)


def rsi(candles: Sequence[Candle], period: int = 14, source: Source = "close") -> list[Decimal | None]:
    """Wilder RSI seeded by ``period`` changes; first value at index period.

    A flat seed produces 50; gains with zero losses produce 100 and losses
    with zero gains produce 0. This convention is explicit for flat markets.
    """
    _period(period)
    with localcontext(_CONTEXT):
        values = _source_values(_closed(candles), source)
        if not values:
            return [None] * len(candles)
        changes = [new - old for old, new in zip(values, values[1:])]
        gains = _wilder_values([max(value, _ZERO) for value in changes], period)
        losses = _wilder_values([max(-value, _ZERO) for value in changes], period)
        result: list[Decimal | None] = [None]
        for gain, loss in zip(gains, losses):
            if gain is None or loss is None:
                result.append(None)
            elif gain == 0 and loss == 0:
                result.append(Decimal(50))
            elif loss == 0:
                result.append(_HUNDRED)
            else:
                result.append(_HUNDRED - _HUNDRED / (_ONE + gain / loss))
        return _aligned(result, candles)


def macd(candles: Sequence[Candle], fast: int = 12, slow: int = 26, signal: int = 9,
         source: Source = "close") -> list[MACDValue | None]:
    """SMA-seeded fast/slow EMAs with an independently seeded signal EMA.

    The line first exists at index slow-1; its signal and histogram first
    exist at index slow+signal-2. Callers must check individual fields.
    """
    for name, value in (("fast", fast), ("slow", slow), ("signal", signal)):
        _period(value, name)
    if fast >= slow:
        raise ValueError("fast period must be smaller than slow period")
    with localcontext(_CONTEXT):
        values = _source_values(_closed(candles), source)
        fast_values = _ema_values(values, fast)
        slow_values = _ema_values(values, slow)
        lines = [None if a is None or b is None else a - b for a, b in zip(fast_values, slow_values)]
        signals = _ema_values(lines, signal)
        result = [
            None if line is None else MACDValue(line, sig, None if sig is None else line - sig)
            for line, sig in zip(lines, signals)
        ]
        return _aligned(result, candles)


def bollinger_bands(candles: Sequence[Candle], period: int = 20,
                    stddev: Decimal | int | str = Decimal("2"),
                    source: Source = "close") -> list[BollingerBands | None]:
    """SMA bands using population variance and Decimal square root."""
    _period(period)
    multiplier = _positive_decimal(stddev, "stddev")
    with localcontext(_CONTEXT):
        values = _source_values(_closed(candles), source)
        means = _sma_values(values, period)
        result: list[BollingerBands | None] = []
        for index, mean in enumerate(means):
            if mean is None:
                result.append(None)
                continue
            variance = sum(((value - mean) ** 2 for value in values[index + 1 - period:index + 1]), _ZERO) / period
            deviation = variance.sqrt()
            result.append(BollingerBands(mean, mean + multiplier * deviation,
                                         mean - multiplier * deviation, deviation))
        return _aligned(result, candles)


def _true_ranges(candles: Sequence[Candle]) -> list[Decimal]:
    """First range is high-low; subsequent ranges include previous close."""
    return [
        candle.high - candle.low if index == 0 else max(
            candle.high - candle.low,
            abs(candle.high - candles[index - 1].close),
            abs(candle.low - candles[index - 1].close),
        )
        for index, candle in enumerate(candles)
    ]


def atr(candles: Sequence[Candle], period: int = 14) -> list[Decimal | None]:
    """Wilder ATR; seed includes first high-low, available at period-1."""
    _period(period)
    with localcontext(_CONTEXT):
        return _aligned(_wilder_values(_true_ranges(_closed(candles)), period), candles)


def stochastic(candles: Sequence[Candle], period: int = 14, smooth_k: int = 3,
               smooth_d: int = 3) -> list[StochasticValue | None]:
    """Slow stochastic with SMA smoothing; zero-range windows produce 50."""
    for name, value in (("period", period), ("smooth_k", smooth_k), ("smooth_d", smooth_d)):
        _period(value, name)
    with localcontext(_CONTEXT):
        closed = _closed(candles)
        raw: list[Decimal | None] = []
        for index, candle in enumerate(closed):
            if index + 1 < period:
                raw.append(None)
                continue
            window = closed[index + 1 - period:index + 1]
            low, high = min(c.low for c in window), max(c.high for c in window)
            raw.append(Decimal(50) if high == low else _HUNDRED * (candle.close - low) / (high - low))
        k_values = _sma_values(raw, smooth_k)
        d_values = _sma_values(k_values, smooth_d)
        return _aligned([None if k is None else StochasticValue(k, d)
                         for k, d in zip(k_values, d_values)], candles)


def vwap(candles: Sequence[Candle], source: Source = "hlc3",
         reset_daily: bool = False) -> list[Decimal | None]:
    """Volume weighted mean from the first candle, optionally reset at UTC day.

    No positive accumulated volume means no defined VWAP. A zero-volume
    candle preserves the previously accumulated value within the session.
    """
    if not isinstance(reset_daily, bool):
        raise ValueError("reset_daily must be a boolean")
    with localcontext(_CONTEXT):
        closed = _closed(candles)
        prices = _source_values(closed, source)
        total_volume = _ZERO
        total_value = _ZERO
        previous_day = None
        result: list[Decimal | None] = []
        for candle, price in zip(closed, prices):
            day = candle.timestamp.date()
            if reset_daily and day != previous_day:
                total_volume = total_value = _ZERO
            previous_day = day
            total_volume += candle.volume
            total_value += price * candle.volume
            result.append(total_value / total_volume if total_volume else None)
        return _aligned(result, candles)


def volume_sma(candles: Sequence[Candle], period: int = 20) -> list[Decimal | None]:
    """Simple moving average of volume."""
    return sma(candles, period=period, source="volume")


def donchian_channels(candles: Sequence[Candle], period: int = 20,
                      previous_closed_window: bool = False) -> list[DonchianChannels | None]:
    """High/low channels over period candles.

    ``previous_closed_window=True`` excludes the candle at the result's index,
    so breakouts compare the current close to a strictly prior window. Warmup
    increases by one candle. A final open candle always receives None.
    """
    _period(period)
    if not isinstance(previous_closed_window, bool):
        raise ValueError("previous_closed_window must be a boolean")
    with localcontext(_CONTEXT):
        closed = _closed(candles)
        result: list[DonchianChannels | None] = []
        for index in range(len(closed)):
            end = index if previous_closed_window else index + 1
            if end < period:
                result.append(None)
                continue
            window = closed[end - period:end]
            upper, lower = max(c.high for c in window), min(c.low for c in window)
            result.append(DonchianChannels(upper, (upper + lower) / 2, lower))
        return _aligned(result, candles)


def adx(candles: Sequence[Candle], period: int = 14) -> list[ADXValue | None]:
    """Wilder directional movement and ADX, based on full candle transitions.

    First +/-DI is at index period. ADX needs a further period DX observations,
    first appearing at index 2*period-1. Equal directional moves contribute
    zero to both sides; a market with no movement has DI and ADX equal to 0.
    """
    _period(period)
    with localcontext(_CONTEXT):
        closed = _closed(candles)
        if not closed:
            return [None] * len(candles)
        plus_dm: list[Decimal] = []
        minus_dm: list[Decimal] = []
        for previous, current in zip(closed, closed[1:]):
            up = current.high - previous.high
            down = previous.low - current.low
            plus_dm.append(up if up > 0 and up > down else _ZERO)
            minus_dm.append(down if down > 0 and down > up else _ZERO)
        true_ranges = _wilder_values(_true_ranges(closed)[1:], period)
        smoothed_plus = _wilder_values(plus_dm, period)
        smoothed_minus = _wilder_values(minus_dm, period)
        directional: list[tuple[Decimal, Decimal] | None] = []
        dx_values: list[Decimal | None] = []
        for tr, plus, minus in zip(true_ranges, smoothed_plus, smoothed_minus):
            if tr is None or plus is None or minus is None:
                directional.append(None)
                dx_values.append(None)
                continue
            plus_di = _HUNDRED * plus / tr if tr else _ZERO
            minus_di = _HUNDRED * minus / tr if tr else _ZERO
            directional.append((plus_di, minus_di))
            total = plus_di + minus_di
            dx_values.append(_HUNDRED * abs(plus_di - minus_di) / total if total else _ZERO)
        adx_values = _wilder_values(dx_values, period)
        result = [None] + [
            None if direction is None else ADXValue(direction[0], direction[1], strength)
            for direction, strength in zip(directional, adx_values)
        ]
        return _aligned(result, candles)


def keltner_channels(candles: Sequence[Candle], period: int = 20, atr_period: int = 10,
                     multiplier: Decimal | int | str = Decimal("2"),
                     source: Source = "close") -> list[KeltnerChannels | None]:
    """Modern Keltner bands: SMA-seeded EMA +/- multiplier * Wilder ATR."""
    _period(period)
    _period(atr_period, "atr_period")
    factor = _positive_decimal(multiplier, "multiplier")
    with localcontext(_CONTEXT):
        closed = _closed(candles)
        midpoints = _ema_values(_source_values(closed, source), period)
        ranges = _wilder_values(_true_ranges(closed), atr_period)
        result = [
            None if middle is None or width is None else KeltnerChannels(
                middle, middle + factor * width, middle - factor * width,
            )
            for middle, width in zip(midpoints, ranges)
        ]
        return _aligned(result, candles)


def supertrend(candles: Sequence[Candle], period: int = 10,
               multiplier: Decimal | int | str = Decimal("3")) -> list[SupertrendValue | None]:
    """ATR trailing bands with close-confirmed trend changes.

    Direction +1 is bullish and -1 is bearish. At the first defined ATR,
    initialize bearish at its upper band; thereafter a strict close across
    the active final band changes direction. Equal-to-band closes preserve
    the previous direction. Initialization is explicit and never backfilled.
    """
    _period(period)
    factor = _positive_decimal(multiplier, "multiplier")
    with localcontext(_CONTEXT):
        closed = _closed(candles)
        ranges = _wilder_values(_true_ranges(closed), period)
        previous: SupertrendValue | None = None
        result: list[SupertrendValue | None] = []
        for index, (candle, width) in enumerate(zip(closed, ranges)):
            if width is None:
                result.append(None)
                continue
            midpoint = (candle.high + candle.low) / 2
            basic_upper, basic_lower = midpoint + factor * width, midpoint - factor * width
            if previous is None:
                current = SupertrendValue(basic_upper, -1, basic_upper, basic_lower)
            else:
                previous_close = closed[index - 1].close
                upper = basic_upper if basic_upper < previous.upper or previous_close > previous.upper else previous.upper
                lower = basic_lower if basic_lower > previous.lower or previous_close < previous.lower else previous.lower
                if previous.direction == -1:
                    direction: Literal[-1, 1] = 1 if candle.close > upper else -1
                else:
                    direction = -1 if candle.close < lower else 1
                current = SupertrendValue(lower if direction == 1 else upper, direction, upper, lower)
            result.append(current)
            previous = current
        return _aligned(result, candles)


def _pivot_levels(high: Decimal, low: Decimal, close: Decimal) -> PivotPoints:
    pivot = (high + low + close) / 3
    spread = high - low
    return PivotPoints(pivot, 2 * pivot - low, pivot + spread, high + 2 * (pivot - low),
                       2 * pivot - high, pivot - spread, low - 2 * (high - pivot))


def pivot_points(candles: Sequence[Candle], session_seconds: int | None = 86400) -> list[PivotPoints | None]:
    """Classical floor pivots from the previous fully closed UTC session.

    Sessions align to Unix epoch boundaries and default to UTC calendar days.
    A partial first session never supplies levels; all candles in a session
    use the same previous session's levels, without incorporating their own
    prices. ``session_seconds=None`` explicitly uses the previous candle.
    Session length must be a whole multiple of the candle timeframe.
    """
    if session_seconds is not None:
        _period(session_seconds, "session_seconds")
    with localcontext(_CONTEXT):
        closed = _closed(candles)
        if session_seconds is None:
            result = [] if not closed else [None] + [
                _pivot_levels(c.high, c.low, c.close) for c in closed[:-1]
            ]
            return _aligned(result, candles)
        if not closed:
            return [None] * len(candles)
        timeframe = closed[0].timeframe_seconds
        if session_seconds % timeframe:
            raise ValueError("session_seconds must be a multiple of the candle timeframe")
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        result: list[PivotPoints | None] = []
        current_session: int | None = None
        first_second = last_second = 0
        high = low = close = _ZERO
        count = 0
        levels: PivotPoints | None = None
        for candle in closed:
            second = int((candle.timestamp - epoch).total_seconds())
            session = second // session_seconds
            if session != current_session:
                complete = (
                    current_session is not None
                    and first_second == current_session * session_seconds
                    and last_second + timeframe == (current_session + 1) * session_seconds
                    and count == session_seconds // timeframe
                    and session == current_session + 1
                )
                levels = _pivot_levels(high, low, close) if complete else None
                current_session = session
                first_second = second
                high, low, count = candle.high, candle.low, 0
            result.append(levels)
            high, low, close = max(high, candle.high), min(low, candle.low), candle.close
            last_second = second
            count += 1
        return _aligned(result, candles)


# Familiar short name for chart integrations.
bbands = bollinger_bands
