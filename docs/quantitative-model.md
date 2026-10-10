# Quantitative model and research limits

The Python package `tradebot_core` contains pure financial math, indicators, typed strategy rules, central risk decisions, shared paper fill calculations, and a deterministic single-instrument backtester. It never contacts an exchange or submits an order. Backend workers own durable account state and must obtain a central risk approval before every entry.

All prices, quantities, fees, margin, P&L, and capital calculations use Decimal in an isolated 34-digit, half-even context. Pydantic rejects nonfinite values, unknown fields, invalid OHLC, unsupported economics, and naive timestamps. UTC candle timestamps denote **bar opening**. A bar's signal is available at opening timestamp plus its duration.

## Contract economics and currency

Only explicitly specified linear futures are implemented. The contract multiplier is underlying units per contract, never “one BTC per contract.” Given price `p`, quantity `q`, multiplier `m`, settlement conversion `c`, and direction `s` (`+1` long, `-1` short):

- Settlement notional: `p × q × m × c`.
- Settlement P&L: `s × (exit − entry) × q × m × c`.
- Fee per fill: fill settlement notional × configured fee rate.
- Initial margin: settlement notional × `max(initial_margin_rate, 1 / requested_leverage)`.

Identical quote and settlement currencies use `c = 1`. Different currencies require an explicit conversion rate and timestamp; runtime risk rejects stale conversion data. Inverse and quanto payoff models are rejected. Exchange margin fields are not silently treated as percentages or fractions.

`metadata_verified` defaults to false. Economics must be reviewed before sizing. `verification_scope=EXCHANGE` describes certified exchange economics; `PAPER_MODEL` describes an internally reviewed research model and requires declared assumptions. The risk engine rejects `PAPER_MODEL` for DEMO and LIVE. A paper model's quantity minimum and margin assumptions are not claims about the exchange's actual order restrictions. Both scopes and assumptions survive serialization and enter the reproducibility hash.

All research capital and reported monetary metrics use the instrument's **settlement currency**. The user's ₹50,000 planning example is separate from a USD virtual balance. An INR planning amount requires an explicit currency conversion before it can become settlement capital; no conversion or fixed profit objective is invented.

## Candles and indicators

Candles must be aligned, chronological, unique, contiguous, and consistent in timeframe. Missing intervals fail validation rather than becoming invented prices. An optional final open candle is ignored by indicators and strategy evaluation. Historical backtests require every supplied candle to be closed.

The indicator module includes SMA, EMA, WMA, Wilder RSI, MACD, Bollinger Bands, Wilder ATR, ADX, Stochastic, VWAP, volume SMA, Donchian Channels, Keltner Channels, Supertrend, and pivot points. Arrays align to input candles; insufficient history is `None`. EMA seeds with SMA. Bollinger bands use population standard deviation. ATR includes the first candle's high-low in its seed. MACD line, signal, and histogram have separate warmup. VWAP is cumulative over the provided window; it is not silently reset at sessions. Pivot points use only the previous completed UTC session, with an explicit previous-bar alternative. Supertrend's initial direction is explicitly bearish.

Published Wilder RSI values, independent numerical vectors, warmup boundaries, incomplete candle masking, missing-data rejection, Decimal context isolation, and prefix comparisons are tested. These conventions can differ from libraries with different seeds/session choices; users should match conventions before comparing outputs.

## Strategies and rules

The original entry kinds remain available:

- `EMA_CROSS`: configured fast/slow EMA crossover after both current and previous values are warm.
- `BREAKOUT`: close above/below the high/low of the **previous** configured window.
- `MEAN_REVERSION`: close outside configured Bollinger bands together with configured RSI thresholds.
- `CUSTOM`: a validated AND/OR/NOT tree with literal/feature comparisons and above/below crosses.

The expanded model also implements MOMENTUM, VOLATILITY, SUPPORT_RESISTANCE and TREND_FOLLOWING entries, composable higher-timeframe alignment, transparent regimes, UTC sessions and timestamped external filters. All fifteen indicator families are available to typed custom conditions. Advanced policies and validation conventions are documented in [quantitative-core.md](quantitative-core.md).

Custom trees have a 12-level / 100-node budget and use an allowlisted feature set. They do not execute Python, JavaScript, strings, or arbitrary attributes. Three-valued logic propagates unavailable indicators: NOT of an unavailable value does not become a tradable signal. Conflicting long and short intentions block entry.

Strategy versions carry independent parameters and no shared mutable runtime state. Signal identity derives from configuration, closed candle timestamp, and side. The separate data snapshot hashes the complete input prefix. The persisted backend additionally namespaces deduplication by strategy identity/version to avoid exposure duplication after a restart or historical data revision.

The backtester caches causal indicator arrays per run. Every indexed decision matches an independent prefix-only calculation; later candles cannot change earlier signals. The cache avoids repeatedly calculating the full history for each bar.

