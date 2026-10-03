from __future__ import annotations

import copy
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from typing import Any

from integration.onboarding.leonardo_inventory import (
    ENVIRONMENTS, EXPECTED_PATHS, MAX_TOTAL, SCHEMA_VERSION, VALIDATION_TOGGLE_PATHS, InventoryError,
    assemble_pages, default_root, load_latest, match_readbacks, minimize_row, prune, require_environment,
    schema_drift, snapshot_payload, to_csv, write_snapshot,
)

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
DEV = ENVIRONMENTS["dev"]


def tenant_row(index: int, **overrides: Any) -> dict[str, Any]:
    """A synthetic tenant row with every documented key, including fields that must never leave."""
    row: dict[str, Any] = {
        "id": f"TENANTID{index:08d}", "_modified": 1759000000000, "_created": 1758000000000, "isDeleted": False,
        "accountName": f"Example Tenant {index}", "accountUuid": f"{index:032x}",
        "accountDomain": f"tenant{index}.example.com", "userEmailDomains": ["example.com"],
        "accountCountryCode": "US", "emailSettings": {"fromAddress": "alerts@example.com"},
        "accountLicense": {
            "id": f"LICENSE{index:08d}", "_created": 1758000000000, "_modified": 1759000000000, "isDeleted": False,
            "shouldArchive": False, "textSearchField": "Example Tenant owner@example.com",
            "accountId": f"TENANTID{index:08d}", "assetsNumber": 10, "domainsNumber": 2, "subDomainsNumber": 50,
            "startDate": 1758000000000, "expirationDate": 1790000000000, "licenseType": "Trial",
            "reconLevel": "Standard", "enabled": True, "scanningFrequency": "Weekly", "notificationsAllowed": True,
            "multipleUsersAllowed": False, "apiAccessAllowed": False, "leakedCredentialsAllowed": True,
            "phishingEnabled": False, "provisioningEnabled": False, "allowedModules": ["recon", "web"],
            "leakedCredentialsScannedDomainsNumber": 1, "elevatedFeaturesEnabled": False,
        },
        "enabled": True, "termsOfUseApproval": True,
        "accountSettings": {"reconSettings": {"automatedDiscoveryEnabled": True}},
        "primaryUserId": f"USER{index:08d}", "accountType": "Customer", "scanningInterval": "Weekly",
        "leakedCredentialsScanningInterval": "Monthly",
        "primaryUser": {"id": f"USER{index:08d}", "firstName": "Pat", "lastName": "Sample",
                        "email": "pat.sample@example.com", "isMfaRequired": True, "jobTitle": "Analyst",
                        "phoneNumber": "+1 555 0100"},
        "alternateDomains": ["alt.example.com"], "subDomains": ["a.example.com", "b.example.com"],
        "additionalNetworks": [], "leakedCredentialsScannedDomains": ["example.com"],
        "lastReconScan": 1759400000000, "lastReconScanDurationMilliseconds": 3600000,
        "lastReconExecutionData": {"timedOutActions": ["x"]}, "pendingValidationAssets": [],
        "campaignsTimeoutInHours": 90, "staticOutboundIpEnabled": False, "fullNucleiScanEnabled": True,
        "aiEnabled": False, "webAiAttackerEnabled": False, "webDictionaryBruteForceEnabled": True,
        "webEnumerationCustomDictionaryPaths": ["/secret-path"], "webDorkingEnabled": True,
        "authenticatedTestingEnabled": False, "operatorAccounts": ["OPERATOR01"], "subDomainsReconEnabled": True,
        "leakedCredentialsSettings": {"spyCloudSettings": {"apiKey": "FAKE-NOT-A-KEY"}},
        "campaignExecutionSettings": {"domainsMultiAttackStackSettings": {"enabled": True},
                                      "subDomainsMultiAttackStackSettings": {"enabled": False}},
        "lastScanStatusEnum": "Completed", "accountSubtype": None,
    }
    row.update(overrides)
    return row


