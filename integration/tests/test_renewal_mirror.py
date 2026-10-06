"""Renewal mirror (owner decision 2026-10-06): the pure plan, the runner flow, the CLI.

No network, no browser, no Salesforce: every page is a fake, every file a temporary one.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import renewal_mirror as mirror
from integration.tests.test_leonardo_inventory import page as inventory_page, tenant_row

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
ID = "a" * 24
UUID = "b" * 32
LICENSE = {"licenseType": "prepaid annual subscription", "assetsNumber": 10000, "domainsNumber": 8,
           "subDomainsNumber": 1000, "startDate": 1758000000000, "expirationDate": 1790000000000,
           "scanningFrequency": "Weekly", "leakedCredentialsAllowed": True, "enabled": True,
           "leakedCredentialsScannedDomainsNumber": 1}


def prod_row(index=1, license_=None, **overrides):
    row = tenant_row(index, accountName="Acme Corp", accountDomain="acme.example",
                     alternateDomains=["acme.org"], scanningInterval="WEEKLY",
                     accountLicense={**tenant_row(index)["accountLicense"], **LICENSE, **(license_ or {})})
    row.update(overrides)
    return row


def payload_of(*rows):
    assembled = inventory.assemble_pages([inventory_page(0, list(rows), len(rows), size=len(rows))],
                                         page_size=len(rows))
    return inventory.snapshot_payload(inventory.ENVIRONMENTS["prod-clone"], assembled, NOW)


def plan_of(*rows, names=("Acme Corp",), domains=("acme.example",), emails=("acme.example",), today=TODAY):
    return mirror.plan_from_payload(payload_of(*rows), names, domains, emails, today)


class FindTenantTests(unittest.TestCase):
    def test_one_match_by_name_or_domain_and_the_same_row_twice_is_one(self):
        self.assertEqual(plan_of(prod_row(), tenant_row(2)).prod_id, "TENANTID00000001")
        self.assertEqual(plan_of(prod_row(), names=("Other",)).tenant_name, "Acme Corp")  # domain only
        self.assertEqual(plan_of(prod_row(), domains=("nope.example",)).tenant_name, "Acme Corp")  # name only

    def test_no_match_is_not_found_and_deleted_rows_never_count(self):
        with self.assertRaises(mirror.MirrorError) as caught:
            plan_of(tenant_row(2))
        self.assertEqual(caught.exception.reason, "mirror_prod_tenant_not_found")
        with self.assertRaises(mirror.MirrorError) as caught:
            plan_of(prod_row(isDeleted=True))
        self.assertEqual(caught.exception.reason, "mirror_prod_tenant_not_found")

    def test_two_distinct_tenants_are_ambiguous(self):
        other = prod_row(2, accountName="Other Co", accountDomain="acme.example")
        with self.assertRaises(mirror.MirrorError) as caught:
            plan_of(prod_row(), other)
        self.assertEqual(caught.exception.reason, "mirror_prod_tenant_ambiguous")
        by_name = prod_row(2, accountName="acme  corp", accountDomain="elsewhere.example")  # name key: case/space
        with self.assertRaises(mirror.MirrorError):
            plan_of(prod_row(), by_name)

    def test_a_co_without_any_identity_fails_closed(self):
        with self.assertRaises(mirror.MirrorError) as caught:
            plan_of(prod_row(), names=(), domains=())
        self.assertEqual(caught.exception.reason, "mirror_co_identity_unavailable")


class PlanTests(unittest.TestCase):
    def test_production_values_map_to_the_add_account_plan(self):
        plan = plan_of(prod_row())
        self.assertEqual((plan.tenant_name, plan.primary_domain, plan.alternate_domains),
                         ("Acme Corp", "acme.example", ("acme.org",)))
        self.assertEqual((plan.license_type, plan.scanning_interval, plan.scanning_frequency),
                         ("Prepaid annual subscription", "Weekly", "Weekly"))
        self.assertEqual((plan.assets, plan.domains, plan.subdomains), (10000, 8, 1000))
        self.assertEqual((plan.leaked_credentials_allowed, plan.lc_domains_count, plan.lc_domains),
                         (True, 1, ("acme.example",)))

    def test_start_is_today_and_expiration_is_production_or_one_year_less_a_day(self):
        future = int(datetime(2027, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
        plan = plan_of(prod_row(license_={"expirationDate": future}))
        self.assertEqual((plan.start_date, plan.end_date, plan.production_expiration),
                         (TODAY, date(2027, 3, 1), date(2027, 3, 1)))
        past = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(plan_of(prod_row(license_={"expirationDate": past})).end_date, date(2027, 10, 5))
        same_day = int(datetime(2026, 10, 6, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(plan_of(prod_row(license_={"expirationDate": same_day})).end_date, date(2027, 10, 5))
        self.assertEqual(plan_of(prod_row(license_={"expirationDate": None})).end_date, date(2027, 10, 5))
        self.assertEqual(mirror.mirror_dates(None, date(2028, 2, 29)), (date(2028, 2, 29), date(2029, 2, 27)))

    def test_unmapped_or_incomplete_values_fail_closed_with_a_code(self):
        cases = [({"licenseType": "Enterprise"}, "mirror_license_type_unmapped"),
                 ({"assetsNumber": None}, "mirror_prod_license_incomplete"),
                 ({"subDomainsNumber": -1}, "mirror_prod_license_incomplete"),
                 ({"leakedCredentialsAllowed": None}, "mirror_prod_license_incomplete")]
        for override, reason in cases:
            with self.subTest(reason=reason, override=override), self.assertRaises(mirror.MirrorError) as caught:
                plan_of(prod_row(license_=override))
            self.assertEqual(caught.exception.reason, reason)
        with self.assertRaises(mirror.MirrorError) as caught:
            plan_of(prod_row(scanningInterval="HOURLY"))
        self.assertEqual(caught.exception.reason, "mirror_interval_unmapped")

    def test_lc_needs_a_co_email_domain_and_is_capped_by_the_count(self):
        with self.assertRaises(mirror.MirrorError) as caught:
            plan_of(prod_row(), emails=())
        self.assertEqual(caught.exception.reason, "mirror_lc_domains_unavailable")
        plan = plan_of(prod_row(license_={"leakedCredentialsScannedDomainsNumber": 2}),
                       emails=("a.example", "b.example", "c.example"))
        self.assertEqual(plan.lc_domains, ("a.example", "b.example"))
        off = plan_of(prod_row(license_={"leakedCredentialsAllowed": False}), emails=())
        self.assertEqual((off.leaked_credentials_allowed, off.lc_domains), (False, ()))


class PlanMirrorFileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, hours_old=0, *rows):
        assembled = inventory.assemble_pages([inventory_page(0, list(rows), len(rows), size=len(rows))],
                                             page_size=len(rows))
        inventory.write_snapshot(inventory.snapshot_payload(
            inventory.ENVIRONMENTS["prod-clone"], assembled, NOW - timedelta(hours=hours_old)), self.root)

    def test_fresh_clone_plans_and_missing_or_stale_clone_is_unavailable(self):
        with self.assertRaises(mirror.MirrorError) as caught:
            mirror.plan_mirror(self.root, ("Acme Corp",), (), (), now=NOW)
        self.assertEqual(caught.exception.reason, "mirror_clone_unavailable")
        self.write(7, prod_row())  # older than the 6 h freshness rule
        with self.assertRaises(mirror.MirrorError) as caught:
            mirror.plan_mirror(self.root, ("Acme Corp",), (), ("acme.example",), now=NOW)
        self.assertEqual(caught.exception.reason, "mirror_clone_unavailable")
        self.write(1, prod_row())
        plan = mirror.plan_mirror(self.root, ("Acme Corp",), (), ("acme.example",), now=NOW)
        self.assertEqual((plan.start_date, plan.tenant_name), (NOW.date(), "Acme Corp"))

    def test_a_clone_without_alternate_domain_lists_is_unavailable(self):
        self.write(0, prod_row(alternateDomains=None))
        with self.assertRaises(mirror.MirrorError) as caught:
            mirror.plan_mirror(self.root, ("Acme Corp",), (), ("acme.example",), now=NOW)
        self.assertEqual(caught.exception.reason, "mirror_clone_unavailable")


class PureModuleBoundaryTests(unittest.TestCase):
    def test_the_module_has_no_transport_or_process_imports(self):
        tree = ast.parse(Path(mirror.__file__).read_text(encoding="utf-8"))
        roots = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for alias in node.names}
        roots |= {node.module.split(".")[0] for node in ast.walk(tree)
                  if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse(roots & {"subprocess", "socket", "urllib", "http", "requests", "httpx", "playwright"})


# ---- runner -----------------------------------------------------------------------------------------------

def source(**kw):
    values = dict(reference="CO-0900", account_name="Acme Corp", country="France", tenant_name="Acme Corp",
                  primary_domain="acme.example", candidate_names=("Acme Corp", "Acme Corp - CE Only"),
                  candidate_domains=("acme.example",), email_domains=("acme.example",),
                  primary_user_alias="acmecorp", renews_surface=True, renews_ce=True)
    values.update(kw)
    return runner.MirrorSource(**values)


class BuildFillTests(unittest.TestCase):
    def test_standard_profile_with_production_values(self):
        expiry = int(datetime(2027, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
        fill = runner.build_mirror_fill(plan_of(prod_row(license_={"expirationDate": expiry})), source())
        self.assertEqual(fill["texts"]["Company name"], "Acme Corp")
        self.assertEqual(fill["texts"][runner.ALTERNATE_DOMAINS_LABEL], "acme.org")
        self.assertEqual((fill["texts"]["Number of assets"], fill["texts"]["Number of domains"],
                          fill["texts"]["Number of subdomains"]), ("10000", "8", "1000"))
        self.assertEqual(fill["selects"], {"Account Type": "Customer", "Country": "France",
                                           "Scanning interval": "Weekly", "Type": "Prepaid annual subscription",
                                           "Leaked Credentials scanning interval": "Weekly"})
        self.assertTrue(fill["checkboxes"]["leakedCredentialsAllowed"])
        self.assertTrue(fill["checkboxes"]["subDomainsReconEnabled"])  # SURFACE_ADVANCED_TOGGLES
        self.assertEqual(fill["absent_checkboxes"], ("scan_now",))  # a schedule removes "Scan now"
        self.assertTrue(fill["operator_account_empty"])
        self.assertEqual((fill["license_start"], fill["license_end"]), (TODAY, date(2027, 3, 1)))

    def test_interval_none_leaves_scan_now_off_and_no_alternates_stay_blank(self):
        fill = runner.build_mirror_fill(plan_of(prod_row(scanningInterval="NONE", alternateDomains=[],
                                                          license_={"leakedCredentialsAllowed": False})),
                                        source(renews_surface=False))
        self.assertIs(fill["checkboxes"]["scan_now"], False)
        self.assertEqual(fill["absent_checkboxes"], ())
        self.assertIn(runner.ALTERNATE_DOMAINS_LABEL, fill["blank_texts"])
        self.assertNotIn("Leaked Credentials scanning interval", fill["selects"])
        self.assertFalse(fill["checkboxes"]["subDomainsReconEnabled"])


class RecordAndLabelTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        patcher = patch.object(runner, "MIRROR_PATH", directory / "mirrors.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_record_is_marked_as_a_mirror_and_holds_no_names(self):
        runner.write_mirror_record("CO-0900", "PRODID1", "create_attempted", "2026-10-06")
        runner.write_mirror_record("CO-0900", "PRODID1", "verified", "2026-10-06", ID, UUID)
        record = json.loads(runner.MIRROR_PATH.read_text(encoding="utf-8"))["CO-0900"]
        self.assertEqual(record, {"mirror_of_production": True, "source_prod_id": "PRODID1",
                                  "created_on": "2026-10-06", "status": "verified", "environment": "dev",
                                  "surface_account_id": ID, "account_uuid": UUID})
        with self.assertRaises(ValueError):
            runner.write_mirror_record("bad", "P", "verified", "2026-10-06")
        with self.assertRaises(ValueError):
            runner.write_mirror_record("CO-0900", "P", "other", "2026-10-06")

    def test_label_never_claims_a_write_for_a_run_that_stopped_before_confirm(self):
        self.assertEqual(runner.mirror_write_label("mirror_created_verified", True), "verified")
        self.assertEqual(runner.mirror_write_label("mirror_create_unverified", True), "attempted_unverified")
        self.assertEqual(runner.mirror_write_label("mirror_create_unverified", False), "not_performed")
        for code in ("mirror_dry_run_verified", "mirror_duplicate_found", "mirror_prod_tenant_not_found",
                     "confirm_button_not_enabled", "mirror_already_exists"):
            self.assertEqual(runner.mirror_write_label(code, True), "not_performed")


class _Locator:
    def __init__(self, page, name):
        self.page, self.name = page, name

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def click(self, **_kw):
        self.page.clicked.append(self.name)

    def wait_for(self, **_kw):
        pass


class FakePage:
    def __init__(self):
        self.clicked = []
        self.submitted = False

    def get_by_role(self, role, name="", exact=False):
        return _Locator(self, name)

    def get_by_label(self, label, exact=False):
        return _Locator(self, label)

    def wait_for_timeout(self, _ms):
        pass

    def expect_response(self, predicate, timeout=0):
        page = self

        class Manager:
            value = types.SimpleNamespace(status=200)

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_exc):
                page.submitted = True
                return False
        return Manager()


class _Control:
    def count(self):
        return 0  # the Add Account form closed


class RunMirrorTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.root = self.dir / "inventory"
        self.page = FakePage()
        self.after_create = []
        self.fill_plans = []
        self.cancelled = []
        self.search_rows = []
        self.dup_ui = "duplicate_clear"
        self.readback = (ID, UUID, "No scan started")
        self.confirm_enabled = True
        patches = [
            patch.object(runner, "MIRROR_PATH", self.dir / "mirrors.json"),
            patch.object(runner, "READBACK_PATH", self.dir / "readbacks.json"),
            patch.object(runner, "RUN_LOG_PATH", self.dir / "run_log.json"),
            patch.object(runner, "inventory_root", return_value=self.root),
            patch.object(runner, "mirror_source", side_effect=lambda ref: source(reference=ref)),
            patch.object(runner, "_run_day", return_value=TODAY),
            patch.object(runner, "_attended_page", self.attended),
            patch.object(runner, "_open_search", lambda page: object()),
            patch.object(runner, "_search_tenants", self.search),
            patch.object(runner, "_settled_tenant_rows", lambda page, name, domain: "duplicate_found" if self.page.submitted else self.dup_ui),
            patch.object(runner, "_api_duplicate", lambda result, name, domain: "duplicate_clear"),
            patch.object(runner, "_capture_search_diagnostics", lambda page: None),
            patch.object(runner, "_fill_add_account_form", self.fill),
            patch.object(runner, "_locate_confirm_button", lambda page: _Locator(page, "Confirm")),
            patch.object(runner, "_ensure_confirm_enabled", lambda page, confirm, plan: self.confirm_enabled),
            patch.object(runner, "_cancel_add_account", self.cancel),
            patch.object(runner, "_api_readback", lambda result, name, domain, **kw: self.readback),
            patch.object(runner, "_spycloud_after_create", lambda *args: self.after_create.append(args)),
            patch.object(runner, "sleep", lambda _s: None),
            patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"),
                                     "playwright.sync_api": types.SimpleNamespace(sync_playwright=self.playwright)}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.write_clone(prod_row())

    def write_clone(self, *rows, hours_old=0):
        assembled = inventory.assemble_pages([inventory_page(0, list(rows), len(rows), size=len(rows))],
                                             page_size=len(rows))
        inventory.write_snapshot(inventory.snapshot_payload(
            inventory.ENVIRONMENTS["prod-clone"], assembled, datetime.now(timezone.utc) - timedelta(hours=hours_old)),
            self.root)

    def write_dev_inventory(self, **overrides):
        assembled = inventory.assemble_pages([inventory_page(0, [tenant_row(9, **overrides)], 1, size=1)],
                                             page_size=1)
        inventory.write_snapshot(inventory.snapshot_payload(
            inventory.ENVIRONMENTS["dev"], assembled, datetime.now(timezone.utc)), self.root)

    @contextlib.contextmanager
    def attended(self, _playwright):
        yield self.page

    @contextlib.contextmanager
    def playwright(self):
        yield object()

    def search(self, page, search, lookup):
        return types.SimpleNamespace(rows=[], total_count=0)

    def fill(self, page, plan):
        self.fill_plans.append(plan)
        return None, _Control()

    def cancel(self, page):
        self.cancelled.append(True)
        return True

    def test_dry_run_fills_cancels_and_writes_nothing(self):
        result = runner.run_renewal_mirror("CO-0900")
        self.assertEqual(result, "mirror_dry_run_verified")
        self.assertEqual(self.cancelled, [True])
        self.assertNotIn("Confirm", self.page.clicked)
        self.assertEqual(self.fill_plans[0]["texts"]["Company name"], "Acme Corp")
        self.assertFalse(runner.MIRROR_PATH.exists() or runner.READBACK_PATH.exists())
        self.assertEqual(runner.mirror_write_label(result, False), "not_performed")

    def test_confirm_write_creates_reads_back_records_twice_and_turns_spycloud_off(self):
        result = runner.run_renewal_mirror("CO-0900", confirm_write=True)
        self.assertEqual(result, "mirror_created_verified")
        self.assertEqual(self.page.clicked.count("Confirm"), 1)  # clicked exactly once, never retried
        mirrors = json.loads(runner.MIRROR_PATH.read_text(encoding="utf-8"))
        self.assertEqual(mirrors["CO-0900"]["status"], "verified")
        self.assertIs(mirrors["CO-0900"]["mirror_of_production"], True)
        self.assertEqual(mirrors["CO-0900"]["source_prod_id"], "TENANTID00000001")
        self.assertEqual(runner._readback_ids("CO-0900"), (ID, UUID))  # the renewal edit finds the mirror
        self.assertEqual(self.after_create[0][1:3], ("CO-0900", runner.CASE3_ENGINE))
        self.assertEqual(runner.mirror_write_label(result, True), "verified")

    def test_no_spycloud_hook_when_the_mirror_has_no_leaked_credentials(self):
        self.write_clone(prod_row(license_={"leakedCredentialsAllowed": False}))
        self.assertEqual(runner.run_renewal_mirror("CO-0900", confirm_write=True), "mirror_created_verified")
        self.assertEqual(self.after_create, [])

    def test_second_run_is_refused_without_touching_leonardo(self):
        runner.run_renewal_mirror("CO-0900", confirm_write=True)
        self.page.clicked.clear()
        self.fill_plans.clear()
        self.assertEqual(runner.run_renewal_mirror("CO-0900", confirm_write=True), "mirror_already_exists")
        self.assertEqual((self.page.clicked, self.fill_plans), ([], []))

    def test_an_unverified_create_still_blocks_a_second_attempt(self):
        self.readback = None
        result = runner.run_renewal_mirror("CO-0900", confirm_write=True)
        self.assertEqual(result, "mirror_create_unverified")
        self.assertEqual(runner.mirror_write_label(result, True), "attempted_unverified")
        self.assertEqual(json.loads(runner.MIRROR_PATH.read_text(encoding="utf-8"))["CO-0900"]["status"],
                         "create_attempted")
        self.assertEqual(runner.run_renewal_mirror("CO-0900", confirm_write=True), "mirror_already_exists")

    def test_refusals_before_any_browser_work(self):
        self.assertEqual(runner.run_renewal_mirror("CO-0900", env_name="prod"), "mirror_environment_not_supported")
        self.assertEqual(runner.run_renewal_mirror("bad"), "invalid_co_reference")
        runner.write_readback_evidence("CO-0900", ID, UUID, "2026-10-06", "No scan started")
        self.assertEqual(runner.run_renewal_mirror("CO-0900"), "mirror_readback_exists")
        with patch.object(runner, "mirror_source", side_effect=RuntimeError("mirror_not_renewal")):
            self.assertEqual(runner.run_renewal_mirror("CO-0901"), "mirror_not_renewal")
        self.assertEqual(self.fill_plans, [])

    def test_production_clone_reason_codes_are_returned(self):
        self.write_clone(tenant_row(2))
        self.assertEqual(runner.run_renewal_mirror("CO-0900"), "mirror_prod_tenant_not_found")
        self.write_clone(prod_row(), prod_row(2, accountName="Acme Corp - CE Only", accountDomain="x.example"))
        self.assertEqual(runner.run_renewal_mirror("CO-0900"), "mirror_prod_tenant_ambiguous")
        self.write_clone(prod_row(), hours_old=7)
        self.assertEqual(runner.run_renewal_mirror("CO-0900"), "mirror_clone_unavailable")
        self.assertEqual(self.fill_plans, [])

    def test_a_dev_inventory_match_stops_before_the_browser(self):
        self.write_dev_inventory(accountName="ACME corp")
        self.assertEqual(runner.run_renewal_mirror("CO-0900", confirm_write=True), "mirror_duplicate_inventory_match")
        self.assertEqual((self.fill_plans, self.page.clicked), ([], []))
        self.assertFalse(runner.MIRROR_PATH.exists())

    def test_a_live_dev_duplicate_stops_before_add_account(self):
        self.dup_ui = "duplicate_found"
        result = runner.run_renewal_mirror("CO-0900", confirm_write=True)
        self.assertEqual(result, "mirror_duplicate_found")
        self.assertEqual((self.fill_plans, self.page.clicked), ([], []))
        self.assertEqual(runner.mirror_write_label(result, True), "not_performed")

    def test_confirm_disabled_stops_before_confirm_and_cancels(self):
        self.confirm_enabled = False
        self.assertEqual(runner.run_renewal_mirror("CO-0900", confirm_write=True), "confirm_button_not_enabled")
        self.assertEqual(self.cancelled, [True])
        self.assertFalse(runner.MIRROR_PATH.exists())

    def test_form_failure_cancels_and_reports_the_fill_code(self):
        with patch.object(runner, "_fill_add_account_form", lambda page, plan: ("fill_value_mismatch", None)):
            self.assertEqual(runner.run_renewal_mirror("CO-0900", confirm_write=True), "fill_value_mismatch")
        self.assertEqual(self.cancelled, [True])


class CliTests(unittest.TestCase):
    def run_main(self, argv, result="mirror_dry_run_verified"):
        out = io.StringIO()
        with patch.object(sys, "argv", ["runner", *argv]), \
                patch.object(runner, "run_renewal_mirror", return_value=result) as called, \
                contextlib.redirect_stdout(out):
            code = runner.main()
        return code, json.loads(out.getvalue()), called

    def test_dry_run_and_confirmed_output(self):
        code, out, called = self.run_main(["--co", "CO-0900", "--mirror-renewal"])
        self.assertEqual((code, out), (0, {"result": "mirror_dry_run_verified", "leonardo_write": "not_performed",
                                           "salesforce_writeback": "not_performed"}))
        called.assert_called_once_with("CO-0900", confirm_write=False, env_name="dev")
        _code, out, called = self.run_main(["--co", "CO-0900", "--mirror-renewal", "--confirm-write"],
                                           "mirror_created_verified")
        self.assertEqual(out["leonardo_write"], "verified")
        called.assert_called_once_with("CO-0900", confirm_write=True, env_name="dev")

    def test_unverified_after_confirm_and_prod_env(self):
        _code, out, _ = self.run_main(["--co", "CO-0900", "--mirror-renewal", "--confirm-write"],
                                      "mirror_create_unverified")
        self.assertEqual(out["leonardo_write"], "attempted_unverified")
        _code, out, called = self.run_main(["--co", "CO-0900", "--mirror-renewal", "--env", "prod"],
                                           "mirror_environment_not_supported")
        called.assert_called_once_with("CO-0900", confirm_write=False, env_name="prod")
        self.assertEqual(out["leonardo_write"], "not_performed")

    def test_co_is_required_and_confirm_write_needs_a_write_mode(self):
        for argv in (["--mirror-renewal"], ["--confirm-write"]):
            with patch.object(sys, "argv", ["runner", *argv]), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                runner.main()


if __name__ == "__main__":
    unittest.main()
