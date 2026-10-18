"""Production CE-only create/readback contract (docs/49): pure checks on synthetic data. No browser, no network."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from integration.onboarding import production_contract as contract
from integration.onboarding import production_probe
import tools.attended_ce_only_playwright as runner

NAME = "Acme Test - CE Only"
DOMAIN = "acme-test.example"
INTERNAL = "internal.example"
UUID = "0123456789abcdef0123456789abcdef"
OTHER_UUID = "fedcba9876543210fedcba9876543210"
ROW_ID = "0123456789abcdef01234567"


def intended(**over):
    data = {"tenant_name": NAME, "primary_domain": DOMAIN, "email_domain": INTERNAL, "country_code": "CL",
            "first_name": "Test", "last_name": "User", "organization_email": "test.user+acme@" + INTERNAL,
            "license_start": date(2026, 10, 16), "license_end": date(2027, 5, 20)}
    data.update(over)
    return data


def unflatten(flat):
    out = {}
    for key, value in flat.items():
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def create_flat():
    flat = {
        "accountName": NAME, "accountDomain": DOMAIN, "accountType": "customer", "accountCountryCode": "CL",
        "organizationEmail": "test.user+acme@" + INTERNAL, "firstName": "Test", "lastName": "User", "mfaRequired": True,
        "alternateDomains": [], "subDomains": [], "additionalNetworks": None, "userEmailDomains": [INTERNAL],
        "operatorAccountNames": [], "scanningInterval": "NO_SCHEDULE", "scanNow": False,
        "leakedCredentialsScanningInterval": "WEEKLY", "leakedCredentialsScannedDomains": [DOMAIN],
        "campaignsTimeoutInHours": 24,
        "license.assetsNumber": "1", "license.domainsNumber": "1", "license.subDomainsNumber": 1,
        "license.licenseType": "prepaid annual subscription", "license.startDate": "2026-10-16T12:00:00.000Z",
        "license.expirationDate": "2027-05-20T07:00:00.000Z", "license.enabled": True,
        "license.notificationsAllowed": False, "license.multipleUsersAllowed": False, "license.apiAccessAllowed": False,
        "license.phishingEnabled": False, "license.provisioningEnabled": True, "license.leakedCredentialsAllowed": True,
        "license.reconLevel": None,
        "campaignExecutionSettings.domainsMultiAttackStackSettings.enabled": False,
        "campaignExecutionSettings.domainsMultiAttackStackSettings.numOfAssetsPerStack": 5,
        "campaignExecutionSettings.subDomainsMultiAttackStackSettings.enabled": False,
        "campaignExecutionSettings.subDomainsMultiAttackStackSettings.numOfAssetsPerStack": 5,
        "leakedCredentialsSettings.spyCloudSettings.enabled": True,
    }
    for name in contract.CREATE_FLAGS_OFF:
        flat[name] = False
    return flat


def row_flat(**over):
    flat = {
        "accountName": NAME, "accountDomain": DOMAIN, "accountType": "Customer", "accountCountryCode": "CL",
        "accountUuid": UUID, "id": ROW_ID, "scanningInterval": "NO_SCHEDULE", "accountLicense.scanningFrequency": "NO_SCHEDULE",
        "leakedCredentialsScanningInterval": "WEEKLY", "accountLicense.reconLevel": "ALL_ASSETS", "userEmailDomains": [INTERNAL],
        "accountLicense.assetsNumber": 1, "accountLicense.domainsNumber": 1, "accountLicense.subDomainsNumber": 1,
        "accountLicense.leakedCredentialsAllowed": True, "accountLicense.provisioningEnabled": True,
        "accountLicense.notificationsAllowed": False, "accountLicense.multipleUsersAllowed": False,
        "accountLicense.apiAccessAllowed": False, "accountLicense.phishingEnabled": False,
        "leakedCredentialsSettings.spyCloudSettings.enabled": True,
    }
    for name, paths in contract.ROW_FLAG_PATHS.items():
        flat[paths[0]] = False
    flat.update(over)
    return flat


class PinnedAgainstRunnerTests(unittest.TestCase):
    """The contract constants are the Dev runner's values, not a second copy that can drift."""

    def setUp(self):
        self.source = runner.CeFillSource(
            reference="CO-0797", source_revision="2026-10-16T00:00:00.000+0000", account_id="001AAAAAAAAAAAAAAA",
            account_name="Acme Test", email_domain=DOMAIN, country="Chile", subscription_start=date(2026, 5, 21),
            subscription_end=date(2027, 5, 20), tenant_name=NAME, primary_user_alias="acmetest")
        self.plan = runner.build_ce_only_fill(self.source, date(2026, 10, 16))

    def test_constants_equal_dev_plan_values(self):
        selects = self.plan["selects"]
        self.assertEqual(selects["Account Type"].casefold(), contract.ACCOUNT_TYPE_CREATE)
        self.assertEqual(selects["Type"].casefold(), contract.LICENSE_TYPE)
        self.assertEqual(selects["Leaked Credentials scanning interval"].upper(), contract.LC_INTERVAL)
        self.assertEqual(selects["Scanning interval"], "None")
        self.assertEqual(contract.SCANNING_INTERVAL, "NO_SCHEDULE")
        for label in ("Number of assets", "Number of domains", "Number of subdomains"):
            self.assertEqual(runner.CE_LICENSE_QUANTITIES[label], contract.LICENSE_COUNT_TEXT)
        self.assertTrue(NAME.endswith(contract.NAME_SUFFIX))
        self.assertTrue(runner.ce_only_names("Acme Test").tenant_name.endswith(contract.NAME_SUFFIX))
        boxes = self.plan["checkboxes"]
        self.assertTrue(boxes["leakedCredentialsAllowed"] and boxes["provisioningEnabled"] and boxes["mfaRequired"])
        for key in ("notificationsAllowed", "multipleUsersAllowed", "apiAccessAllowed", "phishingEnabled"):
            self.assertIs(boxes[key], False)

    def test_runner_intended_comes_from_the_dev_plan(self):
        got = runner.ce_production_intended(self.source, date(2026, 10, 16))
        self.assertEqual(got["tenant_name"], NAME)
        self.assertEqual(got["primary_domain"], DOMAIN)
        self.assertEqual(got["email_domain"], runner.CE_USER_EMAIL_DOMAIN)
        self.assertEqual(got["license_start"], date(2026, 10, 16))
        self.assertEqual(got["license_end"], date(2027, 5, 20))
        payload = contract.expected_create_payload(got)
        self.assertEqual(payload["userEmailDomains"], [runner.CE_USER_EMAIL_DOMAIN])
        self.assertEqual(payload["firstName"], runner.CE_PRIMARY_USER_FIRST_NAME)


