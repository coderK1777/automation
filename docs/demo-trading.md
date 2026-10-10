# Delta India demo integration

The application includes an executable, isolated Delta India DEMO backend and dashboard. Private requests route only to `https://cdn-ind.testnet.deltaex.org`; the authenticated private stream uses `wss://socket-ind.testnet.deltaex.org`. PAPER balances and fills remain separate. Production private order submission is structurally unavailable.

No demo credentials are configured in the current environment. Actual private authentication, orders, protection and credentialed recovery remain **unverified**. Passing controlled offline tests does not establish exchange acceptance or production readiness.

The authoritative endpoint/capability verification record is [delta-api.md](delta-api.md). Initial India demo references are `https://cdn-ind.testnet.deltaex.org`, `wss://socket-ind-pub.testnet.deltaex.org` and `wss://socket-ind.testnet.deltaex.org`; each needs current official confirmation and real connectivity evidence.

## Configure and connect

- Obtain demo-only credentials with read/trade permissions and verify account eligibility. Supply raw `DELTA_DEMO_API_KEY` and `DELTA_DEMO_API_SECRET` through secure environment settings, or store `delta-demo` using the encrypted vault CLI in [security.md](security.md). Browser/API responses never expose values. Managed proxy placeholders cannot compute local HMAC signatures.
- Keep `TRADING_MODE=PAPER` and `LIVE_EXECUTION_ENABLED=false`. The distinct DEMO engine has its own ledger and gates; it does not repurpose the PAPER account.
- Enroll operator MFA and perform password plus TOTP/recovery reauthentication. Connecting, approving contract economics, enabling entries, editing risk, creating entries and starting strategies require recent authenticated MFA.
- Connect on the DEMO page. This performs signed read-only balances, positions, active orders and fills requests, discovers demo products, and binds account identity. Public product refresh works before credentials are configured.
- Approve each actual demo contract's minimum, integer increment, maximum quantity covered by the reviewed margin tier, initial-margin fraction and official evidence. Multiplier, tick and currencies come from observed public HTTPS metadata. Missing certification blocks execution. PAPER assumptions never authorize DEMO. Refresh products hourly and review minimum/margin evidence daily. Economics changes invalidate approval.
- Explicitly set `DEMO_EXECUTION_AUTHORIZED=true` in the secure local environment, then run `.venv/bin/python -m tradebot.demo_worker`. The process performs no private requests before explicit account connection and never enables entries automatically.
- Wait for a fresh reconciled account and authenticated private WebSocket, then enable entries with the confirmation control. Start a validated, backtested exact strategy version whose `allowed_environments` explicitly includes `DEMO`, or review a manual intent with both stop and target. DEMO start does not change its PAPER runner.

The local authorization flag defaults to false. Disabling entries retains exchange protection and permits authorized managed reduce-only exits. Withdrawing the local write flag also prevents cancellation and closing requests, so exchange-side protection must remain active when stopping the process.

## Durable execution and recovery

The API persists intents; only the lease-owning DEMO worker submits/cancels. PostgreSQL locks and a renewable account lease serialize signals, manual intents, worker writes and account controls. Deterministic client IDs bind account identity to the idempotency key. Reusing a key with different economics fails. Exact-version DEMO approval is persisted separately from PAPER approval and certification. The final submit gate rechecks that the strategy has not been paused, edited or lost approval, remains allowed in DEMO, and has durable fresh causal signal/sizing evidence. Unapproved queued signals acquire economic reservations sequentially, before their single POST; subsequent approvals include every committed reservation.

The shared central risk engine checks gross/net exposure, existing stop risk, pending reservations, cost-inclusive trade risk, margin, leverage, period losses/drawdown, metadata age, actual quote spread, exchange timestamp, account reconciliation, stream health and worker ownership. Entries carry documented attached stop/target brackets. Acknowledgments must echo identity and protective prices. The exchange fill ledger records actual fees; pre-trade estimates conservatively reserve 0.1% per side and adverse slippage.

