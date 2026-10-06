from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from tools.serve_attended_open_onboardings_dashboard import (
    ONBOARDING_STAGES, derive_onboarding_stage, user_created_evidence, user_created_proof,
)


class DeriveStageTests(unittest.TestCase):
    def stage(self, sf=None, approval=None, **evidence):
        return derive_onboarding_stage(sf, approval, **evidence)

    def test_every_stage_is_reached_by_its_proof(self):
        self.assertEqual(self.stage("New", "Pending")["index"], 0)
        self.assertEqual(self.stage("New", "Approved")["index"], 1)
        self.assertEqual(self.stage("New", "Approved", tenant_created=True)["index"], 2)
        self.assertEqual(self.stage("New", "Approved", tenant_created=True, scan_done=True)["index"], 3)
        self.assertEqual(self.stage("New", "Approved", tenant_created=True, scan_done=True, user_created=True)["index"], 4)
        self.assertEqual(self.stage("Onboarding Completed", "Approved", tenant_created=True, scan_done=True,
                                    user_created=True)["index"], 5)

    def test_completed_comes_only_from_salesforce(self):
        full = self.stage("User Created", "Approved", tenant_created=True, scan_done=True, user_created=True)
        self.assertEqual(full["stage"], "User Created")

    def test_missing_data_never_jumps_ahead(self):
        # A finished scan without a verified create stays at the last provable stage.
        self.assertEqual(self.stage("New", "Approved", scan_done=True)["index"], 1)
        # Users present but no finished scan: stays at Account Scanning.
        self.assertEqual(self.stage("New", "Approved", tenant_created=True, user_created=True)["index"], 2)
        # Evidence without approval never reaches Request Approved.
        self.assertEqual(self.stage("New", "Pending", tenant_created=True, scan_done=True)["index"], 0)
        # Unknown (None) or False user evidence stops the chain.
        for value in (None, False):
            self.assertEqual(self.stage("New", "Approved", tenant_created=True, scan_done=True,
                                        user_created=value)["index"], 3)
        # Nothing is known at all.
        self.assertEqual(self.stage(None, None)["index"], 0)
        self.assertEqual(self.stage("garbage", "Approved")["index"], 1)

    def test_salesforce_stage_counts_for_itself_and_marks_ahead_or_behind(self):
        ahead = self.stage("Request Approved", "Approved", tenant_created=True)
        self.assertEqual((ahead["index"], ahead["salesforce_index"], ahead["ahead"], ahead["behind"]), (2, 1, True, False))
        same = self.stage("Request Approved", "Approved")
        self.assertFalse(same["ahead"] or same["behind"])
        behind = self.stage("Scan Completed Successfully", "Approved")
        self.assertEqual(behind["index"], 3)  # Salesforce is the authority for its own stage
        self.assertFalse(behind["ahead"])
        unknown = self.stage(None, "Approved", tenant_created=True)
        self.assertFalse(unknown["ahead"])

    def test_stage_names_match_salesforce_path(self):
        self.assertEqual(ONBOARDING_STAGES, ("New", "Request Approved", "Account Scanning",
                                             "Scan Completed Successfully", "User Created", "Onboarding Completed"))


class UserCreatedEvidenceTests(unittest.TestCase):
    def test_dev_needs_operator_and_customer_user(self):
        both = user_created_evidence({"operator_assigned": True, "primary_user": {"present": True}}, "dev")
        self.assertIs(both["created"], True)
        no_user = user_created_evidence({"operator_assigned": True, "primary_user": {"present": False}}, "dev")
        self.assertIs(no_user["created"], False)
        self.assertEqual(no_user["missing"], ["customer user"])
        no_operator = user_created_evidence({"operator_assigned": False, "primary_user": {"present": True}}, "dev")
        self.assertEqual((no_operator["created"], no_operator["missing"]), (False, ["operator account"]))

    def test_unknown_parts_are_never_guessed(self):
        self.assertIsNone(user_created_evidence({"operator_assigned": None, "primary_user": {"present": True}}, "dev")["created"])
        self.assertIsNone(user_created_evidence({}, "dev")["created"])
        self.assertIsNone(user_created_evidence(None, "dev")["created"])
        # The production clone has no primary-user data: the customer part stays unknown.
        clone = user_created_evidence({"operator_assigned": True, "primary_user": {"present": False}}, "prod-clone")
        self.assertIsNone(clone["created"])
        self.assertEqual(clone["unknown"], ["customer user"])
        # A missing operator is still provable on the clone.
        self.assertIs(user_created_evidence({"operator_assigned": False}, "prod-clone")["created"], False)


