"""Closed-candle strategy evaluation and a typed, bounded rule tree (no eval)."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .arithmetic import fixed_decimal

from .indicators import (
    adx, atr, bollinger_bands, donchian_channels, ema, keltner_channels,
    macd, pivot_points, rsi, sma, stochastic, supertrend, volume_sma, vwap, wma,
)
from .models import Candle, CoreModel, Side, Signal, validate_candles
from .context import (
    EvaluationContext, ExternalFilters, HigherTimeframeDependency, HigherTimeframeSeries,
    Regime, RegimeConfig, TradingSession, classify_regimes,
)
from .simulation import AdvancedExecutionConfig, ProtectionRules
from .risk import PositionSizingConfig

Feature = Literal[
    "close", "open", "high", "low", "volume", "ema_fast", "ema_slow", "sma", "rsi", "upper_band", "lower_band",
    "atr", "atr_fraction", "adx", "vwap", "volume_average", "support", "resistance", "momentum",
    "bullish_engulfing", "bearish_engulfing", "body_fraction",
    "wma", "macd_line", "macd_signal", "macd_histogram", "stochastic_k", "stochastic_d",
    "donchian_upper", "donchian_lower", "keltner_upper", "keltner_lower", "supertrend", "supertrend_direction",
    "pivot", "pivot_r1", "pivot_s1", "plus_di", "minus_di",
]


class Operand(CoreModel):
    feature: Feature | None = None
    value: Decimal | None = None

    @model_validator(mode="after")
    def one_source(self) -> Operand:
        if (self.feature is None) == (self.value is None):
            raise ValueError("operand must contain exactly one feature or literal value")
        return self


class RuleNode(CoreModel):
    op: Literal["AND", "OR", "NOT", "GT", "GTE", "LT", "LTE", "EQ", "CROSS_ABOVE", "CROSS_BELOW"]
    children: tuple[RuleNode, ...] = Field(default=(), max_length=20)
    left: Operand | None = None
    right: Operand | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> RuleNode:
        boolean = self.op in ("AND", "OR", "NOT")
        if boolean:
            if self.left is not None or self.right is not None:
                raise ValueError("boolean nodes use children")
            if self.op == "NOT" and len(self.children) != 1:
                raise ValueError("NOT must have exactly one child")
            if self.op in ("AND", "OR") and not self.children:
                raise ValueError("AND/OR must have at least one child")
        elif self.children or self.left is None or self.right is None:
            raise ValueError("comparison nodes require left/right operands and no children")
        # Depth and total node budget protect configuration parsing/evaluation.
        stack: list[tuple[RuleNode, int]] = [(self, 1)]
        count = 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if depth > 12 or count > 100:
                raise ValueError("rule tree exceeds depth/node budget")
            stack.extend((child, depth + 1) for child in node.children)
        return self


class StrategyConfig(CoreModel):
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(default=1, ge=1)
    kind: Literal["EMA_CROSS", "BREAKOUT", "MEAN_REVERSION", "MOMENTUM", "VOLATILITY", "SUPPORT_RESISTANCE", "TREND_FOLLOWING", "CUSTOM"] = "EMA_CROSS"
    description: str = Field(default="", max_length=4000)
    creator: str = Field(default="operator", min_length=1, max_length=120)
    source: str = Field(default="operator-defined", min_length=1, max_length=1000)
    trading_style: Literal["SCALPING", "INTRADAY", "SWING", "TREND_FOLLOWING", "BREAKOUT", "MOMENTUM", "MEAN_REVERSION", "VOLATILITY", "SUPPORT_RESISTANCE", "MULTI_TIMEFRAME", "EVENT_FILTERED", "CUSTOM"] = "INTRADAY"
    allowed_environments: tuple[Literal["BACKTEST", "PAPER", "DEMO", "LIVE"], ...] = ("BACKTEST", "PAPER")
    indicator_dependencies: tuple[Feature, ...] = Field(default=(), max_length=50)
    symbol: str = Field(default="BTCUSD", min_length=1, max_length=64)
    timeframe_seconds: int = Field(default=60, ge=1)
    fast_period: int = Field(default=9, ge=2, le=500)
    slow_period: int = Field(default=21, ge=3, le=1000)
    lookback: int = Field(default=20, ge=2, le=1000)
    rsi_period: int = Field(default=14, ge=2, le=500)
    rsi_lower: Decimal = Field(default=Decimal("30"), ge=0, le=100)
    rsi_upper: Decimal = Field(default=Decimal("70"), ge=0, le=100)
    stop_loss_pct: Decimal = Field(default=Decimal(".01"), gt=0, lt=1)
    take_profit_pct: Decimal = Field(default=Decimal(".02"), gt=0, lt=1)
    risk_fraction: Decimal = Field(default=Decimal(".01"), gt=0, le=Decimal(".05"))
    max_leverage: Decimal = Field(default=Decimal("2"), ge=1, le=100)
    allow_short: bool = True
    long_rules: RuleNode | None = None
    short_rules: RuleNode | None = None
    long_exit_rules: RuleNode | None = None
    short_exit_rules: RuleNode | None = None
    higher_timeframes: tuple[HigherTimeframeDependency, ...] = Field(default=(), max_length=5)
    regime_config: RegimeConfig = Field(default_factory=RegimeConfig)
    allowed_regimes: tuple[Regime, ...] = Field(default=(), max_length=5)
    allowed_volatility: tuple[Literal["LOW", "NORMAL", "HIGH"], ...] = Field(default=(), max_length=3)
    session: TradingSession | None = None
    filters: ExternalFilters = Field(default_factory=ExternalFilters)
    cooldown_bars: int = Field(default=0, ge=0, le=10000)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=10000)
    max_daily_loss_fraction: Decimal | None = Field(default=None, gt=0, le=1)
    capital_allocation_fraction: Decimal | None = Field(default=None, gt=0, le=1)
    max_consecutive_losses: int | None = Field(default=None, ge=1, le=10000)
    max_drawdown_fraction: Decimal | None = Field(default=None, gt=0, le=1)
    max_open_positions: int | None = Field(default=None, ge=1, le=10000)
    min_liquidation_distance_fraction: Decimal | None = Field(default=None, gt=0, lt=1)
    sizing: PositionSizingConfig = Field(default_factory=PositionSizingConfig)
    stop_method: Literal["FIXED_PCT", "ATR", "STRUCTURE", "FIXED_PRICE"] = "FIXED_PCT"
    atr_stop_multiple: Decimal = Field(default=Decimal(2), gt=0, le=100)
    stop_reference_price: Decimal | None = Field(default=None, gt=0)
    reward_risk_target: Decimal | None = Field(default=None, gt=0, le=100)
    target_reference_price: Decimal | None = Field(default=None, gt=0)
    exit_rules: ProtectionRules = Field(default_factory=ProtectionRules)
    execution: AdvancedExecutionConfig | None = None

    @model_validator(mode="after")
    def validate_periods(self) -> StrategyConfig:
        if self.fast_period >= self.slow_period:
            raise ValueError("fast period must be less than slow period")
        if self.rsi_lower >= self.rsi_upper:
            raise ValueError("RSI lower threshold must be less than upper")
        if self.kind == "CUSTOM" and self.long_rules is None and self.short_rules is None:
            raise ValueError("custom strategy requires explicit rule trees")
        if not self.allow_short and self.long_rules is None and self.kind == "CUSTOM":
            raise ValueError("short-only rule tree conflicts with allow_short=False")
        dependencies = [item.timeframe_seconds for item in self.higher_timeframes]
        if len(set(dependencies)) != len(dependencies) or any(
            seconds <= self.timeframe_seconds or seconds % self.timeframe_seconds for seconds in dependencies
        ):
            raise ValueError("higher timeframes must be unique larger multiples of base timeframe")
        if self.stop_method == "FIXED_PRICE" and self.stop_reference_price is None:
            raise ValueError("fixed-price stop requires explicit stop_reference_price")
        if not self.allowed_environments or len(set(self.allowed_environments)) != len(self.allowed_environments):
            raise ValueError("allowed environments must be nonempty and unique")
        if self.min_liquidation_distance_fraction is not None and (
            self.execution is None or self.execution.maintenance_margin_rate is None
        ):
            raise ValueError("minimum liquidation distance requires an explicit maintenance-margin execution model")
        return self


@fixed_decimal
def evaluate_rule(
    node: RuleNode, features: dict[str, Decimal | None], previous: dict[str, Decimal | None] | None = None
) -> bool | None:
    """Three-valued logic: warm-up/missing values cannot become entry via NOT."""
    if node.op in ("AND", "OR", "NOT"):
        values = [evaluate_rule(child, features, previous) for child in node.children]
        if node.op == "NOT":
            return None if values[0] is None else not values[0]
        if node.op == "AND":
            return False if False in values else (None if None in values else True)
        return True if True in values else (None if None in values else False)
    assert node.left is not None and node.right is not None

    def resolve(operand: Operand, source: dict[str, Decimal | None]) -> Decimal | None:
        return operand.value if operand.value is not None else source.get(str(operand.feature))

    left, right = resolve(node.left, features), resolve(node.right, features)
    if left is None or right is None:
        return None
    if node.op in ("CROSS_ABOVE", "CROSS_BELOW"):
        if previous is None:
            return None
        old_left, old_right = resolve(node.left, previous), resolve(node.right, previous)
        if old_left is None or old_right is None:
            return None
        return (
            (left > right and old_left <= old_right)
            if node.op == "CROSS_ABOVE"
            else (left < right and old_left >= old_right)
        )
    return {"GT": left > right, "GTE": left >= right, "LT": left < right, "LTE": left <= right, "EQ": left == right}[
        node.op
    ]


def _features(config: StrategyConfig, candles: list[Candle]) -> list[dict[str, Decimal | None]]:
    fast, slow = ema(candles, config.fast_period), ema(candles, config.slow_period)
    averages, strengths = sma(candles, config.lookback), rsi(candles, config.rsi_period)
    bands = bollinger_bands(candles, config.lookback)
    ranges, trends, vwaps, volumes = atr(candles, config.rsi_period), adx(candles, config.rsi_period), vwap(candles), volume_sma(candles, config.lookback)
    weighted = wma(candles,config.lookback)
    macds = macd(candles,fast=config.fast_period,slow=config.slow_period)
    stochastics = stochastic(candles,period=config.rsi_period)
    donchians = donchian_channels(candles,config.lookback,previous_closed_window=True)
    keltners = keltner_channels(candles,config.lookback,atr_period=config.rsi_period)
    supertrends = supertrend(candles,config.rsi_period)
    pivots = pivot_points(candles,session_seconds=None)
    result = []
    for index, candle in enumerate(candles):
        band = bands[index]
        result.append(
            {
                "close": candle.close,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "volume": candle.volume,
                "ema_fast": fast[index],
                "ema_slow": slow[index],
                "sma": averages[index],
                "rsi": strengths[index],
                "upper_band": band.upper if band else None,
                "lower_band": band.lower if band else None,
                "atr": ranges[index],
                "atr_fraction": ranges[index] / candle.close if ranges[index] is not None else None,
                "adx": trends[index].adx if trends[index] else None,
                "vwap": vwaps[index],
                "volume_average": volumes[index],
                "support": min(c.low for c in candles[index-config.lookback:index]) if index >= config.lookback else None,
                "resistance": max(c.high for c in candles[index-config.lookback:index]) if index >= config.lookback else None,
                "momentum": candle.close / candles[index-config.lookback].close - 1 if index >= config.lookback else None,
                "bullish_engulfing": Decimal(int(index > 0 and candles[index-1].close < candles[index-1].open and candle.close > candle.open and candle.open <= candles[index-1].close and candle.close >= candles[index-1].open)),
                "bearish_engulfing": Decimal(int(index > 0 and candles[index-1].close > candles[index-1].open and candle.close < candle.open and candle.open >= candles[index-1].close and candle.close <= candles[index-1].open)),
                "body_fraction": abs(candle.close-candle.open)/(candle.high-candle.low) if candle.high > candle.low else Decimal(0),
                "wma":weighted[index],
                "macd_line":macds[index].line if macds[index] else None,
                "macd_signal":macds[index].signal if macds[index] else None,
                "macd_histogram":macds[index].histogram if macds[index] else None,
                "stochastic_k":stochastics[index].k if stochastics[index] else None,
                "stochastic_d":stochastics[index].d if stochastics[index] else None,
                "donchian_upper":donchians[index].upper if donchians[index] else None,
                "donchian_lower":donchians[index].lower if donchians[index] else None,
                "keltner_upper":keltners[index].upper if keltners[index] else None,
                "keltner_lower":keltners[index].lower if keltners[index] else None,
                "supertrend":supertrends[index].value if supertrends[index] else None,
                "supertrend_direction":Decimal(supertrends[index].direction) if supertrends[index] else None,
                "pivot":pivots[index].pivot if pivots[index] else None,
                "pivot_r1":pivots[index].r1 if pivots[index] else None,
                "pivot_s1":pivots[index].s1 if pivots[index] else None,
                "plus_di":trends[index].plus_di if trends[index] else None,
                "minus_di":trends[index].minus_di if trends[index] else None,
            }
        )
    return result


class StrategyEvaluator:
    """Per-run causal indicator cache; no hidden mutable state is shared.

    Computing an indicator array once is safe because each aligned indicator
    uses only that candle and prior candles. signal_at never inspects later
    values; tests compare it to independent prefix-only evaluations.
    """

    @fixed_decimal
    def __init__(self, config: StrategyConfig, candles: list[Candle], *, higher_timeframes: dict[int, list[Candle]] | None = None, context: EvaluationContext | None = None):
        validate_candles(candles, allow_open_last=True)
        closed = candles[:-1] if candles and not candles[-1].closed else candles
        if closed and closed[0].timeframe_seconds != config.timeframe_seconds:
            raise ValueError("strategy and candle timeframe differ")
        self.config = config
        self.context = context
        self.higher = {
            dependency.timeframe_seconds: HigherTimeframeSeries(dependency, (higher_timeframes or {}).get(dependency.timeframe_seconds, []))
            for dependency in config.higher_timeframes
        }
        self.candles = tuple(closed)
        self.fast = ema(closed, config.fast_period) if config.kind in ("EMA_CROSS", "TREND_FOLLOWING") else []
        self.slow = ema(closed, config.slow_period) if config.kind in ("EMA_CROSS", "TREND_FOLLOWING") else []
        self.bands = bollinger_bands(closed, config.lookback) if config.kind == "MEAN_REVERSION" else []
        self.strengths = rsi(closed, config.rsi_period) if config.kind == "MEAN_REVERSION" else []
        self.features = _features(config, closed) if config.kind in ("CUSTOM", "MOMENTUM", "VOLATILITY", "SUPPORT_RESISTANCE") or config.long_exit_rules or config.short_exit_rules else []
        self.ranges = atr(closed, config.rsi_period) if config.stop_method == "ATR" else []
        self.regimes = classify_regimes(closed, config.regime_config) if config.allowed_regimes or config.allowed_volatility else []
        digest = hashlib.sha256(b"[")
        self.snapshots = []
        for index, candle in enumerate(closed):
            if index:
                digest.update(b",")
            digest.update(json.dumps(candle.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode())
            prefix = digest.copy()
            prefix.update(b"]")
            self.snapshots.append(prefix.hexdigest())

    @fixed_decimal
    def signal_at(self, index: int, *, context: EvaluationContext | None = None) -> Signal | None:
        if index < 0 or index >= len(self.candles):
            raise IndexError("closed candle index outside prepared history")
        config, candle = self.config, self.candles[index]
        available_at = candle.timestamp + timedelta(seconds=candle.timeframe_seconds)
        if config.session and not config.session.allows(available_at):
            return None
        if not config.filters.allows(available_at, context or self.context):
            return None
        if self.regimes:
            regime = self.regimes[index]
            if regime is None or (config.allowed_regimes and regime.primary not in config.allowed_regimes) or (
                config.allowed_volatility and regime.volatility not in config.allowed_volatility
            ):
                return None
        side = None
        if config.kind == "EMA_CROSS":
            fast, slow = self.fast, self.slow
            if index < 1 or any(
                value is None for value in (fast[index], slow[index], fast[index - 1], slow[index - 1])
            ):
                return None
            if fast[index] > slow[index] and fast[index - 1] <= slow[index - 1]:
                side = Side.BUY
            elif fast[index] < slow[index] and fast[index - 1] >= slow[index - 1]:
                side = Side.SELL
        elif config.kind == "TREND_FOLLOWING":
            if self.fast[index] is None or self.slow[index] is None:
                return None
            side = Side.BUY if self.fast[index] > self.slow[index] and candle.close > self.fast[index] else (
                Side.SELL if self.fast[index] < self.slow[index] and candle.close < self.fast[index] else None
            )
        elif config.kind == "BREAKOUT":
            if index < config.lookback:
                return None
            previous = self.candles[index - config.lookback : index]
            if candle.close > max(c.high for c in previous):
                side = Side.BUY
            elif candle.close < min(c.low for c in previous):
                side = Side.SELL
        elif config.kind == "MEAN_REVERSION":
            band, strength = self.bands[index], self.strengths[index]
            if band is None or strength is None:
                return None
            if candle.close < band.lower and strength < config.rsi_lower:
                side = Side.BUY
            elif candle.close > band.upper and strength > config.rsi_upper:
                side = Side.SELL
        elif config.kind in ("MOMENTUM", "VOLATILITY", "SUPPORT_RESISTANCE"):
            features = self.features[index]
            if config.kind == "MOMENTUM":
                strength, momentum = features["rsi"], features["momentum"]
                if strength is not None and momentum is not None:
                    side = Side.BUY if momentum > 0 and strength >= config.rsi_upper else (
                        Side.SELL if momentum < 0 and strength <= config.rsi_lower else None
                    )
            elif config.kind == "VOLATILITY":
                if features["atr_fraction"] is not None and features["atr_fraction"] >= config.regime_config.high_volatility:
                    if features["resistance"] is not None and candle.close > features["resistance"]:
                        side = Side.BUY
                    elif features["support"] is not None and candle.close < features["support"]:
                        side = Side.SELL
            elif index:
                support, resistance = features["support"], features["resistance"]
                if support is not None and candle.low <= support and candle.close > support and candle.close > candle.open:
                    side = Side.BUY
                elif resistance is not None and candle.high >= resistance and candle.close < resistance and candle.close < candle.open:
                    side = Side.SELL
        else:
            features = self.features[index]
            previous = self.features[index - 1] if index else None
            long = config.long_rules is not None and evaluate_rule(config.long_rules, features, previous) is True
            short = (
                config.allow_short
                and config.short_rules is not None
                and evaluate_rule(config.short_rules, features, previous) is True
            )
            if long and short:
                return None
            side = Side.BUY if long else (Side.SELL if short else None)
        if side is None or (side is Side.SELL and not config.allow_short):
            return None
        if any(not series.allows(available_at, side) for series in self.higher.values()):
            return None
        stop = candle.close * (1 - side.sign * config.stop_loss_pct)
        if config.stop_method == "ATR":
            if self.ranges[index] is None:
                return None
            stop = candle.close - side.sign * self.ranges[index] * config.atr_stop_multiple
        elif config.stop_method == "STRUCTURE":
            if index < config.lookback:
                return None
            window = self.candles[index-config.lookback:index]
            stop = min(bar.low for bar in window) if side is Side.BUY else max(bar.high for bar in window)
        elif config.stop_method == "FIXED_PRICE":
            stop = config.stop_reference_price
        if stop <= 0 or (candle.close - stop) * side.sign <= 0:
            return None
        target = candle.close * (1 + side.sign * config.take_profit_pct)
        if config.reward_risk_target is not None:
            target = candle.close + side.sign * abs(candle.close - stop) * config.reward_risk_target
        if config.target_reference_price is not None:
            target = config.target_reference_price
        if (target-candle.close)*side.sign <= 0:
            return None
        signature = json.dumps(
            {
                "config": config.model_dump(mode="json"),
                "candle_timestamp": candle.timestamp.isoformat(),
                "side": side.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot = self.snapshots[index]
        if self.higher:
            digest = {"base": snapshot, "higher": {}}
            for seconds, series in self.higher.items():
                known = series.index_at(available_at)
                digest["higher"][str(seconds)] = [bar.model_dump(mode="json") for bar in series.candles[:known+1]]
            snapshot = hashlib.sha256(json.dumps(digest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        observations = context or self.context
        if observations is not None and (config.filters.event_filter_enabled or config.filters.maximum_absolute_funding_rate is not None):
            observed = observations.model_dump(mode="json")
            observed["events"] = [event.model_dump(mode="json") for event in sorted(
                (event for event in observations.events if event.published_at <= available_at),key=lambda event:event.event_id
            )]
            snapshot = hashlib.sha256(json.dumps({"market":snapshot,"observations":observed},sort_keys=True,separators=(",", ":")).encode()).hexdigest()
        return Signal(
            signal_id=hashlib.sha256(signature.encode()).hexdigest(),
            strategy_version=config.version,
            symbol=config.symbol,
            side=side,
            timestamp=candle.timestamp + timedelta(seconds=candle.timeframe_seconds),
            candle_timestamp=candle.timestamp,
            reference_price=candle.close,
            stop_price=stop,
            target_price=target,
            reason=config.kind,
            data_snapshot_id=snapshot,
        )

    @fixed_decimal
    def exit_at(self, index: int, side: Side) -> bool | None:
        if index < 0 or index >= len(self.candles):
            raise IndexError("closed candle index outside prepared history")
        rules = self.config.long_exit_rules if side is Side.BUY else self.config.short_exit_rules
        if rules is None:
            return False
        return evaluate_rule(rules,self.features[index],self.features[index-1] if index else None)


@fixed_decimal
def evaluate_strategy(config: StrategyConfig, candles: list[Candle], *, higher_timeframes: dict[int, list[Candle]] | None = None, context: EvaluationContext | None = None) -> Signal | None:
    evaluator = StrategyEvaluator(config, candles, higher_timeframes=higher_timeframes, context=context)
    return evaluator.signal_at(len(evaluator.candles) - 1) if evaluator.candles else None
