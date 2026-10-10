# Advanced quantitative core

`tradebot_core` is deterministic and never contacts an exchange. Its immutable configurations, Decimal economics and causal caches are shared with backend workers. All additional fields are optional; the original full-fill behavior remains available for simple strategies.

## Strategy policies

Entry kinds include EMA_CROSS, BREAKOUT, MEAN_REVERSION, MOMENTUM, VOLATILITY, SUPPORT_RESISTANCE, TREND_FOLLOWING and CUSTOM. Style metadata includes scalping, intraday, swing and the requested composable styles. Style labels do not replace explicit entry conditions. Allowed environments default to BACKTEST and PAPER; naming DEMO or LIVE in a configuration does not bypass backend promotion or credentials.

The custom operand allowlist includes price/volume, EMA/SMA/WMA, RSI, MACD line/signal/histogram, Bollinger bounds, ATR, ADX and DI, stochastic K/D, VWAP, volume average, Donchian/Keltner bounds, Supertrend/direction and prior-bar pivot levels. Previous-window support/resistance, price momentum, engulfing flags and body fraction are available. None of these features executes arbitrary code.

Higher-timeframe configuration example:

```json
{
  "higher_timeframes": [
    {"timeframe_seconds": 300, "fast_period": 9, "slow_period": 21, "alignment": "SAME_DIRECTION"}
  ],
  "allowed_regimes": ["TREND_UP", "BREAKOUT"],
  "allowed_volatility": ["NORMAL"],
  "session": {"start_hour_utc": 0, "end_hour_utc": 0, "weekdays": [0, 1, 2, 3, 4, 5, 6]}
}
```

A higher bar is known only at its completed close. Larger dependencies must be whole multiples of the base timeframe; missing/open/stale/unwarmed data blocks entries. Regime classification reports primary trend/range/breakout/illiquid state and separate LOW/NORMAL/HIGH volatility. Trend is `(fast EMA - slow EMA) / close`; volatility is Wilder ATR/close. Breakout compares the current close with prior-window highs/lows. Configured low average volume takes precedence as ILLIQUID. Thresholds, periods and values are visible and tested with prefix-only evaluations.

`filters` can require a fresh economic calendar and cap absolute funding rates. `EvaluationContext` observations carry source, publication and observation timestamps. A known upcoming high-impact event blocks within configured before/after windows. An event not yet published cannot affect an earlier decision. Missing required observations fail closed.

Strategy risk policies include optional `max_trades_per_day`, `max_daily_loss_fraction` and `capital_allocation_fraction`, in addition to portfolio limits. Trade counts reset at UTC day boundaries and count actual new entries, rather than each partial tranche. Loss limits include current marked equity. Capital allocation limits the strategy's usable margin; they never increase central exposure allowances.

Optional `max_consecutive_losses` and `max_drawdown_fraction` block new research/PAPER entries from already recorded losses and equity. `max_open_positions` remains bounded by the executor's explicit ownership policy. `min_liquidation_distance_fraction` requires an explicit maintenance-margin model and checks the proposed entry against that model; it never invents an exchange liquidation estimate.

`sizing` is a typed policy with modes RISK_FRACTION, FIXED_CONTRACTS, FIXED_NOTIONAL and VOLATILITY. Fixed-contract mode requires `fixed_quantity`; fixed-notional mode requires `fixed_notional` in settlement currency. Both fields are null in other modes. Volatility mode requires a positive completed-bar ATR using `volatility_period` (default 14) and `volatility_multiplier` (default 2). Its requested quantity is the risk budget divided by ATR-based settlement cost plus modeled per-contract fees. Every mode then takes the stricter cost-inclusive protective-stop risk, margin, exposure and contract increment/minimum/maximum limits. A large fixed request or small ATR cannot increase allowed risk. Results record the requested quantity and whether limits/rounding capped it.

`RiskLimits.max_correlated_exposure_multiple` optionally limits candidate notional plus `RiskContext.correlated_exposure` against current equity. The context must include existing and pending gross notional from explicitly declared operator correlation groups. This is a conservative shared exposure cap, not a claim that an inferred historical correlation remains stable; neither direction cancels that gross group exposure.

