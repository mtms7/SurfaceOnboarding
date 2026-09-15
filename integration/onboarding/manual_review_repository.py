"""In-memory storage for exact-snapshot manual-review decisions only."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from .manual_review import ManualReviewDecision


@dataclass(frozen=True, slots=True)
class ManualReviewSaveResult:
    decision: ManualReviewDecision
    created: bool


class InMemoryManualReviewRepository:
    """Preserve one immutable reviewer decision for each workflow snapshot."""

    def __init__(self) -> None:
        self._decisions: dict[tuple[str, str, str], ManualReviewDecision] = {}
        self._lock = RLock()

    def save(self, decision: ManualReviewDecision) -> ManualReviewDecision:
        """Store a new decision or return an identical prior decision idempotently."""
        return self.save_with_status(decision).decision

    def save_with_status(self, decision: ManualReviewDecision) -> ManualReviewSaveResult:
        """Store an immutable decision and report whether this call created it."""
        if not isinstance(decision, ManualReviewDecision):
            raise ValueError("manual_review_repository_requires_decision")
        key = (decision.workflow_record_id, decision.source_revision, decision.intent_hash)
        with self._lock:
            existing = self._decisions.get(key)
            if existing is None:
                self._decisions[key] = decision
                return ManualReviewSaveResult(decision, created=True)
            if existing != decision:
                raise ValueError("manual_review_decision_already_recorded")
            return ManualReviewSaveResult(existing, created=False)

    def get(self, *, workflow_record_id: str, source_revision: str,
            intent_hash: str) -> ManualReviewDecision | None:
        """Read one exact decision snapshot without exposing other review records."""
        with self._lock:
            return self._decisions.get((workflow_record_id, source_revision, intent_hash))