def page(offset: int, rows: list[dict[str, Any]], total: int, *, size: int = 2,
         sort: dict[str, str] | None = None, filters: dict[str, Any] | None = None) -> tuple[dict, dict]:
    request = {"tableServerData": {"offset": offset, "items_per_page": size,
                                   "sort": sort or {"direction": "asc", "key": "accountName"},
                                   "filters": filters or {}, "unique_fields": []}}
    return request, {"pagination_response": {"unique_fields": {}, "total_count": total, "table_data": rows}}


def capture(count: int = 5, size: int = 2) -> list[tuple[dict, dict]]:
    rows = [tenant_row(index) for index in range(count)]
    return [page(offset, rows[offset:offset + size], count, size=size) for offset in range(0, count, size)]


def walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield "key", key
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)
    else:
        yield "value", value


class MinimizeRowTests(unittest.TestCase):
    def test_allow_list_drops_personal_data_domain_lists_and_settings(self):
        tenant = minimize_row(tenant_row(1))
        for kind, item in walk(tenant):
            text = str(item)
            for forbidden in ("@", "firstName", "lastName", "phone", "spyCloud", "Pat", "Sample", "secret-path",
                              "FAKE-NOT-A-KEY", "alt.example.com", "a.example.com", "jobTitle", "Analyst"):
                self.assertNotIn(forbidden.casefold(), text.casefold(), (kind, forbidden))
            if not (kind == "key" and item == "user_email_domains"):  # the one allowed count key
                self.assertNotIn("email", text.casefold())
        self.assertEqual(tenant["account_domain"], "tenant1.example.com")
        self.assertEqual(tenant["counts"]["sub_domains"], 2)
        self.assertEqual(tenant["primary_user"], {"present": True, "mfa_required": True})
        self.assertIs(tenant["operator_assigned"], True)
        self.assertEqual(tenant["scan"]["last_recon_scan_utc"], "2025-10-02T10:13:20Z")
        self.assertEqual(tenant["scan"]["timed_out_actions_count"], 1)
        self.assertEqual(tenant["license"]["allowed_modules"], ["recon", "web"])
        for path in VALIDATION_TOGGLE_PATHS.values():
            self.assertIn(path, tenant["toggles"])
        self.assertIs(tenant["toggles"]["campaignExecutionSettings.domainsMultiAttackStackSettings.enabled"], True)
        self.assertIs(tenant["toggles"]["webAiAttackerEnabled"], False)

    def test_missing_nested_objects_are_recorded_as_none(self):
        tenant = minimize_row({"id": "TENANTID00000001", "accountUuid": "a" * 32, "accountName": "Example Tenant",
                               "accountLicense": None, "primaryUser": None, "accountSettings": None,
                               "lastReconExecutionData": None, "campaignExecutionSettings": None})
        self.assertIsNone(tenant["license"]["type"])
        self.assertEqual(tenant["primary_user"], {"present": False, "mfa_required": None})
        self.assertIsNone(tenant["toggles"]["accountSettings.reconSettings.automatedDiscoveryEnabled"])
        self.assertIsNone(tenant["scan"]["timed_out_actions_count"])
        self.assertIsNone(tenant["counts"]["alternate_domains"])
        self.assertIsNone(tenant["operator_assigned"])
        json.dumps(tenant)

    def test_values_of_unexpected_type_are_dropped_not_copied(self):
        license_ = dict(tenant_row(1)["accountLicense"], assetsNumber="10", enabled=1, allowedModules=["ok", {"x": 1}])
        tenant = minimize_row(tenant_row(1, enabled="yes", isDeleted=0, accountName={"nested": "x"},
                                         lastReconScan="1759400000000", lastReconScanDurationMilliseconds=True,
                                         lastScanStatusEnum="bad status!", campaignsTimeoutInHours=float("nan"),
                                         aiEnabled="true", accountLicense=license_))
        self.assertIsNone(tenant["enabled"])
        self.assertIsNone(tenant["is_deleted"])
        self.assertIsNone(tenant["account_name"])
        self.assertIsNone(tenant["scan"]["last_recon_scan_ms"])
        self.assertIsNone(tenant["scan"]["last_recon_scan_utc"])
        self.assertIsNone(tenant["scan"]["duration_ms"])
        self.assertIsNone(tenant["scan"]["status"])
        self.assertIsNone(tenant["campaigns_timeout_hours"])
        self.assertIsNone(tenant["toggles"]["aiEnabled"])
        self.assertIsNone(tenant["license"]["assets_number"])
        self.assertIsNone(tenant["license"]["enabled"])
        self.assertIsNone(tenant["license"]["allowed_modules"])


