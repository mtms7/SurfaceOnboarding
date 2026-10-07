"""Production match (owner decisions 2026-10-06 / 2026-10-07): the pure state machine and the runner glue.

Pure data only: no Salesforce, no Redash, no network, no browser.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import production_match as pm
from integration.tests.test_redash_inventory import FAKE_KEY, GET, NOW, FakeOpener, result, row
import tools.attended_ce_only_playwright as runner
import tools.redash_inventory_collector as collector

END_MS = 1823817600000  # 2027-10-18T00:00:00Z


def tenant(name="Acme", domain="acme.example", alternates=(), *, enabled=True, deleted=False, kind="PREPAID_ANNUAL_SUBSCRIPTION",
           end=END_MS, start=1790812800000, spycloud=False, operator=True, scan="COMPLETED", ident=None):
    return {"id": ident or "id-" + name.casefold().replace(" ", "-") + ("-d" if deleted else "") + ("" if enabled else "-x"),
            "account_name": name, "account_domain": domain, "alternate_domains": list(alternates), "enabled": enabled,
            "is_deleted": deleted, "created": 1759622400000, "spycloud_enabled": spycloud, "operator_assigned": operator,
            "license": {"type": kind, "start_date": start, "expiration_date": end}, "scan": {"status": scan}}


def snapshot(*tenants, with_alternates=True):
    rows = [dict(item) for item in tenants]
    if not with_alternates:
        for item in rows:
            item["alternate_domains"] = None
    return {"tenants": rows, "captured_at": "2026-10-05T11:30:00Z", "age_seconds": 1800}


CO = {"tenant_names": ("Acme",), "domains": ("acme.example", "alt.acme.example")}
PLAN = {"primary_domain": "acme.example", "alternate_domains": ("alt.acme.example",), "license_type": "Prepaid annual subscription",
        "license_end": date(2027, 10, 18), "license_start": date(2026, 10, 1), "spycloud_off": True}
GOOD = dict(alternates=("alt.acme.example",))


def match(*tenants, plan=PLAN, co=CO, **kwargs):
    return pm.production_match(co, plan, snapshot(*tenants, **kwargs))


class StateMachineTests(unittest.TestCase):
    def test_clone_unavailable_never_clears(self):
        for snap in (None, "inventory_snapshot_missing", "inventory_snapshot_stale", "inventory_snapshot_tampered"):
            with self.subTest(snap=snap):
                found = pm.production_match(CO, PLAN, snap)
                self.assertEqual(found["status"], "clone_unavailable")
                self.assertEqual(found["reason"], snap or "inventory_snapshot_missing")
        found = match(tenant(**GOOD), with_alternates=False)  # the source dropped the alternate-domain field
        self.assertEqual((found["status"], found["reason"]), ("clone_unavailable", "alternate_domains_missing"))
        for bad_co, bad_plan in ((None, PLAN), (CO, None), ({"tenant_names": (), "domains": ("a.example",)}, PLAN),
                                 ({"tenant_names": ("Acme",), "domains": ()}, PLAN)):
            found = pm.production_match(bad_co, bad_plan, snapshot(tenant(**GOOD)))
            self.assertEqual((found["status"], found["reason"]), ("clone_unavailable", "co_source_unreadable"))

    def test_no_candidate_is_not_in_production_and_says_it_is_not_a_clearance(self):
        found = match(tenant("Other Corp", "other.example", ("x.other.example",)))
        self.assertEqual(found["status"], "not_in_production")
        self.assertIn("not a clearance", found["reason"])
        self.assertIn("a day", found["reason"])
        self.assertEqual((found["candidates"], found["target"]), ([], None))

    def test_candidates_are_the_same_name_or_a_primary_or_alternate_domain(self):
        by_name = match(tenant("  ACME  ", "elsewhere.example", ("e.example",), kind="Trial"))
        by_primary = match(tenant("Different", "ACME.example.", ("e.example",), kind="Trial"))
        by_alternate = match(tenant("Different", "elsewhere.example", ("alt.acme.example",), kind="Trial"))
        for found in (by_name, by_primary, by_alternate):
            self.assertEqual(found["status"], "exists_other")
            self.assertEqual(len(found["candidates"]), 1)

    def test_only_trial_pov_disabled_or_deleted_tenants_is_exists_other_and_blocks(self):
        for others in ([tenant(kind="Trial")], [tenant(kind="POV")], [tenant(kind="Evaluation")],
                       [tenant(enabled=False)], [tenant(deleted=True)],
                       [tenant(kind="Trial"), tenant(enabled=False), tenant(deleted=True)]):
            with self.subTest(others=[(t["license"]["type"], t["enabled"], t["is_deleted"]) for t in others]):
                found = match(*others)
                self.assertEqual(found["status"], "exists_other")
                self.assertIn("blocked", found["reason"])
                self.assertIsNone(found["target"])
        deleted = match(tenant(deleted=True))["candidates"][0]
        self.assertTrue(deleted["deleted"])  # deleted tenants are listed, but flagged

    def test_a_live_paid_tenant_with_another_name_does_not_count_as_the_target(self):
        found = match(tenant("Acme Holdings", "acme.example", ("alt.acme.example",)))  # same domain, different name
        self.assertEqual(found["status"], "exists_other")

    def test_two_live_paid_tenants_with_the_exact_name_is_ambiguous(self):
        found = match(tenant(ident="one", **GOOD), tenant(ident="two", **GOOD))
        self.assertEqual(found["status"], "ambiguous")
        self.assertEqual(len(found["candidates"]), 2)

    def test_exactly_one_live_paid_exact_name_tenant_that_agrees_everywhere_matches(self):
        found = match(tenant(**GOOD), tenant("Acme", "acme.example", kind="Trial", ident="trial"), tenant(enabled=False, ident="off"))
        self.assertEqual(found["status"], "exists_matches")
        self.assertEqual(found["differs"], [])
        self.assertEqual(found["target"]["id"], "id-acme")
        self.assertEqual({row["code"]: row["result"] for row in found["fields"]}, {
            "tenant_name": "match", "primary_domain": "match", "alternate_domains": "match", "license_type": "match",
            "license_end": "match", "spycloud_off": "match", "license_start": "match", "operator_assigned": "info",
            "scan_status": "info", "salesforce_account_id": "not_checked"})

    def test_each_required_field_that_differs_makes_exists_differs(self):
        cases = {
            "primary_domain": tenant(domain="acme.example.org", **GOOD),
            "alternate_domains": tenant(alternates=("alt.acme.example", "extra.example")),
            "license_type": tenant(kind="PREPAID_MONTHLY_SUBSCRIPTION", **GOOD),
            "license_end": tenant(end=END_MS + 86400000 * 3, **GOOD),
            "spycloud_off": tenant(spycloud=True, **GOOD),
        }
        for code, item in cases.items():
            with self.subTest(code=code):
                found = match(item, co={**CO, "domains": ("acme.example", "alt.acme.example", "acme.example.org")})
                self.assertEqual((found["status"], found["differs"]), ("exists_differs", [code]))
        self.assertEqual(match(tenant(**GOOD), plan={**PLAN, "alternate_domains": ()})["differs"], ["alternate_domains"])

    def test_the_name_field_compares_against_the_co_name_for_renewals_with_two_names(self):
        co = {**CO, "tenant_names": ("Acme", "Acme - CE Only")}
        found = match(tenant("Acme - CE Only", "acme.example", ("alt.acme.example",)), co=co)
        self.assertEqual((found["status"], found["differs"]), ("exists_differs", ["tenant_name"]))

    def test_alternate_domains_use_the_renewal_domains_normalizer(self):
        found = match(tenant(alternates=("alt.acme.example",)), plan={**PLAN, "alternate_domains": (" ALT.Acme.example. ",)})
        self.assertEqual(found["status"], "exists_matches")

    def test_unknown_required_data_is_never_a_match(self):
        unknown = {
            "primary_domain": {"account_domain": None},
            "alternate_domains": {"alternate_domains": None},
            "license_end": {"license": {"type": "PREPAID_ANNUAL_SUBSCRIPTION", "start_date": 1, "expiration_date": None}},
            "spycloud_off": {"spycloud_enabled": None},
        }
        for code, change in unknown.items():
            with self.subTest(code=code):
                item = {**tenant(**GOOD), **change}
                if code == "alternate_domains":  # keep another tenant's list so the clone itself is not "incomplete"
                    found = pm.production_match(CO, PLAN, snapshot(item, tenant("Z", "z.example", ("a.example",))))
                else:
                    found = match(item)
                self.assertEqual(found["status"], "exists_differs")
                self.assertEqual(found["differs"], [code])
                self.assertEqual({r["code"]: r["result"] for r in found["fields"]}[code], "unknown")
        # A tenant without a licence type is not "live, paid", so it can never be the target (exists_other).
        self.assertEqual(match({**tenant(**GOOD), "license": {"type": None, "expiration_date": END_MS}})["status"], "exists_other")
        missing_plan = match(tenant(**GOOD), plan={**PLAN, "license_end": None})
        self.assertEqual((missing_plan["status"], missing_plan["differs"]), ("exists_differs", ["license_end"]))

    def test_informational_fields_never_block(self):
        found = match(tenant(start=1700000000000, operator=False, scan="RUNNING", **GOOD))
        self.assertEqual(found["status"], "exists_matches")
        results = {row["code"]: row["result"] for row in found["fields"]}
        self.assertEqual((results["license_start"], results["operator_assigned"], results["scan_status"]),
                         ("differs", "info", "info"))
        self.assertFalse([row for row in found["fields"] if row["code"] == "license_start"][0]["required"])
        not_planned = match(tenant(**GOOD), plan={**PLAN, "license_start": None})  # renewals never plan a start
        self.assertEqual(not_planned["status"], "exists_matches")
        self.assertEqual({r["code"]: r["result"] for r in not_planned["fields"]}["license_start"], "info")

    def test_the_salesforce_account_id_is_always_not_checked_and_not_mandatory(self):
        found = match(tenant(**GOOD))
        account = [row for row in found["fields"] if row["code"] == "salesforce_account_id"][0]
        self.assertEqual((account["result"], account["required"]), ("not_checked", False))

    def test_spycloud_is_required_off_only_on_credential_exposure_routes(self):
        surface = match(tenant(spycloud=True, **GOOD), plan={**PLAN, "spycloud_off": False})
        self.assertEqual(surface["status"], "exists_matches")
        self.assertEqual({r["code"]: r["result"] for r in surface["fields"]}["spycloud_off"], "na")
        self.assertEqual(match(tenant(spycloud=True, **GOOD))["differs"], ["spycloud_off"])

    def test_licence_end_accepts_epoch_ms_or_iso_text_and_a_utc_or_local_day(self):
        self.assertEqual(match(tenant(end="2027-10-18T00:00:00Z", **GOOD))["status"], "exists_matches")
        self.assertEqual(match(tenant(end=END_MS + 3600 * 1000 * 5, **GOOD))["status"], "exists_matches")  # same day
        self.assertEqual(match(tenant(end="not a date", **GOOD))["differs"], ["license_end"])
        self.assertEqual(match(tenant(end=True, **GOOD))["differs"], ["license_end"])

    def test_dev_mirror_tenants_are_never_production_evidence(self):
        # Only the production-clone payload is ever read; a Dev id or mirror record cannot enter the match.
        source = Path(pm.__file__).read_text(encoding="utf-8")
        for forbidden in ('"dev"', "attended_renewal_mirrors", "mirror_tenant_ids", "MIRROR_PATH", "readback"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(pm.production_match(CO, PLAN, snapshot())["status"], "clone_unavailable")  # an empty clone is no data

    def test_the_match_is_pure_and_never_mutates_the_snapshot(self):
        snap = snapshot(tenant(**GOOD), tenant("Acme", "acme.example", kind="Trial", ident="t"))
        before = json.dumps(snap, sort_keys=True)
        pm.production_match(CO, PLAN, snap)
        self.assertEqual(json.dumps(snap, sort_keys=True), before)


class SnapshotRuleTests(unittest.TestCase):
    """The same age and tamper rules as the production duplicate gate (6 h)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def collect(self, *rows):
        collector.collect(FakeOpener({GET: result(list(rows) or [row(1)], "2026-10-05T11:30:00")}), FAKE_KEY,
                          root=self.root, now=NOW)

    def test_missing_stale_and_tampered_clones_fail_closed(self):
        self.assertEqual(pm.load_snapshot(self.root, NOW), "inventory_snapshot_missing")
        self.collect()
        self.assertIsInstance(pm.load_snapshot(self.root, NOW), dict)
        self.assertIsInstance(pm.load_snapshot(self.root, NOW.replace(hour=16)), dict)  # 4.5 h old
        self.assertEqual(pm.load_snapshot(self.root, NOW.replace(hour=18, minute=31)), "inventory_snapshot_stale")  # > 6 h
        directory = inventory.env_dir(self.root, "prod-clone")
        path = next(directory.glob("redash-prod-clone-inventory-*.json"))
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tenants"][0]["account_name"] = "Tampered"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(pm.load_snapshot(self.root, NOW), "inventory_snapshot_tampered")
        self.assertEqual(pm.production_match(CO, PLAN, pm.load_snapshot(self.root, NOW))["status"], "clone_unavailable")

    def test_a_real_snapshot_runs_through_the_state_machine(self):
        self.collect(row(1, accountName="Acme", accountDomain="acme.example", alternateDomains=["alt.acme.example"],
                         **{"accountLicense.licenseType": "PREPAID_ANNUAL_SUBSCRIPTION",
                            "accountLicense.expirationDate": END_MS,
                            "leakedCredentialsSettings.spyCloudSettings": {"enabled": False}}))
        found = pm.production_match(CO, PLAN, pm.load_snapshot(self.root, NOW))
        self.assertEqual(found["status"], "exists_matches")
        self.assertEqual(found["captured_at"], "2026-10-05T11:30:00Z")
        self.assertEqual(found["age_seconds"], 1800)


