# Observed market data and operational monitoring

The worker maintains public Delta India subscriptions independently of browser sessions. A PostgreSQL lease allows one producer across worker restarts or multiple workers. It consumes the documented current public socket channels `ticker`, `ob_l2`, `mark_price`, `funding_rate` and strategy candle resolutions. Five actual read-only channel probes passed on 9 October 2026; [the evidence](validation/market-stream-2026-10-09.json) records the endpoint, exchange timestamp and normalized observation.

The top 15 book levels are complete exchange snapshots. The service does not pretend these snapshots are a full depth book, or apply incremental book deltas without checksum validation. A disconnected book remains unavailable until the next validated snapshot. Reordered/conflicting exchange timestamps, crossed or unordered books, negative quantities and malformed candles require recovery. Exact duplicate observations do not advance the feed revision.

Compact WebSocket candle events provide a publish timestamp without an authoritative candle-open timestamp in the verified schema. They are intrabar display observations and never become executable historical candles. Closed trading history continues to use the validated REST importer. On candle stream recovery, REST fills actual available observations and records unresolved intervals; it never substitutes a fabricated candle. A REST ticker recovery snapshot remains visibly different from continuous WebSocket data.

The authenticated `/api/v1/market/stream` SSE endpoint emits an initial complete snapshot and another every two seconds. Reconnecting clients receive a fresh snapshot. Each snapshot reports its connection sequence, durable feed revisions, source, exchange/receive timestamps, age, status and gap observations. Session revocation, expiry or a disabled user closes the stream. Browser consumers must display stale/recovering/unavailable states. Fresh receive time does not conceal an old exchange timestamp. Funding is presented in the raw documented exchange units and is not certified for historical accounting.

## Reproducible historical datasets

The operational candle cache accepts authoritative exchange corrections while immutable `candle_revisions` retain original and corrected OHLCV observations. Normalization and SHA-256 hashing preserve decimal precision independently of the process's Decimal context. Imported observations identify the Delta REST source. Older cache entries whose original provenance was not recorded are labeled explicitly.

`POST /api/v1/market/datasets` takes `symbol`, `timeframe_seconds` and an optional `limit`. It pins an ordered list of immutable revision identities, sources and hashes. Repeating the same snapshot returns the same content identity. Subsequent cache corrections cannot change that dataset. The response includes missing ranges, completeness and approximation disclosures. The dataset only describes the observed interval; it does not assert that earlier unavailable exchange history exists. `/market/datasets/{id}/candles`, `/market/revisions` and `/market/gaps` expose the retained evidence to authenticated users. Snapshot creation requires an operator and CSRF validation.

Historical funding, mark-price and order-book time series are not implied by an OHLCV dataset. Backtests must disclose any substitute assumptions and pin a dataset for exact reruns. Missing data resolves only when actual observations cover the missing interval.

## Logs and metrics

API and worker logs use JSON with UTC time, event type and applicable strategy, signal, order, account or trace identities. Request trace IDs are bounded and validated. Credential-bearing structured fields, connection URL credentials, API keys, bearer tokens, signatures and passwords are redacted. Transport failures emit an exception class rather than sensitive exception text or raw request bodies. API latency labels use route templates, never user query strings or uploads.

The internal `/metrics` endpoint exposes measured API latency/error distributions, process CPU and memory, database availability, worker freshness, market age/continuity/gaps, missing history, unknown orders, notification backlog, reconciliation delay, observed process starts, acknowledgment timings and backup results. No account balances or credentials appear in metrics. `/api/v1/monitoring` provides authenticated operational evidence for the application UI. Unobserved backup/reconciliation evidence remains unknown. A failed backup or reconciliation retains the previous successful timestamp while exposing the failed last attempt. A database/migration failure cannot leave a partly collected healthy state in the response.

## Bounded clock evidence

PAPER entry and remaining partial-fill gates use persisted clock evidence rather than an assumed healthy clock. The existing public REST adapter observes the verified HTTPS response `Date`/`Age` headers, local request/response UTC timestamps and monotonic round-trip time. It does not log response bodies, credentials or unrelated headers. The observation includes HTTP Date's one-second resolution: the absolute offset bound adds half the measured RTT and half a second of quantization uncertainty.

