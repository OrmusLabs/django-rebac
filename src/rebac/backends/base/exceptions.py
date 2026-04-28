# rebac/backends/base/exceptions.py


class RebacError(Exception):
    """Base exception for all ReBAC backend errors."""

    pass


class RebacConnectionError(RebacError):
    """Raised when the underlying ReBAC engine is unreachable or times out."""

    pass


class RebacSchemaError(RebacError):
    """Raised when there is a mismatch between the framework configuration and
    the ReBAC DSL schema.
    """

    pass