## Protection and execution

`stop_method` supports FIXED_PCT, ATR, STRUCTURE and FIXED_PRICE. ATR needs warm values; structure uses the prior window. Invalid direction or nonpositive stops block signals. `reward_risk_target` defines a target multiple of initial stop distance, and `target_reference_price` defines an explicit target price. Typed `long_exit_rules` and `short_exit_rules` evaluate completed bars and execute at the next opening.

`exit_rules` supports fractional staged targets whose fractions sum exactly to one; FIXED/PERCENT/ATR/STRUCTURE trailing; activation in initial-risk multiples; break-even triggers; maximum holding bars; opposite signal exits; base fast/slow EMA invalidation; and a next-bar ATR target. New protection uses completed data and never applies retroactively. Stops are monotonic. Break-even includes paid entry costs, supplied funding and modeled closing costs by default; a gap can still lose money. It is an internal calculation, not a guaranteed exchange fill.

`execution` supports MARKET or LIMIT entries, explicit limit offset, expiry and optional declared volume participation with CONTRACTS or UNDERLYING units. Without volume participation market orders use the existing full adverse-fill model. Limit orders require penetration beyond costs; a mere touch does not claim queue priority. Limit or whole-bar volume-capped fills receive bar-close timestamps, without using the observed bar volume to promise an earlier fill. Protection starts at the fill close; previous intrabar extremes are not replayed. Partial exits cancel remaining entry exposure. No measured order-book liquidity is invented.

Liquidation is disabled unless an explicit maintenance-margin rate is supplied. The declared isolated linear model solves:

`isolated margin - funding paid + signed mark P&L = mark notional × (maintenance rate + liquidation fee rate)`.

The model rejects maintenance at/above initial margin and fees that consume notional. It is not Delta cross-margin logic. Gap fills use the observed worse opening. Stops take priority over profitable targets when the intrabar path is unknown. A closer liquidation boundary takes priority over a more distant stop. Actual metadata and FX provenance remain required.

The advanced engine retains chronological supplied funding events anywhere inside the observed candle time range. Opening events apply only to already-existing exposure. For an intrabar event, the exact protective-exit ordering is unknowable from OHLC: payable charges are applied conservatively before protection, while credits apply only to the quantity surviving the bar. An end-bar entry never receives retroactive funding. This convention is disclosed in results. The baseline full-fill engine retains its explicit opening-aligned funding contract.

## Research APIs

`BacktestEngine.run` accepts `higher_timeframes={seconds: candles}` and `evaluation_contexts={UTC decision close: EvaluationContext}`. `BacktestConfig.execution_model` can override a strategy's execution scenario for research. Results include order events, entry/exit fill counts, monthly returns, direction P&L and deployed/exposure descriptive ratios. Input identity includes datasets, instrument economics, strategy/configuration, external observations and engine version.

`tradebot_core.research` exports `out_of_sample`, `walk_forward`, `parameter_stability`, `cost_stress`, `monte_carlo`, `benchmark_comparison`, `regime_attribution`, `portfolio_correlations` and `simulate_portfolio`. Reports preserve actual component results and reproducibility hashes. Parameter grids cap at 50 configurations. Walk-forward caps at 500 candidate runs, uses nonoverlapping test windows and never selects on future test metrics. Monte Carlo caps at one million sampled trade events, records its seed and states the independent fixed-quantity sampling assumption. Undefined correlation or statistical denominators are null.

Portfolio simulation uses fixed capital sleeves and exact synchronized observations in one settlement currency. Capital, cash, costs, equity, margin and exposure are added from actual runs. It rejects missing timelines and implicit FX. Exposure thresholds diagnose a path breach without rewriting trades. Exchange netting, dynamic portfolio allocation, shared risk allocation, cross margin and order-book cross-impact require distinct models and are not inferred from this report.

Run `.venv/bin/python -m pytest -q tests/unit/test_core*.py` and `.venv/bin/ruff check packages/core/tradebot_core tests/unit/test_core*.py`. Fixtures are explicitly synthetic offline evidence; they do not establish exchange performance or live readiness.
