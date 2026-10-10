# TRADEBOT PRO — COMPLETE PRODUCTION-READY AUTOMATED CRYPTO FUTURES TRADING SYSTEM

## MASTER DEVELOPMENT INSTRUCTIONS FOR AI CODING AGENT

**Project name:** TradeBot Pro

**Project type:** Professional, autonomous cryptocurrency futures trading system with web-based administration.

**Primary and mandatory exchange:** Delta Exchange India.

**Development approach:** Architecture-first, backend-first for trading correctness, with iterative full-stack implementation.

**Deployment target:** Self-hosted Linux VPS or reliable cloud infrastructure capable of running continuous trading workers.

**Trading progression:** Historical Backtesting → Internal Paper Trading → Delta Exchange India Demo/Testnet → Explicitly Authorized Limited Live Trading.

**Initial trading mode:** PAPER.

**Live execution default:** DISABLED.

**Primary objective:** Build a functioning system capable of safely orchestrating independently configured trading strategies, evaluating performance, executing approved orders, maintaining consistent account state, and monitoring itself continuously.

---

# SECTION 1 — YOUR ROLE AND RESPONSIBILITIES

You are my principal AI software engineering agent.

Operate as a complete professional engineering team with the combined expertise of:

1. Principal System Architect
2. Quantitative Trading Researcher
3. Algorithmic Trading Engineer
4. Python Backend Developer
5. Exchange API Integration Engineer
6. Next.js and TypeScript Frontend Developer
7. Database Architect
8. Cybersecurity Engineer
9. Risk Management Engineer
10. DevOps and Site Reliability Engineer
11. Quality Assurance Engineer
12. Automated Testing Engineer
13. Financial Data Engineer
14. Technical Documentation Engineer

You must make practical engineering decisions, write real code, execute tests, validate integrations, identify failures, fix defects, and maintain clear documentation.

I do not want a static demonstration, dummy trading simulator pretending to be connected to an exchange, frontend-only clone, or unfinished collection of placeholder functions.

I want a complete, independently operable trading automation platform.

However, do not falsely claim production readiness. Production readiness requires actual testing evidence, reconciliation testing, operational reliability, and a separate security review.

Implement the real system in phases and accurately distinguish working functionality from incomplete functionality.

You must never independently activate real-money trading without my explicit authorization.

---

# SECTION 2 — CRITICAL EXCHANGE REQUIREMENT

## DELTA EXCHANGE INDIA ONLY FOR INITIAL RELEASE

The initial application must be built specifically for the Indian Delta Exchange environment.

**Do not silently substitute:**
- Delta Exchange Global
- Binance Futures
- Bybit
- OKX
- Another exchange
- A mock trading API for a supposedly real integration

A modular architecture may allow additional exchanges in the future, but the first functioning exchange integration must be Delta Exchange India.

### Official references

Primary API documentation:

https://docs.delta.exchange/

India production REST API:

https://api.india.delta.exchange

India demo/testnet REST API:

https://cdn-ind.testnet.deltaex.org

India production public WebSocket:

wss://public-socket.india.delta.exchange

India production private WebSocket:

wss://socket.india.delta.exchange

India demo public WebSocket:

wss://socket-ind-pub.testnet.deltaex.org

India demo private WebSocket:

wss://socket-ind.testnet.deltaex.org

These are initial reference values. Verify each against current official documentation during implementation.

Keep all environment endpoints in validated configuration. Never scatter endpoint strings throughout the application.

### Mandatory rules

1. Production API credentials must only work with the production environment.
2. Demo credentials must only work with the demo environment.
3. Internal paper trading must not require order-placement permissions.
4. The frontend must never contain exchange API secrets.
5. The backend must never confuse Delta India with Delta Global.
6. Never send production trading orders as part of ordinary setup or automated CI testing.
7. All trading permissions must be explicitly configured.
8. Validate the exchange's current API terms, availability, and account eligibility.
9. Treat official API specifications as authoritative over assumptions from general cryptocurrency exchange libraries.
10. If SDK functionality differs from Delta India behavior, implement and test a dedicated adapter.

---

# SECTION 3 — BUSINESS AND TRADING OBJECTIVES

I want this platform to support multiple forms of automated cryptocurrency futures trading.

Supported trading approaches should include:

- Scalping
- Intraday trading
- Momentum trading
- Trend following
- Breakout trading
- Mean reversion
- Swing trading
- Support/resistance trading
- Volatility-based trading
- Multi-timeframe trading
- Event-driven trading
- News-filtered strategies

I have advanced trading books and strategy research.

I want to import, study, configure, mathematically define, test, compare, and automate these strategies independently.

The system must allow one strategy to use 1-minute BTC futures candles while another strategy uses 15-minute ETH futures candles, for example.

Strategies must not share hidden mutable state.

Each strategy must have separate:
- Parameters
- Versions
- Signals
- Logs
- Statistics
- Risk limits
- Backtest history
- Runtime state
- Deployment approvals

### Capital example

My initial example capital is ₹50,000.

This must be a configurable planning input, not a fixed account balance.

I have considered a profit objective around ₹500 per trade, but the system must treat that as a possible research objective, not an expected result, mandatory position target, or guarantee.

Profit targets must not override risk controls.

Never increase position size simply to force a desired profit.

Build risk-aware position sizing from verified Delta contract economics.

---

# SECTION 4 — TECHNOLOGY STACK

Use professional, maintained technologies.

## Frontend

- Next.js App Router
- React
- TypeScript strict mode
- Tailwind CSS
- shadcn/ui
- Lucide icons
- TanStack Query
- Zustand where needed
- React Hook Form
- Zod
- TradingView Lightweight Charts or an appropriately licensed equivalent
- Recharts for analytics
- WebSocket client
- Responsive design

## Backend

- Python
- FastAPI
- Pydantic
- SQLAlchemy 2.x
- Alembic
- PostgreSQL
- Redis
- asyncio where appropriate
- Dedicated long-running worker processes
- pandas
- NumPy
- A tested technical indicator implementation/library
- HTTPX or another robust asynchronous HTTP client
- A maintained WebSocket client
- Decimal arithmetic for monetary and contract calculations

## Quantitative Research

Use modular quantitative computation libraries for:

- Technical indicators
- Historical data processing
- Backtesting
- Performance measurement
- Statistical analysis
- Walk-forward evaluation
- Strategy comparison
- Portfolio simulation

## Deployment

- Docker
- Docker Compose
- Linux VPS
- Caddy/Nginx
- HTTPS
- GitHub Actions
- PostgreSQL backups
- Prometheus-compatible telemetry
- Grafana
- Centralized structured logs
- Error reporting

Use the current supported stable versions at implementation time and pin dependencies.

Avoid unnecessary infrastructure complexity, but do not compromise trading correctness or recovery.

---

# SECTION 5 — COMPLETE ARCHITECTURE

Build independently testable components with documented interfaces.

## 5.1 Frontend Web Application

The frontend must be a management and monitoring interface.

It must not contain the actual autonomous execution logic.

Closing the browser must not stop approved trading processes.

## 5.2 Backend API

Responsible for:

- Authentication
- Authorization
- Strategy management
- Configuration
- Operator commands
- Reporting
- Risk settings
- API integration settings
- Historical data access
- Audit trails

