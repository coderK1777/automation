"""Optional PAPER strategy gates use attributed costs and observed baselines."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from tradebot.strategy_risk_models import StrategyRiskState

D = Decimal


def configured(paper_fixture, **controls):
    from tradebot.models import Strategy
    from tradebot.repository import version
    from tradebot_core import StrategyConfig
    client, factory, sid = paper_fixture
    with factory() as db:
        saved = version(db, db.get(Strategy, sid))
        config = StrategyConfig.model_validate({**saved.config, **controls})
        # Declared fixture configuration, never a mutation of application data.
        saved.config = config.model_dump(mode="json")
        db.commit()
    return client, factory, sid, config


def test_gross_win_is_a_consecutive_loss_after_actual_fees(paper_fixture):
    from tradebot.models import Fill, PaperAccount, Position
    from tradebot.paper import close_position
    from tradebot.strategy_risk import strategy_risk_reasons
    from tradebot.worker import process_paper
    _, factory, sid, config = configured(paper_fixture, max_consecutive_losses=1)
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        close_position(db, position, position.entry_price+50)
        db.commit()
        fills = list(db.scalars(select(Fill).where(Fill.position_id == position.id)))
        assert sum((fill.realized_pnl for fill in fills), D(0)) > 0
        assert sum((fill.realized_pnl-fill.fee for fill in fills), D(0)) < 0
        reasons = strategy_risk_reasons(db, sid, config, db.get(PaperAccount, 1))
        assert "STRATEGY_CONSECUTIVE_LOSS_LIMIT" in reasons


def test_observed_strategy_drawdown_peak_survives_session_restart(paper_fixture):
    from tradebot.models import PaperAccount, Position
    from tradebot.paper import close_position, snapshot
    from tradebot.strategy_risk import strategy_risk_reasons
    from tradebot.worker import process_paper
    _, factory, sid, config = configured(paper_fixture, max_drawdown_fraction=".005", capital_allocation_fraction=".1")
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        state = db.get(StrategyRiskState, (sid, 1))
        assert position is not None and state.baseline_equity == D(1000)
        position.mark_price = position.entry_price+200
        snapshot(db, db.get(PaperAccount, 1))
        db.commit()
        assert state.peak_equity > state.baseline_equity
        peak = state.peak_equity
        close_position(db, position, position.entry_price-100)
        db.commit()
    with factory() as db:
        state = db.get(StrategyRiskState, (sid, 1))
        assert state.peak_equity == peak
        assert state.current_equity < state.baseline_equity
        assert "STRATEGY_DRAWDOWN_LIMIT" in strategy_risk_reasons(db, sid, config, db.get(PaperAccount, 1))


def test_drawdown_control_cannot_invent_missing_historical_peak(paper_fixture):
    from tradebot.models import PaperAccount, Position
    from tradebot.paper import close_position
    from tradebot.strategy_risk import strategy_risk_reasons
    from tradebot.worker import process_paper
    _, factory, sid, config = configured(paper_fixture)
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        close_position(db, position, position.entry_price+100)
        db.commit()
        constrained = config.model_copy(update={"max_drawdown_fraction": D(".1")})
        assert "STRATEGY_RISK_BASELINE_MISSING" in strategy_risk_reasons(db, sid, constrained, db.get(PaperAccount, 1))
        assert db.get(StrategyRiskState, (sid, 1)) is None


@pytest.mark.parametrize("minimum,expected", [(".02", False), (".005", True)])
def test_minimum_liquidation_distance_gates_immediate_exposure(paper_fixture, minimum, expected):
    from tradebot.models import Position, Signal
    from tradebot.worker import process_paper
    _, factory, _, _ = configured(paper_fixture, min_liquidation_distance_fraction=minimum,
                                   execution={"maintenance_margin_rate": ".49", "liquidation_fee_rate": ".005"})
    # Advanced policy queues its entry; the following final fill must recheck the
    # declared maintenance distance. First verify the explicit shared helper.
    from tradebot.models import Instrument, PaperAccount
    from tradebot.paper import core_instrument
    from tradebot.repository import version
    from tradebot.models import Strategy
    from tradebot.strategy_risk import strategy_liquidation_reasons
    from tradebot_core import Side, StrategyConfig, size_position
    with factory() as db:
        strategy = db.scalar(select(Strategy))
        config = StrategyConfig.model_validate(version(db, strategy).config)
        instrument = core_instrument(db.scalar(select(Instrument)))
        sizing = size_position(instrument=instrument, side=Side.BUY, entry_price=D(64000), stop_price=D(63300),
                     equity=db.get(PaperAccount, 1).cash, risk_fraction=config.risk_fraction, max_leverage=config.max_leverage)
        reasons = strategy_liquidation_reasons(db, config, instrument=instrument, side=Side.BUY, size=sizing)
        assert bool(reasons) is not expected
        if not expected:
            assert reasons == ["STRATEGY_LIQUIDATION_DISTANCE"]
        process_paper(db, executor_healthy=True)
        db.commit()
        assert list(db.scalars(select(Position))) == []  # Queued policies have not filled yet.
        if not expected:
            assert "STRATEGY_LIQUIDATION_DISTANCE" in db.scalar(select(Signal)).reason


def test_invalid_closed_fill_evidence_blocks_optional_loss_control(paper_fixture):
    from tradebot.models import Fill, PaperAccount, Position
    from tradebot.paper import close_position
    from tradebot.strategy_risk import strategy_risk_reasons
    from tradebot.worker import process_paper
    _, factory, sid, config = configured(paper_fixture, max_consecutive_losses=2)
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        close_position(db, position, position.entry_price)
        db.commit()
        exited = db.scalar(select(Fill).where(Fill.position_id == position.id, Fill.realized_pnl != 0))
        exited.realized_pnl += D(1)
        db.commit()
        assert "STRATEGY_LEDGER_INVALID" in strategy_risk_reasons(db, sid, config, db.get(PaperAccount, 1))


def test_funding_changes_a_positive_closed_trade_into_a_loss(paper_fixture):
    from tradebot.db import aware
    from tradebot.expansion_models import PaperFundingEvent
    from tradebot.models import Fill, PaperAccount, Position
    from tradebot.paper import close_position
    from tradebot.strategy_risk import strategy_risk_reasons
    from tradebot.worker import process_paper
    _, factory, sid, config = configured(paper_fixture, max_consecutive_losses=1)
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        db.add(PaperFundingEvent(id="streak-financing", symbol=position.symbol, timestamp=aware(position.opened_at),
                    rate=D(".005"), mark_price=position.entry_price, source="Declared synthetic strategy financing"))
        db.commit()
        close_position(db, position, position.entry_price+200)
        db.commit()
        fills = list(db.scalars(select(Fill).where(Fill.position_id == position.id)))
        assert sum((fill.realized_pnl-fill.fee for fill in fills), D(0)) > 0
        assert "STRATEGY_CONSECUTIVE_LOSS_LIMIT" in strategy_risk_reasons(db, sid, config, db.get(PaperAccount, 1))


def test_strategy_position_cap_does_not_count_partial_fill_as_new_position(paper_fixture):
    from tradebot.models import PaperAccount, Position
    from tradebot.strategy_risk import strategy_risk_reasons
    from tradebot.worker import process_paper
    _, factory, sid, config = configured(paper_fixture, max_open_positions=1)
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        account = db.get(PaperAccount, 1)
        assert "STRATEGY_POSITION_LIMIT" in strategy_risk_reasons(db, sid, config, account)
        assert "STRATEGY_POSITION_LIMIT" not in strategy_risk_reasons(db, sid, config, account, own_position=position)


def test_worker_suspends_drawdown_and_cancels_remainder_but_keeps_stops(paper_fixture, monkeypatch):
    from datetime import timedelta
    from tradebot import paper, paper_expansion
    from tradebot.db import aware
    from tradebot.expansion_models import PaperPendingEntry
    from tradebot.models import Audit, Fill, Notification, Order, Position, Strategy
    from tradebot.paper_expansion import reservations
    from tradebot.worker import process_paper
    from test_paper_expansion import advance, protected_bar

    _, factory, sid, _ = configured(paper_fixture, max_drawdown_fraction=".002",
                capital_allocation_fraction=".1", execution={"entry_type": "LIMIT", "volume_participation_fraction": ".5"},
                exit_rules={"opposite_signal_exit": False})
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        entry = db.scalar(select(Order).where(Order.reduce_only.is_(False)))
        oid = entry.id
    advance(factory, sid, oid, monkeypatch)
    with factory() as db:
        position = db.get(Position, db.get(Order, oid).position_id)
        pid = position.id
        assert db.get(Order, oid).status == "PARTIALLY_FILLED"
        assert db.get(PaperPendingEntry, oid).reserved_margin > 0
        adverse = protected_bar(db, position, close=position.entry_price-300)
        assert adverse.low > position.stop_price
        now = aware(adverse.timestamp)+timedelta(minutes=1, seconds=1)
        monkeypatch.setattr(paper, "utcnow", lambda: now)
        monkeypatch.setattr(paper_expansion, "utcnow", lambda: now)
        db.commit()
        process_paper(db, executor_healthy=True)
        db.commit()
        assert db.get(Strategy, sid).status == "PAUSED"
        assert db.get(Strategy, sid).last_error == "STRATEGY_DRAWDOWN_LIMIT"
        assert db.get(Order, oid).status == "CANCELED"
        assert reservations(db) == (D(0), D(0))
        assert position.status == "OPEN"
        assert len(list(db.scalars(select(Fill).where(Fill.order_id == oid)))) == 1
        brackets = list(db.scalars(select(Order).where(Order.position_id == pid,
                            Order.reduce_only.is_(True), Order.status == "OPEN")))
        assert len(brackets) == 2 and all(order.quantity == position.quantity for order in brackets)
        audits = list(db.scalars(select(Audit).where(Audit.action == "STRATEGY_RISK_SUSPEND")))
        assert len(audits) == 1 and audits[0].details["reasons"] == ["STRATEGY_DRAWDOWN_LIMIT"]
        assert db.scalar(select(Notification).where(Notification.message.contains("suspended by its risk policy")))

    # Suspension is durable; a replacement worker continues protective execution.
    with factory() as db:
        position = db.get(Position, pid)
        stopped = protected_bar(db, position, close=position.stop_price, low=position.stop_price-10,
                                high=position.stop_price+20, open_price=position.stop_price+10)
        now = aware(stopped.timestamp)+timedelta(minutes=1, seconds=1)
        db.commit()
        process_paper(db, executor_healthy=True)
        db.commit()
        assert position.status == "CLOSED" and db.get(Strategy, sid).status == "PAUSED"
        assert len(list(db.scalars(select(Fill).where(Fill.position_id == pid)))) == 2
        assert len(list(db.scalars(select(Audit).where(Audit.action == "STRATEGY_RISK_SUSPEND")))) == 1
