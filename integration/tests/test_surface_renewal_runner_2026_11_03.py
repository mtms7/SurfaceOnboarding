"""Production Surface renewal runner and dashboard wiring (owner go 2026-11-03, CO-0829).

A fake Edit form stands in for BackOffice; Salesforce, the browser and the network are never reached; state files are temporary.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.issue_production_approval as issuer
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_approval
from integration.onboarding import production_create_live as live
from integration.onboarding import production_create_ui as create_ui
from integration.onboarding import production_surface_renewal as renewal
from integration.onboarding import surface_renewal_followup as followup

ACCOUNT_ID, ACCOUNT_UUID = "a" * 24, "b" * 32
EDIT_URL = "https://app.pentera.io/api/v1/backoffice/account/{}/edit"
CO_ROW = {"Name": "CO-0829", "LastModifiedDate": "2026-11-02T21:49:32.000+0000", "Account_Name__c": "Royal Reesink",
          "Main_Domain__c": "royalreesink.com", "Onboarding_Product__c": "Surface",
          "Onboarding_Type__c": "Renewal of Existing Product", "Onboarding_Approval_Status__c": "Approved"}
PLAN = {"engine": "surface_renewal", "blockers": [], "surface_term": {
    "start": "2026-11-01", "end": "2027-10-31", "apply_from": "2026-10-18", "scanning_interval": "Monthly",
    "subdomains": 1000, "domains": 1000, "assets": 10000}}
INTENDED = renewal.intended_from(CO_ROW, PLAN, date(2026, 11, 3))


def row(interval="NO_SCHEDULE", end="2026-10-30T00:00:00Z", **extra):
    return {"id": ACCOUNT_ID, "accountUuid": ACCOUNT_UUID, "accountName": "Royal Reesink", "accountDomain": "royalreesink.com",
            "isDeleted": False, "scanningInterval": interval, "operatorAccounts": [{"operatorName": "Stefan Jansson Operator"}],
            "accountLicense": {"expirationDate": end, "startDate": "2023-11-23T00:00:00Z", "subDomainsNumber": 650}, **extra}


def reply(*rows):
    return {"status": 200, "rows": list(rows)}


class Route:
    def __init__(self, url):
        self.request = SimpleNamespace(method="POST", url=url, post_data=None)
        self.done = None

    def abort(self):
        self.done = "abort"

    def continue_(self):
        self.done = "continue"


class Locator:
    def __init__(self, count=1):
        self._count, self.clicks = count, 0

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_enabled(self):
        return True

    def click(self, **_kw):
        self.clicks += 1


class FakeEditForm:
    """Interval select, start/expiration pickers, one Confirm and the app's edit reply."""

    def __init__(self, guard, *, reply_id=ACCOUNT_ID, status=200, interval="None", end=date(2026, 10, 30), date_sticks=True):
        self.guard, self.reply_id, self.status = guard, reply_id, status
        self.confirm = Locator()
        self.values = {"interval": interval, "license_end": end, "license_start": date(2023, 11, 23)}
        self.date_sticks = date_sticks
        self.sent = []
        self.context = SimpleNamespace(pages=[self])
        self.url = "https://app.pentera.io/backoffice/tenantManagement"

    def get_by_role(self, *_a, **_kw):
        return self.confirm

    def locator(self, _selector):
        return Locator()

    def wait_for_timeout(self, _ms):
        pass

    def evaluate(self, _js):
        return {"count": 0}

    @contextlib.contextmanager
    def expect_response(self, predicate, timeout=0):
        info = SimpleNamespace()
        yield info
        route = Route(EDIT_URL.format(self.reply_id))
        self.guard.handle(route)
        self.sent.append(route.done)
        response = SimpleNamespace(request=route.request, url=route.request.url, status=self.status)
        if route.done != "continue" or not predicate(response):
            raise TimeoutError("no reply")
        info.value = response