class AssemblePagesTests(unittest.TestCase):
    def test_happy_path_multi_page(self):
        assembled = assemble_pages(capture(5, 2), page_size=2)
        self.assertEqual((assembled.total_count, assembled.pages, assembled.duplicates_dropped), (5, 3, 0))
        self.assertEqual(len(assembled.rows), 5)
        self.assertEqual(assembled.query, {"page_size": 2, "sort": {"direction": "asc", "key": "accountName"},
                                           "filter": "none"})
        self.assertNotIn("Example Tenant", repr(assembled))

    def assertReason(self, reason: str, pages: list, page_size: int = 2) -> None:
        with self.assertRaises(InventoryError) as raised:
            assemble_pages(pages, page_size=page_size)
        self.assertEqual(raised.exception.reason, reason)

    def test_total_count_change_is_inconsistent(self):
        pages = capture(5, 2)
        pages[1][1]["pagination_response"]["total_count"] = 6
        self.assertReason("inventory_inconsistent", pages)

    def test_offset_gap_or_missing_page_is_inconsistent(self):
        pages = capture(5, 2)
        self.assertReason("inventory_inconsistent", [pages[0], pages[2]])
        self.assertReason("inventory_inconsistent", pages[:2])

    def test_sort_or_filter_change_is_inconsistent(self):
        pages = capture(5, 2)
        pages[1][0]["tableServerData"]["sort"] = {"direction": "desc", "key": "accountName"}
        self.assertReason("inventory_inconsistent", pages)
        pages = capture(5, 2)
        pages[2][0]["tableServerData"]["filters"] = {"accountName": "Example"}
        self.assertReason("inventory_inconsistent", pages)

    def test_duplicate_with_same_uuid_is_dropped_and_counted(self):
        rows = [tenant_row(index) for index in range(3)]
        pages = [page(0, rows[0:2], 3), page(2, [rows[2], copy.deepcopy(rows[1])], 3)]
        assembled = assemble_pages(pages, page_size=2)
        self.assertEqual((len(assembled.rows), assembled.duplicates_dropped), (3, 1))

    def test_same_id_with_different_uuid_is_a_conflict(self):
        rows = [tenant_row(index) for index in range(3)]
        pages = [page(0, rows[0:2], 3), page(2, [rows[2], tenant_row(1, accountUuid="f" * 32)], 3)]
        self.assertReason("inventory_id_conflict", pages)

    def test_missing_required_key_or_shape_is_schema_unavailable(self):
        pages = capture(3, 2)
        del pages[1][1]["pagination_response"]["table_data"][0]["accountUuid"]
        self.assertReason("inventory_schema_unavailable", pages)
        pages = capture(3, 2)
        pages[0][1]["pagination_response"]["total_count"] = "3"
        self.assertReason("inventory_schema_unavailable", pages)
        self.assertReason("inventory_schema_unavailable", [])

    def test_too_large_and_empty(self):
        self.assertReason("inventory_too_large", [page(0, [], MAX_TOTAL + 1)])
        self.assertReason("inventory_too_large", [page(0, [], 1)] * 201)
        self.assertReason("inventory_empty", [page(0, [], 0)])

    def test_filters_are_reported_only_as_present(self):
        rows = [tenant_row(0)]
        assembled = assemble_pages([page(0, rows, 1, filters={"accountName": "Example"})], page_size=2)
        self.assertEqual(assembled.query["filter"], "present")


