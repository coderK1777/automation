"""Causal higher-timeframe alignment, transparent regimes and external filters."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .arithmetic import fixed_decimal
from .indicators import atr, ema, volume_sma
from .models import Candle, CoreModel, Side, utc_timestamp, validate_candles

D = Decimal
Regime = Literal["TREND_UP", "TREND_DOWN", "RANGE", "BREAKOUT", "ILLIQUID"]


class RegimeConfig(CoreModel):
    period: int = Field(default=14, ge=2, le=1000)
    fast_period: int = Field(default=9, ge=2, le=500)
    slow_period: int = Field(default=21, ge=3, le=1000)
    trend_threshold: Decimal = Field(default=D(".002"), ge=0, lt=1)
    low_volatility: Decimal = Field(default=D(".003"), ge=0, lt=1)
    high_volatility: Decimal = Field(default=D(".02"), gt=0, lt=1)
    minimum_average_volume: Decimal = Field(default=D(0), ge=0)

    @model_validator(mode="after")
    def thresholds(self) -> RegimeConfig:
        if self.fast_period >= self.slow_period or self.low_volatility >= self.high_volatility:
            raise ValueError("regime periods or volatility thresholds are reversed")
        return self


class RegimeSnapshot(CoreModel):
    timestamp: datetime
    primary: Regime
    volatility: Literal["LOW", "NORMAL", "HIGH"]
    atr_fraction: Decimal
    trend_fraction: Decimal
    average_volume: Decimal
    breakout_side: Side | None = None
    _utc = field_validator("timestamp")(utc_timestamp)


@fixed_decimal
def classify_regimes(candles: list[Candle], config: RegimeConfig | None = None) -> list[RegimeSnapshot | None]:
    """All arrays use only current/prior closed bars; breakout excludes current bar."""
    validate_candles(candles)
    config = config or RegimeConfig()
    fast, slow = ema(candles, config.fast_period), ema(candles, config.slow_period)
    ranges, volumes = atr(candles, config.period), volume_sma(candles, config.period)
    result: list[RegimeSnapshot | None] = []
    for index, candle in enumerate(candles):
        if any(value is None for value in (fast[index], slow[index], ranges[index], volumes[index])):
            result.append(None)
            continue
        trend = (fast[index] - slow[index]) / candle.close
        volatility = ranges[index] / candle.close
        vol_label = "HIGH" if volatility >= config.high_volatility else (
            "LOW" if volatility <= config.low_volatility else "NORMAL"
        )
        previous = candles[max(0, index - config.period):index]
        breakout = None
        if len(previous) == config.period:
            if candle.close > max(bar.high for bar in previous):
                breakout = Side.BUY
            elif candle.close < min(bar.low for bar in previous):
                breakout = Side.SELL
        primary = "TREND_UP" if trend > config.trend_threshold else (
            "TREND_DOWN" if trend < -config.trend_threshold else "RANGE"
        )
        if breakout is not None:
            primary = "BREAKOUT"
        if volumes[index] < config.minimum_average_volume:
            primary = "ILLIQUID"
        result.append(RegimeSnapshot(
            timestamp=candle.timestamp + timedelta(seconds=candle.timeframe_seconds),
            primary=primary, volatility=vol_label, atr_fraction=volatility,
            trend_fraction=trend, average_volume=volumes[index], breakout_side=breakout,
        ))
    return result


class HigherTimeframeDependency(CoreModel):
    timeframe_seconds: int = Field(gt=0)
    fast_period: int = Field(default=9, ge=2, le=500)
    slow_period: int = Field(default=21, ge=3, le=1000)
    alignment: Literal["SAME_DIRECTION"] = "SAME_DIRECTION"

    @model_validator(mode="after")
    def periods(self) -> HigherTimeframeDependency:
        if self.fast_period >= self.slow_period:
            raise ValueError("higher timeframe fast period must be less than slow")
        return self


class HigherTimeframeSeries:
    def __init__(self, dependency: HigherTimeframeDependency, candles: list[Candle]):
        validate_candles(candles, allow_open_last=True)
        self.candles = candles[:-1] if candles and not candles[-1].closed else candles
        if self.candles and self.candles[0].timeframe_seconds != dependency.timeframe_seconds:
            raise ValueError("higher timeframe dependency does not match dataset")
        self.close_times = [bar.timestamp + timedelta(seconds=bar.timeframe_seconds) for bar in self.candles]
        self.fast = ema(self.candles, dependency.fast_period)
        self.slow = ema(self.candles, dependency.slow_period)

    def index_at(self, available_at: datetime) -> int | None:
        index = bisect_right(self.close_times, available_at) - 1
        # Missing/really old higher timeframe data fails closed, even if valid.
        if index < 0 or available_at - self.close_times[index] >= timedelta(
            seconds=self.candles[index].timeframe_seconds
        ):
            return None
        return index

    def allows(self, available_at: datetime, side: Side) -> bool:
        index = self.index_at(available_at)
        return index is not None and self.fast[index] is not None and self.slow[index] is not None and (
            self.fast[index] > self.slow[index] if side is Side.BUY else self.fast[index] < self.slow[index]
        )


class TradingSession(CoreModel):
    """UTC sessions, start inclusive/end exclusive; equal hours denotes all day."""
    start_hour_utc: int = Field(default=0, ge=0, le=23)
    end_hour_utc: int = Field(default=0, ge=0, le=23)
    weekdays: tuple[int, ...] = Field(default=(0, 1, 2, 3, 4, 5, 6), min_length=1, max_length=7)

    @field_validator("weekdays")
    @classmethod
    def valid_days(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(value)) != len(value) or any(day < 0 or day > 6 for day in value):
            raise ValueError("session weekdays must be unique values 0..6")
        return value

    def allows(self, timestamp: datetime) -> bool:
        timestamp = utc_timestamp(timestamp)
        if timestamp.weekday() not in self.weekdays:
            return False
        start, end, hour = self.start_hour_utc, self.end_hour_utc, timestamp.hour
        return start == end or (start <= hour < end if start < end else hour >= start or hour < end)


class EconomicEvent(CoreModel):
    event_id: str = Field(min_length=1)
    timestamp: datetime
    published_at: datetime
    impact: Literal["LOW", "MEDIUM", "HIGH"]
    source: str = Field(min_length=1)
    _utc = field_validator("timestamp", "published_at")(utc_timestamp)


class EvaluationContext(CoreModel):
    """External observations carry provenance; absent required observations block entry."""
    events: tuple[EconomicEvent, ...] = ()
    event_feed_observed_at: datetime | None = None
    funding_rate: Decimal | None = None
    funding_observed_at: datetime | None = None
    _utc = field_validator("event_feed_observed_at", "funding_observed_at")(
        lambda value: utc_timestamp(value) if value else None
    )


class ExternalFilters(CoreModel):
    event_filter_enabled: bool = False
    event_before_seconds: int = Field(default=1800, ge=0, le=86400)
    event_after_seconds: int = Field(default=900, ge=0, le=86400)
    event_feed_max_age_seconds: int = Field(default=86400, ge=1)
    maximum_absolute_funding_rate: Decimal | None = Field(default=None, ge=0, lt=1)
    funding_max_age_seconds: int = Field(default=28800, ge=1)

    def allows(self, available_at: datetime, context: EvaluationContext | None) -> bool:
        if self.event_filter_enabled:
            if context is None or context.event_feed_observed_at is None:
                return False
            age = (available_at - context.event_feed_observed_at).total_seconds()
            if age < 0 or age > self.event_feed_max_age_seconds:
                return False
            for event in context.events:
                distance = (event.timestamp - available_at).total_seconds()
                if event.published_at <= available_at and event.impact == "HIGH" and (
                    -self.event_after_seconds <= distance <= self.event_before_seconds
                ):
                    return False
        if self.maximum_absolute_funding_rate is not None:
            if context is None or context.funding_rate is None or context.funding_observed_at is None:
                return False
            age = (available_at - context.funding_observed_at).total_seconds()
            if age < 0 or age > self.funding_max_age_seconds or abs(context.funding_rate) > self.maximum_absolute_funding_rate:
                return False
        return True