## 5.3 Market Data Service

Responsible for:

- Delta public WebSocket subscriptions
- Historical candle downloads
- Real-time prices
- Order book updates
- Trades
- Mark prices
- Funding
- Data normalization
- Time synchronization
- Missing-data detection

## 5.4 Strategy Engine

Responsible for:

- Strategy loading
- Indicator calculations
- Mathematical condition evaluation
- Signal generation
- Strategy state
- Indicator warm-up
- Trading session filters
- Strategy lifecycle
- Error handling

## 5.5 Risk Engine

Responsible for independent pre-trade and continuous portfolio risk checks.

No strategy may place exchange orders directly.

## 5.6 Order Management System

Responsible for:

- Internal order creation
- Risk-approved execution
- Exchange acknowledgments
- Order status updates
- Partial fills
- Stop orders
- Take-profit orders
- Cancellations
- Unknown-order recovery
- Reconciliation

## 5.7 Delta Exchange Adapter

Responsible for translating normalized trading instructions into valid Delta India API requests.

## 5.8 Backtesting Engine

Responsible for historical strategy simulation and performance evaluation.

## 5.9 Paper Trading Engine

Responsible for simulated trading without exchange execution.

## 5.10 Demo Trading Engine

Responsible for executing orders through verified Delta India demo/testnet APIs.

## 5.11 Portfolio Accounting Engine

Responsible for:

- Equity
- Cash
- Margin
- Exposure
- Position ownership
- Fees
- Funding
- Realized P&L
- Unrealized P&L
- Currency conversions

## 5.12 Notification Service

Responsible for Telegram and in-app notifications.

## 5.13 Monitoring and Recovery Service

Responsible for:

- Service health
- Worker heartbeats
- Exchange health
- State reconciliation
- Error detection
- Incident records
- Recovery workflows

---

# SECTION 6 — DELTA EXCHANGE INDIA API INTEGRATION

This section is mandatory and must be implemented carefully.

## 6.1 API Authentication

Use Delta India's documented HMAC-SHA256 signing process.

For REST requests, create the signature using the documented concatenation of:

HTTP method + timestamp + request path + query string + request body.

Required headers include:

- api-key
- signature
- timestamp
- User-Agent

Apply the actual documentation's required encoding and serialization rules.

Do not generate signatures using an incorrectly serialized JSON body.

Account for the short accepted signature lifetime.

Synchronize the server clock using a reliable time source.

Monitor clock drift.

Generate signatures near the time of request submission.

Handle:

- Invalid API key
- Invalid signature
- Expired timestamp
- IP not whitelisted
- Permission denied
- Invalid request
- Insufficient margin
- Rate limit
- Exchange unavailable

Keep API secrets outside the source code and application responses.

## 6.2 WebSocket Authentication

Use Delta India's currently supported private WebSocket authentication method.

Implement the documented key-auth method.

Do not rely on obsolete authentication approaches.

Handle authentication responses explicitly.

Implement authenticated subscriptions to supported private account channels.

## 6.3 REST Features

Implement and validate the relevant documented capabilities for:

- Products
- Instruments
- Tickers
- Historical candles
- Orders
- Order lookup
- Order cancellation
- Order modification
- Positions
- Balances
- Account/trading state
- User fills
- Margin/leverage where supported
- Funding data
- Transaction records

Do not guess undocumented endpoints.

## 6.4 WebSocket Features

Implement supported channels for:

- Candlesticks
- Mark prices
- Order book
- Recent trades
- Funding rates
- Order updates
- Position updates
- User trades/fills
- Account state changes where available
- Exchange system status

Determine which channels belong on public versus private WebSocket endpoints.

Do not use deprecated public data channels on private sockets merely because old examples show them.

## 6.5 Connection Management

Implement:

- Heartbeats
- Ping/pong as appropriate
- Auto-reconnection
- Exponential backoff with jitter
- Subscription recovery
- Message validation
- Duplicate-event handling
- Stale-data detection
- Stream sequence handling where supported
- Snapshot recovery
- Missing-data recovery

Respect Delta's documented connection and request limits.

Do not create a new WebSocket for every active strategy.

Share validated market feeds across strategies through a central market-data service.

## 6.6 Delta Contract Specification

Fetch live product metadata.

For every selected futures contract, identify:

- Product ID
- Product symbol
- Contract type
- Underlying asset
- Quote asset
- Settlement asset
- Contract multiplier
- Tick size
- Contract quantity specification
- Minimum order quantity
- Maximum order quantity if specified
- Permitted quantity increments
- Margin requirements
- Supported leverage
- Trading status
- Price bands
- Funding rules
- Supported order types
- Trigger-price behavior

Never assume a Delta contract represents one BTC.

Never confuse lot size, contract quantity, margin amount, and notional exposure.

Implement exact conversions between:

- Desired economic notional
- Contract quantity
- Margin requirement
- Fee amount
- P&L in settlement currency
- Reference-currency account reporting

Quantities and prices must conform to valid exchange increments.

## 6.7 Instrument Selection

Initial preferred instruments:

- BTCUSD perpetual futures
- ETHUSD perpetual futures

Discover live products through the API.

If an instrument is unavailable, disabled, or unsupported in the chosen environment, show its actual state and block execution.

Do not hardcode product IDs without verifying them.

## 6.8 Order Types

Support documented Delta India order functionality, including where available:

- Market orders
- Limit orders
- Stop-market orders
- Stop-limit orders
- Take-profit orders
- Trailing stops
- Reduce-only orders
- Post-only orders
- Time-in-force options
- Supported bracket/attached protection functionality

Do not assume every order feature works identically across instruments or environments.

Test trigger behavior, protective-order handling, and position-reduction semantics.

## 6.9 Client Order IDs

Generate unique client order IDs consistent with Delta's current rules.

Respect the documented maximum length of 32 characters.

Create a stable relationship between:

- Signal ID
- Internal order ID
- Client order ID
- Exchange order ID
- Fill records

Use the documented lookup-by-client-order-ID functionality for recovery.

Implement duplicate prevention and reconciliation, not merely random ID generation.

## 6.10 Unknown Order Status

A network timeout does not prove an order failed.

If an order submission times out:

1. Mark the instruction UNKNOWN.
2. Block unsafe duplicate submission.
3. Query supported exchange order lookup endpoints.
4. Check relevant fills and position changes.
5. Reconcile current account exposure.
6. Resolve the order state where evidence is sufficient.
7. Escalate unresolved uncertainty.
8. Resume only after risk-safe reconciliation.

Never blindly retry a possibly accepted trading order.

## 6.11 Rate Limits

Read current limits from the official documentation.

Implement:

- Client-side throttling
- Rate-limit budgets
- Rate-limit-aware queues
- Retry policies
- Backoff
- Circuit breakers
- Telemetry for rejected requests

Priority should be given to protective actions and reconciliation under a documented safety policy.

## 6.12 Delta Integration Acceptance Tests

Verify:

- Public API connectivity
- Product discovery
- Historical market-data retrieval
- Public WebSocket subscriptions
- Demo account authentication
- Demo balance retrieval
- Valid demo contract sizing
- Demo order submission
- Order acknowledgment
- Partial-fill handling when testable
- Order lookup
- Order cancellation
- Position retrieval
- Private WebSocket updates
- Reconciliation
- Recovery after disconnect