class UserCreatedProofTests(unittest.TestCase):
    def test_automatic_needs_primary_user_and_operator(self):
        proof = user_created_proof(True, True, None, "2026-10-06")
        self.assertEqual((proof["created"], proof["proof"]), (True, "automatic"))
        self.assertIn("Primary user verified in Leonardo", proof["text"])
        for matches, operator, missing in ((True, False, "No operator assigned"),
                                           (False, True, "Primary user not verified"),
                                           (None, True, "Primary user not verified")):
            proof = user_created_proof(matches, operator, None)
            self.assertIs(proof["created"], False)
            self.assertIn(missing, proof["text"])

    def test_manual_confirmation_is_enough(self):
        proof = user_created_proof(False, False, {"confirmed": True, "confirmed_on": "2026-10-05"})
        self.assertEqual((proof["created"], proof["proof"]), (True, "manual"))
        self.assertIn("Confirmed manually on 2026-10-05", proof["text"])
        self.assertEqual(user_created_proof(True, True, {"confirmed": False})["proof"], "automatic")

    def test_user_created_never_jumps_ahead_of_earlier_stages(self):
        proof = user_created_proof(True, True, {"confirmed": True, "confirmed_on": "2026-10-05"})
        result = derive_onboarding_stage("New", "Approved", tenant_created=True, scan_done=False,
                                         user_created=proof["created"])
        self.assertEqual(result["index"], 2)


class ConfirmationStoreAndRouteTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [f.unlink() for f in directory.iterdir()] or directory.rmdir())
        patcher = patch.object(dashboard, "USER_CREATED_CONFIRMATION_PATH", directory / "confirm.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = dashboard.USER_CREATED_CONFIRMATION_PATH

    def test_confirm_and_undo_store_only_booleans_and_dates(self):
        dashboard.set_user_created_confirmation("CO-0767", True, "Some Operator")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(stored["CO-0767"]), {"confirmed", "confirmed_on", "confirmed_by"})
        self.assertIs(stored["CO-0767"]["confirmed"], True)
        self.assertEqual(len(stored["CO-0767"]["confirmed_on"]), 10)
        self.assertEqual(dashboard.load_user_created_confirmations()["CO-0767"]["confirmed_by"], "Some Operator")
        dashboard.set_user_created_confirmation("CO-0767", False, None)
        self.assertEqual(dashboard.load_user_created_confirmations(), {})
        dashboard.set_user_created_confirmation("CO-0768", True, None)
        self.assertEqual(dashboard.load_user_created_confirmations()["CO-0768"]["confirmed_by"], "operator")

    def test_bad_reference_and_corrupt_file_fail_closed(self):
        with self.assertRaises(ValueError):
            dashboard.set_user_created_confirmation("bad", True, None)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(dashboard.load_user_created_confirmations(), {})
        self.path.write_text(json.dumps({"CO-0767": {"confirmed": "yes", "confirmed_on": "2026-10-06", "confirmed_by": "x"}}),
                             encoding="utf-8")
        self.assertEqual(dashboard.load_user_created_confirmations(), {})

    def _post(self, path, body, origin=None):
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
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if origin is not None:
            headers["Origin"] = origin
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status, response.getheader("Location")

    def test_guards_are_enforced_and_the_signed_in_identity_is_recorded(self):
        for route in ("/attended/confirm-user-created", "/attended/unconfirm-user-created"):
            self.assertIn(route, dashboard.POST_ROUTES)
        with patch.object(dashboard, "login_required", return_value=True), \
                patch.object(dashboard, "dashboard_operator", return_value=None):
            status, _location = self._post("/attended/confirm-user-created", "reference=CO-0767")
        self.assertEqual(status, 403)
        self.assertFalse(self.path.exists())
        with patch.object(dashboard, "login_required", return_value=False):
            status, _location = self._post("/attended/confirm-user-created", "reference=CO-0767", "http://evil.example")
        self.assertEqual(status, 403)
        self.assertFalse(self.path.exists())
        with patch.object(dashboard, "login_required", return_value=True), \
                patch.object(dashboard, "dashboard_operator", return_value="Signed In"):
            self.assertEqual(self._post("/attended/confirm-user-created", "reference=CO-0767"), (303, "/co/CO-0767"))
            self.assertEqual(dashboard.load_user_created_confirmations()["CO-0767"]["confirmed_by"], "Signed In")
            self.assertEqual(self._post("/attended/unconfirm-user-created", "reference=CO-0767"), (303, "/co/CO-0767"))
        self.assertEqual(dashboard.load_user_created_confirmations(), {})
        with patch.object(dashboard, "login_required", return_value=False):
            status, _location = self._post("/attended/confirm-user-created", "reference=bad")
        self.assertEqual(status, 400)


