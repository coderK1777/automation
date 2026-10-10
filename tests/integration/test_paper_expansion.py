"""Durable advanced PAPER policies against declared synthetic market fixtures."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from tradebot.expansion_models import PaperFundingEvent, PaperMovement, PaperPendingEntry, PaperPositionState

D = Decimal


def queued(paper_fixture, *, execution=None, exit_rules=None, config_updates=None):
    from tradebot.models import Candle, Order, Signal, Strategy
    from tradebot.paper import enter_signal
    from tradebot.repository import core_candles, version
    from tradebot_core import StrategyConfig, evaluate_strategy
    client, factory, identifier = paper_fixture
    with factory() as db:
        strategy = db.get(Strategy, identifier)
        config = StrategyConfig.model_validate({**version(db, strategy).config,
                    "execution": execution or {"entry_type": "LIMIT", "volume_participation_fraction": ".5"},
                    "exit_rules": exit_rules or {"opposite_signal_exit": False}, **(config_updates or {})})
        rows = list(db.scalars(select(Candle).order_by(Candle.timestamp)))
        generated = evaluate_strategy(config, core_candles(rows))
        assert generated is not None
        enter_signal(db, identifier, config, generated, rows[-1], executor_healthy=True)
        db.commit()
        entry = db.scalar(select(Order).where(Order.reduce_only.is_(False)))
        assert entry is not None, [(signal.status, signal.reason) for signal in db.scalars(select(Signal))]
        assert entry.status == "OPEN"
        return client, factory, identifier, entry.id


def advance(factory, strategy_id, order_id, monkeypatch, *, low=None, high=None, open_price=None, close=None, volume=D(20), skip=0):
    from tradebot import paper, paper_expansion
    from tradebot.db import aware
    from tradebot.models import Candle, Order
    with factory() as db:
        pending = db.get(PaperPendingEntry, order_id)
        order = db.get(Order, order_id)
        timestamp = aware(pending.last_candle_at)+timedelta(minutes=1+skip)
        limit = order.price or D(63950)
        reference = close if close is not None else limit+50
        bar = Candle(symbol=order.symbol, timeframe_seconds=60, timestamp=timestamp,
                     open=open_price if open_price is not None else reference,
                     high=high if high is not None else reference+10,
                     low=low if low is not None else limit-50,
                     close=reference, volume=volume)
        db.add(bar)
        db.commit()
        now = timestamp+timedelta(minutes=1, seconds=1)
        monkeypatch.setattr(paper, "utcnow", lambda: now)
        monkeypatch.setattr(paper_expansion, "utcnow", lambda: now)
        paper_expansion.process_pending(db, {strategy_id: [bar]}, executor_healthy=True)
        db.commit()
        return bar.id


def assert_balanced(db):
    from tradebot.models import PaperAccount
    from tradebot.paper import reconcile
    assert reconcile(db, db.get(PaperAccount, 1)) == []


def test_limit_touch_partial_restart_then_full_fill_has_one_owned_position(paper_fixture, monkeypatch):
    from tradebot.models import Fill, Order, Position
    from tradebot.paper_expansion import reservations
    _, factory, sid, oid = queued(paper_fixture, config_updates={"max_trades_per_day": 1})
    with factory() as db:
        entry = db.get(Order, oid)
        limit, requested = entry.price, entry.quantity
        initial_margin, initial_risk = reservations(db)
    advance(factory, sid, oid, monkeypatch, low=limit)
    with factory() as db:
        assert db.get(Order, oid).status == "OPEN"
        assert list(db.scalars(select(Fill))) == []
    advance(factory, sid, oid, monkeypatch, low=limit-50)
    with factory() as db:
        order = db.get(Order, oid)
        position_id = order.position_id
        assert order.status == "PARTIALLY_FILLED"
        assert db.get(Position, position_id).quantity == D(10)
        margin, risk = reservations(db)
        assert D(0) < margin < initial_margin
        assert D(0) < risk < initial_risk
        assert_balanced(db)
    # A new database session represents a worker restart: the same intent and
    # ownership are resumed, rather than submitting a replacement entry.
    advance(factory, sid, oid, monkeypatch, low=limit-50, volume=D(1000))
    with factory() as db:
        assert db.get(Order, oid).status == "FILLED"
        assert len(list(db.scalars(select(Position)))) == 1
        assert db.get(Position, position_id).quantity == requested
        fills = list(db.scalars(select(Fill).where(Fill.order_id == oid)))
        assert len(fills) == 2 and sum((fill.quantity for fill in fills), D(0)) == requested
        assert reservations(db) == (D(0), D(0))
        assert_balanced(db)


@pytest.mark.parametrize("gate", ["emergency", "pause", "version", "downtime", "unhealthy"])
def test_pending_entry_gates_release_reservations_without_exposure(paper_fixture, monkeypatch, gate):
    from tradebot.models import Candle, Order, PaperAccount, Position, Strategy
    from tradebot.paper_expansion import process_pending, reservations
    _, factory, sid, oid = queued(paper_fixture)
    with factory() as db:
        if gate == "emergency":
            db.get(PaperAccount, 1).emergency_stop = True
        elif gate == "pause":
            db.get(Strategy, sid).status = "PAUSED"
        elif gate == "version":
            db.get(Strategy, sid).approved_version = 2
        db.commit()
    if gate == "unhealthy":
        from tradebot import paper_expansion
        original = paper_expansion.process_pending
        monkeypatch.setattr(paper_expansion, "process_pending", lambda db, history, **kwargs: original(db, history, executor_healthy=False))
    bar = advance(factory, sid, oid, monkeypatch, skip=1 if gate == "downtime" else 0)
    with factory() as db:
        order = db.get(Order, oid)
        assert order.status in ("CANCELED", "REJECTED")
        assert reservations(db) == (D(0), D(0))
        assert list(db.scalars(select(Position))) == []
        # Repeating a terminal transition remains harmless.
        process_pending(db, {sid: [db.get(Candle, bar)]}, executor_healthy=True)
        db.commit()
        assert_balanced(db)


def test_cancel_after_partial_fill_retains_exact_protection(paper_fixture, monkeypatch):
    from tradebot.models import Order, Position
    _, factory, sid, oid = queued(paper_fixture)
    advance(factory, sid, oid, monkeypatch)
    with factory() as db:
        from tradebot.paper_expansion import cancel_pending, reservations
        pending = db.get(PaperPendingEntry, oid)
        position = db.get(Position, db.get(Order, oid).position_id)
        cancel_pending(db, pending)
        db.commit()
        assert db.get(Order, oid).status == "CANCELED"
        assert reservations(db) == (D(0), D(0))
        brackets = list(db.scalars(select(Order).where(Order.position_id == position.id, Order.reduce_only.is_(True), Order.status == "OPEN")))
        assert len(brackets) == 2
        assert all(order.quantity == position.quantity and order.side == "SELL" for order in brackets)
        assert_balanced(db)


@pytest.mark.parametrize("fault", ["missing", "stale", "offset"])
def test_partial_fill_rechecks_operational_clock_and_retains_protection(paper_fixture, monkeypatch, fault):
    from tradebot.data_models import TelemetryState
    from tradebot.db import utcnow
    from tradebot.models import Fill, Order, PaperAccount, Position, Signal
    from tradebot.paper_expansion import reservations
    _, factory, sid, oid = queued(paper_fixture)
    advance(factory, sid, oid, monkeypatch)
    with factory() as db:
        position = db.get(Position, db.get(Order, oid).position_id)
        pid, quantity, cash = position.id, position.quantity, db.get(PaperAccount, 1).cash
        from tradebot_delta.clock import monotonic_domain
        row = db.get(TelemetryState, "clock:production:"+monotonic_domain())
        if fault == "missing":
            db.delete(row)
        elif fault == "stale":
            row.payload = {**row.payload, "last_usable": {**row.payload["last_usable"],
                "sampled_at": (utcnow()-timedelta(seconds=121)).isoformat()}}
        else:
            row.payload = {**row.payload, "blocking_observation": {"issues": ["CLOCK_OFFSET_EXCEEDED"]}}
        db.commit()
    advance(factory, sid, oid, monkeypatch)
    with factory() as db:
        position = db.get(Position, pid)
        order = db.get(Order, oid)
        assert order.status == "REJECTED"
        assert "CLOCK_UNHEALTHY" in db.get(Signal, order.signal_id).reason
        assert position.status == "OPEN" and position.quantity == quantity
        assert db.get(PaperAccount, 1).cash == cash and reservations(db) == (D(0), D(0))
        assert len(list(db.scalars(select(Fill).where(Fill.order_id == oid)))) == 1
        brackets = list(db.scalars(select(Order).where(Order.position_id == pid,
                            Order.reduce_only.is_(True), Order.status == "OPEN")))
        assert len(brackets) == 2 and all(order.quantity == quantity for order in brackets)
        assert_balanced(db)


def open_position(paper_fixture, *, rules=None):
    from tradebot.models import Position
    from tradebot.worker import process_paper
    client, factory, sid = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        state = db.get(PaperPositionState, position.id)
        state.policy = {**state.policy, "exit_rules": {"opposite_signal_exit": False, **(rules or {})}}
        db.commit()
        return client, factory, sid, position.id


def protected_bar(db, position, *, close, high=None, low=None, open_price=None):
    from tradebot.db import aware
    from tradebot.models import Candle
    timestamp = aware(position.last_protection_candle_at)+timedelta(minutes=1)
    position.opened_at = min(aware(position.opened_at), timestamp)
    bar = Candle(symbol=position.symbol, timeframe_seconds=60, timestamp=timestamp,
                 open=open_price if open_price is not None else close,
                 high=high if high is not None else close+10,
                 low=low if low is not None else close-10, close=close, volume=D(100))
    db.add(bar)
    return bar


def test_multi_targets_reduce_owned_quantity_and_replay_once(paper_fixture, monkeypatch):
    from tradebot import paper
    from tradebot.db import aware
    from tradebot.models import Fill, Order, Position
    _, factory, sid, pid = open_position(paper_fixture, rules={"targets": [{"reward_multiple": "1", "fraction": ".5"}, {"reward_multiple": "2", "fraction": ".5"}]})
    with factory() as db:
        position = db.get(Position, pid)
        original = position.quantity
        risk = position.entry_price-position.stop_price
        first_target = position.entry_price+risk
        bar = protected_bar(db, position, close=first_target+10, low=position.entry_price+20)
        now = aware(bar.timestamp)+timedelta(minutes=1, seconds=1)
        monkeypatch.setattr(paper, "utcnow", lambda: now)
        paper.protect_positions(db, {sid: [bar]})
        db.commit()
        assert position.status == "OPEN"
        assert D(0) < position.quantity < original
        assert db.get(PaperPositionState, pid).completed_targets == [0]
        fills_before = len(list(db.scalars(select(Fill))))
        paper.protect_positions(db, {sid: [bar]})
        db.commit()
        assert len(list(db.scalars(select(Fill)))) == fills_before
        assert_balanced(db)
        final = protected_bar(db, position, close=position.entry_price+risk*2+10, low=first_target+1)
        now = aware(final.timestamp)+timedelta(minutes=1, seconds=1)
        paper.protect_positions(db, {sid: [bar, final]})
        db.commit()
        assert position.status == "CLOSED"
        assert not list(db.scalars(select(Order).where(Order.position_id == pid, Order.status == "OPEN")))
        assert_balanced(db)


def test_break_even_covers_fees_and_adverse_next_bar_exit(paper_fixture, monkeypatch):
    from tradebot import paper
    from tradebot.db import aware
    from tradebot.models import Fill, Position
    _, factory, sid, pid = open_position(paper_fixture, rules={"break_even_trigger_r": ".5"})
    with factory() as db:
        position = db.get(Position, pid)
        entry = position.entry_price
        risk = entry-position.stop_price
        favorable = protected_bar(db, position, close=entry+risk*D(".75"), low=entry-10)
        now = aware(favorable.timestamp)+timedelta(minutes=1, seconds=1)
        monkeypatch.setattr(paper, "utcnow", lambda: now)
        paper.protect_positions(db, {sid: [favorable]})
        db.commit()
        assert position.status == "OPEN"  # New stop is never applied retroactively.
        assert position.stop_price > entry
        old_stop = position.stop_price
        stopped = protected_bar(db, position, close=old_stop, low=old_stop-10, high=old_stop+20, open_price=old_stop+10)
        now = aware(stopped.timestamp)+timedelta(minutes=1, seconds=1)
        paper.protect_positions(db, {sid: [favorable, stopped]})
        db.commit()
        assert position.status == "CLOSED"
        fills = list(db.scalars(select(Fill).where(Fill.position_id == pid)))
        assert sum((fill.realized_pnl-fill.fee for fill in fills), D(0)) >= 0
        assert_balanced(db)


@pytest.mark.parametrize("scenario", ["gap", "liquidation_closer", "stop_closer"])
def test_liquidation_precedence_and_fee_snapshot(paper_fixture, monkeypatch, scenario):
    from tradebot import paper
    from tradebot.db import aware
    from tradebot.models import EquitySnapshot, PaperAccount, Position
    from tradebot_core import Instrument, Side
    from tradebot_core.simulation import isolated_liquidation_price
    _, factory, sid, pid = open_position(paper_fixture)
    with factory() as db:
        position = db.get(Position, pid)
        state = db.get(PaperPositionState, pid)
        maintenance = D(".49") if scenario == "liquidation_closer" else D(".1")
        fee_rate = D(".005") if scenario == "liquidation_closer" else D(".01")
        state.policy = {**state.policy, "execution": {"maintenance_margin_rate": str(maintenance), "liquidation_fee_rate": str(fee_rate)}}
        economics = Instrument.model_validate(position.instrument_snapshot)
        liquidation = isolated_liquidation_price(instrument=economics, side=Side.BUY, entry_price=position.entry_price,
                          quantity=position.quantity, isolated_margin=position.margin, funding_paid=D(0),
                          maintenance_margin_rate=maintenance, liquidation_fee_rate=fee_rate)
        price = liquidation-100
        opened = price if scenario == "gap" else position.entry_price-10
        closed = price if scenario != "stop_closer" else position.entry_price-10
        bar = protected_bar(db, position, close=closed, open_price=opened, low=price-10, high=max(opened, closed)+10)
        now = aware(bar.timestamp)+timedelta(minutes=1, seconds=1)
        monkeypatch.setattr(paper, "utcnow", lambda: now)
        paper.protect_positions(db, {sid: [bar]})
        db.commit()
        assert position.status == "CLOSED"
        fee = db.scalar(select(PaperMovement).where(PaperMovement.kind == "LIQUIDATION_FEE"))
        if scenario == "stop_closer":
            assert fee is None
        else:
            assert fee is not None and fee.amount < 0
        account = db.get(PaperAccount, 1)
        latest = db.scalar(select(EquitySnapshot).order_by(EquitySnapshot.id.desc()))
        assert latest.cash == account.cash
        assert latest.equity == account.cash
        assert_balanced(db)


def test_partial_exit_allocates_old_funding_and_new_events_to_remaining_lots(paper_fixture):
    from tradebot.db import aware
    from tradebot.models import Position
    from tradebot.paper_expansion import apply_funding_through, close_partial
    _, factory, _, pid = open_position(paper_fixture)
    with factory() as db:
        position = db.get(Position, pid)
        original = position.quantity
        timestamp = aware(position.opened_at)
        event = PaperFundingEvent(id="before-partial", symbol=position.symbol, timestamp=timestamp,
                                 rate=D(".001"), mark_price=position.mark_price, source="Declared synthetic funding")
        db.add(event)
        db.flush()
        apply_funding_through(db, position, timestamp)
        state = db.get(PaperPositionState, pid)
        initial_paid = state.funding_paid
        quantity = (original/D(2)).to_integral_value(rounding="ROUND_FLOOR")
        close_partial(db, position, quantity, position.mark_price, "TEST_PARTIAL", execution_at=timestamp)
        first_remaining = initial_paid*position.quantity/original
        assert D(state.entry_observations["isolated_funding_paid"]) == first_remaining
        later = PaperFundingEvent(id="after-partial", symbol=position.symbol, timestamp=timestamp+timedelta(seconds=30),
                                 rate=D(".001"), mark_price=position.mark_price, source="Declared synthetic funding")
        db.add(later)
        db.flush()
        apply_funding_through(db, position, later.timestamp)
        db.commit()
        second_paid = initial_paid*position.quantity/original
        assert D(state.entry_observations["isolated_funding_paid"]) == first_remaining+second_paid
        assert state.funding_paid == initial_paid+second_paid
        assert_balanced(db)


def test_pending_market_gap_cannot_exceed_strategy_capital_allocation(paper_fixture, monkeypatch):
    from tradebot.models import Order, Position
    _, factory, sid, oid = queued(paper_fixture, execution={"entry_type": "MARKET"}, config_updates={"capital_allocation_fraction": ".1"})
    advance(factory, sid, oid, monkeypatch, close=D(65000), low=D(64900), high=D(65100), volume=D(1000))
    with factory() as db:
        assert db.get(Order, oid).status == "REJECTED"
        assert list(db.scalars(select(Position))) == []
        assert_balanced(db)


def test_strategy_funding_loss_blocks_next_partial_fill_but_retains_protection(paper_fixture, monkeypatch):
    from tradebot.db import aware
    from tradebot.models import Order, Position
    from tradebot.paper_expansion import apply_funding_through
    _, factory, sid, oid = queued(paper_fixture, config_updates={"max_daily_loss_fraction": ".0001"})
    advance(factory, sid, oid, monkeypatch)
    with factory() as db:
        position = db.get(Position, db.get(Order, oid).position_id)
        assert position.quantity == D(10)
        at = aware(position.opened_at)
        db.add(PaperFundingEvent(id="strategy-limit-funding", symbol=position.symbol, timestamp=at,
                                rate=D(".01"), mark_price=position.mark_price, source="Declared synthetic financing loss"))
        db.flush()
        apply_funding_through(db, position, at)
        db.commit()
    advance(factory, sid, oid, monkeypatch)
    with factory() as db:
        order = db.get(Order, oid)
        assert order.status == "REJECTED"
        position = db.get(Position, order.position_id)
        assert position.quantity == D(10) and position.protection_verified
        assert_balanced(db)


def test_zero_volume_pending_expiry_never_fabricates_a_fill(paper_fixture, monkeypatch):
    from tradebot.models import Fill, Order
    from tradebot.paper_expansion import reservations
    _, factory, sid, oid = queued(paper_fixture)
    for _ in range(4):
        advance(factory, sid, oid, monkeypatch, volume=D(0))
    with factory() as db:
        assert db.get(Order, oid).status == "EXPIRED"
        assert list(db.scalars(select(Fill))) == []
        assert reservations(db) == (D(0), D(0))
        assert_balanced(db)


@pytest.mark.parametrize("observation,expected", [("prior_range", "OPEN"), ("closed_beyond_stop", "CLOSED"), ("closed_beyond_target", "CLOSED")])
def test_end_bar_limit_entry_protection_uses_its_close(paper_fixture, monkeypatch, observation, expected):
    from tradebot.models import Order, Position
    from tradebot.paper_expansion import reservations
    _, factory, sid, oid = queued(paper_fixture)
    with factory() as db:
        pending = db.get(PaperPendingEntry, oid)
        limit = db.get(Order, oid).price
        stop, target = pending.stop_price, pending.target_price
    price = limit+50
    if observation == "closed_beyond_stop":
        price = stop-50
    elif observation == "closed_beyond_target":
        price = target+50
    advance(factory, sid, oid, monkeypatch, open_price=limit+50, close=price,
            low=min(stop-50, price), high=max(target+50, price), volume=D(20))
    with factory() as db:
        order = db.get(Order, oid)
        position = db.get(Position, order.position_id)
        assert position.status == expected
        if expected == "CLOSED":
            assert order.status == "CANCELED"  # Filled tranche is retained, remainder canceled.
            assert reservations(db) == (D(0), D(0))
        else:
            assert order.status == "PARTIALLY_FILLED"
        assert_balanced(db)
