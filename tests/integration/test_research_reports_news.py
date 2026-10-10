"""Actual workspace/report persistence; sources and trading data are isolated fixtures."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tradebot import news, notifications, reporting
from tradebot.db import utcnow
from tradebot.expansion_models import PaperMovement
from tradebot.models import EquitySnapshot, Order, PaperAccount, Strategy
from tradebot.news_models import NewsEvent, NewsSource, NewsSourceObservation
from tradebot.notifications_models import TelegramOutbox
from tradebot.reporting_models import PerformanceReport


@pytest.fixture
def auxiliary(authenticated):
    return authenticated


def import_document(client):
    value = client.post("/api/v1/research/documents", json={"title": "Authorized research note", "source_reference": "Operator's notebook, page 3",
                       "format": "TEXT", "content": "# Strong momentum strategy\nBuy when momentum is strong.\nStop when the trend is weak.\nProfit target unspecified."})
    assert value.status_code == 201, value.text
    return value.json()


def test_research_import_preserves_attribution_and_ambiguity(auxiliary):
    client, factory = auxiliary
    data = import_document(client)
    candidate = data["candidates"][0]
    assert candidate["status"] == "NEEDS_REVIEW"
    assert candidate["specification"]["automation_ambiguities"]
    assert "page 3" in candidate["source_location"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Strategy)) == 0
    repeated = import_document(client)
    assert repeated["deduplicated"] is True
    assert repeated["document"]["id"] == data["document"]["id"]


def test_research_requires_review_explicit_resolution_and_draft_only(auxiliary):
    client, factory = auxiliary
    data = import_document(client)
    candidate = data["candidates"][0]
    path = f"/api/v1/research/candidates/{candidate['id']}"
    payload = {"confirm": True, "config": {"kind": "EMA_CROSS", "fast_period": 3, "slow_period": 8}}
    assert client.post(path + "/approve", json=payload).status_code == 409
    assert client.patch(path, json={"specification": {}}).status_code == 200
    assert client.post(path + "/approve", json=payload).status_code == 409
    value = client.patch(path, json={"specification": {"automation_ambiguities": [], "source_title": "cannot erase attribution"}}).json()
    assert value["specification"]["source_title"] == "Authorized research note"
    assert client.post(path + "/approve", json={**payload, "confirm": False}).status_code == 422
    approved = client.post(path + "/approve", json=payload)
    assert approved.status_code == 201, approved.text
    assert approved.json()["strategy"]["status"] == "DRAFT"
    assert approved.json()["candidate"]["specification"]["strategy_id"] == approved.json()["strategy"]["id"]
    assert client.patch(path, json={"specification": {}}).status_code == 409
    assert client.post(path + "/approve", json=payload).status_code == 409
    assert client.get("/api/v1/research/documents").json()[0].get("content") is None


def test_research_unmeasurable_config_cannot_be_approved(auxiliary):
    client, factory = auxiliary
    data = import_document(client)
    path = f"/api/v1/research/candidates/{data['candidates'][0]['id']}"
    client.patch(path, json={"specification": {"automation_ambiguities": []}})
    value = client.post(path + "/approve", json={"confirm": True, "config": {"kind": "CUSTOM", "long_rules": {"op": "RUN_CODE", "code": "buy()"}}})
    assert value.status_code == 422
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Strategy)) == 0


def test_pdf_failure_is_bounded_without_echoing_source(auxiliary):
    client, factory = auxiliary
    value = client.post("/api/v1/research/documents", json={"title": "PDF", "source_reference": "Authorized PDF", "format": "PDF_BASE64", "content": "not-base64-sensitive-source"})
    assert value.status_code == 422
    assert "sensitive-source" not in value.text


def add_source(client, required=False):
    response = client.post("/api/v1/news/sources", json={"name": "Authorized operator calendar", "authorized": True, "required_for_trading": required})
    assert response.status_code == 201, response.text
    return response.json()


def event_payload(source, now=None):
    now = now or utcnow()
    return {"source_id": source["id"], "external_id": "documented-event-1", "title": "Explicit test economic event",
            "category": "ECONOMIC", "impact": "HIGH", "published_at": (now - timedelta(minutes=10)).isoformat(),
            "scheduled_at": (now + timedelta(minutes=5)).isoformat(), "symbols": ["BTCUSD"], "source_url": "https://calendar.example/event/1"}


def test_event_authorization_deduplication_and_blackout(auxiliary):
    client, factory = auxiliary
    assert client.post("/api/v1/news/sources", json={"name": "Unauthorized", "authorized": False}).status_code == 422
    source = add_source(client)
    payload = event_payload(source)
    first = client.post("/api/v1/news/events", json=payload)
    assert first.status_code == 201, first.text
    repeated = client.post("/api/v1/news/events", json=payload)
    assert repeated.json()["deduplicated"]
    rule = client.post("/api/v1/news/blackouts", json={"name": "Major calendar events", "categories": ["ECONOMIC"], "minimum_impact": "HIGH", "before_minutes": 15, "after_minutes": 15})
    assert rule.status_code == 201
    assert not client.get("/api/v1/news/entry-check?symbol=BTCUSD").json()["allowed"]
    assert client.get("/api/v1/news/entry-check?symbol=ETHUSD").json()["allowed"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(NewsEvent)) == 1
        # Historical decisions must not use events received later.
        assert news.entry_allowed(db, "BTCUSD", utcnow() - timedelta(minutes=1))["allowed"]
    client.put(f"/api/v1/news/blackouts/{rule.json()['id']}", json={"name": "Disabled", "categories": ["ECONOMIC"], "enabled": False})
    assert client.get("/api/v1/news/entry-check?symbol=BTCUSD").json()["allowed"]


def test_required_source_staleness_blocks_entries(auxiliary):
    client, factory = auxiliary
    source = add_source(client, required=True)
    decision = client.get("/api/v1/news/entry-check").json()
    assert decision["reasons"][0]["kind"] == "STALE_REQUIRED_SOURCE"
    client.post("/api/v1/news/events", json=event_payload(source))
    assert client.get("/api/v1/news/entry-check").json()["allowed"]
    with factory() as db:
        row = db.get(NewsSource, source["id"])
        row.last_success_at = utcnow() - timedelta(hours=2)
        for observation in db.scalars(select(NewsSourceObservation)):
            observation.observed_at = utcnow() - timedelta(hours=2)
        db.commit()
    assert not client.get("/api/v1/news/entry-check").json()["allowed"]


def test_event_timestamp_quality_validation(auxiliary):
    client, factory = auxiliary
    payload = event_payload(add_source(client))
    assert client.post("/api/v1/news/events", json={**payload, "published_at": "2020-01-01T00:00:00"}).status_code == 422
    assert client.post("/api/v1/news/events", json={**payload, "published_at": (utcnow() + timedelta(days=2)).isoformat()}).status_code == 422
    assert client.post("/api/v1/news/sources", json={"name": "Bad URL", "authorized": True, "url": "http://localhost:8000"}).status_code == 422


def test_report_period_boundaries_use_india_time():
    start, end = reporting.period_bounds("DAILY", datetime(2026, 10, 9).date())
    assert start == datetime(2026, 10, 8, 18, 30, tzinfo=timezone.utc)
    assert end - start == timedelta(days=1)
    start, end = reporting.period_bounds("WEEKLY", datetime(2026, 10, 9).date())
    assert start.astimezone(reporting.IST).weekday() == 0
    start, end = reporting.period_bounds("MONTHLY", datetime(2024, 2, 29).date())
    assert end - start == timedelta(days=29)


def report_fixture(client, factory):
    assert client.post("/api/v1/paper/account", json={"initial_balance": "1000", "settlement_asset": "USD"}).status_code == 201
    start, end = reporting.period_bounds("DAILY", datetime(2026, 1, 2).date())
    with factory() as db:
        account = db.get(PaperAccount, 1)
        account.created_at = start - timedelta(days=1)
        db.add(EquitySnapshot(account_id=1, equity=Decimal(1000), cash=Decimal(1000), created_at=start))
        db.add(EquitySnapshot(account_id=1, equity=Decimal(1510), cash=Decimal(1500), created_at=end - timedelta(minutes=1)))
        db.add(PaperMovement(id="capital-flow", account_id=1, kind="RESET", amount=Decimal(500), details={}, created_at=start + timedelta(hours=1)))
        db.add(PaperMovement(id="funding-flow", account_id=1, kind="FUNDING", amount=Decimal(-2), details={}, created_at=start + timedelta(hours=2)))
        db.commit()
    return start, end


def test_report_return_excludes_reset_and_funding_is_actual(auxiliary):
    client, factory = auxiliary
    start, end = report_fixture(client, factory)
    result = client.post("/api/v1/reports", json={"period": "DAILY", "date": "2026-01-02"})
    assert result.status_code == 201, result.text
    payload = result.json()["payload"]
    assert Decimal(payload["net_return"]) == Decimal(".01")
    assert Decimal(payload["funding"]) == -2
    assert Decimal(payload["net_realized_pnl"]) == -2
    assert payload["unrealized_pnl"] == "10.000000000000"
    assert payload["total_trades"] == 0
    assert payload["win_rate"] is None
    assert payload["profit_factor"] is None
    repeated = client.post("/api/v1/reports", json={"period": "DAILY", "date": "2026-01-02"})
    assert repeated.json()["id"] == result.json()["id"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PerformanceReport)) == 1
    identifier = result.json()["id"]
    assert client.get(f"/api/v1/reports/{identifier}/export?format=json").json()["mode"] == "PAPER"
    exported = client.get(f"/api/v1/reports/{identifier}/export?format=csv")
    assert "summary,funding,'-2" in exported.text
    assert "attachment" in exported.headers["content-disposition"]


def test_reports_reject_incomplete_period_and_schedule_only_actual_history(auxiliary):
    client, factory = auxiliary
    client.post("/api/v1/paper/account", json={"initial_balance": "1000", "settlement_asset": "USD"})
    assert client.post("/api/v1/reports", json={"period": "MONTHLY", "date": utcnow().date().isoformat()}).status_code == 409
    with factory() as db:
        reporting.report_tick(db)
        db.commit()
        assert db.scalar(select(func.count()).select_from(PerformanceReport)) == 0
    assert client.get("/api/v1/reports/analytics").status_code == 200


def test_viewer_cannot_import_or_configure(backend):
    client, factory = backend
    logged = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "test-only-password"})
    client.headers["X-CSRF-Token"] = logged.json()["csrf_token"]
    assert client.post("/api/v1/research/documents", json={"title": "note", "source_reference": "file", "content": "notes"}).status_code == 403


def test_telegram_both_allowlists_and_commands_are_read_only(auxiliary):
    client, factory = auxiliary
    assert client.put("/api/v1/integrations/telegram", json={"enabled": True, "allowed_chat_ids": ["10"]}).status_code == 422
    configured = client.put("/api/v1/integrations/telegram", json={"enabled": True, "allowed_chat_ids": ["10"], "allowed_user_ids": ["20"]})
    assert configured.status_code == 200
    with factory() as db:
        assert not notifications.accept_update(db, {"update_id": 1, "message": {"chat": {"id": 10}, "from": {"id": 21}, "text": "/status"}})
        assert notifications.accept_update(db, {"update_id": 2, "message": {"chat": {"id": 10}, "from": {"id": 20}, "text": "/status"}})
        assert not notifications.accept_update(db, {"update_id": 2, "message": {"chat": {"id": 10}, "from": {"id": 20}, "text": "/status"}})
        notifications.accept_update(db, {"update_id": 3, "message": {"chat": {"id": 10}, "from": {"id": 20}, "text": "/buy BTCUSD"}})
        db.commit()
        assert db.scalar(select(func.count()).select_from(TelegramOutbox)) == 1
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        for command in notifications.COMMANDS:
            assert notifications.command_response(db, command)
        assert db.scalar(select(func.count()).select_from(PaperAccount)) == 0


@pytest.mark.asyncio
async def test_telegram_outbox_retry_redacts_token_and_does_not_fail_recovery(auxiliary, monkeypatch):
    import httpx
    client, factory = auxiliary
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:offline-test-secret")
    monkeypatch.setenv("TELEGRAM_DELIVERY_ENABLED", "true")
    client.put("/api/v1/integrations/telegram", json={"enabled": True, "allowed_chat_ids": ["10"], "allowed_user_ids": ["20"]})
    calls = []
    def failed(request):
        calls.append(request)
        return httpx.Response(429, json={"ok": False, "description": "DO_NOT_SAVE_PROVIDER_SECRET", "parameters": {"retry_after": 20}})
    with factory() as db:
        notifications.enqueue(db, "offline-test", "10", "PAPER offline fixture alert")
        db.commit()
        await notifications.deliver_pending(db, transport=httpx.MockTransport(failed), allow_network=True)
        row = db.scalar(select(TelegramOutbox))
        assert row.status == "PENDING" and row.attempts == 1
        assert "secret" not in row.last_error.lower()
        assert "DO_NOT_SAVE" not in row.last_error
        row.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
        def accepted(request):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        await notifications.deliver_pending(db, transport=httpx.MockTransport(accepted), allow_network=True)
        assert row.status == "SENT" and row.attempts == 2
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_telegram_default_network_gate_prevents_send(auxiliary, monkeypatch):
    import httpx
    client, factory = auxiliary
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:offline-test-secret")
    monkeypatch.setenv("TELEGRAM_DELIVERY_ENABLED", "true")
    client.put("/api/v1/integrations/telegram", json={"enabled": True, "allowed_chat_ids": ["10"], "allowed_user_ids": ["20"]})
    def should_not_send(request):
        raise AssertionError("No transport call permitted")
    with factory() as db:
        notifications.enqueue(db, "disabled-test", "10", "PAPER alert")
        db.commit()
        await notifications.deliver_pending(db, transport=httpx.MockTransport(should_not_send))
        assert db.scalar(select(TelegramOutbox)).attempts == 0


def test_pdf_text_import_runs_isolated_parser(auxiliary):
    import base64
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    client, factory = auxiliary
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 250 Td (EMA strategy: Buy when momentum is strong.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    response = client.post("/api/v1/research/documents", json={"title": "Controlled PDF fixture", "source_reference": "Test-only PDF, page 1",
                           "format": "PDF_BASE64", "content": base64.b64encode(buffer.getvalue()).decode()})
    assert response.status_code == 201, response.text
    assert response.json()["document"]["page_count"] == 1
    assert "[Page 1]" in response.json()["document"]["content"]
    assert response.json()["candidates"][0]["specification"]["indicators"] == ["EMA"]


@pytest.mark.asyncio
async def test_authorized_feed_poll_validates_atomically_and_records_history(auxiliary, monkeypatch):
    import httpx
    client, factory = auxiliary
    source_data = client.post("/api/v1/news/sources", json={"name": "Offline feed fixture", "authorized": True, "url": "https://calendar.example/events"}).json()
    payload = event_payload(source_data)
    monkeypatch.setattr(news, "_authorized_public_url", lambda url: None)
    with factory() as db:
        source = db.get(NewsSource, source_data["id"])
        await news.poll_source(db, source, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"events": [payload, {"title": "Invalid schema"}]})))
        db.commit()
        assert db.scalar(select(func.count()).select_from(NewsEvent)) == 0
        assert db.scalar(select(NewsSourceObservation.status)) == "ERROR"
        assert source.last_error
        await news.poll_source(db, source, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"events": [payload]})))
        db.commit()
        assert db.scalar(select(func.count()).select_from(NewsEvent)) == 1
        assert db.scalar(select(func.count()).select_from(NewsSourceObservation).where(NewsSourceObservation.status == "SUCCESS")) == 1
        assert source.last_error is None
        before = utcnow() - timedelta(seconds=10)
        assert db.scalar(select(NewsSourceObservation).where(NewsSourceObservation.status == "SUCCESS", NewsSourceObservation.observed_at <= before)) is None


def test_feed_rejects_non_allowlisted_and_private_resolutions(monkeypatch):
    monkeypatch.setenv("NEWS_ALLOWED_HOSTS", "calendar.example")
    with pytest.raises(ValueError, match="authorization"):
        news._authorized_public_url("https://another.example/events")
    monkeypatch.setattr(news.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="public addresses"):
        news._authorized_public_url("https://calendar.example/events")


def test_capital_withdrawal_does_not_create_false_drawdown(auxiliary):
    client, factory = auxiliary
    start, end = report_fixture(client, factory)
    with factory() as db:
        db.get(PaperMovement, "capital-flow").amount = Decimal(-500)
        closing = db.scalar(select(EquitySnapshot).where(EquitySnapshot.created_at > start, EquitySnapshot.created_at < end))
        closing.equity, closing.cash = Decimal(490), Decimal(490)
        db.commit()
        value = reporting.build_report(db, db.get(PaperAccount, 1), "DAILY", start, end)
        assert Decimal(value["maximum_drawdown"]) == Decimal(".01")
        assert Decimal(value["net_return"]) == Decimal("-.01")
        assert Decimal(value["equity_curve"][-1]["equity_excluding_period_capital_flows"]) == 990


def test_report_trade_fees_funding_regime_and_entry_quality(auxiliary):
    from tradebot.expansion_models import PaperPositionState
    from tradebot.models import Fill, Order, Position, Strategy, StrategyVersion
    client, factory = auxiliary
    start, end = report_fixture(client, factory)
    with factory() as db:
        strategy = Strategy(name="Controlled trade fixture", status="STOPPED")
        db.add(strategy)
        db.flush()
        db.add(StrategyVersion(strategy_id=strategy.id, version=1, config={"timeframe_seconds": 60, "max_leverage": "2"}))
        position = Position(id="closed-fixture-position", account_id=1, strategy_id=strategy.id, symbol="BTCUSD", side="BUY", status="CLOSED",
                            quantity=Decimal(1), contract_size=Decimal(".001"), conversion_rate=Decimal(1), instrument_snapshot={},
                            entry_price=Decimal(60010), mark_price=Decimal(61000), stop_price=Decimal(59000), take_profit_price=Decimal(62000),
                            margin=Decimal(30), realized_pnl=Decimal(10), opened_at=start + timedelta(hours=1), closed_at=start + timedelta(hours=2))
        db.add(position)
        db.flush()
        db.add(PaperPositionState(position_id=position.id, strategy_version=1, initial_stop=Decimal(59000), entry_quantity=Decimal(1),
                                 favorable_price=Decimal(61000), policy={}, entry_observations={"reference_price": "60000", "fill_price": "60010", "regime": {"primary": "TREND_UP"}}))
        for identifier, reduce, realized in (("entry-fixture", False, Decimal(0)), ("exit-fixture", True, Decimal(10))):
            db.add(Order(id=identifier, client_order_id=identifier, strategy_id=strategy.id, account_id=1, position_id=position.id, symbol="BTCUSD",
                         side="SELL" if reduce else "BUY", order_type="MARKET", status="FILLED", quantity=Decimal(1), price=Decimal(60010), reduce_only=reduce,
                         mode="PAPER", created_at=start + timedelta(hours=1 + int(reduce))))
            db.flush()
            db.add(Fill(id=identifier + "-fill", order_id=identifier, position_id=position.id, quantity=Decimal(1), price=Decimal(60010), fee=Decimal(1),
                        realized_pnl=realized, created_at=start + timedelta(hours=1 + int(reduce))))
        db.add(PaperMovement(id="position-funding", account_id=1, position_id=position.id, kind="FUNDING", amount=Decimal(-1), created_at=start + timedelta(hours=1)))
        db.commit()
        report = reporting.build_report(db, db.get(PaperAccount, 1), "DAILY", start, end)
        assert report["total_trades"] == 1 and report["winning_trades"] == 1
        assert Decimal(report["fees"]) == 2
        assert Decimal(report["gross_realized_pnl"]) == 10
        assert Decimal(report["net_realized_pnl"]) == 5  # includes other account funding -2
        assert Decimal(report["strategy_contributions"][0]["net_realized_pnl"]) == 7
        assert report["market_regime_performance"]["covered_trades"] == 1
        assert Decimal(report["market_regime_performance"]["regimes"][0]["net_realized_pnl"]) == 7
        assert report["execution_quality"]["covered_positions"] == 1
        assert Decimal(report["execution_quality"]["average_adverse_difference_bps"]) > 0
        assert report["capital_allocation"]["positions"] == []
        db.add(PaperMovement(id="liquidation-cost", account_id=1, position_id=position.id, kind="LIQUIDATION_FEE", amount=Decimal(-20), created_at=start + timedelta(hours=2)))
        db.commit()
        after_cost = reporting.build_report(db, db.get(PaperAccount, 1), "DAILY", start, end)
        assert after_cost["winning_trades"] == 0 and after_cost["losing_trades"] == 1
        assert Decimal(after_cost["fees"]) == 22
        assert Decimal(after_cost["trading_fees"]) == 2 and Decimal(after_cost["liquidation_fees"]) == 20
        assert Decimal(after_cost["net_realized_pnl"]) == -15
        assert Decimal(after_cost["strategy_contributions"][-1]["net_realized_pnl"]) == -13
        assert Decimal(after_cost["market_regime_performance"]["regimes"][0]["net_realized_pnl"]) == -13


def test_telegram_notification_capture_is_idempotent(auxiliary):
    from tradebot.models import Notification
    client, factory = auxiliary
    client.put("/api/v1/integrations/telegram", json={"enabled": True, "allowed_chat_ids": ["10", "11"], "allowed_user_ids": ["20"]})
    with factory() as db:
        db.add(Notification(severity="WARNING", message="Controlled offline risk fixture"))
        db.commit()
        notifications.enqueue_notifications(db)
        db.commit()
        notifications.enqueue_notifications(db)
        db.commit()
        assert db.scalar(select(func.count()).select_from(TelegramOutbox)) == 2
        assert all("MODE UNSPECIFIED" in row.text and "Controlled offline risk fixture" in row.text for row in db.scalars(select(TelegramOutbox)))


@pytest.mark.asyncio
async def test_telegram_uncertain_attempt_stops_after_eight_and_can_retry(auxiliary, monkeypatch):
    import httpx
    client, factory = auxiliary
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:offline-test-secret")
    monkeypatch.setenv("TELEGRAM_DELIVERY_ENABLED", "true")
    client.put("/api/v1/integrations/telegram", json={"enabled": True, "allowed_chat_ids": ["10"], "allowed_user_ids": ["20"]})
    def uncertain(request):
        raise httpx.ReadTimeout("token-must-not-be-logged", request=request)
    with factory() as db:
        row = notifications.enqueue(db, "uncertain-test", "10", "PAPER fixture")
        row.attempts = 7
        db.commit()
        await notifications.deliver_pending(db, transport=httpx.MockTransport(uncertain), allow_network=True)
        assert row.status == "FAILED" and row.attempts == 8
        assert "duplicate" in row.last_error
        assert "token" not in row.last_error
        identifier = row.id
    value = client.post(f"/api/v1/integrations/telegram/outbox/{identifier}", json={"action": "RETRY"})
    assert value.status_code == 200 and value.json()["attempts"] == 0
    assert client.post(f"/api/v1/integrations/telegram/outbox/{identifier}", json={"action": "CANCEL"}).json()["status"] == "CANCELED"



def test_news_corrections_preserve_causal_revision_history(auxiliary, monkeypatch):
    client, factory = auxiliary
    source = add_source(client)
    base = utcnow()
    monkeypatch.setattr(news, "utcnow", lambda: base)
    payload = event_payload(source, base)
    original = client.post("/api/v1/news/events", json=payload).json()["event"]
    client.post("/api/v1/news/blackouts", json={"name": "Economic gate", "categories": ["ECONOMIC"], "minimum_impact": "HIGH", "before_minutes": 15, "after_minutes": 15})
    corrected_at = base + timedelta(seconds=10)
    monkeypatch.setattr(news, "utcnow", lambda: corrected_at)
    corrected = client.post("/api/v1/news/events", json={**payload, "scheduled_at": (base + timedelta(hours=1)).isoformat(), "details": "Publisher postponed the original event"})
    assert corrected.status_code == 201, corrected.text
    revision = corrected.json()["event"]
    assert revision["revision"] == 2 and revision["id"] != original["id"]
    assert corrected.json()["deduplicated"] is False
    assert len(client.get("/api/v1/news/events").json()) == 1
    assert len(client.get("/api/v1/news/events?include_history=true").json()) == 2
    history = client.get(f"/api/v1/news/events/{revision['id']}/history").json()
    assert [row["revision"] for row in history] == [1, 2]
    assert history[0]["superseded_at"] == revision["received_at"]
    with factory() as db:
        before = news.entry_allowed(db, "BTCUSD", base + timedelta(seconds=5))
        assert not before["allowed"] and before["reasons"][0]["event_revision"] == 1
        assert news.entry_allowed(db, "BTCUSD", corrected_at)["allowed"]
    # A publisher may revert to its initial values without erasing revision 2.
    monkeypatch.setattr(news, "utcnow", lambda: base + timedelta(seconds=20))
    reverted = client.post("/api/v1/news/events", json=payload).json()["event"]
    assert reverted["revision"] == 3 and reverted["content_hash"] == original["content_hash"]
    assert len(client.get(f"/api/v1/news/events/{reverted['id']}/history").json()) == 3
    with factory() as db:
        assert not news.entry_allowed(db, "BTCUSD", base + timedelta(seconds=20))["allowed"]


def test_news_impact_correction_and_equivalent_timezone_dedup(auxiliary, monkeypatch):
    client, factory = auxiliary
    source = add_source(client)
    base = utcnow()
    monkeypatch.setattr(news, "utcnow", lambda: base)
    payload = event_payload(source, base)
    first = client.post("/api/v1/news/events", json=payload).json()["event"]
    equivalent = {**payload, "published_at": datetime.fromisoformat(payload["published_at"]).astimezone(reporting.IST).isoformat(),
                  "scheduled_at": datetime.fromisoformat(payload["scheduled_at"]).astimezone(reporting.IST).isoformat()}
    repeated = client.post("/api/v1/news/events", json=equivalent).json()
    assert repeated["deduplicated"] and repeated["event"]["id"] == first["id"]
    monkeypatch.setattr(news, "utcnow", lambda: base + timedelta(seconds=5))
    lowered = client.post("/api/v1/news/events", json={**payload, "impact": "LOW"}).json()["event"]
    assert lowered["revision"] == 2 and lowered["impact"] == "LOW"
    assert lowered["content_hash"] != first["content_hash"]