class ExpectedCreatePayloadTests(unittest.TestCase):
    def test_matches_docs49_section_1(self):
        expected = contract.expected_create_payload(intended())
        self.assertEqual(expected["accountType"], "customer")
        self.assertEqual(expected["accountName"], NAME)
        self.assertEqual(expected["userEmailDomains"], [INTERNAL])
        self.assertEqual(expected["license.assetsNumber"], "1")
        self.assertEqual(expected["license.domainsNumber"], "1")
        self.assertIs(type(expected["license.subDomainsNumber"]), int)
        self.assertEqual(expected["license.subDomainsNumber"], 1)
        self.assertEqual(expected["license.licenseType"], "prepaid annual subscription")
        self.assertEqual(expected["scanningInterval"], "NO_SCHEDULE")
        self.assertEqual(expected["leakedCredentialsScanningInterval"], "WEEKLY")
        self.assertEqual(expected["leakedCredentialsScannedDomains"], [DOMAIN])
        self.assertEqual(expected["campaignsTimeoutInHours"], 24)
        for name in contract.CREATE_FLAGS_OFF:
            self.assertIs(expected[name], False)
        for name in ("license.provisioningEnabled", "license.leakedCredentialsAllowed", "mfaRequired",
                     "leakedCredentialsSettings.spyCloudSettings.enabled", "license.enabled"):
            self.assertIs(expected[name], True)
        self.assertIsNone(expected["license.reconLevel"])

    def test_full_sample_body_has_no_mismatch(self):
        expected = contract.expected_create_payload(intended())
        self.assertEqual(contract.compare_payload(expected, unflatten(create_flat())), [])

    def test_name_must_be_ce_only_and_email_domain_internal(self):
        with self.assertRaises(ValueError):
            contract.expected_create_payload(intended(tenant_name="Acme Test"))
        with self.assertRaises(ValueError):
            contract.expected_create_payload(intended(email_domain=DOMAIN))
        with self.assertRaises(ValueError):
            contract.expected_create_payload(intended(email_domain="a.example,b.example"))
        with self.assertRaises(ValueError):
            contract.expected_create_payload(intended(primary_domain=""))


