"""Safe operator projection for local audit metadata."""

from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditEvent, AuditEventType
from .models import ReasonCode


@dataclass(frozen=True, slots=True)
class AuditRow:
    """Operator-visible audit fields; correlation identifiers are intentionally absent."""

    event_type: AuditEventType
    actor: str
    occurred_at: str
    reason_code: ReasonCode | None


def audit_rows(events: tuple[AuditEvent, ...]) -> tuple[AuditRow, ...]:
    """Project validated local events in append order without IDs or source metadata."""
    return tuple(
        AuditRow(event.event_type, event.actor_id, event.occurred_at.isoformat(), event.reason_code)
        for event in events
    )
