"""Path B (owner go 2026-10-22): the guarded LIVE production create of a Case 3 CO (Surface & Credential Exposure, New).

``run_production_create(..., route="case_3_combined_baseline")`` is the CE-only live create generalised by route: the Case 3 source and
fill plan, the intended values built by ``production_case3_contract.case3_intended`` (the tier rule), a gate that also needs the approval
bound to the exact tenant name, a duplicate check over the name AND both domains, the one create POST compared with the Case 3 contract,
a readback compared with the Case 3 row contract, exactly one Confirm click, the approval consumed once. Both lock constants are patched
True only inside these tests (never changed). No browser, network, Salesforce, Redash or BackOffice: every external step is a fake.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import unittest
from datetime import date, timedelta
from unittest import mock

from integration.onboarding import production_approval as approval
from integration.onboarding import production_case3_contract as c3
from integration.onboarding import production_contract as contract
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_launch_ticket as ticket
from integration.onboarding import session_readiness
from integration.tests import test_production_create_runner as live_tests
from integration.tests import test_production_dry_run as dry
from integration.tests.test_case3_dry_run_runner import EMAIL, MAIN, NAME, MAX_SCAN_LABEL, Recorder, make_case3_source
from integration.tests.test_production_create_live import contract_row
import tools.attended_ce_only_playwright as runner

CASE3 = "case_3_combined_baseline"
CE = "case_2_new_ce_only"
CO, REF, REV, NOW = dry.CO, dry.REF, dry.REV, dry.NOW
DAY = date(2026, 10, 16)
SAMPLES = {"non_empty_text": "x", "iso2": "CL", "iso_utc": "2026-10-16T12:00:00.000Z", "number": 5}


def intended_for(source):
    return runner.case3_live_intended(source, runner.build_case3_fill(source, DAY), DAY)


def case3_body(source, **changes):
    """The Case 3 create body the page would send for ``source`` (contract values; shape fields get a sample)."""
    expected = c3.expected_case3_create_payload({**intended_for(source), "country_code": "CL"})
    flat = {}
    for key, want in expected.items():
        if isinstance(want, contract._Shape):
            want = (f"{want.name[len('iso_utc_on_'):]}T07:00:00.000Z" if want.name.startswith("iso_utc_on_")
                    else SAMPLES[want.name])
        flat[key] = want
    for key, value in changes.items():
        flat[key.replace("__", ".")] = value
    body = {}
    for key, value in flat.items():
        node = body
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return json.dumps(body)


def case3_row(source, **over):
    """The stored row of the created tenant as the contract expects it for ``source``."""
    intended = intended_for(source)
    flat = {"accountName": intended["tenant_name"], "accountDomain": intended["primary_domain"],
            "scanningInterval": intended["scanning_interval"], "accountLicense.scanningFrequency": intended["scanning_interval"],
            "accountLicense.assetsNumber": 10000, "accountLicense.domainsNumber": 10000,
            "accountLicense.subDomainsNumber": intended["subdomains_number"],
            "accountLicense.notificationsAllowed": True, "accountLicense.multipleUsersAllowed": True,
            "accountLicense.apiAccessAllowed": True, "userEmailDomains": [intended["email_domain"]]}
    flat.update(over)
    row = contract_row(**flat)
    for logical in ("subDomainsReconEnabled", "webDictionaryBruteForceEnabled"):
        row[contract.ROW_FLAG_PATHS[logical][0]] = True
    return row


class C3LivePage(live_tests.LivePage):
    """The live fake page plus the Surface-only "Maximum scan Duration" control."""

    def __init__(self, source, *, body=None, rows=None, **kw):
        super().__init__(body=body if body is not None else case3_body(source),
                         readbacks=rows if rows is not None else [live_tests.reply([case3_row(source)])], **kw)
        self.texts[MAX_SCAN_LABEL] = dry.Ctl(self, "text", MAX_SCAN_LABEL, value="24", enabled=True)


def prime_source():
    base = make_case3_source()
    entitlement = dataclasses.replace(base.surface.entitlement, tier="Prime", scanning_interval="Weekly", baseline_subdomains=1000)
    return dataclasses.replace(base, surface=dataclasses.replace(base.surface, entitlement=entitlement))


class C3Harness(live_tests.Harness):
    def __init__(self, test, *, source=None, bound_name=NAME, approval_route=CASE3, **page_kw):
        self.source = source or make_case3_source()
        search = Recorder(page_kw.pop("search", {}))
        for key, value in live_tests.CONTROL_SEARCH.items():
            search.setdefault(key, value)
        dry.Harness.__init__(self, test, search=search)
        self.page = C3LivePage(self.source, search=search, **page_kw)
        self.approval_path.unlink()
        approval.issue(self.approval_path, REF, CO, approval_route, REV, "milton", frozenset({"milton"}), NOW - timedelta(hours=1),
                       tenant_name=bound_name if approval_route == CASE3 else None)
        self.date_calls = []

    def seed_dry_run(self):
        pcr.record(self.records, {"co_reference": CO, "approval_ref": REF, "outcome": "dry_run_passed", "route": CASE3,
                                  "salesforce_revision": REV}, NOW - timedelta(minutes=30))

    def load_source(self, _ref):
        self.loaded += 1
        if self.page.form_open:  # the Salesforce re-read between the fill and the click
            for key in list(self.page.on_read):
                self.page.on_read.pop(key)()
        return self.source

    def go(self, *, runner_flag=True, dry_flag=True, route=CASE3, clone=live_tests.good_clone, source=None, with_ticket=True,
           seed_dry=True, owner_go=True):
        if seed_dry:
            self.seed_dry_run()
        if with_ticket:
            self.mint()
        out = io.StringIO()
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", runner_flag), \
                mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", dry_flag), contextlib.redirect_stdout(out):
            line = runner.run_production_create(
                CO, REV, owner_go=owner_go, route=route, environ=dict(dry.UNLOCK), now=NOW, records_parent=self.records,
                approval_path=self.approval_path, load_source=(lambda _r: source) if source else self.load_source,
                clone_facts=clone, open_page=self.open_page, fill_date=lambda *_a: True, date_kept=lambda *_a: True,
                run_day=DAY, ticket_parent=self.records, org_probe=dry.ORG_PROBE)
        self.line = line
        return json.loads(line)

    def consumed(self):
        return approval.record_for(self.approval_path, REF).get("consumed_at") is not None


class LockedTests(unittest.TestCase):
    def test_both_locks_false_means_nothing_is_reachable_for_case_3(self):
        for runner_flag, dry_flag in ((False, False), (True, False), (False, True)):
            with self.subTest(runner=runner_flag, dry=dry_flag):
                h = C3Harness(self)
                out = h.go(runner_flag=runner_flag, dry_flag=dry_flag)
                self.assertEqual(out["outcome"], "refused")
                self.assertTrue(out["result"].startswith("production_locked"), out)
                self.assertEqual((h.opened, h.page.confirm_clicks, h.page.fetches), (0, 0, []))
                self.assertFalse(h.consumed())

    def test_the_constants_in_the_build_are_false(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # dry run only unlocked for the CO-0802 dry run; the create stays locked


class PassingRunTests(unittest.TestCase):
    def test_a_go_tier_case_3_create_clicks_confirm_once_verifies_and_consumes_the_approval(self):
        h = C3Harness(self)
        out = h.go()
        self.assertEqual((out["result"], out["outcome"]), ("created_verified", "readback_verified"), out)
        self.assertEqual(h.page.confirm_clicks, 1)
        self.assertTrue(h.consumed())
        record = [r for r in h.loaded_records() if r["outcome"] == "readback_verified"][0]
        self.assertEqual((record["route"], record["tenant_name"]), (CASE3, NAME))
        self.assertEqual(record["domains"], [MAIN])
        self.assertTrue(record["account_id"] and record["account_uuid"])

    def test_a_prime_tier_case_3_create_uses_weekly_and_1000_subdomains(self):
        h = C3Harness(self, source=prime_source())
        out = h.go()
        self.assertEqual(out["outcome"], "readback_verified", out)
        self.assertEqual(h.page.confirm_clicks, 1)

    def test_the_name_and_both_domains_are_searched_before_the_form_opens(self):
        h = C3Harness(self)
        h.go()
        for value in (NAME, MAIN, EMAIL):
            self.assertIn(value, h.page.search.asked)

    def test_the_run_never_prints_names_domains_or_ids(self):
        h = C3Harness(self)
        h.go()
        for secret in (NAME, MAIN, EMAIL, live_tests.UUID):
            self.assertNotIn(secret, h.line)


class RefusalTests(unittest.TestCase):
    def refused(self, h, reason, **kw):
        out = h.go(**kw)
        self.assertEqual(out["outcome"], "refused", out)
        self.assertEqual(out["result"], reason, out)
        self.assertEqual(h.page.confirm_clicks, 0)
        self.assertFalse(h.consumed())

    def test_an_approval_bound_to_another_name_refuses_before_any_browser(self):
        h = C3Harness(self, bound_name="Another Tenant Name")
        self.refused(h, "approval_tenant_name_drift")
        self.assertEqual(h.opened, 0)

    def test_a_ce_only_approval_never_opens_a_case_3_create(self):
        h = C3Harness(self, approval_route=CE)
        self.refused(h, "approval_route_mismatch")
        self.assertEqual(h.opened, 0)

    def test_an_unconfirmed_long_name_refuses_before_any_browser(self):
        h = C3Harness(self)
        flagged = dataclasses.replace(h.source, surface=dataclasses.replace(h.source.surface))
        with mock.patch.object(type(flagged), "tenant_name_unconfirmed", True, create=True):
            self.refused(h, "tenant_name_not_confirmed", source=flagged)
        self.assertEqual(h.opened, 0)

    def test_ids_already_on_the_co_refuse(self):
        h = C3Harness(self)
        present = dataclasses.replace(h.source, salesforce_id_present=True)
        self.refused(h, "salesforce_prior_uuid_present", source=present)
        self.assertEqual(h.opened, 0)

    def test_a_missing_ticket_or_dry_run_or_owner_go_refuses(self):
        self.refused(C3Harness(self), "launch_ticket_invalid", with_ticket=False)
        out = C3Harness(self).go(seed_dry=False)
        self.assertEqual((out["outcome"], out["result"]), ("refused", "dry_run_required"))
        out = C3Harness(self).go(owner_go=False)
        self.assertEqual((out["outcome"], out["result"]), ("refused", "owner_go_missing"))

    def test_a_duplicate_on_the_name_the_main_domain_or_the_email_domain_refuses(self):
        for value in (NAME, MAIN, EMAIL):
            with self.subTest(value=value):
                match = live_tests.reply([{"name": NAME, "domain": MAIN, "alternates": [], "emails": [], "deleted": False}])
                h = C3Harness(self, search={value: match})
                out = h.go()
                self.assertEqual(out["outcome"], "refused", out)
                self.assertEqual(h.page.confirm_clicks, 0)
                self.assertFalse(h.consumed())

    def test_a_subdomain_count_the_tier_rule_does_not_allow_is_refused(self):
        base = make_case3_source()
        entitlement = dataclasses.replace(base.surface.entitlement, baseline_subdomains=750)
        odd = dataclasses.replace(base, surface=dataclasses.replace(base.surface, entitlement=entitlement))
        h = C3Harness(self, body="{}")
        out = h.go(source=odd)
        self.assertEqual(out["outcome"], "refused", out)
        self.assertEqual(h.page.confirm_clicks, 0)
        self.assertEqual(h.opened, 0)


class OutcomeTests(unittest.TestCase):
    def test_a_create_body_that_differs_from_the_contract_is_never_verified(self):
        h = C3Harness(self, body=case3_body(make_case3_source(), scanningInterval="WEEKLY"))
        out = h.go()
        self.assertEqual(out["outcome"], "created_unverified", out)
        self.assertEqual(h.page.confirm_clicks, 1)
        record = [r for r in h.loaded_records() if r["outcome"] == "created_unverified"][0]
        self.assertIn("scanningInterval", record["payload_mismatches"])

    def test_a_readback_that_differs_is_never_verified(self):
        source = make_case3_source()
        h = C3Harness(self, rows=[live_tests.reply([case3_row(source, **{"accountLicense.subDomainsNumber": 1000})])])
        out = h.go()
        self.assertEqual(out["outcome"], "created_unverified", out)
        self.assertEqual(h.page.confirm_clicks, 1)

    def test_the_confirm_is_clicked_at_most_once(self):
        h = C3Harness(self, rows=[live_tests.reply([])])
        out = h.go()
        self.assertEqual(h.page.confirm_clicks, 1)
        self.assertNotEqual(out["outcome"], "readback_verified")


class NameAgreementTests(unittest.TestCase):
    def test_the_typed_company_name_must_be_the_name_the_approval_covers(self):
        source = make_case3_source()
        plan = runner.build_case3_fill(source, DAY)
        key = next(k for k in plan["texts"] if " ".join(k.split()) == "Company name")
        plan["texts"][key] = plan["texts"][key] + " Ltd"  # a future plan change that splits form and approval
        with self.assertRaises(ValueError) as caught:
            runner.case3_live_intended(source, plan, DAY)
        self.assertEqual(str(caught.exception), "case3_tier_values_mismatch")


class CommandLineTests(unittest.TestCase):
    def run_main(self, *argv):
        with mock.patch.object(runner, "run_production_create", return_value="{}") as create, \
                mock.patch("sys.argv", ["attended_ce_only_playwright.py", *argv]), contextlib.redirect_stdout(io.StringIO()):
            try:
                code = runner.main()
            except SystemExit as exit_:
                code = exit_.code
        return code, create

    def test_the_default_route_stays_ce_only(self):
        code, create = self.run_main("--production-create", "--co", "CO-0797", "--revision", REV, "--owner-go")
        self.assertEqual(code, 0)
        self.assertEqual(create.call_args.kwargs.get("route", CE), CE)

    def test_case_3_is_selected_by_route(self):
        code, create = self.run_main("--production-create", "--co", "CO-0802", "--revision", REV, "--owner-go", "--route", CASE3)
        self.assertEqual(code, 0)
        self.assertEqual(create.call_args.kwargs["route"], CASE3)

    def test_any_other_route_is_refused(self):
        for route in ("case_1_new_surface_only", "case_4_renew_surface_new_ce"):
            code, create = self.run_main("--production-create", "--co", "CO-0802", "--revision", REV, "--owner-go", "--route", route)
            self.assertNotEqual(code, 0)
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
