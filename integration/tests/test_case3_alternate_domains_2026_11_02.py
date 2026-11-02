"""Owner go 2026-11-02 (CO-0757, one alternate domain): the Case 3 production create types the CO's alternate domains into
"Alternate Domains (Comma Separated Values)" (the roots the Dev runner already typed and verified on Leonardo Development),
reads them back before Confirm, expects them as the ``alternateDomains`` list of the create body, and searches them in every
duplicate check. A CO without alternate domains keeps the verified-blank control; subdomains are still refused.
Hermetic: fake page, fake sources; nothing reads Salesforce or BackOffice.
"""

from __future__ import annotations

import unittest

from integration.onboarding import production_case3_contract as c3
from integration.onboarding import production_dry_run as pdr
from integration.tests import test_case3_dry_run_runner as case3
import tools.attended_ce_only_playwright as runner

ALT = "alt-example-klinik.example"
LABEL = pdr.ALTERNATE_DOMAINS_LABEL


def plan_for(source):
    return runner._pdr_plan(runner.CASE3_ENGINE, source, case3.RUN_DAY)


class MappingTests(unittest.TestCase):
    def test_an_alternate_domain_is_typed_and_no_longer_verified_blank(self):
        intended = pdr.map_plan(plan_for(case3.make_case3_source(alternates=(ALT,))), runner.SELECT_NAMES, runner.EARLY_SELECTS)
        self.assertEqual(dict(intended.texts)[LABEL], ALT)
        self.assertNotIn(LABEL, intended.blanks)
        self.assertIn("SubDomains (Comma Separated Values)", intended.blanks)
        self.assertIn("text.alternate_domains", pdr.field_keys(intended))

    def test_without_alternate_domains_the_control_stays_verified_blank(self):
        intended = pdr.map_plan(plan_for(case3.make_case3_source()), runner.SELECT_NAMES, runner.EARLY_SELECTS)
        self.assertNotIn(LABEL, dict(intended.texts))
        self.assertEqual(intended.blanks, pdr.BLANK_LABELS)

    def test_an_empty_value_in_the_plan_is_blank(self):
        plan = plan_for(case3.make_case3_source())
        plan["texts"][LABEL] = "  "
        self.assertIn(LABEL, pdr.map_plan(plan, runner.SELECT_NAMES, runner.EARLY_SELECTS).blanks)

    def test_a_malformed_value_is_refused(self):
        for value in ("a.example b.example", "a.example, a.example", "A.Example", "a.example,", "http://a.example"):
            with self.subTest(value=value):
                plan = plan_for(case3.make_case3_source())
                plan["texts"][LABEL] = value
                with self.assertRaises(pdr.MappingError) as raised:
                    pdr.map_plan(plan, runner.SELECT_NAMES, runner.EARLY_SELECTS)
                self.assertEqual(str(raised.exception), "alternate_domains_invalid")

    def test_the_parser_keeps_the_typed_order(self):
        self.assertEqual(pdr.alternate_domains("b.example, a.example"), ("b.example", "a.example"))
        self.assertIsNone(pdr.alternate_domains(None))

    def test_subdomains_are_still_refused(self):
        with self.assertRaises(pdr.MappingError) as raised:
            plan_for(case3.make_case3_source(alternates=(ALT,), subdomains=("app." + case3.MAIN,)))
        self.assertEqual(str(raised.exception), "case3_subdomains_not_supported")


class ContractTests(unittest.TestCase):
    def intended(self, alternates=(ALT,)):
        return c3.case3_intended(tenant_name=case3.NAME, main_domain=case3.MAIN, ce_email_domains=case3.EMAIL,
                                 user_email_domain="pentera.io", license_end="2029-09-30", tier="Go",
                                 alternate_domains=alternates)

    def test_the_create_body_lists_the_alternate_domains(self):
        self.assertEqual(c3.expected_case3_create_payload(self.intended())["alternateDomains"], [ALT])
        self.assertEqual(c3.expected_case3_create_payload(self.intended(()))["alternateDomains"], [])

    def test_the_body_comparison_passes_only_the_exact_list(self):
        expected = {"alternateDomains": [ALT]}
        self.assertEqual(c3.compare_case3_payload(expected, {"alternateDomains": [ALT]}), [])
        self.assertEqual(c3.compare_case3_payload(expected, {"alternateDomains": []}), ["alternateDomains"])
        self.assertEqual(c3.compare_case3_payload(expected, {"alternateDomains": ALT}), ["alternateDomains"])

    def test_bad_alternate_domains_are_refused(self):
        for bad in ((case3.MAIN,), (ALT, ALT), ("not a domain",), ALT, ("Alt.Example",)):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as raised:
                self.intended(bad)
            self.assertEqual(str(raised.exception), "alternate_domains_invalid")

    def test_an_intended_without_the_key_still_sends_an_empty_list(self):
        built = self.intended(())
        del built["alternate_domains"]
        self.assertEqual(c3.expected_case3_create_payload(built)["alternateDomains"], [])


class LiveIntendedTests(unittest.TestCase):
    def test_the_live_values_carry_the_typed_alternate_domains(self):
        source = case3.make_case3_source(alternates=(ALT,))
        self.assertEqual(runner.case3_live_intended(source, plan_for(source), case3.RUN_DAY)["alternate_domains"], (ALT,))

    def test_a_plan_that_does_not_type_the_co_s_alternate_domains_is_refused(self):
        source = case3.make_case3_source(alternates=(ALT,))
        with self.assertRaises(ValueError) as raised:
            runner.case3_live_intended(source, plan_for(case3.make_case3_source()), case3.RUN_DAY)
        self.assertEqual(str(raised.exception), "case3_tier_values_mismatch")

    def test_every_duplicate_lookup_includes_the_alternate_domains(self):
        source = case3.make_case3_source(alternates=(ALT,))
        self.assertEqual(runner._co_domains(runner.CASE3_ROUTE, source), (case3.MAIN, case3.EMAIL, ALT))
        names = [name for name, _ in runner.CASE3_ROUTE.duplicate_lookups(source)]
        self.assertEqual(names, ["tenant_name", "primary_domain", "ce_email_domain", "alternate_domain_1"])
        self.assertEqual(runner.CASE3_ROUTE.lookup_domain(source, "alternate_domain_1"), ALT)


class DryRunTests(unittest.TestCase):
    def test_a_dry_run_types_reads_back_and_searches_the_alternate_domain(self):
        h = case3.C3Harness(self)
        h.source = case3.make_case3_source(alternates=(ALT,))
        out = h.go()
        self.assertEqual((out["result"], out["outcome"]), ("dry_run_passed", "dry_run_passed"))
        self.assertEqual(h.page.texts[LABEL].value, ALT)
        self.assertIn(ALT, h.page.search.asked)  # the BackOffice duplicate search asked for it
        self.assertNotIn("confirm", [kind for kind in h.page.clicks])

    def test_a_dry_run_stops_when_the_alternate_domain_belongs_to_another_tenant(self):
        h = case3.C3Harness(self, search={ALT: case3.hit(domain=ALT)})
        h.source = case3.make_case3_source(alternates=(ALT,))
        out = h.go()
        self.assertEqual(out["outcome"], "refused")
        self.assertNotEqual(h.page.texts[LABEL].value, ALT)  # stopped before the form was filled


if __name__ == "__main__":
    unittest.main()
