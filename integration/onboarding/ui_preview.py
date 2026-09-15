"""Static, redacted visual preview for the future operator queue.

The preview has no listener, external data access, authentication, or workflow
actions. It exists solely to review the future UI's security posture before an
SSO-protected implementation is authorized.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .dashboard_view import (QueueType, ReadOnlyQueueItem, SalesforceDisplayStage,
                             render_read_only_detail, render_read_only_queue)
from .models import EngineValue


def preview_items() -> tuple[ReadOnlyQueueItem, ...]:
    """Synthetic safe rows only; this is not a Salesforce data source."""
    return (
        ReadOnlyQueueItem("CO-DEMO-0001", QueueType.BLOCKED, EngineValue.CASE_3,
                          SalesforceDisplayStage.NEW, "mapping_not_approved",
                          datetime(2026, 9, 10, 12, tzinfo=timezone.utc)),
        ReadOnlyQueueItem("CO-DEMO-0002", QueueType.MANUAL_REVIEW, EngineValue.CASE_1,
                          SalesforceDisplayStage.REQUEST_APPROVED, "source_review_required",
                          datetime(2026, 9, 10, 11, tzinfo=timezone.utc)),
    )


def render_dashboard_preview() -> str:
    """Return a self-contained, data-free HTML preview with no executable code."""
    return render_read_only_queue(preview_items())


def render_dashboard_detail(reference: str) -> str | None:
    """Return a synthetic safe detail page only for a known preview reference."""
    return next((render_read_only_detail(item) for item in preview_items() if item.reference == reference), None)
