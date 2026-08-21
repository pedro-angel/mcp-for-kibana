"""Domain error hierarchy. Adapters translate library errors into these;
tool code translates these into MCP tool errors. Messages must be safe to
show to the calling LLM (no secrets, no stack traces)."""


class KibanaMcpError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class KibanaAuthError(KibanaMcpError):
    """401/403 from Kibana."""


class KibanaNotFound(KibanaMcpError):
    """Requested object does not exist."""


class KibanaSpaceNotFound(KibanaNotFound):
    """The requested *space scope* does not exist (fail-closed validation).

    Distinct from KibanaNotFound so the adapter's scoped-context suffix
    never re-suffixes an error whose subject already is the space."""


class KibanaRejected(KibanaMcpError):
    """Kibana rejected a payload (schema validation, 400)."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class KibanaUnavailable(KibanaMcpError):
    """Connection/timeout/5xx."""


class UnsafeDashboardError(KibanaMcpError):
    """Dashboard contains panels the 9.4 API cannot round-trip (dropped_panel).
    Modifying it via read-modify-write would silently destroy those panels."""
