"""Explicit execution capabilities for the isolated, certified DEMO engine.

Backtest policies do not become exchange capabilities by being saved in JSON.
Every strategy authorization and final entry submission checks this contract.
"""
from tradebot_core import StrategyConfig
from tradebot_core.risk import RiskLimits


def require_strategy(config: StrategyConfig) -> None:
    # Imported locally so the coordinator can invoke this guard without a cycle.
    from tradebot.demo import DemoRejected
    from tradebot.market import RESOLUTIONS

    if "DEMO" not in config.allowed_environments:
        raise DemoRejected("Strategy allowed_environments does not authorize DEMO")
    unsupported = []
    if config.execution is not None:
        unsupported.append("execution (advanced entry/expiry/volume/liquidation policy)")
    for field in ("long_exit_rules", "short_exit_rules", "max_trades_per_day", "max_daily_loss_fraction",
                  "capital_allocation_fraction", "max_consecutive_losses", "max_drawdown_fraction",
                  "max_open_positions", "min_liquidation_distance_fraction"):
        if getattr(config, field) is not None:
            unsupported.append(field)
    if config.cooldown_bars:
        unsupported.append("cooldown_bars")
    if config.filters.event_filter_enabled:
        unsupported.append("filters.event_filter_enabled (causal DEMO news context unavailable)")
    if config.filters.maximum_absolute_funding_rate is not None:
        unsupported.append("filters.maximum_absolute_funding_rate (verified DEMO rate context unavailable)")
    protection = config.exit_rules
    if protection.targets:
        unsupported.append("exit_rules.targets")
    if protection.trailing_method != "NONE" or protection.trailing_distance is not None:
        unsupported.append("exit_rules.trailing_method/trailing_distance")
    for field in ("break_even_trigger_r", "max_holding_bars", "dynamic_target_atr_multiple"):
        if getattr(protection, field) is not None:
            unsupported.append("exit_rules."+field)
    if protection.trend_invalidation_exit:
        unsupported.append("exit_rules.trend_invalidation_exit")
    if config.timeframe_seconds not in RESOLUTIONS or any(item.timeframe_seconds not in RESOLUTIONS for item in config.higher_timeframes):
        unsupported.append("timeframe_seconds/higher_timeframes (unsupported official candle resolution)")
    if unsupported:
        raise DemoRejected("Unsupported DEMO strategy controls: "+", ".join(unsupported)+"; authorize a supported exact version instead")


def require_limits(limits: RiskLimits) -> None:
    from tradebot.demo import DemoRejected

    if limits.max_correlated_exposure_multiple is not None:
        raise DemoRejected("Unsupported DEMO risk control: max_correlated_exposure_multiple requires verified DEMO correlation groups; entries remain blocked")
