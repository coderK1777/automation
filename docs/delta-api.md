# Delta Exchange India integration evidence

This milestone provides a dedicated async public adapter and a separate guarded
India demo private adapter. It has no production private order implementation.
Private mock tests are wire-contract evidence, not successful exchange demo tests.

## Authoritative documentation verification

On 9 October 2026 (India local date), initial TLS-verified requests were rejected
by the cloud egress proxy with `403 Forbidden` during CONNECT. After the domain
settings reached the runtime, all eight actual production/demo public checks
passed: product discovery, ticker, historical candles, and public ticker WebSocket
for each environment. The sanitized [public evidence](validation/delta-public-2026-10-09.json)
records the results at `2026-10-08T22:01:21Z` (9 October in India).

The official documentation **source** was retrieved through the existing scoped
HTTPS Git route from https://github.com/delta-exchange/slate at commit
`44de5b7589c7ae0348feacd22c76a2dd37b0e003` (committed 5 October 2026).
Its `CNAME` is `docs.delta.exchange`. The sources below confirm the current India
destinations, current `key-auth`, API paths, and the public-channel migration.
The published documentation website still returned HTTP 403 on recheck. Current
documentation is verified against the official Git source; actual REST and public
WebSocket availability are separately verified by the smoke report. A Git read
alone does not prove runtime API availability.

