"""DEMO execution coordinator. Every exchange write is owned by the demo worker.

An uncertain POST is never repeated, including after restart or a 404 lookup.
Exchange account snapshots and fills remain authoritative; unexplained exposure
blocks new entries and is visible instead of being silently adopted.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradebot.db import aware, utcnow
from tradebot.demo_models import DemoAccount, DemoCashMovement, DemoEquitySnapshot, DemoFill, DemoOrder, DemoOrderEvent, DemoPosition, DemoProduct, DemoProtection, DemoStrategy
from tradebot.models import Approval, Audit, Notification, Strategy, StrategyVersion
from tradebot.telemetry import record_order_ack, record_reconciliation
from tradebot_core import Instrument, PaperFillModel, Side, StrategyConfig
from tradebot_core.risk import PositionSize, RiskContext, RiskLimits, evaluate_risk
from tradebot_delta import DemoClient, DemoCredentials
from tradebot_delta.errors import DeltaAPIError, OrderSubmissionUnknown

D = Decimal
ACTIVE = frozenset({"RISK_PENDING", "RISK_APPROVED", "SUBMITTING", "UNKNOWN", "OPEN", "PARTIALLY_FILLED", "ACKNOWLEDGED", "CANCEL_PENDING"})
TERMINAL = frozenset({"FILLED", "CANCELED", "REJECTED"})
FEE_RATE = D("0.001")  # Conservative cost reservation, not claimed as exchange fee tier.
SLIPPAGE_BPS = D("5")


class DemoRejected(ValueError):
    pass


def execution_authorized() -> bool:
    return os.getenv("DEMO_EXECUTION_AUTHORIZED", "false").lower() in ("true", "1", "yes")


def credentials() -> DemoCredentials:
    key, secret = os.getenv("DELTA_DEMO_API_KEY"), os.getenv("DELTA_DEMO_API_SECRET")
    if not key or not secret:
        try:
            from tradebot.vault import VaultError, vault_credentials
            stored = vault_credentials("delta-demo") or {}
            key, secret = stored.get("api_key"), stored.get("api_secret")
        except ImportError:
            key, secret = None, None
        except (VaultError, ValueError, OSError):
            key, secret = None, None
    if not key or not secret:
        raise DemoRejected("Configure demo credentials through secure environment variables or the encrypted vault CLI")
    return DemoCredentials(key, secret)


def credential_configured() -> bool:
    try:
        credentials()
        return True
    except (DemoRejected, ValueError):
        return False


def fingerprint() -> str:
    return hashlib.sha256(credentials().api_key.encode()).hexdigest()


def client(*, writes=False) -> DemoClient:
    if writes and not execution_authorized():
        raise DemoRejected("Local DEMO_EXECUTION_AUTHORIZED must explicitly authorize demo writes")
    return DemoClient(credentials(), allow_demo_orders=writes)


def lock_account(db: Session) -> DemoAccount:
    account = db.scalar(select(DemoAccount).where(DemoAccount.id == 1).with_for_update().execution_options(populate_existing=True))
    if account is None or not account.connected:
        raise DemoRejected("Connect and reconcile the demo account first")
    if account.credential_fingerprint != fingerprint():
        raise DemoRejected("Configured credentials differ from the connected demo account; explicit account review required")
    return account


def event(db: Session, order: DemoOrder, kind: str, details: dict[str, Any]) -> None:
    encoded = json.dumps(details, sort_keys=True, separators=(",", ":"), default=str)
    key = hashlib.sha256(f"{order.id}|{kind}|{encoded}".encode()).hexdigest()
    if not db.scalar(select(DemoOrderEvent).where(DemoOrderEvent.event_key == key)):
        db.add(DemoOrderEvent(order_id=order.id, event_key=key, event_type=kind, details=json.loads(encoded)))


def number(value: Any, *, default: str | None = None) -> Decimal:
    try:
        result = D(str(default if value is None and default is not None else value))
    except (InvalidOperation, ValueError):
        raise DemoRejected("Exchange snapshot contains invalid monetary data") from None
    if not result.is_finite():
        raise DemoRejected("Exchange snapshot contains non-finite monetary data")
    return result


def cash_ledger(db: Session, account: DemoAccount, wallet: dict[str, Any], transactions: list[dict[str, Any]]) -> list[str]:
    """Reconcile signed asset-native amounts without inferring funding formulae.

    Official transaction.asset_symbol has conflicting example/schema types;
    the verified wallet's numeric asset_id binds denomination authoritatively.
    A baseline anchors the last observed transaction; future rows must conserve
    running balances and match the wallet snapshot, or exposure stays blocked.
    """
    asset_id = wallet.get("asset_id")
    if type(asset_id) is not int or asset_id <= 0:
        raise DemoRejected("Wallet asset identity is missing; cash ledger denomination cannot be verified")
    rows = []
    for value in transactions:
        if type(value.get("id")) is not int or value["id"] <= 0 or type(value.get("asset_id")) is not int:
            raise DemoRejected("Wallet transaction has invalid immutable identity")
        if value["asset_id"] != asset_id:
            continue
        try:
            timestamp = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError
            timestamp = timestamp.astimezone(timezone.utc)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise DemoRejected("Wallet transaction lacks an authoritative timezone-aware timestamp") from None
        symbol = value.get("asset_symbol")
        if isinstance(symbol, str) and symbol != account.settlement_asset:
            raise DemoRejected("Wallet transaction asset identity and currency disagree")
        amount, balance = number(value.get("amount")), number(value.get("balance"))
        kind = value.get("transaction_type")
        if not isinstance(kind, str) or not kind or len(kind) > 80:
            raise DemoRejected("Wallet transaction type is missing")
        row = db.get(DemoCashMovement, str(value["id"]))
        identity = (asset_id, amount, balance, kind, timestamp)
        if row:
            if (row.asset_id, row.amount, row.balance, row.transaction_type, aware(row.exchange_created_at)) != identity:
                raise DemoRejected("Wallet transaction identity changed monetary content")
        else:
            row = DemoCashMovement(id=str(value["id"]), asset_id=asset_id, asset_symbol=account.settlement_asset,
                                   amount=amount, balance=balance, transaction_type=kind, product_id=value.get("product_id"),
                                   exchange_created_at=timestamp, details={"source": "Delta India DEMO signed /v2/wallet/transactions",
                                   "denomination_source": "wallet.asset_id matched to transaction.asset_id"})
            db.add(row)
        rows.append(row)
    rows.sort(key=lambda row: (aware(row.exchange_created_at), int(row.id)))
    baseline = db.get(DemoCashMovement, "baseline:"+account.credential_fingerprint[:32])
    if baseline is None:
        anchor = rows[-1] if rows else None
        baseline = DemoCashMovement(id="baseline:"+account.credential_fingerprint[:32], asset_id=asset_id,
                    asset_symbol=account.settlement_asset, amount=D(0), balance=anchor.balance if anchor else number(wallet["balance"]),
                    transaction_type="LOCAL_OBSERVATION_BASELINE", exchange_created_at=aware(anchor.exchange_created_at) if anchor else utcnow(),
                    details={"source": "Explicit initial DEMO connection, no guessed cashflow", "anchor_transaction_id": int(anchor.id) if anchor else 0})
        db.add(baseline)
    if baseline.asset_id != asset_id or baseline.asset_symbol != account.settlement_asset:
        raise DemoRejected("Demo settlement wallet identity changed")
    anchor_key = (aware(baseline.exchange_created_at), baseline.details["anchor_transaction_id"])
    expected, issues = baseline.balance, []
    # Include persisted rows, not merely the latest response, so a missing page
    # cannot remove an already observed economic movement from the ledger.
    db.flush()
    durable = list(db.scalars(select(DemoCashMovement).where(DemoCashMovement.asset_id == asset_id, DemoCashMovement.transaction_type != "LOCAL_OBSERVATION_BASELINE")))
    durable.sort(key=lambda row: (aware(row.exchange_created_at), int(row.id)))
    for row in durable:
        if (aware(row.exchange_created_at), int(row.id)) <= anchor_key:
            continue
        expected += row.amount
        if abs(expected-row.balance) > D("0.00000001"):
            issues.append("Demo wallet transaction running balance mismatch; history may be incomplete or delayed")
    if abs(expected-number(wallet["balance"])) > D("0.00000001"):
        issues.append("Demo wallet cash differs from durable asset-native cashflow ledger; authoritative snapshot may be delayed")
    return issues


def core_contract(product: DemoProduct) -> Instrument:
    if not product.verified_contract or not product.verified_at:
        raise DemoRejected("Verify the actual demo minimum, increments and margin tier with documented evidence first")
    result = Instrument.model_validate(product.verified_contract)
    if result.verification_scope != "EXCHANGE" or not result.metadata_verified:
        raise DemoRejected("Paper assumptions cannot authorize a demo order")
    if (utcnow() - aware(product.observed_at)).total_seconds() > 3600:
        raise DemoRejected("Refresh demo contract discovery; exchange metadata is stale")
    if (utcnow() - aware(product.verified_at)).total_seconds() > 86400:
        raise DemoRejected("Demo minimum/margin evidence requires daily operator review")
    meta = product.metadata_json
    if not meta.get("linear_economics_verified") or meta.get("state") != "live" or meta.get("trading_status") != "operational":
        raise DemoRejected("Demo product is not verified linear and operational")
    return result.model_copy(update={"metadata_timestamp": aware(product.observed_at)})


async def discover(db: Session, adapter: DemoClient) -> list[DemoProduct]:
    values = await adapter.products()
    rows = []
    for value in values:
        row = db.get(DemoProduct, value["id"])
        if row is None:
            row = DemoProduct(id=value["id"], symbol=value["symbol"], metadata_json=value)
            db.add(row)
        elif row.symbol != value["symbol"]:
            raise DemoRejected("Demo exchange product identity changed")
        previous = row.metadata_json
        important = ("contract_value", "tick_size", "quote_asset", "settlement_asset", "underlying_asset", "state", "trading_status")
        if row.verified_contract and any(previous.get(k) != value.get(k) for k in important):
            row.verified_contract, row.evidence, row.verified_at = None, None, None
        row.metadata_json, row.observed_at = value, utcnow()
        rows.append(row)
    return rows


def approve_contract(db: Session, product: DemoProduct, values: dict[str, Any], actor: str) -> Instrument:
    meta = product.metadata_json
    if not meta.get("linear_economics_verified"):
        raise DemoRejected("Actual public HTTPS metadata must verify linear contract economics")
    minimum, increment, margin = number(values["min_quantity"]), number(values["quantity_step"]), number(values["initial_margin_rate"])
    if minimum != minimum.to_integral_value() or increment != increment.to_integral_value():
        raise DemoRejected("Delta derivative order size is integer contracts")
    maximum = number(values.get("max_quantity"))
    if maximum != maximum.to_integral_value():
        raise DemoRejected("Certify an integer maximum quantity covered by the reviewed margin tier")
    evidence = str(values["evidence"]).strip()
    if len(evidence) < 20 or len(evidence) > 4000:
        raise DemoRejected("Provide 20-4000 characters describing the official source and applicable minimum/margin tier")
    result = Instrument(product_id=product.id, symbol=product.symbol, underlying_asset=meta["underlying_asset"],
                        quote_asset=meta["quote_asset"], settlement_asset=meta["settlement_asset"],
                        contract_size=meta["contract_value"], tick_size=meta["tick_size"],
                        quantity_step=increment, min_quantity=minimum, max_quantity=maximum,
                        initial_margin_rate=margin, metadata_verified=True, metadata_timestamp=aware(product.observed_at))
    product.verified_contract, product.evidence, product.approved_by, product.verified_at = result.model_dump(mode="json"), evidence, actor, utcnow()
    db.add(Audit(actor=actor, action="demo.contract.verify", entity=str(product.id), details={"contract": result.model_dump(mode="json"), "evidence": evidence}))
    return result


def queue_order(db: Session, values: dict[str, Any], actor: str) -> DemoOrder:
    account = lock_account(db)
    old = db.scalar(select(DemoOrder).where(DemoOrder.idempotency_key == values["idempotency_key"]))
    if old:
        check = (old.symbol, old.side, old.quantity, old.order_type, old.limit_price, old.stop_price, old.target_price, old.reduce_only)
        request = (values["symbol"], values["side"], D(values["quantity"]), values["order_type"], values.get("limit_price"), values.get("stop_price"), values.get("target_price"), bool(values.get("reduce_only", False)))
        if check != request:
            raise DemoRejected("Idempotency key is already bound to a different order")
        return old
    if not execution_authorized():
        raise DemoRejected("Demo execution has not been authorized locally")
    reduce_only = bool(values.get("reduce_only", False))
    if not reduce_only and (not account.entries_enabled or account.emergency_stop or account.reconciliation_required):
        raise DemoRejected("Demo entries blocked; enable a reconciled account first")
    product = db.scalar(select(DemoProduct).where(DemoProduct.symbol == values["symbol"]))
    if product is None:
        raise DemoRejected("Discover the demo product first")
    instrument = core_contract(product)
    instrument.validate_quantity(D(values["quantity"]))
    if D(values["quantity"]) != D(values["quantity"]).to_integral_value():
        raise DemoRejected("Demo quantity must be whole contracts")
    for name in ("limit_price", "stop_price", "target_price"):
        price = values.get(name)
        if price is not None and (number(price) <= 0 or number(price) % instrument.tick_size):
            raise DemoRejected(f"Demo {name} must align to the verified exchange tick")
    if instrument.settlement_asset != account.settlement_asset:
        raise DemoRejected("Demo product settlement differs from the configured account asset")
    if reduce_only:
        position = db.get(DemoPosition, product.id)
        side = Side(values["side"])
        if not position or not position.managed or position.signed_quantity * side.sign >= 0 or D(values["quantity"]) > abs(position.signed_quantity):
            raise DemoRejected("Reduce-only action must reduce an identified managed demo position")
    elif db.scalar(select(DemoOrder).where(DemoOrder.product_id == product.id, DemoOrder.status.in_(ACTIVE))):
        raise DemoRejected("Demo product already has a pending managed order")
    identifier = hashlib.sha256(f"DEMO|{account.credential_fingerprint}|{values['idempotency_key']}".encode()).hexdigest()[:32]
    order = DemoOrder(id=identifier, idempotency_key=values["idempotency_key"], client_order_id="d_" + identifier[:30],
                      product_id=product.id, symbol=product.symbol, side=values["side"], quantity=D(values["quantity"]),
                      order_type=values["order_type"], limit_price=values.get("limit_price"), stop_price=values.get("stop_price"),
                      target_price=values.get("target_price"), reduce_only=reduce_only, strategy_id=values.get("strategy_id"),
                      strategy_version=values.get("strategy_version"), instrument_snapshot=instrument.model_dump(mode="json"))
    db.add(order)
    db.flush()
    event(db, order, "INTENT_CREATED", {"actor": actor, "mode": "DEMO", "requested_quantity": str(order.quantity), "reduce_only": reduce_only})
    db.add(Audit(actor=actor, action="demo.order.queue", entity=order.id, details={"mode": "DEMO", "symbol": order.symbol, "quantity": str(order.quantity)}))
    return order


def apply_order(db: Session, order: DemoOrder, value: dict[str, Any]) -> None:
    expected = (order.product_id, order.client_order_id, order.side.lower(), int(order.quantity))
    actual = (value.get("product_id"), value.get("client_order_id"), value.get("side"), value.get("size"))
    if actual != expected or type(value.get("id")) is not int:
        raise DemoRejected("Exchange order identity differs from its durable intent")
    unfilled = number(value.get("unfilled_size"))
    if unfilled < 0 or unfilled > order.quantity or unfilled != unfilled.to_integral_value():
        raise DemoRejected("Invalid exchange remaining quantity")
    filled = order.quantity - unfilled
    if filled < order.filled_quantity:
        raise DemoRejected("Exchange fill quantity regressed; reconciliation incomplete")
    state = value.get("state")
    mapped = "FILLED" if state == "closed" and unfilled == 0 else "CANCELED" if state == "cancelled" else "PARTIALLY_FILLED" if filled > 0 and state in ("open", "pending") else "OPEN" if state in ("open", "pending") else "UNKNOWN"
    if order.status in TERMINAL and mapped != order.status:
        raise DemoRejected("Exchange terminal order state changed")
    order.exchange_order_id, order.filled_quantity, order.status = value["id"], filled, mapped
    if filled:
        order.average_fill_price = number(value.get("average_fill_price"))
        if order.average_fill_price <= 0:
            raise DemoRejected("Filled exchange order lacks a valid average price")
    if mapped in TERMINAL:
        order.reserved_risk, order.reserved_margin = D(0), D(0)
    order.updated_at = utcnow()
    event(db, order, "EXCHANGE_STATE", {"exchange_order_id": order.exchange_order_id, "state": mapped, "filled_quantity": str(filled), "average_fill_price": str(order.average_fill_price) if order.average_fill_price else None})


def _period_equity(db: Session, account: DemoAccount, boundary: datetime) -> Decimal:
    row = db.scalar(select(DemoEquitySnapshot).where(DemoEquitySnapshot.created_at <= boundary).order_by(DemoEquitySnapshot.created_at.desc()).limit(1))
    if row:
        return row.equity
    if aware(account.created_at) >= boundary:
        return account.initial_equity
    raise DemoRejected("Missing durable demo period-start equity; review account history")


async def risk_approval(db: Session, account: DemoAccount, order: DemoOrder, adapter: DemoClient) -> dict[str, Any]:
    from tradebot.demo_capabilities import require_limits, require_strategy
    config = None
    limits = RiskLimits.model_validate(account.risk_limits)
    require_limits(limits)
    if order.strategy_id:
        runner = db.scalar(select(DemoStrategy).where(DemoStrategy.strategy_id == order.strategy_id).execution_options(populate_existing=True))
        strategy = db.scalar(select(Strategy).where(Strategy.id == order.strategy_id).execution_options(populate_existing=True))
        approved = db.scalar(select(Approval).where(Approval.strategy_id == order.strategy_id, Approval.version == order.strategy_version, Approval.stage == "DEMO"))
        if not runner or runner.status != "RUNNING" or runner.version != order.strategy_version or not strategy or strategy.current_version != order.strategy_version or not approved:
            raise DemoRejected("Exact-version DEMO strategy approval was paused, changed or withdrawn before submission")
        saved = db.scalar(select(StrategyVersion).where(StrategyVersion.strategy_id == order.strategy_id, StrategyVersion.version == order.strategy_version))
        if not saved or not saved.validated:
            raise DemoRejected("Exact-version DEMO strategy configuration lacks validation before submission")
        config = StrategyConfig.model_validate(saved.config)
        require_strategy(config)
        if config.version != order.strategy_version or config.symbol != order.symbol:
            raise DemoRejected("Exact-version DEMO configuration does not match the queued strategy identity")
        limits = limits.model_copy(update={"max_risk_fraction": min(limits.max_risk_fraction, config.risk_fraction)})
        signal_event = db.scalar(select(DemoOrderEvent).where(DemoOrderEvent.order_id == order.id, DemoOrderEvent.event_type == "STRATEGY_SIZING"))
        if not signal_event or not isinstance(signal_event.details.get("data_snapshot_id"), str) or len(signal_event.details["data_snapshot_id"]) != 64:
            raise DemoRejected("Demo strategy intent lacks durable causal signal and sizing evidence")
        if order.idempotency_key != f"strategy:{order.strategy_id}:v{order.strategy_version}:{signal_event.details.get('signal_id')}":
            raise DemoRejected("Demo strategy intent identity does not match its approved causal signal")
        try:
            signal_at = datetime.fromisoformat(signal_event.details["signal_timestamp"])
            if signal_at.tzinfo is None:
                raise ValueError("missing timezone")
            signal_age = (utcnow()-signal_at).total_seconds()
        except (ValueError, TypeError, KeyError):
            raise DemoRejected("Demo strategy intent lacks an authoritative closed-bar signal timestamp") from None
        if not 0 <= signal_age <= limits.market_max_age_seconds:
            raise DemoRejected("Demo strategy signal is stale; require a new closed-bar signal")
    instrument = core_contract(db.get(DemoProduct, order.product_id))
    original = Instrument.model_validate(order.instrument_snapshot)
    if instrument.model_dump(exclude={"metadata_timestamp"}) != original.model_dump(exclude={"metadata_timestamp"}):
        raise DemoRejected("Demo contract approval changed after intent creation; review a new intent")
    ticker = await adapter.ticker(order.symbol)
    if ticker.get("product_id") != order.product_id or ticker.get("symbol") != order.symbol:
        raise DemoRejected("Demo ticker product identity mismatch")
    timestamp = ticker.get("timestamp")
    if type(timestamp) is not int:
        raise DemoRejected("Demo ticker lacks exchange timestamp")
    market_time = datetime.fromtimestamp(timestamp / 1_000_000, timezone.utc)
    quotes = ticker.get("quotes") or {}
    bid, ask = number(quotes.get("best_bid")), number(quotes.get("best_ask"))
    if bid <= 0 or ask < bid:
        raise DemoRejected("Demo order book quotes are invalid")
    reference = number(order.limit_price) if order.order_type == "LIMIT" else ask if order.side == "BUY" else bid
    side, quantity = Side(order.side), order.quantity
    model = PaperFillModel(fee_rate=FEE_RATE, slippage_bps=SLIPPAGE_BPS, spread_bps=D(0))
    entry = model.execution_price(instrument, side, reference)
    stop = number(order.stop_price)
    stop_fill = model.execution_price(instrument, side.opposite, stop)
    fees = instrument.fee(entry, quantity, FEE_RATE) + instrument.fee(stop_fill, quantity, FEE_RATE)
    leverage = min(D(2), limits.max_leverage, config.max_leverage if config else limits.max_leverage)
    size = PositionSize(quantity=quantity, entry_price=entry, stop_price=stop, notional=instrument.notional(entry, quantity),
                        margin=instrument.margin(entry, quantity, leverage), risk_amount=-instrument.pnl(entry, stop_fill, quantity, side) + fees, estimated_fees=fees)
    positions = list(db.scalars(select(DemoPosition).where(DemoPosition.signed_quantity != 0).execution_options(populate_existing=True)))
    # Unapproved queued signals own no exposure. The exclusive worker approves
    # sequentially and commits each reservation before considering the next.
    pending = list(db.scalars(select(DemoOrder).where(DemoOrder.status.in_(ACTIVE-{"RISK_PENDING"}), DemoOrder.id != order.id, DemoOrder.reduce_only.is_(False)).execution_options(populate_existing=True)))
    gross, net, position_risk = D(0), D(0), D(0)
    for position in positions:
        contract = core_contract(db.get(DemoProduct, position.product_id))
        exposure = contract.notional(position.mark_price, abs(position.signed_quantity))
        gross += exposure
        net += exposure if position.signed_quantity > 0 else -exposure
        # A fully occupied managed position owns one entry intent for that product.
        owned = db.scalar(select(DemoOrder).where(DemoOrder.product_id == position.product_id, DemoOrder.reduce_only.is_(False), DemoOrder.filled_quantity > 0).order_by(DemoOrder.created_at.desc()).limit(1))
        if not owned or owned.stop_price is None:
            raise DemoRejected("Open demo exposure lacks durable stop-risk ownership")
        position_side = Side.BUY if position.signed_quantity > 0 else Side.SELL
        adverse = model.execution_price(contract, position_side.opposite, owned.stop_price)
        position_risk += max(D(0), -contract.pnl(position.entry_price, adverse, abs(position.signed_quantity), position_side)) + contract.fee(adverse, abs(position.signed_quantity), FEE_RATE)
    for pending_order in pending:
        snapshot = Instrument.model_validate(pending_order.instrument_snapshot)
        price = pending_order.limit_price or (pending_order.risk_decision.get("entry_price"))
        if price is None:
            raise DemoRejected("Pending demo order lacks an exposure reservation")
        remaining = pending_order.quantity - pending_order.filled_quantity
        exposure = snapshot.notional(number(price), remaining)
        gross += exposure
        net += exposure * Side(pending_order.side).sign
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    day, week = _period_equity(db, account, today), _period_equity(db, account, today - timedelta(days=today.weekday()))
    now = utcnow()
    context = RiskContext(execution_environment="DEMO", equity=account.equity,
                          available_margin=max(D(0), account.available_margin-sum((p.reserved_margin for p in pending), D(0))),
                          margin_used=sum((p.margin for p in positions), D(0)), gross_exposure=gross, net_exposure=net,
                          reserved_risk=sum((p.reserved_risk for p in pending), D(0)), open_position_risk=position_risk,
                          daily_pnl=account.equity-day, weekly_pnl=account.equity-week, day_start_equity=day,
                          week_start_equity=week, peak_equity=account.peak_equity, open_positions=len(positions), pending_orders=len(pending),
                          now=now, market_timestamp=market_time, account_reconciled=not account.reconciliation_required and bool(account.last_reconciled_at) and (now-aware(account.last_reconciled_at)).total_seconds() < 30,
                          instrument_metadata_fresh=True, data_healthy=True, exchange_healthy=True,
                          worker_healthy=account.worker_status == "RUNNING" and account.stream_status == "CONNECTED",
                          clock_healthy=abs((now-market_time).total_seconds()) <= 30, database_healthy=True,
                          protective_orders_verified=all(p.managed and p.protection_verified for p in positions),
                          has_unknown_orders=any(p.status in ("UNKNOWN", "SUBMITTING") for p in pending),
                          emergency_stop=account.emergency_stop or not account.entries_enabled)
    spread_bps = (ask-bid)/((ask+bid)/2)*10000
    # Quotes already include the spread in the adverse reference. Zero additional
    # spread is used for economics, while the actual spread gate stays independent.
    if spread_bps > limits.max_spread_bps:
        raise DemoRejected("Demo bid/ask spread exceeds risk limit")
    decision = evaluate_risk(limits=limits, context=context, instrument=instrument, side=side, size=size,
                             target_price=number(order.target_price), leverage=leverage, spread_bps=D(0),
                             slippage_bps=SLIPPAGE_BPS, fee_rate=FEE_RATE)
    result = {**decision.model_dump(mode="json"), "entry_price": str(entry), "spread_bps": str(spread_bps),
              "fee_reservation_rate": str(FEE_RATE), "instrument": instrument.model_dump(mode="json")}
    if any(position.product_id == order.product_id for position in positions):
        result["approved"], result["approved_quantity"] = False, "0"
        result["reasons"].append("SYMBOL_ALREADY_OWNED")
    return result


async def execute_intent(db: Session, account: DemoAccount, order: DemoOrder, adapter: DemoClient) -> None:
    """Persist SUBMITTING before IO. Restart handles it as UNKNOWN, never retry."""
    account = lock_account(db)
    db.refresh(order)
    if order.status not in ("RISK_PENDING", "RISK_APPROVED"):
        return
    if not execution_authorized():
        raise DemoRejected("Demo write authorization was withdrawn")
    if order.reduce_only:
        position = db.get(DemoPosition, order.product_id)
        if not position or not position.managed or position.signed_quantity*Side(order.side).sign >= 0 or order.quantity > abs(position.signed_quantity):
            raise DemoRejected("Managed demo position changed before reduce-only submission")
    else:
        decision = await risk_approval(db, account, order, adapter)
        order.risk_decision = decision
        event(db, order, "RISK_DECISION", decision)
        if not decision["approved"]:
            order.status, order.last_error = "REJECTED", ", ".join(decision["reasons"])
            if set(decision["reasons"]) & {"DAILY_LOSS_LIMIT", "WEEKLY_LOSS_LIMIT", "DRAWDOWN_LIMIT"}:
                block_entries(db, account, "demo-worker:risk-breach")
                for runner in db.scalars(select(DemoStrategy).where(DemoStrategy.status == "RUNNING")):
                    runner.status, runner.last_error = "PAUSED", "Portfolio loss/drawdown limit breached; operator review required"
                db.add(Notification(severity="CRITICAL", message="DEMO loss/drawdown limit breached; entries blocked, pending entries canceled, exchange protection retained"))
            db.commit()
            return
        order.reserved_risk, order.reserved_margin = number(decision["estimated_risk"]), number(decision["required_margin"])
    executor_owner = account.owner
    if not executor_owner or not account.lease_expires_at or aware(account.lease_expires_at) <= utcnow()+timedelta(seconds=30):
        raise DemoRejected("A fresh exclusive demo executor lease is required")
    order.status, order.updated_at = "SUBMITTING", utcnow()
    event(db, order, "SUBMITTING", {"client_order_id": order.client_order_id, "mode": "DEMO"})
    db.commit()  # The reservation and intent must survive a process/network crash.
    # Committing crash-safe SUBMITTING releases the first lock. An operator can
    # disable/pause/change risk in that window, so reacquire and freshly verify
    # every gate before the first byte of the sole POST. All controls serialize
    # on this account lock. A failure here is a known NO POST outcome.
    try:
        locked = lock_account(db)
        if locked.owner != executor_owner or not locked.lease_expires_at or aware(locked.lease_expires_at) <= utcnow():
            raise DemoRejected("Demo lease changed before order submission")
        if not order.reduce_only:
            fresh = await risk_approval(db, locked, order, adapter)
            order.risk_decision = fresh
            event(db, order, "RISK_DECISION", fresh)
            if not fresh["approved"]:
                if set(fresh["reasons"]) & {"DAILY_LOSS_LIMIT", "WEEKLY_LOSS_LIMIT", "DRAWDOWN_LIMIT"}:
                    block_entries(db, locked, "demo-worker:risk-breach")
                    for runner in db.scalars(select(DemoStrategy).where(DemoStrategy.status == "RUNNING")):
                        runner.status, runner.last_error = "PAUSED", "Portfolio loss/drawdown limit breached; operator review required"
                    db.add(Notification(severity="CRITICAL", message="DEMO loss/drawdown limit breached; entries blocked, protection retained"))
                raise DemoRejected(", ".join(fresh["reasons"]))
            order.reserved_risk, order.reserved_margin = number(fresh["estimated_risk"]), number(fresh["required_margin"])
        else:
            position = db.scalar(select(DemoPosition).where(DemoPosition.product_id == order.product_id).execution_options(populate_existing=True))
            if not position or not position.managed or position.signed_quantity*Side(order.side).sign >= 0 or order.quantity > abs(position.signed_quantity):
                raise DemoRejected("Managed demo position changed before reduce-only submission")
    except Exception as error:
        order.status = "REJECTED"
        order.reserved_risk, order.reserved_margin = D(0), D(0)
        order.last_error = str(error) if isinstance(error, DemoRejected) else "Pre-write verification failed; no order POST attempted"
        event(db, order, "REJECTED_BEFORE_SUBMISSION", {"reason": order.last_error, "post_attempted": False})
        db.commit()
        return
    try:
        submitted_at = time.monotonic()
        result = await adapter.place_order(product_id=order.product_id, side=order.side.lower(), size=int(order.quantity),
                                           client_order_id=order.client_order_id,
                                           order_type="market_order" if order.order_type == "MARKET" else "limit_order",
                                           limit_price=order.limit_price, reduce_only=order.reduce_only,
                                           bracket_stop_loss_price=None if order.reduce_only else order.stop_price,
                                           bracket_take_profit_price=None if order.reduce_only else order.target_price)
        apply_order(db, order, result)
        record_order_ack("DEMO", time.monotonic()-submitted_at, db=db)
        locked.reconciliation_required = True
    except OrderSubmissionUnknown:
        order.status, order.last_error = "UNKNOWN", "Exchange write outcome is UNKNOWN; client ID reconciliation required"
        account.reconciliation_required = True
        event(db, order, "UNKNOWN", {"operation": "place_order", "client_order_id": order.client_order_id})
    except DeltaAPIError as error:
        order.status, order.last_error = "REJECTED", str(error)
        order.reserved_risk, order.reserved_margin = D(0), D(0)
        event(db, order, "REJECTED", {"http_status": error.status, "code": error.code})
    except Exception:
        order.status, order.last_error = "UNKNOWN", "Order requires reconciliation after interrupted submission"
        account.reconciliation_required = True
        event(db, order, "UNKNOWN", {"operation": "interrupted_submission"})
    db.commit()


async def reconcile(db: Session, adapter: DemoClient, *, connect=False) -> DemoAccount:
    """REST recovery is required after every private stream gap and every write."""
    current = db.get(DemoAccount, 1)
    if current and current.credential_fingerprint != fingerprint():
        raise DemoRejected("Demo account credential identity changed; existing ledger must be reviewed")
    balances = await adapter.balances()
    positions = await adapter.positions()
    active = await adapter.active_orders()
    start = int(aware(current.created_at).timestamp()*1_000_000) if current else int(utcnow().timestamp()*1_000_000)
    fills = await adapter.fills(start_time=max(0, start-60_000_000))
    transactions = await adapter.wallet_transactions(start_time=max(0, start-60_000_000))
    if not isinstance(balances, list) or not isinstance(positions, list):
        raise DemoRejected("Incomplete demo balance/position snapshot")
    account = db.scalar(select(DemoAccount).where(DemoAccount.id == 1).with_for_update())
    if account is None:
        if not connect:
            raise DemoRejected("Connect demo first")
        account = DemoAccount(id=1, credential_fingerprint=fingerprint(), risk_limits=RiskLimits().model_dump(mode="json"), connected=True)
        db.add(account)
        db.flush()
    wallet = [row for row in balances if row.get("asset_symbol") == account.settlement_asset]
    if len(wallet) != 1:
        raise DemoRejected("Demo settlement wallet could not be identified uniquely")
    issues = cash_ledger(db, account, wallet[0], transactions)
    owned = list(db.scalars(select(DemoOrder).order_by(DemoOrder.created_at)))
    by_exchange = {str(order.exchange_order_id): order for order in owned if order.exchange_order_id is not None}
    by_client = {order.client_order_id: order for order in owned}
    active_ids = set()
    for value in active:
        if (type(value.get("id")) is not int or value["id"] <= 0 or type(value.get("product_id")) is not int
                or value.get("side") not in ("buy", "sell") or value.get("state") not in ("open", "pending")
                or number(value.get("size")) <= 0 or number(value.get("size")) != number(value.get("size")).to_integral_value()):
            raise DemoRejected("Active demo order snapshot has invalid identity or quantity")
        active_ids.add(value.get("id"))
        order = by_client.get(value.get("client_order_id"))
        if order:
            cancel_requested = order.status == "CANCEL_PENDING"
            apply_order(db, order, value)
            if cancel_requested and order.status not in TERMINAL:
                order.status = "CANCEL_PENDING"
    for order in owned:
        if order.status == "SUBMITTING":
            order.status = "UNKNOWN"
            event(db, order, "UNKNOWN", {"operation": "worker_restart_after_submission"})
        if order.status in ("UNKNOWN", "ACKNOWLEDGED", "OPEN", "PARTIALLY_FILLED", "CANCEL_PENDING") and order.exchange_order_id not in active_ids:
            try:
                value = await adapter.order_by_client_id(order.client_order_id)
                apply_order(db, order, value)
            except DeltaAPIError:
                order.status = "UNKNOWN"
                issues.append(f"Order {order.id} could not be resolved by original client ID; no automatic resubmission")
    by_exchange = {str(order.exchange_order_id): order for order in owned if order.exchange_order_id is not None}
    latest_entries = {}
    for order in owned:
        if not order.reduce_only and order.filled_quantity > 0:
            latest_entries[order.product_id] = order
    for value in active:
        if value.get("client_order_id") in by_client or value.get("reduce_only") not in (True, "true"):
            continue
        kind = value.get("stop_order_type")
        if kind not in ("stop_loss_order", "take_profit_order") or value.get("bracket_order") is not True:
            continue
        candidates = [order for order in latest_entries.values() if not order.reduce_only and order.filled_quantity > 0
                      and order.product_id == value.get("product_id") and Side(order.side).opposite.value.lower() == value.get("side")
                      and number(value.get("stop_price")) == (order.stop_price if kind == "stop_loss_order" else order.target_price)
                      and 0 < number(value.get("size")) <= order.quantity]
        if len(candidates) == 1:
            existing = db.get(DemoProtection, value.get("id"))
            if existing and existing.entry_order_id != candidates[0].id:
                issues.append("Generated demo protection ownership changed")
                continue
            if existing is None:
                existing = DemoProtection(exchange_order_id=value["id"], entry_order_id=candidates[0].id,
                                          product_id=value["product_id"], kind=kind, stop_price=number(value["stop_price"]), status=value["state"])
                db.add(existing)
            else:
                existing.status, existing.observed_at = value["state"], utcnow()
    db.flush()
    for value in fills:
        identity = str(value.get("id", ""))
        if not identity or len(identity) > 80:
            raise DemoRejected("Invalid exchange fill identity")
        product_id, exchange_order_id = value.get("product_id"), str(value.get("order_id"))
        if type(product_id) is not int or value.get("side") not in ("buy", "sell"):
            raise DemoRejected("Invalid exchange fill ownership metadata")
        existing = db.get(DemoFill, identity)
        amount, price, fee = number(value.get("size")), number(value.get("price")), number(value.get("commission"))
        if amount <= 0 or price <= 0:
            raise DemoRejected("Invalid exchange fill economics")
        matched = by_exchange.get(exchange_order_id)
        protection = db.get(DemoProtection, int(exchange_order_id)) if exchange_order_id.isdigit() else None
        attributes = (product_id, exchange_order_id, value["side"].upper(), amount, price, fee)
        if existing:
            if (existing.product_id, existing.exchange_order_id, existing.side, existing.quantity, existing.price, existing.fee) != attributes:
                raise DemoRejected("Exchange fill identity was reused with changed economics")
        else:
            db.add(DemoFill(id=identity, order_id=matched.id if matched else None, exchange_order_id=exchange_order_id,
                            protection_order_id=protection.exchange_order_id if protection else None,
                            product_id=product_id, side=value["side"].upper(), quantity=amount, price=price, fee=fee,
                            settlement_asset=str(value.get("settling_asset_symbol", account.settlement_asset)),
                            fill_type=str(value.get("fill_type", "normal")), exchange_created_at=str(value.get("created_at", ""))))
            if matched:
                event(db, matched, "FILL", {"fill_id": identity, "quantity": str(amount), "price": str(price), "fee": str(fee)})
        if not matched and not protection:
            issues.append(f"Unmanaged demo fill {identity} observed; generated exits must have durable exchange protection ownership")
    observed = set()
    unrealized = D(0)
    for value in positions:
        product_id = value.get("product_id")
        quantity = number(value.get("size"))
        if type(product_id) is not int or product_id <= 0 or quantity != quantity.to_integral_value():
            raise DemoRejected("Demo position lacks product identity")
        if quantity == 0:
            continue
        observed.add(product_id)
        product = db.get(DemoProduct, product_id)
        symbol = value.get("product_symbol") or (product.symbol if product else str(product_id))
        position = db.get(DemoPosition, product_id)
        if position is None:
            position = DemoPosition(product_id=product_id, symbol=symbol)
            db.add(position)
        position.signed_quantity, position.entry_price = quantity, number(value.get("entry_price"))
        position.mark_price = number(value.get("mark_price"), default=str(position.entry_price))
        position.margin, position.unrealized_pnl = number(value.get("margin")), number(value.get("unrealized_pnl"), default="0")
        if position.entry_price <= 0 or position.mark_price <= 0 or position.margin < 0:
            raise DemoRejected("Demo position has invalid price/margin economics")
        position.realized_pnl, position.realized_funding = number(value.get("realized_pnl"), default="0"), number(value.get("realized_funding"), default="0")
        position.observed_at = utcnow()
        settlement = product.metadata_json.get("settlement_asset") if product else (value.get("product") or {}).get("settling_asset", {}).get("symbol")
        if settlement == account.settlement_asset:
            unrealized += position.unrealized_pnl
        else:
            issues.append(f"Position {symbol} settlement currency is unsupported/unverified; account valuation is incomplete")
        entries = [o for o in owned if o.product_id == product_id and o.filled_quantity > 0 and not o.reduce_only]
        # Exactly one entry can own a symbol. Exchange-generated bracket fills
        # and manual trades are checked against the durable fill net below.
        own_fills = list(db.scalars(select(DemoFill).where(DemoFill.product_id == product_id)))
        net = sum((f.quantity*Side(f.side).sign for f in own_fills if f.order_id or f.protection_order_id), D(0))
        position.managed = bool(entries) and net == quantity
        if not position.managed:
            issues.append(f"Unmanaged or mismatched exchange position {symbol}; external exposure blocks entries")
        entry = entries[-1] if entries else None
        opposite = "sell" if quantity > 0 else "buy"
        owned_protection_ids = {p.exchange_order_id for p in db.scalars(select(DemoProtection).where(DemoProtection.product_id == product_id))}
        protections = [v for v in active if v.get("id") in owned_protection_ids and v.get("product_id") == product_id and v.get("side") == opposite and v.get("reduce_only") in (True, "true")]
        position.protection_verified = bool(entry) and all(any(
            v.get("stop_order_type") == kind and number(v.get("stop_price")) == expected
            and number(v.get("size")) >= abs(quantity) and v.get("state") in ("open", "pending")
            for v in protections) for kind, expected in (("stop_loss_order", entry.stop_price), ("take_profit_order", entry.target_price)))
        if not position.protection_verified:
            issues.append(f"Position {symbol} lacks verified exchange stop and target protection")
    for position in db.scalars(select(DemoPosition)):
        if position.product_id not in observed:
            position.signed_quantity, position.protection_verified, position.observed_at = D(0), False, utcnow()
    for protection in db.scalars(select(DemoProtection)):
        if protection.exchange_order_id in active_ids and protection.product_id not in observed:
            protection.status = "CANCEL_PENDING"
            issues.append(f"Flat demo product {protection.product_id} has active managed protection; cancellation pending")
    # /positions/margined can lag by up to ten seconds according to the
    # official API. A filled intent must never disappear during that lag.
    for product_id in {order.product_id for order in owned if order.filled_quantity > 0}:
        managed_fills = list(db.scalars(select(DemoFill).where(DemoFill.product_id == product_id)))
        managed_net = sum((f.quantity*Side(f.side).sign for f in managed_fills if f.order_id or f.protection_order_id), D(0))
        observed_position = db.get(DemoPosition, product_id)
        observed_net = observed_position.signed_quantity if observed_position and product_id in observed else D(0)
        if managed_net != observed_net:
            issues.append(f"Managed demo fill net differs from exchange position for product {product_id}; snapshot may be delayed")
    # Do not let active unowned non-reduce orders hide in the account.
    for value in active:
        if value.get("client_order_id") not in by_client and value.get("reduce_only") not in (True, "true"):
            issues.append(f"External active demo order {value.get('id')} blocks entries")
    for order in owned:
        if order.status == "UNKNOWN":
            issues.append(f"Order {order.id} remains UNKNOWN")
        if order.filled_quantity and order.exchange_order_id is not None:
            durable = sum((f.quantity for f in db.scalars(select(DemoFill).where(DemoFill.order_id == order.id))), D(0))
            if durable != order.filled_quantity:
                issues.append(f"Order {order.id} fill history differs from cumulative exchange fill quantity")
    account.equity, account.available_margin = number(wallet[0].get("balance"))+unrealized, number(wallet[0].get("available_balance"))
    if account.equity <= 0 or account.available_margin < 0:
        issues.append("Demo wallet equity/margin is nonpositive or invalid")
    if account.initial_equity == 0:
        account.initial_equity = account.equity
    account.peak_equity = max(account.peak_equity, account.equity)
    account.connected, account.issues, account.reconciliation_required, account.last_reconciled_at = True, sorted(set(issues)), bool(issues), utcnow()
    record_reconciliation("DEMO", not issues, db=db)
    db.add(DemoEquitySnapshot(equity=account.equity, available_margin=account.available_margin))
    db.flush()
    return account


def claim_lease(db: Session, owner: str, seconds=120) -> bool:
    account = lock_account(db)
    now = utcnow()
    if account.owner and account.owner != owner and account.lease_expires_at and aware(account.lease_expires_at) > now:
        return False
    account.owner, account.lease_expires_at = owner, now+timedelta(seconds=seconds)
    account.worker_status, account.worker_updated_at = "RUNNING", now
    return True


def request_cancel(db: Session, order: DemoOrder, actor: str) -> None:
    lock_account(db)
    if order.status in TERMINAL:
        return
    if order.status in ("RISK_PENDING", "RISK_APPROVED"):
        order.status, order.reserved_risk, order.reserved_margin = "CANCELED", D(0), D(0)
    elif order.status in ("UNKNOWN", "SUBMITTING"):
        raise DemoRejected("Resolve the original client order ID before requesting cancellation")
    else:
        order.status = "CANCEL_PENDING"
    event(db, order, "CANCEL_REQUESTED", {"actor": actor})


def block_entries(db: Session, account: DemoAccount, actor: str) -> None:
    """Cancel queued/resting entry quantities; preserve protective reduce orders."""
    account.entries_enabled, account.emergency_stop = False, True
    for order in db.scalars(select(DemoOrder).where(DemoOrder.reduce_only.is_(False), DemoOrder.status.in_(ACTIVE))):
        if order.status in ("UNKNOWN", "SUBMITTING"):
            account.reconciliation_required = True
            continue
        request_cancel(db, order, actor)


async def execute_cancel(db: Session, account: DemoAccount, order: DemoOrder, adapter: DemoClient) -> None:
    if order.status != "CANCEL_PENDING" or order.exchange_order_id is None:
        return
    try:
        result = await adapter.cancel_order(product_id=order.product_id, order_id=order.exchange_order_id)
        apply_order(db, order, result)
        account.reconciliation_required = True
    except OrderSubmissionUnknown:
        order.status, account.reconciliation_required = "UNKNOWN", True
        event(db, order, "UNKNOWN", {"operation": "cancel_order"})
    except DeltaAPIError as error:
        # A rejection can race a fill. Keep the reservation and reconcile.
        order.last_error, account.reconciliation_required = str(error), True
    db.commit()


def queue_close(db: Session, product_id: int, actor: str) -> DemoOrder:
    position = db.get(DemoPosition, product_id)
    if not position or position.signed_quantity == 0 or not position.managed:
        raise DemoRejected("Only identified managed demo positions can be closed")
    for entry in db.scalars(select(DemoOrder).where(DemoOrder.product_id == product_id, DemoOrder.reduce_only.is_(False), DemoOrder.status.in_(("OPEN", "PARTIALLY_FILLED", "ACKNOWLEDGED")))):
        request_cancel(db, entry, actor)
    existing = db.scalar(select(DemoOrder).where(DemoOrder.product_id == product_id, DemoOrder.reduce_only.is_(True), DemoOrder.status.in_(ACTIVE)))
    if existing:
        return existing
    return queue_order(db, {"idempotency_key": f"close:{product_id}:{uuid.uuid4().hex}", "symbol": position.symbol,
                            "side": "SELL" if position.signed_quantity > 0 else "BUY", "quantity": int(abs(position.signed_quantity)),
                            "order_type": "MARKET", "reduce_only": True}, actor)
