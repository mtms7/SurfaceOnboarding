"""Volatile storage for already-validated, metadata-only audit events."""

from __future__ import annotations

from threading import RLock

from .audit import AuditEvent


class InMemoryAuditRepository:
    """Append and read local audit metadata without a file or database backend."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = RLock()

    def record(self, event: AuditEvent) -> AuditEvent:
        """Append one validated event and return the immutable event unchanged."""
        if not isinstance(event, AuditEvent):
            raise ValueError("audit_repository_requires_audit_event")
        with self._lock:
            self._events.append(event)
        return event

    def list_events(self) -> tuple[AuditEvent, ...]:
        """Return a stable snapshot in append order; events remain process-local."""
        with self._lock:
            return tuple(self._events)