class EditTests(unittest.TestCase):
    def setUp(self):
        self.guard = live.RenewalEditGuard()
        self.replies = [reply(row()), reply(row("MONTHLY", "2027-10-31T00:00:00Z"))]
        self.recorded, self.consumed, self.cancelled = [], [], 0

        def cancel(_page):
            self.cancelled += 1
            return True

        def select(page, _name, option):
            page.values["interval"] = option
            return None, option

        reader = SimpleNamespace(readback=lambda _name: self.replies.pop(0) if len(self.replies) > 1 else self.replies[0])
        for patch in (mock.patch.object(runner, "_pdr_reader", lambda *_a, **_k: reader),
                      mock.patch.object(runner, "_open_row_action", lambda *_a: None),
                      mock.patch.object(runner, "_spycloud_cancel", cancel),
                      mock.patch.object(runner, "_pdr_read_selected", lambda page, _n: page.values["interval"]),
                      mock.patch.object(runner, "_pdr_select_by_name", select),
                      mock.patch.object(runner, "_license_date_value", lambda page, key: page.values[key]),
                      mock.patch.object(runner, "_confirm_enabled", lambda *_a: True)):
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def fill(page, key, value):
        if page.date_sticks:
            page.values[key] = value
        return True

    @staticmethod
    def kept(page, key, value):
        return page.values[key] == value

    def run_edit(self, page):
        def consume():
            self.consumed.append(1)
            return None
        return runner._psr_edit(page, self.guard, INTENDED, self.recorded.append, consume, self.fill, self.kept)

    def test_sets_both_values_confirms_once_and_verifies(self):
        page = FakeEditForm(self.guard)
        result, ids = self.run_edit(page)
        self.assertEqual(result, renewal.VERIFIED)
        self.assertEqual(ids, {"account_id": ACCOUNT_ID, "account_uuid": ACCOUNT_UUID})
        self.assertEqual(page.values["interval"], "Monthly")
        self.assertEqual(page.values["license_end"], date(2027, 10, 31))
        self.assertEqual(page.values["license_start"], date(2023, 11, 23))
        self.assertEqual((page.confirm.clicks, page.sent, self.recorded, self.consumed), (1, ["continue"], ["write_started"], [1]))
        self.assertFalse(self.guard.edit_armed)

    def test_already_current_opens_nothing(self):
        self.replies = [reply(row("MONTHLY", "2027-10-31T00:00:00Z"))]
        page = FakeEditForm(self.guard)
        self.assertEqual(self.run_edit(page)[0], renewal.ALREADY_CURRENT)
        self.assertEqual((page.confirm.clicks, self.consumed), (0, []))

    def test_a_form_that_is_not_the_row_cancels(self):
        page = FakeEditForm(self.guard, interval="Weekly")
        self.assertEqual(self.run_edit(page), ("renewal_form_mismatch", None))
        self.replies = [reply(row())]
        page = FakeEditForm(self.guard, end=date(2025, 1, 1))
        self.assertEqual(self.run_edit(page), ("renewal_form_mismatch", None))
        self.assertEqual((self.cancelled, self.consumed, self.recorded), (2, [], []))

    def test_a_date_that_does_not_stick_cancels_before_confirm(self):
        page = FakeEditForm(self.guard, date_sticks=False)
        self.assertEqual(self.run_edit(page), ("renewal_value_mismatch", None))
        self.assertEqual((page.confirm.clicks, self.consumed), (0, []))

    def test_a_used_approval_stops_before_confirm(self):
        page = FakeEditForm(self.guard)
        result = runner._psr_edit(page, self.guard, INTENDED, self.recorded.append, lambda: "approval_consumed",
                                  self.fill, self.kept)
        self.assertEqual(result, ("approval_consumed", None))
        self.assertEqual(page.confirm.clicks, 0)

    def test_an_edit_for_another_tenant_is_blocked(self):
        page = FakeEditForm(self.guard, reply_id="c" * 24)
        self.assertEqual(self.run_edit(page), ("renewal_save_id_mismatch", None))
        self.assertEqual(page.sent, ["abort"])

    def test_a_failed_save_and_values_not_kept(self):
        self.assertEqual(self.run_edit(FakeEditForm(self.guard, status=500))[0], "renewal_save_failed")
        self.guard = live.RenewalEditGuard()
        self.replies = [reply(row()), reply(row("MONTHLY"))]
        self.assertEqual(self.run_edit(FakeEditForm(self.guard))[0], "renewal_values_not_kept")

    def test_an_ambiguous_tenant_opens_nothing(self):
        self.replies = [reply(row(), dict(row(), id="c" * 24, accountUuid="d" * 32))]
        page = FakeEditForm(self.guard)
        self.assertEqual(self.run_edit(page), ("renewal_tenant_ambiguous", None))
        self.assertEqual(self.cancelled, 0)