## Sizing and central risk

Entry and stop execution estimates include half the spread, adverse slippage, adverse tick rounding, and entry/exit fees. Quantity is rounded **down** to the exchange increment after taking the strictest of monetary risk, equity risk, available margin, notional cap, and maximum quantity. If the result is below the minimum, quantity becomes zero with a rejection reason.

Stop risk estimates are sizing assumptions, not a loss guarantee: gaps and insufficient liquidity can produce worse exits. A desired profit, including the ₹500 example, never increases size.

Central pre-trade risk independently recomputes proposed economics to reject forged risk/margin/notional amounts. It checks reviewed metadata and purpose, stop/target direction, risk/reward, per-trade and committed risk, leverage, margin, gross/net exposure, daily/weekly loss, drawdown, position/order counts, spread/slippage, and market/conversion freshness. Operational checks fail closed for unconfirmed reconciliation, unknown orders, emergency stop, database/clock/worker/exchange health, and protective-order verification. Runtime account locks and pending reservations remain the backend's responsibility; context must include current exposure and reservations.

Example limits are editable settlement-currency policies. They are not universally safe investment settings. Research entry cooldown and historical correlation reports are implemented. Measured order-book liquidity, exchange liquidation distance, continuous exchange cross margin and regime-dependent correlation estimates still require verified feeds/models before promotion where applicable.

## Shared paper execution and order states

`PaperFillModel` is a declared **full-fill** model: adverse bid/ask spread, fixed slippage, tick rounding, and fees. Its financial math is shared by the durable paper backend and historical backtester. It does not claim order-book partial fills, exchange matching, real order acknowledgments, or exchange protection.

Order transitions distinguish creation, risk approval, submission, acknowledgement, partial/full fills, cancellation, rejection, expiry, and UNKNOWN. UNKNOWN cannot be resubmitted; it can resolve only with affirmative reconciliation evidence. Cumulative fills cannot decrease, partial fills must be strictly partial, and FILLED requires complete fill evidence. Durable deduplication, fill events, and reconciliation are implemented outside the pure model.

## Backtest sequence and reproducibility

Each bar processes funding on an existing position at opening, then gap protection, then a previous closed-bar intention at the current opening, then intrabar protection, then closing equity and the next signal. A signal never fills at the close that created it. Stops crossed by an opening gap fill at the worse opening reference. Target gaps conservatively use the target reference. If stop and target are both touched in an OHLC bar, stop wins and the trade records the ambiguity. Existing opposite signals close an owned position without an immediate reversal. A final position closes at the last data close with `END_OF_DATA` recorded.

Funding requires either the explicit zero assumption or supplied chronological events containing a timestamp, rate, mark price, and source. Events align to candle openings. Positive funding means long pays and short receives. A newly opened position does not receive funding retroactively for that same opening. Fees apply to both entry and exit. Cash, fees, funding, gross P&L, and net P&L reconcile exactly.

Results include strategy/version, instrument review scope and assumptions, dataset version and SHA-256, execution configuration, trade journal, rejected signals, equity curve, metrics, and a reproducibility hash covering every input plus engine version. No random behavior or wall-clock run time enters this deterministic hash.

Win rate, expectancy, drawdown, costs, trade direction, and holding metrics are computed from actual results. Profit factor is null without a loss denominator. Sharpe and Sortino require at least 30 adjacent complete daily intervals, selected at UTC midnight; partial days, missing daily observations, zero variance, and insufficient downside samples are not fabricated into ratios. Calmar requires at least 365 daily intervals and a nonzero drawdown. Returns assume a zero risk-free rate and 365 crypto trading days. No trade samples or metrics imply future performance.

## Recorded validation and limits

Run `.venv/bin/python -m pytest -q tests/unit/test_core*.py` and `.venv/bin/ruff check packages/core/tradebot_core tests/unit/test_core*.py` from the repository root. Test fixtures are explicitly offline models; they are not verified Delta products or real trading performance.

A single local measurement of a constant-price offline EMA/no-fill fixture took 0.0201 seconds for 1,000 bars and 0.2126 seconds for 10,000 bars after causal caching. Those measurements describe this cloud run only; they do not measure exchange latency, real strategies with many fills, or production throughput.

The optional advanced loop adds declared limit/partial-fill assumptions, completed-bar protection changes, multi-timeframe alignment, supplied event/funding filters and explicit isolated liquidation. Research orchestration adds OOS/walk-forward/stability/stress/Monte Carlo/benchmarks/regime attribution and fixed portfolio sleeves. Limits remain fixed conversion/economics snapshots, approximate intrabar execution, candle-close drawdown, no measured order-book path, arbitrary custom code, exchange cross margin or centrally netted portfolio matching. A paper result does not approve demo or live deployment.
