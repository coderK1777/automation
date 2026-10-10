"""Dedicated Delta India public and demo adapter. No live order capability."""

from .client import DeltaClient, DemoClient, DemoCredentials, normalize_product
from .config import ENDPOINTS, Environment
from .errors import (DeltaAPIError, DeltaError, DeltaProtocolError, DeltaStreamUnavailable,
                     DemoAuthorizationRequired, OrderSubmissionUnknown)
from .signing import encode_body, sign_request

__all__ = ["DeltaClient", "DemoClient", "DemoCredentials", "Environment", "ENDPOINTS",
           "DeltaError", "DeltaAPIError", "DeltaProtocolError", "DeltaStreamUnavailable",
           "DemoAuthorizationRequired", "OrderSubmissionUnknown", "normalize_product",
           "encode_body", "sign_request"]