These tests must run in the demo environment or through controlled offline mocks where the test explicitly declares that it is mocked.

Do not use actual production order submission in unattended automated tests.

---

# SECTION 7 — TRADING DASHBOARD UI DESIGN

Build a professional, dark-mode, desktop-first trading dashboard matching the following design concept.

## Design System

Background: dark navy/near-black.

Sidebar: slightly darker than content panels.

Cards: subtly elevated dark panels with soft borders.

Accent colors:

- Blue: navigation and primary controls
- Green: profitable values and running status
- Red: losses, rejected actions, dangerous operations
- Amber: warnings
- Purple: analytics and indicators
- Gray: secondary information

Use consistent:

- Typography
- Spacing
- Borders
- Icons
- Hover states
- Loading states
- Skeletons
- Modals
- Confirmation flows
- Toast messages
- Empty states
- Error boundaries

The UI should remain usable with thousands of historical records and dozens of strategies.

Never render fake account profits as actual account information.

All simulated data must be visibly marked.

---

# SECTION 8 — COMPLETE APPLICATION PAGES

Implement the following pages.

## 8.1 Command Center / Dashboard

Cards:

- Account balance
- Account equity
- Available margin
- Margin used
- Today's P&L
- Weekly P&L
- Monthly P&L
- Active bots
- Total trades
- Win rate
- Current drawdown
- Open positions
- Total risk exposure

Components:

- Equity curve
- Live prices
- Active strategies
- Running bot statuses
- Recent orders
- Recent trades
- Risk alerts
- Exchange connection indicator
- WebSocket health indicator
- Trading worker health
- Latest reconciliation status
- Emergency stop

## 8.2 Live Trading Terminal

Features:

- BTC/ETH instrument switcher
- Interactive candlestick chart
- Timeframe selector
- Volume
- RSI
- MACD
- EMA
- SMA
- Bollinger Bands
- ATR
- Order book
- Recent trades
- Open orders
- Open positions
- Order placement form
- Long/short actions
- Contract quantity
- Economic notional preview
- Estimated required margin
- Estimated fees
- Stop-loss
- Take-profit
- Leverage
- Order history

Actions must use actual backend endpoints.

Disable unauthorized controls.

## 8.3 Strategies Page

Create a professional strategy management center.

Summary cards:

- Total strategies
- Running strategies
- Paused strategies
- Stopped strategies
- Recent trade count
- Average strategy win rate
- Strategy P&L
- Highest-performing strategy by selected metric

Main table:

- Strategy ID
- Name
- Style
- Instrument
- Timeframe
- Environment
- Status
- Win rate
- Net P&L
- Profit factor
- Maximum drawdown
- Last signal
- Version
- Actions

Actions:

- Create
- Edit
- Duplicate
- Validate
- Backtest
- Start paper trading
- Start demo trading
- Pause
- Stop
- View logs
- Export
- Archive
- Request live approval

Include a strategy performance comparison chart.

## 8.4 Strategy Details Page

Tabs:

- Overview
- Configuration
- Indicators
- Entry Rules
- Exit Rules
- Risk Management
- Backtests
- Paper Results
- Demo Results
- Live Results
- Trade History
- Logs
- Version History

Show the current approved version and its deployment environment.

## 8.5 Strategy Builder

Create a guided wizard and advanced configuration editor.

Allow non-programmers to create strategies using rules.

For advanced users, allow a typed strategy SDK with separately secured plugin execution.

## 8.6 Backtesting Lab

Complete historical simulation workbench.

## 8.7 Paper Trading Dashboard

Separate simulated balances and positions.

## 8.8 Delta Demo Trading Dashboard

Connect exclusively to Delta India demo/testnet.

## 8.9 Live Trading Dashboard

Protected production account interface.

## 8.10 Positions

Display actual environment-specific positions with P&L, margin, liquidation information where available, and protective orders.

## 8.11 Orders

Display orders and their full lifecycle.

## 8.12 Risk Management

Provide risk settings, active restrictions, and breach history.

## 8.13 Performance Analytics

Provide detailed trade and strategy statistics.

## 8.14 Trade Journal

Record signals, decision reasons, orders, fills, and exits.

## 8.15 News and Signals

Monitor authorized market news and event-based filters.

## 8.16 Notifications

In-app notification center.

## 8.17 Integrations

Delta account connection management and Telegram setup.

## 8.18 System Health

Display workers, connections, queues, databases, failures, and recovery status.

## 8.19 Settings

Security, user settings, defaults, sessions, backup configuration, and system settings.

---

# SECTION 9 — ADVANCED STRATEGY CREATION

This is a central product feature.

## 9.1 Strategy Types

Support:

SCALPING

INTRADAY

SWING

TREND_FOLLOWING

BREAKOUT

MOMENTUM

MEAN_REVERSION

VOLATILITY

SUPPORT_RESISTANCE

MULTI_TIMEFRAME

EVENT_FILTERED

CUSTOM

## 9.2 Strategy Metadata

Each strategy must include:

- Unique ID
- Strategy name
- Description
- Creator
- Source
- Version
- Trading style
- Instrument
- Timeframe
- Higher-timeframe dependencies
- Market-regime assumptions
- Indicator dependencies
- Entry logic
- Exit logic
- Risk configuration
- Performance history
- Allowed environments
- Approval state
- Activation state

## 9.3 Entry Conditions

Allow configurable entry rules using:

- Price comparisons
- Indicator crosses
- Indicator thresholds
- Candle patterns
- Trend filters
- Support/resistance levels
- Volume conditions
- Volatility conditions
- Market-regime classification
- Trading sessions
- Funding conditions

Support nested AND/OR/NOT logic.

## 9.4 Exit Conditions

Support:

- Fixed stop-loss
- ATR-based stop-loss
- Structure-based stop-loss
- Fixed reward/risk targets
- Multiple take-profit targets
- Trailing stop
- Break-even adjustment
- Opposite signal exit
- Time-based exit
- Trend invalidation
- Emergency risk exit

## 9.5 No-Trade Conditions

Support:

- Excessive spread
- Insufficient liquidity
- Extreme volatility
- Upcoming high-impact events
- Stale data
- Exchange maintenance
- Risk limits reached
- Maximum concurrent trades
- Excessive funding costs
- Invalid indicator state
- Cooldown active
- Correlated portfolio exposure

## 9.6 Multi-Strategy Operation

Support multiple strategies simultaneously.

Examples:

Strategy A:
BTCUSD, EMA crossover, 1-minute, scalping.

Strategy B:
ETHUSD, breakout with volume, 5-minute, intraday.

Strategy C:
BTCUSD, higher-timeframe trend, 1-hour, swing.

Each strategy must have its own lifecycle.

All strategies share the portfolio risk engine and central execution coordinator.

Handle conflicts when strategies produce opposite signals on the same instrument.

Implement explicit netting/ownership policy instead of assuming independent positions exist.

---

# SECTION 10 — IMPORT STRATEGIES FROM TRADING BOOKS

I will provide trading books in PDF, text, or research-note format.

Create a Strategy Research and Extraction Workspace.

## Import Pipeline

