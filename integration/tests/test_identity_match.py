"""Owner request 2026-10-19: every open CO is validated against the production clone, not only CE-only New.

Before this, a Surface + CE New CO was "Not checked" (its production inputs are only built for CE-only) and a renewal showed
"No automated route", and on the desktop no CO carried its Account UUID into the match, so an onboarded CO could not be
recognised. The identity check uses only what Salesforce holds for the CO (account name, domains, Account UUID) and the
clone's tenants: it can say onboarded (UUID), duplicate, in production (renewal) or not in production, but it never compares
the plan, and it never clears a CO the full match would block. Pure data: no Salesforce, no Redash, no network.
"""

import unittest
from unittest import mock

import tools.attended_ce_only_playwright as runner
from integration.onboarding import production_match as pm
from integration.onboarding import production_views as views
from integration.tests.test_production_match import snapshot, tenant

UUID = "0123456789abcdef0123456789abcdef"
OTHER = "f" * 32
IDENTITY = {"names": ("Acme",), "domains": ("acme.example",), "uuid": None}


def identity(**over):
    return {**IDENTITY, **over}


def owned(**over):
    return {**tenant(**over), "account_uuid": UUID}


class IdentityMatchTests(unittest.TestCase):
    def match(self, *tenants, ident=None, renewal=False):
        return pm.identity_match(ident or IDENTITY, snapshot(*tenants), renewal=renewal)

    def test_an_unusable_clone_or_no_identity_never_clears(self):
        for snap in (None, "inventory_snapshot_stale"):
            self.assertEqual(pm.identity_match(IDENTITY, snap, renewal=False)["status"], "clone_unavailable")
        for empty in ({"names": (), "domains": (), "uuid": None}, None,
                      {"names": ("", " "), "domains": ("not a domain",), "uuid": ""}):
            found = pm.identity_match(empty, snapshot(tenant()), renewal=False)
            self.assertEqual((found["status"], found["reason"]), ("not_checked", "co_identity_unreadable"))

    def test_the_cos_own_uuid_makes_it_onboarded_with_no_plan_review(self):
        found = self.match(owned(), ident=identity(uuid=UUID))
        self.assertEqual(found["status"], "onboarded")
        self.assertEqual(found["review"]["warnings"], [])
        self.assertIn("identity", found["review"]["line"].casefold())
        self.assertNotIn(UUID, str(found))
        for renewal in (False, True):
            self.assertEqual(self.match(owned(), ident=identity(uuid=UUID), renewal=renewal)["status"], "onboarded")

    def test_two_tenants_with_the_uuid_or_a_rival_live_tenant_is_ambiguous(self):
        twin = {**owned(name="Twin", domain="twin.example"), "id": "id-twin"}
        self.assertEqual(self.match(owned(), twin, ident=identity(uuid=UUID))["status"], "ambiguous")
        rival = tenant("Acme", "acme.example", ident="id-rival")
        self.assertEqual(self.match(owned(name="Acme Owner", domain="owner.example"), rival,
                                    ident=identity(uuid=UUID))["status"], "ambiguous")

    def test_a_uuid_with_no_owner_and_no_candidate_is_not_in_clone(self):
        found = self.match(tenant("Other", "other.example"), ident=identity(uuid=OTHER))
        self.assertEqual(found["status"], "uuid_not_in_clone")

    def test_a_new_onboarding_with_no_candidate_is_not_in_production_and_not_a_clearance(self):
        found = self.match(tenant("Other", "other.example"))
        self.assertEqual(found["status"], "not_in_production")
        self.assertIn("not a clearance", found["reason"])

    def test_a_new_onboarding_that_matches_a_live_paid_tenant_is_a_duplicate_whose_plan_was_not_compared(self):
        found = self.match(tenant("Acme", "acme.example"))
        self.assertEqual((found["status"], found["differs"]), ("exists_differs", ["plan_not_compared"]))
        self.assertEqual(views.production_check(found)["code"], "duplicate")
        self.assertTrue(views.production_check(found)["blocked"])

    def test_a_new_onboarding_matching_only_trials_or_two_live_tenants_blocks(self):
        self.assertEqual(self.match(tenant("Acme", "acme.example", kind="Trial"))["status"], "exists_other")
        two = self.match(tenant("Acme", "a1.example", ident="i1"), tenant("ACME", "a2.example", ident="i2"),
                         ident=identity(domains=("acme.example", "a1.example", "a2.example")))
        self.assertEqual(two["status"], "ambiguous")

    def test_a_renewal_expects_the_tenant_to_exist(self):
        found = self.match(tenant("Acme", "acme.example"), renewal=True)
        self.assertEqual(found["status"], "tenant_found")
        check = views.production_check(found)
        self.assertEqual((check["code"], check["label"], check["blocked"], check["css"]),
                         ("tenant_found", "In production", False, "chip-ok"))
        missing = self.match(tenant("Other", "other.example"), renewal=True)
        self.assertEqual(missing["status"], "tenant_missing")
        check = views.production_check(missing)
        self.assertEqual((check["code"], check["label"], check["blocked"], check["css"]),
                         ("tenant_missing", "Not in production", True, "chip-warn"))
        self.assertEqual(self.match(tenant("Acme", "acme.example", kind="Trial"), renewal=True)["status"], "tenant_found")

    def test_nothing_sensitive_is_in_any_result(self):
        for found in (self.match(owned(), ident=identity(uuid=UUID)), self.match(tenant("Acme", "acme.example"))):
            self.assertNotIn(UUID, str(found))


