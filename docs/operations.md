# Operations and incident runbook

The supplied infrastructure is for repeatable development and future deployment preparation. It is not evidence of 24/7 exchange trading reliability. Live execution stays disabled and actual demo execution is pending.

## Process model

API, PAPER/public-market worker, isolated DEMO worker, auxiliary reporting/notification worker and web are separate processes. Workers run independently of a browser. PostgreSQL persists trading/research records; Redis supports coordination and dependency health. Docker restart policies assist recovery but do not prove it. A service restarting successfully does not imply its account state is reconciled.

`bash scripts/dev.sh up` applies Alembic migrations, preserves the bootstrap operator and private vault, and starts the five owned application processes. `status`, `restart` and `down` inspect/manage those processes. Durable positions/orders and restrictions remain in the database. Do not reset a database, account or strategy to make a health check pass.

Use the backend health/system-health endpoints and worker heartbeat. Observe dependency failure, stale market data, last worker error and any reconciliation-required flag. A public exchange network failure must show degraded/unverified state rather than healthy exchange trading readiness.

## Incident actions

| Symptom | Immediate action | Before resume |
| --- | --- | --- |
| Stale/missing market data | Block new entries; preserve last timestamp and source | Validate fresh closed candles and continuity; do not generate substitute prices. |
| Worker crash | Preserve durable state; restart only the owned worker | Inspect lease, heartbeat, duplicate prevention and current positions. |
| Database/Redis unavailable | Block new exposure; retain logs | Restore dependency, rerun functional health and review incomplete operations. |
| Unexpected paper position/account mismatch | Set/review reconciliation-required state; block entries | Compare orders/fills/account ledger and record correction evidence. |
| Unknown future exchange submission | Preserve original client ID; never blindly retry | Authoritative lookup/fills/positions and explicit resolution. |
| Unverified protective order | Mark unprotected; block new exposure and alert | Confirm protection or apply separately approved reduction policy. |
| Credential/signature failure | Block affected exchange operations; redact secret material | Verify time, scope, destination and secure credential rotation. |
| Rate limiting/outage | Stop unsafe retries, respect supported backoff | Recover subscriptions/snapshots and reconcile before entries. |

PAPER emergency actions distinguish blocking entries, canceling pending reservations and explicitly flattening managed simulated positions. DEMO cancellation and managed reduce-only closes belong to its separately authenticated, locally authorized executor; protection remains active when entries are blocked. Production operations are unavailable. An emergency stop is not a guarantee of position closure or execution price.

## Backup and restore

The development snapshot helper is `.venv/bin/python scripts/backup.py`. It writes a custom-format `pg_dump` to ignored `.runtime/backups/snapshot.dump` through a private temporary file and atomic replacement. `restore-snapshot.py` restores retained state only into a genuinely empty development database; it preserves a populated database. Docker named volumes may not survive cloud publication, so a retained snapshot is required to restore database contents. Publication does not retain API/worker/web processes.

This local snapshot is not an off-host backup system. Schedule PostgreSQL backups to encrypted storage outside the application host. Verify a current backup can restore into an isolated database using the documented PostgreSQL version. Compare migration revision, critical constraints, order/fill identities, account balances and position quantities. Exercise restart against the restored copy without exchange execution.

The local restore drill `.venv/bin/python scripts/check-backup.py` restores the custom-format dump into a disposable database and verifies the current migration head, every registered application table, operator and instrument records before removing that test database. Authenticated encryption, verified SSH/SFTP transfer tooling and optional fail-closed backup timers are implemented; actual off-host transfer and disaster recovery remain unverified without a provisioned destination. See [VPS and offsite backups](vps-deployment-and-offsite-backups.md). Preserve the original credential-vault key and a separately protected backup-encryption recovery key. Never include secret files in an unencrypted export.

## VPS deployment prerequisites

Provide a dedicated unprivileged runtime, sufficient CPU/memory/storage, accurate time, persistent database volumes, least-privilege network boundaries, HTTPS reverse proxy and off-host monitoring. Expose only the appropriate authenticated HTTPS boundary. Validate firewall, secure cookies, session/origin settings and health probes with the deployed domain.

Deploy separate demo/live environments and secrets. Verify graceful stop, host reboot, account lease ownership, stale-data blocking, and eventual exchange reconciliation. Telegram/alternative alerts must actually reach an authorized operator and notification outage must not block recovery. A local Compose smoke run does not meet VPS acceptance.

## Observability and release evidence

Retain structured logs with strategy/signal/order/account identifiers and secret redaction. Record actual latency distributions and resource measurements before performance claims. Metrics scraping alone does not prove alert delivery. Review resolved dependency scans, database migrations, backup/restore result and rollback procedure before deploying a new release.

All incident notes state environment, UTC event time, evidence, actions, remaining exposure and who authorized resume. Record operator-facing dates in the operator's chosen timezone without altering stored UTC timestamps.
