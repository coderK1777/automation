"""Controlled OFFLINE native-currency fixtures; no Delta acceptance evidence."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from tradebot.demo_models import DemoAccount, DemoCashMovement, DemoEquitySnapshot, DemoFill, DemoOrder, DemoPerformanceReport
from tradebot.demo_reporting import period_report, save_report, schedule_reports
from tradebot.reporting import period_bounds
from tradebot_core import Instrument

pytestmark = pytest.mark.mocked
D = Decimal


@pytest.fixture
def observed_demo(authenticated):
    client, factory = authenticated
    start, end = period_bounds("DAILY", date(2026, 10, 5))
    with factory() as db:
        account = DemoAccount(id=1, credential_fingerprint="OFFLINE_REPORT_FIXTURE", connected=True, settlement_asset="USD", equity=10001,
                              available_margin=10001, initial_equity=10000, peak_equity=10001, reconciliation_required=False, created_at=start+timedelta(minutes=1))
        db.add(account)
        contract = Instrument(product_id=84, symbol="BTCUSD", underlying_asset="BTC", quote_asset="USD", settlement_asset="USD",
                              contract_size=D(".001"), tick_size=D(".1"), quantity_step=D(1), min_quantity=D(1), max_quantity=D(10),
                              initial_margin_rate=D(".5"), metadata_verified=True, metadata_timestamp=start)
        # These are only explicit controlled fixtures; no app runtime seeding.
        from tradebot.demo_models import DemoProduct
        db.add(DemoProduct(id=84, symbol="BTCUSD", metadata_json={"source": "OFFLINE fixture"}))
        for identity, side, reduced, moment in (("offline-entry", "BUY", False, start+timedelta(hours=1)), ("offline-exit", "SELL", True, start+timedelta(hours=2))):
            db.add(DemoOrder(id=identity, idempotency_key=identity, client_order_id=identity, product_id=84, symbol="BTCUSD", side=side,
                              order_type="MARKET", status="FILLED", quantity=D(2), filled_quantity=D(2), reduce_only=reduced,
                              instrument_snapshot=contract.model_dump(mode="json"), created_at=moment))
        db.flush()
        for identity, order, quantity, price, fee, moment in (
            ("offline-fill-a", "offline-entry", "1", "80000", ".04", start+timedelta(hours=1)),
            ("offline-fill-b", "offline-entry", "1", "80000", ".04", start+timedelta(hours=1, seconds=1)),
            ("offline-fill-c", "offline-exit", "2", "81000", ".081", start+timedelta(hours=2)),
        ):
            db.add(DemoFill(id=identity, order_id=order, exchange_order_id=order, product_id=84,
                           side="BUY" if order == "offline-entry" else "SELL", quantity=D(quantity), price=D(price), fee=D(fee),
                           settlement_asset="USD", exchange_created_at=moment.isoformat()))
        db.add(DemoCashMovement(id="offline-funding", asset_id=1, asset_symbol="USD", amount=D("-.25"), balance=D("10001.589"),
                                transaction_type="funding", product_id=84, exchange_created_at=start+timedelta(hours=3), details={"source": "OFFLINE signed-amount fixture"}))
        for moment, equity in ((start+timedelta(minutes=1), D(10000)), (start+timedelta(hours=1), D("9999.92")), (end-timedelta(seconds=1), D("10001.589"))):
            db.add(DemoEquitySnapshot(equity=equity, available_margin=equity, created_at=moment))
        db.commit()
    yield client, factory, start, end


def test_mocked_demo_report_partial_fill_episode_actual_native_fees_and_funding(observed_demo):
    _, factory, start, end = observed_demo
    with factory() as db:
        result = period_report(db, "DAILY", date(2026, 10, 5), now=end+timedelta(minutes=2))
    assert result["mode"] == "DEMO" and result["simulated"] is False
    assert result["currency"] == "USD" and result["timezone"] == "Asia/Calcutta"
    assert result["coverage_start"] == (start+timedelta(minutes=1)).isoformat()
    assert result["partial_period"] is False and result["actual_order_acceptance_certified"] is False
    assert result["closed_episodes"] == 1 and result["fill_count"] == 3
    assert D(result["gross_realized_exit_pnl"]) == D(2)
    assert D(result["closed_trade_net_pnl_excluding_funding"]) == D("1.839")
    assert D(result["fees_by_currency"]["USD"]) == D(".161")
    assert D(result["funding_cashflow"]) == D("-.25")
    assert D(result["observed_equity_change"]) == D("1.589")
    assert result["profit_factor_excluding_funding"] is None  # zero loss denominator
    assert result["sharpe"] is None and result["annualized_return"] is None
    assert len(result["dataset_hash"]) == 64


def test_mocked_reports_never_merge_other_currency_fees_or_cashflows(observed_demo):
    _, factory, start, end = observed_demo
    with factory() as db:
        db.add(DemoCashMovement(id="offline-inr-funding", asset_id=2, asset_symbol="INR", amount=D(100), balance=D(100), transaction_type="funding", exchange_created_at=start+timedelta(hours=4)))
        db.add(DemoFill(id="offline-unowned-usdt", order_id=None, exchange_order_id="external", product_id=1699, side="BUY", quantity=D(1), price=D(2000), fee=D(".5"), settlement_asset="USDT", exchange_created_at=(start+timedelta(hours=4)).isoformat()))
        db.commit()
        result = period_report(db, "DAILY", date(2026, 10, 5), now=end+timedelta(minutes=2))
    assert result["cashflows_by_currency_and_type"]["INR:funding"] == "100.000000000000"
    assert D(result["funding_cashflow"]) == D("-.25")
    assert D(result["fees_by_currency"]["USDT"]) == D(".5")
    assert result["closed_episodes"] == 1
    assert any("other-currency" in issue for issue in result["coverage"])


def test_mocked_missing_history_leaves_boundaries_and_trade_metrics_null(observed_demo):
    _, factory, _, _ = observed_demo
    with factory() as db:
        result = period_report(db, "DAILY", date(2026, 9, 1))
    assert result["equity_start"] is None and result["equity_end"] is None
    assert result["observed_equity_change"] is None and result["sampled_max_drawdown"] is None
    assert result["win_rate"] is None and result["profit_factor_excluding_funding"] is None


def test_mocked_identical_report_is_idempotent_and_persisted_result_is_immutable(observed_demo):
    _, factory, _, end = observed_demo
    now = end+timedelta(minutes=2)
    with factory() as db:
        first = save_report(db, "DAILY", date(2026, 10, 5), "fixture-operator", now=now)
        db.commit()
        second = save_report(db, "DAILY", date(2026, 10, 5), "fixture-operator", now=now)
        assert first.id == second.id
        old_pnl = first.result["closed_trade_net_pnl_excluding_funding"]
        fee = db.get(DemoFill, "offline-fill-c")
        fee.fee = D(".2")  # fault fixture: would be rejected by real coordinator
        db.commit()
        revised = save_report(db, "DAILY", date(2026, 10, 5), "fixture-operator", now=now)
        db.commit()
        assert revised.id != first.id and revised.dataset_hash != first.dataset_hash
        assert first.result["closed_trade_net_pnl_excluding_funding"] == old_pnl


@pytest.mark.parametrize("period", ["DAILY", "WEEKLY", "MONTHLY"])
def test_mocked_demo_calendar_groups_and_exports_are_mode_labeled(observed_demo, period):
    client, _, _, _ = observed_demo
    response = client.post("/api/v1/demo/reports", json={"period": period, "date": "2026-10-05"})
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["mode"] == "DEMO" and report["result"]["period"] == period
    identity = report["id"]
    json_export = client.get(f"/api/v1/demo/reports/{identity}/export?format=json")
    assert json_export.status_code == 200 and json_export.json()["mode"] == "DEMO"
    csv_export = client.get(f"/api/v1/demo/reports/{identity}/export?format=csv")
    assert csv_export.status_code == 200 and "DEMO,USD" in csv_export.text
    assert "dataset_hash" in csv_export.text and "coverage" in csv_export.text
    assert client.get("/api/v1/demo/reports").json()[0]["id"] == identity


def test_demo_report_requires_connected_actual_account_without_synthetic_data(authenticated):
    client, _ = authenticated
    response = client.post("/api/v1/demo/reports", json={"period": "DAILY", "date": "2026-10-05"})
    assert response.status_code == 409
    assert client.get("/api/v1/demo/reports").json() == []


def test_mocked_daily_scheduler_requires_history_reconciliation_and_is_once(observed_demo):
    _, factory, _, end = observed_demo
    with factory() as db:
        schedule_reports(db, now=end+timedelta(minutes=2))
        db.commit()
        schedule_reports(db, now=end+timedelta(minutes=3))
        db.commit()
        daily = list(db.scalars(select(DemoPerformanceReport).where(DemoPerformanceReport.period == "DAILY")))
        assert len(daily) == 1
        db.get(DemoAccount, 1).reconciliation_required = True
        schedule_reports(db, now=end+timedelta(days=1, minutes=2))
        assert len(list(db.scalars(select(DemoPerformanceReport)))) == 1


def test_mocked_stale_opening_equity_is_explicit_preperiod_coverage(observed_demo):
    _, factory, start, end = observed_demo
    with factory() as db:
        db.get(DemoAccount, 1).created_at = start-timedelta(days=7)
        db.add(DemoEquitySnapshot(equity=D(9000), available_margin=D(9000), created_at=start-timedelta(days=2)))
        db.commit()
        result = period_report(db, "DAILY", date(2026, 10, 5), now=end+timedelta(minutes=2))
    assert result["opening_observation_at"] == (start-timedelta(days=2)).isoformat()
    assert any("Opening equity observation" in warning and "pre-period" in warning for warning in result["coverage"])


def test_mocked_partial_report_same_actual_observations_reuse_revision_across_wallclock(observed_demo):
    _, factory, start, _ = observed_demo
    with factory() as db:
        first = save_report(db, "DAILY", date(2026, 10, 5), "fixture", now=start+timedelta(hours=4))
        db.commit()
        later = save_report(db, "DAILY", date(2026, 10, 5), "fixture", now=start+timedelta(hours=4, minutes=1))
        assert later.id == first.id and later.dataset_hash == first.dataset_hash
        assert first.result["partial_period"] is True
        assert first.result["observed_through"] == (start+timedelta(hours=3)).isoformat()


@pytest.mark.parametrize("overlap", [False, True])
def test_mocked_report_cannot_attribute_other_entry_protection_to_current_episode(observed_demo, overlap):
    from tradebot.demo_models import DemoProtection
    _, factory, start, end = observed_demo
    with factory() as db:
        original = db.get(DemoOrder, "offline-entry")
        db.add(DemoOrder(id="offline-other-entry", idempotency_key="offline-other-entry", client_order_id="offline-other-entry",
                        product_id=84, symbol="BTCUSD", side="BUY", order_type="MARKET", quantity=D(2), filled_quantity=D(2),
                        status="FILLED", instrument_snapshot=original.instrument_snapshot))
        db.flush()
        if overlap:
            db.add(DemoFill(id="offline-overlap", order_id="offline-other-entry", exchange_order_id="other", product_id=84,
                           side="BUY", quantity=D(2), price=D(80000), fee=D(0), settlement_asset="USD",
                           exchange_created_at=(start+timedelta(hours=1, minutes=20)).isoformat()))
        db.add(DemoProtection(exchange_order_id=999, entry_order_id="offline-other-entry", product_id=84, kind="take_profit_order", stop_price=D(90000), status="FILLED"))
        db.flush()
        db.add(DemoFill(id="offline-other-bracket-fill", protection_order_id=999, exchange_order_id="999", product_id=84,
                       side="SELL", quantity=D(2), price=D(90000), fee=D(0), settlement_asset="USD",
                       exchange_created_at=(start+timedelta(hours=1, minutes=30)).isoformat()))
        db.commit()
        result = period_report(db, "DAILY", date(2026, 10, 5), now=end+timedelta(minutes=2))
    assert result["closed_episodes"] == 0
    assert D(result["gross_realized_exit_pnl"]) == 0
    assert any("ownership" in warning or "another entry" in warning for warning in result["coverage"])


def test_mocked_fill_side_identity_conflict_excludes_episode_instead_of_profit(observed_demo):
    _, factory, _, end = observed_demo
    with factory() as db:
        db.get(DemoFill, "offline-fill-a").side = "SELL"  # contradictory offline fault fixture
        db.commit()
        result = period_report(db, "DAILY", date(2026, 10, 5), now=end+timedelta(minutes=2))
    assert result["closed_episodes"] == 0 and D(result["gross_realized_exit_pnl"]) == 0
    assert any("Fill identity conflicts" in warning for warning in result["coverage"])
