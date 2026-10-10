"""The only source of Delta India environment destinations.

Endpoints match the official docs source at commit 44de5b7589c7ae0348feacd22c76a2dd37b0e003.
Runtime connectivity verification is tracked separately in docs/delta-api.md.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlsplit


class Environment(StrEnum):
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class DeltaEndpoints:
    rest: str
    public_ws: str
    private_ws: str

    def __post_init__(self) -> None:
        for value, scheme in ((self.rest, "https"), (self.public_ws, "wss"), (self.private_ws, "wss")):
            parsed = urlsplit(value)
            if parsed.scheme != scheme or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError("Invalid Delta India endpoint")


ENDPOINTS = MappingProxyType({
    Environment.LIVE: DeltaEndpoints(
        "https://api.india.delta.exchange",
        "wss://public-socket.india.delta.exchange",
        "wss://socket.india.delta.exchange",
    ),
    Environment.DEMO: DeltaEndpoints(
        "https://cdn-ind.testnet.deltaex.org",
        "wss://socket-ind-pub.testnet.deltaex.org",
        "wss://socket-ind.testnet.deltaex.org",
    ),
})


def public_environment(value: str | Environment) -> Environment:
    if value == "production":
        return Environment.LIVE
    environment = Environment(value)
    if environment is Environment.PAPER:
        # Paper has no exchange account: it uses production PUBLIC prices only.
        return Environment.LIVE
    return environment
