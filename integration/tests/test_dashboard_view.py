from __future__ import annotations

from datetime import datetime, timezone
import unittest

from integration.onboarding.dashboard_view import (QueueSort, QueueType, ReadOnlyQueueItem,
                                                   SalesforceDisplayStage, render_read_only_detail,
                                                   render_read_only_queue, sort_read_only_queue)
from integration.onboarding.models import EngineValue


def item(reference: str, queue_type: QueueType, route: EngineValue, minute: int) -> ReadOnlyQueueItem:
    return ReadOnlyQueueItem(reference, queue_type, route, SalesforceDisplayStage.NEW,
                             "mapping_not_approved", datetime(2026, 9, 10, 12, minute,
                                                                tzinfo=timezone.utc))


class DashboardViewTests(unittest.TestCase):
    def test_safe_queue_item_renders_compact_salesforce_style_columns_and_link(self):
        page = render_read_only_queue((item("CO-TEST-001", QueueType.BLOCKED, EngineValue.CASE_3, 1),))
        for expected in ("CO-TEST-001", "case_3_combined_baseline", "New", "Mapping Not Approved",
                         "Queue type", "Last updated", 'href="/co/CO-TEST-001"'):
            self.assertIn(expected, page)
        self.assertNotIn("<script", page)
        self.assertNotIn("salesforce.com", page)

    def test_queue_type_is_default_sort_and_other_safe_sorts_are_deterministic(self):
        items = (
            item("CO-TEST-003", QueueType.ELIGIBLE, EngineValue.CASE_2, 3),
            item("CO-TEST-002", QueueType.BLOCKED, EngineValue.CASE_3, 2),
            item("CO-TEST-001", QueueType.MANUAL_REVIEW, EngineValue.CASE_1, 1),
        )
        self.assertEqual([row.reference for row in sort_read_only_queue(items)],
                         ["CO-TEST-002", "CO-TEST-001", "CO-TEST-003"])
        self.assertEqual([row.reference for row in sort_read_only_queue(items, sort_by=QueueSort.ROUTE)],
                         ["CO-TEST-001", "CO-TEST-003", "CO-TEST-002"])
        self.assertEqual([row.reference for row in sort_read_only_queue(items, sort_by=QueueSort.LAST_UPDATED)],
                         ["CO-TEST-003", "CO-TEST-002", "CO-TEST-001"])

    def test_detail_is_read_only_and_rejects_raw_or_malformed_values(self):
        detail = render_read_only_detail(item("CO-TEST-001", QueueType.BLOCKED, EngineValue.CASE_1, 1))
        self.assertIn("CO-TEST-001", detail)
        self.assertIn('href="/"', detail)
        for label in ("Account", "Onboarding Product", "Onboarding Type", "Primary User",
                      "Main Domain", "Alternative Domains", "Email Domains", "Onboarding Comments",
                      "Onboarding Stage", "Surface Account ID", "Account UUID"):
            self.assertIn(label, detail)
        self.assertEqual(detail.count("Not loaded in synthetic preview"), 11)
        for forbidden in ("<form", "<button", "<script", "password", "token"):
            self.assertNotIn(forbidden, detail.lower())
        with self.assertRaisesRegex(ValueError, "reference"):
            item("<customer name>", QueueType.BLOCKED, EngineValue.CASE_1, 1)
        with self.assertRaisesRegex(ValueError, "reason"):
            ReadOnlyQueueItem("CO-TEST-001", QueueType.BLOCKED, EngineValue.CASE_1,
                              SalesforceDisplayStage.NEW, "raw payload: value",
                              datetime.now(timezone.utc))

    def test_empty_queue_is_safe_and_non_actionable(self):
        page = render_read_only_queue(())
        self.assertIn("No safe queue metadata is available.", page)
        self.assertNotIn("<form", page)
