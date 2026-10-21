"""Golden tests for the Case 3 (Surface & Credential Exposure, New) production create body observed in the owner's manual create of
CO-0800 (HAR, 2026-10-21, path A). The observed body is rebuilt below with SYNTHETIC values only (no real name, domain, e-mail, id or
UUID); the contract module must accept exactly that key set and values, and name every drift. Pure and hermetic: no I/O, no network.
"""

from __future__ import annotations

import copy
import unittest

from integration.onboarding import production_case3_contract as c3

INTENDED = {"tenant_name": "Example Klinik gGmbH", "primary_domain": "example-klinik.example",
            "email_domain": "pentera.io", "leaked_domain": "example-klinik-ost.example", "country_code": "DE",
            "organization_email": "owner+x@pentera.io", "first_name": "Ada", "last_name": "Lovelace",
            "operator_name": "Example Operator", "license_end": "2029-09-30"}


def observed_body() -> dict:
    """The observed create body shape (HAR 2026-10-21), synthetic values."""
    return {
        "accountCountryCode": "DE", "accountDomain": "example-klinik.example", "accountName": "Example Klinik gGmbH",
        "accountType": "customer", "additionalNetworks": None, "aiEnabled": False, "alternateDomains": [],
        "automatedDiscoveryEnabled": False,
        "campaignExecutionSettings": {"domainsMultiAttackStackSettings": {"enabled": False, "numOfAssetsPerStack": 1},
                                      "subDomainsMultiAttackStackSettings": {"enabled": False, "numOfAssetsPerStack": 2000}},
        "campaignsTimeoutInHours": 90, "firstName": "Ada", "fullNucleiScanEnabled": True, "lastName": "Lovelace",
        "leakedCredentialsScannedDomains": ["example-klinik-ost.example"], "leakedCredentialsScanningInterval": "NO_SCHEDULE",
        "leakedCredentialsSettings": {"spyCloudSettings": {"enabled": True}},
        "license": {"apiAccessAllowed": True, "assetsNumber": "10000", "domainsNumber": "10000", "enabled": True,
                    "expirationDate": "2029-09-30T07:00:00.000Z", "leakedCredentialsAllowed": True,
                    "licenseType": "prepaid annual subscription", "multipleUsersAllowed": True, "notificationsAllowed": True,
                    "phishingEnabled": False, "provisioningEnabled": True, "reconLevel": None,
                    "startDate": "2026-10-21T14:59:23.797Z", "subDomainsNumber": 500},
        "mfaRequired": True, "operatorAccountNames": ["Example Operator"], "organizationEmail": "owner+x@pentera.io",
        "scanNow": True, "scanningInterval": "MONTHLY", "staticOutboundIpEnabled": False, "subDomains": [],
        "subDomainsReconEnabled": True, "userEmailDomains": ["pentera.io"], "vodBetaEnabled": False,
        "webAiAttackerEnabled": False, "webDictionaryBruteForceEnabled": True, "webDorkingEnabled": False}