| Official source at verified commit | What was checked |
| --- | --- |
| [REST API](https://github.com/delta-exchange/slate/blob/44de5b7589c7ae0348feacd22c76a2dd37b0e003/source/includes/_rest_api.md) | India base URLs, products, tickers, orders, positions, balances, candles |
| [Authentication](https://github.com/delta-exchange/slate/blob/44de5b7589c7ae0348feacd22c76a2dd37b0e003/source/includes/_authentication.md) | HMAC SHA256, method + timestamp + path + query + raw body, required headers, five-second signature lifetime |
| [WebSocket](https://github.com/delta-exchange/slate/blob/44de5b7589c7ae0348feacd22c76a2dd37b0e003/source/includes/_websocket_api.md) | India public/private sockets, numeric key-auth timestamp, compact ticker/candle payloads, private fills and sequence numbers |
| [Limits](https://github.com/delta-exchange/slate/blob/44de5b7589c7ae0348feacd22c76a2dd37b0e003/source/includes/_rate_limits.md) | 20,000 weighted units / five-minute window, reset header in milliseconds, endpoint weights |
| [Changelog](https://github.com/delta-exchange/slate/blob/44de5b7589c7ae0348feacd22c76a2dd37b0e003/source/includes/_changelog.md) | Public-channel migration and legacy private-socket removal scheduled 31 July 2026; client-order IDs unique across all open orders |

## Central environment configuration

Only `packages/delta/tradebot_delta/config.py` owns exchange destinations. There
are no Global endpoints and no custom private base-URL override.

| Environment | REST | Public WebSocket | Private WebSocket |
| --- | --- | --- | --- |
| India production | `https://api.india.delta.exchange` | `wss://public-socket.india.delta.exchange` | `wss://socket.india.delta.exchange` |
| India demo | `https://cdn-ind.testnet.deltaex.org` | `wss://socket-ind-pub.testnet.deltaex.org` | `wss://socket-ind.testnet.deltaex.org` |

`Environment` defines PAPER, DEMO, and LIVE. Paper can consume production public
prices without credentials. `DeltaClient(environment='production'|'demo')` exposes
public methods only. `DemoClient` accepts only demo-scoped credentials and rejects
production/paper initialization. Redirects are never followed with credentials.
Private writes additionally require an explicit boolean `allow_demo_orders=True`.
The application's execution coordinator must authorize and risk-check each demo
instruction; this flag is not an approval or evidence of account eligibility.

Required network additions are `docs.delta.exchange`, `api.india.delta.exchange`,
`cdn-ind.testnet.deltaex.org`, `public-socket.india.delta.exchange`,
`socket.india.delta.exchange`, `socket-ind-pub.testnet.deltaex.org`, and
`socket-ind.testnet.deltaex.org`. Public runtime verification followed an observed
policy change; saving an environment draft alone is not readiness evidence.

## Capability matrix

| Capability | Official contract | Implementation | Actual exchange evidence |
| --- | --- | --- | --- |
| Products | `GET /v2/products`, cursor pagination; `/v2/products/{symbol}` | Public; metadata normalization and pagination | Production 231 / demo 16 perpetual products discovered; BTCUSD + ETHUSD identified |
| Tickers | `GET /v2/tickers`, `/v2/tickers/{symbol}` | Public | Actual BTCUSD ticker passed in production and demo |
| Historical candles | `GET /v2/history/candles`, maximum 2000 / response | Public; split ranges, exact decimal strings, sorted/deduplicated OHLCV | Actual BTCUSD one-minute OHLCV passed in production and demo |
| Public ticker | New public socket channel **`ticker`** | Subscription, heartbeat, bounded reconnect/backoff, gap/staleness signals | Actual subscription received validated ticker on production and demo public sockets |
| Public candles | New public socket `candlestick_<resolution>` | Compact public message validation | Not tested against exchange |
| Orders / balance / positions | `POST` / `DELETE /v2/orders`, `GET /v2/wallet/balances`, `/v2/positions/margined` | Demo only, opt-in writes | Controlled offline mock only; credentials absent |
| Order lookup | `/v2/orders/{id}`, `/v2/orders/client_order_id/{client_oid}` | Demo only; stable client ID supplied by execution coordinator | Controlled offline mock only |
| Private socket auth | `key-auth`, HMAC of `GET` + timestamp + `/live` | Demo only; numeric timestamp, explicit success/status acknowledgement | Controlled offline mock only |
| Private account feeds | `orders`, `positions`, `v2/user_trades`, `margins` | Demo subscription; documented sequence gaps stop stream for reconciliation | Controlled offline mock only |
| Mark / funding / trades / order book / system status streams | `mark_price`, `funding_rate`, `trades`, `ob_l1`, `ob_l2`, `ob_updates`, `system_status` | Not implemented in this milestone | Not tested |
| Advanced demo orders | Official stop, trigger, trailing, bracket APIs | Deferred; market/limit, reduce-only, post-only, GTC/IOC only | Not tested |
| Account reconciliation, partial fills, demo cancel/fill lifecycle | REST + private feed | Adapter primitives; complete account reconciliation remains a separate execution milestone | Not tested against demo |
| Production private trading | Separate explicit authorization needed | **Unavailable** | Never executed |

`v2/ticker` is a legacy channel. The verified documentation migrates it to
`ticker` on the public socket. Market feeds must be shared centrally and fanned
out to strategies. On a `stream_gap`, callers must pause decisions and backfill
market data or reconcile account state. Order-book snapshot/sequence recovery is
not provided by this milestone, so order-book execution is unsupported.

## Contract economics and INR reporting

An API documentation example is not actual product discovery. The official
BTCUSD example uses `contract_value="0.001"`, `contract_unit_currency="BTC"`,
`tick_size="0.5"`, `notional_type="vanilla"`, and USD quote/settling asset objects,
while its prose description says INR. Its underlying asset object also says USD.
Those contradictory example fields cannot certify India settlement or an INR
conversion formula. ETHUSD live economics remain unverified too.

Actual read-only product discovery and individual product checks resolved the
example conflict: preferred contracts identify underlying BTC/ETH, matching
contract-unit currency, `notional_type=vanilla`, `is_quanto=false`, and **USD quote
and USD settlement**, with production descriptions also specifying US dollars.

| Actual environment | Symbol | Observed ID | Contract multiplier | Tick | Quote / settlement |
| --- | --- | --- | --- | --- | --- |
| Production | BTCUSD | 27 | 0.001 BTC | 0.5 | USD / USD |
| Production | ETHUSD | 3136 | 0.01 ETH | 0.05 | USD / USD |
| Demo | BTCUSD | 84 | 0.001 BTC | 0.1 | USD / USD |
| Demo | ETHUSD | 1699 | 0.01 ETH | 0.05 | USD / USD |

These are dated observations, not hardcoded product IDs. The production/demo BTC
tick difference demonstrates why each environment requires its own discovery.

The adapter retains actual `contract_value`, denomination, notional type,
`is_quanto`, and all asset fields without assuming that one contract means one
BTC or that quote/settlement currencies are interchangeable. Documented integer
derivative order size supplies `qty_step="1"`; missing minimum/maximum quantity
stays absent. The official `less_than_order_min_size` error confirms a product may
have an exchange minimum beyond the integer quantity increment. `initial_margin`
stays raw because its unit is not explicit in the checked schema.
`linear_economics_verified=True` means an actual TLS response passed the complete
linear multiplier/tick/currency checks; injected mock data cannot earn that flag.
`metadata_verified=False` still prevents confusing this limited scope with
certified exchange quantity limits, account margin tiers, or demo-order acceptance.
An internal paper/backtest model may use its own explicitly labeled whole-contract
minimum and conservative leverage/margin assumptions; those remain simulator
parameters rather than claims about actual minimum order acceptance or liquidation.
An INR portfolio requires an actual, dated conversion snapshot where currencies
differ. Unverified, inverse, quanto, or unsupported conversion contracts must
remain blocked by the risk/execution layer. No product ID or FX rate is hardcoded.

## Authentication and safe failures

HMAC signs the exact prepared path, encoded query (including `?`), and UTF-8 body
bytes sent by HTTPX. JSON is serialized once. A new timestamp is generated after
throttling, immediately before submission. Headers include `api-key`, `signature`,
`timestamp`, and `User-Agent`. TLS verification remains enabled.

Credentials are never accepted by the public client and never belong in frontend
responses. No keys were present or used for this onboarding. Demo HMAC requires
securely supplied **local** secret material from a supported secret manager/runtime
binding. Egress proxy placeholders are not raw HMAC secrets; obvious placeholders
are rejected. Do not enter values in chat or save them in source code/scripts.
Account eligibility, API terms, permission/IP allowlisting, reliable host clock
synchronization and drift monitoring, and actual demo authentication are still
required before private tests. The exchange rejects signatures over five seconds
old; the adapter does not claim to install NTP or independently certify host time.

The client spaces requests conservatively and tracks documented weighted quota.
Safe GET retries use bounded backoff and `X-RATE-LIMIT-RESET` (milliseconds) /
`Retry-After` (seconds). An excessive delay returns control instead of retrying
early. Quota is shared across all exchange traffic on an account/IP; this adapter's
per-client accounting is conservative but cannot account for external processes.
WebSocket 429 stops reconnect attempts; documentation says wait 5–10 minutes and
limits connection attempts to 150 / five minutes / IP.

Private writes are sent once. Timeout, network loss, server error, redirect, or
malformed acknowledgement raises `OrderSubmissionUnknown`. The execution ledger
must persist UNKNOWN, prevent duplicate submission, look up the stable client ID,
inspect fills and positions, and resume only after reconciliation. This adapter
provides no blind POST retry or automatic live promotion.

## Reproduce the evidence

Run offline contract tests:

```sh
.venv/bin/python -m pytest -q tests/contract/test_delta_adapter.py
```

These tests explicitly declare `mocked`; their fixtures are invented test data,
including invented product ID 12345. They check signing (including an independent
OpenSSL known vector), actual transmitted bytes, public/demo environment
separation, rate reset headers, pagination, decimal precision, uncertainty without
retry, key-auth, and stream gaps. They are not demo acceptance evidence.

Run the actual **public** smoke check (it reads no credentials and cannot submit
orders); the checked-in evidence records a successful run:

```sh
.venv/bin/python scripts/check_delta.py --environment both --output /tmp/delta-evidence.json
```

It records production/demo products, BTCUSD ticker/candles, and public ticker
messages. Failures remain failures in the JSON report; there is no fallback fake
data. Private demo authentication, balance, quantity review, authorized demo order,
acknowledgement, lookup, cancellation, fill/position/private stream reconciliation
remain unverified until secure credentials and explicit demo execution approval
are available. Production-money tests are outside this milestone.
