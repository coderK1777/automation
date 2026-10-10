#!/usr/bin/env python3
"""Real public checks only. Does not read credentials or issue private writes.

Run: .venv/bin/python scripts/check_delta.py --environment both --output /tmp/delta-evidence.json
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

# Support running from a checkout before the package is installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "delta"))
from tradebot_delta import DeltaClient  # noqa: E402


async def check(environment: str, *, websocket: bool) -> dict:
    evidence = {"environment": environment, "kind": "actual_public_exchange_check", "checks": []}
    async with DeltaClient(environment, timeout=10, max_get_attempts=1) as client:
        async def record(name, operation):
            try:
                details = await operation()
                evidence["checks"].append({"name": name, "status": "passed", "details": details})
            except Exception as exc:
                # Do not dump response/request headers or credential-bearing objects.
                message = str(exc)
                message = message[:240] if not any(word in message.lower() for word in ("api-key", "signature", "authorization")) else "Request rejected; inspect configured permissions"
                evidence["checks"].append({"name": name, "status": "failed", "error_type": type(exc).__name__, "reason": message})

        async def products():
            discovered = await client.products()
            selected = [{key: product[key] for key in ("id", "symbol", "contract_type", "notional_type", "contract_value", "contract_value_currency", "tick_size", "quote_asset", "settlement_asset", "trading_status", "metadata_verified")}
                        for product in discovered if product["symbol"] in ("BTCUSD", "ETHUSD")]
            if not selected:
                raise RuntimeError("Preferred futures absent from actual product discovery")
            return {"product_count": len(discovered), "preferred_products": selected}

        async def ticker():
            result = await client.ticker("BTCUSD")
            return {key: result.get(key) for key in ("symbol", "product_id", "mark_price", "close")}

        async def candles():
            end = int(time.time()) // 60 * 60 - 60
            rows = await client.candles("BTCUSD", "1m", end - 60 * 15, end)
            if not rows:
                raise RuntimeError("Actual candle response contained no data")
            return {"count": len(rows), "first_timestamp": rows[0]["timestamp"], "last_timestamp": rows[-1]["timestamp"]}

        async def stream():
            messages = client.public_messages("ticker", ["BTCUSD"], max_reconnects=0)
            try:
                message = await asyncio.wait_for(anext(messages), timeout=20)
                if message.get("type") != "ticker":
                    raise RuntimeError("Public stream disconnected before validated ticker")
                return {"type": message["type"], "symbol": message["sy"], "timestamp_us": message["ts"]}
            finally:
                await messages.aclose()

        await record("products", products)
        await record("ticker", ticker)
        await record("historical_candles", candles)
        if websocket:
            await record("public_websocket_ticker", stream)
    evidence["private_demo_tests"] = "not executed: no credentials used by public smoke script"
    return evidence


async def main(args):
    environments = ("production", "demo") if args.environment == "both" else (args.environment,)
    results = await asyncio.gather(*(check(environment, websocket=not args.no_websocket) for environment in environments))
    output = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "tls_verification": "enabled",
              "production_private_orders": "structurally unavailable", "results": results}
    rendered = json.dumps(output, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if all(check["status"] == "passed" for result in results for check in result["checks"]) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("production", "demo", "both"), default="both")
    parser.add_argument("--no-websocket", action="store_true")
    parser.add_argument("--output", type=Path)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
