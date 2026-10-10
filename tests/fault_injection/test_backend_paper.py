"""Explicit synthetic fault injection; no exchange private endpoint is called."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select


def test_protection_rejection_rolls_back_all_exposure(paper_fixture, monkeypatch):
    from tradebot import paper
    from tradebot.models import Fill, Order, PaperAccount, Position, Signal
    from tradebot.worker import process_paper
    _, factory, _ = paper_fixture
    real_order = paper._order

    def reject_stop(*args, **kwargs):
        if kwargs["kind"] == "STOP_MARKET":
            raise paper.PaperRejected("Injected simulated protective stop rejection")
        return real_order(*args, **kwargs)

    monkeypatch.setattr(paper, "_order", reject_stop)
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
    with factory() as db:
        assert list(db.scalars(select(Position))) == []
        assert list(db.scalars(select(Order))) == []
        assert list(db.scalars(select(Fill))) == []
        assert db.get(PaperAccount, 1).cash == 10000
        signal = db.scalar(select(Signal))
        assert signal.status == "REJECTED"
        assert "stop rejection" in signal.reason


@pytest.mark.parametrize("field,value", [("side", "BUY"), ("price", Decimal("1")), ("quantity", Decimal("1")), ("mode", "LIVE")])
def test_restart_reconciliation_blocks_wrong_protection(paper_fixture, field, value):
    from tradebot.models import Order, PaperAccount
    from tradebot.paper import reconcile
    from tradebot.worker import process_paper
    _, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
    with factory() as db:
        stop = db.scalar(select(Order).where(Order.order_type == "STOP_MARKET"))
        setattr(stop, field, value)
        db.commit()
    with factory() as db:
        issues = reconcile(db, db.get(PaperAccount, 1))
        assert any("protection" in issue for issue in issues)
        assert db.get(PaperAccount, 1).reconciliation_required is True


def test_cash_mismatch_is_detected_without_overwriting(paper_fixture):
    from tradebot.models import PaperAccount
    from tradebot.paper import reconcile
    _, factory, _ = paper_fixture
    with factory() as db:
        account = db.get(PaperAccount, 1)
        account.cash += Decimal("42")
        db.commit()
    with factory() as db:
        account = db.get(PaperAccount, 1)
        assert any("ledger mismatch" in issue for issue in reconcile(db, account))
        assert account.cash == Decimal("10042")
        assert account.reconciliation_required is True


def test_duplicate_fill_evidence_blocks_accounting(paper_fixture):
    from tradebot.models import Fill, PaperAccount
    from tradebot.worker import process_paper
    _, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        fill = db.scalar(select(Fill))
        cash = db.get(PaperAccount, 1).cash
        db.add(Fill(id="duplicate-synthetic-fill", order_id=fill.order_id, position_id=fill.position_id,
                    quantity=fill.quantity, price=fill.price, fee=fill.fee))
        db.commit()
        from tradebot.paper import reconcile
        account = db.get(PaperAccount, 1)
        issues = reconcile(db, account)
        assert any("mismatch" in issue for issue in issues)
        assert account.reconciliation_required
        assert db.get(PaperAccount, 1).cash == cash
        assert len(list(db.scalars(select(Fill)))) == 2


def test_unknown_order_blocks_recovery(paper_fixture):
    from tradebot.models import Order, PaperAccount
    from tradebot.paper import reconcile
    from tradebot.worker import process_paper
    _, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        entry = db.scalar(select(Order).where(Order.order_type == "MARKET"))
        entry.status = "UNKNOWN"
        db.commit()
    with factory() as db:
        assert "Unresolved UNKNOWN paper order" in reconcile(db, db.get(PaperAccount, 1))


def test_stale_and_unhealthy_data_rejects_new_exposure(paper_fixture):
    from tradebot.models import Candle, Position, Signal
    from tradebot.worker import process_paper
    _, factory, _ = paper_fixture
    with factory() as db:
        for candle in db.scalars(select(Candle)):
            candle.timestamp -= timedelta(hours=1)
        db.commit()
        process_paper(db, executor_healthy=True)
        db.commit()
        assert list(db.scalars(select(Position))) == []
        assert "stale" in db.scalar(select(Signal)).reason.lower()


def test_market_fetch_failure_blocks_entries(paper_fixture):
    from tradebot.models import Position, Signal
    from tradebot.worker import process_paper
    _, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=False)
        db.commit()
        assert list(db.scalars(select(Position))) == []
        assert "Healthy" in db.scalar(select(Signal)).reason


def test_executor_lease_recovery_never_has_two_owners(paper_fixture):
    from tradebot.db import utcnow
    from tradebot.models import ExecutorLease
    from tradebot.paper import claim_lease
    _, factory, _ = paper_fixture
    with factory() as db:
        assert claim_lease(db, "worker-a") is True
        db.commit()
    with factory() as db:
        assert claim_lease(db, "worker-b") is False
        lease = db.get(ExecutorLease, 1)
        lease.expires_at = utcnow()-timedelta(seconds=1)
        db.commit()
    with factory() as db:
        assert claim_lease(db, "worker-b") is True
        db.commit()
        assert db.get(ExecutorLease, 1).owner == "worker-b"


def test_daily_risk_includes_unrealized_loss_and_retains_stops(paper_fixture):
    from tradebot.models import Candle, Order, Position, RiskConfig, Strategy
    from tradebot.worker import process_paper
    _, factory, sid = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        # Synthetic current bar closes lower after entry; do not manufacture
        # pre-entry intrabar stop execution. Mark-to-market still gates risk.
        candle = db.scalar(select(Candle).order_by(Candle.timestamp.desc()))
        candle.close = position.entry_price-Decimal("2000")
        candle.low = candle.close-1
        risk = db.get(RiskConfig, 1)
        risk.max_daily_loss = Decimal("0.001")
        db.commit()
        process_paper(db, executor_healthy=True)
        db.commit()
        assert risk.entries_enabled is False
        assert db.get(Strategy, sid).status == "PAUSED"
        assert position.status == "OPEN"
        assert len(list(db.scalars(select(Order).where(Order.status == "OPEN", Order.reduce_only.is_(True))))) == 2


def test_emergency_stop_retains_open_position_and_protection(paper_fixture):
    from tradebot.worker import process_paper
    client, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
    response = client.post("/api/v1/emergency-stop", json={"action": "BLOCK_ENTRIES"})
    assert response.status_code == 200
    assert response.json()["open_positions"] == 1
    assert response.json()["emergency_stop"] is True
    assert len([order for order in client.get("/api/v1/orders").json() if order["status"] == "OPEN"]) == 2
    assert client.post("/api/v1/emergency-stop", json={"action": "RESUME_ENTRIES"}).status_code == 409


def test_downtime_replays_earlier_stop_even_if_latest_bar_recovers(paper_fixture):
    from tradebot.db import aware
    from tradebot.models import Candle, Fill, Position
    from tradebot.worker import process_paper
    _, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        bars = list(db.scalars(select(Candle).order_by(Candle.timestamp.desc()).limit(6)))
        bars.reverse()
        position.opened_at = aware(bars[0].timestamp)+timedelta(seconds=10)
        position.last_protection_candle_at = bars[0].timestamp
        breached = bars[2]
        breached.low = position.stop_price-Decimal("100")
        # Latest bar recovers above the stop: checking latest alone misses it.
        assert bars[-1].low > position.stop_price
        db.commit()
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        assert position.status == "CLOSED"
        assert len(list(db.scalars(select(Fill)))) == 2
        # Replaying a second worker recovery cannot repeat the exit fill.
        process_paper(db, executor_healthy=True)
        db.commit()
        assert len(list(db.scalars(select(Fill)))) == 2


def test_missing_protective_candle_blocks_new_exposure(paper_fixture):
    from tradebot.db import aware
    from tradebot.models import Candle, PaperAccount, Position
    from tradebot.worker import process_paper
    _, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        bars = list(db.scalars(select(Candle).order_by(Candle.timestamp.desc()).limit(5)))
        bars.reverse()
        position.opened_at = aware(bars[0].timestamp)+timedelta(seconds=10)
        position.last_protection_candle_at = bars[0].timestamp
        db.delete(bars[1])
        db.commit()
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        assert db.get(PaperAccount, 1).reconciliation_required is True
        assert db.scalar(select(Position)).status == "OPEN"


def test_inactive_other_timeframe_cannot_mask_owned_position_stop(paper_fixture):
    from tradebot.db import aware
    from tradebot.models import Candle, Position
    from tradebot.worker import process_paper
    client, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        position = db.scalar(select(Position))
        bars = list(db.scalars(select(Candle).order_by(Candle.timestamp.desc()).limit(4)))
        bars.reverse()
        position.opened_at = aware(bars[0].timestamp)+timedelta(seconds=1)
        position.last_protection_candle_at = bars[0].timestamp
        bars[2].low = position.stop_price-Decimal("100")
        # A second non-running same-symbol configuration has a 15-minute bar
        # above the stop; it cannot replace the owning 1-minute history.
        timestamp = aware(bars[-1].timestamp).replace(minute=(aware(bars[-1].timestamp).minute//15)*15)
        db.add(Candle(symbol="BTCUSD", timeframe_seconds=900, timestamp=timestamp,
                      open=position.entry_price, high=position.entry_price+1, low=position.entry_price-1,
                      close=position.entry_price, volume=100))
        db.commit()
    response = client.post("/api/v1/strategies", json={"name": "Inactive BTC 15m", "config": {"symbol": "BTCUSD", "timeframe_seconds": 900}})
    assert response.status_code == 201
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        assert db.scalar(select(Position)).status == "CLOSED"