The intent and reservation commit before the single POST. Timeout, malformed/missing acknowledgment, or an interrupted submission becomes `UNKNOWN`. Restart converts persisted `SUBMITTING` to `UNKNOWN`. Recovery queries the original client ID, fills and positions. A 404 never authorizes resubmission. Unknown orders retain reservations and block entries.

REST reads follow opaque cursors and reject missing, overlapping or cycling pages. Immutable exchange fill IDs deduplicate partial fills. Partial orders retain reservations until authoritative terminal status. The documented `/positions/margined` lag cannot hide recently filled exposure: fill/position disagreement blocks entries until snapshots converge. External active orders, unmanaged positions/fills, changed fill identities and regressed cumulative fills remain visible issues.

Private stream reconnects reauthenticate and require REST recovery. Duplicates and sequence gaps never create fills or orders from guessed state. Missing protection blocks entries and queues a managed reduce-only close. Unidentified external positions are never closed. Residual managed protection on a flat product is canceled before another entry.

Emergency block, strategy pause and portfolio loss/drawdown limits cancel queued/resting entry quantities without releasing unresolved risk. Protective reduce orders remain active. A managed close first requests cancellation of any known remaining entry quantity. An UNKNOWN entry remains an explicit reconciliation issue and can require operator intervention even after observed exposure was reduced.

The signed `/v2/wallet/transactions` ledger records native amounts, reported running balances and transaction types, including funding and commission. Currency binds to the wallet's numeric asset ID; the official transaction `asset_symbol` documentation has inconsistent types, so no conversion is guessed. A durable initial observation anchors future cash conservation. Missing cashflows, changed IDs, running-balance gaps and lagging wallet snapshots block entries until authoritative evidence agrees. Unsupported position currencies produce an incomplete-valuation issue rather than being added to USD equity. No funding-rate formula is inferred from ledger payments.

Generated brackets acquire durable ownership when observed in the active-order snapshot. An exit that occurs before that snapshot remains an unowned fill and blocks reconciliation for review. This conservative case requires credentialed lifecycle evidence and official parent linkage verification before unattended DEMO can be certified. Trailing-stop wire parameters are implemented and tested offline; automatic replacement remains disabled pending lifecycle verification.

The `/api/v1/demo` router provides status, connection, products/approval, risk, enable/disable, reconciliation, orders/cancel, positions/close, fills, events, cash movements, reports/export and exact-version strategy start/pause. `demo_*` tables isolate products, candles, equity, orders, events, fills, cash movements, protective ownership, reports and runners.

## Supported strategy execution

DEMO strategy entries use actual closed demo candles, shared causal strategy/rule evaluation, supported higher-timeframe dependencies, regime/volatility/session filters, declared stop/target methods and attached fixed stop/target brackets. Risk-fraction, fixed-contract, fixed-notional and volatility sizing all obey independent cost-inclusive stop-risk, margin and certified maximum-quantity caps. Volatility sizing requires a warm ATR from observed closed demo bars. Open bars are excluded and closed-bar revisions block evaluation. Targets round conservatively to the certified tick. Sizing evidence records the source snapshot and requested/capped quantity.

An opposite closed-bar signal can queue a managed reduce-only exit when `exit_rules.opposite_signal_exit` is enabled. This continues for owned strategy positions while their entry runner is paused or entries are disabled. Existing exchange brackets remain installed until authoritative flat reconciliation. The runner does not adopt external positions or place a new entry into an occupied product.

The following saved policies are explicitly rejected at start, generation and final entry submission because their DEMO lifecycle or isolated attribution is unavailable: `execution` advanced entry/expiry/volume/liquidation settings; custom `long_exit_rules`/`short_exit_rules`; `cooldown_bars`; per-strategy trade/loss/allocation/consecutive-loss/drawdown/open-position/liquidation-distance caps; staged targets; trailing, break-even, holding-duration, trend-invalidation and dynamic-target exits; external news-event and funding-rate filters. The error names each unsupported field. Manual MARKET/LIMIT intents remain supported with reviewed prices and mandatory brackets. Configuring `max_correlated_exposure_multiple` also blocks DEMO enablement/submission until verified isolated DEMO correlation groups exist. No PAPER ledger or guessed maintenance tier supplies these contexts.

