# Strategy development

Strategies are deterministic configurations, not arbitrary uploaded Python code. The supported configuration schema and rule evaluator are in `packages/core/tradebot_core`; [quantitative-model.md](quantitative-model.md) specifies exact formulas and constraints.

Each strategy has independent configuration versions, approval evidence, signals and lifecycle status. An edit creates a new version; historical results keep their version reference. Validate before backtesting, review the evidence before paper deployment, and suspend rather than silently using invalid indicator values.

## Development workflow

1. Define instrument, closed-candle timeframe, indicator parameters, long/short entry conditions, stop/target and risk settings using the supported schema.
2. Create the strategy through the Strategies page or authenticated `/api/v1/strategies` endpoint. The API rejects unknown configuration fields and invalid parameter combinations.
3. Validate. Ensure required indicator warm-up and candle continuity before interpreting signals.
4. Import authorized real market history or an explicitly labeled recorded dataset; run the backtest. Inspect fees, slippage, ambiguous bars, drawdown, losing streaks and trade sample size.
5. Review the pinned strategy version, then approve/start PAPER only. Worker execution observes the same central risk checks as the API.
6. Observe signals, orders, fills, position ownership, audit and heartbeat. Pause or stop without assuming open positions have closed.
7. A changed configuration requires new evidence. There is no automatic demo/live promotion based on returns.

Nested logical conditions and indicator operands must be represented by the typed schema; unsupported subjective terms are rejected rather than interpreted as executable intent. Do not add private exchange credentials or direct order calls to a strategy.

## Isolation and conflict policy

Timeframes and parameters are per strategy version. The central feed may serve multiple strategies, but no hidden mutable indicator state is shared. Current paper account/instrument ownership is exclusive: another strategy cannot open conflicting exposure. Broader account assignment and explicit exchange netting are pending features.

Multi-timeframe dependencies use larger whole multiples of the base timeframe. A `SAME_DIRECTION` dependency requires the latest completed higher-timeframe fast EMA to exceed its slow EMA for a long, or fall below it for a short. The higher bar's opening time does not make it available: its full close must precede or equal the base decision time. Missing, stale, incomplete or unwarmed data blocks entry. Independent prefix tests cover future revisions and exact closure boundaries.

## Research and strategy metadata

Strategy configuration includes description, creator, source, immutable version, trading style, indicator dependencies and allowed environments. Approval and activation belong to the persisted exact version, rather than being inferred from these descriptive fields. A style such as SCALPING or SWING describes the trading horizon; explicit entry kind, timeframe and rules still determine behavior. Book/research candidates must retain their references and ambiguity review before becoming an approved configuration. “Strong momentum” needs an explicit reviewed threshold and must never become arbitrary executable logic.

The custom rule feature set covers all fifteen indicator families, causal previous-window support/resistance, momentum, engulfing candle flags and candle body fraction. Undefined values propagate through three-valued AND/OR/NOT logic. The pivot rule feature explicitly uses the prior completed candle; the indicator module separately supports prior completed UTC sessions.

Regime filters expose the calculation parameters and classifications. External event/funding filters require timestamped observations and fail closed when required data is unavailable or stale. UTC sessions use start-inclusive/end-exclusive hours and explicit weekdays. Exit rules support fixed/ATR/structure stops, fixed price or reward/risk targets, partial targets, completed-bar trailing, modeled cost-inclusive break-even, opposing signals, time limits, base-EMA invalidation and typed indicator exits. See [quantitative-core.md](quantitative-core.md) for schema and execution boundaries.

Research validation retains losing runs and rejected signals, rather than selecting only a favorable past return. No public book, positive backtest or paper result proves profitability or authorizes live execution.