class RunTests(unittest.TestCase):
    """The orchestration: gates before the browser, the result line, the follow-up only after a verified edit."""

    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.parent, True)
        self.approvals = self.parent / "approvals.json"
        now = datetime.now(timezone.utc)
        production_approval.issue(self.approvals, "PROD-SR-0829-20261103", "CO-0829", production_approval.SURFACE_RENEWAL_ROUTE,
                                  CO_ROW["LastModifiedDate"], "owner@pentera.io", frozenset({"owner@pentera.io"}), now)
        self.environ = {"SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": "PROD-SR-0829-20261103"}
        self.opened = []
        for patch in (mock.patch.object(runner, "production_target", lambda *_a: "target"),
                      mock.patch.object(runner.launch_ticket if hasattr(runner, "launch_ticket") else
                                        __import__("integration.onboarding.production_launch_ticket", fromlist=["x"]),
                                        "consume", lambda *_a: ("owner@pentera.io", "")),
                      mock.patch.object(runner, "_pcr_org_problem", lambda *_a: None)):
            patch.start()
            self.addCleanup(patch.stop)

    @contextlib.contextmanager
    def open_page(self, *_a):
        page = SimpleNamespace(evaluate=lambda _js: {"count": 0}, route=lambda *_a: None, on=lambda *_a: None, context=None)
        self.opened.append(page)
        yield page

    def run_it(self, **kw):
        args = dict(owner_go=True, environ=self.environ, records_parent=self.parent, approval_path=self.approvals,
                    load_intended=lambda *_a: (INTENDED, CO_ROW), open_page=self.open_page, ticket_parent=self.parent,
                    read_row=lambda: None, run_argv=lambda _argv: True)
        args.update(kw)
        return json.loads(runner.run_production_surface_renewal("CO-0829", CO_ROW["LastModifiedDate"], **args))

    def test_verified_edit_runs_the_followup(self):
        with mock.patch.object(runner, "_psr_edit", return_value=(renewal.VERIFIED, {"account_id": ACCOUNT_ID,
                                                                                    "account_uuid": ACCOUNT_UUID})), \
                mock.patch.object(followup, "after_verified_edit", return_value={"ids": "written_verified",
                                                                                 "stage": "written_verified"}) as follow:
            line = self.run_it()
        self.assertEqual(line, {"result": renewal.VERIFIED, "salesforce_writeback": "written_verified", "stage": "written_verified"})
        self.assertEqual(follow.call_args.args[2], {"account_id": ACCOUNT_ID, "account_uuid": ACCOUNT_UUID})
        self.assertEqual(followup.last(self.parent, "CO-0829", renewal.STEP), renewal.VERIFIED)

    def test_a_stop_writes_nothing_to_salesforce(self):
        with mock.patch.object(runner, "_psr_edit", return_value=("renewal_form_mismatch", None)), \
                mock.patch.object(followup, "after_verified_edit") as follow:
            self.assertEqual(self.run_it()["result"], "renewal_form_mismatch")
        follow.assert_not_called()

    def test_gates_before_the_browser(self):
        cases = [
            ({"owner_go": False}, "owner_go_missing"),
            ({"load_intended": lambda *_a: (_ for _ in ()).throw(renewal.RenewalRefused("renewal_not_yet_applicable"))},
             "renewal_not_yet_applicable"),
            ({"load_intended": lambda *_a: (renewal.intended_from(dict(CO_ROW, LastModifiedDate="2026-11-03T00:00:00.000+0000"),
                                                                  PLAN, date(2026, 11, 3)), CO_ROW)}, "approval_revision_drift"),
        ]
        for kw, code in cases:
            with self.subTest(code=code), mock.patch.object(runner, "_psr_edit") as edit:
                self.assertEqual(self.run_it(**kw)["result"], code)
                edit.assert_not_called()
        self.assertEqual(self.opened, [])

    def test_an_earlier_confirm_blocks_a_second_run(self):
        followup.record(self.parent, "CO-0829", renewal.STEP, "write_started", datetime.now(timezone.utc))
        with mock.patch.object(runner, "_psr_edit") as edit:
            self.assertEqual(self.run_it()["result"], "renewal_already_attempted")
        edit.assert_not_called()

    def test_an_approval_for_another_route_is_refused(self):
        self.approvals.unlink()
        production_approval.issue(self.approvals, "PROD-SR-0829-20261103", "CO-0829", production_approval.ALLOWED_ROUTE,
                                  CO_ROW["LastModifiedDate"], "owner@pentera.io", frozenset({"owner@pentera.io"}),
                                  datetime.now(timezone.utc))
        with mock.patch.object(runner, "_psr_edit") as edit:
            self.assertEqual(self.run_it()["result"], "approval_route_mismatch")
        edit.assert_not_called()


