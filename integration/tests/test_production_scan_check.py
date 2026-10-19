"""Build 3 (owner decision 2026-10-19): the read-only CE scan check of a production tenant, bound to the CO's verified
create and started by the owner from the CO page (SSO + MFA in the runner window).

The runner reads the tenant's OWN row through the page's own table request (the read-only guard installed), matched by
the verified Account UUID, and keeps only scan-related field names with short scalar values (enums, numbers, booleans,
ISO times; never a domain, name, email or UUID). "Finished" is recognised only by a rule confirmed from a live read: until
the first live check of CO-0798 is reviewed the rule set is empty, so every result is "unconfirmed" and no stage moves.
Hermetic: fake pages, temporary folders; nothing is launched or sent.
"""

import json
import unittest
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_scan_check as sc
from integration.onboarding import production_create_ui as ui
from integration.tests import test_production_create_ui as base

CO, UUID, NOW = base.CO, base.UUID, base.NOW
OTHER = "f" * 32


def body(*rows):
    return {"pagination_response": {"table_data": list(rows), "total_count": len(rows)}}


def tenant_row(**over):
    data = {"accountUuid": UUID, "id": "6ad6629ec6675e6765827358", "accountName": "Advanced Technology International - CE Only",
            "accountDomain": "ati.org", "lastReconScan": "2026-10-20T10:00:00Z", "lastScanStatusEnum": "COMPLETED",
            "lastReconScanDurationMilliseconds": 4200, "leakedCredentialsScanningInterval": "WEEKLY",
            "leakedCredentialsScannedDomains": ["ati.org"], "leakedCredentialsLastScan": 1792141200000,
            "leakedCredentialsSettings": {"spyCloudSettings": {"enabled": False}, "lastScanStatus": "DONE"}}
    data.update(over)
    return data


class ReplyTests(unittest.TestCase):
    def test_only_scan_fields_with_safe_values_leave_the_row(self):
        reply = sc.scan_reply(200, body(tenant_row()), UUID)
        self.assertTrue(reply["found"])
        fields = reply["fields"]
        self.assertEqual(fields["lastScanStatusEnum"], "COMPLETED")
        self.assertEqual(fields["lastReconScan"], "2026-10-20T10:00:00Z")
        self.assertEqual(fields["lastReconScanDurationMilliseconds"], 4200)
        self.assertEqual(fields["leakedCredentialsLastScan"], 1792141200000)
        self.assertEqual(fields["leakedCredentialsSettings.lastScanStatus"], "DONE")
        self.assertEqual(fields["leakedCredentialsScannedDomains"], sc.WITHHELD)   # customer domains never leave
        for name in ("accountName", "accountDomain", "accountUuid", "id"):
            self.assertNotIn(name, fields)
        self.assertNotIn("ati.org", json.dumps(reply))
        self.assertNotIn(UUID, json.dumps(reply))

    def test_the_row_must_be_exactly_the_verified_uuid(self):
        self.assertFalse(sc.scan_reply(200, body(tenant_row(accountUuid=OTHER)), UUID)["found"])
        self.assertEqual(sc.scan_reply(200, body(tenant_row(), tenant_row(id="x")), UUID)["state"], "row_ambiguous")
        self.assertEqual(sc.scan_reply(401, None, UUID)["state"], "production_session_expired")
        self.assertEqual(sc.scan_reply(200, {"x": 1}, UUID)["state"], "reply_unexpected")
        self.assertEqual(sc.scan_reply(0, None, UUID)["state"], "read_unavailable")

    def test_finished_is_unconfirmed_until_a_live_read_confirms_the_rule(self):
        self.assertEqual(sc.CONFIRMED_FINISHED_RULES, ())
        self.assertEqual(sc.scan_reply(200, body(tenant_row()), UUID)["state"], "unconfirmed")
        rule = (("lastScanStatusEnum", "COMPLETED"),)
        with mock.patch.object(sc, "CONFIRMED_FINISHED_RULES", (rule,)):
            self.assertEqual(sc.scan_reply(200, body(tenant_row()), UUID)["state"], "scan_completed")
            self.assertEqual(sc.scan_reply(200, body(tenant_row(lastScanStatusEnum="RUNNING")), UUID)["state"],
                             "unconfirmed")

    def test_observations_are_recorded_masked_and_the_latest_is_read_back(self):
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        reply = sc.scan_reply(200, body(tenant_row()), UUID)
        sc.record(Path(tmp.name), CO, reply, NOW)
        sc.record(Path(tmp.name), CO, {"state": "read_unavailable", "found": False, "fields": {}}, NOW + timedelta(minutes=5))
        latest = sc.latest(Path(tmp.name), CO)
        self.assertEqual(latest["state"], "read_unavailable")
        raw = "".join(p.read_text(encoding="utf-8") for p in (Path(tmp.name) / sc.SUBDIR).iterdir())
        self.assertNotIn("ati.org", raw)
        self.assertNotIn(UUID, raw)


class FakePage:
    def __init__(self):
        self.routes = []

    def route(self, pattern, handler):
        self.routes.append(pattern)

    def on(self, *_a):
        pass

    def evaluate(self, _js):
        return {"registrations": 0}