A usable observation requires the configured Delta public HTTPS source, a valid explicit UTC Date, no known positive cache Age, a round trip at most two seconds, wall/monotonic elapsed-time difference at most 0.25 seconds and absolute offset plus uncertainty at most five seconds. Evidence expires after 120 seconds. A local clock that moves backward enough to put the saved sample in the future fails closed. Missing, malformed, cached or excessively slow attempts cannot create health; they preserve a still-fresh prior usable observation. A measured excessive offset or local wall-clock jump blocks immediately and remains blocking until a new usable measurement arrives. Application/cloud bootstrap never invents a healthy default. Offline backend fixtures seed explicitly labeled synthetic evidence only in isolated test databases.

Measurements are ordered by monotonic response time within an opaque Linux boot/time-namespace identifier, rather than wall time. PostgreSQL row locks and refreshed session state keep concurrent or delayed callbacks from clearing a newer unsafe observation. Evidence from another host, boot or time namespace cannot establish this process's health. A wall-clock change relative to monotonic elapsed time after a saved sample also blocks, without waiting for the next response. Platforms without this monotonic provenance fail closed.

Standard procfs with both time-namespace interfaces absent uses boot identity
as the shared domain; deployments must not selectively hide those interfaces.
Public clients collect observations during IO and persist them after async
cleanup, in a short independent transaction using a separate unpooled
connection. This prevents event-loop lock/pool deadlocks and retains unsafe
evidence even when the market-data transaction rolls back. Use PostgreSQL or
file-backed SQLite for this workflow; independent durable evidence cannot be
shared through a private in-memory SQLite database.

`/api/v1/clock`, `/api/v1/monitoring` and the clock metrics expose source, age, measured uncertainty, last attempt and unresolved blocking reasons. Two actual public HTTPS observations were checked on 9 October 2026: a cached ticker Date was rejected and an uncached product response supplied a usable bounded estimate; [the evidence](validation/clock-public-2026-10-09.json) retains the measurements. Missing reliable observations block new exposure while existing protective/reducing operations remain independently managed.

HTTP Date can be generated by a CDN or managed TLS intermediary, and an absent `Age` header does not prove an origin response was uncached. This bounded estimate detects operational UTC discrepancies; it cannot independently certify Delta's signing clock or replace OS NTP/time-service monitoring. Credentialed demo signing acceptance and production time-service deployment evidence remain separate requirements.

## Optional monitoring containers

Run `.venv/bin/python scripts/setup-monitoring.py` once. It creates a random Grafana password in the ignored private `.runtime/monitoring` directory and preserves an existing password. The password is never printed. Its file is readable only by the runtime account/group; Compose grants Grafana that one supplemental group so file-backed secrets work without making the password world readable. On a VPS with a different service group, set `TRADEBOT_GRAFANA_SECRET_GID` to the file's group ID.

Start the app using `scripts/compose-app.py up` first. Then start only the
optional monitoring services, preserving the helper's app UID/vault mapping:

```bash
TRADEBOT_GRAFANA_SECRET_GID="$(stat -c %g .runtime/monitoring/grafana-password)" docker compose -f docker-compose.yml -f infrastructure/docker-monitoring.yml --profile app --profile monitoring up -d --wait --no-deps prometheus grafana
```

Prometheus binds to `127.0.0.1:9090`; Grafana binds to `127.0.0.1:3001`, uses the `operator` account, disables anonymous access/sign-up and provisions the TradeBot dashboard. Access these boundaries through a secure tunnel when needed. The compose overlay uses digest-pinned images and persistent monitoring volumes. It does not replace the application's authentication.

Prometheus configuration and operational alert rules pass the actual pinned image's `promtool` validation. The Grafana dashboard uses measured series and displays absent values as unobserved. Local alert evaluation does not prove alert delivery: configure and test an authorized external receiver before claiming an operator will be notified. Production image vulnerability review, capacity/retention sizing and off-host monitor availability are separate deployment checks.

Run `.venv/bin/python scripts/check-monitoring.py` for actual API scrape,
evaluated rules and authenticated Grafana dashboard/datasource acceptance.
