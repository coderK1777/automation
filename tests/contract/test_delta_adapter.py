"""CONTROLLED OFFLINE MOCKS: none of these tests authenticate with Delta.

This suite exercises documented wire contracts and injected faults. Public
connectivity and actual demo account acceptance use separate evidence.
"""

import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from tradebot_delta import (DeltaAPIError, DeltaClient, DeltaProtocolError,
                           DemoAuthorizationRequired, DemoClient, DemoCredentials,
                           Environment, OrderSubmissionUnknown, encode_body, sign_request)
from tradebot_delta import client as adapter

pytestmark = pytest.mark.mocked

MOCK_PRODUCT = {"id": 12345, "symbol": "BTCUSD", "contract_type": "perpetual_futures",
                "notional_type": "vanilla", "contract_value": "0.001", "contract_unit_currency": "BTC",
                "tick_size": "0.5", "settling_asset": {"symbol": "USD"},
                "quoting_asset": {"symbol": "USD"}, "underlying_asset": {"symbol": "BTC"},
                "state": "live", "trading_status": "operational"}


def response(result, *, status=200, meta=None, headers=None):
    return httpx.Response(status, json={"success": True, "result": result, **({"meta": meta} if meta else {})}, headers=headers)


@pytest.mark.asyncio
async def test_mocked_public_product_discovery_is_credential_free_and_preserves_economics():
    requests = []

    def handle(request):
        requests.append(request)
        return response([MOCK_PRODUCT])

    async with DeltaClient(transport=httpx.MockTransport(handle)) as client:
        product = (await client.products())[0]
    assert product["contract_value"] == "0.001"
    assert product["settlement_asset"] == "USD"
    assert product["min_qty"] is None
    assert product["qty_step"] == "1"
    assert product["metadata_verified"] is False
    assert requests[0].url.host == "api.india.delta.exchange"
    assert all(name not in requests[0].headers for name in ("api-key", "signature", "timestamp"))


@pytest.mark.asyncio
async def test_mocked_product_cursor_pagination_discovers_second_page():
    seen = []

    def handle(request):
        seen.append(request.url.params.get("after"))
        if request.url.params.get("after"):
            return response([{**MOCK_PRODUCT, "id": 54321, "symbol": "ETHUSD"}])
        return response([MOCK_PRODUCT], meta={"after": "second-page"})

    async with DeltaClient(transport=httpx.MockTransport(handle), requests_per_second=5) as client:
        products = await client.products()
    assert [p["symbol"] for p in products] == ["BTCUSD", "ETHUSD"]
    assert seen == [None, "second-page"]


@pytest.mark.asyncio
async def test_mocked_history_preserves_json_decimal_precision_and_deduplicates():
    content = b'{"success":true,"result":[{"time":120,"open":1.1234567890123456789,"high":2,"low":1,"close":1.5,"volume":10},{"time":60,"open":1,"high":2,"low":1,"close":1.5,"volume":10},{"time":60,"open":1,"high":2,"low":1,"close":1.5,"volume":10}]}'
    async with DeltaClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as client:
        candles = await client.candles("BTCUSD", "1m", 60, 180)
    assert [c["timestamp"] for c in candles] == [60, 120]
    assert candles[1]["open"] == "1.1234567890123456789"


@pytest.mark.asyncio
async def test_mocked_large_history_splits_documented_2000_candle_cap():
    requested = []

    def handle(request):
        requested.append(dict(request.url.params))
        return response([])

    async with DeltaClient(transport=httpx.MockTransport(handle), requests_per_second=5) as client:
        assert await client.candles("BTCUSD", "1m", 0, 2500 * 60) == []
    assert len(requested) == 2
    assert int(requested[0]["end"]) == 1999 * 60
    assert int(requested[1]["start"]) == 1999 * 60 + 1


@pytest.mark.asyncio
async def test_mocked_conflicting_duplicate_history_is_rejected():
    row = {"time": 60, "open": 1, "high": 3, "low": 1, "close": 2, "volume": 10}
    async with DeltaClient(transport=httpx.MockTransport(lambda request: response([row, {**row, "close": 3}]))) as client:
        with pytest.raises(DeltaProtocolError, match="Conflicting duplicate"):
            await client.candles("BTCUSD", "1m", 0, 180)


