"""Explicit, read-only verification of current Delta public feed contracts.

This probe uses no private client or credentials and performs no orders. It is
not run automatically at application startup and does not certify historical
funding economics or private demo/live execution.
"""
import asyncio
import json
from pathlib import Path

from tradebot.db import utcnow
from tradebot.market_stream import normalize_event
from tradebot_delta import DeltaClient


async def check(channel: str, selected: str):
    async with DeltaClient(environment="production") as client:
        stream = client.public_messages(channel, [selected], max_reconnects=0)
        try:
            event = await asyncio.wait_for(anext(stream), timeout=45)
            symbol, kind, stamp, payload = normalize_event(event)
            return {"channel": kind, "symbol": symbol, "status": "passed", "event_time": stamp.isoformat(),
                    "source": client.endpoints.public_ws, "payload": payload, "verification": "actual-public-read-only"}
        except Exception as error:
            return {"channel": channel, "symbol": selected, "status": "failed", "error_type": type(error).__name__,
                    "verification": "actual-public-read-only"}
        finally:
            await stream.aclose()


async def main():
    channels = [("ticker", "BTCUSD"), ("ob_l2", "BTCUSD"), ("mark_price", "MARK:BTCUSD"),
                ("funding_rate", "BTCUSD"), ("candlestick_1m", "BTCUSD")]
    results = await asyncio.gather(*(check(channel, symbol) for channel, symbol in channels))
    now = utcnow()
    evidence = {"observed_at": now.isoformat(), "environment": "production-public-only", "checks": results,
                "limitations": ["No authenticated/private/demo/live exchange operation was tested.",
                "Funding rates retain raw exchange units and are not certified historical accounting inputs.",
                "Compact intrabar candle events are display data; authoritative closed history uses REST."]}
    destination = Path(__file__).resolve().parents[1]/"docs"/"validation"/("market-stream-"+now.date().isoformat()+".json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(evidence, indent=2)+"\n")
    print(json.dumps({"checks": [{"channel": row["channel"], "status": row["status"]} for row in results],
                      "evidence": str(destination.relative_to(destination.parents[2]))}))
    if any(row["status"] != "passed" for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
