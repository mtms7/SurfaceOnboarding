"""Thread-safe in-memory reference repository for metadata-only workflow items.

This is a test double for the later PostgreSQL repository. It has no file,
database, network, or source-payload access.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from .models import WorkflowItem, WorkflowState


@dataclass(frozen=True, slots=True)
class UpsertResult:
    item: WorkflowItem
    created: bool
    changed: bool


class InMemoryWorkflowRepository:
    """Enforce record/revision compare-and-swap and global idempotency uniqueness."""

    def __init__(self) -> None:
        self._items: dict[str, WorkflowItem] = {}
        self._idempotency_index: dict[str, str] = {}
        self._lock = RLock()

    def get(self, salesforce_record_id: str) -> WorkflowItem | None:
        with self._lock:
            return self._items.get(salesforce_record_id)

    def list_items(self) -> tuple[WorkflowItem, ...]:
        """Return immutable metadata records for a local queue projection."""
        with self._lock:
            return tuple(self._items.values())

    def upsert(self, item: WorkflowItem, *, expected_source_revision: str | None = None) -> UpsertResult:
        """Atomically insert or compare-and-swap one non-sensitive workflow item.

        A changed source revision is a new source snapshot and must restart at
        ``discovered``. An unchanged source revision is idempotent only when
        the entire immutable item is equal; state changes use ``save``.
        """
        with self._lock:
            existing = self._items.get(item.salesforce_record_id)
            indexed_record_id = self._idempotency_index.get(item.idempotency_key)
            if indexed_record_id is not None and indexed_record_id != item.salesforce_record_id:
                raise ValueError("idempotency_key_already_bound")

            if existing is None:
                if expected_source_revision is not None:
                    raise ValueError("expected_revision_without_existing_item")
                self._store(item)
                return UpsertResult(item, created=True, changed=True)

            if expected_source_revision is None and item == existing:
                return UpsertResult(existing, created=False, changed=False)
            if expected_source_revision != existing.source_revision:
                raise ValueError("stale_source_revision")
            if item.source_revision == existing.source_revision:
                if item != existing:
                    raise ValueError("same_revision_conflict_requires_save")
                return UpsertResult(existing, created=False, changed=False)
            if item.state is not WorkflowState.DISCOVERED:
                raise ValueError("new_source_revision_must_restart_discovered")

            self._idempotency_index.pop(existing.idempotency_key, None)
            self._store(item)
            return UpsertResult(item, created=False, changed=True)

    def save(self, item: WorkflowItem, *, expected_source_revision: str, expected_state: WorkflowState) -> WorkflowItem:
        """Atomically persist a state-machine result for the current revision."""
        with self._lock:
            existing = self._items.get(item.salesforce_record_id)
            if existing is None:
                raise ValueError("workflow_item_not_found")
            if expected_source_revision != existing.source_revision or item.source_revision != existing.source_revision:
                raise ValueError("stale_source_revision")
            if expected_state is not existing.state:
                raise ValueError("stale_workflow_state")
            if item.idempotency_key != existing.idempotency_key:
                raise ValueError("idempotency_key_cannot_change_during_state_transition")
            if self._idempotency_index.get(item.idempotency_key) != item.salesforce_record_id:
                raise ValueError("idempotency_index_inconsistent")
            self._items[item.salesforce_record_id] = item
            return item

    def _store(self, item: WorkflowItem) -> None:
        self._items[item.salesforce_record_id] = item
        self._idempotency_index[item.idempotency_key] = item.salesforce_record_id