@pytest.mark.asyncio
async def test_mocked_get_retry_respects_official_millisecond_reset_header(monkeypatch):
    sleeps = []
    seen = []

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(adapter.asyncio, "sleep", sleep)

    def handle(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, json={"success": False, "error": {"code": "rate_limit"}}, headers={"X-RATE-LIMIT-RESET": "1500"})
        return response([MOCK_PRODUCT])

    async with DeltaClient(transport=httpx.MockTransport(handle)) as client:
        assert await client.products()
    assert len(seen) == 2
    assert 1.5 in sleeps


@pytest.mark.asyncio
async def test_mocked_long_rate_reset_returns_control_without_retry():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(429, json={"success": False}, headers={"X-RATE-LIMIT-RESET": "300000"})

    async with DeltaClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(DeltaAPIError) as caught:
            await client.products()
    assert caught.value.retry_after == 300
    assert len(seen) == 1


def test_mocked_signature_binds_exact_method_timestamp_path_query_and_utf8_body():
    body = b'{"client_order_id":"demo-a","note":"\xe2\x82\xb9"}'
    expected = hmac.new(b"offline-test-secret", b"POST1700000000/v2/orders?one=hello+world" + body, hashlib.sha256).hexdigest()
    assert sign_request("offline-test-secret", "POST", "1700000000", "/v2/orders", "?one=hello+world", body) == expected
    assert encode_body({"client_order_id": "demo-a", "note": "₹"}) == body
    # Independent OpenSSL vector: printf GET1700000000/live | openssl dgst
    # -sha256 -hmac offline-secret (test fixture, not an exchange credential).
    assert sign_request("offline-secret", "GET", "1700000000", "/live") == "6a19748560da469130c4c7f392022ad3f330a57878d3ae688d57533d054708c0"


def test_mocked_public_client_has_no_order_methods_and_demo_refuses_live():
    assert not hasattr(DeltaClient, "place_order")
    assert not hasattr(DeltaClient, "balances")
    with pytest.raises(ValueError, match="DemoClient"):
        DemoClient(DemoCredentials("offline-key", "offline-secret"), environment="production")
    with pytest.raises(ValueError, match="demo-scoped"):
        DemoCredentials("offline-key", "offline-secret", Environment.LIVE)
    assert "offline-secret" not in repr(DemoCredentials("offline-key", "offline-secret"))
    with pytest.raises(ValueError, match="proxy placeholders"):
        DemoCredentials("offline-key", "proxy-secret:managed-placeholder")


@pytest.mark.asyncio
async def test_mocked_demo_write_requires_explicit_authorization():
    seen = []
    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), transport=httpx.MockTransport(lambda request: seen.append(request))) as client:
        with pytest.raises(DemoAuthorizationRequired):
            await client.place_order(product_id=12345, side="buy", size=1, client_order_id="offline-demo", limit_price="50000")
    assert seen == []


@pytest.mark.asyncio
async def test_mocked_demo_signs_the_actual_prepared_body_and_never_production(monkeypatch):
    monkeypatch.setattr(adapter.time, "time", lambda: 1700000000)
    seen = []

    def handle(request):
        seen.append(request)
        return response({"id": 77, "state": "open", "client_order_id": "offline-demo", "product_id": 12345, "side": "buy", "size": 1})

    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), allow_demo_orders=True, transport=httpx.MockTransport(handle)) as client:
        assert (await client.place_order(product_id=12345, side="buy", size=1, client_order_id="offline-demo", limit_price=Decimal("50000.5")))["id"] == 77
    request = seen[0]
    assert request.url.host == "cdn-ind.testnet.deltaex.org"
    expected = hmac.new(b"offline-secret", b"POST1700000000/v2/orders" + request.content, hashlib.sha256).hexdigest()
    assert request.headers["signature"] == expected
    assert request.headers["timestamp"] == "1700000000"
    assert request.headers["User-Agent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["timeout", "server_error", "invalid_json", "missing_status", "null_result", "mismatched_identity"])
async def test_mocked_order_uncertainty_never_blindly_retries(fault):
    seen = []

    def handle(request):
        seen.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("injected after acceptance", request=request)
        if fault == "server_error":
            return httpx.Response(503, json={"success": False})
        if fault == "missing_status":
            return httpx.Response(200, json={})
        if fault == "null_result":
            return response(None)
        if fault == "mismatched_identity":
            return response({"id": 88, "state": "open", "client_order_id": "different", "product_id": 12345, "side": "buy", "size": 1})
        return httpx.Response(200, text="invalid acknowledgment")

    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), allow_demo_orders=True, transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(OrderSubmissionUnknown) as caught:
            await client.place_order(product_id=12345, side="buy", size=1, client_order_id="offline-demo", order_type="market_order")
    assert len(seen) == 1
    assert caught.value.client_order_id == "offline-demo"


