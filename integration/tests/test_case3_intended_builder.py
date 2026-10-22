"""Path B step (c): the pure builder of the Case 3 ``intended`` values, checked against the golden create body (owner go 2026-10-22).

Tier rule (owner 2026-10-22): Go = MONTHLY and 500 subdomains, Prime = WEEKLY and 1000; any other tier fails closed.
The builder does no I/O; it is the only place the tier turns into the interval and the subdomain count, and the contract module
reads both from the ``intended`` values (defaults MONTHLY/500 keep the observed CO-0800 golden body). Values are synthetic.
"""

import copy
import unittest

from integration.onboarding import production_case3_contract as c3
from integration.tests.test_case3_create_contract import INTENDED, observed_body

FACTS = dict(tenant_name="Example Klinik gGmbH", main_domain="Example-Klinik.example",
             ce_email_domains="@Example-Klinik-Ost.example", user_email_domain="pentera.io",
             license_end="2029-09-30", tier="Go", country_code="DE", operator_name="Example Operator",
             organization_email="owner+x@pentera.io", first_name="Ada", last_name="Lovelace")


def build(**override):
    return c3.case3_intended(**{**FACTS, **override})


def plain(expected):
    """The expected values that are exact (a shape placeholder has no equality of its own)."""
    return {key: value for key, value in expected.items() if not isinstance(value, c3.pc._Shape)}


def refusal(**override):
    with unittest.TestCase().assertRaises(ValueError) as caught:
        build(**override)
    return str(caught.exception)


class BuilderGoldenTests(unittest.TestCase):
    def test_the_go_tier_rebuilds_the_observed_co_0800_body_except_the_leaked_credentials_interval(self):
        expected = c3.expected_case3_create_payload(build())
        self.assertEqual(plain(expected), plain(c3.expected_case3_create_payload(INTENDED)))
        self.assertEqual(c3.compare_case3_payload(expected, observed_body()), ["leakedCredentialsScanningInterval"])

    def test_the_builder_output_has_exactly_the_keys_the_contract_reads(self):
        self.assertEqual(set(build()), {"tenant_name", "primary_domain", "email_domain", "leaked_domain", "license_end",
                                        "country_code", "operator_name", "organization_email", "first_name", "last_name",
                                        "scanning_interval", "subdomains_number"})

    def test_the_leaked_credentials_domain_is_the_ce_email_domain_and_domains_are_folded(self):
        built = build()
        self.assertEqual((built["primary_domain"], built["email_domain"], built["leaked_domain"]),
                         ("example-klinik.example", "pentera.io", "example-klinik-ost.example"))

    def test_the_tier_rule(self):
        go, prime = build(tier="Go"), build(tier="Prime")
        self.assertEqual((go["scanning_interval"], go["subdomains_number"]), ("MONTHLY", 500))
        self.assertEqual((prime["scanning_interval"], prime["subdomains_number"]), ("WEEKLY", 1000))

    def test_the_prime_tier_flows_into_the_expected_body_and_nothing_else_changes(self):
        go = c3.expected_case3_create_payload(build(tier="Go"))
        prime = c3.expected_case3_create_payload(build(tier="Prime"))
        changed = sorted(key for key in plain(go) if go[key] != prime[key])
        self.assertEqual(changed, ["license.subDomainsNumber", "scanningInterval"])
        self.assertEqual(set(go), set(prime))
        self.assertEqual((prime["scanningInterval"], prime["license.subDomainsNumber"]), ("WEEKLY", 1000))
        self.assertEqual(prime["leakedCredentialsScanningInterval"], "WEEKLY")  # always weekly, whatever the tier

    def test_the_prime_tier_flows_into_the_readback_row_and_the_response(self):
        row = c3.expected_case3_readback_row(build(tier="Prime"))
        self.assertEqual((row["scanningInterval"], row["scanningFrequency"], row["accountLicense.subDomainsNumber"]),
                         ("WEEKLY", "WEEKLY", 1000))
        response = {**c3.expected_case3_response("WEEKLY", 1000), "accountName": "x", "organizationEmail": "x",
                    "expirationDate": 1, "startDate": 1}
        self.assertEqual((response["scanningFrequency"], response["subDomainsNumber"]), ("WEEKLY", 1000))
        self.assertEqual(c3.compare_case3_response(response, "WEEKLY", 1000), [])
        self.assertEqual(c3.compare_case3_response(response), ["scanningFrequency", "subDomainsNumber"])

    def test_the_contract_defaults_stay_monthly_and_500(self):
        want = c3.expected_case3_create_payload(INTENDED)
        self.assertEqual((want["scanningInterval"], want["license.subDomainsNumber"]), ("MONTHLY", 500))
        row = c3.expected_case3_readback_row(INTENDED)
        self.assertEqual((row["scanningInterval"], row["accountLicense.subDomainsNumber"]), ("MONTHLY", 500))

    def test_an_unknown_tier_fails_closed(self):
        for tier in ("go", "PRIME", "Go ", "Core Plus", "", None, 1, ["Go"]):
            with self.subTest(tier=tier):
                self.assertEqual(refusal(tier=tier), "tier_unknown")

    def test_the_contract_refuses_an_interval_or_count_outside_the_tier_rule(self):
        for key, value in (("scanning_interval", "NO_SCHEDULE"), ("scanning_interval", "monthly"), ("scanning_interval", None),
                           ("subdomains_number", 750), ("subdomains_number", "500"), ("subdomains_number", True),
                           ("subdomains_number", None)):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    c3.expected_case3_create_payload({**INTENDED, key: value})
                with self.assertRaises(ValueError):
                    c3.expected_case3_readback_row({**INTENDED, key: value})

    def test_a_mixed_tier_pair_is_refused(self):
        for pair in (("MONTHLY", 1000), ("WEEKLY", 500)):
            with self.subTest(pair=pair):
                with self.assertRaises(ValueError):
                    c3.expected_case3_create_payload({**INTENDED, "scanning_interval": pair[0], "subdomains_number": pair[1]})


