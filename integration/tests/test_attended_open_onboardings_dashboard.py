from __future__ import annotations

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
    salesforce_cli_command,
    start_attended_salesforce_login,
    CommentUpdateEvaluation,
    consume_comment_update_ack,
    issue_comment_update_ack,
    update_co0741_comment_after_confirmation,
)


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
        self.assertNotIn("CO-0740</span>", page)
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