@pytest.mark.asyncio
async def test_mocked_lookup_cancel_balances_positions_use_documented_demo_paths():
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path, dict(request.url.params), request.content))
        return response({"id": 77, "product_id": 12345, "state": "cancelled"} if request.method == "DELETE" else [])

    async with DemoClient(DemoCredentials("offline-key", "offline-secret"), allow_demo_orders=True, requests_per_second=5, transport=httpx.MockTransport(handle)) as client:
        await client.balances()
        await client.positions(12345)
        await client.order(77)
        await client.order_by_client_id("offline-demo")
        await client.cancel_order(product_id=12345, order_id=77)
    assert [(method, path) for method, path, _, _ in seen] == [
        ("GET", "/v2/wallet/balances"), ("GET", "/v2/positions/margined"), ("GET", "/v2/orders/77"),
        ("GET", "/v2/orders/client_order_id/offline-demo"), ("DELETE", "/v2/orders")]
    assert seen[1][2] == {"product_ids": "12345"}
    assert json.loads(seen[-1][3]) == {"id": 77, "product_id": 12345}


class MockSocket:
    """Offline WebSocket transport; never contacts the exchange."""

    def __init__(self, events):
        self.events = iter(events)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        return json.dumps(next(self.events))


@pytest.mark.asyncio
async def test_mocked_public_ws_uses_current_ticker_public_socket_and_heartbeat(monkeypatch):
    socket = MockSocket([{"type": "heartbeat"}, {"type": "ticker", "sy": "BTCUSD", "ts": 1700000000000000, "d": []}])
    targets = []

    def connect(url, **kwargs):
        targets.append(url)
        return socket

    monkeypatch.setattr(adapter, "connect", connect)
    async with DeltaClient() as client:
        stream = client.public_messages("ticker", ["BTCUSD"])
        assert (await anext(stream))["type"] == "ticker"
        await stream.aclose()
    assert targets == ["wss://public-socket.india.delta.exchange"]
    assert socket.sent[0]["payload"]["channels"] == [{"name": "ticker", "symbols": ["BTCUSD"]}]
    assert socket.sent[1] == {"type": "enable_heartbeat"}


@pytest.mark.asyncio
async def test_mocked_private_key_auth_is_numeric_and_sequence_gap_blocks_updates(monkeypatch):
    order = {"type": "orders", "symbol": "BTCUSD", "seq_no": 1, "order_id": 77}
    socket = MockSocket([{"type": "key-auth", "success": True, "status_code": 200, "status": "authenticated"}, order, order, {**order, "seq_no": 3}])
    monkeypatch.setattr(adapter, "connect", lambda *args, **kwargs: socket)
    async with DemoClient(DemoCredentials("offline-key", "offline-secret")) as client:
        auth = client.private_auth_message(1700000000)
        assert auth["type"] == "key-auth"
        assert auth["payload"]["timestamp"] == 1700000000
        assert auth["payload"]["signature"] == hmac.new(b"offline-secret", b"GET1700000000/live", hashlib.sha256).hexdigest()
        stream = client.private_messages(["orders"], ["BTCUSD"])
        assert (await anext(stream))["seq_no"] == 1
        assert (await anext(stream))["type"] == "stream_gap"
        await stream.aclose()
    assert socket.sent[0]["type"] == "key-auth"
