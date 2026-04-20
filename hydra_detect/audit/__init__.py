"""Thread-safe bounded audit sink + summary endpoint backing."""

from .audit_log import (
    AuditSink,
    attach_to_logger,
    get_default_sink,
)

__all__ = ["AuditSink", "attach_to_logger", "get_default_sink"]