class StepperRenderTests(unittest.TestCase):
    def state(self, index, **extra):
        return {"index": index, "salesforce_index": extra.pop("sf", index), "ahead": extra.pop("ahead", False),
                "evidence": extra.pop("evidence", None), "environment": "dev", "captured_at": "", **extra}

    def test_done_current_upcoming_with_accessible_text(self):
        html = dashboard._stage_tracker_html(self.state(2))
        self.assertIn("aria-label='Onboarding stage'", html)
        self.assertEqual(html.count("class='st done'"), 2)
        self.assertEqual(html.count("class='st current' aria-current='step'"), 1)
        self.assertEqual(html.count("class='st upcoming'"), 3)
        self.assertIn("Account Scanning<span class='sr'> — current stage</span>", html)
        self.assertIn("New<span class='sr'> — done</span>", html)
        self.assertIn("Onboarding Completed<span class='sr'> — upcoming</span>", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("http", html)

    def test_completed_is_all_done(self):
        html = dashboard._stage_tracker_html(self.state(5))
        self.assertEqual(html.count("class='st done'"), 6)
        self.assertNotIn("aria-current", html)

    def test_ahead_of_salesforce_is_highlighted(self):
        html = dashboard._stage_tracker_html(self.state(2, sf=1, ahead=True))
        self.assertIn("Salesforce still shows: Request Approved", html)
        self.assertIn("nothing is written to Salesforce", html)
        self.assertNotIn("still shows", dashboard._stage_tracker_html(self.state(1, sf=1)))

    def test_missing_part_is_named(self):
        proof = user_created_proof(False, False, None)
        html = dashboard._stage_tracker_html(self.state(3, user_proof=proof), "CO-0702")
        self.assertIn("Primary user not verified", html)
        self.assertIn("run Validate", html)
        self.assertIn("No operator assigned", html)
        self.assertIn("action='/attended/confirm-user-created'", html)
        self.assertIn("Confirm user created", html)

    def test_manual_proof_shows_date_and_undo(self):
        proof = user_created_proof(None, False, {"confirmed": True, "confirmed_on": "2026-10-06"})
        html = dashboard._stage_tracker_html(self.state(4, user_proof=proof), "CO-0702")
        self.assertIn("Confirmed manually on 2026-10-06", html)
        self.assertIn("action='/attended/unconfirm-user-created'", html)
        self.assertNotIn("Confirm user created", html)


def _validation(matches, checked_on):
    from datetime import datetime
    now = datetime(2026, 10, 6, 9, 0)
    return {"CO-0702": {"primary_user_matches": matches, "primary_user_checked_on": checked_on, "checks": [],
                        "plan_note": "", "observed_at": now, "expires_at": now}}


class DetailPageTrackerTests(unittest.TestCase):
    ROW = {"Name": "CO-0702", "Account_Name__c": "Acme", "Onboarding_Product__c": "Credential Exposure",
           "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Approval_Status__c": "Approved",
           "Onboarding_Stage__c": "Request Approved", "Main_Domain__c": "acme.example"}
    READBACK = {"surface_account_id": "A" * 20, "account_uuid": "a" * 32, "leonardo_state": "Account Scanning",
                "observed_on": "2026-10-06", "source": "Leonardo Development Details readback"}

    def render(self, readback, scan=None, tenant=None, validation=None, confirmations=None):
        with patch.object(dashboard, "attended_leonardo_readbacks", return_value=readback), \
                patch.object(dashboard, "attended_scan_statuses", return_value=scan or {}), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "attended_validations", return_value=validation or {}), \
                patch.object(dashboard, "load_user_created_confirmations", return_value=confirmations or {}), \
                patch.object(dashboard, "load_attended_reminders", return_value={}), \
                patch.object(dashboard, "_stage_tenant", return_value=(tenant, "dev" if tenant else "", "2026-10-06T08:00")), \
                patch.object(dashboard, "sf_json", side_effect=AssertionError("no Salesforce")):
            return dashboard.page_detail("CO-0702", dict(self.ROW))

    def test_tracker_is_first_and_forms_are_unchanged(self):
        page = self.render({})
        self.assertLess(page.index("class='tracker'"), page.index("<h1>CO-0702</h1>"))
        self.assertEqual(page.count("class='st current' aria-current='step'"), 1)
        self.assertIn("Request Approved<span class='sr'> — current stage</span>", page)
        self.assertIn("action='/attended/", page)

    def test_scan_done_and_users_advance_the_tracker_and_checks_are_folded(self):
        tenant = {"operator_assigned": True, "primary_user": {"present": True},
                  "license": {"start_date": "2026-10-01", "expiration_date": "2027-09-30"}}
        page = self.render({"CO-0702": dict(self.READBACK)}, {"CO-0702": {"execution_state": "done", "state": "scan_completed"}}, tenant,
                           validation=_validation(True, "2026-10-06"))
        self.assertIn("Primary user verified in Leonardo", page)
        self.assertIn("User Created<span class='sr'> — current stage</span>", page)
        self.assertIn("Salesforce still shows: Request Approved", page)
        self.assertIn("2026-10-01 → 2027-09-30", page)
        self.assertIn("AAAAAAAA…", page)
        self.assertLess(page.index("class='tracker'"), page.index("Tenant checks"))
        self.assertIn("<details class='more'><summary><h2 class='sum-h'>Tenant checks</h2>", page)

    def test_manual_confirmation_advances_and_missing_proof_blocks(self):
        scan = {"CO-0702": {"execution_state": "done", "state": "scan_completed"}}
        tenant = {"operator_assigned": False, "primary_user": {"present": True}}
        page = self.render({"CO-0702": dict(self.READBACK)}, scan, tenant,
                           validation=_validation(True, ""))
        self.assertIn("Scan Completed Successfully<span class='sr'> — current stage</span>", page)
        self.assertIn("No operator assigned", page)
        page = self.render({"CO-0702": dict(self.READBACK)}, scan, tenant,
                           confirmations={"CO-0702": {"confirmed": True, "confirmed_on": "2026-10-06", "confirmed_by": "operator"}})
        self.assertIn("User Created<span class='sr'> — current stage</span>", page)
        self.assertIn("Confirmed manually on 2026-10-06", page)

    def test_readback_without_scan_stays_at_account_scanning(self):
        page = self.render({"CO-0702": dict(self.READBACK)})
        self.assertIn("Account Scanning<span class='sr'> — current stage</span>", page)


if __name__ == "__main__":
    unittest.main()
