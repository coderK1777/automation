"""Async India public adapter and a separate, opt-in demo-only private client.

No class implements production private requests. Mock transports exist solely
for controlled contract tests; they never turn mock results into demo evidence.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import random
import re
import time
from typing import Any, AsyncIterator, Callable, Iterable
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from .config import ENDPOINTS, Environment, public_environment
from .clock import monotonic_domain
from .errors import DeltaAPIError, DeltaProtocolError, DeltaStreamUnavailable, DemoAuthorizationRequired, OrderSubmissionUnknown
from .signing import encode_body, sign_request

USER_AGENT = "TradeBotPro/0.1 (Delta-India; public-and-demo-only)"
_SYMBOL = re.compile(r"^[A-Za-z0-9_.:+-]{1,80}$")
_CLIENT_ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
REST_RESOLUTIONS = frozenset(("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d", "1w"))
WS_RESOLUTIONS = REST_RESOLUTIONS | {"12h"}
PUBLIC_SNAPSHOT_CHANNELS = frozenset(("ticker", "ob_l2", "mark_price", "funding_rate"))
RESOLUTION_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                      "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400, "1w": 604800}


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        raise ValueError("Invalid exchange symbol")
    return value


def _decimal_string(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise DeltaProtocolError("Invalid decimal in Delta response") from exc
    if not number.is_finite():
        raise DeltaProtocolError("Non-finite decimal in Delta response")
    return str(number)


def _asset(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("symbol")
    return value if isinstance(value, str) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _retry_delay(response: httpx.Response) -> float:
    """Official X-RATE-LIMIT-RESET is milliseconds, Retry-After is seconds."""
    delays = [0.0]
    for name, divisor in (("X-RATE-LIMIT-RESET", 1000), ("Retry-After", 1)):
        try:
            delay = float(response.headers.get(name, "0")) / divisor
            if delay >= 0:
                delays.append(delay)
        except ValueError:
            pass
    return max(delays)


def normalize_product(product: dict[str, Any], *, public_source_verified: bool = False) -> dict[str, Any]:
    """Preserve economics exactly; absent quantity/FX specifications stay absent."""
    if not isinstance(product.get("id"), int) or not isinstance(product.get("symbol"), str):
        raise DeltaProtocolError("Delta product is missing id or symbol")
    multiplier = _decimal_string(product.get("contract_value"))
    status = product.get("trading_status")
    underlying, quote, settlement = (_asset(product.get(key)) for key in ("underlying_asset", "quoting_asset", "settling_asset"))
    tick = _decimal_string(product.get("tick_size"))
    linear_verified = bool(public_source_verified and product.get("contract_type") == "perpetual_futures"
                           and product.get("notional_type") == "vanilla" and product.get("is_quanto") is False
                           and underlying and underlying == product.get("contract_unit_currency")
                           and quote and quote == settlement and multiplier is not None and Decimal(multiplier) > 0
                           and tick is not None and Decimal(tick) > 0)
    return {
        "id": product["id"],
        "symbol": product["symbol"],
        "contract_type": product.get("contract_type"),
        "notional_type": product.get("notional_type"),
        "is_quanto": product.get("is_quanto"),
        "contract_value": multiplier,
        "multiplier": multiplier,
        "contract_value_currency": product.get("contract_unit_currency"),
        "tick_size": tick,
        "underlying_asset": underlying,
        "quote_asset": quote,
        "settlement_asset": settlement,
        "min_qty": _decimal_string(product.get("min_order_size")),
        "max_qty": _decimal_string(product.get("max_order_size")),
        "qty_step": "1",  # Documented derivative CreateOrderRequest.size is integer contracts.
        "qty_step_source": "official integer derivative order-size schema",
        "initial_margin": _decimal_string(product.get("initial_margin")),
        "maintenance_margin": _decimal_string(product.get("maintenance_margin")),
        "state": product.get("state"),
        "status": status,
        "trading_status": status,
        "metadata_verified": False,
        "linear_economics_verified": linear_verified,
        "public_metadata_observed_at": datetime.now(timezone.utc).isoformat() if public_source_verified else None,
        "verification_scope": "linear multiplier, tick and currencies only; exchange minimum and margin tiers unverified",
        "raw": _json_safe(product),
    }


class _Throttle:
    """Conservative spacing plus official 20,000 weighted units / five minutes.

    Shared by this client's requests. Exchange quotas also include other clients
    on the same account/IP, so server rate-limit headers remain authoritative.
    """

    def __init__(self, rate: float):
        if rate <= 0 or rate > 5:
            raise ValueError("Request rate must be > 0 and <= 5 per second")
        self.interval = 1 / rate
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.costs: deque[tuple[float, int]] = deque()
        self.spent = 0

    async def acquire(self, weight: int = 1) -> None:
        async with self.lock:
            now = time.monotonic()
            while self.costs and now - self.costs[0][0] >= 300:
                _, expired = self.costs.popleft()
                self.spent -= expired
            if self.spent + weight > 20_000:
                delay = max(0, 300 - (now - self.costs[0][0]))
                raise DeltaAPIError(429, "local_weighted_budget", retry_after=delay)
            if self.next_request > now:
                await asyncio.sleep(self.next_request - now)
            now = time.monotonic()
            self.next_request = now + self.interval
            self.costs.append((now, weight))
            self.spent += weight


class DeltaClient:
    """Public data only, irrespective of environment; never accepts credentials."""

    def __init__(self, environment: str | Environment = "production", *, timeout: float = 15,
                 max_get_attempts: int = 3, requests_per_second: float = 2,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock_observer: Callable[[dict[str, Any]], None] | None = None):
        if not 1 <= max_get_attempts <= 5:
            raise ValueError("GET attempts must be between 1 and 5")
        self.environment = public_environment(environment)
        self.endpoints = ENDPOINTS[self.environment]
        self.max_get_attempts = max_get_attempts
        self._public_source_verified = transport is None
        if clock_observer is not None and not callable(clock_observer):
            raise ValueError("Clock observer must be callable")
        self._clock_observer = clock_observer
        self._throttle = _Throttle(requests_per_second)
        self._http = httpx.AsyncClient(base_url=self.endpoints.rest, timeout=timeout, follow_redirects=False,
                                      headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                                      transport=transport)

    async def __aenter__(self) -> DeltaClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        try:
            payload = response.json(parse_float=Decimal)
        except ValueError as exc:
            raise DeltaProtocolError("Delta returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise DeltaProtocolError("Delta returned an invalid envelope")
        if type(payload.get("success")) is not bool:
            raise DeltaProtocolError("Delta response is missing an explicit success status")
        if response.is_error and payload["success"]:
            raise DeltaProtocolError("Delta response has conflicting HTTP and envelope status")
        if response.is_error or payload["success"] is False:
            error = payload.get("error", {})
            code = error.get("code", "request_rejected") if isinstance(error, dict) else "request_rejected"
            # A response is untrusted; avoid reflecting arbitrary text/secrets.
            code = code if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,64}", code) else "request_rejected"
            raise DeltaAPIError(response.status_code, code)
        if "result" not in payload:
            raise DeltaProtocolError("Delta response is missing result")
        return payload["result"]

    async def _get(self, path: str, params: dict[str, Any] | None = None, *, with_meta: bool = False) -> Any:
        for attempt in range(self.max_get_attempts):
            await self._throttle.acquire(3)
            sent_wall, sent_monotonic = time.time(), time.monotonic()
            try:
                response = await self._http.get(path, params=params)
            except httpx.TransportError:
                if attempt + 1 == self.max_get_attempts:
                    raise
                await asyncio.sleep(min(4.0, .25 * 2 ** attempt + random.uniform(0, .1)))
                continue
            if response.status_code in (429, 502, 503, 504) and attempt + 1 < self.max_get_attempts:
                retry_after = _retry_delay(response)
                # Do not send early when the exchange asks for an excessive delay.
                if retry_after > 30:
                    raise DeltaAPIError(response.status_code, "rate_limit", retry_after=retry_after)
                await asyncio.sleep(max(retry_after, min(4.0, .25 * 2 ** attempt + random.uniform(0, .1))))
                continue
            if response.is_redirect:
                raise DeltaProtocolError("Delta endpoint redirected; destination review is required")
            result = self._decode(response)
            if self._clock_observer is not None:
                received_wall, received_monotonic = time.time(), time.monotonic()
                self._clock_observer({
                    "environment": "production" if self.environment is Environment.LIVE else self.environment.value,
                    "source": self.endpoints.rest,
                    "public_source_verified": self._public_source_verified,
                    "date_header": response.headers.get("Date"), "age_header": response.headers.get("Age"),
                    "request_started_at": datetime.fromtimestamp(sent_wall, timezone.utc).isoformat(),
                    "response_received_at": datetime.fromtimestamp(received_wall, timezone.utc).isoformat(),
                    "round_trip_seconds": received_monotonic-sent_monotonic,
                    "monotonic_sample": received_monotonic, "monotonic_domain": monotonic_domain(),
                    "wall_elapsed_seconds": received_wall-sent_wall,
                    "verification_scope": "PUBLIC_HTTPS_RESPONSE" if self._public_source_verified else "CONTROLLED_TRANSPORT",
                })
            return {"result": result, "meta": response.json().get("meta", {})} if with_meta else result
        raise AssertionError("Unreachable request loop")

    async def products(self, contract_types: str | None = "perpetual_futures") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"page_size": 100}
        if contract_types:
            params["contract_types"] = contract_types
        rows: dict[int, dict[str, Any]] = {}
        cursors: set[str] = set()
        for _ in range(100):
            envelope = await self._get("/v2/products", params, with_meta=True)
            result = envelope["result"]
            if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
                raise DeltaProtocolError("Expected product list")
            for row in result:
                product = normalize_product(row, public_source_verified=self._public_source_verified)
                if product["id"] in rows and product["raw"] != rows[product["id"]]["raw"]:
                    raise DeltaProtocolError("Product metadata changed during pagination")
                rows[product["id"]] = product
            meta = envelope["meta"]
            cursor = meta.get("after") if isinstance(meta, dict) else None
            if not cursor:
                return list(rows.values())
            if not isinstance(cursor, str) or cursor in cursors:
                raise DeltaProtocolError("Invalid or repeated product pagination cursor")
            cursors.add(cursor)
            params["after"] = cursor
        raise DeltaProtocolError("Product pagination safety limit reached")

    async def product(self, symbol: str) -> dict[str, Any]:
        result = await self._get(f"/v2/products/{_symbol(symbol)}")
        if not isinstance(result, dict):
            raise DeltaProtocolError("Expected product object")
        return normalize_product(result, public_source_verified=self._public_source_verified)

    async def tickers(self, contract_types: str | None = "perpetual_futures") -> list[dict[str, Any]]:
        result = await self._get("/v2/tickers", {"contract_types": contract_types} if contract_types else None)
        if not isinstance(result, list):
            raise DeltaProtocolError("Expected ticker list")
        return _json_safe(result)

    async def ticker(self, symbol: str) -> dict[str, Any]:
        result = await self._get(f"/v2/tickers/{_symbol(symbol)}")
        if not isinstance(result, dict):
            raise DeltaProtocolError("Expected ticker object")
        return _json_safe(result)

    async def candles(self, symbol: str, resolution: str, start: int, end: int) -> list[dict[str, Any]]:
        _symbol(symbol)
        if resolution not in REST_RESOLUTIONS or type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise ValueError("Candles require a resolution and ordered UTC epoch seconds")
        # The API caps one response at 2000 candles. Split by time rather than
        # silently accepting truncation; gaps still remain visible to callers.
        results: list[dict[str, Any]] = []
        current_start = start
        while current_start < end:
            current_end = min(end, current_start + 1999 * RESOLUTION_SECONDS[resolution])
            result = await self._get("/v2/history/candles", {"symbol": symbol, "resolution": resolution,
                                                          "start": current_start, "end": current_end})
            if not isinstance(result, list):
                raise DeltaProtocolError("Expected candle list")
            if len(result) > 2000:
                raise DeltaProtocolError("Exchange candle limit exceeded")
            results.extend(result)
            current_start = current_end + 1
        candles: dict[int, dict[str, Any]] = {}
        for row in results:
            if not isinstance(row, dict) or not isinstance(row.get("time"), int):
                raise DeltaProtocolError("Candle is missing integer time")
            timestamp = row["time"]
            if not start <= timestamp <= end:
                raise DeltaProtocolError("Candle timestamp falls outside requested range")
            candle = {"timestamp": timestamp}
            for key in ("open", "high", "low", "close", "volume"):
                candle[key] = _decimal_string(row.get(key))
                if candle[key] is None:
                    raise DeltaProtocolError("Candle is missing OHLCV values")
            if Decimal(candle["low"]) > min(Decimal(candle["open"]), Decimal(candle["close"])) or Decimal(candle["high"]) < max(Decimal(candle["open"]), Decimal(candle["close"])) or Decimal(candle["volume"]) < 0:
                raise DeltaProtocolError("Invalid candle geometry or volume")
            if timestamp in candles and candles[timestamp] != candle:
                raise DeltaProtocolError("Conflicting duplicate candle")
            candles[timestamp] = candle
        return [candles[timestamp] for timestamp in sorted(candles)]

    async def public_messages(self, channel: str, symbols: Iterable[str], *, max_reconnects: int = 5,
                              stale_after: float = 35) -> AsyncIterator[dict[str, Any]]:
        """One shared consumer per feed; caller fans out to independent strategies.

        Re-subscribes after reconnect. A disconnect emits an explicit stream-gap
        event; consumers must backfill candles / reset books before trading.
        ``ob_l2`` is a complete top-15-level snapshot on each event. The
        sequence-sensitive ``ob_updates`` channel is not supported here.
        """
        if channel not in PUBLIC_SNAPSHOT_CHANNELS and (not channel.startswith("candlestick_") or channel.removeprefix("candlestick_") not in WS_RESOLUTIONS):
            raise ValueError("Supported public channels: ticker, ob_l2, mark_price, funding_rate and candlestick_<resolution>")
        selected = list(dict.fromkeys(_symbol(s) for s in symbols))
        if not selected or len(selected) > 100 or stale_after <= 0 or max_reconnects < 0:
            raise ValueError("Invalid subscription limits")
        subscription = {"type": "subscribe", "payload": {"channels": [{"name": channel, "symbols": selected}]}}
        failures = 0
        last_events: dict[tuple[str, str], tuple[int, str]] = {}
        while True:
            try:
                async with connect(self.endpoints.public_ws, open_timeout=15, ping_interval=20,
                                   ping_timeout=10, max_size=2 ** 20, proxy=True) as socket:
                    await socket.send(json.dumps(subscription, separators=(",", ":")))
                    await socket.send('{"type":"enable_heartbeat"}')
                    last_data_at = time.monotonic()
                    while True:
                        raw = await asyncio.wait_for(socket.recv(), timeout=stale_after)
                        try:
                            message = json.loads(raw)
                        except (ValueError, TypeError) as exc:
                            raise DeltaProtocolError("Invalid WebSocket JSON") from exc
                        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                            raise DeltaProtocolError("Invalid WebSocket envelope")
                        if message["type"] == "error":
                            raise DeltaProtocolError("Delta rejected public subscription")
                        if time.monotonic() - last_data_at > stale_after:
                            raise asyncio.TimeoutError("Market data is stale")
                        if message["type"] == channel:
                            if not isinstance(message.get("sy"), str) or not isinstance(message.get("ts"), int):
                                raise DeltaProtocolError("Market event lacks symbol or microsecond timestamp")
                            if message["sy"] not in selected:
                                continue
                            key = (channel, message["sy"])
                            fingerprint = json.dumps(message, sort_keys=True, separators=(",", ":"))
                            previous = last_events.get(key)
                            if previous is not None and message["ts"] <= previous[0]:
                                if message["ts"] == previous[0] and fingerprint == previous[1]:
                                    continue
                                raise DeltaProtocolError("Public data is reordered or conflicts with a prior event")
                            last_events[key] = (message["ts"], fingerprint)
                            last_data_at = time.monotonic()
                            yield message
            except InvalidStatus as exc:
                if exc.response.status_code == 429:
                    raise DeltaStreamUnavailable("WebSocket connection quota exhausted; wait 5-10 minutes") from None
                failures += 1
                yield {"type": "stream_gap", "channel": channel, "requires_recovery": True}
                if failures > max_reconnects:
                    raise DeltaStreamUnavailable("Public stream retry budget exhausted") from None
                await asyncio.sleep(min(30, 2 ** (failures - 1)) + random.uniform(0, .25))
            except (ConnectionClosed, OSError, WebSocketException, asyncio.TimeoutError):
                failures += 1
                yield {"type": "stream_gap", "channel": channel, "requires_recovery": True}
                if failures > max_reconnects:
                    raise DeltaStreamUnavailable("Public stream retry budget exhausted") from None
                await asyncio.sleep(min(30, 2 ** (failures - 1)) + random.uniform(0, .25))


@dataclass(frozen=True, slots=True)
class DemoCredentials:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    environment: Environment = Environment.DEMO

    def __post_init__(self) -> None:
        if self.environment != Environment.DEMO or not self.api_key or not self.api_secret:
            raise ValueError("Nonempty, demo-scoped credentials are required")
        # Managed egress placeholders are replaced at the HTTPS proxy. Local
        # HMAC requires actual secret material, so they cannot sign requests.
        placeholder = re.compile(r"^(?:\$\{|\{\{|<|__CODEX|codex[-_:]proxy|proxy[-_:]secret|secret://|placeholder)", re.IGNORECASE)
        if placeholder.match(self.api_key) or placeholder.match(self.api_secret):
            raise ValueError("Demo HMAC requires securely supplied local credentials, not proxy placeholders")


class DemoClient(DeltaClient):
    """Only India demo private requests. Writes require a separate explicit opt-in.

    These methods must be invoked by an authorized execution coordinator after
    risk approval. Offline tests do not establish real demo authentication.
    """

    def __init__(self, credentials: DemoCredentials, *, allow_demo_orders: bool = False,
                 environment: str | Environment = Environment.DEMO, **kwargs: Any):
        if environment != Environment.DEMO:
            raise ValueError("DemoClient cannot use a production or paper environment")
        if not isinstance(credentials, DemoCredentials) or type(allow_demo_orders) is not bool:
            raise ValueError("Demo credentials and an explicit boolean order authorization are required")
        super().__init__(environment=Environment.DEMO, **kwargs)
        self._credentials = credentials
        self._allow_demo_orders = allow_demo_orders

    async def _private(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                       payload: dict[str, Any] | None = None, with_meta: bool = False) -> Any:
        write = method != "GET"
        if write and not self._allow_demo_orders:
            raise DemoAuthorizationRequired("Explicit demo order authorization is required")
        if self.environment != Environment.DEMO or self.endpoints is not ENDPOINTS[Environment.DEMO]:
            raise DemoAuthorizationRequired("Demo environment guard failed")
        body = encode_body(payload)
        url = path + ("?" + urlencode(params) if params else "")
        attempts = 1 if write else self.max_get_attempts
        for attempt in range(attempts):
            await self._throttle.acquire(5 if write else 3)
            request = self._http.build_request(method, url, content=body)
            if str(request.url).split("/v2/", 1)[0] != ENDPOINTS[Environment.DEMO].rest:
                raise DemoAuthorizationRequired("Private request target is not India demo")
            timestamp = str(int(time.time()))
            raw_path = request.url.raw_path.decode("ascii")
            signing_path, separator, query = raw_path.partition("?")
            request.headers.update({"api-key": self._credentials.api_key, "timestamp": timestamp,
                                    "signature": sign_request(self._credentials.api_secret, method, timestamp,
                                                              signing_path, "?" + query if separator else "", body),
                                    "Content-Type": "application/json"})
            try:
                response = await self._http.send(request)
            except httpx.TransportError:
                if write:
                    raise OrderSubmissionUnknown((payload or {}).get("client_order_id"), f"{method} {path}") from None
                if attempt + 1 == attempts:
                    raise
                await asyncio.sleep(.25 * 2 ** attempt)
                continue
            if write and (response.status_code >= 500 or response.is_redirect):
                raise OrderSubmissionUnknown((payload or {}).get("client_order_id"), f"{method} {path}")
            if not write and response.status_code in (429, 502, 503, 504) and attempt + 1 < attempts:
                delay = _retry_delay(response)
                if delay > 30:
                    raise DeltaAPIError(response.status_code, "rate_limit", retry_after=delay)
                await asyncio.sleep(max(delay, .25 * 2 ** attempt))
                continue
            if response.is_redirect:
                raise DeltaProtocolError("Private endpoint redirected; credentials were not forwarded")
            try:
                result = _json_safe(self._decode(response))
                return {"result": result, "meta": response.json().get("meta", {})} if with_meta else result
            except DeltaProtocolError:
                if write:
                    raise OrderSubmissionUnknown((payload or {}).get("client_order_id"), f"{method} {path}") from None
                raise
        raise AssertionError("Unreachable private request loop")

    async def balances(self) -> Any:
        return await self._private("GET", "/v2/wallet/balances")

    async def _private_pages(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Follow official opaque cursors; fail rather than truncate account state."""
        query = {"page_size": 50, **(params or {})}
        result: list[dict[str, Any]] = []
        cursors: set[str] = set()
        identities: set[str] = set()
        for _ in range(1000):
            envelope = await self._private("GET", path, params=query, with_meta=True)
            rows = envelope["result"]
            if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("id") is None for row in rows):
                raise DeltaProtocolError("Invalid private paginated result")
            for row in rows:
                identity = str(row["id"])
                if identity in identities:
                    raise DeltaProtocolError("Overlapping private pagination requires a new snapshot")
                identities.add(identity)
                result.append(row)
            meta = envelope["meta"]
            cursor = meta.get("after") if isinstance(meta, dict) else None
            if not cursor:
                return result
            if not isinstance(cursor, str) or cursor in cursors:
                raise DeltaProtocolError("Invalid private pagination cursor")
            cursors.add(cursor)
            query["after"] = cursor
        raise DeltaProtocolError("Private pagination budget exceeded; account snapshot incomplete")

    async def active_orders(self) -> list[dict[str, Any]]:
        return await self._private_pages("/v2/orders", {"states": "open,pending"})

    async def fills(self, *, start_time: int | None = None, end_time: int | None = None) -> list[dict[str, Any]]:
        """Times are UTC microseconds as specified by GET /v2/fills."""
        params = {}
        for name, value in (("start_time", start_time), ("end_time", end_time)):
            if value is not None:
                if type(value) is not int or value < 0:
                    raise ValueError("Fill times must be UTC epoch microseconds")
                params[name] = value
        return await self._private_pages("/v2/fills", params)

    async def wallet_transactions(self, *, start_time: int | None = None) -> list[dict[str, Any]]:
        """Official signed cash ledger. Amount is already in the named asset."""
        if start_time is not None and (type(start_time) is not int or start_time < 0):
            raise ValueError("Wallet transaction start must be UTC epoch microseconds")
        return await self._private_pages("/v2/wallet/transactions", {"start_time": start_time} if start_time is not None else None)

    async def positions(self, product_id: int | None = None) -> Any:
        return await self._private("GET", "/v2/positions/margined", params={"product_ids": product_id} if product_id is not None else None)

    async def order(self, order_id: int) -> Any:
        if type(order_id) is not int or order_id <= 0:
            raise ValueError("Positive exchange order ID is required")
        return await self._private("GET", f"/v2/orders/{order_id}")

    async def order_by_client_id(self, client_order_id: str) -> Any:
        if not _CLIENT_ORDER_ID.fullmatch(client_order_id):
            raise ValueError("Client order ID must use 1-32 letters, digits, hyphens or underscores")
        return await self._private("GET", f"/v2/orders/client_order_id/{client_order_id}")

    async def place_order(self, *, product_id: int, side: str, size: int, client_order_id: str,
                          order_type: str = "limit_order", limit_price: str | Decimal | None = None,
                          reduce_only: bool = False, post_only: bool = False, time_in_force: str = "gtc",
                          stop_order_type: str | None = None, stop_price: str | Decimal | None = None,
                          trail_amount: str | Decimal | None = None, stop_trigger_method: str = "mark_price",
                          bracket_stop_loss_price: str | Decimal | None = None,
                          bracket_take_profit_price: str | Decimal | None = None) -> Any:
        if type(product_id) is not int or product_id <= 0 or type(size) is not int or size <= 0:
            raise ValueError("Positive product ID and integer contract quantity are required")
        if side not in ("buy", "sell") or order_type not in ("limit_order", "market_order"):
            raise ValueError("Unsupported demo order side or type")
        if not _CLIENT_ORDER_ID.fullmatch(client_order_id):
            raise ValueError("Client order ID must use 1-32 letters, digits, hyphens or underscores")
        if time_in_force not in ("gtc", "ioc"):
            raise ValueError("Unsupported time in force")
        if type(reduce_only) is not bool or type(post_only) is not bool:
            raise ValueError("Order flags must be boolean")
        body = {"product_id": product_id, "side": side, "size": size, "client_order_id": client_order_id,
                "order_type": order_type, "reduce_only": reduce_only, "post_only": post_only, "time_in_force": time_in_force}
        if order_type == "limit_order":
            price = _decimal_string(limit_price)
            if price is None or Decimal(price) <= 0:
                raise ValueError("Positive limit price is required")
            body["limit_price"] = price
        elif limit_price is not None or post_only:
            raise ValueError("Market orders cannot specify a limit price or post-only")
        if stop_trigger_method not in ("mark_price", "last_traded_price", "spot_price"):
            raise ValueError("Unsupported stop trigger method")
        if stop_order_type is not None:
            if stop_order_type not in ("stop_loss_order", "take_profit_order") or (stop_price is None) == (trail_amount is None):
                raise ValueError("Stop order requires one stop price or trailing amount")
            body.update(stop_order_type=stop_order_type, stop_trigger_method=stop_trigger_method)
        elif stop_price is not None or trail_amount is not None:
            raise ValueError("Stop parameters require a stop order type")
        for name, value in (("stop_price", stop_price), ("trail_amount", trail_amount),
                            ("bracket_stop_loss_price", bracket_stop_loss_price),
                            ("bracket_take_profit_price", bracket_take_profit_price)):
            if value is not None:
                price = _decimal_string(value)
                if price is None or Decimal(price) <= 0:
                    raise ValueError("Protective prices and trailing amount must be positive")
                body[name] = price
        if (bracket_stop_loss_price is None) != (bracket_take_profit_price is None) or (reduce_only and bracket_stop_loss_price is not None):
            raise ValueError("Entry brackets require both stop and target and cannot be reduce-only")
        if bracket_stop_loss_price is not None:
            body["bracket_stop_trigger_method"] = stop_trigger_method
        result = await self._private("POST", "/v2/orders", payload=body)
        valid = (isinstance(result, dict) and type(result.get("id")) is int and result["id"] > 0
                 and result.get("client_order_id") == client_order_id and result.get("product_id") == product_id
                 and result.get("side") == side and result.get("size") == size
                 and result.get("state") in ("open", "pending", "closed", "cancelled"))
        if valid and "unfilled_size" in result:
            valid = type(result["unfilled_size"]) is int and 0 <= result["unfilled_size"] <= size
        if valid and order_type == "limit_order" and result.get("limit_price") is not None:
            try:
                valid = Decimal(str(result["limit_price"])) == Decimal(body["limit_price"])
            except InvalidOperation:
                valid = False
        if not valid:
            raise OrderSubmissionUnknown(client_order_id, "POST /v2/orders")
        for name in ("stop_order_type", "stop_price", "trail_amount", "bracket_stop_loss_price", "bracket_take_profit_price"):
            if name in body:
                echoed = result.get(name)
                if name == "stop_order_type":
                    matched = echoed == body[name]
                else:
                    try:
                        matched = echoed is not None and Decimal(str(echoed)) == Decimal(body[name])
                    except InvalidOperation:
                        matched = False
                if not matched:
                    raise OrderSubmissionUnknown(client_order_id, "POST /v2/orders protection acknowledgment")
        return result

    async def cancel_order(self, *, product_id: int, order_id: int) -> Any:
        if type(product_id) is not int or product_id <= 0 or type(order_id) is not int or order_id <= 0:
            raise ValueError("Positive product and exchange order IDs are required")
        result = await self._private("DELETE", "/v2/orders", payload={"id": order_id, "product_id": product_id})
        if (not isinstance(result, dict) or result.get("id") != order_id or result.get("product_id") != product_id
                or result.get("state") not in ("cancelled", "closed")):
            raise OrderSubmissionUnknown(None, "DELETE /v2/orders")
        return result

    def private_auth_message(self, timestamp: int | None = None) -> dict[str, Any]:
        """Official key-auth schema; real demo authentication remains unverified."""
        value = str(timestamp if timestamp is not None else int(time.time()))
        return {"type": "key-auth", "payload": {"api-key": self._credentials.api_key, "timestamp": int(value),
                                                "signature": sign_request(self._credentials.api_secret, "GET", value, "/live")}}

    async def private_messages(self, channels: Iterable[str], symbols: Iterable[str], *, emit_auth=False) -> AsyncIterator[dict[str, Any]]:
        """Demo only. Explicitly parse authentication before account subscription.

        Caller handles reconciliation before restarting after any disconnect.
        This does not reconnect and silently assume account state is complete.
        """
        selected_channels = list(dict.fromkeys(channels))
        if not selected_channels or any(channel not in ("orders", "positions", "v2/user_trades", "margins") for channel in selected_channels):
            raise ValueError("Unsupported private channel")
        selected_symbols = list(dict.fromkeys(_symbol(symbol) for symbol in symbols))
        if not selected_symbols:
            raise ValueError("Private subscriptions require explicit symbols")
        async with connect(ENDPOINTS[Environment.DEMO].private_ws, open_timeout=15,
                           ping_interval=20, ping_timeout=10, proxy=True) as socket:
            await socket.send(json.dumps(self.private_auth_message(), separators=(",", ":")))
            auth = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
            if not isinstance(auth, dict) or auth.get("type") != "key-auth" or auth.get("success") is not True or auth.get("status") != "authenticated" or auth.get("status_code") != 200:
                raise DemoAuthorizationRequired("Demo private WebSocket authentication was not acknowledged")
            await socket.send('{"type":"enable_heartbeat"}')
            await socket.send(json.dumps({"type": "subscribe", "payload": {"channels": [
                {"name": channel, **({"symbols": selected_symbols} if channel != "margins" else {})} for channel in selected_channels
            ]}}, separators=(",", ":")))
            if emit_auth:
                yield {"type": "private_authenticated", "environment": "DEMO", "requires_recovery": True}
            sequences: dict[tuple[str, str], tuple[int, str]] = {}
            while True:
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=35)
                except (ConnectionClosed, asyncio.TimeoutError):
                    raise DeltaStreamUnavailable("Private stream lost; reconcile before reconnecting") from None
                event = json.loads(raw)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise DeltaProtocolError("Invalid private WebSocket envelope")
                if event["type"] == "error":
                    raise DeltaProtocolError("Delta rejected private subscription")
                if event["type"] in selected_channels:
                    kind = event["type"]
                    symbol = event.get("sy" if kind == "v2/user_trades" else "symbol")
                    sequence = event.get("se") if kind == "v2/user_trades" else event.get("seq_no")
                    if kind == "orders" and event.get("action") == "snapshot":
                        sequence = event.get("meta", {}).get("seq_no")
                    if sequence is not None:
                        if not isinstance(symbol, str) or type(sequence) is not int:
                            raise DeltaProtocolError("Invalid private event sequence")
                        key = (kind, symbol)
                        previous = sequences.get(key)
                        fingerprint = json.dumps(event, sort_keys=True, separators=(",", ":"))
                        if previous and sequence == previous[0] and fingerprint == previous[1]:
                            continue
                        if previous and sequence != previous[0] + 1:
                            yield {"type": "stream_gap", "channel": kind, "symbol": symbol, "requires_recovery": True}
                            raise DeltaStreamUnavailable("Private event sequence gap/reset requires reconciliation")
                        sequences[key] = (sequence, fingerprint)
                    yield event