1. Upload document.
2. Parse document content.
3. Identify strategy sections.
4. Extract strategy names.
5. Extract indicators.
6. Extract trading conditions.
7. Extract timeframe requirements.
8. Extract entry rules.
9. Extract confirmations.
10. Extract exit rules.
11. Extract stop-loss rules.
12. Extract profit-taking rules.
13. Extract invalidation conditions.
14. Extract market assumptions.
15. Detect missing rules.
16. Propose mathematically measurable definitions.
17. Generate structured strategy specifications.
18. Create validation examples.
19. Require operator review.
20. Save an approved strategy version.
21. Enable historical backtesting.

## Structured Strategy Schema

Every imported strategy should provide:

- strategy_id
- name
- source_title
- source_location
- trading_style
- supported_markets
- preferred_timeframes
- indicators
- parameters
- market_regime
- long_entry_rules
- short_entry_rules
- confirmation_rules
- no_trade_rules
- invalidation_rules
- stop_loss_rules
- take_profit_rules
- trailing_stop_rules
- position_sizing
- risk_rules
- exit_rules
- automation_ambiguities
- confidence_in_extraction
- pseudocode
- test_cases
- approval_status

Do not silently turn subjective language into arbitrary executable rules.

Example:

A book says, "Buy when momentum is strong."

Mark this as ambiguous.

Suggest candidate measurable conditions, such as an RSI threshold or price-relative-to-moving-average condition.

Present alternatives and require approval.

Do not assume the strategy is profitable or correct merely because it appears in a published book.

## AI Integration

AI can help with:

- Document understanding
- Strategy extraction
- Mathematical translation
- Code drafting
- Test-case generation
- Performance interpretation

AI must not directly control live order routing.

Use a deterministic strategy engine to execute approved rules.

Preserve document references and strategy version history.

---

# SECTION 11 — TECHNICAL INDICATOR ENGINE

Build a reusable indicator framework.

Initial supported indicators:

- SMA
- EMA
- WMA
- RSI
- MACD
- Bollinger Bands
- ATR
- ADX
- Stochastic Oscillator
- VWAP
- Volume moving average
- Donchian Channels
- Keltner Channels
- Supertrend
- Pivot Points

Later expansion may include:

- Market structure
- Swing highs and lows
- Breakout zones
- Volatility regimes
- Support/resistance zones
- Multi-timeframe alignment

Every indicator must have:

- Validated parameters
- Explicit warm-up handling
- Deterministic calculation
- Clear candle-close semantics
- Unit tests against a trusted implementation
- Correct treatment of missing data

Do not allow strategies to act on undefined indicator values.

---

# SECTION 12 — STRATEGY SIGNAL PROCESSING

A strategy must output signals, not exchange API requests.

Signal fields:

- signal_id
- strategy_id
- strategy_version
- environment
- instrument_id
- direction
- timestamp
- data_snapshot_id
- entry_intent
- stop_reference
- target_reference
- confidence if applicable
- reason
- expiration
- invalidation_conditions

Signal workflow:

Market Data
→ Indicator Engine
→ Strategy Rules
→ Signal Generated
→ Signal Validation
→ Portfolio Coordinator
→ Risk Approval
→ Order Intent
→ Execution Engine
→ Delta Adapter
→ Exchange Result
→ Reconciliation
→ Strategy Feedback.

Implement signal deduplication.

A repeated candle or repeated event must not create duplicate exposure.

---

# SECTION 13 — HISTORICAL DATA ENGINE

Store historical price and market data in a normalized form.

Support:

- Downloading Delta historical candles
- Historical data pagination
- Multiple resolutions
- Data caching
- Data integrity checks
- Candle continuity validation
- UTC timestamps
- Source identification
- Deduplication
- Incremental historical updates
- Missing-data records
- Data revisions

Do not fabricate historical candles.

Handle the limitations of API historical-data availability.

Store dataset versions for reproducible backtesting.

Where reliable historical funding, mark-price, or order-book data is unavailable, disclose the approximation used.

---

# SECTION 14 — PROFESSIONAL BACKTESTING ENGINE

Implement event-driven historical simulation.

Avoid overly simplistic candle-crossing backtests that assume perfect fills.

Inputs:

- Strategy version
- Selected market
- Historical date range
- Starting capital
- Timeframes
- Fee assumptions
- Spread model
- Slippage model
- Funding model
- Leverage
- Position sizing
- Margin mode
- Stop-loss rules
- Take-profit rules

Model:

- Long positions
- Short positions
- Orders
- Fills
- Partial fills where meaningful
- Slippage
- Bid/ask spread
- Fees
- Funding
- Margin usage
- Portfolio equity
- Stop execution
- Intrabar ambiguity
- Realistic limitations

Prevent look-ahead bias.

Never execute an order using future candle information.

## Performance Metrics

Calculate:

- Total net profit
- Gross profit
- Gross loss
- Profit factor
- Win rate
- Average win
- Average loss
- Expected value per trade
- Maximum drawdown
- Recovery factor
- Sharpe ratio
- Sortino ratio
- Calmar ratio where meaningful
- Trading fees
- Funding costs
- Consecutive losses
- Consecutive wins
- Average holding duration
- Monthly returns
- Long/short breakdown
- Return on deployed capital
- Exposure-adjusted returns

For ratios, clearly identify calculation conventions and handle insufficient sample sizes.

## Validation Methods

Include:

- In-sample testing
- Out-of-sample testing
- Walk-forward testing
- Parameter stability testing
- Sensitivity testing
- Fee stress tests
- Slippage stress tests
- Market-regime testing
- Monte Carlo analysis of trade sequences with stated assumptions
- Benchmark comparison

Do not optimize exclusively for the highest past return.

Save:

- Strategy version
- Dataset version
- Parameters
- Execution assumptions
- Trade history
- Metrics
- Charts
- Run timestamps
- Reproducibility hash

---

# SECTION 15 — PAPER TRADING MODE

Paper trading must be a functioning internal trading environment.

It must not place orders on Delta production or Delta demo.

Use real or recorded market data, while simulating execution.

## Paper Account

Allow:

- Virtual capital configuration
- Currency configuration
- Reset balance
- Account history
- Multiple strategy assignments
- Virtual margin
- Virtual positions
- Virtual funding charges
- Simulated fees
- Simulated liquidation
- Performance reporting

## Paper Execution

Simulate:

- Market orders
- Limit orders
- Stop orders
- Take-profit orders
- Trailing stops
- Partial fills using declared assumptions
- Order cancellations
- Margin rejection
- Risk limits
- Slippage
- Spread

## Paper Trading User Interface

Show:

- Paper account equity
- Available balance
- Virtual exposure
- Realized P&L
- Unrealized P&L
- Open positions
- Pending orders
- Trade history
- Strategy statistics
- Paper execution logs

Paper strategies must continue running while the browser is closed.

Persist state across server restarts.

A paper account must never hold credentials capable of submitting actual orders.

---

# SECTION 16 — DELTA EXCHANGE DEMO TRADING

Build a distinct demo environment using the official Delta India demo/testnet API.

This mode must submit actual requests to the demo exchange, not the production environment.

## Demo Account Integration

Features:

- Connect demo API key
- Verify credentials
- Fetch demo balances
- Fetch demo positions
- Discover demo products
- Select demo instrument
- Configure demo risk profile
- Submit demo order
- Cancel demo order
- Retrieve order status
- Receive private WebSocket updates
- Retrieve fills
- Reconcile positions
- Generate demo performance reports

## Demo Testing Scenarios

Execute controlled tests for:

1. Connection and authentication.
2. Instrument discovery.
3. Valid minimum-size trade.
4. Invalid quantity rejection.
5. Insufficient margin rejection when safely reproducible.
6. Market order execution.
7. Limit order execution.
8. Limit order cancellation.
9. Stop-loss functionality.
10. Take-profit functionality.
11. Trailing stop where supported.
12. Partial fills when reproducible.
13. Client order ID lookup.
14. WebSocket reconnection.
15. Worker restart.
16. Exchange state reconciliation.
17. Duplicate signal prevention.
18. Rate-limit behavior using simulated failures.
19. Emergency stop.
20. Position closure.

Do not force dangerous trading conditions merely to simulate a failure.

Use controlled mocks/fault injection for failure cases that cannot be reproduced safely on demo.

Every demo test must report whether it was actually executed against Delta or only simulated.

---

# SECTION 17 — LIVE TRADING MODE

Live mode uses:

https://api.india.delta.exchange

Production private and public WebSocket services must be configured separately.

Live trading is disabled by default.

## Live Activation Requirements

Before activation, verify:

- Production credentials are valid.
- Trading permissions are properly scoped.
- Withdrawal permissions are not enabled.
- IP allowlisting is configured where supported.
- Instrument metadata is fresh.
- Strategy version is approved.
- Risk limits are configured.
- Account equity is reconciled.
- No unknown orders remain.
- Existing positions are understood.
- Market data is healthy.
- Execution service is healthy.
- Emergency stop has been tested.
- Monitoring is functional.
- Telegram or equivalent alerts are operational.
- The operator explicitly authorizes live trading.

Require an additional confirmation for material exposure-increasing actions.

## Live Trading Execution

Support:

- Approved automated entry orders
- Approved automated exits
- Position sizing
- Stop-loss
- Take-profit
- Trailing stop
- Partial exits
- Order cancellations
- Position reconciliation
- Realized/unrealized P&L
- Fee and funding accounting
- Risk suspension

## Initial Live Rollout

Use minimum valid order sizes and strict independently configured risk limits.

Do not activate all strategies simultaneously.

Begin with a separately approved limited-capital pilot.

Increase allocation only after operational review and explicit approval.

Do not assume that successful paper or demo performance predicts profitability.

---

# SECTION 18 — CENTRAL ORDER MANAGEMENT SYSTEM

Implement a formal order state machine.

Suggested order states:

CREATED

VALIDATING

RISK_PENDING

RISK_APPROVED

SUBMIT_PENDING

SUBMITTING

ACKNOWLEDGED

OPEN

PARTIALLY_FILLED

FILLED

CANCEL_REQUESTED

CANCELLED

REJECTED

EXPIRED

UNKNOWN

RECONCILIATION_REQUIRED

Use exchange-specific mapping for the actual Delta order states.

Do not assume every state transition is reversible.

Persist order events.

Support idempotent event processing.

## Concurrency

Implement safeguards for:

- Multiple strategies
- Simultaneous signals
- Duplicate WebSocket messages
- Multiple workers
- Strategy restarts
- Manual exchange trades
- Pending order reservations
- Cross-strategy portfolio exposure

Only one logically authorized execution worker should own a given account/order stream at a time.

Use leader election, database locking, leases, or an equivalent reliable mechanism.

## Execution Journal

Record:

- Signal
- Pre-trade checks
- Requested quantity
- Approved quantity
- Estimated margin
- Order submission
- Exchange acknowledgment
- Fill
- Fee
- Funding
- Protective order state
- Position update
- Exit reason
- Final P&L

---

# SECTION 19 — RISK MANAGEMENT ENGINE

This is the most critical component of the entire platform.

All trading strategies must pass through one authoritative risk engine.

## Required Risk Controls

### Trade-Level

- Maximum monetary risk per trade
- Maximum percentage risk per trade
- Minimum/maximum position size
- Stop-loss requirement
- Reward/risk evaluation
- Maximum allowed spread
- Maximum slippage
- Minimum liquidity
- Leverage restrictions
- Contract-size validation
- Margin availability
- Minimum liquidation distance

### Strategy-Level

- Maximum open trades
- Maximum daily strategy loss
- Maximum consecutive losses
- Maximum trade frequency
- Cooldown
- Maximum strategy allocation
- Strategy drawdown threshold
- Automatic suspension conditions

### Portfolio-Level

- Maximum gross exposure
- Maximum net exposure
- Maximum correlated exposure
- Maximum margin utilization
- Maximum account drawdown
- Maximum daily loss
- Maximum weekly loss
- Maximum simultaneous positions
- Maximum orders pending
- Exchange/account health restrictions

### Operational-Level

- Market data freshness
- Exchange connectivity
- Worker health
- System clock health
- Database availability
- Reconciliation status
- Stale order state
- Protective order verification

## Position Sizing Methods

Support:

1. Fixed contracts.
2. Fixed monetary notional.
3. Fixed percentage of equity risk.
4. Fixed maximum monetary risk.
5. Volatility-based sizing.

Derive actual Delta contract quantities from verified product specifications.

Include fees, estimated slippage, and stop-loss distance in risk estimates.

Use Decimal calculations.

Round conservatively and validate exchange increments.

## Default Risk Configuration

Provide editable conservative example presets for testing.

Do not treat example values as universally safe.

Live risk limits must require explicit approval.

No strategy may automatically relax its risk limits based on short-term profitability.

---

# SECTION 20 — STOP-LOSS, TAKE-PROFIT, TRAILING STOP

Implement complete position protection.

## Stop-Loss

Support:

- Fixed percentage
- Fixed price
- ATR-based
- Swing-high/swing-low
- Support/resistance-based
- Strategy invalidation level

Stop prices must be validated against direction, current price, instrument ticks, and the exchange's trigger semantics.

## Take-Profit

Support:

- Fixed target
- Reward/risk ratio
- Multiple targets
- Percentage of position closed
- Indicator exit
- Dynamic target

## Trailing Stop

Support:

- Fixed trailing distance
- Percentage trailing distance
- ATR-based trailing
- Structure-based trailing

Determine whether each trailing method is supported natively by Delta or must be managed by the bot.

If locally managed, disclose dependency on network availability and worker uptime.

## Protection Failure Policy

If an entry fills but its required protective stop cannot be verified:

- Mark the position unprotected.
- Block further entry exposure.
- Attempt a predefined safe recovery.
- Alert immediately.
- Apply an explicitly configured reduction/closure policy when feasible.

Do not falsely display a stop as active until confirmed.

---

# SECTION 21 — AUTOMATED STRATEGY EXECUTION

The system should be able to evaluate valid strategy conditions automatically.

Example lifecycle:

1. Market data arrives.
2. Data is validated.
3. Required indicators are updated.
4. Strategy conditions are evaluated.
5. A signal is generated.
6. Duplicate detection runs.
7. Trading session and cooldown checks run.
8. Portfolio exposure is checked.
9. Risk engine evaluates the order.
10. Contract quantity is calculated.
11. Order intent is persisted.
12. Order is submitted to the selected environment.
13. Exchange/simulator response is processed.
14. Fills and positions are updated.
15. Protective order state is verified.
16. Telegram notification is generated.
17. Statistics and journal are updated.