class BuilderRefusalTests(unittest.TestCase):
    def test_tenant_name(self):
        for name in (None, "", "  ", "bad\nname", 5, "Example - CE Only"):
            with self.subTest(name=name):
                self.assertIn(refusal(tenant_name=name), ("tenant_name_invalid", "tenant_name_has_ce_only_suffix"))
        self.assertEqual(refusal(tenant_name="Example - CE Only"), "tenant_name_has_ce_only_suffix")
        self.assertEqual(refusal(tenant_name=None), "tenant_name_invalid")

    def test_main_domain(self):
        for domain in (None, "", "nodot", "two domains.example", "a.example,b.example", "http://a.example", 3):
            with self.subTest(domain=domain):
                self.assertEqual(refusal(main_domain=domain), "main_domain_invalid")

    def test_exactly_one_email_domain(self):
        for raw in (None, "", "@a.example, @b.example", "a.example;b.example", "not a domain", "@nodot", 4):
            with self.subTest(raw=raw):
                self.assertEqual(refusal(ce_email_domains=raw), "email_domain_not_single")
        for raw in ("@ce.example", "ce.example", " @CE.example ", ["ce.example"], ("@ce.example",)):
            with self.subTest(raw=raw):
                self.assertEqual(build(ce_email_domains=raw)["leaked_domain"], "ce.example")
        self.assertEqual(refusal(ce_email_domains=["a.example", "b.example"]), "email_domain_not_single")

    def test_the_user_email_domain_is_one_valid_domain(self):
        for raw in (None, "", "a.example, b.example", "nodot", 4):
            with self.subTest(raw=raw):
                self.assertEqual(refusal(user_email_domain=raw), "user_email_domain_invalid")

    def test_licence_end(self):
        for day in (None, "", "30.09.2029", "2029-13-45", "2029-09-30T07:00:00Z", 20290930):
            with self.subTest(day=day):
                self.assertEqual(refusal(license_end=day), "license_end_invalid")

    def test_country_and_operator_are_optional_but_never_malformed(self):
        built = build(country_code=None, operator_name=None, organization_email=None, first_name=None, last_name=None)
        self.assertEqual({built[k] for k in ("country_code", "operator_name", "organization_email", "first_name", "last_name")},
                         {None})
        self.assertEqual(build(operator_name="  ")["operator_name"], None)
        for code in ("de", "DEU", "D", 5):
            with self.subTest(code=code):
                self.assertEqual(refusal(country_code=code), "country_code_invalid")
        self.assertEqual(refusal(operator_name="bad\nname"), "operator_name_invalid")

    def test_reasons_never_contain_the_values(self):
        secret = "secret-value.example"
        for override in ({"main_domain": secret + " x"}, {"ce_email_domains": secret + ", other.example"},
                         {"tenant_name": secret + " - CE Only"}, {"tier": secret}):
            with self.subTest(override=override):
                self.assertNotIn("secret-value", refusal(**override))

    def test_the_inputs_are_not_mutated_and_the_builder_is_deterministic(self):
        facts = copy.deepcopy(FACTS)
        facts["ce_email_domains"] = ["ce.example"]
        before = copy.deepcopy(facts)
        first = c3.case3_intended(**facts)
        self.assertEqual(facts, before)
        self.assertEqual(first, c3.case3_intended(**facts))


class BuilderIsolationTests(unittest.TestCase):
    def test_the_builder_touches_no_lock_runner_or_external_module(self):
        import inspect
        source = inspect.getsource(c3.case3_intended)
        for word in ("open(", "subprocess", "requests", "urllib", "playwright", "environ", "session_readiness"):
            self.assertNotIn(word, source)
        from integration.onboarding import session_readiness
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, False)


if __name__ == "__main__":
    unittest.main()