class SchemaDriftTests(unittest.TestCase):
    def test_clean_rows_report_no_drift(self):
        self.assertEqual(schema_drift([tenant_row(1)]), {"unknown": [], "missing": [], "type_changed": []})

    def test_unknown_missing_type_changed_and_key_redaction(self):
        row = tenant_row(1, newFlag=True, lastReconScan="2026-10-01", **{"a.example.com": {"inner": 1}})
        del row["aiEnabled"]
        row["accountLicense"] = dict(row["accountLicense"], **{"owner@example.com": "x"})
        drift = schema_drift([row])
        self.assertEqual(drift["unknown"], ["<key>", "accountLicense.<key>", "newFlag"])
        self.assertEqual(drift["missing"], ["aiEnabled"])
        self.assertEqual(drift["type_changed"], ["lastReconScan:str"])
        self.assertNotIn("example", json.dumps(drift))

    def test_open_settings_children_are_not_reported(self):
        row = tenant_row(1, emailSettings={"anything": {"deep": 1}})
        self.assertEqual(schema_drift([row])["unknown"], [])

    def test_missing_nested_path_only_reported_when_parent_is_an_object(self):
        drift = schema_drift([tenant_row(1, primaryUser=None)])
        self.assertEqual(drift["missing"], [])
        self.assertIn("primaryUser.isMfaRequired", EXPECTED_PATHS)

    def test_required_path_missing_raises(self):
        row = tenant_row(1)
        del row["accountLicense"]
        with self.assertRaises(InventoryError) as raised:
            schema_drift([row])
        self.assertEqual(raised.exception.reason, "inventory_schema_unavailable")


class SnapshotTests(unittest.TestCase):
    def payload(self, count: int = 5, captured_at: datetime = NOW) -> dict[str, Any]:
        return snapshot_payload(DEV, assemble_pages(capture(count, 2), page_size=2), captured_at)

    def test_production_and_unknown_environments_are_refused(self):
        for name, reason in (("prod", "production_inventory_not_approved"), ("qa", "unknown_environment")):
            with self.assertRaises(InventoryError) as raised:
                require_environment(name)
            self.assertEqual(raised.exception.reason, reason)
        with self.assertRaises(InventoryError):
            snapshot_payload(ENVIRONMENTS["prod"], assemble_pages(capture(1, 2), page_size=2), NOW)

    def test_payload_shape_and_sha_stable_regardless_of_input_order(self):
        payload = self.payload()
        rows = [tenant_row(index) for index in reversed(range(5))]
        reordered = snapshot_payload(DEV, assemble_pages(
            [page(offset, rows[offset:offset + 2], 5) for offset in range(0, 5, 2)], page_size=2), NOW)
        self.assertEqual(payload["rows_sha256"], reordered["rows_sha256"])
        self.assertEqual([tenant["id"] for tenant in payload["tenants"]], sorted(t["id"] for t in payload["tenants"]))
        self.assertEqual((payload["schema_version"], payload["environment"], payload["captured_at"],
                          payload["acquisition"], payload["row_count"]),
                         (SCHEMA_VERSION, "dev", "2026-10-03T12:00:00Z", "ui_pagination_intercept", 5))
        self.assertNotIn("@", json.dumps(payload))

    def test_write_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_snapshot(self.payload(), Path(directory))
            self.assertEqual(path.name, "inventory-20261003T120000Z.json")
            self.assertEqual(sorted(entry.name for entry in path.parent.iterdir()), [path.name, "latest.json"])
            loaded = load_latest(Path(directory), "dev", max_age=timedelta(hours=1), now=NOW + timedelta(minutes=5))
            self.assertEqual(loaded["age_seconds"], 300)
            self.assertEqual(loaded["rows_sha256"], self.payload()["rows_sha256"])

    def test_prune_keeps_newest_and_ignores_foreign_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for hour in range(5):
                write_snapshot(self.payload(captured_at=NOW + timedelta(hours=hour)), root, keep=3)
            folder = root / "dev"
            (folder / "notes.json").write_text("{}", encoding="utf-8")
            (folder / "inventory-latest.json").write_text("{}", encoding="utf-8")
            self.assertEqual(prune(folder, 2), ["inventory-20261003T140000Z.json"])
            self.assertEqual(sorted(entry.name for entry in folder.iterdir()), [
                "inventory-20261003T150000Z.json", "inventory-20261003T160000Z.json", "inventory-latest.json",
                "latest.json", "notes.json"])

    def written(self, directory: str) -> Path:
        return write_snapshot(self.payload(), Path(directory))

    def assertLoadReason(self, directory: str, reason: str, *, env: str = "dev", age: timedelta = timedelta(0)) -> None:
        with self.assertRaises(InventoryError) as raised:
            load_latest(Path(directory), env, max_age=timedelta(hours=1), now=NOW + age)
        self.assertEqual(raised.exception.reason, reason)

    def test_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertLoadReason(directory, "inventory_snapshot_missing")

    def test_tampered_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.written(directory)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["tenants"][0]["account_name"] = "Changed Example Tenant"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertLoadReason(directory, "inventory_snapshot_tampered")

    def test_stale_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            self.written(directory)
            self.assertLoadReason(directory, "inventory_snapshot_stale", age=timedelta(hours=2))

    def test_wrong_environment_and_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.written(directory)
            latest_path = path.parent / "latest.json"
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            latest_path.write_text(json.dumps(dict(latest, environment="prod")), encoding="utf-8")
            self.assertLoadReason(directory, "inventory_snapshot_wrong_environment")
            latest_path.write_text(json.dumps(dict(latest, schema_version=2)), encoding="utf-8")
            self.assertLoadReason(directory, "inventory_snapshot_schema_version")
            data = json.loads(path.read_text(encoding="utf-8"))
            latest_path.write_text(json.dumps(latest), encoding="utf-8")
            path.write_text(json.dumps(dict(data, origin="https://app.pentera.io")), encoding="utf-8")
            self.assertLoadReason(directory, "inventory_snapshot_wrong_environment")
            self.assertLoadReason(directory, "production_inventory_not_approved", env="prod")

    def test_path_traversal_in_latest_is_tampered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.written(directory)
            latest_path = path.parent / "latest.json"
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            for name in ("../inventory-20261003T120000Z.json", "..\\x.json", str(path)):
                latest_path.write_text(json.dumps(dict(latest, file=name)), encoding="utf-8")
                self.assertLoadReason(directory, "inventory_snapshot_tampered")

    def test_default_root_uses_local_app_data(self):
        self.assertEqual(default_root().parts[-2:], ("SurfaceOnboarding", "leonardo-inventory"))


