"""Safe queue projection without a web server or live-source fetch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import EngineValue, ReasonCode, WorkflowItem, WorkflowState
from .repository import InMemoryWorkflowRepository


@dataclass(frozen=True, slots=True)
class QueueRow:
    """Display-safe metadata only; source contents remain outside this view."""

    co_number: str
    engine_value: EngineValue
    state: WorkflowState
    reason_code: ReasonCode | None
    updated_at: datetime


def project_queue(items: tuple[WorkflowItem, ...]) -> tuple[QueueRow, ...]:
    """Sort newest first without returning record IDs, hashes, or source data."""
    rows = tuple(
        QueueRow(item.co_number, item.engine_value, item.state, item.reason_code, item.updated_at)
        for item in items
    )
    return tuple(sorted(rows, key=lambda row: (row.updated_at, row.co_number), reverse=True))


def queue_rows(repository: InMemoryWorkflowRepository) -> tuple[QueueRow, ...]:
    return project_queue(repository.list_items())
