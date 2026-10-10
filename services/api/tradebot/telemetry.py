"""Redacted JSON logs, bounded trace IDs and honest operational metrics."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import time
import uuid

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradebot.data_models import MarketDataGap, TelemetryState
from tradebot.db import Base, aware, get_db, session_factory, utcnow
from tradebot.market_stream import feed_rows
from tradebot.models import Order, PaperAccount, Position, WorkerHeartbeat
from tradebot.security import identity

router = APIRouter(prefix="/api/v1", tags=["monitoring"])

trace_id: ContextVar[str | None] = ContextVar("tradebot_trace_id", default=None)
_TRACE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
_SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|authorization|cookie|signature|api[-_]?key|credentials|csrf)", re.I)
_KEY_VALUE = re.compile(r"(?i)([\"']?[\w-]*(?:password|passwd|secret|token|authorization|cookie|signature|api[-_]?key|credentials|csrf)[\w-]*[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)")
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9_.~+/=-]+")
_URL_CREDENTIALS = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/@]+:[^\s/@]+@")
_EXTRA_FIELDS = ("event", "component", "environment", "strategy_id", "strategy_version", "signal_id",
                 "order_id", "client_order_id", "account_id", "position_id", "channel", "symbol",
                 "method", "route", "status", "latency_seconds", "error_type", "details", "trace_id")

REQUESTS = Counter("tradebot_http_requests_total", "Completed HTTP requests", ["method", "route", "status"])
REQUEST_SECONDS = Histogram("tradebot_http_request_seconds", "HTTP request elapsed seconds", ["method", "route"],
                            buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30))
ORDER_ACK = Histogram("tradebot_order_ack_seconds", "Measured exchange submission to acknowledgment seconds",
                      ["environment"], buckets=(.01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30))


def redact(value):
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        # Remove the entire auth token before generic key/value matching can
        # consume only the word Bearer and leave its following token behind.
        value = _BEARER.sub(lambda match: match.group(1)+" [REDACTED]", value)
        value = _KEY_VALUE.sub(lambda match: match.group(1)+"[REDACTED]", value)
        return _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact(str(value))


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body = {"time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(), "level": record.levelname,
                "logger": record.name, "message": redact(record.getMessage())}
        for key in _EXTRA_FIELDS:
            if hasattr(record, key):
                body[key] = redact(getattr(record, key))
        if "trace_id" not in body and trace_id.get():
            body["trace_id"] = trace_id.get()
        if record.exc_info:
            # Exception text/traceback can contain credentials and transport
            # request bodies. A class name is sufficient for the public log.
            body["error_type"] = record.exc_info[0].__name__
        return json.dumps(body, separators=(",", ":"), ensure_ascii=True)


def configure_logging(component: str):
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # Dependency request logs are verbose and can include sensitive URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("tradebot.lifecycle").info("Process starting", extra={"event": "process_start", "component": component})


def record_process_start(component: str, db: Session | None = None):
    if db is None:
        with session_factory()() as current:
            record_process_start(component, current)
            current.commit()
        return
    key = "process-starts:"+component
    row = db.get(TelemetryState, key)
    if row is None:
        row = TelemetryState(key=key, payload={"count": 0})
        db.add(row)
    row.payload = {"count": int(row.payload.get("count", 0))+1, "last_started_at": utcnow().isoformat()}
    row.updated_at = utcnow()


def record_backup(success: bool, size_bytes: int = 0, *, db: Session | None = None):
    if type(success) is not bool or size_bytes < 0:
        raise ValueError("Backup telemetry requires an actual result and nonnegative byte count")
    if db is None:
        with session_factory()() as current:
            record_backup(success, size_bytes, db=current)
            current.commit()
        return
    row = db.get(TelemetryState, "backup")
    if row is None:
        row = TelemetryState(key="backup", payload={})
        db.add(row)
    previous = row.payload
    row.payload = {"last_attempt_at": utcnow().isoformat(), "last_attempt_success": success,
                   "last_success_at": utcnow().isoformat() if success else previous.get("last_success_at"),
                   "last_success_bytes": size_bytes if success else previous.get("last_success_bytes", 0)}
    row.updated_at = utcnow()


def record_offsite_backup(success: bool, size_bytes: int = 0, *, db: Session | None = None):
    if type(success) is not bool or size_bytes < 0:
        raise ValueError("Offsite backup telemetry requires an actual result and nonnegative byte count")
    if db is None:
        with session_factory()() as current:
            record_offsite_backup(success, size_bytes, db=current)
            current.commit()
        return
    row = db.get(TelemetryState, "backup-offsite")
    if row is None:
        row = TelemetryState(key="backup-offsite", payload={})
        db.add(row)
    row.payload = {"last_attempt_at": utcnow().isoformat(), "last_attempt_success": success,
                   "last_success_at": utcnow().isoformat() if success else row.payload.get("last_success_at"),
                   "last_success_bytes": size_bytes if success else row.payload.get("last_success_bytes", 0),
                   "scope": "encrypted SFTP delivery acknowledged; restoration is separate evidence"}
    row.updated_at = utcnow()


def record_reconciliation(environment: str, success: bool, *, db: Session):
    if environment not in ("PAPER", "DEMO", "LIVE") or type(success) is not bool:
        raise ValueError("Reconciliation telemetry requires a named environment and actual result")
    key = "reconciliation:"+environment
    row = db.get(TelemetryState, key)
    if row is None:
        row = TelemetryState(key=key, payload={})
        db.add(row)
    row.payload = {"last_attempt_at": utcnow().isoformat(), "last_attempt_success": success,
                   "last_success_at": utcnow().isoformat() if success else row.payload.get("last_success_at")}
    row.updated_at = utcnow()


def record_order_ack(environment: str, seconds: float, *, db: Session | None = None):
    if environment not in ("DEMO", "LIVE") or seconds < 0:
        raise ValueError("Acknowledgment metrics require a named exchange environment and nonnegative latency")
    ORDER_ACK.labels(environment=environment).observe(seconds)
    if db is None:
        with session_factory()() as current:
            _persist_ack(current, environment, seconds)
            current.commit()
    else:
        _persist_ack(db, environment, seconds)


def _persist_ack(db: Session, environment: str, seconds: float):
    row = db.scalar(select(TelemetryState).where(TelemetryState.key == "order-ack:"+environment).with_for_update())
    if row is None:
        row = TelemetryState(key="order-ack:"+environment, payload={})
        db.add(row)
    previous = row.payload
    row.payload = {"count": previous.get("count", 0)+1, "total_seconds": previous.get("total_seconds", 0)+seconds,
                   "maximum_seconds": max(previous.get("maximum_seconds", 0), seconds), "last_seconds": seconds,
                   "last_observed_at": utcnow().isoformat()}
    row.updated_at = utcnow()


def install_api_telemetry(app: FastAPI):
    if getattr(app.state, "tradebot_telemetry_installed", False):
        return
    app.state.tradebot_telemetry_installed = True

    @app.middleware("http")
    async def measure(request, call_next):
        requested = request.headers.get("x-request-id", "")
        identifier = requested if _TRACE.fullmatch(requested) else uuid.uuid4().hex
        token = trace_id.set(identifier)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = identifier
            return response
        finally:
            elapsed = time.perf_counter()-started
            route = getattr(request.scope.get("route"), "path", "unmatched")
            # Route templates avoid URL cardinality and never contain query
            # strings, credentials, passwords or uploaded filenames.
            method = request.method if request.method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS") else "OTHER"
            REQUESTS.labels(method, route, str(status)).inc()
            REQUEST_SECONDS.labels(method, route).observe(elapsed)
            logging.getLogger("tradebot.http").info("HTTP request completed", extra={"event": "http_request",
                        "method": method, "route": route, "status": status, "latency_seconds": round(elapsed, 6)})
            trace_id.reset(token)


def _local_backup_metrics():
    """A process without a recorded success must report unknown, never healthy."""
    status_file = Path(".runtime/backups/status.json")
    if status_file.is_file():
        try:
            return json.loads(status_file.read_text())
        except (OSError, ValueError):
            return {}
    return {}


def _age(stamp):
    return max(0, (utcnow()-aware(datetime.fromisoformat(stamp))).total_seconds()) if stamp else None


@router.get("/monitoring")
def monitoring(db: Session = Depends(get_db), principal=Depends(identity)):
    from tradebot.clock import clock_status
    values = {row.key: row.payload for row in db.scalars(select(TelemetryState))}
    backup = values.get("backup") or _local_backup_metrics()
    offsite = values.get("backup-offsite") or {}
    reconciliations = {key.split(":", 1)[1]: dict(payload, age_seconds=_age(payload.get("last_success_at")))
                       for key, payload in values.items() if key.startswith("reconciliation:")}
    try:
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        memory_bytes = resident_pages*os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        memory_bytes = None
    return {"observed_at": utcnow().isoformat(), "scope": "API process and persisted worker observations",
            "backup": dict(backup, age_seconds=_age(backup.get("last_success_at"))),
            "offsite_backup": dict(offsite, age_seconds=_age(offsite.get("last_success_at"))),
            "reconciliation": reconciliations,
            "process_starts": {key.split(":", 1)[1]: payload for key, payload in values.items() if key.startswith("process-starts:")},
            "order_acknowledgments": {key.split(":", 1)[1]: payload for key, payload in values.items() if key.startswith("order-ack:")},
            "resource": {"api_resident_memory_bytes": memory_bytes, "host_load_1m": os.getloadavg()[0],
                         "cpu_seconds": os.times().user+os.times().system},
            "clock": clock_status(db),
            "public_feeds": feed_rows(db), "live_enabled": False}


def metrics_response(db: Session) -> Response:
    registry = CollectorRegistry()

    def metric(name: str, documentation: str, value: float, labels=None):
        labels = labels or {}
        gauge = Gauge(name, documentation, list(labels), registry=registry)
        gauge.labels(**labels).set(value) if labels else gauge.set(value)
        return gauge

    metric("tradebot_live_enabled", "Production execution is enabled", 0)
    try:
        db.execute(select(1))
        metric("tradebot_database_available", "Database can be queried", 1)
        from tradebot.clock import clock_status
        clock = clock_status(db)
        metric("tradebot_clock_healthy", "Fresh bounded UTC evidence exists without an unresolved unsafe observation", int(clock["healthy"]))
        metric("tradebot_clock_evidence_age_seconds", "Age of last usable public HTTPS UTC observation; -1 means none",
               clock["age_seconds"] if clock["age_seconds"] is not None else -1)
        metric("tradebot_clock_offset_bound_seconds", "Absolute measured UTC offset plus RTT/Date uncertainty; -1 means none",
               clock.get("sample", {}).get("absolute_offset_bound_seconds", -1) if clock.get("sample") else -1)
        metric("tradebot_paper_open_positions", "Open managed paper positions",
               db.scalar(select(func.count()).select_from(Position).where(Position.status == "OPEN")) or 0)
        unknown = db.scalar(select(func.count()).select_from(Order).where(Order.status == "UNKNOWN")) or 0
        demo_orders = Base.metadata.tables.get("demo_orders")
        if demo_orders is not None:
            unknown += db.scalar(select(func.count()).select_from(demo_orders).where(demo_orders.c.status == "UNKNOWN")) or 0
        metric("tradebot_unknown_orders", "PAPER or DEMO orders that require authoritative resolution", unknown)
        from tradebot.notifications_models import TelegramOutbox
        metric("tradebot_notification_queue_backlog", "Telegram deliveries pending or in progress",
               db.scalar(select(func.count()).select_from(TelegramOutbox).where(TelegramOutbox.status.in_(("PENDING", "SENDING")))) or 0)
        metric("tradebot_notification_queue_failed", "Telegram deliveries that exhausted retries",
               db.scalar(select(func.count()).select_from(TelegramOutbox).where(TelegramOutbox.status == "FAILED")) or 0)
        metric("tradebot_unresolved_market_gaps", "Historical missing-data ranges requiring real observations",
               db.scalar(select(func.count()).select_from(MarketDataGap).where(MarketDataGap.status == "OPEN")) or 0)
        account = db.get(PaperAccount, 1)
        metric("tradebot_paper_reconciliation_required", "Account reconciliation required or account unavailable",
               1 if account is None or account.reconciliation_required else 0)
        demo_accounts = Base.metadata.tables.get("demo_accounts")
        if demo_accounts is not None:
            demo = db.execute(select(demo_accounts).where(demo_accounts.c.id == 1)).mappings().first()
            metric("tradebot_demo_reconciliation_required", "DEMO account needs authoritative reconciliation or is unavailable",
                   int(demo is None or demo["reconciliation_required"]))
            metric("tradebot_demo_worker_healthy", "Fresh connected DEMO worker and private stream observations",
                   int(demo is not None and demo["connected"] and demo["worker_status"] == "RUNNING"
                       and demo["stream_status"] == "CONNECTED" and demo["worker_updated_at"] is not None
                       and (utcnow()-aware(demo["worker_updated_at"])).total_seconds() <= 90
                       and not demo["reconciliation_required"]))
        # Notification/report worker health does not establish trading-worker
        # availability. Match the API's paper worker identity boundary.
        heartbeats = list(db.scalars(select(WorkerHeartbeat).where(~WorkerHeartbeat.id.like("aux:%"))))
        active = [row for row in heartbeats if (utcnow()-aware(row.updated_at)).total_seconds() <= 90]
        metric("tradebot_worker_healthy", "At least one fresh RUNNING paper worker exists", int(any(row.status == "RUNNING" for row in active)))
        running = [aware(row.updated_at) for row in heartbeats if row.status == "RUNNING"]
        metric("tradebot_worker_last_success_age_seconds", "Age of last successful worker heartbeat; -1 means unavailable",
               max(0, (utcnow()-max(running)).total_seconds()) if running else -1)
        auxiliary = list(db.scalars(select(WorkerHeartbeat).where(WorkerHeartbeat.id.like("aux:%"))))
        metric("tradebot_auxiliary_worker_healthy", "At least one fresh RUNNING reports/notifications worker exists",
               int(any(row.status == "RUNNING" and (utcnow()-aware(row.updated_at)).total_seconds() <= 90 for row in auxiliary)))
        feed_age = Gauge("tradebot_market_data_age_seconds", "Maximum event/receive age; -1 means unavailable",
                         ["symbol", "channel"], registry=registry)
        feed_live = Gauge("tradebot_exchange_feed_live", "Fresh continuous public WebSocket feed",
                          ["symbol", "channel"], registry=registry)
        feed_gaps = Gauge("tradebot_exchange_feed_gaps", "Durable stream discontinuity observations",
                          ["symbol", "channel"], registry=registry)
        for row in feed_rows(db):
            labels = {"symbol": row["symbol"], "channel": row["channel"]}
            feed_age.labels(**labels).set(row["age_seconds"] if row["age_seconds"] is not None else -1)
            feed_live.labels(**labels).set(row["status"] == "LIVE")
            feed_gaps.labels(**labels).set(row["gap_count"])
        starts = Gauge("tradebot_process_starts", "Persisted successful startup observations", ["component"], registry=registry)
        for row in db.scalars(select(TelemetryState).where(TelemetryState.key.like("process-starts:%"))):
            starts.labels(component=row.key.split(":", 1)[1]).set(row.payload.get("count", 0))
        ack_count = Gauge("tradebot_exchange_order_ack_count", "Persisted actual acknowledgment observations",
                          ["environment"], registry=registry)
        ack_sum = Gauge("tradebot_exchange_order_ack_seconds_sum", "Persisted actual acknowledgment elapsed seconds",
                        ["environment"], registry=registry)
        ack_max = Gauge("tradebot_exchange_order_ack_seconds_max", "Largest persisted acknowledgment elapsed seconds",
                        ["environment"], registry=registry)
        for row in db.scalars(select(TelemetryState).where(TelemetryState.key.like("order-ack:%"))):
            environment = row.key.split(":", 1)[1]
            ack_count.labels(environment).set(row.payload.get("count", 0))
            ack_sum.labels(environment).set(row.payload.get("total_seconds", 0))
            ack_max.labels(environment).set(row.payload.get("maximum_seconds", 0))
        reconciliation_age = Gauge("tradebot_reconciliation_age_seconds", "Age of last successful actual reconciliation; absent means never observed",
                                   ["environment"], registry=registry)
        reconciliation_success = Gauge("tradebot_reconciliation_last_attempt_success", "Last actual reconciliation succeeded",
                                       ["environment"], registry=registry)
        for row in db.scalars(select(TelemetryState).where(TelemetryState.key.like("reconciliation:%"))):
            environment = row.key.split(":", 1)[1]
            age = _age(row.payload.get("last_success_at"))
            reconciliation_age.labels(environment).set(age if age is not None else -1)
            reconciliation_success.labels(environment).set(bool(row.payload.get("last_attempt_success")))
        backup = db.get(TelemetryState, "backup")
        state = backup.payload if backup else _local_backup_metrics()
        last_success = state.get("last_success_at")
        metric("tradebot_backup_last_attempt_success", "Last backup succeeded; -1 means no recorded attempt",
               int(state["last_attempt_success"]) if "last_attempt_success" in state else -1)
        metric("tradebot_backup_last_success_age_seconds", "Age of last observed backup success; -1 means none",
               max(0, (utcnow()-aware(datetime.fromisoformat(last_success))).total_seconds()) if last_success else -1)
        metric("tradebot_backup_last_success_bytes", "Bytes in last successful local backup", state.get("last_success_bytes", 0))
        offsite = db.get(TelemetryState, "backup-offsite")
        offsite_state = offsite.payload if offsite else {}
        offsite_age = _age(offsite_state.get("last_success_at"))
        metric("tradebot_offsite_backup_last_success_age_seconds", "Age of last acknowledged encrypted offsite delivery; -1 means none",
               offsite_age if offsite_age is not None else -1)
        metric("tradebot_offsite_backup_last_attempt_success", "Last encrypted offsite delivery succeeded; -1 means no observed attempt",
               int(offsite_state["last_attempt_success"]) if "last_attempt_success" in offsite_state else -1)
    except Exception:
        # Return only explicitly unknown/unavailable database state on a
        # query/migration failure, never retain partly collected health values.
        registry = CollectorRegistry()
        metric("tradebot_live_enabled", "Production execution is enabled", 0)
        metric("tradebot_database_available", "Database can be queried", 0)
    body = generate_latest()+generate_latest(registry)
    return Response(body, headers={"Content-Type": CONTENT_TYPE_LATEST})