class GoldenCreateBodyTests(unittest.TestCase):
    def expected(self, **override):
        return c3.expected_case3_create_payload(dict(INTENDED, **override))

    def test_the_observed_manual_create_body_differs_only_in_the_leaked_credentials_interval(self):
        # The owner's manual create sent NO_SCHEDULE by mistake (2026-10-21); the contract says WEEKLY.
        self.assertEqual(c3.compare_case3_payload(self.expected(), observed_body()), ["leakedCredentialsScanningInterval"])
        self.assertEqual(c3.compare_case3_payload(self.expected(), self._with(("leakedCredentialsScanningInterval",), "WEEKLY")), [])

    def test_the_observed_key_set_is_pinned(self):
        self.assertEqual(len(self.expected()), 46)  # dotted leaves of the observed body
        self.assertEqual(set(self.expected()), set(c3.pc._flatten(observed_body())))

    def test_case3_differs_from_ce_only_where_the_har_showed_it(self):
        want = self.expected()
        self.assertEqual(want["scanningInterval"], "MONTHLY")
        self.assertEqual(want["leakedCredentialsScanningInterval"], "WEEKLY")  # as CE-only; the manual create erred
        self.assertEqual(want["campaignsTimeoutInHours"], 90)
        self.assertIs(want["fullNucleiScanEnabled"], True)
        self.assertIs(want["subDomainsReconEnabled"], True)
        self.assertIs(want["webDictionaryBruteForceEnabled"], True)
        self.assertIs(want["automatedDiscoveryEnabled"], False)
        self.assertIs(want["staticOutboundIpEnabled"], False)
        for allowed in ("notificationsAllowed", "multipleUsersAllowed", "apiAccessAllowed"):
            self.assertIs(want["license." + allowed], True, allowed)
        self.assertEqual((want["license.assetsNumber"], want["license.domainsNumber"], want["license.subDomainsNumber"]),
                         ("10000", "10000", 500))  # assets and domains are STRINGS, subdomains a number

    def test_the_licence_end_keeps_the_calendar_day_not_the_clock(self):
        early = self._with(("license", "expirationDate"), "2029-09-30T00:00:00.000Z")
        early["leakedCredentialsScanningInterval"] = "WEEKLY"
        self.assertEqual(c3.compare_case3_payload(self.expected(), early), [])
        late = self._with(("license", "expirationDate"), "2029-10-01T07:00:00.000Z")
        late["leakedCredentialsScanningInterval"] = "WEEKLY"
        self.assertEqual(c3.compare_case3_payload(self.expected(), late), ["license.expirationDate"])

    def _with(self, path, value):
        body = observed_body()
        node = body
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        return body

    def test_every_drift_is_named_by_field_never_by_value(self):
        for path, value, name in ((("campaignsTimeoutInHours",), 24, "campaignsTimeoutInHours"),
                                  (("staticOutboundIpEnabled",), True, "staticOutboundIpEnabled"),
                                  (("fullNucleiScanEnabled",), False, "fullNucleiScanEnabled"),
                                  (("scanningInterval",), "WEEKLY", "scanningInterval"),
                                  (("license", "assetsNumber"), 10000, "license.assetsNumber"),
                                  (("accountName",), "Example Klinik gGmbH - CE Only", "accountName"),
                                  (("operatorAccountNames",), [], "operatorAccountNames")):
            body = self._with(path, value)
            body["leakedCredentialsScanningInterval"] = "WEEKLY"
            self.assertEqual(c3.compare_case3_payload(self.expected(), body), [name], name)

    def test_an_extra_or_missing_field_fails_closed(self):
        body = observed_body()
        body["leakedCredentialsScanningInterval"] = "WEEKLY"
        body["surpriseFlag"] = True
        self.assertEqual(c3.compare_case3_payload(self.expected(), body), ["unexpected:surpriseFlag"])
        body = observed_body()
        body["leakedCredentialsScanningInterval"] = "WEEKLY"
        del body["mfaRequired"]
        self.assertEqual(c3.compare_case3_payload(self.expected(), body), ["mfaRequired"])
        self.assertEqual(c3.compare_case3_payload(self.expected(), None), sorted(self.expected()))

    def test_scan_now_is_a_boolean_the_contract_records_but_the_owner_still_decides(self):
        self.assertIs(self.expected()["scanNow"], c3.OBSERVED_SCAN_NOW)
        self.assertIs(c3.OBSERVED_SCAN_NOW, True)  # observed in the manual create 2026-10-21; automation build needs an owner decision

    def test_an_ce_only_suffix_or_a_missing_intended_value_is_refused(self):
        with self.assertRaises(ValueError):
            c3.expected_case3_create_payload(dict(INTENDED, tenant_name="Example - CE Only"))
        for key in ("tenant_name", "primary_domain", "email_domain", "leaked_domain", "license_end"):
            broken = {k: v for k, v in INTENDED.items() if k != key}
            with self.assertRaises(ValueError, msg=key):
                c3.expected_case3_create_payload(broken)

    def test_the_operator_may_be_empty_or_one_name(self):
        empty = {k: v for k, v in INTENDED.items() if k != "operator_name"}
        want = c3.expected_case3_create_payload(empty)
        self.assertEqual(want["operatorAccountNames"], [])
        self.assertEqual(self.expected()["operatorAccountNames"], ["Example Operator"])

    def test_the_helper_does_not_mutate_its_input(self):
        before = copy.deepcopy(INTENDED)
        c3.expected_case3_create_payload(INTENDED)
        self.assertEqual(INTENDED, before)


class StoredRowTests(unittest.TestCase):
    """The stored row the create response returned (HAR): module-level contract of the response shape, synthetic values."""

    RESPONSE = {"accountName": "Example Klinik gGmbH", "allowedModules": [], "apiAccess": True, "assetsNumber": 10000,
                "domainsNumber": 10000, "elevatedFeaturesEnabled": False, "expirationDate": 1885000000000,
                "leakedCredentials": True, "leakedCredentialsScannedDomainsNumber": 1, "licenseStatus": None,
                "licenseType": "prepaid annual subscription", "multiUsers": True, "notifications": True,
                "organizationEmail": "owner+x@pentera.io", "phishingEnabled": False, "provisioningEnabled": True,
                "reconLevel": "ALL_ASSETS", "scanQuotaEnforcement": None, "scanningFrequency": "MONTHLY",
                "startDate": 1792000000000, "subDomainsNumber": 500}

    def test_the_create_response_matches_the_stored_summary_contract(self):
        self.assertEqual(c3.compare_case3_response(self.RESPONSE, scanning_frequency="MONTHLY"), [])

    def test_the_response_drift_is_named(self):
        for key, value in (("reconLevel", "NONE"), ("assetsNumber", "10000"), ("scanningFrequency", "WEEKLY"),
                           ("leakedCredentialsScannedDomainsNumber", 2), ("multiUsers", False)):
            bad = dict(self.RESPONSE, **{key: value})
            self.assertEqual(c3.compare_case3_response(bad, scanning_frequency="MONTHLY"), [key], key)

    def test_the_response_does_not_carry_the_account_id_or_uuid(self):
        self.assertFalse(set(self.RESPONSE) & {"id", "accountId", "accountUuid", "uuid"})
        self.assertEqual(c3.RESPONSE_CARRIES_ACCOUNT_IDS, False)  # they come from the tenant Details tab / the list readback


if __name__ == "__main__":
    unittest.main()
