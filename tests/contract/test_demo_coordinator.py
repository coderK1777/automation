"""All fixtures in this module are controlled OFFLINE mocks, never Delta evidence."""
import copy
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from tradebot import demo
from tradebot.db import Base, utcnow
from tradebot.demo_models import DemoAccount, DemoCashMovement, DemoFill, DemoOrder, DemoOrderEvent, DemoPosition, DemoProduct, DemoProtection
from tradebot_core import Instrument
from tradebot_core.risk import RiskLimits
from tradebot_delta.errors import DeltaAPIError, OrderSubmissionUnknown

pytestmark = pytest.mark.mocked
D = Decimal


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_DEMO_API_KEY", "offline-fixture-key")
    monkeypatch.setenv("DELTA_DEMO_API_SECRET", "offline-fixture-secret")
    monkeypatch.setenv("DEMO_EXECUTION_AUTHORIZED", "true")
    engine = create_engine("sqlite:///"+str(tmp_path/"demo-fixture.db"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        now = utcnow()
        contract = Instrument(product_id=84, symbol="BTCUSD", underlying_asset="BTC", quote_asset="USD", settlement_asset="USD",
                              contract_size=D(".001"), tick_size=D(".1"), quantity_step=D(1), min_quantity=D(1),
                              max_quantity=D(10), initial_margin_rate=D(".5"), metadata_verified=True, metadata_timestamp=now)
        metadata = {"id": 84, "symbol": "BTCUSD", "linear_economics_verified": True, "state": "live", "trading_status": "operational",
                    "underlying_asset": "BTC", "quote_asset": "USD", "settlement_asset": "USD", "contract_value": ".001", "tick_size": ".1"}
        db.add(DemoProduct(id=84, symbol="BTCUSD", metadata_json=metadata, verified_contract=contract.model_dump(mode="json"),
                           evidence="OFFLINE synthetic contract fixture, never real exchange verification", verified_at=now))
        db.add(DemoAccount(id=1, credential_fingerprint=demo.fingerprint(), connected=True, entries_enabled=True, reconciliation_required=False,
                           equity=D(10000), available_margin=D(10000), initial_equity=D(10000), peak_equity=D(10000),
                           risk_limits=RiskLimits().model_dump(mode="json"), owner="fixture-owner", lease_expires_at=now+timedelta(seconds=120),
                           worker_status="RUNNING", worker_updated_at=now, stream_status="CONNECTED", last_reconciled_at=now))
        db.commit()
        yield db
    engine.dispose()


def request(key="offline_signal_0001", quantity=3):
    return {"idempotency_key": key, "symbol": "BTCUSD", "side": "BUY", "quantity": quantity,
            "order_type": "MARKET", "stop_price": D(81000), "target_price": D(85000)}


class MockExchange:
    """Explicit synthetic exchange state, not an adapter transport to real APIs."""
    def __init__(self):
        self.orders = {}
        self.position_rows = []
        self.fill_rows = []
        self.submissions = []
        self.cancellations = []
        self.failure = None

    async def ticker(self, symbol):
        return {"product_id": 84, "symbol": symbol, "timestamp": int(utcnow().timestamp()*1000000),
                "quotes": {"best_bid": "82493", "best_ask": "82497"}}

    async def balances(self):
        return [{"asset_id": 1, "asset_symbol": "USD", "balance": "10000", "available_balance": "9900"}]

    async def wallet_transactions(self, **kwargs):
        return []

    async def positions(self):
        return copy.deepcopy(self.position_rows)

    async def active_orders(self):
        return [copy.deepcopy(row) for row in self.orders.values() if row["state"] in ("open", "pending")]

    async def fills(self, **kwargs):
        return copy.deepcopy(self.fill_rows)

    async def order_by_client_id(self, client_id):
        for row in self.orders.values():
            if row.get("client_order_id") == client_id:
                return copy.deepcopy(row)
        raise DeltaAPIError(404, "not_found")

    async def place_order(self, **kwargs):
        self.submissions.append(kwargs)
        if self.failure:
            raise self.failure
        row = {"id": len(self.orders)+101, "product_id": kwargs["product_id"], "product_symbol": "BTCUSD",
               "client_order_id": kwargs["client_order_id"], "side": kwargs["side"], "size": kwargs["size"],
               "unfilled_size": kwargs["size"], "state": "open", "reduce_only": kwargs["reduce_only"],
               "order_type": kwargs["order_type"], "bracket_stop_loss_price": str(kwargs.get("bracket_stop_loss_price")),
               "bracket_take_profit_price": str(kwargs.get("bracket_take_profit_price"))}
        self.orders[row["id"]] = row
        return copy.deepcopy(row)

    async def cancel_order(self, **kwargs):
        self.cancellations.append(kwargs)
        row = self.orders[kwargs["order_id"]]
        row["state"] = "cancelled"
        return copy.deepcopy(row)

    def partial_fill(self, order, quantity=1):
        row = self.orders[order.exchange_order_id]
        row.update(unfilled_size=int(order.quantity)-quantity, average_fill_price="82497")
        self.position_rows = [{"product_id": 84, "product_symbol": "BTCUSD", "size": quantity, "entry_price": "82497",
                               "mark_price": "82497", "margin": "42", "unrealized_pnl": "0"}]
        self.fill_rows = [{"id": "offline-fill-1", "order_id": str(order.exchange_order_id), "product_id": 84, "side": "buy",
                          "size": str(quantity), "price": "82497", "commission": ".08", "created_at": utcnow().isoformat(), "settling_asset_symbol": "USD"}]
        for identity, kind, price in ((201, "stop_loss_order", order.stop_price), (202, "take_profit_order", order.target_price)):
            self.orders[identity] = {"id": identity, "product_id": 84, "side": "sell", "size": quantity, "unfilled_size": quantity,
                                     "state": "pending", "reduce_only": True, "bracket_order": True, "stop_order_type": kind, "stop_price": str(price)}


@pytest.mark.asyncio
async def test_mocked_order_intent_reservation_partial_fills_cancel_and_dedup(ledger):
    db, exchange = ledger, MockExchange()
    order = demo.queue_order(db, request(), "fixture-operator")
    db.commit()
    assert demo.queue_order(db, request(), "fixture-operator").id == order.id
    await demo.execute_intent(db, db.get(DemoAccount, 1), order, exchange)
    assert order.status == "OPEN" and len(exchange.submissions) == 1
    assert order.reserved_risk > 0 and order.reserved_margin > 0
    assert exchange.submissions[0]["bracket_stop_loss_price"] == D(81000)
    exchange.partial_fill(order)
    account = await demo.reconcile(db, exchange)
    db.commit()
    assert not account.reconciliation_required, account.issues
    assert order.status == "PARTIALLY_FILLED" and order.filled_quantity == D(1)
    assert db.get(DemoPosition, 84).protection_verified
    assert len(list(db.scalars(select(DemoProtection)))) == 2
    await demo.reconcile(db, exchange)
    db.commit()
    assert len(list(db.scalars(select(DemoFill)))) == 1
    events_before = len(list(db.scalars(select(DemoOrderEvent))))
    await demo.reconcile(db, exchange)
    db.commit()
    assert len(list(db.scalars(select(DemoOrderEvent)))) == events_before
    demo.request_cancel(db, order, "fixture-operator")
    db.commit()
    await demo.reconcile(db, exchange)
    assert order.status == "CANCEL_PENDING"
    await demo.execute_cancel(db, account, order, exchange)
    assert order.status == "CANCELED" and order.filled_quantity == D(1)
    assert order.reserved_margin == 0 and len(exchange.cancellations) == 1


@pytest.mark.asyncio
async def test_mocked_unknown_outcome_and_404_never_resubmit(ledger):
    exchange = MockExchange()
    order = demo.queue_order(ledger, request(), "fixture-operator")
    ledger.commit()
    exchange.failure = OrderSubmissionUnknown(order.client_order_id, "POST /v2/orders")
    await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    assert order.status == "UNKNOWN" and order.reserved_margin > 0
    for _ in range(3):
        account = await demo.reconcile(ledger, exchange)
        await demo.execute_intent(ledger, account, order, exchange)
    assert order.status == "UNKNOWN" and account.reconciliation_required
    assert len(exchange.submissions) == 1


@pytest.mark.asyncio
async def test_mocked_restart_submitting_is_unknown_without_post(ledger):
    order = demo.queue_order(ledger, request(), "fixture-operator")
    order.status = "SUBMITTING"
    ledger.commit()
    exchange = MockExchange()
    account = await demo.reconcile(ledger, exchange)
    assert account.reconciliation_required and order.status == "UNKNOWN"
    await demo.execute_intent(ledger, account, order, exchange)
    assert not exchange.submissions


@pytest.mark.asyncio
async def test_mocked_authoritative_lookup_resolves_unknown_original_id(ledger):
    exchange = MockExchange()
    order = demo.queue_order(ledger, request(), "fixture-operator")
    ledger.commit()
    await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    order.status, order.exchange_order_id = "UNKNOWN", None
    ledger.commit()
    account = await demo.reconcile(ledger, exchange)
    assert order.status == "OPEN" and not account.reconciliation_required
    assert len(exchange.submissions) == 1


@pytest.mark.asyncio
async def test_mocked_unmanaged_exposure_blocks_entries_and_is_not_adopted(ledger):
    exchange = MockExchange()
    exchange.position_rows = [{"product_id": 84, "product_symbol": "BTCUSD", "size": 2, "entry_price": "82497", "mark_price": "82497", "margin": "83"}]
    account = await demo.reconcile(ledger, exchange)
    ledger.commit()
    assert account.reconciliation_required and not ledger.get(DemoPosition, 84).managed
    with pytest.raises(demo.DemoRejected, match="entries blocked"):
        demo.queue_order(ledger, request(), "fixture-operator")
    with pytest.raises(demo.DemoRejected, match="identified managed"):
        demo.queue_close(ledger, 84, "fixture-operator")


@pytest.mark.asyncio
async def test_mocked_margin_or_stale_stream_risk_rejection_performs_no_write(ledger):
    order = demo.queue_order(ledger, request(), "fixture-operator")
    account = ledger.get(DemoAccount, 1)
    account.available_margin, account.stream_status = D(1), "DISCONNECTED"
    ledger.commit()
    exchange = MockExchange()
    await demo.execute_intent(ledger, account, order, exchange)
    assert order.status == "REJECTED" and not exchange.submissions
    assert "MARGIN_LIMIT" in order.risk_decision["reasons"]
    assert "WORKER_UNHEALTHY" in order.risk_decision["reasons"]


def test_mocked_idempotency_payload_reuse_and_pending_ownership_rejected(ledger):
    demo.queue_order(ledger, request(), "fixture-operator")
    ledger.commit()
    with pytest.raises(demo.DemoRejected, match="different order"):
        demo.queue_order(ledger, request(quantity=2), "fixture-operator")
    with pytest.raises(demo.DemoRejected, match="pending managed"):
        demo.queue_order(ledger, request(key="offline_signal_other"), "fixture-operator")


def test_mocked_contract_evidence_and_paper_assumptions_cannot_authorize(ledger):
    product = ledger.get(DemoProduct, 84)
    product.verified_contract = {**product.verified_contract, "verification_scope": "PAPER_MODEL", "assumptions": ["Synthetic paper assumptions"]}
    with pytest.raises(demo.DemoRejected, match="Paper assumptions"):
        demo.core_contract(product)
    product.verified_contract = None
    with pytest.raises(demo.DemoRejected, match="Verify the actual"):
        demo.queue_order(ledger, request(), "fixture-operator")


def test_mocked_only_one_executor_lease_and_credential_rotation_is_blocked(ledger, monkeypatch):
    assert not demo.claim_lease(ledger, "second-owner")
    assert demo.claim_lease(ledger, "fixture-owner")
    monkeypatch.setenv("DELTA_DEMO_API_KEY", "different-offline-account")
    with pytest.raises(demo.DemoRejected, match="differ"):
        demo.lock_account(ledger)


@pytest.mark.asyncio
async def test_mocked_filled_intent_cannot_vanish_during_margined_position_lag(ledger):
    exchange = MockExchange()
    order = demo.queue_order(ledger, request(quantity=1), "fixture-operator")
    ledger.commit()
    await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    exchange.partial_fill(order)
    exchange.orders[order.exchange_order_id]["state"] = "closed"
    exchange.position_rows = []  # documented lag in /positions/margined
    account = await demo.reconcile(ledger, exchange)
    assert account.reconciliation_required
    assert any("fill net differs" in issue for issue in account.issues)


@pytest.mark.asyncio
async def test_mocked_changed_fill_identity_or_regressed_execution_is_rejected(ledger):
    exchange = MockExchange()
    order = demo.queue_order(ledger, request(), "fixture-operator")
    ledger.commit()
    await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    exchange.partial_fill(order)
    await demo.reconcile(ledger, exchange)
    ledger.commit()
    exchange.fill_rows[0]["price"] = "90000"
    with pytest.raises(demo.DemoRejected, match="reused"):
        await demo.reconcile(ledger, exchange)
    exchange.fill_rows[0]["price"] = "82497"
    exchange.orders[order.exchange_order_id]["unfilled_size"] = 3
    with pytest.raises(demo.DemoRejected, match="regressed"):
        await demo.reconcile(ledger, exchange)


def test_mocked_emergency_block_keeps_protective_close_authorized(ledger):
    account = ledger.get(DemoAccount, 1)
    account.emergency_stop, account.entries_enabled = True, False
    ledger.add(DemoPosition(product_id=84, symbol="BTCUSD", signed_quantity=D(1), entry_price=D(82497), mark_price=D(82497), margin=D(42), managed=True, protection_verified=True))
    ledger.commit()
    with pytest.raises(demo.DemoRejected, match="entries blocked"):
        demo.queue_order(ledger, request(), "fixture-operator")
    close = demo.queue_close(ledger, 84, "fixture-operator")
    assert close.reduce_only and close.side == "SELL" and close.quantity == D(1)
    assert demo.queue_close(ledger, 84, "fixture-operator").id == close.id


def test_mocked_emergency_cancels_resting_entries_without_releasing_unknown_risk(ledger):
    order = demo.queue_order(ledger, request(), "fixture-operator")
    order.status, order.reserved_margin, order.reserved_risk = "OPEN", D(125), D(5)
    ledger.commit()
    account = ledger.get(DemoAccount, 1)
    demo.block_entries(ledger, account, "fixture-emergency")
    assert order.status == "CANCEL_PENDING" and order.reserved_margin == D(125)
    assert account.emergency_stop and not account.entries_enabled
    order.status = "UNKNOWN"
    ledger.commit()
    demo.block_entries(ledger, account, "fixture-emergency")
    assert order.status == "UNKNOWN" and order.reserved_risk == D(5)
    assert account.reconciliation_required


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["PAUSED", "VERSION_CHANGED", "NO_APPROVAL"])
async def test_mocked_final_submission_rechecks_exact_version_approval_and_pause(ledger, reason):
    from tradebot.demo_models import DemoStrategy
    from tradebot.models import Approval, Strategy
    strategy = Strategy(name="OFFLINE gated demo strategy", current_version=1)
    ledger.add(strategy)
    ledger.flush()
    ledger.add(DemoStrategy(strategy_id=strategy.id, version=1, status="PAUSED" if reason == "PAUSED" else "RUNNING"))
    if reason != "NO_APPROVAL":
        ledger.add(Approval(strategy_id=strategy.id, version=1, stage="DEMO", evidence={"source": "OFFLINE fixture"}, actor="fixture-operator"))
    order = demo.queue_order(ledger, {**request(), "strategy_id": strategy.id, "strategy_version": 1}, "fixture-operator")
    if reason == "VERSION_CHANGED":
        strategy.current_version = 2
    ledger.commit()
    exchange = MockExchange()
    with pytest.raises(demo.DemoRejected, match="approval was paused, changed or withdrawn"):
        await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    assert not exchange.submissions


@pytest.mark.asyncio
async def test_mocked_portfolio_drawdown_blocks_entries_preserving_exchange_protection(ledger):
    from tradebot.models import Notification
    order = demo.queue_order(ledger, request(), "fixture-operator")
    account = ledger.get(DemoAccount, 1)
    account.peak_equity = D(20000)
    ledger.commit()
    exchange = MockExchange()
    await demo.execute_intent(ledger, account, order, exchange)
    assert order.status == "REJECTED" and "DRAWDOWN_LIMIT" in order.risk_decision["reasons"]
    assert account.emergency_stop and not account.entries_enabled and not exchange.submissions
    assert ledger.scalar(select(Notification).where(Notification.severity == "CRITICAL")) is not None


@pytest.mark.asyncio
async def test_mocked_manual_protection_or_external_fill_is_never_silently_adopted(ledger):
    exchange = MockExchange()
    order = demo.queue_order(ledger, request(), "fixture-operator")
    ledger.commit()
    await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    exchange.partial_fill(order)
    for identity in (201, 202):
        exchange.orders[identity]["bracket_order"] = False  # manual exchange stops
    account = await demo.reconcile(ledger, exchange)
    ledger.commit()
    assert account.reconciliation_required and not ledger.get(DemoPosition, 84).protection_verified
    assert not list(ledger.scalars(select(DemoProtection)))
    # A manual fill increases observed quantity but cannot become managed entry
    # ownership merely because the account has prior application trades.
    exchange.fill_rows.append({**exchange.fill_rows[0], "id": "offline-external-fill", "order_id": "9999", "size": "1"})
    exchange.position_rows[0]["size"] = 2
    account = await demo.reconcile(ledger, exchange)
    assert account.reconciliation_required and not ledger.get(DemoPosition, 84).managed
    assert any("Unmanaged demo fill" in issue for issue in account.issues)


@pytest.mark.asyncio
async def test_mocked_new_contract_approval_cannot_rebind_existing_order_snapshot(ledger):
    order = demo.queue_order(ledger, request(), "fixture-operator")
    product = ledger.get(DemoProduct, 84)
    product.verified_contract = {**product.verified_contract, "initial_margin_rate": ".75"}
    ledger.commit()
    exchange = MockExchange()
    with pytest.raises(demo.DemoRejected, match="approval changed"):
        await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    assert not exchange.submissions and order.status == "RISK_PENDING"


@pytest.mark.asyncio
async def test_mocked_private_worker_reconnect_reauthenticates_and_marks_recovery(ledger, monkeypatch):
    import asyncio
    from sqlalchemy.orm import sessionmaker
    from tradebot import demo_worker
    from tradebot_delta.errors import DeltaStreamUnavailable
    factory = sessionmaker(ledger.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(demo_worker, "session_factory", lambda: factory)
    stopped, connections = asyncio.Event(), []
    class PrivateClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def private_messages(self, *args, **kwargs):
            connections.append((args, kwargs))
            yield {"type": "private_authenticated", "environment": "DEMO", "requires_recovery": True}
            if len(connections) == 1:
                raise DeltaStreamUnavailable("OFFLINE forced private disconnect")
            stopped.set()
            yield {"type": "orders", "symbol": "BTCUSD", "seq_no": 1}
    monkeypatch.setattr(demo, "client", lambda **kwargs: PrivateClient())
    await asyncio.wait_for(demo_worker.private_stream("fixture-owner", stopped), timeout=8)
    ledger.expire_all()
    account = ledger.get(DemoAccount, 1)
    assert len(connections) == 2
    assert all(args[1] == ("all",) and kwargs["emit_auth"] is True for args, kwargs in connections)
    assert account.reconciliation_required and account.stream_status == "CONNECTED"
    assert not list(ledger.scalars(select(DemoFill)))


@pytest.mark.asyncio
async def test_mocked_standby_worker_makes_no_exchange_requests(ledger, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from tradebot import demo_worker
    factory = sessionmaker(ledger.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(demo_worker, "session_factory", lambda: factory)
    def forbidden(**kwargs):
        raise AssertionError("Standby worker must never open a private client")
    monkeypatch.setattr(demo, "client", forbidden)
    assert await demo_worker.cycle("second-owner") is False


def test_mocked_authoritative_cash_funding_ledger_dedup_and_delayed_wallet(ledger):
    account = ledger.get(DemoAccount, 1)
    wallet = {"asset_id": 1, "asset_symbol": "USD", "balance": "10000"}
    assert demo.cash_ledger(ledger, account, wallet, []) == []
    ledger.commit()
    timestamp = utcnow()+timedelta(seconds=1)
    funding = {"id": 10001, "asset_id": 1, "asset_symbol": 1, "amount": ".25", "balance": "10000.25", "transaction_type": "funding", "product_id": 84, "created_at": timestamp.isoformat()}
    # Signed amount and wallet asset identity define denomination. No funding
    # rate/interval formula or misleading integer asset_symbol is inferred.
    issues = demo.cash_ledger(ledger, account, wallet, [funding])
    assert any("snapshot may be delayed" in issue for issue in issues)
    ledger.commit()
    wallet["balance"] = "10000.25"
    assert demo.cash_ledger(ledger, account, wallet, [funding]) == []
    assert demo.cash_ledger(ledger, account, wallet, [funding]) == []
    ledger.commit()
    assert len(list(ledger.scalars(select(DemoCashMovement)))) == 2  # baseline+funding
    assert ledger.get(DemoCashMovement, "10001").asset_symbol == "USD"
    with pytest.raises(demo.DemoRejected, match="changed monetary"):
        demo.cash_ledger(ledger, account, wallet, [{**funding, "amount": ".5"}])


def test_mocked_cash_ledger_gap_or_wrong_currency_blocks_instead_of_converting(ledger):
    account = ledger.get(DemoAccount, 1)
    wallet = {"asset_id": 1, "asset_symbol": "USD", "balance": "10000"}
    demo.cash_ledger(ledger, account, wallet, [])
    ledger.commit()
    row = {"id": 10002, "asset_id": 1, "asset_symbol": "USD", "amount": "-1", "balance": "9998", "transaction_type": "commission", "created_at": (utcnow()+timedelta(seconds=1)).isoformat()}
    issues = demo.cash_ledger(ledger, account, {**wallet, "balance": "9998"}, [row])
    assert any("running balance mismatch" in issue for issue in issues)
    with pytest.raises(demo.DemoRejected, match="currency disagree"):
        demo.cash_ledger(ledger, account, wallet, [{**row, "asset_symbol": "INR"}])


@pytest.mark.asyncio
async def test_mocked_repeated_strategy_trade_does_not_assign_brackets_to_historical_entry(ledger):
    old = demo.queue_order(ledger, request(key="offline-old-entry", quantity=1), "fixture-operator")
    old.status, old.filled_quantity, old.exchange_order_id = "FILLED", D(1), 51
    old.average_fill_price, old.created_at = D(82497), utcnow()-timedelta(hours=1)
    closing = DemoOrder(id="offline_old_close", idempotency_key="offline-old-close", client_order_id="offline-old-close",
                        product_id=84, symbol="BTCUSD", side="SELL", quantity=D(1), filled_quantity=D(1),
                        order_type="MARKET", status="FILLED", reduce_only=True, exchange_order_id=52,
                        average_fill_price=D(82497), instrument_snapshot=old.instrument_snapshot, created_at=old.created_at+timedelta(minutes=1))
    ledger.add(closing)
    for identity, order, side in (("offline-old-buy", old, "BUY"), ("offline-old-sell", closing, "SELL")):
        ledger.add(DemoFill(id=identity, order_id=order.id, exchange_order_id=str(order.exchange_order_id), product_id=84,
                           side=side, quantity=D(1), price=D(82497), fee=D(".08"), settlement_asset="USD", exchange_created_at=order.created_at.isoformat()))
    ledger.commit()
    current = demo.queue_order(ledger, request(key="offline-current-entry", quantity=1), "fixture-operator")
    ledger.commit()
    exchange = MockExchange()
    await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), current, exchange)
    exchange.partial_fill(current)
    exchange.orders[current.exchange_order_id]["state"] = "closed"
    account = await demo.reconcile(ledger, exchange)
    ledger.commit()
    assert not account.reconciliation_required, account.issues
    assert all(protection.entry_order_id == current.id for protection in ledger.scalars(select(DemoProtection)))


def demo_strategy_fixture(db, changes=None):
    from tradebot.demo_models import DemoStrategy
    from tradebot.models import Approval, Backtest, Strategy, StrategyVersion
    from tradebot_core import StrategyConfig
    rule = {"op": "GT", "left": {"feature": "close"}, "right": {"value": "0"}}
    config = StrategyConfig.model_validate({"name": "OFFLINE causal demo strategy", "kind": "CUSTOM", "long_rules": rule,
                                           "fast_period": 2, "slow_period": 3, "lookback": 2, "rsi_period": 2,
                                           "allowed_environments": ["BACKTEST", "PAPER", "DEMO"], **(changes or {})})
    strategy = Strategy(name=config.name, current_version=1)
    db.add(strategy)
    db.flush()
    saved = StrategyVersion(strategy_id=strategy.id, version=1, validated=True, config=config.model_dump(mode="json"))
    runner = DemoStrategy(strategy_id=strategy.id, version=1, status="RUNNING")
    db.add_all([saved, runner, Backtest(strategy_id=strategy.id, version=1, status="COMPLETED", dataset_hash="OFFLINE_FIXTURE"),
                Approval(strategy_id=strategy.id, version=1, stage="DEMO", actor="fixture", evidence={"source": "OFFLINE fixture"})])
    db.commit()
    return strategy, saved, runner


UNSUPPORTED_DEMO_CONFIGS = [
    ({"allowed_environments": ["BACKTEST", "PAPER"]}, "allowed_environments"),
    ({"execution": {"entry_type": "LIMIT"}}, "execution"),
    ({"cooldown_bars": 2}, "cooldown_bars"),
    ({"max_trades_per_day": 3}, "max_trades_per_day"),
    ({"max_daily_loss_fraction": ".02"}, "max_daily_loss_fraction"),
    ({"capital_allocation_fraction": ".2"}, "capital_allocation_fraction"),
    ({"max_consecutive_losses": 2}, "max_consecutive_losses"),
    ({"max_drawdown_fraction": ".03"}, "max_drawdown_fraction"),
    ({"max_open_positions": 1}, "max_open_positions"),
    ({"execution": {"maintenance_margin_rate": ".1"}, "min_liquidation_distance_fraction": ".02"}, "min_liquidation_distance_fraction"),
    ({"long_exit_rules": {"op": "GT", "left": {"feature": "close"}, "right": {"value": "0"}}}, "long_exit_rules"),
    ({"filters": {"event_filter_enabled": True}}, "filters.event_filter_enabled"),
    ({"filters": {"maximum_absolute_funding_rate": ".01"}}, "filters.maximum_absolute_funding_rate"),
    ({"exit_rules": {"trailing_method": "FIXED", "trailing_distance": "50"}}, "exit_rules.trailing_method"),
    ({"exit_rules": {"targets": [{"reward_multiple": 1, "fraction": ".5"}, {"reward_multiple": 2, "fraction": ".5"}]}}, "exit_rules.targets"),
    ({"exit_rules": {"break_even_trigger_r": 1}}, "exit_rules.break_even_trigger_r"),
    ({"exit_rules": {"max_holding_bars": 3}}, "exit_rules.max_holding_bars"),
    ({"exit_rules": {"trend_invalidation_exit": True}}, "exit_rules.trend_invalidation_exit"),
    ({"exit_rules": {"dynamic_target_atr_multiple": 2}}, "exit_rules.dynamic_target_atr_multiple"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,field", UNSUPPORTED_DEMO_CONFIGS)
async def test_mocked_strategy_start_and_final_submission_reject_unsupported_policies(ledger, changes, field):
    from fastapi import HTTPException
    from types import SimpleNamespace
    from tradebot.demo_api import Confirm, start_strategy
    strategy, _, _ = demo_strategy_fixture(ledger, changes)
    with pytest.raises(HTTPException) as rejected:
        start_strategy(strategy.id, Confirm(confirm=True), user=SimpleNamespace(username="fixture"), db=ledger)
    assert rejected.value.status_code == 409 and field in rejected.value.detail
    order = demo.queue_order(ledger, {**request(), "strategy_id": strategy.id, "strategy_version": 1}, "fixture")
    ledger.commit()
    exchange = MockExchange()
    with pytest.raises(demo.DemoRejected, match=field):
        await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    assert not exchange.submissions and order.reserved_risk == 0


@pytest.mark.asyncio
async def test_mocked_correlated_limit_has_no_unverified_zero_context_fallback(ledger):
    account = ledger.get(DemoAccount, 1)
    account.risk_limits = {**account.risk_limits, "max_correlated_exposure_multiple": "1"}
    ledger.commit()
    order = demo.queue_order(ledger, request(), "fixture")
    ledger.commit()
    exchange = MockExchange()
    with pytest.raises(demo.DemoRejected, match="max_correlated_exposure_multiple"):
        await demo.execute_intent(ledger, account, order, exchange)
    assert not exchange.submissions


class MockCandleExchange(MockExchange):
    def __init__(self, *, count=40, direction=1):
        super().__init__()
        self.count, self.direction, self.candle_requests = count, direction, []

    async def candles(self, symbol, resolution, start, end):
        seconds = {"1m": 60, "5m": 300}[resolution]
        self.candle_requests.append((symbol, seconds))
        latest_closed = int(utcnow().timestamp())//seconds*seconds-seconds
        rows = []
        for index in range(self.count):
            close = D(82000)+self.direction*D(index*10)
            rows.append({"timestamp": latest_closed-(self.count-1-index)*seconds, "open": str(close), "high": str(close+10),
                         "low": str(close-10), "close": str(close), "volume": "1000"})
        # Include the current still-open exchange bar to prove it is excluded.
        rows.append({**rows[-1], "timestamp": latest_closed+seconds, "high": "10000000", "volume": "999999999"})
        return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("sizing", [
    {"mode": "FIXED_CONTRACTS", "fixed_quantity": 3},
    {"mode": "FIXED_NOTIONAL", "fixed_notional": "250"},
    {"mode": "VOLATILITY", "volatility_period": 14, "volatility_multiplier": 2},
])
async def test_mocked_demo_worker_uses_closed_higher_timeframes_and_declared_risk_capped_sizing(ledger, sizing):
    from tradebot.demo_models import DemoCandle
    from tradebot.demo_worker import process_strategies
    _, _, runner = demo_strategy_fixture(ledger, {"sizing": sizing, "higher_timeframes": [{"timeframe_seconds": 300, "fast_period": 2, "slow_period": 3}]})
    exchange = MockCandleExchange()
    await process_strategies(ledger, ledger.get(DemoAccount, 1), exchange)
    order = ledger.scalar(select(DemoOrder))
    assert order is not None, runner.last_error
    assert exchange.candle_requests == [("BTCUSD", 60), ("BTCUSD", 300)]
    assert all(c.high < D(10000000) for c in ledger.scalars(select(DemoCandle)))
    evidence = ledger.scalar(select(DemoOrderEvent).where(DemoOrderEvent.event_type == "STRATEGY_SIZING")).details
    assert evidence["sizing"]["sizing_mode"] == sizing["mode"]
    assert order.quantity == D(3) if sizing["mode"] == "FIXED_CONTRACTS" else order.quantity <= 10
    assert D(evidence["volatility_atr"]) == D(20) if sizing["mode"] == "VOLATILITY" else evidence["volatility_atr"] is None
    assert len(evidence["data_snapshot_id"]) == 64 and not exchange.submissions
    await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    assert order.status == "OPEN" and len(exchange.submissions) == 1


@pytest.mark.asyncio
async def test_mocked_demo_volatility_sizing_requires_warm_actual_closed_atr(ledger):
    from tradebot.demo_worker import process_strategies
    _, _, runner = demo_strategy_fixture(ledger, {"sizing": {"mode": "VOLATILITY", "volatility_period": 14}})
    await process_strategies(ledger, ledger.get(DemoAccount, 1), MockCandleExchange(count=4))
    assert ledger.scalar(select(DemoOrder)) is None
    assert runner.last_error


@pytest.mark.asyncio
async def test_mocked_missing_higher_timeframe_is_never_replaced_with_base_history(ledger):
    from tradebot.demo_worker import process_strategies
    demo_strategy_fixture(ledger, {"higher_timeframes": [{"timeframe_seconds": 300, "fast_period": 2, "slow_period": 3}]})
    exchange = MockCandleExchange()
    original_candles = exchange.candles
    async def missing_higher(symbol, resolution, start, end):
        return [] if resolution == "5m" else await original_candles(symbol, resolution, start, end)
    exchange.candles = missing_higher
    await process_strategies(ledger, ledger.get(DemoAccount, 1), exchange)
    assert ledger.scalar(select(DemoOrder)) is None and not exchange.submissions


@pytest.mark.asyncio
async def test_mocked_worker_pauses_unapproved_environment_before_reading_market(ledger):
    from tradebot.demo_worker import process_strategies
    _, _, runner = demo_strategy_fixture(ledger, {"allowed_environments": ["PAPER"]})
    exchange = MockCandleExchange()
    await process_strategies(ledger, ledger.get(DemoAccount, 1), exchange)
    assert runner.status == "PAUSED" and "allowed_environments" in runner.last_error
    assert not exchange.candle_requests and ledger.scalar(select(DemoOrder)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["MISSING", "STALE", "FUTURE"])
async def test_mocked_final_strategy_submit_requires_original_fresh_causal_signal_evidence(ledger, fault):
    from tradebot.demo_worker import process_strategies
    demo_strategy_fixture(ledger)
    exchange = MockCandleExchange()
    await process_strategies(ledger, ledger.get(DemoAccount, 1), exchange)
    order = ledger.scalar(select(DemoOrder))
    evidence = ledger.scalar(select(DemoOrderEvent).where(DemoOrderEvent.event_type == "STRATEGY_SIZING"))
    if fault == "MISSING":
        ledger.delete(evidence)
    else:
        moment = utcnow()+timedelta(seconds=30) if fault == "FUTURE" else utcnow()-timedelta(minutes=3)
        evidence.details = {**evidence.details, "signal_timestamp": moment.isoformat()}
    ledger.commit()
    with pytest.raises(demo.DemoRejected, match="causal signal|signal is stale"):
        await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    assert not exchange.submissions and order.reserved_margin == 0


@pytest.mark.asyncio
async def test_mocked_paused_strategy_opposite_exit_retains_brackets_and_owns_close(ledger):
    from tradebot.demo_worker import process_opposite_exits
    _, _, runner = demo_strategy_fixture(ledger, {"long_rules": None, "short_rules": {"op": "GT", "left": {"feature": "close"}, "right": {"value": "0"}}})
    entry = demo.queue_order(ledger, {**request(), "strategy_id": runner.strategy_id, "strategy_version": 1}, "fixture")
    entry.status, entry.filled_quantity = "FILLED", D(3)
    position = DemoPosition(product_id=84, symbol="BTCUSD", signed_quantity=D(3), entry_price=D(82000), mark_price=D(82000),
                            margin=D(125), managed=True, protection_verified=True)
    protection = DemoProtection(exchange_order_id=201, entry_order_id=entry.id, product_id=84, kind="stop_loss_order", stop_price=D(81000), status="OPEN")
    ledger.add_all([position, protection])
    runner.status = "PAUSED"
    ledger.get(DemoAccount, 1).entries_enabled = False
    ledger.commit()
    exchange = MockCandleExchange()
    await process_opposite_exits(ledger, ledger.get(DemoAccount, 1), exchange)
    close = ledger.scalar(select(DemoOrder).where(DemoOrder.reduce_only.is_(True)))
    assert close is not None and close.side == "SELL" and close.quantity == D(3)
    assert protection.status == "OPEN" and not exchange.cancellations and not exchange.submissions
    assert runner.status == "PAUSED"


@pytest.mark.asyncio
async def test_mocked_multiple_queued_signals_reserve_sequentially_without_phantom_exposure(ledger):
    # Two separate products can queue before the lease owner prices either.
    first = demo.queue_order(ledger, request(quantity=1), "fixture")
    product = ledger.get(DemoProduct, 84)
    second_contract = {**product.verified_contract, "product_id": 85, "symbol": "OFFLINEETHUSD"}
    ledger.add(DemoProduct(id=85, symbol="OFFLINEETHUSD", verified_contract=second_contract,
                          metadata_json={**product.metadata_json, "id": 85, "symbol": "OFFLINEETHUSD"},
                          evidence=product.evidence, verified_at=product.verified_at))
    ledger.commit()
    second = demo.queue_order(ledger, {**request(key="offline_signal_0002", quantity=1), "symbol": "OFFLINEETHUSD"}, "fixture")
    ledger.commit()
    exchange = MockExchange()
    first_decision = await demo.risk_approval(ledger, ledger.get(DemoAccount, 1), first, exchange)
    assert first_decision["approved"]  # second RISK_PENDING has no economic reservation yet
    first.status, first.risk_decision = "OPEN", first_decision
    first.reserved_margin, first.reserved_risk = D("9999.9"), D(2)
    ledger.commit()
    original_ticker = exchange.ticker
    async def second_ticker(symbol):
        return {**await original_ticker(symbol), "product_id": 85}
    exchange.ticker = second_ticker
    second_decision = await demo.risk_approval(ledger, ledger.get(DemoAccount, 1), second, exchange)
    assert not second_decision["approved"]  # first committed reservation is now mandatory
    assert not exchange.submissions


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["DISABLE", "PAUSE"])
async def test_mocked_operator_control_between_durable_submit_and_post_prevents_write(ledger, monkeypatch, control):
    from tradebot.demo_worker import process_strategies
    _, _, runner = demo_strategy_fixture(ledger)
    exchange = MockCandleExchange()
    await process_strategies(ledger, ledger.get(DemoAccount, 1), exchange)
    order = ledger.scalar(select(DemoOrder))
    original_lock, locks = demo.lock_account, []
    def raced_lock(db):
        account = original_lock(db)
        locks.append(account)
        if len(locks) == 2:  # durable SUBMITTING has committed; no POST yet
            assert db.get(DemoOrder, order.id).status == "SUBMITTING"
            if control == "DISABLE":
                account.entries_enabled = False
            else:
                runner.status = "PAUSED"
            db.flush()
        return account
    monkeypatch.setattr(demo, "lock_account", raced_lock)
    await demo.execute_intent(ledger, ledger.get(DemoAccount, 1), order, exchange)
    assert order.status == "REJECTED" and not exchange.submissions
    assert order.reserved_margin == 0 and order.reserved_risk == 0
    evidence = ledger.scalar(select(DemoOrderEvent).where(DemoOrderEvent.event_type == "REJECTED_BEFORE_SUBMISSION"))
    assert evidence.details["post_attempted"] is False