class ViewTests(unittest.TestCase):
    def test_csv_neutralizes_formula_cells(self):
        rows = [tenant_row(0, accountName="=HYPERLINK(\"http://example.com\")"), tenant_row(1, accountName="+cmd"),
                tenant_row(2, accountName="-2+3"), tenant_row(3, accountName="@SUM(A1)"),
                tenant_row(4, accountName="\tTabbed Example")]
        payload = snapshot_payload(DEV, assemble_pages([page(0, rows, 5, size=5)], page_size=5), NOW)
        parsed = list(csv.reader(io.StringIO(to_csv(payload))))
        self.assertEqual(parsed[0][:3], ["id", "account_uuid", "account_name"])
        names = [line[2] for line in parsed[1:]]
        self.assertEqual(names, ["'=HYPERLINK(\"http://example.com\")", "'+cmd", "'-2+3", "'@SUM(A1)",
                                 "'\tTabbed Example"])
        self.assertEqual(parsed[1][5], "true")

    def test_match_readbacks_match_conflict_and_orphan(self):
        payload = snapshot_payload(DEV, assemble_pages(capture(4, 2), page_size=2), NOW)
        readbacks = {
            "CO-0001": {"surface_account_id": "TENANTID00000000", "account_uuid": f"{0:032X}"},
            "CO-0002": {"surface_account_id": "TENANTID00000001", "account_uuid": "e" * 32},
            "CO-0003": {"surface_account_id": "TENANTID99999999", "account_uuid": f"{2:032x}"},
            "CO-0004": {"surface_account_id": "TENANTID99999999", "account_uuid": "d" * 32},
        }
        result = match_readbacks(payload, readbacks)
        self.assertEqual(result["by_reference"], {"CO-0001": "TENANTID00000000", "CO-0002": "conflict",
                                                  "CO-0003": "conflict", "CO-0004": None})
        self.assertEqual(result["orphans"], ["TENANTID00000001", "TENANTID00000002", "TENANTID00000003"])


if __name__ == "__main__":
    unittest.main()
