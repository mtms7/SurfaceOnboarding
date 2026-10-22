"""Dashboard wiring of the Case 3 live create and its owner-confirmed Salesforce follow-up (path B, owner go 2026-10-22).

* the create launcher names the Case 3 route to the runner and starts NO automatic follow-up (CE-only keeps its worker thread);
* the progress bar of a Case 3 create waits for the owner's review instead of writing anything;
* the tenant name the gate needs is the owner-confirmed name (or the short account name), never a guess;
* the review page shows only field names and the last four characters, the confirm page writes through ``case3_followup`` only;
* nothing is reachable while a lock constant is False or on the VM.
Hermetic: fake processes and Salesforce, temporary folders, no browser.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import case3_followup as fu
from integration.onboarding import production_create_ui as create_ui
from integration.onboarding import tenant_name_decision as tnd
from integration.tests import test_case3_live_create_ui as base
import tools.attended_ce_only_playwright as runner

CO, REV, NOW, OWNER, NAME, CASE3 = base.CO, base.REV, base.NOW, base.OWNER, base.NAME, base.CASE3
ACCOUNT_ID, UUID = base.ACCOUNT_ID, base.UUID


class Process:
    def __init__(self, running=True):
        self.running = running

    def poll(self):
        return None if self.running else 0


class Fixture(base.Base):
    def setUp(self):
        super().setUp()
        for patch in (mock.patch.dict(dashboard.PRODUCTION_RUNS, clear=True),
                      mock.patch.object(dashboard, "production_records_parent", lambda: self.dir)):
            patch.start()
            self.addCleanup(patch.stop)


class LaunchTests(Fixture):
    def launch(self, *args, **kw):
        env = {"SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1", "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": base.REF}
        with mock.patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                mock.patch.object(dashboard.subprocess, "Popen", return_value=Process(True)) as popen:
            result = dashboard._launch_production_create(CO, REV, env, *args, **kw)
        return result, popen

    def test_the_case_3_launch_names_the_route_and_the_owner_go(self):
        process, popen = self.launch(route=CASE3)
        self.assertIsNotNone(process)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[2:], ["--production-create", "--co", CO, "--revision", REV, "--owner-go", "--route", CASE3])

    def test_the_ce_only_launch_is_unchanged(self):
        _process, popen = self.launch()
        self.assertEqual(popen.call_args.args[0][2:], ["--production-create", "--co", CO, "--revision", REV, "--owner-go"])

    def test_a_case_3_run_starts_no_automatic_follow_up_and_records_its_route(self):
        started = []
        with mock.patch.object(dashboard, "_launch_production_create", return_value=Process(True)), \
                mock.patch.object(dashboard, "Thread", side_effect=lambda **kw: started.append(kw) or mock.Mock()):
            dashboard._launch_production_create_followed(CO, REV, {}, route=CASE3)
        self.assertEqual(started, [])
        self.assertEqual(dashboard.PRODUCTION_RUNS[CO]["route"], CASE3)

    def test_a_ce_only_run_still_starts_its_follow_up_worker(self):
        started = []
        with mock.patch.object(dashboard, "_launch_production_create", return_value=Process(True)), \
                mock.patch.object(dashboard, "Thread", side_effect=lambda **kw: started.append(kw) or mock.Mock()):
            dashboard._launch_production_create_followed(CO, REV, {})
        self.assertEqual(len(started), 1)

    def test_a_failed_launch_records_nothing(self):
        with mock.patch.object(dashboard, "_launch_production_create", return_value=None):
            self.assertIsNone(dashboard._launch_production_create_followed(CO, REV, {}, route=CASE3))
        self.assertNotIn(CO, dashboard.PRODUCTION_RUNS)


class ProgressTests(Fixture):
    def test_the_case_3_bar_waits_for_the_review_and_never_spins_on_a_missing_follow_up(self):
        since = "2026-10-22T11:00:00Z"
        dashboard.PRODUCTION_RUNS[CO] = {"since": since, "follow": None, "route": CASE3}
        self.record(outcome="readback_verified", when=NOW.replace(hour=11, minute=30), account_id=ACCOUNT_ID,
                    account_uuid=UUID, tenant_name=NAME)
        progress = dashboard._production_run_progress(CO)
        self.assertEqual([state for _l, state in progress["steps"]][:4], ["done", "done", "done", "active"])
        self.assertIsNone(progress["failed"])
        self.assertIn("review", progress["current"].lower())

    def test_a_ce_only_run_keeps_the_ce_progress(self):
        dashboard.PRODUCTION_RUNS[CO] = {"since": "2026-10-22T11:00:00Z", "follow": None}
        progress = dashboard._production_run_progress(CO)
        self.assertEqual(len(progress["steps"]), 5)
        self.assertIn("Writing the Account UUID", progress["steps"][3][0])


class InputsTests(Fixture):
    def setUp(self):
        super().setUp()
        self.names = self.dir / tnd.STATE_FILE_NAME
        for patch in (mock.patch.object(runner, "TENANT_NAMES_PATH", self.names),
                      mock.patch.object(dashboard, "_salesforce_ready_for_create", lambda: True),
                      mock.patch.object(dashboard, "_clone_status", lambda: dict(base.FRESH_CLONE)),
                      mock.patch.object(dashboard, "production_now", lambda: NOW),
                      mock.patch.object(dashboard, "vm_mode", lambda: False)):
            patch.start()
            self.addCleanup(patch.stop)

    def name_for(self, **over):
        return dashboard._create_ui_inputs(CO, base.row(**over), OWNER).case3_tenant_name

    def test_a_short_account_name_is_the_tenant_name_with_the_trailing_period_removed(self):
        self.assertEqual(self.name_for(Account_Name__c="Example Klinik gGmbH."), "Example Klinik gGmbH")

    def test_a_long_name_needs_the_owner_confirmation(self):
        full = "Barmherzige Brüder gemeinnützige Träger GmbH"
        self.assertIsNone(self.name_for(Account_Name__c=full))
        tnd.confirm(self.names, CO, full, "Barmherzige Brüder gemeinnützige TG", OWNER, NOW)
        self.assertEqual(self.name_for(Account_Name__c=full), "Barmherzige Brüder gemeinnützige TG")

    def test_an_unreadable_name_file_or_account_name_gives_no_name(self):
        full = "Barmherzige Brüder gemeinnützige Träger GmbH"
        self.names.write_text("{broken", encoding="utf-8")
        self.assertIsNone(self.name_for(Account_Name__c=full))
        self.assertIsNone(self.name_for(Account_Name__c=None))

    def test_a_ce_only_row_carries_no_case_3_name(self):
        self.assertIsNone(self.name_for(Onboarding_Product__c="Credential Exposure"))


class FollowupPostTests(Fixture):
    def setUp(self):
        super().setUp()
        self.unlocked()
        self.record(outcome="readback_verified", account_id=ACCOUNT_ID, account_uuid=UUID, tenant_name=NAME,
                    domains=["example-klinik.example"])
        self.org_row = {"Id": "a0B5g00000AbCdEFGH", "Name": CO, "LastModifiedDate": REV,
                        "Onboarding_Product__c": "Surface & Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
                        "Onboarding_Stage__c": "Request Approved", "Surface_Account_ID__c": None, "Account_UUID__c": None}
        self.argvs = []

    def handler(self):
        sent = []
        handler = object.__new__(dashboard.Handler)
        handler.send_page = lambda status, page: sent.append(("page", int(status), page))
        handler.send_redirect = lambda target: sent.append(("redirect", target))
        handler.request_operator = lambda: OWNER
        return handler, sent

    def run_argv(self, argv):
        self.argvs.append(argv)
        values = argv[argv.index("--values") + 1]
        if values.startswith("Onboarding_Stage__c"):
            self.org_row["Onboarding_Stage__c"] = values.split("=", 1)[1].strip("'")
        else:
            for pair in values.split(" "):
                name, value = pair.split("=", 1)
                self.org_row[name] = value
        self.org_row["LastModifiedDate"] = "2026-10-22T12:30:00.000+0000"
        return True

    def post(self, path, values, **over):
        handler, sent = self.handler()
        with mock.patch.object(dashboard, "detail_row", return_value=base.row()), \
                mock.patch.object(dashboard, "_create_ui_inputs", lambda ref, row, operator=None: self.inputs(**over)), \
                mock.patch.object(dashboard, "_read_case3_followup_row", lambda ref: dict(self.org_row)), \
                mock.patch.object(dashboard, "_run_sf_argv", self.run_argv):
            handler._post_production_create(path, CO, values)
        return sent

    def review(self):
        sent = self.post(create_ui.FOLLOWUP_REVIEW_PATH, {"reference": [CO]})
        self.assertEqual(sent[0][:2], ("page", 200), sent)
        return sent[0][2]

    def test_the_review_page_shows_field_names_and_tails_only_and_writes_nothing(self):
        html = self.review()
        for fragment in ("Surface_Account_ID__c", "Account_UUID__c", ACCOUNT_ID[-4:], UUID[-4:], "Request Approved",
                         "Account Scanning", create_ui.FOLLOWUP_CONFIRM_PATH, "name='confirm_reference'",
                         "name='case3_followup_authorized'", "name='nonce'"):
            self.assertIn(fragment, html)
        self.assertNotIn(ACCOUNT_ID, html)
        self.assertNotIn(UUID, html)
        self.assertEqual(self.argvs, [])

    def confirm_values(self, html, **over):
        data = {"reference": [CO], "nonce": [re.search(r"name='nonce' value='([^']+)'", html).group(1)],
                "confirm_reference": [CO], "case3_followup_authorized": ["1"]}
        data.update(over)
        return data

    def test_the_confirmation_writes_the_ids_then_the_stage_and_goes_back_to_the_co_page(self):
        html = self.review()
        sent = self.post(create_ui.FOLLOWUP_CONFIRM_PATH, self.confirm_values(html))
        self.assertEqual(sent, [("redirect", "/co/" + CO + "?env=prod")])
        self.assertEqual(len(self.argvs), 2)
        self.assertEqual(self.org_row["Onboarding_Stage__c"], "Account Scanning")
        self.assertEqual((self.org_row["Surface_Account_ID__c"], self.org_row["Account_UUID__c"]), (ACCOUNT_ID, UUID))

    def test_a_missing_confirmation_or_a_second_use_of_the_token_writes_nothing(self):
        html = self.review()
        sent = self.post(create_ui.FOLLOWUP_CONFIRM_PATH, self.confirm_values(html, confirm_reference=["CO-0001"]))
        self.assertEqual(sent[0][:2], ("page", 409))
        sent = self.post(create_ui.FOLLOWUP_CONFIRM_PATH, self.confirm_values(self.review()))
        self.assertEqual(sent, [("redirect", "/co/" + CO + "?env=prod")])
        n = len(self.argvs)
        sent = self.post(create_ui.FOLLOWUP_CONFIRM_PATH, self.confirm_values(html))
        self.assertEqual(len(self.argvs), n)

    def test_the_locks_and_the_vm_refuse_both_steps_and_write_nothing(self):
        for over in ({"vm": True},):
            for path, values in ((create_ui.FOLLOWUP_REVIEW_PATH, {"reference": [CO]}),
                                 (create_ui.FOLLOWUP_CONFIRM_PATH, {"reference": [CO], "nonce": ["x"], "confirm_reference": [CO],
                                                                    "case3_followup_authorized": ["1"]})):
                sent = self.post(path, values, **over)
                self.assertEqual(sent[0][:2], ("page", 409), (path, over))
        self.assertEqual(self.argvs, [])

    def test_a_locked_build_or_the_vm_reads_nothing_from_salesforce(self):
        reads = []
        handler, sent = self.handler()
        with mock.patch.object(dashboard, "detail_row", return_value=base.row()),                 mock.patch.object(dashboard, "_create_ui_inputs", lambda ref, row, operator=None: self.inputs(vm=True)),                 mock.patch.object(dashboard, "_read_case3_followup_row", lambda ref: reads.append(ref) or dict(self.org_row)):
            handler._post_production_create(create_ui.FOLLOWUP_REVIEW_PATH, CO, {"reference": [CO]})
        self.assertEqual((sent[0][:2], reads), (("page", 409), []))

    def test_a_ce_only_co_is_refused_by_the_follow_up(self):
        self.org_row["Onboarding_Product__c"] = "Credential Exposure"
        sent = self.post(create_ui.FOLLOWUP_REVIEW_PATH, {"reference": [CO]})
        self.assertEqual(sent[0][:2], ("page", 409))
        self.assertIn("Nothing was sent", sent[0][2])

    def test_an_unreadable_salesforce_row_is_refused(self):
        handler, sent = self.handler()
        with mock.patch.object(dashboard, "detail_row", side_effect=dashboard.ReadUnavailable()):
            handler._post_production_create(create_ui.FOLLOWUP_REVIEW_PATH, CO, {"reference": [CO]})
        self.assertEqual(sent[0][:2], ("page", 503))


class RowReadTests(unittest.TestCase):
    def test_the_follow_up_row_read_is_one_fixed_query_with_the_ids_and_the_stage(self):
        queries = []

        def fake(args):
            queries.append(args[args.index("--query") + 1])
            return {"status": 0, "result": {"records": [{"attributes": {}, "Id": "a0B5g00000AbCdEFGH", "Name": CO,
                                                         "Surface_Account_ID__c": None, "Account_UUID__c": None,
                                                         "Onboarding_Stage__c": "Request Approved"}]}}
        with mock.patch.object(dashboard, "sf_json", fake):
            row = dashboard._read_case3_followup_row(CO)
        self.assertEqual(len(queries), 1)
        for field in ("Id", "Name", "LastModifiedDate", "Onboarding_Product__c", "Onboarding_Type__c", "Surface_Account_ID__c",
                      "Account_UUID__c", "Onboarding_Stage__c"):
            self.assertIn(field, queries[0])
        self.assertIn("WHERE Name = '" + CO + "'", queries[0])
        self.assertNotIn("attributes", row)

    def test_a_bad_reference_an_alias_override_or_an_odd_reply_gives_none(self):
        self.assertIsNone(dashboard._read_case3_followup_row("not-a-co"))
        with mock.patch.dict("os.environ", {"SURFACE_SF_TARGET_ORG": "another"}):
            self.assertIsNone(dashboard._read_case3_followup_row(CO))
        with mock.patch.object(dashboard, "sf_json", lambda args: {"status": 0, "result": {"records": []}}):
            self.assertIsNone(dashboard._read_case3_followup_row(CO))


class RegistrationTests(unittest.TestCase):
    def test_the_follow_up_posts_are_registered_and_the_vm_has_no_operator_post(self):
        self.assertIn(create_ui.FOLLOWUP_REVIEW_PATH, dashboard.POST_ROUTES)
        self.assertIn(create_ui.FOLLOWUP_CONFIRM_PATH, dashboard.POST_ROUTES)
        self.assertEqual(dashboard.VM_OPERATOR_POSTS, frozenset())


if __name__ == "__main__":
    unittest.main()