class WiringTests(unittest.TestCase):
    def test_the_approval_tool_knows_the_route(self):
        approved = dict(CO_ROW, Surface_Account_ID__c=None, Account_UUID__c=None)
        self.assertEqual(issuer.route_for_row(approved), production_approval.SURFACE_RENEWAL_ROUTE)
        self.assertIsNone(issuer.eligibility_problem(approved, "CO-0829"))
        self.assertEqual(issuer.eligibility_problem(dict(approved, Main_Domain__c=None), "CO-0829"), "main_domain_missing")
        self.assertTrue(issuer.default_approval_ref("CO-0829", datetime(2026, 11, 3, tzinfo=timezone.utc),
                                                    production_approval.SURFACE_RENEWAL_ROUTE).startswith("PROD-SR-0829-"))
        self.assertEqual(create_ui._row_route(CO_ROW), production_approval.SURFACE_RENEWAL_ROUTE)
        self.assertIsNone(create_ui._row_route(dict(CO_ROW, Onboarding_Product__c="Credential Exposure")))

    def test_the_dashboard_launches_its_own_run(self):
        with mock.patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                mock.patch.object(dashboard.subprocess, "Popen") as popen, \
                mock.patch.object(dashboard, "runner_log_dir", return_value=None):
            dashboard._launch_production_create("CO-0829", "rev", {}, route=production_approval.SURFACE_RENEWAL_ROUTE)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[2:], ["--production-surface-renewal", "--co", "CO-0829", "--revision", "rev", "--owner-go"])

    def test_the_runner_cli_takes_the_flag_only_alone(self):
        with mock.patch.object(runner, "run_production_surface_renewal", return_value="{}") as run, \
                mock.patch("sys.argv", ["x", "--production-surface-renewal", "--co", "CO-0829", "--revision", "r", "--owner-go"]):
            self.assertEqual(runner.main(), 0)
        run.assert_called_once_with("CO-0829", "r", owner_go=True)
        with mock.patch("sys.argv", ["x", "--production-surface-renewal", "--co", "CO-0829", "--revision", "r"]), \
                self.assertRaises(SystemExit):
            runner.main()

    def test_progress_from_recorded_lines(self):
        parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, parent, True)
        since = "2026-11-03T00:00:00Z"
        self.assertEqual(followup.progress(parent, "CO-0829", since, running=True)["steps"][0][1], "active")
        self.assertEqual(followup.progress(parent, "CO-0829", since, running=False, refusal="approval_missing")["failed"],
                         "approval_missing")
        moment = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)
        followup.record(parent, "CO-0829", renewal.STEP, renewal.VERIFIED, moment)
        followup.record(parent, "CO-0829", "ids", "written_verified", moment)
        followup.record(parent, "CO-0829", "stage", "written_verified", moment)
        done = followup.progress(parent, "CO-0829", since, running=False)
        self.assertTrue(done["done"])
        self.assertIsNone(done["failed"])


if __name__ == "__main__":
    unittest.main()


