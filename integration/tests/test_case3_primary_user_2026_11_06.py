"""Case 3 primary user (owner request + HAR 2026-11-06, CO-0841): plan, exact-body guard, User Created move, runner gates."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from integration.onboarding import case3_followup
from integration.onboarding import production_case3_primary_user as primary

NOW = datetime(2026, 11, 6, 22, 0, tzinfo=timezone.utc)
UUID = "0123456789abcdef0123456789abcdef"


def co_row(**changes):
    row = {"Id": "a0B000000000001AAA", "Name": "CO-0841", "Onboarding_Product__c": "Surface & Credential Exposure",
           "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Stage__c": "Scan Completed Successfully",
           "Main_Domain__c": "example.org", "Email_Domains__c": "example.org", "Primary_User__c": "003000000000001",
           "Primary_User__r": {"FirstName": "Ada", "LastName": "Lovelace", "Email": "ada.lovelace@example.org"}}
    row.update(changes)
    return row


class PlanTests(unittest.TestCase):
    def test_plan_from_the_primary_user_contact(self):
        user, problem = primary.plan(co_row())
        self.assertEqual(problem, "")
        self.assertEqual((user.first_name, user.last_name, user.domain), ("Ada", "Lovelace", "example.org"))
        self.assertNotIn("ada.lovelace", repr(user))  # the address never reaches a repr / log

    def test_plan_refusals(self):
        cases = {
            "route_not_case3_new": co_row(Onboarding_Product__c="Credential Exposure"),
            "primary_user_missing": co_row(Primary_User__c=None, Primary_User__r=None),
            "primary_user_email_invalid": co_row(Primary_User__r={"FirstName": "Ada", "LastName": "L", "Email": "nope"}),
            "primary_user_domain_not_on_co": co_row(Primary_User__r={"FirstName": "Ada", "LastName": "L",
                                                                     "Email": "ada@other.com"}),
            "primary_user_name_invalid": co_row(Primary_User__r={"FirstName": "<b>", "LastName": "L",
                                                                 "Email": "ada@example.org"}),
            "co_unreadable": None,
        }
        for code, row in cases.items():
            self.assertEqual(primary.plan(row), (None, code), code)

    def test_stage_problem(self):
        self.assertIsNone(primary.stage_problem(co_row()))
        self.assertIsNone(primary.stage_problem(co_row(Onboarding_Stage__c="User Created")))
        self.assertEqual(primary.stage_problem(co_row(Onboarding_Stage__c="Account Scanning")), "scan_not_marked_finished")
        self.assertEqual(primary.stage_problem(co_row(Onboarding_Stage__c="Request Approved")), "stage_not_expected")


class BodyAndReplyTests(unittest.TestCase):
    def setUp(self):
        self.user, _ = primary.plan(co_row())

    def test_only_the_exact_har_body_matches(self):
        good = {"firstName": "Ada", "lastName": "Lovelace", "email": "ADA.lovelace@example.org", "roleNames": ["USER_ACCOUNT_ADMIN"]}
        self.assertTrue(primary.body_matches(json.dumps(good), self.user))
        for bad in ({**good, "roleNames": ["USER_ACCOUNT_OPERATOR"]}, {**good, "roleNames": ["USER_ACCOUNT_ADMIN", "X"]},
                    {**good, "email": "eve@example.org"}, {**good, "firstName": "Eve"}, {**good, "phoneNumber": "1"}, "nope"):
            self.assertFalse(primary.body_matches(json.dumps(bad) if isinstance(bad, dict) else bad, self.user))

    def test_users_list_states(self):
        def reply(rows, total=None):
            return {"pagination_response": {"total_count": len(rows) if total is None else total, "table_data": rows}}

        attended = {"email": "milton.stevenson+x@pentera.io", "roleNames": ["USER_ACCOUNT_ADMIN"], "primaryUser": True}
        mine = {"email": "Ada.Lovelace@example.org", "roleNames": ["USER_ACCOUNT_ADMIN"], "primaryUser": False}
        self.assertEqual(primary.user_state(reply([attended]), self.user), "absent")
        self.assertEqual(primary.user_state(reply([attended, mine]), self.user), "present_admin")
        self.assertEqual(primary.user_state(reply([{**mine, "roleNames": ["USER_ACCOUNT_VIEWER"]}]), self.user),
                         "present_other_role")
        self.assertIsNone(primary.user_state(reply([attended], total=11), self.user))  # incomplete page: fail closed
        self.assertIsNone(primary.user_state({"oops": 1}, self.user))

    def test_users_list_body_and_control_path(self):
        self.assertTrue(primary.users_list_body_ok(json.dumps({"tableServerData": {"offset": 0}})))
        self.assertFalse(primary.users_list_body_ok(json.dumps({"tableServerData": {}, "delete": True})))
        self.assertEqual(primary.control_id("GET", "/api/v1/backoffice/account/control/" + "a" * 24), "a" * 24)
        self.assertIsNone(primary.control_id("POST", "/api/v1/backoffice/account/control/" + "a" * 24))

    def test_text_names_an_unreleased_tenant(self):
        self.assertIn("Give up control", primary.text("add_rejected+" + primary.CONTROL_NOT_RELEASED))


class GuardTests(unittest.TestCase):
    def setUp(self):
        import tools.attended_ce_only_playwright as runner

        self.runner = runner
        self.user, _ = primary.plan(co_row())
        self.guard = runner.PrimaryUserGuard("b" * 24, self.user)

    def _route(self, method, path, body=None):
        route = mock.Mock()
        route.request.method, route.request.url, route.request.post_data = method, "https://app.pentera.io" + path, body
        self.guard.handle(route)
        return "continue" if route.continue_.called else "abort" if route.abort.called else "other"

    def test_add_only_when_armed_once_and_exact(self):
        good = json.dumps({"firstName": "Ada", "lastName": "Lovelace", "email": "ada.lovelace@example.org",
                           "roleNames": ["USER_ACCOUNT_ADMIN"]})
        self.assertEqual(self._route("POST", primary.ADD_PATH, good), "abort")  # not armed
        self.guard.add_armed = True
        wrong = json.dumps({**json.loads(good), "roleNames": ["USER_ACCOUNT_OPERATOR"]})
        self.assertEqual(self._route("POST", primary.ADD_PATH, wrong), "abort")
        self.assertEqual(self.guard.add_mismatch, 1)
        self.assertEqual(self._route("POST", primary.ADD_PATH, good), "continue")
        self.assertEqual(self._route("POST", primary.ADD_PATH, good), "abort")  # never a second add

    def test_control_only_for_the_verified_tenant_and_other_writes_abort(self):
        self.assertEqual(self._route("GET", "/api/v1/backoffice/account/control/" + "b" * 24), "continue")
        self.assertEqual(self._route("GET", "/api/v1/backoffice/account/control/" + "c" * 24), "abort")
        self.assertEqual(self._route("POST", primary.USERS_PATH, json.dumps({"tableServerData": {}})), "continue")
        self.assertEqual(self._route("POST", primary.GIVE_UP_PATH, "{}"), "continue")
        self.assertEqual(self._route("POST", "/api/v1/administration/users/delete", "{}"), "abort")
        self.assertEqual(self._route("POST", "/api/v1/backoffice/account/" + "b" * 24 + "/edit", "{}"), "abort")


class UserCreatedMoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.inputs = case3_followup.FollowupInputs(reference="CO-0841", records_parent=self.parent, now=NOW, vm=False,
                                                    sf_ready=True)
        self.verified = [{"outcome": "readback_verified", "route": "case_3_combined_baseline", "co_reference": "CO-0841",
                          "recorded_at": "2026-11-05T20:44:44Z", "account_id": "a" * 24, "account_uuid": UUID}]
        constants = mock.patch.object(case3_followup, "_both_constants", return_value=True)
        constants.start()
        self.addCleanup(constants.stop)

    def _move(self, rows, accepted=True):
        calls, reads = [], iter(rows)
        result = case3_followup.after_primary_user(self.inputs, self.verified, lambda: next(reads),
                                                   lambda argv: calls.append(argv) or accepted, "surface-onboarding")
        return result, calls

    def test_needs_a_done_primary_user_result(self):
        self.assertEqual(self._move([co_row()]), ("primary_user_not_done", []))
        case3_followup.record_primary_user(self.parent, "CO-0841", "add_rejected", NOW, "example.org")
        self.assertEqual(self._move([co_row()]), ("primary_user_not_done", []))

    def test_moves_once_after_by_hand_or_created(self):
        case3_followup.record_primary_user(self.parent, "CO-0841", primary.BY_HAND, NOW, "example.org")
        result, calls = self._move([co_row(), co_row(Onboarding_Stage__c="User Created")])
        self.assertEqual(result, "written_verified")
        self.assertEqual(len(calls), 1)
        self.assertIn("Onboarding_Stage__c='User Created'", calls[0])
        self.assertEqual(case3_followup.last_stage(self.parent, "CO-0841"), "User Created")
        self.assertEqual(self._move([co_row(Onboarding_Stage__c="User Created")]), ("already_present", []))

    def test_wrong_stage_and_vm_write_nothing(self):
        case3_followup.record_primary_user(self.parent, "CO-0841", primary.CREATED, NOW, "example.org")
        self.assertEqual(self._move([co_row(Onboarding_Stage__c="Account Scanning")]), ("stage_not_expected", []))
        vm = case3_followup.FollowupInputs(reference="CO-0841", records_parent=self.parent, now=NOW, vm=True, sf_ready=True)
        self.assertEqual(case3_followup.after_primary_user(vm, self.verified, lambda: co_row(), lambda a: True, "x"),
                         "vm_read_only")

    def test_record_keeps_domain_and_safe_diagnostics_only(self):
        case3_followup.record_primary_user(self.parent, "CO-0841", "add_form_unavailable", NOW, "example.org",
                                           {"field_first": "1", "menu_items": "Button-Grid_Edit,Button-Grid_Control",
                                            "bad": "Ada Lovelace"})
        text = (self.parent / "production-create-records" / case3_followup.WRITES_FILE).read_text(encoding="utf-8")
        self.assertIn("example.org", text)
        self.assertIn("Button-Grid_Control", text)
        self.assertNotIn("Ada", text)


class RunnerGateTests(unittest.TestCase):
    """The runner refuses before any browser; the opener must never be called in these cases."""

    def setUp(self):
        import tools.attended_ce_only_playwright as runner

        self.runner = runner
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.opener = mock.Mock(side_effect=AssertionError("no browser"))
        os_name = mock.patch.object(runner, "_launch_os_name", return_value="nt")
        os_name.start()
        self.addCleanup(os_name.stop)
        progress = mock.patch.object(runner, "PROGRESS_PATH", self.parent / "progress.json")  # never the real progress file
        progress.start()
        self.addCleanup(progress.stop)

    def _run(self, **kw):
        line = self.runner.run_production_primary_user("CO-0841", environ={"SURFACE_ONBOARDING_RUNTIME": "desktop"},
                                                       now=NOW, records_parent=self.parent, open_page=self.opener, **kw)
        return json.loads(line)["result"]

    def test_vm_refuses(self):
        line = self.runner.run_production_primary_user("CO-0841", environ={"SURFACE_ONBOARDING_RUNTIME": "vm"},
                                                       records_parent=self.parent, open_page=self.opener)
        self.assertEqual(json.loads(line)["result"], "browser_launch_not_allowed")

    def test_done_and_needs_review_never_open_a_browser(self):
        case3_followup.record_primary_user(self.parent, "CO-0841", primary.BY_HAND, NOW)
        self.assertEqual(self._run(), "already_done")
        other = Path(tempfile.mkdtemp(dir=self.tmp.name))
        case3_followup.record_primary_user(other, "CO-0841", "write_started", NOW)
        line = self.runner.run_production_primary_user("CO-0841", environ={"SURFACE_ONBOARDING_RUNTIME": "desktop"},
                                                       records_parent=other, open_page=self.opener)
        self.assertEqual(json.loads(line)["result"], "primary_user_needs_review")

    def test_no_verified_create_and_bad_co_never_open_a_browser(self):
        self.assertEqual(self._run(), "no_verified_case3_create")
        verified = {"outcome": "readback_verified", "route": "case_3_combined_baseline", "co_reference": "CO-0841",
                    "recorded_at": "2026-11-05T20:44:44Z", "account_id": "a" * 24, "tenant_name": "Example Tenant"}
        with mock.patch("integration.onboarding.production_create_ui.load_co_records", return_value=[verified]), \
                mock.patch("integration.onboarding.production_create_ui.co_status",
                           return_value={"code": "created_verified"}), \
                mock.patch("integration.onboarding.production_create_ui.CREATED_VERIFIED", "created_verified"):
            self.assertEqual(self._run(read_co=lambda q: [co_row(Primary_User__c=None, Primary_User__r=None)]),
                             "primary_user_missing")
            self.assertEqual(self._run(read_co=lambda q: [co_row(Onboarding_Stage__c="Account Scanning")]),
                             "scan_not_marked_finished")
        self.opener.assert_not_called()


class ControlMenuItemTests(unittest.TestCase):
    """2026-11-11 (CO-0844): the take-control item is "Access" (Button-Grid_Access); the first live run looked for "control"."""

    LIVE = ["Button-Grid_Access", "Button-Grid_Scan_Now", "Button-Grid_Edit", "Button-Grid_Details", "Button-Grid_Stop_Scan",
            "Button-Grid_Delete"]

    def test_the_live_menu_gives_access(self):
        self.assertEqual(primary.control_item_token(self.LIVE), "Button-Grid_Access")

    def test_a_control_named_item_still_works(self):
        self.assertEqual(primary.control_item_token(["Button-Grid_Edit", "Button-Grid_Take_Control"]), "Button-Grid_Take_Control")

    def test_never_destructive_scan_or_ambiguous(self):
        self.assertIsNone(primary.control_item_token(["Button-Grid_Delete", "Button-Grid_Scan_Now", "Button-Grid_Stop_Scan"]))
        self.assertIsNone(primary.control_item_token(["Button-Grid_Access", "Button-Grid_Take_Control"]))
        self.assertIsNone(primary.control_item_token(["Button-Grid_Access", "Button-Grid_Access"]))
        self.assertIsNone(primary.control_item_token(["Button-Grid_Delete_Control"]))
        self.assertIsNone(primary.control_item_token([]))
        self.assertIsNone(primary.control_item_token(["access", "Button-Grid_Access x"]))

class SuffixDoneTests(unittest.TestCase):
    """CO-0844 2026-11-11: created and read back, then control not given back; the user exists, so it is done."""

    def test_created_with_control_not_released_is_done(self):
        self.assertTrue(primary.is_done("user_created_verified+control_not_released"))
        self.assertTrue(primary.is_done("user_already_present"))
        self.assertFalse(primary.is_done("add_form_unavailable+control_not_released"))
        self.assertFalse(primary.is_done("user_created_unverified"))
        self.assertFalse(primary.is_done(None))

if __name__ == "__main__":
    unittest.main()