class RunnerTests(base.Base):
    def run_check(self, *, verified=True, table=(200, None), env=None, fail=None):
        if verified:
            self.record("readback_verified", account_uuid=UUID, tenant_name="Advanced Technology International - CE Only")
        page, opened = FakePage(), []

        @contextmanager
        def open_page(target, wait):
            opened.append((target.origin, wait))
            if fail:
                raise fail
            yield page
        reader = SimpleNamespace(table=lambda value, **kw: (table[0], table[1] if table[1] is not None else body(tenant_row())))
        with mock.patch.object(runner, "_pdr_reader", return_value=reader), \
                mock.patch.object(runner, "_launch_os_name", return_value="nt"), \
                mock.patch.object(runner.production_dry_run, "service_worker_verdict", return_value=None):
            line = json.loads(runner.run_production_scan_check(CO, environ=env if env is not None else {}, now=NOW,
                                                               records_parent=self.dir, open_page=open_page))
        return line, opened, page

    def test_a_verified_create_is_checked_read_only_and_recorded(self):
        line, opened, page = self.run_check()
        self.assertEqual(line["result"], "unconfirmed")
        self.assertEqual(line["production_write"], "not_performed")
        self.assertEqual(opened[0][0], "https://app.pentera.io")
        self.assertEqual(page.routes, ["**/*"])   # the read-only guard is installed before the read
        self.assertEqual(sc.latest(self.dir, CO)["state"], "unconfirmed")

    def test_without_a_verified_create_nothing_opens(self):
        line, opened, _page = self.run_check(verified=False)
        self.assertEqual(line["result"], "no_verified_create")
        self.assertEqual(opened, [])

    def test_the_vm_and_unknown_runtimes_never_open_a_browser(self):
        for env in ({"SURFACE_ONBOARDING_RUNTIME": "vm"}, {"SURFACE_ONBOARDING_RUNTIME": "consol"}):
            line, opened, _page = self.run_check(env=env)
            self.assertEqual(line["result"], "browser_launch_not_allowed")
            self.assertEqual(opened, [])

    def test_a_login_timeout_or_crash_is_a_code(self):
        line, _o, _p = self.run_check(fail=RuntimeError("production_dry_run_login_timeout"))
        self.assertEqual(line["result"], "production_sign_in_timeout")
        line, _o, _p = self.run_check(fail=ValueError("Bearer secret"))
        self.assertEqual(line["result"], "scan_check_failed")
        self.assertNotIn("Bearer", json.dumps(line))


class DashboardTests(base.Base):
    def setUp(self):
        super().setUp()
        self.record("readback_verified", account_uuid=UUID)
        ui.record_uuid_write(self.dir, CO, UUID, "written_verified", NOW)
        ui.record_stage_write(self.dir, CO, "Account Scanning", "written_verified", NOW)
        dashboard.PRODUCTION_SCAN_RUNS.clear()
        self.addCleanup(dashboard.PRODUCTION_SCAN_RUNS.clear)

    def test_the_scan_card_offers_the_check_only_after_account_scanning(self):
        html = ui.scan_card_html(self.dir, CO, running=False)
        self.assertIn("action='" + ui.SCAN_CHECK_PATH + "'", html)
        self.assertIn("Check CE scan in BackOffice", html)
        self.assertNotIn(UUID, html)

    def test_no_check_before_the_stage_moved(self):
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(ui.scan_check_problem(Path(tmp.name), CO), "no_verified_create")
        self.assertIsNone(ui.scan_check_problem(self.dir, CO))
        ui.record_stage_write(self.dir, CO, "Scan Completed Successfully", "write_uncertain", NOW)
        self.assertEqual(ui.scan_check_problem(self.dir, CO), "stage_write_needs_review")

    def test_a_stage_moved_by_hand_still_allows_the_check(self):
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from integration.onboarding import production_create_record as pcr
        pcr.record(Path(tmp.name), {"co_reference": CO, "approval_ref": base.REF, "outcome": "readback_verified",
                                    "route": base.ROUTE, "salesforce_revision": base.REV, "account_uuid": UUID}, NOW)
        ui.record_uuid_write(Path(tmp.name), CO, UUID, "written_verified", NOW)
        self.assertIsNone(ui.scan_check_problem(Path(tmp.name), CO))

    def test_the_route_is_registered_and_the_vm_allows_none(self):
        self.assertIn(ui.SCAN_CHECK_PATH, dashboard.POST_ROUTES)
        self.assertEqual(dashboard.VM_OPERATOR_POSTS, frozenset())

    def test_a_finished_check_runs_the_two_stage_moves(self):
        sc.record(self.dir, CO, {"state": "scan_completed", "found": True, "fields": {}}, NOW + timedelta(minutes=1))
        with mock.patch.object(dashboard.create_ui, "after_scan_finished", return_value=["written_verified"] * 2) as moves, \
                mock.patch.object(dashboard, "_create_ui_inputs", return_value=self.inputs()), \
                mock.patch.object(dashboard, "production_records_parent", return_value=self.dir):
            dashboard._follow_scan_check(CO, mock.Mock())
        self.assertEqual(moves.call_args.kwargs["scan_state"], "scan_completed")
        self.assertEqual(dashboard.PRODUCTION_SCAN_RUNS[CO]["stages"], ["written_verified"] * 2)

    def test_an_unconfirmed_check_moves_nothing(self):
        sc.record(self.dir, CO, {"state": "unconfirmed", "found": True, "fields": {}}, NOW + timedelta(minutes=1))
        with mock.patch.object(dashboard.create_ui, "after_scan_finished", return_value=["scan_not_finished"]) as moves, \
                mock.patch.object(dashboard, "_create_ui_inputs", return_value=self.inputs()), \
                mock.patch.object(dashboard, "production_records_parent", return_value=self.dir):
            dashboard._follow_scan_check(CO, mock.Mock())
        self.assertEqual(moves.call_args.kwargs["scan_state"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