class QueueLinkTests(unittest.TestCase):
    """Owner report 2026-11-03: CO-0829 was listed without a link; a Surface renewal links its production CO page."""

    def render(self, product="Surface"):
        parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, parent, True)
        row = dict(CO_ROW, Onboarding_Product__c=product, **{"Account__r.Name": "Royal Reesink"},
                   Onboarding_Stage__c="Request Approved", Submission_Date__c="2026-11-02")
        check = {"css": "chip-ok", "label": "In production", "reason": "", "blocked": False, "code": "tenant_found"}
        with mock.patch.object(dashboard, "queue_rows", return_value=[row]), \
                mock.patch.object(dashboard, "queue_start_dates", return_value={"CO-0829": "2026-11-01"}), \
                mock.patch.object(dashboard, "prefetch_display_read"), \
                mock.patch.object(dashboard, "QUEUE_MATCH_NOWAIT", False), \
                mock.patch.object(dashboard, "_match_of", return_value=None), \
                mock.patch.object(dashboard.production_views, "production_check", return_value=check), \
                mock.patch.object(dashboard, "_clone_payload_for_job", return_value=None), \
                mock.patch.object(dashboard, "_clone_strip", return_value=""), \
                mock.patch.object(dashboard, "production_records_parent", return_value=parent), \
                mock.patch.object(dashboard, "approvals_read_path", return_value=parent / "approvals.json"):
            return dashboard.render_onboard_queue()

    def test_a_surface_renewal_links_the_production_co_page(self):
        page = self.render()
        self.assertIn("href='/co/CO-0829?env=prod'", page)
        self.assertIn("Surface renewal · one Edit Account", page)

    def test_a_ce_renewal_stays_unlinked(self):
        page = self.render("Credential Exposure")
        self.assertNotIn("href='/co/CO-0829", page)
        self.assertIn("No automated route", page)


class RenewalCoverageTests(unittest.TestCase):
    """Owner request 2026-11-03: CO-0834 showed "overdue 1022d" although its tenant already runs to the DealHub end."""

    @staticmethod
    def match(end, live=True, status="tenant_found", others=0):
        tenant = {"id": "x", "name": "Respec", "live_paid": live, "license_end": end, "scanning_interval": "MONTHLY"}
        return {"status": status, "candidates": [tenant] + [{"live_paid": False, "license_end": "2020-01-01"}] * others}

    def test_rule(self):
        from integration.onboarding import production_views as views
        self.assertEqual(views.renewal_coverage(self.match("2027-01-14"), "2027-01-15")["state"], "renewed")  # 22:00 UTC
        self.assertEqual(views.renewal_coverage(self.match("2027-02-01", others=6), "2027-01-15")["state"], "renewed")
        self.assertEqual(views.renewal_coverage(self.match("2026-10-30"), "2027-10-31")["state"], "due")
        self.assertIsNone(views.renewal_coverage(self.match("2027-01-14", live=False), "2027-01-15"))
        self.assertIsNone(views.renewal_coverage(self.match("2027-01-14"), None))
        self.assertIsNone(views.renewal_coverage(self.match(None), "2027-01-15"))
        self.assertIsNone(views.renewal_coverage(self.match("2027-01-14", status="tenant_missing"), "2027-01-15"))

    def test_the_match_summary_carries_the_licence_end(self):
        from integration.onboarding import production_match as pm
        summary = pm._summary({"id": "x", "license": {"expiration_date": 1799964000000}, "scanning_interval": "MONTHLY"})
        self.assertEqual(summary["license_end"], "2027-01-15")
        self.assertEqual(summary["scanning_interval"], "MONTHLY")

    def test_queue_row_of_an_already_renewed_tenant_is_done(self):
        parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, parent, True)
        row = {"Name": "CO-0834", "Onboarding_Product__c": "Surface & Credential Exposure",
               "Onboarding_Type__c": "Renewal of Existing Product", "Onboarding_Approval_Status__c": "Approved",
               "Onboarding_Stage__c": "Request Approved", "Account__r.Name": "Respec Inc", "Submission_Date__c": "2026-11-03"}

        def starts(_refs):
            dashboard.QUEUE_END_DATES["CO-0834"] = "2027-01-15"
            return {"CO-0834": "2024-01-16"}

        with mock.patch.object(dashboard, "queue_rows", return_value=[row]), \
                mock.patch.object(dashboard, "queue_start_dates", side_effect=starts), \
                mock.patch.object(dashboard, "prefetch_display_read"), \
                mock.patch.object(dashboard, "QUEUE_MATCH_NOWAIT", False), \
                mock.patch.object(dashboard, "_match_of", return_value=self.match("2027-01-15")), \
                mock.patch.object(dashboard, "_clone_payload_for_job", return_value=None), \
                mock.patch.object(dashboard, "_clone_strip", return_value=""), \
                mock.patch.object(dashboard, "production_records_parent", return_value=parent), \
                mock.patch.object(dashboard, "approvals_read_path", return_value=parent / "approvals.json"):
            page = dashboard.render_onboard_queue()
        self.assertNotIn("overdue", page)
        self.assertIn("Renewed in production: licence until 2027-01-15 (DealHub end 2027-01-15); scanning Monthly.", page)