All steps must be observable.

The strategy must not directly access production API credentials.

---

# SECTION 22 — MARKET REGIME DETECTION

Create a market regime analysis component.

Possible regime categories:

- Trending upward
- Trending downward
- Sideways/ranging
- High volatility
- Low volatility
- Breakout conditions
- Illiquid conditions

Regimes must be defined through transparent and testable calculations.

Allow strategies to specify approved regimes.

Example:

Trend-following strategies may disable new entries in a detected low-volatility ranging regime.

Mean-reversion strategies may avoid identified breakout conditions.

Regime classification must never use future data in historical testing.

---

# SECTION 23 — NEWS AND ECONOMIC EVENT FILTERS

Build a modular event-awareness system.

Monitor approved data sources for:

- Major economic announcements
- Scheduled high-impact events
- Crypto market events
- Exchange maintenance
- Trading suspensions
- Relevant funding events
- Unexpected market volatility

Features:

- Source attribution
- Publication timestamps
- Scheduled event timestamps
- Deduplication
- Event categories
- Data quality validation
- Market-impact filters
- Alert notifications
- Trading blackout windows

News must be obtained through authorized sources.

Do not generate fictional market events.

Do not allow unverified language-model output to create live exchange orders directly.

---

# SECTION 24 — PORTFOLIO MANAGEMENT AND ACCOUNTING

Build a precise portfolio accounting system.

Track:

- Account equity
- Available balance
- Used margin
- Maintenance margin where available
- Realized P&L
- Unrealized P&L
- Trading commissions
- Funding payments
- Currency conversion
- Contract positions
- Strategy ownership
- Trade costs
- Liquidation events
- Daily account snapshots

Keep paper, demo, and production ledgers separate.

The exchange remains authoritative for actual exchange balances, orders, and positions.

Any discrepancy between the local database and exchange must be detected, logged, and reconciled.

Handle trades performed manually outside the bot.

Define how externally created positions affect bot-managed exposure.

Do not silently overwrite exposure records.

---

# SECTION 25 — TELEGRAM INTEGRATION

Add a complete Telegram notification service.

## Alerts

Send:

- System started
- System stopped
- Strategy started
- Strategy paused
- Strategy failure
- Trading signal
- Order submitted
- Order accepted
- Order filled
- Order rejected
- Stop-loss triggered
- Take-profit triggered
- Trailing stop updated
- Daily profit report
- Risk violation
- Margin warning
- Exchange disconnected
- Worker failure
- Reconciliation failure
- Emergency stop

## Telegram Commands

Implement authenticated and allowlisted read-only commands:

/status

/balance

/positions

/orders

/strategies

/pnl

/risk

/health

/logs

For remote trading controls, require stronger authentication and an explicit approval process.

Do not allow anyone who merely knows the Telegram bot username to operate the account.

Protect the Telegram token.

Add retry handling and notification failure tracking.

Do not allow notification service failures to prevent critical execution recovery.

---

# SECTION 26 — AUTOMATED DAILY REPORTS

Generate reports using actual persisted data.

## Daily

- Starting equity
- Ending equity
- Total trades
- Winning trades
- Losing trades
- Net realized P&L
- Unrealized P&L
- Fees
- Funding
- Maximum daily drawdown
- Strategy contributions
- Risk incidents

## Weekly

- Weekly net return
- Equity chart
- Strategy comparison
- Win rate
- Profit factor
- Maximum drawdown
- Transaction costs
- Operational incidents

## Monthly

- Full account performance
- Capital allocation
- Strategy rankings
- Net return
- Drawdown
- Fees/funding
- Market-regime performance
- Execution quality
- Improvement opportunities

Allow export to CSV and machine-readable JSON.

Clearly distinguish backtest, paper, demo, and live reports.

---

# SECTION 27 — FULL DATABASE ARCHITECTURE

Use PostgreSQL.

Create models/tables for:

- users
- sessions
- roles
- exchange_accounts
- exchange_connections
- credential_metadata
- instruments
- instrument_metadata
- historical_candles
- market_data_sources
- strategies
- strategy_versions
- strategy_configurations
- strategy_approvals
- strategy_runs
- strategy_signals
- indicators
- backtest_runs
- backtest_trades
- optimization_runs
- paper_accounts
- trading_accounts
- orders
- order_events
- order_fills
- positions
- position_events
- balance_snapshots
- portfolio_snapshots
- risk_profiles
- risk_decisions
- risk_incidents
- system_events
- exchange_events
- notification_events
- audit_logs
- deployment_events

Use:

- Foreign keys
- Unique constraints
- Transactions
- Indexes
- UTC timestamps
- Numeric financial columns
- Alembic migrations
- Optimistic locking where appropriate
- Append-only critical event records

Keep audit history for changes to live execution permissions and risk configuration.

---

# SECTION 28 — BACKEND API

Create versioned endpoints under:

/api/v1/

Required functional groups:

- auth
- dashboard
- exchanges
- markets
- instruments
- candles
- strategies
- strategy-versions
- signals
- backtests
- paper-accounts
- demo-accounts
- live-readiness
- orders
- fills
- positions
- risk
- performance
- journal
- notifications
- integrations
- system-health

Provide OpenAPI documentation.

Validate all incoming data.

Use typed request and response models.

Do not expose internal credentials through application APIs.

Require role-based authorization.

---

# SECTION 29 — REAL-TIME FRONTEND UPDATES

Use WebSocket or an appropriate server-to-client streaming mechanism.

Update:

- Live prices
- Candles
- Open orders
- Positions
- Account equity
- Unrealized P&L
- Strategy signals
- Bot statuses
- Risk events
- Connection health
- System alerts

Implement:

- Reconnect
- Backoff
- State resynchronization
- Initial snapshots
- Sequence checks where possible
- Stale-data UI warnings

The frontend must clearly show when it is displaying outdated information.

---

# SECTION 30 — SYSTEM SECURITY

Treat live API access as a high-security feature.

Implement:

- Secure authentication
- RBAC
- Secure sessions
- MFA for sensitive operations
- CSRF protection where needed
- Request rate limiting
- HTTPS
- Secure cookies
- Security headers
- Input validation
- Secrets encryption
- Secret rotation procedures
- Audit logs
- Dependency scanning
- Access controls
- Safe logging

## Exchange API Secret Handling

Never:

- Hardcode secrets
- Put secrets in frontend code
- Store secrets in Git
- Expose secrets through API responses
- Print signatures or API secrets in logs
- Send exchange secrets to an LLM provider
- Enable withdrawal permission

Use secured environment/secret-management facilities.

Prefer API keys restricted to necessary trading permissions and approved IPs.

Provide a safe local method for configuring credentials without pasting them into an AI chat.

---

# SECTION 31 — EMERGENCY STOP AND FAILURE RECOVERY

Create an emergency trading control independent of the frontend.

Provide distinct actions for:

- Disable new signals
- Block new entry orders
- Cancel pending entries
- Cancel permitted outstanding orders
- Pause strategies
- Stop workers safely
- Close selected positions
- Flatten managed positions after explicit authorization

