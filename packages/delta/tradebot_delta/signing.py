"""Signing uses the exact prepared URL and bytes transmitted by httpx."""

import hashlib
import hmac
import json
from typing import Any


def encode_body(payload: dict[str, Any] | None) -> bytes:
    if payload is None:
        return b""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sign_request(secret: str, method: str, timestamp: str, path: str, query: str = "", body: bytes = b"") -> str:
    if not timestamp.isdigit() or not path.startswith("/") or (query and not query.startswith("?")):
        raise ValueError("Invalid signing inputs")
    message = method.upper().encode("ascii") + timestamp.encode("ascii") + path.encode("ascii") + query.encode("ascii") + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