class RunnerGlueTests(unittest.TestCase):
    """``production_match_for``: clone first, then one read-only source/plan read; every failure fails closed."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        patcher = patch.object(runner, "inventory_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def collect(self, *rows):
        collector.collect(FakeOpener({GET: result(list(rows), "2026-10-05T11:30:00")}), FAKE_KEY, root=self.root, now=NOW)

    def clock(self):
        return patch.object(runner, "datetime", wraps=datetime, **{"now.return_value": NOW})

    def test_an_unusable_clone_needs_no_salesforce_read(self):
        with patch.object(runner, "_production_expectation", side_effect=AssertionError("no Salesforce read")), self.clock():
            found = runner.production_match_for("CO-0801", "Credential Exposure", "New Product Onboarding")
        self.assertEqual((found["status"], found["reason"]), ("clone_unavailable", "inventory_snapshot_missing"))

    def test_an_unreadable_co_source_is_clone_unavailable_never_a_clearance(self):
        self.collect(row(1))
        for error in (RuntimeError("salesforce_fill_source_unavailable"), ValueError(), KeyError("x")):
            with self.subTest(error=error), patch.object(runner, "_production_expectation", side_effect=error), self.clock():
                found = runner.production_match_for("CO-0801", "Credential Exposure", "New Product Onboarding")
                self.assertEqual((found["status"], found["reason"]), ("clone_unavailable", "co_source_unreadable"))
        with self.clock():
            self.assertEqual(runner.production_match_for("not-a-co")["reason"], "co_source_unreadable")

    def test_the_expectation_comes_from_the_co_and_passes_to_the_match(self):
        self.collect(row(1, accountName="Acme - CE Only", accountDomain="mail.example", alternateDomains=[],
                         **{"accountLicense.licenseType": "PREPAID_ANNUAL_SUBSCRIPTION", "accountLicense.expirationDate": END_MS,
                            "leakedCredentialsSettings.spyCloudSettings": {"enabled": False}}))
        expectation = (runner.CE_ENGINE, {"tenant_names": ("Acme - CE Only",), "domains": ("mail.example",)},
                       {"primary_domain": "mail.example", "alternate_domains": (), "license_type": "Prepaid annual subscription",
                        "license_end": date(2027, 10, 18), "license_start": date(2026, 10, 1), "spycloud_off": True})
        with patch.object(runner, "_production_expectation", return_value=expectation) as read, self.clock():
            found = runner.production_match_for("CO-0801", "Credential Exposure", "New Product Onboarding")
        read.assert_called_once_with("CO-0801", "Credential Exposure", "New Product Onboarding")
        self.assertEqual((found["status"], found["route"]), ("exists_matches", runner.CE_ENGINE))

    def test_production_dates_never_use_the_dev_entered_or_run_day_dates(self):
        calls = []

        class Source:
            reference, tenant_name, email_domain = "CO-0801", "Acme - CE Only", "mail.example"
            subscription_start, subscription_end = date(2026, 11, 2), date(2029, 11, 1)

        contract = runner.RouteContract(
            engine=runner.CE_ENGINE, load_source=lambda ref: Source(), license_dates=lambda *a: None,
            build_fill=lambda source, day=None: calls.append(day) or {"selects": {"Type": "Prepaid annual subscription"},
                                                                       "license_start": day, "license_end": date(2027, 11, 1)},
            primary_domain=lambda source: source.email_domain, redactions=lambda source: (), allow_scan_started=False)
        with patch.dict(runner.ROUTES, {runner.CE_ENGINE: contract}), \
                patch.object(runner, "load_runner_state", side_effect=AssertionError("entered dates are never read")), \
                patch.object(runner, "_run_day", side_effect=AssertionError("the Dev run day is never used")):
            engine, source, plan = runner._production_expectation("CO-0801", "Credential Exposure", "New Product Onboarding")
        self.assertEqual(calls, [date(2026, 11, 2)])  # the DealHub subscription start, not today
        self.assertEqual((engine, plan["license_start"], plan["license_end"], plan["spycloud_off"]),
                         (runner.CE_ENGINE, date(2026, 11, 2), date(2027, 11, 1), True))
        self.assertEqual((source["tenant_names"], source["domains"]), (("Acme - CE Only",), ("mail.example",)))

    def test_surface_only_does_not_require_spycloud_off_and_compares_main_and_alternate_roots(self):
        class Source:
            reference, tenant_name, main_domain = "CO-0802", "Acme", "acme.example"
            alternate_domains = ("alt.acme.example",)
            subscription_start, subscription_end = date(2026, 11, 2), date(2027, 11, 1)

        contract = runner.RouteContract(
            engine=runner.SURFACE_ENGINE, load_source=lambda ref: Source(), license_dates=lambda *a: None,
            build_fill=lambda source, day=None: {"selects": {"Type": "Prepaid annual subscription"}, "license_start": day,
                                                 "license_end": date(2027, 11, 1)},
            primary_domain=lambda source: source.main_domain, redactions=lambda source: (), allow_scan_started=True)
        with patch.dict(runner.ROUTES, {runner.SURFACE_ENGINE: contract}):
            engine, source, plan = runner._production_expectation("CO-0802", "Surface", "New Product Onboarding")
        self.assertEqual((engine, plan["spycloud_off"], plan["primary_domain"], plan["alternate_domains"]),
                         (runner.SURFACE_ENGINE, False, "acme.example", ("alt.acme.example",)))
        self.assertEqual(source["domains"], ("acme.example", "alt.acme.example"))

    def test_renewals_expect_the_dealhub_term_end_exactly_both_names_and_spycloud_off(self):
        class Source:
            tenant_name, ce_tenant_name, main_domain = "Acme", "Acme - CE Only", "acme.example"
            alternate_domains, ce_email_domain = ("alt.acme.example",), "mail.example"
            surface_term = {"end": "2029-10-26"}

        contract = runner.RouteContract(
            engine="case_6_renew_both", load_source=lambda ref: Source(), license_dates=lambda *a: None,
            build_fill=lambda *a: {}, primary_domain=lambda s: s.main_domain, redactions=lambda s: (),
            allow_scan_started=True, mode="edit")
        with patch.dict(runner.RENEWAL_ROUTES, {"case_6_renew_both": contract}):
            engine, source, plan = runner._production_expectation("CO-0767", "Surface & Credential Exposure",
                                                                   "Renewal of Existing Product")
        self.assertEqual(engine, "case_6_renew_both")
        self.assertEqual((plan["license_end"], plan["license_start"], plan["spycloud_off"]), (date(2029, 10, 26), None, True))
        self.assertEqual(source["tenant_names"], ("Acme", "Acme - CE Only"))
        self.assertIn("mail.example", source["domains"])


if __name__ == "__main__":
    unittest.main()