A stop action must not silently imply that all positions are closed.

## Failure Conditions

Handle:

- Exchange API outage
- Private WebSocket outage
- Public WebSocket outage
- Database unavailable
- Worker crash
- Server reboot
- Unknown order status
- Position mismatch
- Duplicate event
- Rate-limit errors
- Invalid signature
- Stale market price
- Stop-order rejection
- Insufficient margin
- Liquidation
- Market suspension

During uncertain account state, block new exposure.

Preserve logs and pending reconciliation work.

Use a documented resume workflow.

For emergency closures, account for failed or partially completed actions.

Do not promise a guaranteed exit price.

---

# SECTION 32 — 24/7 AUTONOMOUS OPERATION

Deploy the backend to reliable infrastructure.

Trading must not depend on:

- Browser being open
- Personal PC being turned on
- Active frontend session
- Manual refreshing

Required:

- Dedicated workers
- Automatic restart policies
- Graceful shutdown
- Worker heartbeats
- Durable state
- Recovery after reboot
- Reconnection
- Reconciliation
- Database persistence
- Monitoring
- Alerts
- Backups
- Single-active-executor safeguards

Use a VPS or appropriate cloud environment with reliable long-running processes.

Do not run the entire execution engine inside Next.js API routes or a short-lived serverless function.

Configure firewall, HTTPS, environment isolation, and scheduled backups.

---

# SECTION 33 — LOGGING AND OBSERVABILITY

Create structured logs for:

- Market data
- Signals
- Strategy decisions
- Risk decisions
- Orders
- Fills
- Exchange responses
- Account reconciliation
- Position changes
- Worker lifecycle
- Authentication events
- Notifications
- System failures

Each record should have useful trace identifiers.

Implement:

- Health endpoints
- Metrics endpoints
- Latency metrics
- Exchange connectivity status
- Queue backlog metrics
- Error-rate metrics
- Order acknowledgment latency
- Market data age
- Reconciliation delay
- Restart counts
- CPU and memory metrics
- Backup success monitoring

Health indicators must not falsely represent a degraded system as trading-ready.

---

# SECTION 34 — TESTING REQUIREMENTS

Build comprehensive automated tests.

## Unit Tests

Test:

- Indicators
- Strategy conditions
- Timeframe alignment
- Position sizing
- Contract multiplication
- Margin calculations
- Currency conversion
- P&L
- Fees
- Funding
- Trailing stops
- Risk limits
- Order state transitions

## Integration Tests

Test:

- FastAPI endpoints
- Database transactions
- Redis integration
- Strategy workers
- Exchange adapter
- Paper execution
- Demo execution
- Telegram service
- WebSocket messages

## End-to-End Tests

Test the full path:

Create Strategy
→ Save Version
→ Backtest
→ Review
→ Paper Deploy
→ Signal
→ Risk Check
→ Paper Fill
→ Portfolio Update
→ Journal
→ Notification
→ Stop
→ Restart
→ Recover.

Repeat the applicable path using demo trading.

## Fault Injection

Test:

- API timeout after order submission
- WebSocket disconnection
- Duplicate signal
- Duplicate fill
- Database crash
- Worker crash
- Server reboot
- Rate limit
- Position mismatch
- Invalid market data
- Stop rejection
- Partial fill
- Risk breach

Do not use production money for fault-injection tests.

## Performance Testing

Measure:

- Feed processing latency
- Signal processing time
- Risk evaluation time
- Order submission latency
- Reconciliation latency
- Worker throughput
- API responsiveness
- Database performance

Record real measurements rather than inventing performance claims.

---

# SECTION 35 — STRATEGY PROMOTION RULES

Every strategy must follow a controlled deployment lifecycle.

## Stage 1 — Draft

Strategy exists but is not tradable.

## Stage 2 — Validated

Mathematical conditions, configuration, and tests are complete.

## Stage 3 — Backtested

Historical simulation completed with realistic costs and documented limitations.

## Stage 4 — Paper Approved

Internal paper trading has been tested.

## Stage 5 — Demo Approved

Delta India demo trading has passed required integration checks.

## Stage 6 — Live Ready

All operational, security, and risk gates have been reviewed.

## Stage 7 — Limited Live

Operator explicitly authorizes small-size deployment.

## Stage 8 — Production Monitoring

Trading continues within the approved limits, with automatic suspension on critical failures.

Require evidence for every promotion.

Do not automate promotion to live simply because a strategy meets a profit threshold.

---

# SECTION 36 — PROJECT REPOSITORY STRUCTURE

Use a maintainable monorepo similar to:

tradebot-pro/
  apps/
    web/
  services/
    api/
    market-data/
    strategy-engine/
    risk-engine/
    execution-engine/
    backtesting/
    paper-trading/
    notifications/
    reconciliation/
  packages/
    exchange-delta-india/
    strategy-sdk/
    shared-models/
    financial-math/
  infrastructure/
    docker/
    reverse-proxy/
    monitoring/
    deployment/
  tests/
    unit/
    integration/
    contract/
    end-to-end/
    fault-injection/
  docs/
    architecture/
    delta-api/
    strategy-development/
    backtesting/
    paper-trading/
    demo-trading/
    live-trading/
    security/
    operations/
  scripts/
  docker-compose.yml
  .env.example
  README.md

Adapt the structure if there is a sound technical reason.

Maintain strict module boundaries and avoid unnecessary duplication.

---

# SECTION 37 — DEVELOPMENT PHASES

Execute the implementation through these phases.

## Phase 1: Requirements and Architecture

Deliver:

- Architecture diagrams
- Service responsibilities
- Delta API capability matrix
- Data flow
- Database schema
- State machines
- Risk design
- Project structure
- Development plan

## Phase 2: Backend Foundation

Implement:

- FastAPI
- PostgreSQL
- Redis
- Authentication
- API structure
- Migrations
- Logging
- Health checks
- Test framework

## Phase 3: Delta Market Data

Implement:

- Product discovery
- Instrument metadata
- Historical candles
- Public WebSocket feeds
- Data validation
- Reconnection
- Market data storage

## Phase 4: Strategy Engine

Implement:

- Strategy SDK
- Indicator engine
- Strategy registry
- Configuration models
- Signal generation
- Strategy persistence
- Versioning

## Phase 5: Risk and Execution Foundation

Implement:

- Central risk engine
- Position sizing
- Order state machine
- Portfolio accounting
- Delta order adapter
- Idempotency
- Reconciliation

## Phase 6: Backtesting

Implement:

- Historical simulation
- Trade cost models
- Performance statistics
- Backtesting dashboard
- Validation reports

## Phase 7: Paper Trading

Implement:

- Virtual accounts
- Simulated orders
- Paper positions
- Fee and funding models
- Risk enforcement
- Paper statistics
- Restart recovery

## Phase 8: Delta Demo

Implement:

- Testnet authentication
- Demo balances
- Demo orders
- Demo positions
- Private WebSocket
- Demo execution logs
- Reconciliation tests

## Phase 9: Complete Frontend

Implement all dashboard and management pages with real backend integration.

Frontend components can be developed earlier in parallel, but this phase must eliminate remaining gaps.

## Phase 10: Notifications and Analytics

Implement:

- Telegram
- Reports
- Performance charts
- Trade journals
- System monitoring

## Phase 11: Deployment and Reliability

Implement:

- Docker
- VPS deployment
- Reverse proxy
- HTTPS
- CI/CD
- Monitoring
- Backups
- Restart recovery
- Operational documentation

## Phase 12: Live Readiness

Conduct:

- Security review
- Delta production configuration verification
- Risk review
- Reconciliation tests
- Emergency stop tests
- Backup/restore tests
- Deployment review

Prepare the platform for explicit live activation.

Do not activate it automatically.

---

# SECTION 38 — ACCEPTANCE CHECKLIST

The system cannot be declared complete without evidence for the following.

## Frontend

- [ ] Dashboard works.
- [ ] Strategies page works.
- [ ] Strategy builder works.
- [ ] Backtesting page works.
- [ ] Paper trading dashboard works.
- [ ] Demo dashboard works.
- [ ] Live dashboard is access-controlled.
- [ ] Charts update correctly.
- [ ] Real backend data is displayed.
- [ ] Settings persist.
- [ ] UI is responsive.

## Backend

- [ ] API endpoints function.
- [ ] Database persists records.
- [ ] Strategy engine generates signals.
- [ ] Risk engine enforces limits.
- [ ] Order state machine works.
- [ ] Portfolio accounting works.
- [ ] Reconciliation works.
- [ ] Worker restarts safely.

## Delta API

- [ ] India production public connectivity verified.
- [ ] India demo API connectivity verified.
- [ ] Demo authentication verified.
- [ ] Correct contract metadata retrieved.
- [ ] Demo order submitted and confirmed.
- [ ] Demo order cancellation verified.
- [ ] Demo fills retrieved.
- [ ] Demo positions reconciled.
- [ ] WebSocket feeds verified.
- [ ] Authentication errors handled.
- [ ] API rate limits handled.
- [ ] Unknown-order recovery tested.

## Trading

- [ ] Strategies remain independently configurable.
- [ ] Multi-timeframe strategies work.
- [ ] Contract sizing is correct.
- [ ] Position sizing is correct.
- [ ] Stop-loss behavior tested.
- [ ] Take-profit behavior tested.
- [ ] Trailing stops tested.
- [ ] Risk limits cannot be bypassed.
- [ ] Fees and funding are accounted for.
- [ ] Duplicate signals do not create duplicate exposure.
- [ ] Strategy state survives restart.

## Operational Safety

- [ ] Paper mode cannot route real orders.
- [ ] Demo mode cannot route production orders.
- [ ] Live trading is disabled by default.
- [ ] Production secrets are securely stored.
- [ ] No exchange secrets are exposed through frontend APIs.
- [ ] Emergency stop works.
- [ ] System blocks new exposure after critical failures.
- [ ] Reconciliation runs on startup.
- [ ] Monitoring works.
- [ ] Alerts work.
- [ ] Database backups work.
- [ ] Restore procedure is tested.

## Documentation

- [ ] Installation guide.
- [ ] Development guide.
- [ ] Architecture guide.
- [ ] Delta API integration guide.
- [ ] Strategy development guide.
- [ ] Backtesting guide.
- [ ] Paper trading guide.
- [ ] Demo trading guide.
- [ ] Live readiness guide.
- [ ] Deployment guide.
- [ ] Operational incident runbook.
- [ ] Known limitations.

---

# SECTION 39 — AI AGENT WORKING RULES

Follow these mandatory instructions.

1. Inspect the repository before writing code.
2. Create a clear implementation plan.
3. Start implementing rather than only explaining.
4. Write actual source files.
5. Keep the project runnable throughout development.
6. Use official documentation for Delta India API integration.
7. Never invent endpoint capabilities.
8. Never claim an unexecuted test passed.
9. Use mocks only where clearly appropriate.
10. Never present mock trading as live/demo trading.
11. Use secure handling for credentials.
12. Never request production API secrets in chat.
13. Write meaningful tests.
14. Fix failing tests or report blockers.
15. Keep code typed and modular.
16. Avoid giant monolithic files.
17. Do not implement trading risk controls only in the frontend.
18. Do not allow strategies to bypass execution authorization.
19. Preserve database and exchange state during restarts.
20. Keep paper/demo/live environments fully separate.
21. Never assume API requests failed solely because the network timed out.
22. Never claim guaranteed profits.
23. Do not automatically enable live trading.
24. Document assumptions and implementation limitations.
25. Maintain a project progress file.
26. Record completed, blocked, and unverified tasks.
27. Complete one phase to its acceptance criteria before depending on it in later phases.
28. Use practical engineering defaults when requirements are noncritical.
29. Ask for decisions only when necessary for secrets, account authorization, financial risk, exchange eligibility, or ambiguous strategy definitions.
30. Never fabricate production operational evidence.

At the end of each phase, report:

- Features implemented
- Files created
- Files modified
- Tests run
- Actual test results
- API features verified
- Known defects
- Missing requirements
- Security concerns
- Next implementation actions

If blocked by missing API credentials, continue with independent development, mocks, contract tests, and safe public API features. Identify which private-account tests remain unverified.

---

# SECTION 40 — FINAL SYSTEM OBJECTIVE

The final platform must let me:

1. Open a professional web trading dashboard.
2. Connect a Delta Exchange India account securely.
3. View actual market data.
4. Create individual trading strategies.
5. Import strategy rules from books and documents.
6. Convert approved strategy concepts into mathematical trading logic.
7. Configure indicators and trading conditions.
8. Configure stop-loss, take-profit, and risk rules.
9. Backtest strategies using historical data.
10. Compare strategy performance.
11. Run strategies using virtual money.
12. Monitor autonomous paper trading.
13. Connect a Delta India demo account.
14. Execute real demo orders.
15. Test order protection and recovery.
16. Review strategy readiness.
17. Explicitly authorize limited live trading after safety gates pass.
18. Monitor actual positions, equity, orders, and risk.
19. Receive Telegram notifications.
20. Review trade journals and reports.
21. Pause or stop strategies.
22. Activate emergency risk controls.
23. Recover the system after crashes and exchange interruptions.
24. Operate continuously on a VPS without keeping my computer running.

The application must be genuinely functional, operationally observable, secure, maintainable, and extensible.

**Success means demonstrated system behavior with real tests—not a visually impressive interface, a positive backtest, or a claim that the bot is production-ready.**

---

# SECTION 41 — BEGIN IMPLEMENTATION

Start immediately.

Your first task is to inspect the current repository and establish the actual Delta Exchange India integration capabilities using the latest official documentation.

Then:

1. Produce the system architecture.
2. Produce the database schema.
3. Define the trading environment isolation model.
4. Define the signal and order state machines.
5. Define the risk engine architecture.
6. Set up the monorepo.
7. Set up Docker-based development infrastructure.
8. Implement the foundational backend.
9. Begin the Delta India market-data adapter.
10. Add tests and demonstrate working results.

Continue through the documented phases, maintaining runnable milestones.

Do not stop after generating a plan or dashboard.

Do not automatically submit real-money trades.

Do not mark unfinished work as completed.

**Build TradeBot Pro as a serious Delta Exchange India automation system, designed for validated backtesting, realistic paper trading, actual exchange demo testing, and tightly controlled live deployment.**