class ComparePayloadTests(unittest.TestCase):
    def setUp(self):
        self.expected = contract.expected_create_payload(intended())

    def observed(self, **change):
        flat = create_flat()
        for key, value in change.items():
            flat[key.replace("__", ".")] = value
        return unflatten(flat)

    def test_string_versus_number_is_a_mismatch(self):
        self.assertEqual(contract.compare_payload(self.expected, self.observed(license__assetsNumber=1)),
                         ["license.assetsNumber"])
        self.assertEqual(contract.compare_payload(self.expected, self.observed(license__subDomainsNumber="1")),
                         ["license.subDomainsNumber"])

    def test_bool_is_not_a_number(self):
        self.assertEqual(contract.compare_payload(self.expected, self.observed(campaignsTimeoutInHours=True)),
                         ["campaignsTimeoutInHours"])

    def test_wrong_flags_are_named(self):
        got = contract.compare_payload(self.expected, self.observed(aiEnabled=True, license__provisioningEnabled=False))
        self.assertEqual(got, ["aiEnabled", "license.provisioningEnabled"])

    def test_missing_key_is_a_mismatch(self):
        body = self.observed()
        del body["scanNow"]
        self.assertEqual(contract.compare_payload(self.expected, body), ["scanNow"])

    def test_unknown_extra_keys_reported_as_unexpected(self):
        body = self.observed()
        body["surprise"] = "x"
        body["license"]["extraThing"] = 3
        self.assertEqual(contract.compare_payload(self.expected, body), ["unexpected:license.extraThing", "unexpected:surprise"])

    def test_customer_domain_as_email_domain_is_a_mismatch(self):
        self.assertEqual(contract.compare_payload(self.expected, self.observed(userEmailDomains=[DOMAIN])),
                         ["userEmailDomains"])
        self.assertEqual(contract.compare_payload(self.expected, self.observed(userEmailDomains=[INTERNAL, DOMAIN])),
                         ["userEmailDomains"])

    def test_dates_must_be_iso_and_expiration_the_intended_day(self):
        self.assertEqual(contract.compare_payload(self.expected, self.observed(license__startDate="16/10/2026")),
                         ["license.startDate"])
        self.assertEqual(contract.compare_payload(self.expected, self.observed(license__expirationDate="2027-05-21T07:00:00.000Z")),
                         ["license.expirationDate"])
        self.assertEqual(contract.compare_payload(self.expected, self.observed(license__expirationDate=1811000000000)),
                         ["license.expirationDate"])

    def test_non_mapping_observed_body_is_all_mismatch(self):
        self.assertTrue(contract.compare_payload(self.expected, None))
        self.assertTrue(contract.compare_payload(self.expected, []))

    def test_result_holds_names_only(self):
        text = json.dumps(contract.compare_payload(self.expected, self.observed(accountName="Other Name - CE Only",
                                                                                 organizationEmail="x@y.example")))
        self.assertNotIn("Other Name", text)
        self.assertNotIn("y.example", text)


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.expected = contract.expected_readback_row(intended())

    def test_good_row_flat_and_nested(self):
        for row in (row_flat(), unflatten(row_flat())):
            mismatches, uuid_ok = contract.compare_readback(self.expected, row)
            self.assertEqual((mismatches, uuid_ok), ([], True))

    def test_expected_matches_docs49_section_3(self):
        e = self.expected
        self.assertEqual(e["accountType"], "Customer")
        self.assertEqual(e["scanningInterval"], "NO_SCHEDULE")
        self.assertEqual(e["leakedCredentialsScanningInterval"], "WEEKLY")
        self.assertEqual(e["reconLevel"], "ALL_ASSETS")
        self.assertEqual(e["scanningFrequency"], "NO_SCHEDULE")
        for name in ("accountLicense.assetsNumber", "accountLicense.domainsNumber", "accountLicense.subDomainsNumber"):
            self.assertEqual(e[name], 1)
            self.assertIs(type(e[name]), int)
        self.assertEqual(e["userEmailDomains"], [INTERNAL])
        self.assertEqual(e["accountName"], NAME)
        self.assertEqual(e["accountDomain"], DOMAIN)

    def test_strings_for_license_numbers_are_a_mismatch(self):
        mismatches, _ = contract.compare_readback(self.expected, row_flat(**{"accountLicense.assetsNumber": "1"}))
        self.assertEqual(mismatches, ["accountLicense.assetsNumber"])

    def test_wrong_values_named(self):
        row = row_flat(accountType="customer", **{"accountLicense.reconLevel": "NONE", "accountLicense.phishingEnabled": True,
                                                                     "leakedCredentialsSettings.spyCloudSettings.enabled": False})
        mismatches, _ = contract.compare_readback(self.expected, row)
        self.assertEqual(sorted(mismatches), ["accountLicense.phishingEnabled", "accountType",
                                              "leakedCredentialsSettings.spyCloudSettings.enabled", "reconLevel"])

    def test_wrong_flag_in_row(self):
        first = next(iter(contract.ROW_FLAG_PATHS))
        row = row_flat(**{contract.ROW_FLAG_PATHS[first][0]: True})
        self.assertEqual(contract.compare_readback(self.expected, row)[0], [first])

    def test_missing_field_is_a_mismatch(self):
        row = row_flat()
        del row["accountLicense.scanningFrequency"]
        self.assertEqual(contract.compare_readback(self.expected, row)[0], ["scanningFrequency"])

    def test_name_domain_and_email_domains_exact(self):
        row = row_flat(accountName="acme test - CE Only", accountDomain="other.example", userEmailDomains=[DOMAIN])
        self.assertEqual(sorted(contract.compare_readback(self.expected, row)[0]),
                         ["accountDomain", "accountName", "userEmailDomains"])

    def test_country_code_exact_when_given_else_shape(self):
        row = row_flat(accountCountryCode="AR")
        self.assertEqual(contract.compare_readback(self.expected, row)[0], ["accountCountryCode"])
        shaped = contract.expected_readback_row(intended(country_code=None))
        self.assertEqual(contract.compare_readback(shaped, row_flat(accountCountryCode="AR"))[0], [])
        self.assertEqual(contract.compare_readback(shaped, row_flat(accountCountryCode="Chile"))[0], ["accountCountryCode"])

    def test_uuid_ok_only_for_32_lowercase_hex(self):
        for value, ok in ((UUID, True), (UUID.upper(), False), (UUID[:-1], False), (UUID + "0", False), (None, False),
                          (123, False), ("g" * 32, False)):
            with self.subTest(value=value):
                self.assertIs(contract.compare_readback(self.expected, row_flat(accountUuid=value))[1], ok)

    def test_result_never_contains_the_uuid_or_values(self):
        out = contract.compare_readback(self.expected, row_flat(accountUuid=OTHER_UUID, accountName="Zed"))
        text = json.dumps(out)
        self.assertNotIn(OTHER_UUID, text)
        self.assertNotIn("Zed", text)

    def test_non_mapping_row(self):
        mismatches, uuid_ok = contract.compare_readback(self.expected, None)
        self.assertTrue(mismatches)
        self.assertFalse(uuid_ok)

    def test_salesforce_uuid_match(self):
        self.assertIs(contract.salesforce_uuid_matches(UUID, UUID), True)
        self.assertIs(contract.salesforce_uuid_matches(UUID.upper(), UUID), True)
        self.assertIs(contract.salesforce_uuid_matches(OTHER_UUID, UUID), False)
        self.assertIsNone(contract.salesforce_uuid_matches("", UUID))
        self.assertIsNone(contract.salesforce_uuid_matches(None, UUID))
        self.assertIs(contract.salesforce_uuid_matches(UUID, None), False)
        self.assertIs(contract.salesforce_uuid_matches(UUID, "zz"), False)

    def test_salesforce_uuid_match_normalises_hyphens_and_case_on_both_sides(self):
        hyphenated = "-".join((UUID[:8], UUID[8:12], UUID[12:16], UUID[16:20], UUID[20:]))
        self.assertIs(contract.salesforce_uuid_matches(hyphenated, UUID), True)
        self.assertIs(contract.salesforce_uuid_matches(hyphenated.upper(), UUID), True)
        self.assertIs(contract.salesforce_uuid_matches(UUID, hyphenated.upper()), True)
        self.assertIs(contract.salesforce_uuid_matches(" " + hyphenated + " ", UUID), True)
        self.assertIs(contract.salesforce_uuid_matches(hyphenated, OTHER_UUID), False)
        self.assertIs(contract.salesforce_uuid_matches(UUID, UUID[:-1] + "z"), False)   # tenant side must still be 32 hex
        self.assertIs(contract.salesforce_uuid_matches(UUID, UUID[:-1]), False)


