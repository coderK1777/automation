# Internal paper trading

PAPER is the only initial execution environment. It simulates execution in the application database and never sends an exchange order to production or demo. An authentic public price feed does not make a simulated fill an exchange fill.

## Account and strategy setup

Configure virtual capital and settlement currency, then review risk limits. INR planning capital is a separate field from USD/USDT settlement balance; supply an explicit conversion rate for INR reporting. Defaults are editable research examples, not a safety recommendation.

Import valid candles, create and validate a strategy version, run a historical backtest, then use the backend approval/start action for PAPER. Empty databases intentionally show empty states; setup does not seed fabricated market prices, account profits or completed trades.

The worker evaluates persisted market data independently of the browser. A closed browser does not pause it. If fresh public data cannot be obtained, new exposure must be blocked rather than replacing it with generated prices. Historical candles can support research, but stale historic prices cannot justify a new real-time entry.

## Accounting and protection

Orders, order events, simulated fills, positions, fees, realized P&L and equity snapshots are durable. Fills use declared slippage/fee assumptions. Stop/target checks are local simulator behavior and depend on valid candles and worker operation. They are not confirmed exchange-native protection.

The initial model uses full simulated fills. Partial fills, order-book liquidity, exchange liquidation and historically sourced funding are pending expansions. See [quantitative-model.md](quantitative-model.md) and [known-limitations.md](known-limitations.md) before interpreting a result.

## Operator controls

Pausing/stopping a strategy prevents its new entries but must not imply its open position is closed. Emergency `BLOCK_ENTRIES` prevents additional entry exposure. `FLATTEN_MANAGED` is a separate explicit paper-account action and requires confirmation; it acts on the managed positions only, with current model prices and costs. Resume only after inspecting the original incident and current state.

## Recovery

The account lock and executor lease coordinate paper state. Deterministic signal identity and unique order/fill identity prevent duplicate exposure when a candle is reprocessed. Restart must preserve existing orders and positions; it must never reset the database as a startup step.

Inspect worker heartbeat, account reconciliation flag, open positions, pending orders and risk restrictions before resuming. Core tests, PostgreSQL integration tests and an actual stop/restart smoke path are separate evidence categories. SQLite tests do not demonstrate multiple-worker Postgres locking.

No exchange trading credentials are required for this mode. Do not add them merely to operate the paper account.
