"""Financial fault checks: declared synthetic bars, no exchange writes."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from tradebot.expansion_models import PaperFundingEvent, PaperMovement, PaperPendingEntry
from tradebot.news_models import NewsSource

D = Decimal


def _queue_fixture(factory, strategy_id, *, other_reservations=False):
    from tradebot.db import aware
    from tradebot.models import Candle, Instrument, Order, Signal
    from tradebot.paper import core_instrument, enter_signal
    from tradebot.repository import core_candles, version
    from tradebot.models import Strategy
    from tradebot_core import StrategyConfig, evaluate_strategy
    with factory() as db:
        strategy = db.get(Strategy, strategy_id)
        raw = version(db, strategy).config
        config = StrategyConfig.model_validate({**raw, "execution": {"volume_participation_fraction": ".5"}})
        for bar in db.scalars(select(Candle)):
            bar.timestamp -= timedelta(minutes=1)
        db.flush()
        rows = list(db.scalars(select(Candle).order_by(Candle.timestamp)))
        instrument = core_instrument(db.scalar(select(Instrument)))
        if other_reservations:
            for symbol in ("ETHUSD", "SOLUSD"):
                identifier = uuid4().hex
                owner = Strategy(name="Reserved " + symbol, status="RUNNING", approved_version=1)
                db.add(owner)
                db.flush()
                db.add(Signal(id=identifier, strategy_id=owner.id, version=1, symbol=symbol, side="BUY", candle_time=rows[-1].timestamp))
                db.flush()
                db.add(Order(id=identifier, client_order_id=identifier, signal_id=identifier, strategy_id=owner.id,
                             account_id=1, symbol=symbol, side="BUY", order_type="LIMIT", status="OPEN",
                             quantity=D(10), price=D(100), reduce_only=False, mode="PAPER"))
                db.flush()
                db.add(PaperPendingEntry(order_id=identifier, signal_id=identifier, strategy_id=owner.id,
                            config=config.model_dump(mode="json"), instrument=instrument.model_dump(mode="json"),
                            stop_price=D(50), target_price=D(200), reserved_margin=D(100), reserved_risk=D(95),
                            filled_quantity=D(0), last_candle_at=rows[-1].timestamp,
                            expires_at=aware(rows[-1].timestamp)+timedelta(minutes=10)))
            db.flush()
        generated = evaluate_strategy(config, core_candles(rows))
        assert generated is not None
        enter_signal(db, strategy_id, config, generated, rows[-1], executor_healthy=True)
        db.commit()
        queued = db.scalar(select(Order).where(Order.symbol == "BTCUSD", Order.status == "OPEN", Order.reduce_only.is_(False)))
        assert queued is not None
        latest = aware(rows[-1].timestamp)
        return queued.id, latest


def _fill_bar(factory, timestamp, *, price=D(65000)):
    from tradebot.models import Candle
    with factory() as db:
        bar = Candle(symbol="BTCUSD", timeframe_seconds=60, timestamp=timestamp+timedelta(minutes=1),
                     open=price, high=price+100, low=price-100, close=price, volume=D(100))
        db.add(bar)
        db.commit()
        return bar.id


def test_partial_gap_fill_rechecks_remaining_reserved_risk(paper_fixture):
    from tradebot.models import Candle, Order, PaperAccount, Position
    from tradebot.paper_expansion import process_pending, reservations
    _, factory, strategy_id = paper_fixture
    identifier, latest = _queue_fixture(factory, strategy_id, other_reservations=True)
    bar_id = _fill_bar(factory, latest)
    with factory() as db:
        process_pending(db, {strategy_id: [db.get(Candle, bar_id)]}, executor_healthy=True)
        db.commit()
        # The tranche itself fits 1% risk. Including its still-pending remainder
        # and other reservations breaches the 3% total, so no exposure is valid.
        assert db.get(Order, identifier).status == "REJECTED"
        assert list(db.scalars(select(Position))) == []
        assert db.get(PaperAccount, 1).cash == D(10000)
        assert reservations(db)[1] == D(190)


def test_queued_entry_rechecks_source_health_at_fill(paper_fixture):
    from tradebot.models import Candle, Order, Position
    from tradebot.paper_expansion import process_pending
    _, factory, strategy_id = paper_fixture
    identifier, latest = _queue_fixture(factory, strategy_id)
    bar_id = _fill_bar(factory, latest, price=D(63950))
    with factory() as db:
        # The source became required and stale after queuing the approved intent.
        db.add(NewsSource(name="Fault test stale source", authorized_by="fixture", enabled=True, required_for_trading=True))
        db.commit()
        process_pending(db, {strategy_id: [db.get(Candle, bar_id)]}, executor_healthy=True)
        db.commit()
        assert db.get(Order, identifier).status == "REJECTED"
        assert list(db.scalars(select(Position))) == []


def test_manual_close_applies_due_funding_once(paper_fixture):
    from tradebot.db import aware
    from tradebot.models import PaperAccount, Position
    from tradebot.paper import close_position, reconcile
    from tradebot.worker import process_paper
    from tradebot_core import Instrument
    _, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        assert position is not None
        event = PaperFundingEvent(id="due-funding-fixture", symbol=position.symbol,
                   timestamp=aware(position.opened_at)+timedelta(microseconds=1), rate=D(".001"),
                   mark_price=position.mark_price, source="Explicit synthetic fault fixture")
        db.add(event)
        db.commit()
        close_position(db, position, position.mark_price)
        db.commit()
        movement = db.scalar(select(PaperMovement).where(PaperMovement.kind == "FUNDING"))
        assert movement is not None
        economics = Instrument.model_validate(position.instrument_snapshot)
        assert movement.amount == -economics.notional(event.mark_price, position.quantity)*event.rate
        assert reconcile(db, db.get(PaperAccount, 1)) == []
        count = len(list(db.scalars(select(PaperMovement))))
        close_position(db, position, position.mark_price)
        db.commit()
        assert len(list(db.scalars(select(PaperMovement)))) == count


def test_replayed_time_exit_does_not_charge_later_funding(paper_fixture):
    from tradebot.db import utcnow
    from tradebot.expansion_models import PaperPositionState
    from tradebot.models import Candle, Position
    from tradebot.paper import protect_positions
    from tradebot.worker import process_paper
    _, factory, strategy_id = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        cursor = utcnow().replace(second=0, microsecond=0)-timedelta(minutes=4)
        position.opened_at = cursor-timedelta(minutes=1)
        position.last_protection_candle_at = cursor
        state = db.get(PaperPositionState, position.id)
        state.policy = {**state.policy, "exit_rules": {"max_holding_bars": 1, "opposite_signal_exit": False}}
        timestamp = cursor+timedelta(minutes=1)
        price = position.entry_price
        bar = db.scalar(select(Candle).where(Candle.symbol == position.symbol, Candle.timestamp == timestamp))
        assert bar is not None
        bar.open, bar.high, bar.low, bar.close, bar.volume = price, price+10, price-10, price, D(100)
        # This event is due in wall-clock time, but follows the modeled exit.
        db.add(PaperFundingEvent(id="post-replayed-exit", symbol=position.symbol,
                   timestamp=timestamp+timedelta(minutes=2), rate=D(".001"),
                   mark_price=price, source="Explicit synthetic fault fixture"))
        db.commit()
        protect_positions(db, {strategy_id: [bar]})
        db.commit()
        assert position.status == "CLOSED"
        assert list(db.scalars(select(PaperMovement).where(PaperMovement.kind == "FUNDING"))) == []