## Native-currency reports

`GET /api/v1/demo/reports` lists immutable observed reports. Authenticated operators can persist daily, weekly or monthly snapshots using `POST /api/v1/demo/reports` with `period` and an ISO `date`; details and JSON/CSV exports are available at `/{id}` and `/{id}/export?format=json|csv`. The worker schedules the preceding completed day/week/month after successful reconciliation. Calendar boundaries use Asia/Calcutta. Repeating identical source evidence reuses the same report; changed evidence produces a separate revision with a source hash.

Reports reconstruct only managed fill episodes using certified exchange linear economics and actual native fees. Funding payments use the signed native wallet ledger and remain separately identified; the engine does not guess funding into strategy episodes or invent FX rates. Equity changes explicitly include capital transfers and wallet activity, and sampled drawdown is not flow-adjusted. Missing history leaves boundary metrics null or carries coverage warnings. Sharpe, Sortino and annualized return remain null without verified coverage. Historical data begins at actual observation, never a seeded account or PAPER snapshot. Every report labels mode, account, currency, observation boundary and incomplete certification.

## Acceptance evidence

Record actual authentication, balance/positions retrieval, minimum-valid order, acknowledgment, lookup, cancel, fills, protection and reconciliation results. Every test reports one of real demo, offline mocked, blocked, skipped or unrun. A mocked timeout/duplicate-fill test is useful fault evidence but does not count as real demo exchange verification.

Run the controlled offline suites and application authorization tests:

```bash
uv run pytest -q tests/contract/test_demo_coordinator.py tests/contract/test_demo_wire_extensions.py tests/contract/test_delta_adapter.py tests/contract/test_demo_reports.py
uv run pytest -q tests/integration/test_demo_api_gates.py
```

They cover wire signing/pagination, bracket/trailing parameter validation, duplicate intents/events/fills, partial fills, cancellation, uncertain POST, original-ID lookup, restart/private reconnect, insufficient margin, stale stream, external exposure, repeated strategy trades, snapshot delay, contract/credential changes, exact approval/pause/environment/capability gates, causal signal freshness, higher timeframes, all sizing modes, emergency/loss block, protected closing, currency/cash conservation, immutable report revisions, MFA/CSRF and negative production routing. The contract suites are marked `mocked`; none of these checks submit real requests. A separate PostgreSQL test exercises two simultaneous DEMO lease claimants against a disposable schema, using offline credential fixtures.

The acceptance runner is explicit and never called by setup, startup or CI:

```bash
python -m tradebot.demo_acceptance --read-only
python -m tradebot.demo_acceptance --order-flow --confirm-demo-orders
```

Read-only mode checks configured demo authentication, balances, positions, active orders, fills, cash ledger and private WebSocket reauthentication. Missing credentials produce a `blocked` report. Order-flow mode additionally requires local write authorization, a previously connected/enabled healthy DEMO account, certified contract economics and no existing positions, active intents or running DEMO strategies. It prompts locally for operator password and fresh MFA, then queues a minimum-size market trade through the application risk coordinator, verifies actual protection, closes managed exposure, and exercises a limit-order/cancel race. It cannot enable entries or bypass risk. Cleanup owns only its own intents, leaves exchange protection intact, and blocks entries afterward. A failure may leave UNKNOWN intent/exposure requiring operator review; no blind retry is performed. The runner never certifies production readiness or forces stop/target triggers to manufacture evidence.

Unknown submit outcomes require lookup by the original client order ID plus fill/position reconciliation. Recovery cannot blindly retry. Fault tests should use controlled mocks when real demo failures cannot be safely reproduced.

Keep production order submission excluded from unattended CI and onboarding. Successful demo testing does not authorize real money and does not predict returns.
