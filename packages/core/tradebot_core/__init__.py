"""TradeBot Pro pure, deterministic quantitative domain API."""

from .models import Candle, Instrument, Side, Signal, validate_candles
from .strategy import Operand, RuleNode, StrategyConfig, StrategyEvaluator, evaluate_rule, evaluate_strategy
from .execution import PaperFill, PaperFillModel, OrderState, OrderStatus, round_increment, transition_order
from .risk import PositionSize, PositionSizingConfig, RiskLimits, RiskContext, RiskDecision, evaluate_risk, size_position
from .backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    BacktestMetrics,
    BacktestTrade,
    FundingEvent,
    calculate_metrics,
)
from .context import (
    EconomicEvent, EvaluationContext, ExternalFilters, HigherTimeframeDependency,
    RegimeConfig, RegimeSnapshot, TradingSession, classify_regimes,
)
from .simulation import (
    AdvancedExecutionConfig, ProtectionRules, SimulatedOrderEvent, TargetLeg,
    break_even_reference_price, isolated_liquidation_price, limit_fill, next_protective_stop, volume_capacity,
)
from .research import (
    BenchmarkReport, CorrelationReport, MonteCarloReport, PortfolioReport, ValidationReport,
    benchmark_comparison, cost_stress, monte_carlo, out_of_sample,
    parameter_stability, portfolio_correlations, regime_attribution, simulate_portfolio, walk_forward,
)

__all__ = [
    "Candle",
    "Instrument",
    "Side",
    "Signal",
    "validate_candles",
    "Operand",
    "RuleNode",
    "StrategyConfig",
    "StrategyEvaluator",
    "evaluate_rule",
    "evaluate_strategy",
    "PaperFill",
    "PaperFillModel",
    "OrderState",
    "OrderStatus",
    "round_increment",
    "transition_order",
    "PositionSize",
    "PositionSizingConfig",
    "RiskLimits",
    "RiskContext",
    "RiskDecision",
    "evaluate_risk",
    "size_position",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BacktestMetrics",
    "BacktestTrade",
    "FundingEvent",
    "calculate_metrics",
    "EconomicEvent", "EvaluationContext", "ExternalFilters", "HigherTimeframeDependency",
    "RegimeConfig", "RegimeSnapshot", "TradingSession", "classify_regimes",
    "AdvancedExecutionConfig", "ProtectionRules", "SimulatedOrderEvent", "TargetLeg",
    "isolated_liquidation_price", "limit_fill", "next_protective_stop", "volume_capacity",
    "break_even_reference_price",
    "BenchmarkReport", "CorrelationReport", "MonteCarloReport", "PortfolioReport", "ValidationReport",
    "benchmark_comparison", "cost_stress", "monte_carlo", "out_of_sample",
    "parameter_stability", "portfolio_correlations", "regime_attribution", "simulate_portfolio", "walk_forward",
]
