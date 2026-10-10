"""Exchange errors intentionally omit credential headers and raw responses."""


class DeltaError(Exception):
    pass


class DeltaProtocolError(DeltaError):
    pass


class DeltaAPIError(DeltaError):
    def __init__(self, status: int, code: str, *, retry_after: float | None = None):
        self.status = status
        self.code = code
        self.retry_after = retry_after
        super().__init__(f"Delta request rejected: HTTP {status}, code {code}")


class DemoAuthorizationRequired(DeltaError):
    pass


class OrderSubmissionUnknown(DeltaError):
    """A write may have reached Delta. Reconcile; do not automatically retry."""

    def __init__(self, client_order_id: str | None, operation: str):
        self.client_order_id = client_order_id
        self.operation = operation
        super().__init__(f"Demo {operation} outcome UNKNOWN; reconciliation is required")


class DeltaStreamUnavailable(DeltaError):
    pass