class IdentityFromSalesforceTests(unittest.TestCase):
    def test_names_domains_and_uuid_are_read_from_the_fixed_fields_only(self):
        row = {"Name": "CO-0766", "Account_Name__c": "  Payments  Network ", "Main_Domain__c": "PNPI.com.",
               "Alternate_Domains__c": "pnpi.ph; pnpi.org\nbad domain", "Email_Domains__c": "pnpi.com", "Account_UUID__c": UUID,
               "Onboarding_Comments__c": "SECRET comment"}
        with mock.patch.object(runner, "_sf_records", return_value=[row]) as read:
            got = runner._identity_for("CO-0766")
        query = read.call_args[0][0]
        self.assertIn("FROM Customer_Onboarding__c WHERE Name = 'CO-0766' LIMIT 2", query)
        self.assertNotIn("Onboarding_Comments__c", query)
        self.assertEqual(got["uuid"], UUID)
        self.assertEqual(got["names"], ("Payments Network",))
        self.assertEqual(sorted(got["domains"]), ["pnpi.com", "pnpi.org", "pnpi.ph"])
        self.assertNotIn("SECRET", str(got))

    def test_an_unreadable_or_ambiguous_row_gives_no_identity(self):
        for rows in ([], [{"Name": "CO-0766"}, {"Name": "CO-0766"}], [{"Name": "CO-0001"}]):
            with mock.patch.object(runner, "_sf_records", return_value=rows):
                self.assertIsNone(runner._identity_for("CO-0766"))
        with mock.patch.object(runner, "_sf_records", side_effect=RuntimeError("x")):
            self.assertIsNone(runner._identity_for("CO-0766"))


class ProductionMatchForTests(unittest.TestCase):
    """The glue: the full CE-only match keeps working; every other CO gets the identity check; the desktop passes the UUID."""

    SNAP = snapshot(tenant("Acme", "acme.example"), owned(name="Owned Co", domain="owned.example"))

    def run_for(self, *, expectation, ident, product="Surface + CE", kind="New Product Onboarding"):
        with mock.patch.object(pm, "load_snapshot", return_value=self.SNAP), \
                mock.patch.object(runner, "_production_expectation", side_effect=expectation), \
                mock.patch.object(runner, "_identity_for", return_value=ident), \
                mock.patch("integration.onboarding.state_paths.vm_mode", return_value=False):
            return runner.production_match_for("CO-0766", product, kind)

    @staticmethod
    def boom(*args, **kwargs):
        raise RuntimeError("inputs not prepared")

    def test_when_the_production_inputs_cannot_be_built_the_identity_check_runs_instead_of_not_checked(self):
        found = self.run_for(expectation=self.boom, ident=identity(names=("Nobody",), domains=("nobody.example",)))
        self.assertEqual(found["status"], "not_in_production")
        self.assertIs(found["identity_only"], True)

    def test_a_renewal_pair_is_checked_as_a_renewal(self):
        found = self.run_for(expectation=self.boom, ident=identity(), product="Credential Exposure", kind="Renewal")
        self.assertEqual(found["status"], "tenant_found")

    def test_the_desktop_passes_the_cos_uuid_so_an_onboarded_co_is_recognised(self):
        found = self.run_for(expectation=self.boom, ident=identity(names=("Owned Co",), domains=("owned.example",), uuid=UUID))
        self.assertEqual(found["status"], "onboarded")
        self.assertIs(found["uuid_checked"], True)
        self.assertIs(found["co_uuid_set"], True)

    def test_an_unreadable_identity_stays_not_checked_never_clear(self):
        found = self.run_for(expectation=self.boom, ident=None)
        self.assertEqual(found["status"], "not_checked")
        self.assertEqual(found["reason"], "production_inputs_not_prepared")

    def test_a_full_match_still_wins_when_the_inputs_exist(self):
        co = {"tenant_names": ("Acme",), "domains": ("acme.example",)}
        plan = {"primary_domain": "acme.example", "alternate_domains": (), "license_type": "Prepaid annual subscription",
                "license_end": None, "license_start": None, "spycloud_shown": True}
        found = self.run_for(expectation=lambda *a, **k: ("ce_only", co, plan), ident=identity(uuid=None))
        self.assertNotIn("identity_only", found)
        self.assertNotEqual(found["differs"], ["plan_not_compared"])


if __name__ == "__main__":
    unittest.main()
