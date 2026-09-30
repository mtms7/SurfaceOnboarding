from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from tools.serve_attended_open_onboardings_dashboard import (
    LEONARDO_DEVELOPMENT_TENANT_MANAGEMENT,
    PRODUCTION_BACKOFFICE_LOGIN,
    _manual_start_acks,
    consume_manual_start_ack,
    grant_manual_start_ack,
    manual_start_ack_is_active,
    open_attended_leonardo_tenant_management,
    open_attended_production_backoffice_login,
    open_onboardings_view_id,
    page_detail,
    listener_address,
    page_queue,
    page_salesforce_unavailable,
    page_salesforce_login_opened,
    page_comment_update_confirmation,
    evaluate_co0745_renewal_comment,
    evaluate_ce_only_fill_preflight,
    page_ce_only_fill_preflight,
    page_ce_only_runner_status,
    page_co0745_renewal_evaluation,
    salesforce_cli_command,
    start_attended_salesforce_login,
    CommentUpdateEvaluation,
    consume_comment_update_ack,
    issue_comment_update_ack,
    update_co0741_comment_after_confirmation,
)
from tools.attended_ce_only_playwright import ce_only_names, one_email_domain


class AttendedOpenOnboardingsDashboardTests(unittest.TestCase):
    def test_source_ready_detail_offers_only_an_attended_login_preflight(self):
        page = page_detail("CO-0717", {"Onboarding_Approval_Status__c": "Approved"})
        self.assertIn("Leonardo Development session check", page)
        self.assertIn("browser will show tenant management", page)
        self.assertIn("/attended/leonardo-session-check", page)
        self.assertIn("Start manual onboarding", page)
        self.assertIn("disabled", page)
        self.assertNotIn("password", page.lower())
        self.assertNotIn("token", page.lower())

    def test_session_check_opens_only_the_exact_development_tenant_route(self):
        opened: list[str] = []
        with patch("tools.serve_attended_open_onboardings_dashboard.local_browser_launch_allowed", return_value=True):
            self.assertTrue(open_attended_leonardo_tenant_management(opener=lambda url: opened.append(url) or True))
        self.assertEqual(opened, [LEONARDO_DEVELOPMENT_TENANT_MANAGEMENT])

    def test_session_check_fails_closed_when_browser_rejects_launch(self):
        self.assertFalse(open_attended_leonardo_tenant_management(opener=lambda _url: False))

    def test_production_renewal_preflight_opens_only_the_login_page(self):
        opened: list[str] = []
        with patch("tools.serve_attended_open_onboardings_dashboard.local_browser_launch_allowed", return_value=True):
            self.assertTrue(open_attended_production_backoffice_login(opener=lambda url: opened.append(url) or True))
        self.assertEqual(opened, [PRODUCTION_BACKOFFICE_LOGIN])

    def test_renewal_detail_offers_production_preflight_without_tenant_action(self):
        page = page_detail("CO-0745", {"Onboarding_Type__c": "Renewal of Existing Product"})
        self.assertIn("Production renewal account validation", page)
        self.assertIn("/attended/production-renewal-preflight", page)
        self.assertIn("stops before any tenant search, edit, or save action", page)

    def test_fresh_session_ack_enables_one_attested_manual_start_for_same_revision(self):
        _manual_start_acks.clear()
        row = {"Onboarding_Approval_Status__c": "Approved", "LastModifiedDate": "2026-09-12T17:00:00Z"}
        nonce = grant_manual_start_ack("CO-0717", row)
        self.assertIsNotNone(nonce)
        self.assertTrue(manual_start_ack_is_active("CO-0717", row))
        page = page_detail("CO-0717", row)
        self.assertIn("I attest that my Leonardo Development admin session is active", page)
        self.assertIn("<button type='submit'>Start manual onboarding</button>", page)
        self.assertTrue(consume_manual_start_ack("CO-0717", row, nonce or ""))
        self.assertFalse(consume_manual_start_ack("CO-0717", row, nonce or ""))

    def test_session_ack_fails_closed_on_source_revision_drift_or_expiry(self):
        _manual_start_acks.clear()
        row = {"Onboarding_Approval_Status__c": "Approved", "LastModifiedDate": "2026-09-12T17:00:00Z"}
        self.assertIsNotNone(grant_manual_start_ack("CO-0717", row, now=100))
        drifted = {"Onboarding_Approval_Status__c": "Approved", "LastModifiedDate": "2026-09-12T17:01:00Z"}
        self.assertFalse(manual_start_ack_is_active("CO-0717", drifted, now=101))
        self.assertIsNotNone(grant_manual_start_ack("CO-0717", row, now=100))
        self.assertFalse(manual_start_ack_is_active("CO-0717", row, now=100 + 15 * 60))

    def test_history_month_keys_cover_thirteen_months_ending_this_month(self):
        from datetime import date
        keys = dashboard.history_month_keys(date(2026, 9, 29))
        self.assertEqual(len(keys), 13)
        self.assertEqual((keys[0], keys[-1]), ((2025, 9), (2026, 9)))
        self.assertEqual(dashboard.history_month_keys(date(2026, 1, 5))[-2:], [(2025, 12), (2026, 1)])

    def test_closed_history_zero_fills_orders_series_and_folds_unknown_products(self):
        from datetime import date
        completed = [
            {"y": 2026, "m": 9, "p": "Credential Exposure", "n": 20},
            {"y": 2026, "m": 9, "p": "Surface", "n": 2},
            {"y": 2026, "m": 8, "p": "Surface & Credential Exposure", "n": 24},
            {"y": 2026, "m": 8, "p": None, "n": 1},
            {"y": 2024, "m": 1, "p": "Surface", "n": 99},  # outside the window: ignored
        ]
        created = [{"y": 2026, "m": 9, "n": 40}, {"y": 2026, "m": 8, "n": 43}]
        history = dashboard.build_closed_history(completed, created, date(2026, 9, 29))
        self.assertEqual(history.series, ("Credential Exposure", "Surface & Credential Exposure", "Surface", "Other"))
        self.assertEqual(len(history.months), 13)
        self.assertEqual(history.months[-1].completed, (20, 0, 2, 0))
        self.assertEqual(history.months[-2].completed, (0, 24, 0, 1))
        self.assertEqual(history.months[0].completed, (0, 0, 0, 0))
        self.assertEqual((history.months[-1].created, history.months[-2].created), (40, 43))
        self.assertEqual(history.completed_total, 47)
        self.assertEqual(history.months[-1].label, "Sep 2026")

    def test_closed_history_has_no_other_series_when_every_product_is_known(self):
        from datetime import date
        history = dashboard.build_closed_history([{"y": 2026, "m": 9, "p": "Surface", "n": 3}], [], date(2026, 9, 29))
        self.assertEqual(history.series, dashboard.HISTORY_PRODUCTS)
        self.assertEqual(history.months[-1].completed, (0, 0, 3))

    def test_closed_history_rejects_malformed_aggregate_rows(self):
        from datetime import date
        for bad in ({"y": 2026, "m": 9, "p": "Surface", "n": -1}, {"y": "2026", "m": 9, "n": 1},
                    {"y": 2026, "m": 9, "n": 1.5}, {"y": 2026, "m": 9, "n": True}, "not-a-row"):
            with self.subTest(bad=bad), self.assertRaises(dashboard.ReadUnavailable):
                dashboard.build_closed_history([bad], [], date(2026, 9, 29))

    @staticmethod
    def _fake_history_sf(calls):
        def fake_sf_json(args):
            calls.append(args[3])
            if args[3] == dashboard.REJECTED_TOTAL_QUERY:
                return {"status": 0, "result": {"records": [{"n": 99}]}}
            return {"status": 0, "result": {"records": []}}
        return fake_sf_json

    def test_closed_history_is_cached_and_uses_only_the_fixed_queries(self):
        from datetime import date, datetime, timezone
        calls = []
        now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        with patch.object(dashboard, "sf_json", side_effect=self._fake_history_sf(calls)), \
                patch.object(dashboard, "_history_cache", None):
            first = dashboard.closed_history(now=1000.0, today=date(2026, 9, 29), wall_clock=now)
            second = dashboard.closed_history(now=1000.0 + 60, today=date(2026, 9, 29), wall_clock=now)
            self.assertIs(first, second)
            self.assertEqual(calls, [dashboard.COMPLETED_HISTORY_QUERY, dashboard.CREATED_HISTORY_QUERY,
                                     dashboard.COMPLETION_DURATIONS_QUERY, dashboard.REJECTED_TOTAL_QUERY])
            dashboard.closed_history(now=1000.0 + dashboard.HISTORY_CACHE_SECONDS + 1, today=date(2026, 9, 29), wall_clock=now)
            self.assertEqual(len(calls), 8)
            # The cache holds only derived, frozen numbers.
            cached = dashboard._history_cache[1]
            self.assertIsInstance(cached, dashboard.ClosedHistory)
            self.assertEqual(cached.kpis.rejected_total, 99)
        for query in (dashboard.COMPLETED_HISTORY_QUERY, dashboard.CREATED_HISTORY_QUERY, dashboard.REJECTED_TOTAL_QUERY):
            self.assertIn("COUNT(Id)", query)
        # The per-record query selects only the two timestamps.
        self.assertTrue(dashboard.COMPLETION_DURATIONS_QUERY.startswith(
            "SELECT CreatedDate, Completed_Time_Stamp__c FROM Customer_Onboarding__c"))
        self.assertIn("LIMIT 2000", dashboard.COMPLETION_DURATIONS_QUERY)

    def test_closed_history_failure_is_cached_briefly_and_raises(self):
        calls = []

        def failing(args):
            calls.append(args)
            raise dashboard.ReadUnavailable()

        with patch.object(dashboard, "sf_json", side_effect=failing), \
                patch.object(dashboard, "_history_cache", None):
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.closed_history(now=5.0)
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.closed_history(now=5.0 + 30)  # cached failure: no second Salesforce call
            self.assertEqual(len(calls), 1)
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.closed_history(now=5.0 + dashboard.HISTORY_FAILURE_CACHE_SECONDS + 1)
            self.assertEqual(len(calls), 2)

    def test_closed_kpis_counts_windows_median_and_rejected(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)

        def pair(created, completed):
            return {"CreatedDate": created, "Completed_Time_Stamp__c": completed}

        records = [
            pair("2026-09-01T12:00:00.000+0000", "2026-09-11T12:00:00.000+0000"),  # 10 days, in last 30
            pair("2026-09-20T12:00:00.000+0000", "2026-09-22T12:00:00.000+0000"),  # 2 days, in last 30
            pair("2026-07-20T12:00:00.000+0000", "2026-08-15T12:00:00.000+0000"),  # 26 days, previous 30
        ]
        kpis = dashboard.build_closed_kpis(records, [{"n": 99}], now)
        self.assertEqual((kpis.completed_30d, kpis.completed_prev_30d), (2, 1))
        self.assertEqual(kpis.median_days_90d, 10.0)
        self.assertEqual((kpis.median_sample, kpis.rejected_total), (3, 99))
        # A read that hit the row limit reports no median instead of a biased one.
        full = [records[0]] * dashboard.HISTORY_DURATION_LIMIT
        self.assertIsNone(dashboard.build_closed_kpis(full, [{"n": 1}], now).median_days_90d)
        with self.assertRaises(dashboard.ReadUnavailable):
            dashboard.build_closed_kpis([pair("not-a-date", "2026-09-11T12:00:00.000+0000")], [{"n": 1}], now)

    @staticmethod
    def _history_fixture():
        from datetime import date
        completed = [{"y": 2026, "m": m, "p": p, "n": n} for m, p, n in (
            (6, "Credential Exposure", 35), (6, "Surface & Credential Exposure", 23), (6, "Surface", 2),
            (8, "Credential Exposure", 26), (8, "Surface & Credential Exposure", 24), (8, "Surface", 9),
            (9, "Credential Exposure", 20), (9, "Surface", 2))]
        created = [{"y": 2026, "m": 6, "n": 37}, {"y": 2026, "m": 8, "n": 43}, {"y": 2026, "m": 9, "n": 40}]
        history = dashboard.build_closed_history(completed, created, date(2026, 9, 29))
        kpis = dashboard.ClosedKpis(41, 35, 12.4, 131, 99)
        return dashboard.ClosedHistory(history.series, history.months, history.as_of, kpis)

    def test_history_chart_renders_stacked_months_ticks_legend_and_table(self):
        html = dashboard.render_closed_history(self._history_fixture(), "20:45")
        self.assertIn("<svg viewBox='0 0 880 200' role='img'", html)
        self.assertIn("Closed onboardings per month", html)
        for name in ("Credential Exposure", "Surface &amp; Credential Exposure", "Surface", "New COs created"):
            self.assertIn(name, html)
        self.assertEqual(html.count("class='qh-hit'"), 13)  # one hover band per month
        self.assertIn("to date", html)  # the current month is marked partial
        self.assertIn("Sep 2026 · to 29 Sep", html)
        self.assertIn("<details class='qh-table'><summary>Table view</summary>", html)
        self.assertIn("99 COs rejected all time", html)
        self.assertIn("41", html)
        self.assertIn("+6 vs previous 30 days", html)
        self.assertIn("12 days", html)
        # The hover overlay is hidden from assistive tech (the table carries the values).
        self.assertIn("class='qh-hits' aria-hidden='true'", html)
        self.assertNotIn("tabindex", html)
        self.assertNotIn("<script", html)

    def test_history_chart_labels_selectively_and_never_the_partial_month(self):
        html = dashboard.render_closed_history(self._history_fixture(), "20:45")
        labels = re.findall(r"<text class='cap'[^>]*>([0-9,]+)</text>", html)
        self.assertLessEqual(len(labels), 2)
        self.assertNotIn("22", labels)  # September (partial, 22 completed) is never labelled

    def test_history_chart_empty_state_and_nice_ticks(self):
        from datetime import date
        empty = dashboard.build_closed_history([], [], date(2026, 9, 29))
        html = dashboard.render_closed_history(empty, "20:45")
        self.assertIn("No onboardings were completed or created", html)
        self.assertNotIn("<svg", html)
        self.assertEqual(dashboard.nice_ticks(94), (0, 20, 40, 60, 80, 100))
        self.assertEqual(dashboard.nice_ticks(3), (0, 1, 2, 3))
        self.assertEqual(dashboard.nice_ticks(0), (0, 1))

    def test_history_card_unavailable_state_keeps_the_queue(self):
        card = dashboard.history_card(None, "20:45", failed=True)
        self.assertIn("History unavailable", card)
        self.assertIn("The queue above is unaffected", card)

    def test_queue_classification_rules(self):
        base = {"Name": "CO-9001", "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding"}
        cases = [
            (dict(base, Onboarding_Approval_Status__c="Approved", Onboarding_Stage__c="Request Approved"), None,
             "ready", "Start onboarding", "you"),
            (dict(base, Onboarding_Product__c="Surface", Onboarding_Approval_Status__c="Approved",
                  Onboarding_Stage__c="Request Approved"), None, "ready", "Review scope, start", "you"),
            (dict(base, Onboarding_Type__c="Renewal of Existing Product", Onboarding_Approval_Status__c="Approved",
                  Onboarding_Stage__c="Request Approved"), None, "ready", "Onboard manually", "you"),
            (dict(base, Onboarding_Approval_Status__c="Approved", Onboarding_Stage__c="Request Approved"),
             {"source_revision": "r", "result": "readback_verified"}, "scanning", "Update Salesforce", "you"),
            (dict(base, Onboarding_Approval_Status__c="Approved", Onboarding_Stage__c="Request Approved"),
             {"source_revision": "r", "result": "duplicate_found"}, "review", "Review existing tenant", "you"),
            (dict(base, Onboarding_Approval_Status__c="Approved", Onboarding_Stage__c="Request Approved"),
             {"source_revision": "r", "result": "fill_form_schema_unavailable"}, "review", "Review failed run", "you"),
            (dict(base, Onboarding_Approval_Status__c="Approved", Onboarding_Stage__c="Request Approved"),
             {"source_revision": "r", "started_on": "2026-09-29T10:00:00"}, "ready", "Waiting · runner", "runner"),
            (dict(base, Onboarding_Approval_Status__c="Approved", Onboarding_Stage__c="Account Scanning"), None,
             "scanning", "Waiting · Leonardo scan", "leonardo"),
            (dict(base, Onboarding_Approval_Status__c="Approved", Onboarding_Stage__c="Scan Completed Successfully"), None,
             "scanning", "Create customer user", "you"),
            (dict(base, Onboarding_Approval_Status__c="Approved", Onboarding_Stage__c="User Created"), None,
             "scanning", "Complete onboarding", "you"),
            (dict(base, Onboarding_Approval_Status__c="Pending", Onboarding_Stage__c="New"), None,
             "validation", "Validate DealHub term", "you"),
            (dict(base, Onboarding_Stage__c="New"), None, "review", "Set approval status", "you"),
            (dict(base, Onboarding_Approval_Status__c="Pending", Onboarding_Stage__c="Request Approved"), None,
             "review", "Review source record", "you"),
        ]
        for row, record, key, step, owner in cases:
            with self.subTest(step=step, key=key):
                got_key, got_step, got_owner = dashboard.classify_queue_row(row, record)
                self.assertEqual((got_key, got_owner), (key, owner))
                self.assertIn(step, got_step)

    def test_dashboard_page_uses_the_shell_tiles_tables_and_history(self):
        rows = [
            {"Name": "CO-9002", "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "Request Approved",
             "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
             "Account__r.Name": "Example Account 02", "Submission_Date__c": "2026-09-10"},
            {"Name": "CO-9003", "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "Request Approved",
             "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
             "Account__r.Name": "Example Account 03", "Submission_Date__c": "2026-09-12", "Local_Leonardo_State": "No scan started"},
        ]
        from datetime import date
        page = page_queue(rows, runner_state={"CO-9003": {"source_revision": "r", "result": "readback_verified"}},
                          history=self._history_fixture(), read_at="20:45", today=date(2026, 9, 29))
        self.assertIn("class='side'", page)  # the shared Pentera shell
        self.assertIn("<nav class='tiles' aria-label='Queues'>", page)
        self.assertIn("aria-current='page'", page)  # "All open" tile is current
        self.assertIn("<table class='q'>", page)
        self.assertIn("Start onboarding", page)
        self.assertIn("Update Salesforce", page)  # an onboarded CO is a follow-up, not "ready" again
        self.assertIn("chip chip-ok'>Onboarded", page)
        self.assertIn("19 d", page)
        self.assertIn("Read from Salesforce at 20:45", page)
        self.assertIn("id='history'", page)
        self.assertNotIn("<script", page)
        filtered = page_queue(rows, "ready", history=self._history_fixture(), read_at="20:45")
        self.assertNotIn("id='history'", filtered)  # the chart shows only on the unfiltered view
        self.assertIn("<h1>Ready to onboard</h1>", filtered)

    def test_dashboard_page_degrades_when_run_records_are_unreadable(self):
        page = page_queue([{"Name": "CO-9002", "Onboarding_Approval_Status__c": "Approved",
                            "Onboarding_Stage__c": "Request Approved"}], runner_state_unavailable=True)
        self.assertIn("Local run results unavailable", page)

    def test_empty_queue_and_empty_bucket_states(self):
        self.assertIn("No open onboardings", page_queue([]))
        page = page_queue([{"Name": "CO-9002", "Onboarding_Approval_Status__c": "Pending", "Onboarding_Stage__c": "New"}], "ready")
        self.assertIn("Nothing in Ready to onboard right now", page)

    def test_render_dashboard_isolates_history_failures(self):
        rows = [{"Name": "CO-9002", "Onboarding_Approval_Status__c": "Pending", "Onboarding_Stage__c": "New"}]
        with patch.object(dashboard, "queue_rows", return_value=rows), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "closed_history", side_effect=dashboard.ReadUnavailable()):
            page = dashboard.render_dashboard("")
        self.assertIn("History unavailable", page)
        self.assertIn("CO-9002", page)
        with patch.object(dashboard, "queue_rows", return_value=rows), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "closed_history", side_effect=AssertionError("filtered views skip the history read")):
            dashboard.render_dashboard("validation")

    def test_connection_page_only_claims_a_failure_on_the_failure_path(self):
        with patch.dict(dashboard.os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}, clear=False):
            self.assertIn("Connection required", page_salesforce_unavailable())
            navigated = page_salesforce_unavailable(failed=False)
        self.assertNotIn("Connection required", navigated)
        self.assertIn("class='side'", navigated)

    def test_account_scanning_uses_a_distinct_local_queue(self):
        page = page_queue([{"Name": "CO-0740", "Submission_Date__c": "2026-09-13", "Local_Leonardo_State": "Account Scanning"}])
        self.assertIn("Account Scanning", page)
        self.assertIn("Leonardo evidence indicates a tenant is scanning.", page)

    def test_queue_can_be_rendered_as_one_filtered_operational_view(self):
        page = page_queue([
            {"Name": "CO-0740", "Local_Leonardo_State": "Account Scanning"},
            {"Name": "CO-0747", "Onboarding_Approval_Status__c": "Pending", "Onboarding_Stage__c": "New"},
        ], "validation")
        self.assertIn("CO-0747", page)
        self.assertNotIn("/co/CO-0740", page)
        self.assertIn("/?queue=validation", page)

    def test_blank_approval_status_is_labelled_as_a_manual_review_condition(self):
        page = page_queue([{"Name": "CO-0742", "Onboarding_Stage__c": "New"}])
        self.assertIn("Approval status not populated", page)
        self.assertIn("Manual review", page)

    def test_commercially_ready_pending_co_shows_ready_without_approval_or_write(self):
        page = page_detail("CO-0747", {
            "Onboarding_Approval_Status__c": "Pending",
            "Onboarding_Stage__c": "New",
        }, commercial_readiness={"commercial_ready": True, "manual_review_required": False})
        self.assertIn("Source ready to onboard", page)
        self.assertIn("Salesforce remains Pending", page)
        self.assertIn("Awaiting approval", page)
        self.assertNotIn("Check Leonardo Development session", page)

    def test_existing_comment_keeps_commercial_ready_co_in_manual_review(self):
        page = page_detail("CO-0747", {
            "Onboarding_Approval_Status__c": "Pending",
            "Onboarding_Stage__c": "New",
        }, commercial_readiness={"commercial_ready": True, "manual_review_required": True})
        self.assertIn("Source ready to onboard — manual review required", page)
        self.assertIn("will not be overwritten automatically", page)

    def test_co0740_detail_labels_local_readback_as_not_salesforce(self):
        evidence = {"CO-0740": {"surface_account_id": "a" * 16, "account_uuid": "b" * 32,
                                  "leonardo_state": "Account Scanning", "observed_on": "2026-09-13",
                                  "source": "Leonardo Development Details readback"}}
        with patch("tools.serve_attended_open_onboardings_dashboard.attended_leonardo_readbacks", return_value=evidence):
            page = page_detail("CO-0740", {"Onboarding_Approval_Status__c": "Approved"})
        self.assertIn("Leonardo Development readback", page)
        self.assertIn("Local operator evidence only; Salesforce remains unchanged.", page)

    def test_absent_local_readback_file_means_no_evidence_not_a_dashboard_error(self):
        with patch.object(dashboard, "ATTENDED_LEONARDO_READBACK_PATH") as path:
            path.read_text.side_effect = FileNotFoundError()
            self.assertEqual(dashboard.attended_leonardo_readbacks(), {})

    def test_attended_leonardo_readbacks_accepts_no_scan_started(self):
        # A tenant with no scan yet is a legitimate readback state.
        payload = {"CO-0702": {"surface_account_id": "a" * 16, "account_uuid": "b" * 32,
                               "leonardo_state": "No scan started", "observed_on": "2026-09-25",
                               "source": "Leonardo Development Details readback"}}
        with patch.object(dashboard, "ATTENDED_LEONARDO_READBACK_PATH") as path:
            path.read_text.return_value = json.dumps(payload)
            readbacks = dashboard.attended_leonardo_readbacks()
        self.assertEqual(readbacks["CO-0702"]["leonardo_state"], "No scan started")

    def test_attended_leonardo_readbacks_rejects_unrecognized_state(self):
        payload = {"CO-0702": {"surface_account_id": "a" * 16, "account_uuid": "b" * 32,
                               "leonardo_state": "Account Paused", "observed_on": "2026-09-25",
                               "source": "Leonardo Development Details readback"}}
        with patch.object(dashboard, "ATTENDED_LEONARDO_READBACK_PATH") as path:
            path.read_text.return_value = json.dumps(payload)
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.attended_leonardo_readbacks()

    def test_detail_page_shows_no_scan_started_readback_state(self):
        evidence = {"CO-0702": {"surface_account_id": "a" * 16, "account_uuid": "b" * 32,
                                "leonardo_state": "No scan started", "observed_on": "2026-09-25",
                                "source": "Leonardo Development Details readback"}}
        with patch("tools.serve_attended_open_onboardings_dashboard.attended_leonardo_readbacks", return_value=evidence):
            page = page_detail("CO-0702", {"Onboarding_Approval_Status__c": "Approved"})
        self.assertIn("Leonardo Development readback", page)
        self.assertIn("No scan started", page)

    def test_list_view_identifier_is_the_only_process_cached_salesforce_value(self):
        dashboard._open_onboardings_view_id = None
        response = {"status": 0, "result": {"records": [{"Id": "00B000000000001AAA"}]}}
        with patch("tools.serve_attended_open_onboardings_dashboard.sf_json", return_value=response) as mocked:
            self.assertEqual(open_onboardings_view_id(), "00B000000000001AAA")
            self.assertEqual(open_onboardings_view_id(), "00B000000000001AAA")
        self.assertEqual(mocked.call_count, 1)
        dashboard._open_onboardings_view_id = None

    def test_case4_detail_shows_comment_gate_but_never_approves_execution(self):
        page = page_detail("CO-0741", {
            "Onboarding_Approval_Status__c": "Approved",
            "Onboarding_Product__c": "Surface & Credential Exposure",
            "Onboarding_Type__c": "Renewal of Surface + New Credential Exposure Module",
            "Onboarding_Comments__c": "2026-08-01 - 2029-07-31",
        })
        self.assertIn("Case 4 comments validation", page)
        self.assertIn("case_not_mapped_yet", page)
        self.assertIn("No existing-account lookup, Salesforce write, or Leonardo action", page)
        self.assertIn("Case 4 route blocked", page)
        self.assertNotIn("Check Leonardo Development session", page)

    def test_unavailable_page_offers_attended_salesforce_login_without_sensitive_prompts(self):
        page = page_salesforce_unavailable()
        self.assertIn("/attended/salesforce-login", page)
        self.assertIn("Salesforce connection", page)
        self.assertIn("All queues", page)
        self.assertIn("Complete SSO/MFA", page)
        self.assertNotIn("password", page.lower())
        self.assertNotIn("token", page.lower())

    def test_vm_unavailable_page_requires_a_separate_runner_without_a_login_action(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "vm"}):
            page = page_salesforce_unavailable()
        self.assertIn("Manual Salesforce runner is unavailable", page)
        self.assertIn("RUNNER REQUIRED", page)
        self.assertNotIn("/attended/salesforce-login", page)
        self.assertNotIn("Sign in to Salesforce", page)
        self.assertNotIn("password", page.lower())
        self.assertNotIn("token", page.lower())

    def test_login_opened_page_returns_to_the_dashboard_without_credentials(self):
        page = page_salesforce_login_opened()
        self.assertIn("Return to queues", page)
        self.assertIn("/connection", page)
        self.assertNotIn("password", page.lower())

    def test_salesforce_login_launcher_discards_cli_streams(self):
        dashboard._salesforce_login_process = None
        process = unittest.mock.Mock()
        process.poll.return_value = None
        with patch("tools.serve_attended_open_onboardings_dashboard.local_browser_launch_allowed", return_value=True), patch(
            "tools.serve_attended_open_onboardings_dashboard.subprocess.Popen", return_value=process
        ) as launcher:
            self.assertTrue(start_attended_salesforce_login())
        args, kwargs = launcher.call_args
        self.assertEqual(args[0][:4], [salesforce_cli_command(), "org", "login", "web"])
        self.assertIs(kwargs["stdin"], dashboard.subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], dashboard.subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], dashboard.subprocess.DEVNULL)
        dashboard._salesforce_login_process = None

    def test_dashboard_uses_configured_salesforce_cli_without_hard_coding_windows(self):
        with patch.dict(dashboard.os.environ, {"SURFACE_SF_CLI": "sf"}, clear=False):
            self.assertEqual(salesforce_cli_command(), "sf")

    def test_vm_runtime_refuses_to_launch_a_local_browser_or_mfa_flow(self):
        with patch.object(dashboard.os, "name", "posix"), patch.dict(
            dashboard.os.environ, {"SURFACE_ONBOARDING_RUNTIME": "vm"}, clear=False
        ):
            self.assertFalse(start_attended_salesforce_login())
            self.assertFalse(open_attended_leonardo_tenant_management())
            self.assertFalse(open_attended_production_backoffice_login())

    def test_vm_listener_requires_identity_marker_and_remains_loopback_only(self):
        with patch.dict(dashboard.os.environ, {"SURFACE_ONBOARDING_RUNTIME": "vm"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "vm_web_identity_approval_required"):
                listener_address()
        with patch.dict(dashboard.os.environ, {
            "SURFACE_ONBOARDING_RUNTIME": "vm",
            "SURFACE_ONBOARDING_WEB_IDENTITY_APPROVED": "1",
        }, clear=False):
            self.assertEqual(listener_address(), ("127.0.0.1", 8000))

    def test_connection_page_offers_leonardo_session_bootstrap_on_desktop(self):
        with patch.dict(dashboard.os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}, clear=False):
            page = page_salesforce_unavailable()
        self.assertIn("/attended/leonardo-dev-session-check", page)
        self.assertIn("/attended/leonardo-dev-session-bootstrap", page)
        self.assertIn("Re-establish Leonardo session (SSO/MFA)", page)
        self.assertIn("/attended/leonardo-dev-session-reset", page)

    def test_vm_connection_page_has_no_leonardo_session_panel(self):
        with patch.dict(dashboard.os.environ, {"SURFACE_ONBOARDING_RUNTIME": "vm"}, clear=False):
            page = page_salesforce_unavailable()
        self.assertNotIn("leonardo-dev-session-bootstrap", page)
        self.assertNotIn("leonardo-dev-session-check", page)

    def test_runner_result_messages_cover_duplicate_schema_unavailable(self):
        kind, message = dashboard.RUNNER_RESULT_MESSAGES["duplicate_schema_unavailable"]
        self.assertEqual(kind, "blocked")
        self.assertIn("No tenant was created", message)

    def test_leonardo_session_messages_cover_bootstrap(self):
        kind, _message = dashboard.LEONARDO_SESSION_MESSAGES["leonardo_session_bootstrapped"]
        self.assertEqual(kind, "success")

    def test_connection_page_offers_close_automation_browser_on_desktop_only(self):
        with patch.dict(dashboard.os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}, clear=False):
            page = page_salesforce_unavailable()
        self.assertIn("/attended/leonardo-dev-browser-close", page)
        self.assertIn("Close automation browser", page)
        with patch.dict(dashboard.os.environ, {"SURFACE_ONBOARDING_RUNTIME": "vm"}, clear=False):
            page = page_salesforce_unavailable()
        self.assertNotIn("leonardo-dev-browser-close", page)

    def test_leonardo_session_messages_cover_browser_reuse_codes(self):
        self.assertEqual(dashboard.LEONARDO_SESSION_MESSAGES["automation_browser_closed"][0], "info")
        self.assertEqual(dashboard.LEONARDO_SESSION_MESSAGES["automation_browser_not_running"][0], "info")
        self.assertEqual(dashboard.LEONARDO_SESSION_MESSAGES["automation_browser_close_unavailable"][0], "blocked")
        self.assertEqual(dashboard.LEONARDO_SESSION_MESSAGES["browser_tab_unavailable"][0], "blocked")

    def test_co0741_detail_offers_a_read_only_comment_rerun(self):
        page = page_detail("CO-0741", {
            "Onboarding_Approval_Status__c": "Pending",
            "Onboarding_Stage__c": "New",
            "Onboarding_Product__c": "Surface & Credential Exposure",
            "Onboarding_Type__c": "Renewal of Surface + New Credential Exposure Module",
        })
        self.assertIn("Re-run DealHub comment evaluation", page)
        self.assertIn("/attended/rerun-comment-evaluation", page)
        self.assertIn("cannot overwrite a non-empty comment", page)

    def test_co0741_verified_page_shows_popup_and_disables_repair_rerun(self):
        page = page_detail("CO-0741", {
            "Onboarding_Approval_Status__c": "Approved",
            "Onboarding_Stage__c": "Request Approved",
            "Onboarding_Comments__c": "2026-09-11 - 2027-09-10",
            "Onboarding_Product__c": "Surface & Credential Exposure",
            "Onboarding_Type__c": "Renewal of Surface + New Credential Exposure Module",
        }, "verified")
        self.assertIn("Update verified", page)
        self.assertIn("role='status'", page)
        self.assertIn("Re-run evaluation unavailable", page)
        self.assertNotIn("<button type='submit'>Re-run evaluation</button>", page)

    def test_co0745_detail_offers_a_read_only_renewal_term_evaluation(self):
        page = page_detail("CO-0745", {
            "Onboarding_Approval_Status__c": "Pending",
            "Onboarding_Stage__c": "New",
            "Onboarding_Product__c": "Surface & Credential Exposure",
            "Onboarding_Type__c": "Renewal of Surface + New Credential Exposure Module",
        })
        self.assertIn("Validate DealHub renewal term", page)
        self.assertIn("/attended/rerun-co0745-renewal-evaluation", page)
        self.assertIn("Production existing-account validation", page)
        self.assertNotIn("Confirm update in Salesforce", page)

    def test_co0745_renewal_evaluation_selects_one_active_surface_baseline_without_writing(self):
        co_response = {"status": 0, "result": {"records": [{
            "Id": "a0B000000000045", "Name": "CO-0745", "Account__c": "001000000000045",
            "Account_Name__c": "Acme Bank",
            "LastModifiedDate": "2026-09-16T10:00:00Z", "Onboarding_Comments__c": None,
            "Onboarding_Stage__c": "New", "Onboarding_Approval_Status__c": "Pending",
            "Onboarding_Product__c": "Surface & Credential Exposure",
            "Onboarding_Type__c": "Renewal of Surface + New Credential Exposure Module",
        }]}}
        subscriptions_response = {"status": 0, "result": {"records": [{
            "Id": "a0C000000000045", "SystemModstamp": "2026-09-16T10:01:00Z",
            "DealHub_Account__c": "001000000000045",
            "Product_Full_Name__c": "Pentera Surface Go - 500 Subdomains", "DealHub_Status__c": "Active",
            "DealHub_Subscription_Start_Date__c": "2026-09-11",
            "DealHub_Subscription_End_Date__c": "2027-09-10",
        }]}}
        with patch("tools.serve_attended_open_onboardings_dashboard.sf_json", side_effect=[co_response, subscriptions_response]) as reader, patch(
            "tools.serve_attended_open_onboardings_dashboard.sf_write_json"
        ) as writer:
            evaluation = evaluate_co0745_renewal_comment()
        self.assertEqual(evaluation.proposed_comment, "2026-09-11 - 2027-09-10")
        self.assertEqual(evaluation.product_name, "Pentera Surface Go - 500 Subdomains")
        self.assertEqual(evaluation.expected_tenant_name, "Acme Bank - CE Only")
        self.assertEqual(reader.call_count, 2)
        writer.assert_not_called()
        page = page_co0745_renewal_evaluation(evaluation)
        self.assertIn("No Salesforce field was changed", page)
        self.assertIn("Acme Bank - CE Only", page)
        self.assertNotIn("Confirm update in Salesforce", page)

    def test_co0702_preflight_calculates_ce_only_scope_without_external_action(self):
        source = {"status": 0, "result": {"records": [{
            "Id": "a0B000000000702", "Name": "CO-0702", "LastModifiedDate": "2026-09-17T10:00:00Z",
            "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "example.test",
        }]}}
        with patch("tools.serve_attended_open_onboardings_dashboard.sf_json", return_value=source) as reader:
            evaluation = evaluate_ce_only_fill_preflight("CO-0702")
        self.assertTrue(evaluation.eligible_for_fill_review)
        self.assertEqual(evaluation.email_domain_count, 1)
        self.assertEqual(reader.call_count, 1)
        page = page_ce_only_fill_preflight(evaluation)
        self.assertIn("Email Domains configured", page)
        self.assertIn("No browser, Leonardo, duplicate lookup, form fill, tenant creation, or Salesforce update", page)
        self.assertIn("Start Onboarding", page)

    def test_co0702_detail_shows_onboard_automation_as_primary_for_approved_source(self):
        source = {"status": 0, "result": {"records": [{
            "Id": "a0B000000000702", "Name": "CO-0702", "LastModifiedDate": "2026-09-17T10:00:00Z",
            "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "example.test",
        }]}}
        row = {"Onboarding_Approval_Status__c": "Approved", "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "example.test"}
        with patch("tools.serve_attended_open_onboardings_dashboard.sf_json", return_value=source), \
             patch("tools.serve_attended_open_onboardings_dashboard.load_runner_state", return_value={}), \
             patch("tools.serve_attended_open_onboardings_dashboard.attended_leonardo_readbacks", return_value={}):
            page = page_detail("CO-0702", row)
        self.assertIn("Credential Exposure onboarding", page)
        self.assertIn("Start Onboarding", page)
        self.assertIn("I authorize one Leonardo Development run for this source revision", page)
        self.assertNotIn("Start manual onboarding", page)
        self.assertNotIn("Run fill preflight", page)

    def test_co0702_detail_onboard_section_has_one_action_with_the_duplicate_check_built_in(self):
        source = {"status": 0, "result": {"records": [{
            "Id": "a0B000000000702", "Name": "CO-0702", "LastModifiedDate": "2026-09-17T10:00:00Z",
            "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "example.test",
        }]}}
        row = {"Onboarding_Approval_Status__c": "Approved", "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "example.test"}
        with patch("tools.serve_attended_open_onboardings_dashboard.sf_json", return_value=source), \
             patch("tools.serve_attended_open_onboardings_dashboard.load_runner_state", return_value={}), \
             patch("tools.serve_attended_open_onboardings_dashboard.attended_leonardo_readbacks", return_value={}):
            page = page_detail("CO-0702", row)
        # One action only: the separate read-only check buttons are gone and the
        # duplicate check is part of Start Onboarding.
        self.assertEqual(page.count("<button type='submit'>Start Onboarding</button>"), 1)
        self.assertNotIn("/attended/ce-only-readback", page)
        self.assertNotIn("/attended/ce-only-duplicate-check", page)
        self.assertNotIn("Verify existing tenant", page)
        self.assertNotIn("Check for duplicates", page)
        self.assertIn("Checks Leonardo for an existing tenant", page)
        self.assertIn("chip-info'>Ready<", page)
        # Creation is enabled for CE-only, so the "not enabled" note is gone.
        self.assertNotIn("Why Leonardo creation is not enabled yet", page)

    def test_co0702_detail_onboard_panel_shows_prior_result_instead_of_rerun(self):
        source = {"status": 0, "result": {"records": [{
            "Id": "a0B000000000702", "Name": "CO-0702", "LastModifiedDate": "2026-09-17T10:00:00Z",
            "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "example.test",
        }]}}
        row = {"Onboarding_Approval_Status__c": "Approved", "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "example.test"}
        state = {"CO-0702": {"source_revision": "2026-09-17T10:00:00Z", "result": "fill_form_schema_unavailable",
                             "completed_on": "2026-09-23T18:55:28"}}
        with patch("tools.serve_attended_open_onboardings_dashboard.sf_json", return_value=source), \
             patch("tools.serve_attended_open_onboardings_dashboard.load_runner_state", return_value=state), \
             patch("tools.serve_attended_open_onboardings_dashboard.attended_leonardo_readbacks", return_value={}):
            page = page_detail("CO-0702", row)
        self.assertIn("Credential Exposure onboarding", page)
        self.assertIn("fill_form_schema_unavailable", page)
        self.assertNotIn("Start Onboarding", page)

    def test_ce_only_name_and_primary_user_alias_rules_are_deterministic(self):
        self.assertEqual(ce_only_names("Sample Company").tenant_name, "Sample Company - CE Only")
        # The alias is casefolded (lowercase) in both branches so the
        # organization email alias always matches the lowercase contract.
        self.assertEqual(ce_only_names("Sample Company").primary_user_alias, "samplecompany")
        self.assertEqual(ce_only_names("Abcdefghijklmno").primary_user_alias, "abcdefghijklmno")
        self.assertEqual(ce_only_names("First Main Bank & Trust").primary_user_alias, "fmbt")
        self.assertEqual(one_email_domain("Example.Test"), "example.test")
        self.assertIsNone(one_email_domain("one.test, two.test"))

    def test_co0702_preflight_blocks_unapproved_or_missing_ce_entitlement(self):
        source = {"status": 0, "result": {"records": [{
            "Id": "a0B000000000702", "Name": "CO-0702", "LastModifiedDate": "2026-09-17T10:00:00Z",
            "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
            "Email_Domains__c": "one.test,two.test",
        }]}}
        with patch("tools.serve_attended_open_onboardings_dashboard.sf_json", return_value=source):
            evaluation = evaluate_ce_only_fill_preflight("CO-0702")
        self.assertFalse(evaluation.eligible_for_fill_review)
        self.assertEqual(evaluation.blockers, ("exactly_one_email_domain_required",))

    def test_ce_route_requires_exact_credential_exposure_new_product(self):
        # 2026-09-29 audit: a Surface or combined CO with one email domain must
        # never be offered, or pass, the CE-only route.
        base = {"Email_Domains__c": "example.test", "Onboarding_Type__c": "New Product Onboarding"}
        for product, onboarding_type in (("Surface", "New Product Onboarding"),
                                         ("Surface & Credential Exposure", "New Product Onboarding"),
                                         ("Credential Exposure", "Renewal"), (None, None)):
            row = dict(base, Onboarding_Product__c=product, Onboarding_Type__c=onboarding_type)
            with self.subTest(product=product, type=onboarding_type):
                self.assertFalse(dashboard.ce_only_eligible(row))
                source = {"status": 0, "result": {"records": [dict(row, Id="a0B000000000757", Name="CO-0757",
                                                                   LastModifiedDate="2026-09-24T13:41:31Z")]}}
                with patch("tools.serve_attended_open_onboardings_dashboard.sf_json", return_value=source):
                    evaluation = evaluate_ce_only_fill_preflight("CO-0757")
                self.assertIn("not_a_new_credential_exposure_onboarding", evaluation.blockers)
                self.assertFalse(evaluation.eligible_for_fill_review)

    def test_surface_co_detail_does_not_offer_the_ce_only_start(self):
        # A Surface CO now gets the Surface-only card (Case 1), never the CE one.
        row = {"Onboarding_Approval_Status__c": "Approved", "Onboarding_Product__c": "Surface",
               "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "example.test"}
        blocked = dashboard.SurfaceScopePreflight("CO-0649", "", ("surface_baseline_unavailable",))
        with patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "attended_leonardo_readbacks", return_value={}), \
                patch.object(dashboard, "source_ready_to_onboard", return_value=True), \
                patch.object(dashboard, "evaluate_ce_only_fill_preflight",
                             side_effect=AssertionError("CE preflight must not run for a Surface CO")), \
                patch.object(dashboard, "evaluate_surface_fill_preflight", return_value=blocked), \
                patch.object(dashboard, "manual_start_ack_nonce", return_value=None):
            page = page_detail("CO-0649", row)
        self.assertNotIn("Start Onboarding", page)
        self.assertNotIn("Credential Exposure onboarding", page)
        self.assertIn("Surface onboarding", page)
        self.assertIn("surface_baseline_unavailable", page)

    def test_evaluate_ce_only_start_requires_revision_acknowledgement(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0702", "rev1", 1, ())
        self.assertEqual(dashboard.evaluate_ce_only_start(None, evaluation, {}), "revision_acknowledgement_missing")
        self.assertEqual(dashboard.evaluate_ce_only_start("", evaluation, {}), "revision_acknowledgement_missing")

    def test_evaluate_ce_only_start_blocks_when_preflight_blocked(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0702", "rev1", 2, ("exactly_one_email_domain_required",))
        self.assertEqual(dashboard.evaluate_ce_only_start("rev1", evaluation, {}), "preflight_blocked")

    def test_evaluate_ce_only_start_blocks_when_source_revision_changed(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0702", "rev2", 1, ())
        self.assertEqual(dashboard.evaluate_ce_only_start("rev1", evaluation, {}), "source_revision_changed")

    def test_evaluate_ce_only_start_blocks_when_revision_already_acknowledged(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0702", "rev1", 1, ())
        state = {"CO-0702": {"source_revision": "rev1", "started_on": "2026-09-21T10:00:00"}}
        self.assertEqual(dashboard.evaluate_ce_only_start("rev1", evaluation, state), "revision_already_acknowledged")

    def test_evaluate_ce_only_start_allows_a_new_revision(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0702", "rev2", 1, ())
        state = {"CO-0702": {"source_revision": "rev1", "started_on": "2026-09-21T10:00:00"}}
        self.assertEqual(dashboard.evaluate_ce_only_start("rev2", evaluation, state), "start")

    def test_ce_only_preflight_page_shows_revision_and_hidden_ack_field(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0702", "rev1", 1, ())
        page = page_ce_only_fill_preflight(evaluation)
        self.assertIn("Source revision", page)
        self.assertIn(">rev1<", page)
        self.assertIn("name='source_revision'", page)
        self.assertIn("value='rev1'", page)
        self.assertIn("Start Onboarding", page)

    def test_ce_only_preflight_page_hides_form_when_current_revision_in_progress(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0702", "rev1", 1, ())
        state = {"CO-0702": {"source_revision": "rev1", "started_on": "2026-09-21T10:00:00"}}
        page = page_ce_only_fill_preflight(evaluation, state)
        self.assertNotIn("Start Onboarding", page)
        self.assertIn("has not reported a result", page)

    def test_ce_only_preflight_page_shows_form_for_a_new_revision(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0702", "rev2", 1, ())
        state = {"CO-0702": {"source_revision": "rev1", "started_on": "2026-09-21T10:00:00"}}
        page = page_ce_only_fill_preflight(evaluation, state)
        self.assertIn("Start Onboarding", page)
        self.assertIn("Previous attended run for a different source revision", page)

    def test_ce_only_preflight_page_shows_completed_result_and_hides_form(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0702", "rev1", 1, ())
        state = {"CO-0702": {"source_revision": "rev1", "started_on": "2026-09-21T10:00:00",
                             "result": "readback_verified", "completed_on": "2026-09-21T10:05:00"}}
        page = page_ce_only_fill_preflight(evaluation, state)
        self.assertIn("readback_verified", page)
        self.assertNotIn("Start Onboarding", page)

    def test_runner_status_page_shows_no_run_when_absent(self):
        page = page_ce_only_runner_status(None, "CO-0702")
        self.assertIn("No attended run is recorded", page)
        self.assertNotIn("http-equiv='refresh'", page)

    def test_runner_status_page_waits_and_refreshes_when_started_without_result(self):
        state = {"CO-0702": {"source_revision": "rev1", "started_on": "2026-09-23T10:38:05"}}
        page = page_ce_only_runner_status(state, "CO-0702")
        self.assertIn("Onboarding in progress", page)
        self.assertIn("refreshes every 5 seconds", page)
        self.assertIn("http-equiv='refresh'", page)
        self.assertIn("2026-09-23T10:38:05", page)

    def test_runner_status_page_shows_success_for_readback_verified(self):
        state = {"CO-0702": {"source_revision": "rev1", "result": "readback_verified", "completed_on": "2026-09-23T10:40:00"}}
        page = page_ce_only_runner_status(state, "CO-0702")
        # Green check icon and headline; returns to the CO page automatically.
        self.assertIn("Onboarded successfully", page)
        self.assertIn("&#10003;", page)
        self.assertIn("outcome-success", page)
        self.assertNotIn("Onboarding failed", page)
        self.assertIn("Tenant created and read back", page)
        self.assertIn("http-equiv='refresh' content='6;url=/co/CO-0702'", page)
        self.assertIn("href='/co/CO-0702'", page)

    def test_runner_status_page_shows_blocked_message_for_schema_failure(self):
        state = {"CO-0702": {"source_revision": "rev1", "result": "duplicate_search_schema_unavailable", "completed_on": "2026-09-23T10:39:07"}}
        page = page_ce_only_runner_status(state, "CO-0702")
        # Red cross icon and headline; still returns to the CO page.
        self.assertIn("Onboarding failed", page)
        self.assertIn("&#10007;", page)
        self.assertIn("outcome-failed", page)
        self.assertNotIn("Onboarded successfully", page)
        self.assertIn("search control could not be found", page)
        self.assertIn("duplicate_search_schema_unavailable", page)
        self.assertIn("url=/co/CO-0702", page)

    def test_runner_status_page_unknown_result_code_is_blocked(self):
        state = {"CO-0702": {"source_revision": "rev1", "result": "some_future_code", "completed_on": "2026-09-23T10:40:00"}}
        page = page_ce_only_runner_status(state, "CO-0702")
        self.assertIn("Onboarding failed", page)
        self.assertIn("&#10007;", page)
        self.assertIn("some_future_code", page)

    def test_runner_status_page_in_progress_does_not_redirect_away(self):
        state = {"CO-0702": {"source_revision": "rev1", "started_on": "2026-09-23T10:38:05"}}
        page = page_ce_only_runner_status(state, "CO-0702")
        self.assertIn("content='5'", page)
        self.assertNotIn("url=", page)

    def test_outcome_banner_escapes_untrusted_result_code(self):
        banner = dashboard._outcome_banner("blocked", "static", "<script>x</script>", "<b>now</b>")
        self.assertNotIn("<script>", banner)
        self.assertNotIn("<b>now</b>", banner)
        self.assertIn("&lt;script&gt;", banner)

    def test_co_detail_onboard_panel_shows_green_icon_for_a_created_tenant(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0679", "rev1", 1, ())
        state = {"CO-0679": {"source_revision": "rev1", "started_on": "2026-09-29T13:39:53",
                             "result": "readback_verified", "completed_on": "2026-09-29T13:40:37"}}
        with patch.object(dashboard, "evaluate_ce_only_fill_preflight", return_value=evaluation), \
                patch.object(dashboard, "load_runner_state", return_value=state):
            section = dashboard._ce_only_onboard_section("CO-0679")
        self.assertIn("Onboarded successfully", section)
        self.assertIn("&#10003;", section)
        self.assertNotIn("Start Onboarding", section)

    def test_co_detail_onboard_panel_shows_red_icon_for_a_failed_run(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0679", "rev1", 1, ())
        state = {"CO-0679": {"source_revision": "rev1", "started_on": "2026-09-29T13:05:19",
                             "result": "fill_form_schema_unavailable", "completed_on": "2026-09-29T13:06:03"}}
        with patch.object(dashboard, "evaluate_ce_only_fill_preflight", return_value=evaluation), \
                patch.object(dashboard, "load_runner_state", return_value=state):
            section = dashboard._ce_only_onboard_section("CO-0679")
        self.assertIn("Onboarding failed", section)
        self.assertIn("&#10007;", section)
        self.assertIn("Reset runner record", section)

    def _post(self, path: str, body: str):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        self.addCleanup(connection.close)
        connection.request("POST", path, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        response = connection.getresponse()
        return response.status, response.getheader("Location"), response.read().decode("utf-8")

    def test_duplicate_run_is_reported_as_already_exists(self):
        state = {"CO-0728": {"source_revision": "rev1", "result": "duplicate_found", "completed_on": "2026-09-29T15:00:40"}}
        page = page_ce_only_runner_status(state, "CO-0728")
        self.assertIn("Already exists — duplicate", page)
        self.assertIn("outcome-failed", page)
        self.assertIn("Nothing was created", page)
        self.assertIn("url=/co/CO-0728", page)
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0728", "rev1", 1, ())
        with patch.object(dashboard, "evaluate_ce_only_fill_preflight", return_value=evaluation), \
                patch.object(dashboard, "load_runner_state", return_value=state):
            section = dashboard._ce_only_onboard_section("CO-0728")
            chip = dashboard._onboarding_chip("CO-0728")
        self.assertIn("chip-bad'>Duplicate<", section)
        self.assertIn("Already exists — duplicate", section)
        self.assertIn("Reset runner record", section)
        self.assertIn("Duplicate", chip)

    def test_header_chip_reflects_the_latest_run(self):
        cases = [({}, ""),
                 ({"CO-0679": {"source_revision": "r", "started_on": "2026-09-29T13:39:53"}}, "Running"),
                 ({"CO-0679": {"source_revision": "r", "result": "readback_verified"}}, "Onboarded"),
                 ({"CO-0679": {"source_revision": "r", "result": "fill_form_schema_unavailable"}}, "Onboarding failed")]
        for state, expected in cases:
            with self.subTest(expected=expected), patch.object(dashboard, "load_runner_state", return_value=state):
                chip = dashboard._onboarding_chip("CO-0679")
            if expected:
                self.assertIn(">" + expected + "<", chip)
            else:
                self.assertEqual(chip, "")

    def test_co_page_uses_the_pentera_shell(self):
        row = {"Onboarding_Approval_Status__c": "Pending"}
        with patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "attended_leonardo_readbacks", return_value={}):
            page = page_detail("CO-0999", row)
        self.assertIn("class='side'", page)
        self.assertIn("PENTERA.", page)
        self.assertIn("--nav:#0f1626", page)
        self.assertIn("&larr; Open Onboardings", page)
        self.assertNotIn("\u00e2\u2020", page)  # no mojibake arrow

    def test_every_post_route_in_the_handler_is_registered(self):
        # Guards POST_ROUTES against drift: each routed path must be listed.
        import inspect
        import re as _re
        source = inspect.getsource(dashboard.Handler.do_POST)
        routed = set(_re.findall(r'"(/attended/[a-z0-9-]+)"', source))
        self.assertEqual(routed, set(dashboard.POST_ROUTES))

    def test_removed_check_routes_launch_nothing(self):
        # Called on a fake request (no socket): an unknown route must return
        # 404 before reading the form, Salesforce, or launching anything.
        class _FakeRequest:
            def __init__(self, path):
                self.path = path
                self.sent = []

            def send_page(self, status, page):
                self.sent.append(status)

            def __getattr__(self, name):
                raise AssertionError("unexpected handler access: " + name)

        with patch.object(dashboard, "post_form", side_effect=AssertionError("no form parse")), \
                patch.object(dashboard, "detail_row", side_effect=AssertionError("no Salesforce read")), \
                patch.object(dashboard, "start_attended_ce_only_runner", side_effect=AssertionError("no launch")):
            for route in ("/attended/ce-only-duplicate-check", "/attended/ce-only-readback", "/attended/anything"):
                with self.subTest(route=route):
                    request = _FakeRequest(route)
                    dashboard.Handler.do_POST(request)
                    self.assertEqual(request.sent, [404])

    def test_starting_an_already_acknowledged_revision_redirects_to_the_outcome_page(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0679", "rev1", 1, ())
        state = {"CO-0679": {"source_revision": "rev1", "result": "readback_verified",
                             "completed_on": "2026-09-29T13:40:37"}}
        with patch.object(dashboard, "evaluate_ce_only_fill_preflight", return_value=evaluation), \
                patch.object(dashboard, "load_runner_state", return_value=state), \
                patch.object(dashboard, "start_attended_ce_only_runner",
                             side_effect=AssertionError("no browser may be launched")):
            status, location, _body = self._post(
                "/attended/start-ce-only-runner",
                "reference=CO-0679&attended_create_authorized=1&source_revision=rev1")
        self.assertEqual(status, 303)
        self.assertEqual(location, "/attended/ce-only-runner-status?ref=CO-0679")

    def test_other_blocked_starts_link_back_to_the_co(self):
        evaluation = dashboard.CredentialExposureFillPreflight("CO-0679", "rev2", 1, ())
        with patch.object(dashboard, "evaluate_ce_only_fill_preflight", return_value=evaluation), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "start_attended_ce_only_runner",
                             side_effect=AssertionError("no browser may be launched")):
            status, _location, body = self._post(
                "/attended/start-ce-only-runner",
                "reference=CO-0679&attended_create_authorized=1&source_revision=rev1")
        self.assertEqual(status, 409)
        self.assertIn("source revision changed", body)
        self.assertIn("href='/co/CO-0679'", body)

    def test_ce_only_eligible_requires_exactly_one_valid_email_domain(self):
        self.assertTrue(dashboard.ce_only_eligible({"Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "example.test"}))
        self.assertTrue(dashboard.ce_only_eligible({"Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding", "Email_Domains__c": "  Example.Test  "}))
        self.assertFalse(dashboard.ce_only_eligible({"Email_Domains__c": "one.test, two.test"}))
        self.assertFalse(dashboard.ce_only_eligible({"Email_Domains__c": ""}))
        self.assertFalse(dashboard.ce_only_eligible({"Email_Domains__c": None}))
        self.assertFalse(dashboard.ce_only_eligible({}))

    def test_detail_with_multiple_email_domains_shows_manual_flow_not_ce_only(self):
        source = {"status": 0, "result": {"records": [{
            "Id": "a0B000000000703", "Name": "CO-0703", "LastModifiedDate": "2026-09-17T10:00:00Z",
            "Email_Domains__c": "one.test,two.test",
        }]}}
        row = {"Onboarding_Approval_Status__c": "Approved", "Email_Domains__c": "one.test,two.test"}
        with patch("tools.serve_attended_open_onboardings_dashboard.sf_json", return_value=source), \
             patch("tools.serve_attended_open_onboardings_dashboard.load_runner_state", return_value={}), \
             patch("tools.serve_attended_open_onboardings_dashboard.attended_leonardo_readbacks", return_value={}):
            page = page_detail("CO-0703", row)
        self.assertNotIn("Onboard CO-0703 (Credential Exposure)", page)
        self.assertIn("Start manual onboarding", page)

    def test_comment_update_confirmation_is_revision_bound_and_one_time(self):
        dashboard._comment_update_acks.clear()
        evaluation = CommentUpdateEvaluation("a0B000000000001", "2026-09-14T01:00:00Z", "a0C000000000001", "2026-09-14T01:00:01Z", "2026-09-11 - 2027-09-10")
        nonce = issue_comment_update_ack(evaluation, now=100)
        page = page_comment_update_confirmation(evaluation, nonce)
        self.assertIn("2026-09-11 - 2027-09-10", page)
        self.assertIn("Confirm update in Salesforce", page)
        self.assertTrue(consume_comment_update_ack(evaluation, nonce, now=101))
        self.assertFalse(consume_comment_update_ack(evaluation, nonce, now=101))

    def test_confirmed_update_uses_one_fresh_evaluation_then_update_and_readback(self):
        dashboard._comment_update_acks.clear()
        evaluation = CommentUpdateEvaluation("a0B000000000001", "2026-09-14T01:00:00Z", "a0C000000000001", "2026-09-14T01:00:01Z", "2026-09-11 - 2027-09-10")
        nonce = issue_comment_update_ack(evaluation)
        readback = {"status": 0, "result": {"records": [{"Name": "CO-0741", "Onboarding_Comments__c": "2026-09-11 - 2027-09-10", "Onboarding_Stage__c": "Request Approved", "Onboarding_Approval_Status__c": "Approved"}]}}
        with patch("tools.serve_attended_open_onboardings_dashboard.sf_write_json", return_value={"status": 0}) as writer, patch("tools.serve_attended_open_onboardings_dashboard.sf_json", return_value=readback) as source:
            self.assertTrue(update_co0741_comment_after_confirmation(evaluation, nonce))
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(source.call_count, 1)
        self.assertEqual(writer.call_args_list[0].args[0][0:3], ["data", "update", "record"])
        values = writer.call_args_list[0].args[0][8]
        self.assertIn("Onboarding_Comments__c='2026-09-11 - 2027-09-10'", values)
        self.assertIn("Onboarding_Stage__c='Request Approved'", values)
        self.assertIn("Onboarding_Approval_Status__c=Approved", values)


SURFACE_ROW = {"Onboarding_Approval_Status__c": "Approved", "Onboarding_Product__c": "Surface",
               "Onboarding_Type__c": "New Product Onboarding", "Main_Domain__c": "surface-sample.example"}
SURFACE_SCOPE = {
    "tier": "prime", "scanning_interval": "Weekly", "main_domains": 1, "alternate_root_domains": 2,
    "requested_subdomains": 3, "number_of_domains": 3, "baseline_subdomains": 1000, "addon_subdomains": 400,
    "licensed_subdomains": 1400, "product_domains": None, "assets": 10000,
    "license_start": "2026-09-29", "license_end": "2027-09-28", "large_scope": True, "core_plus_present": False,
}


def _surface_evaluation(revision="rev1", *, core_plus=False, scope=SURFACE_SCOPE):
    return dashboard.SurfaceScopePreflight("CO-0801", revision, (), dict(scope, core_plus_present=core_plus),
                                           core_plus)


class SurfaceRouteDashboardTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path
        directory = Path(tempfile.mkdtemp(prefix="dashboard_reminders_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        patcher = patch.object(dashboard, "ATTENDED_REMINDERS_PATH", directory / "reminders.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, path: str, body: str):
        return AttendedOpenOnboardingsDashboardTests._post(self, path, body)

    def _section(self, evaluation, state=None):
        with patch.object(dashboard, "evaluate_surface_fill_preflight", return_value=evaluation), \
                patch.object(dashboard, "load_runner_state", return_value=state or {}):
            return dashboard._surface_onboard_section("CO-0801")

    def test_route_for_uses_exact_product_and_type(self):
        self.assertEqual(dashboard.route_for(SURFACE_ROW), "case_1_new_surface_only")
        self.assertEqual(dashboard.route_for({"Onboarding_Product__c": "Credential Exposure",
                                              "Onboarding_Type__c": "New Product Onboarding"}), "case_2_new_ce_only")
        for product, onboarding_type in (("Surface", "Renewal"), ("surface", "New Product Onboarding"),
                                         ("Surface & Credential Exposure", "New Product Onboarding"), (None, None)):
            with self.subTest(product=product, type=onboarding_type):
                self.assertIsNone(dashboard.route_for({"Onboarding_Product__c": product,
                                                       "Onboarding_Type__c": onboarding_type}))
        from integration.onboarding.models import EngineValue
        self.assertEqual(dashboard.SURFACE_ENGINE, EngineValue.CASE_1.value)
        self.assertEqual(dashboard.CE_ENGINE, EngineValue.CASE_2.value)

    def test_preflight_maps_source_errors_to_blockers(self):
        from tools.attended_ce_only_playwright import SurfaceSourceError
        with patch.object(dashboard, "surface_fill_source", side_effect=SurfaceSourceError("surface_tier_unknown")):
            evaluation = dashboard.evaluate_surface_fill_preflight("CO-0801")
        self.assertEqual(evaluation.blockers, ("surface_tier_unknown",))
        self.assertFalse(evaluation.eligible_for_fill_review)
        self.assertEqual(evaluation.scope_digest, "")
        with self.assertRaises(dashboard.ReadUnavailable):
            dashboard.evaluate_surface_fill_preflight("BAD")

    def test_scope_digest_changes_with_the_scope(self):
        first, second = _surface_evaluation(), _surface_evaluation(scope=dict(SURFACE_SCOPE, licensed_subdomains=9))
        self.assertEqual(len(first.scope_digest), 64)
        self.assertNotEqual(first.scope_digest, second.scope_digest)
        self.assertNotEqual(first.scope_digest, _surface_evaluation("rev2").scope_digest)

    def test_detail_page_shows_the_surface_card_with_scope_and_both_checkboxes(self):
        with patch.object(dashboard, "evaluate_surface_fill_preflight", return_value=_surface_evaluation()), \
                patch.object(dashboard, "evaluate_ce_only_fill_preflight", side_effect=AssertionError("no CE")), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "attended_leonardo_readbacks", return_value={}):
            page = page_detail("CO-0801", SURFACE_ROW)
        self.assertIn("Surface onboarding", page)
        self.assertIn("chip-info'>Ready<", page)
        self.assertEqual(page.count("<button type='submit'>Start Onboarding</button>"), 1)
        self.assertIn("action='/attended/start-surface-runner'", page)
        self.assertIn("name='attended_create_authorized' value='1' required", page)
        self.assertIn("name='scope_reviewed' value='1' required", page)
        self.assertIn("I reviewed the scope for this source revision", page)
        self.assertIn("value='" + _surface_evaluation().scope_digest + "'", page)
        for label, value in (("Tier", "Prime"), ("Scanning interval", "Weekly"), ("Alternate root domains", "2"),
                             ("Requested subdomains", "3"), ("Assets", "10000"),
                             ("Licensed subdomains", "1400 (1000 baseline + 400 add-on)"),
                             ("License dates", "2026-09-29 → 2027-09-28")):
            self.assertIn("<dt>" + label + "</dt><dd>" + value + "</dd>", page)
        self.assertNotIn("Why Leonardo creation is not enabled yet", page)
        self.assertNotIn("Start manual onboarding", page)
        self.assertNotIn("Credential Exposure onboarding", page)

    def test_non_surface_cos_keep_todays_behaviour(self):
        row = dict(SURFACE_ROW, Onboarding_Product__c="Surface & Credential Exposure")
        with patch.object(dashboard, "evaluate_surface_fill_preflight", side_effect=AssertionError("no Surface")), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "attended_leonardo_readbacks", return_value={}), \
                patch.object(dashboard, "manual_start_ack_nonce", return_value=None):
            page = page_detail("CO-0802", row, commercial_readiness=None)
        self.assertIn("Start manual onboarding", page)
        self.assertNotIn("Surface onboarding", page)

    def test_blocked_card_shows_the_code_and_message_without_a_start(self):
        section = self._section(dashboard.SurfaceScopePreflight("CO-0801", "rev1", ("surface_networks_not_supported",)))
        self.assertIn("chip-bad'>Blocked<", section)
        self.assertIn("surface_networks_not_supported", section)
        self.assertIn("networks are not supported", section)
        self.assertNotIn("Start Onboarding", section)

    def test_prior_result_and_reset_for_failed_runs(self):
        state = {"CO-0801": {"source_revision": "rev1", "route": "case_1_new_surface_only",
                             "result": "max_scan_duration_schema_unavailable", "completed_on": "2026-09-29T10:00:00"}}
        section = self._section(_surface_evaluation(), state)
        self.assertIn("Onboarding failed", section)
        self.assertIn("Maximum scan Duration (hours)", section)
        self.assertIn("Reset runner record", section)
        self.assertNotIn("Start Onboarding", section)

    def test_verified_run_never_offers_a_new_start_for_a_later_revision(self):
        state = {"CO-0801": {"source_revision": "rev1", "route": "case_1_new_surface_only",
                             "result": "readback_verified", "completed_on": "2026-09-29T10:00:00"}}
        section = self._section(_surface_evaluation("rev2"), state)
        self.assertIn("chip-ok'>Onboarded<", section)
        self.assertNotIn("Start Onboarding", section)

    def test_new_result_codes_have_messages(self):
        for code in ("surface_route_mismatch", "surface_source_unavailable", "surface_product_unrecognized",
                     "surface_baseline_unavailable", "surface_baseline_ambiguous", "surface_tier_unknown",
                     "surface_subscription_invalid", "surface_main_domain_invalid", "surface_domains_invalid",
                     "surface_networks_not_supported", "surface_license_dates_unavailable",
                     "max_scan_duration_schema_unavailable", "scope_review_missing", "route_unsupported"):
            with self.subTest(code=code):
                self.assertIn(code, dashboard.RUNNER_RESULT_MESSAGES)
        self.assertNotIn("surface_route_case3_detected", dashboard.RUNNER_RESULT_MESSAGES)

    def test_start_without_scope_review_is_rejected(self):
        evaluation = _surface_evaluation()
        with patch.object(dashboard, "evaluate_surface_fill_preflight", return_value=evaluation), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "start_attended_surface_runner", side_effect=AssertionError("no launch")), \
                patch.object(dashboard, "record_runner_start", side_effect=AssertionError("no record")):
            for body in ("reference=CO-0801&attended_create_authorized=1&source_revision=rev1&scope_digest="
                         + evaluation.scope_digest,
                         "reference=CO-0801&attended_create_authorized=1&scope_reviewed=0&source_revision=rev1"):
                with self.subTest(body=body):
                    status, _location, page = self._post("/attended/start-surface-runner", body)
                    self.assertEqual(status, 409)
                    self.assertIn("scope review acknowledgement", page)
            status, _location, _page = self._post(
                "/attended/start-surface-runner", "reference=CO-0801&scope_reviewed=1&source_revision=rev1")
            self.assertEqual(status, 409)

    def test_start_with_changed_scope_or_revision_is_rejected(self):
        evaluation = _surface_evaluation()
        stale = _surface_evaluation(scope=dict(SURFACE_SCOPE, licensed_subdomains=1)).scope_digest
        with patch.object(dashboard, "evaluate_surface_fill_preflight", return_value=evaluation), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "start_attended_surface_runner", side_effect=AssertionError("no launch")):
            status, _location, page = self._post(
                "/attended/start-surface-runner",
                "reference=CO-0801&attended_create_authorized=1&scope_reviewed=1&source_revision=rev1&scope_digest=" + stale)
            self.assertEqual(status, 409)
            self.assertIn("computed scope changed", page)
            status, _location, page = self._post(
                "/attended/start-surface-runner",
                "reference=CO-0801&attended_create_authorized=1&scope_reviewed=1&source_revision=rev0&scope_digest="
                + evaluation.scope_digest)
            self.assertEqual(status, 409)
            self.assertIn("source revision changed", page)

    def test_reviewed_start_launches_the_surface_route_and_records_the_review(self):
        evaluation = _surface_evaluation()
        launched, recorded = [], []
        with patch.object(dashboard, "evaluate_surface_fill_preflight", return_value=evaluation), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "start_attended_surface_runner",
                             side_effect=lambda ref, rev: launched.append((ref, rev)) or True), \
                patch.object(dashboard, "start_attended_ce_only_runner", side_effect=AssertionError("not CE")), \
                patch.object(dashboard, "record_runner_start",
                             side_effect=lambda *args, **kwargs: recorded.append((args, kwargs))):
            status, location, _page = self._post(
                "/attended/start-surface-runner",
                "reference=CO-0801&attended_create_authorized=1&scope_reviewed=1&source_revision=rev1&scope_digest="
                + evaluation.scope_digest)
        self.assertEqual((status, location), (303, "/attended/ce-only-runner-status?ref=CO-0801"))
        self.assertEqual(launched, [("CO-0801", "rev1")])
        (args, kwargs), = recorded
        self.assertEqual(args[:2], ("CO-0801", "rev1"))
        self.assertEqual(kwargs["route"], "case_1_new_surface_only")
        self.assertEqual(kwargs["scope_reviewed_on"], args[2])

    def test_surface_runner_command_passes_the_route(self):
        with patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                patch.object(dashboard.subprocess, "Popen") as popen:
            self.assertTrue(dashboard.start_attended_surface_runner("CO-0801", "rev1"))
        command = popen.call_args.args[0]
        self.assertEqual(command[-2:], ["--route", "case_1_new_surface_only"])
        self.assertIn("--revision", command)

    def test_scan_reminder_after_a_verified_surface_run_and_its_acknowledgement(self):
        state = {"CO-0801": {"source_revision": "rev1", "route": "case_1_new_surface_only",
                             "result": "readback_verified", "completed_on": "2026-09-29T10:00:00"}}
        section = self._section(_surface_evaluation(), state)
        self.assertIn(dashboard.SCAN_REMINDER_TEXT, section)
        self.assertIn("Scan now / scanning interval are ON for this Leonardo Development tenant", section)
        self.assertIn("action='/attended/mark-scan-settings-off'", section)
        self.assertIn("Mark scan settings turned off", section)
        with patch.object(dashboard, "load_runner_state", return_value=state), \
                patch.object(dashboard, "evaluate_surface_fill_preflight", side_effect=AssertionError("no read")):
            status, location, _page = self._post("/attended/mark-scan-settings-off", "reference=CO-0801")
        self.assertEqual((status, location), (303, "/co/CO-0801"))
        stored = json.loads(dashboard.ATTENDED_REMINDERS_PATH.read_text(encoding="utf-8"))
        self.assertIn("scan_settings_off_on", stored["CO-0801"])
        section = self._section(_surface_evaluation(), state)
        self.assertNotIn("Mark scan settings turned off", section)
        self.assertIn("Scan settings marked off on", section)

    def test_scan_reminder_is_refused_for_ce_or_unverified_runs(self):
        for record in ({"source_revision": "rev1", "result": "readback_verified"},
                       {"source_revision": "rev1", "route": "case_1_new_surface_only", "result": "duplicate_found"},
                       None):
            state = {} if record is None else {"CO-0801": record}
            with self.subTest(record=record), patch.object(dashboard, "load_runner_state", return_value=state):
                status, _location, _page = self._post("/attended/mark-scan-settings-off", "reference=CO-0801")
                self.assertEqual(status, 409)
                self.assertNotIn(dashboard.SCAN_REMINDER_TEXT, self._section(_surface_evaluation(), state))
        self.assertFalse(dashboard.ATTENDED_REMINDERS_PATH.exists())

    def test_core_plus_reminder_before_onboarding_and_its_acknowledgement(self):
        evaluation = _surface_evaluation(core_plus=True)
        section = self._section(evaluation)
        self.assertIn(dashboard.CE_REMINDER_TEXT, section)
        self.assertIn("Core Plus (Credential Exposure) purchased", section)
        self.assertIn("<dt>Core Plus on account</dt><dd>yes — CE to be enabled later</dd>", section)
        self.assertIn("Mark CE enabled", section)
        self.assertIn("Start Onboarding", section)  # Core Plus never blocks the Surface route
        with patch.object(dashboard, "evaluate_surface_fill_preflight", return_value=evaluation):
            status, location, _page = self._post("/attended/mark-ce-enabled", "reference=CO-0801")
        self.assertEqual((status, location), (303, "/co/CO-0801"))
        self.assertIn("ce_enabled_on", json.loads(dashboard.ATTENDED_REMINDERS_PATH.read_text(encoding="utf-8"))["CO-0801"])
        section = self._section(evaluation)
        self.assertNotIn("Mark CE enabled", section)
        self.assertIn("Credential Exposure marked enabled on", section)

    def test_core_plus_reminder_after_onboarding_and_refused_without_core_plus(self):
        state = {"CO-0801": {"source_revision": "rev1", "route": "case_1_new_surface_only",
                             "result": "readback_verified", "completed_on": "2026-09-29T10:00:00"}}
        section = self._section(_surface_evaluation(core_plus=True), state)
        self.assertIn(dashboard.CE_REMINDER_TEXT, section)
        self.assertIn(dashboard.SCAN_REMINDER_TEXT, section)
        self.assertNotIn(dashboard.CE_REMINDER_TEXT, self._section(_surface_evaluation(core_plus=False)))
        with patch.object(dashboard, "evaluate_surface_fill_preflight", return_value=_surface_evaluation()):
            status, _location, _page = self._post("/attended/mark-ce-enabled", "reference=CO-0801")
        self.assertEqual(status, 409)
        self.assertFalse(dashboard.ATTENDED_REMINDERS_PATH.exists())

    def test_reminders_file_validation(self):
        self.assertEqual(dashboard.load_attended_reminders(), {})
        for content in ("{bad", "[]", json.dumps({"CO-0801": {"other": "2026-09-29T10:00:00"}}),
                        json.dumps({"CO-0801": {"ce_enabled_on": "yesterday"}}), json.dumps({"bad": {}})):
            with self.subTest(content=content):
                dashboard.ATTENDED_REMINDERS_PATH.write_text(content, encoding="utf-8")
                with self.assertRaises(dashboard.ReadUnavailable):
                    dashboard.load_attended_reminders()
        with self.assertRaises(ValueError):
            dashboard.record_attended_reminder("CO-0801", "other")

    def test_reminders_file_is_gitignored(self):
        from pathlib import Path
        gitignore = (Path(dashboard.__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("integration/attended_scan_reminders.json", gitignore.splitlines())
        self.assertEqual(dashboard.REMINDER_FIELDS, {"scan_settings_off": "scan_settings_off_on",
                                                     "ce_enabled": "ce_enabled_on"})


class SalesforceCliEncodingTests(unittest.TestCase):
    """Guard the CLI read/write path against non-locale (UTF-8) output.

    The Salesforce CLI emits UTF-8 JSON. Decoding it with the Windows locale
    code page (cp1252) raises UnicodeDecodeError on multi-byte sequences and
    leaves stdout as None, which previously crashed the dashboard handler.
    """

    @staticmethod
    def _completed(stdout, returncode: int = 0):
        return subprocess.CompletedProcess(args=["sf"], returncode=returncode, stdout=stdout, stderr="")

    def test_sf_json_parses_utf8_output_with_non_ascii(self):
        payload = json.dumps({"status": 0, "result": {"records": [{"Name": "Café CO-0702", "Note": "sévé—✓"}]}})
        with patch("tools.serve_attended_open_onboardings_dashboard.subprocess.run", return_value=self._completed(payload)), \
                patch("tools.serve_attended_open_onboardings_dashboard.salesforce_cli_command", return_value="sf"):
            parsed = dashboard.sf_json(["data", "query", "--query", "SELECT 1", "--json"])
        self.assertEqual(parsed["status"], 0)
        self.assertEqual(parsed["result"]["records"][0]["Name"], "Café CO-0702")

    def test_sf_json_fails_closed_when_stdout_is_none(self):
        with patch("tools.serve_attended_open_onboardings_dashboard.subprocess.run", return_value=self._completed(None)), \
                patch("tools.serve_attended_open_onboardings_dashboard.salesforce_cli_command", return_value="sf"):
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.sf_json(["data", "query", "--query", "SELECT 1", "--json"])

    def test_sf_json_fails_closed_on_nonzero_returncode(self):
        with patch("tools.serve_attended_open_onboardings_dashboard.subprocess.run", return_value=self._completed("{}", returncode=1)), \
                patch("tools.serve_attended_open_onboardings_dashboard.salesforce_cli_command", return_value="sf"):
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.sf_json(["data", "query", "--query", "SELECT 1", "--json"])

    def test_sf_write_json_fails_closed_when_stdout_is_none(self):
        with patch("tools.serve_attended_open_onboardings_dashboard.subprocess.run", return_value=self._completed(None)), \
                patch("tools.serve_attended_open_onboardings_dashboard.salesforce_cli_command", return_value="sf"):
            with self.assertRaises(dashboard.WriteUnavailable):
                dashboard.sf_write_json(["data", "update", "record", "Customer_Onboarding__c", "id", "--json"])