class SelectUniqueRowTests(unittest.TestCase):
    def row(self, **over):
        data = {"accountName": NAME, "accountDomain": DOMAIN, "accountUuid": UUID, "id": ROW_ID}
        data.update(over)
        return data

    def test_single_exact_row(self):
        row, code = contract.select_unique_row([self.row()], NAME, DOMAIN)
        self.assertIsNone(code)
        self.assertEqual(row["accountUuid"], UUID)

    def test_two_identical_rows_same_uuid_are_one(self):
        row, code = contract.select_unique_row([self.row(), self.row()], NAME, DOMAIN)
        self.assertIsNone(code)
        self.assertIsNotNone(row)

    def test_two_distinct_tenants_refuse(self):
        rows = [self.row(), self.row(accountUuid=OTHER_UUID, id="aaaaaaaaaaaaaaaaaaaaaaaa")]
        self.assertEqual(contract.select_unique_row(rows, NAME, DOMAIN), (None, "multiple_matches"))

    def test_same_uuid_but_different_id_refuses(self):
        rows = [self.row(), self.row(id="aaaaaaaaaaaaaaaaaaaaaaaa")]
        self.assertEqual(contract.select_unique_row(rows, NAME, DOMAIN), (None, "multiple_matches"))

    def test_rows_without_uuid_are_not_treated_as_one(self):
        rows = [self.row(accountUuid=None, id=None), self.row(accountUuid=None, id=None)]
        self.assertEqual(contract.select_unique_row(rows, NAME, DOMAIN), (None, "multiple_matches"))

    def test_no_match(self):
        self.assertEqual(contract.select_unique_row([], NAME, DOMAIN), (None, "no_match"))
        self.assertEqual(contract.select_unique_row([self.row(accountName="Other", accountDomain="o.example")], NAME, DOMAIN),
                         (None, "no_match"))
        self.assertEqual(contract.select_unique_row([self.row(accountName="Other")], NAME, DOMAIN), (None, "no_match"))

    def test_name_only_match_refuses(self):
        self.assertEqual(contract.select_unique_row([self.row(accountDomain="other.example")], NAME, DOMAIN),
                         (None, "name_only_match"))

    def test_exact_row_plus_name_only_duplicate_refuses(self):
        rows = [self.row(), self.row(accountDomain="other.example", accountUuid=OTHER_UUID)]
        self.assertEqual(contract.select_unique_row(rows, NAME, DOMAIN), (None, "name_only_match"))

    def test_match_is_exact_not_normalised(self):
        for name, domain in ((NAME.upper(), DOMAIN), (NAME, DOMAIN.upper()), (NAME + " ", DOMAIN)):
            with self.subTest(name=name, domain=domain):
                row, code = contract.select_unique_row([self.row(accountName=name, accountDomain=domain)], NAME, DOMAIN)
                self.assertIsNone(row)
                self.assertIsNotNone(code)

    def test_case_variant_name_refuses_even_beside_an_exact_row(self):
        variant = self.row(accountName=NAME.upper(), accountUuid=OTHER_UUID, id="aaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(contract.select_unique_row([variant], NAME, DOMAIN), (None, "case_variant_match"))
        self.assertEqual(contract.select_unique_row([self.row(), variant], NAME, DOMAIN), (None, "case_variant_match"))
        self.assertEqual(contract.select_unique_row([variant, self.row()], NAME, DOMAIN), (None, "case_variant_match"))
        self.assertEqual(contract.select_unique_row([self.row(accountName=NAME.lower(), accountDomain="o.example")], NAME, DOMAIN),
                         (None, "case_variant_match"))

    def test_bad_input_shapes_fail_closed(self):
        self.assertEqual(contract.select_unique_row(None, NAME, DOMAIN), (None, "no_match"))
        self.assertEqual(contract.select_unique_row(["x", 3, None], NAME, DOMAIN), (None, "no_match"))
        self.assertEqual(contract.select_unique_row([self.row()], "", DOMAIN), (None, "no_match"))
        self.assertEqual(contract.select_unique_row([self.row()], NAME, ""), (None, "no_match"))


class SearchBodyAndScriptTests(unittest.TestCase):
    def test_body_grammar(self):
        body = contract.build_readback_body(NAME)
        data = body["tableServerData"]
        self.assertEqual(data["filters"], {"and": [{"method": "search", "value": NAME}]})
        self.assertEqual(data["sort"], {"direction": "asc", "key": "accountName"})
        self.assertEqual(data["offset"], 0)
        self.assertIsInstance(data["items_per_page"], int)
        self.assertIsInstance(data["unique_fields"], list)
        self.assertNotIn("projection", data)  # docs/48: no projection, or every compared field
        with self.assertRaises(ValueError):
            contract.build_readback_body("  ")

    def test_the_readback_reducer_names_no_secret_store_and_runs_no_script(self):
        import inspect

        source = inspect.getsource(contract.readback_reply) + inspect.getsource(contract._pick_ok)
        for word in ("localStorage", "sessionStorage", "document.cookie", "Authorization", "headers", "fetch("):
            self.assertNotIn(word, source)
        self.assertFalse(hasattr(contract, "READBACK_JS"))  # no script is evaluated in the page any more
        self.assertEqual(contract.ACCOUNTS_PATH, "/api/v1/backoffice/getAllDetailedAccounts")
        self.assertIn("accountUuid", contract.READBACK_PICK_PATHS)


class RealRowPathTests(unittest.TestCase):
    """Live finding 2026-10-18: the server's full rows carry these paths; the proven-absent fallback names are gone."""

    REAL = {
        "licenseStartDate": ("accountLicense.startDate",), "licenseExpirationDate": ("accountLicense.expirationDate",),
        "reconLevel": ("accountLicense.reconLevel",), "scanningFrequency": ("accountLicense.scanningFrequency",),
        "automatedDiscoveryEnabled": ("accountSettings.reconSettings.automatedDiscoveryEnabled",),
        "mfaRequired": ("primaryUser.isMfaRequired",), "organizationEmail": ("primaryUser.email",),
    }
    GONE = ("startDate", "expirationDate", "organizationEmail", "reconLevel", "scanningFrequency", "automatedDiscoveryEnabled",
            "accountLicense.licenseStartDate", "accountLicense.licenseExpirationDate")

    def full_row(self):
        row = unflatten({k: v for k, v in row_flat().items() if k != "accountSettings.reconSettings.automatedDiscoveryEnabled"})
        row.update({"accountLicense": {**row["accountLicense"], "startDate": "2026-10-16T09:00:00.000Z",
                                       "expirationDate": "2027-05-20T07:00:00.000Z", "reconLevel": "ALL_ASSETS",
                                       "scanningFrequency": "NO_SCHEDULE"},
                    "accountSettings": {"reconSettings": {"automatedDiscoveryEnabled": False}},
                    "primaryUser": {"isMfaRequired": True, "email": "test.user+acme@" + INTERNAL},
                    "campaignExecutionSettings": {"domainsMultiAttackStackSettings": {"enabled": False}}})
        return row

    def test_candidate_lists_hold_only_the_real_paths(self):
        for logical, paths in self.REAL.items():
            self.assertEqual(contract.ROW_PATHS[logical], paths, logical)
        for name in self.GONE:
            self.assertNotIn(name, contract.READBACK_PICK_PATHS)
        self.assertNotIn("organizationEmail", contract.STRICT_ROW_PATHS["organizationEmail"])

    def test_a_full_nested_row_compares_clean_with_the_real_paths(self):
        expected = contract.expected_readback_row(intended(), strict=True)
        self.assertEqual(contract.compare_readback(expected, self.full_row())[0], [])
        reduced = contract.readback_reply(200, {"pagination_response": {"total_count": 1, "table_data": [self.full_row()]}})
        self.assertEqual(contract.compare_readback(expected, reduced["rows"][0])[0], [])

    def test_a_removed_fallback_name_never_satisfies_a_compared_field_and_absent_paths_fail_closed(self):
        expected = contract.expected_readback_row(intended(), strict=True)
        row = self.full_row()
        for nested, flat_key, logical in (("accountLicense", "startDate", "licenseStartDate"),
                                          ("accountLicense", "expirationDate", "licenseExpirationDate"),
                                          ("accountLicense", "reconLevel", "reconLevel"),
                                          ("accountLicense", "scanningFrequency", "scanningFrequency"),
                                          ("primaryUser", "email", "organizationEmail")):
            moved = json.loads(json.dumps(row))
            value = moved[nested].pop(flat_key)
            moved[flat_key if nested == "accountLicense" else "organizationEmail"] = value  # the old top-level fallback name
            self.assertEqual(contract.compare_readback(expected, moved)[0], [logical], logical)
        moved = json.loads(json.dumps(row))
        moved["accountSettings"]["reconSettings"].pop("automatedDiscoveryEnabled")
        moved["automatedDiscoveryEnabled"] = False
        self.assertEqual(contract.compare_readback(expected, moved)[0], ["automatedDiscoveryEnabled"])
        self.assertEqual(sorted(contract.compare_readback(expected, {"accountName": NAME})[0]),
                         sorted(n for n in expected if n != "accountName"))

    def test_license_counts_are_numbers_and_the_picked_email_is_compared_but_never_reported(self):
        expected = contract.expected_readback_row(intended(), strict=True)
        row = self.full_row()
        row["accountLicense"]["assetsNumber"] = "1"
        self.assertEqual(contract.compare_readback(expected, row)[0], ["accountLicense.assetsNumber"])
        row = self.full_row()
        row["primaryUser"]["email"] = "someone.else@" + INTERNAL
        out = contract.compare_readback(expected, row)
        self.assertEqual(out[0], ["organizationEmail"])
        self.assertNotIn("someone.else", json.dumps(out))

    def test_the_picker_keeps_no_unrelated_content_and_handles_large_rows(self):
        row = self.full_row()
        row.update({"token": "SECRET-TOKEN-VALUE", "notes": "n" * 100_000, "operatorAccounts": [{"email": "op@x.example"}],
                    "primaryUser": {**row["primaryUser"], "phoneNumber": "+10000000", "firstName": "Zed"},
                    "blob": [{"k": "v" * 50}] * 5000})
        out = contract.readback_reply(200, {"pagination_response": {"total_count": 100, "table_data": [row] * 100}})
        self.assertEqual(len(out["rows"]), 100)
        text = json.dumps(out)
        for needle in ("SECRET-TOKEN-VALUE", "op@x.example", "+10000000", "Zed", "n" * 50, "blob", "notes", "firstName"):
            self.assertNotIn(needle, text)
        self.assertLess(len(text), 100 * 1500)
        self.assertTrue(set(out["rows"][0]) <= set(contract.READBACK_PICK_PATHS))
        self.assertLessEqual(production_probe.TABLE_ROW_LIMIT, 100)


class ReportTests(unittest.TestCase):
    def test_refuses_unredacted_and_writes_redacted(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            report = {"result": "production_readback_complete", "mismatches": ["accountType"]}
            path = contract.write_readback_report(base, report, now)
            self.assertEqual(path, base / production_probe.REPORT_SUBDIR / "readback-20261016T120000Z.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
            with self.assertRaises(ValueError):
                contract.write_readback_report(base, {"x": UUID}, now)
            with self.assertRaises(ValueError):
                contract.write_readback_report(base, {"x": "a@b.example"}, now)


if __name__ == "__main__":
    unittest.main()
