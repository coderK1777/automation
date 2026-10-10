"""Controlled OFFLINE wire tests; no private exchange acceptance is implied."""
import json

import httpx
import pytest

from tradebot_delta import DeltaProtocolError, DemoAuthorizationRequired, DemoClient, DemoCredentials, OrderSubmissionUnknown
from tradebot_delta import client as adapter

pytestmark = pytest.mark.mocked


def success(result, meta=None):
    return httpx.Response(200, json={"success": True, "result": result, "meta": meta or {}})


@pytest.mark.asyncio
async def test_mocked_private_fill_pagination_is_complete_and_demo_scoped():
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.host == "cdn-ind.testnet.deltaex.org"
        if request.url.params.get("after"):
            return success([{"id": "mocked-fill-b"}])
        return success([{"id": "mocked-fill-a"}], {"after": "mocked-next-page"})
    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), transport=httpx.MockTransport(handler), requests_per_second=5) as client:
        rows = await client.fills(start_time=1700000000000000)
    assert [row["id"] for row in rows] == ["mocked-fill-a", "mocked-fill-b"]
    assert seen[0].url.params["start_time"] == "1700000000000000"
    assert seen[1].url.params["page_size"] == "50"
    assert all(request.headers.get("signature") for request in seen)


@pytest.mark.asyncio
async def test_mocked_duplicate_or_cycling_private_pagination_fails_closed():
    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), transport=httpx.MockTransport(lambda request: success([{"id": 1}], {"after": "repeat"})), requests_per_second=5) as client:
        with pytest.raises(DeltaProtocolError, match="Overlapping"):
            await client.active_orders()


@pytest.mark.asyncio
async def test_mocked_attached_entry_bracket_exact_ack_and_no_stop_retry():
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return success({**body, "id": 401, "unfilled_size": 1, "state": "open"})
    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), allow_demo_orders=True, transport=httpx.MockTransport(handler)) as client:
        result = await client.place_order(product_id=84, side="buy", size=1, client_order_id="offline-bracket",
                                          order_type="market_order", bracket_stop_loss_price="80000", bracket_take_profit_price="85000")
    assert result["id"] == 401 and len(seen) == 1
    assert seen[0]["bracket_stop_trigger_method"] == "mark_price"
    assert seen[0]["bracket_stop_loss_price"] == "80000"


@pytest.mark.asyncio
async def test_mocked_missing_protection_ack_is_unknown_never_success():
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return success({**body, "id": 401, "unfilled_size": 1, "state": "open", "bracket_stop_loss_price": None})
    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), allow_demo_orders=True, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OrderSubmissionUnknown, match="protection acknowledgment"):
            await client.place_order(product_id=84, side="buy", size=1, client_order_id="offline-bracket",
                                     order_type="market_order", bracket_stop_loss_price="80000", bracket_take_profit_price="85000")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_mocked_trailing_stop_wire_and_invalid_ambiguous_stop():
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return success({**body, "id": 402, "unfilled_size": 1, "state": "pending"})
    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), allow_demo_orders=True, transport=httpx.MockTransport(handler)) as client:
        await client.place_order(product_id=84, side="sell", size=1, client_order_id="offline-trailing",
                                 order_type="market_order", reduce_only=True, stop_order_type="stop_loss_order", trail_amount="50")
        with pytest.raises(ValueError, match="one stop price"):
            await client.place_order(product_id=84, side="sell", size=1, client_order_id="offline-ambiguous",
                                     order_type="market_order", stop_order_type="stop_loss_order", stop_price="80000", trail_amount="50")
    assert len(seen) == 1 and seen[0]["trail_amount"] == "50"


class MockSocket:
    def __init__(self, events):
        self.events, self.sent = iter(events), []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        return None
    async def send(self, raw):
        self.sent.append(json.loads(raw))
    async def recv(self):
        return json.dumps(next(self.events))


@pytest.mark.asyncio
async def test_mocked_private_auth_reconnect_requires_explicit_recovery_event(monkeypatch):
    sockets = [MockSocket([{"type": "key-auth", "success": True, "status_code": 200, "status": "authenticated"}]) for _ in range(2)]
    targets = []
    def connect(url, **kwargs):
        targets.append(url)
        return sockets[len(targets)-1]
    monkeypatch.setattr(adapter, "connect", connect)
    async with DemoClient(DemoCredentials("offline-key", "offline-secret")) as client:
        for _ in range(2):
            stream = client.private_messages(["orders", "positions", "margins"], ["all"], emit_auth=True)
            message = await anext(stream)
            assert message == {"type": "private_authenticated", "environment": "DEMO", "requires_recovery": True}
            await stream.aclose()
    assert targets == ["wss://socket-ind.testnet.deltaex.org"]*2
    assert all(socket.sent[0]["type"] == "key-auth" for socket in sockets)


@pytest.mark.asyncio
async def test_mocked_production_redirect_or_mutated_base_never_receives_signed_headers():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(307, headers={"Location": "https://api.india.delta.exchange/v2/orders"})
    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), allow_demo_orders=True, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OrderSubmissionUnknown):
            await client.place_order(product_id=84, side="buy", size=1, client_order_id="offline-no-redirect", order_type="market_order")
        client._http.base_url = httpx.URL("https://api.india.delta.exchange")
        with pytest.raises(DemoAuthorizationRequired, match="not India demo"):
            await client.balances()
    assert len(seen) == 1 and seen[0].url.host == "cdn-ind.testnet.deltaex.org"


@pytest.mark.asyncio
async def test_mocked_acceptance_runner_missing_credentials_does_no_network(monkeypatch):
    from tradebot import demo, demo_acceptance
    monkeypatch.setattr(demo, "credential_configured", lambda: False)
    def forbidden(*args, **kwargs):
        raise AssertionError("Missing credential acceptance must not open an exchange client")
    monkeypatch.setattr(demo, "client", forbidden)
    results = await demo_acceptance.readonly()
    assert results[0]["classification"] == "blocked" and results[0]["status"] == "not_run"


@pytest.mark.asyncio
async def test_mocked_acceptance_write_runner_requires_local_authorization(monkeypatch):
    from tradebot import demo, demo_acceptance
    monkeypatch.setattr(demo, "execution_authorized", lambda: False)
    def forbidden(*args, **kwargs):
        raise AssertionError("No explicit local DEMO write authorization")
    monkeypatch.setattr(demo, "client", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    result = await demo_acceptance.orderflow("http://127.0.0.1:3000", "BTCUSD")
    assert result[0]["classification"] == "blocked"
