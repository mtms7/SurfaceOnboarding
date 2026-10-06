"""Redash production-clone inventory (2026-10-05): adapter, collector transport, key store.

Fake HTTP only: no network, no Redash, no real key.
"""
from __future__ import annotations

import contextlib
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
import http.server
import io
import json
import sys
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import redash_inventory as redash
import tools.attended_ce_only_playwright as runner
import tools.redash_inventory_collector as collector
import tools.serve_attended_open_onboardings_dashboard as dashboard

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
FAKE_KEY = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
RETRIEVED_FRESH = "2026-10-05T11:30:00"  # 30 minutes old at NOW
RETRIEVED_STALE = "2026-10-05T08:00:00"  # 4 hours old
RETRIEVED_ANCIENT = "2026-10-03T08:00:00"  # more than 36 hours old


def row(index: int, **overrides):
    values = {
        "_id": f"68c9{index:020x}", "_created": "2026-08-11 13:57:03", "_modified": "2026-10-04T16:49:00.123000",
        "accountDomain": f"tenant{index}.example.com", "accountName": f"Example Tenant {index}",
        "accountUuid": f"{index:032x}", "alternateDomains": ["Alt.Example.com."], "enabled": True, "isDeleted": False,
        "lastReconScan": "2026-10-04T14:00:00Z", "lastReconScanDurationMilliseconds": 3600000.0,
        "scanningInterval": "WEEKLY", "accountType": "Customer", "operatorCount": 1,
        "accountLicense.assetsNumber": 10, "accountLicense.domainsNumber": 2.0, "accountLicense.subDomainsNumber": 50,
        "accountLicense.startDate": "2026-10-01T00:00:00Z", "accountLicense.expirationDate": 1790000000000,
        "accountLicense.licenseType": "Trial", "accountLicense.enabled": True,
        "accountLicense.scanningFrequency": "Weekly", "accountLicense.leakedCredentialsAllowed": True,
        "accountLicense.leakedCredentialsScannedDomainsNumber": 1, "lastScanStatusEnum": "COMPLETED",
        "leakedCredentialsSettings.spyCloudSettings": {"enabled": True},
    }
    values.update(overrides)
    return values


def result(rows, retrieved_at=RETRIEVED_FRESH, drop=()):
    names = [name for name in rows[0] if name not in drop] if rows else []
    return {"query_result": {"retrieved_at": retrieved_at, "data": {
        "columns": [{"name": name, "type": "string"} for name in names],
        "rows": [{key: value for key, value in item.items() if key not in drop} for item in rows]}}}


class AdapterTests(unittest.TestCase):
    def test_rows_take_leonardos_shape_and_only_allow_listed_fields(self):
        normalized = redash.normalize(result([row(1), row(2)]))
        first = normalized.rows[0]
        self.assertEqual(first["id"], "68c9" + "1".rjust(20, "0"))
        self.assertEqual(first["accountLicense"]["domainsNumber"], 2)  # 2.0 -> 2
        self.assertEqual(first["accountLicense"]["expirationDate"], 1790000000000)
        self.assertEqual(first["lastReconScan"], 1791122400000)
        self.assertEqual(first["lastReconScanDurationMilliseconds"], 3600000)
        self.assertEqual(first["operatorAccounts"], ["operator"])  # only "an operator is assigned", never who
        self.assertEqual(first["leakedCredentialsSettings"], {"spyCloudSettings": {"enabled": True}})  # the boolean only
        self.assertEqual(normalized.latest_modified, datetime(2026, 10, 4, 16, 49, 0, 123000, tzinfo=timezone.utc))
        self.assertEqual(normalized.retrieved_at, datetime(2026, 10, 5, 11, 30, tzinfo=timezone.utc))
        self.assertEqual((normalized.unexpected_columns, normalized.missing_columns), ((), ()))
        tenant = inventory.minimize_row(first)
        self.assertEqual(tenant["license"]["type"], "Trial")
        self.assertEqual(tenant["scan"]["status"], "COMPLETED")
        self.assertEqual(tenant["scan"]["last_recon_scan_utc"], "2026-10-04T14:00:00Z")
        self.assertEqual(tenant["alternate_domains"], ["alt.example.com"])
        self.assertIs(tenant["operator_assigned"], True)

    def test_a_tenant_without_a_licence_gets_none_not_an_empty_object(self):
        bare = row(3, **{key: None for key in row(3) if key.startswith("accountLicense.")})
        self.assertIsNone(redash.normalize(result([bare])).rows[0]["accountLicense"])

    def test_a_nested_licence_object_is_accepted_too(self):
        nested = {key: value for key, value in row(1).items() if not key.startswith("accountLicense.")}
        nested["accountLicense"] = {"licenseType": "Trial", "assetsNumber": 5}
        self.assertEqual(redash.normalize(result([nested])).rows[0]["accountLicense"]["assetsNumber"], 5)

    def test_unknown_and_missing_columns_are_reported_not_stored(self):
        extra = row(1, secretNote="do not keep")
        normalized = redash.normalize(result([extra], drop=("scanningInterval",)))
        self.assertEqual(normalized.unexpected_columns, ("secretNote",))
        self.assertIn("scanningInterval", normalized.missing_columns)
        self.assertNotIn("secretNote", json.dumps(normalized.rows[0]))

    def test_the_result_shape_fails_closed(self):
        for bad in (None, {}, {"query_result": {}}, {"query_result": {"data": {"columns": [], "rows": []}}},
                    {"query_result": {"retrieved_at": "x", "data": {"columns": "no", "rows": []}}}):
            with self.subTest(bad=bad), self.assertRaises(inventory.InventoryError) as raised:
                redash.normalize(bad)
            self.assertIn(raised.exception.reason, ("redash_schema_unavailable", "redash_date_unparseable"))
        for required in redash.REQUIRED_COLUMNS:
            with self.subTest(required=required), self.assertRaises(inventory.InventoryError) as raised:
                redash.normalize(result([row(1)], drop=(required,)))
            self.assertEqual(raised.exception.reason, "redash_schema_unavailable")
        with self.assertRaises(inventory.InventoryError):
            redash.normalize({"query_result": {"retrieved_at": RETRIEVED_FRESH, "data": {
                "columns": [{"name": "_id"}, {"name": "accountName"}, {"name": "accountUuid"}], "rows": ["not a row"]}}})

    def test_dates_numbers_and_lists_are_strict(self):
        self.assertEqual(redash.parse_timestamp_ms("2026-10-04 14:00:00"), 1791122400000)
        self.assertEqual(redash.parse_timestamp_ms("2026-10-04T14:00:00+00:00"), 1791122400000)
        self.assertEqual(redash.parse_timestamp_ms(1791122400), 1791122400000)  # seconds
        self.assertEqual(redash.parse_timestamp_ms(1791122400000), 1791122400000)
        self.assertIsNone(redash.parse_timestamp_ms(None))
        for bad in ("yesterday", True, {"x": 1}, float("nan")):
            with self.subTest(bad=bad), self.assertRaises(inventory.InventoryError):
                redash.parse_timestamp_ms(bad)
        for override in ({"accountLicense.assetsNumber": 1.5}, {"accountLicense.assetsNumber": "10"},
                         {"alternateDomains": "not json"}, {"alternateDomains": 7}, {"operatorCount": 100000},
                         {"lastReconScan": "someday"}):
            with self.subTest(override=override), self.assertRaises(inventory.InventoryError):
                redash.normalize(result([row(1, **override)]))
        listed = redash.normalize(result([row(1, alternateDomains='["a.example.com"]')])).rows[0]
        self.assertEqual(listed["alternateDomains"], ["a.example.com"])

    def test_rows_without_identity_duplicates_and_conflicts(self):
        with self.assertRaises(inventory.InventoryError) as raised:
            inventory.assemble_rows(redash.normalize(result([row(1, **{"_id": None})])).rows)
        self.assertEqual(raised.exception.reason, "inventory_schema_unavailable")
        twice = inventory.assemble_rows(redash.normalize(result([row(1), row(1)])).rows)
        self.assertEqual((twice.total_count, twice.duplicates_dropped), (1, 1))
        with self.assertRaises(inventory.InventoryError) as raised:
            inventory.assemble_rows(redash.normalize(result([row(1), row(1, accountUuid="f" * 32)])).rows)
        self.assertEqual(raised.exception.reason, "inventory_id_conflict")
        with self.assertRaises(inventory.InventoryError) as raised:
            inventory.assemble_rows([])
        self.assertEqual(raised.exception.reason, "inventory_empty")
        with self.assertRaises(inventory.InventoryError) as raised:
            inventory.assemble_rows(redash.normalize(result([row(1), row(2)])).rows, max_total=1)
        self.assertEqual(raised.exception.reason, "inventory_too_large")

    def test_spycloud_boolean_from_the_object_or_flat_column_and_nothing_else(self):
        obj = "leakedCredentialsSettings.spyCloudSettings"
        cases = (({obj: {"enabled": False, "apiKey": "FAKE"}}, False), ({obj: json.dumps({"enabled": True})}, True),
                 ({obj: {"enabled": "yes"}}, None), ({obj: "not json"}, None), ({obj: None}, None),
                 ({obj: {"other": 1}}, None), ({obj: None, obj + ".enabled": False}, False),
                 ({obj: None, obj + ".enabled": "no"}, None))
        for overrides, expected in cases:
            item = row(1, **overrides)
            normalized = redash.normalize(result([item]))
            payload = inventory.snapshot_payload(
                inventory.require_environment("prod-clone"), inventory.assemble_rows(normalized.rows),
                normalized.retrieved_at, acquisition=inventory.REDASH_ACQUISITION,
                expected_paths=redash.EXPECTED_REDASH_PATHS)
            self.assertIs(payload["tenants"][0]["spycloud_enabled"], expected, overrides)
            self.assertNotIn("FAKE", json.dumps(payload))
            self.assertEqual(payload["schema_drift"]["unknown"], [])

    def test_a_tenant_with_a_null_uuid_is_kept_and_counted(self):
        assembled = inventory.assemble_rows(redash.normalize(result([row(1, accountUuid=None), row(2)])).rows)
        self.assertEqual(assembled.uuid_missing_count, 1)

    def test_snapshot_is_labelled_prod_clone_and_round_trips(self):
        normalized = redash.normalize(result([row(1), row(2), row(3, isDeleted=True)]))
        environment = inventory.require_environment("prod-clone")
        payload = inventory.snapshot_payload(
            environment, inventory.assemble_rows(normalized.rows), normalized.retrieved_at,
            acquisition=inventory.REDASH_ACQUISITION, source=redash.source_meta(normalized, NOW, refreshed=True),
            expected_paths=redash.EXPECTED_REDASH_PATHS)
        self.assertEqual((payload["environment"], payload["environment_label"], payload["origin"]),
                         ("prod-clone", "PROD (cloned copy via Redash)", "https://redash.pentera.io"))
        self.assertEqual(payload["endpoint_path"], "/api/queries/251/results.json")
        self.assertEqual((payload["row_count"], payload["deleted_count"], payload["acquisition"]),
                         (3, 1, "redash_saved_query_results"))
        self.assertEqual(payload["source"]["data_latest_modified"], "2026-10-04T16:49:00Z")
        self.assertEqual(payload["schema_drift"], {"unknown": [], "missing": [], "type_changed": []})
        self.assertNotIn("@", json.dumps(payload))
        # Only the boolean is kept (owner decision 2026-10-05), never the object.
        self.assertEqual({tenant["spycloud_enabled"] for tenant in payload["tenants"]}, {True})
        self.assertEqual(json.dumps(payload).casefold().count("spycloud"), 3)
        with tempfile.TemporaryDirectory() as directory:
            path = inventory.write_snapshot(payload, Path(directory))
            self.assertEqual(path.name, "redash-prod-clone-inventory-20261005T113000Z.json")
            loaded = inventory.load_latest(Path(directory), "prod-clone", max_age=timedelta(days=1), now=NOW)
            self.assertEqual(loaded["rows_sha256"], payload["rows_sha256"])
            with self.assertRaises(inventory.InventoryError):  # the DEV loader never reads the clone's snapshot
                inventory.load_latest(Path(directory), "dev", max_age=timedelta(days=1), now=NOW)

    def test_hardening_of_the_adapter(self):
        many = result([row(1)])
        many["query_result"]["data"]["rows"] = [{}] * (inventory.REDASH_MAX_TOTAL + 1)
        with self.assertRaises(inventory.InventoryError) as raised:
            redash.normalize(many)
        self.assertEqual(raised.exception.reason, "inventory_too_large")  # refused before any row is processed
        for huge in (10 ** 30, 9.9e300, "9999999999-01-01", "0001-01-01T00:00:00+23:59"):
            with self.subTest(huge=huge), self.assertRaises(inventory.InventoryError) as raised:
                redash.parse_timestamp_ms(huge)
            self.assertEqual(raised.exception.reason, "redash_date_unparseable")
        odd = redash.normalize(result([dict(row(1), **{"x" * 200: 1, "bad name!": 2})]))
        self.assertTrue(all(len(name) <= 64 for name in odd.unexpected_columns))
        self.assertIn("<column>", odd.unexpected_columns)
        self.assertEqual(redash.normalize(result([row(1, operatorCount=0)])).rows[0]["operatorAccounts"], [])
        self.assertEqual(redash.normalize(result([row(1, operatorCount=900)])).rows[0]["operatorAccounts"], ["operator"])

    def test_strict_assembly_refuses_a_repeated_id_that_disagrees_with_itself(self):
        for change in ({"accountName": "Renamed"}, {"isDeleted": True}):
            with self.subTest(change=change), self.assertRaises(inventory.InventoryError) as raised:
                inventory.assemble_rows(redash.normalize(result([row(1), row(1, **change)])).rows)
            self.assertEqual(raised.exception.reason, "inventory_id_conflict")

    def test_prune_never_deletes_the_snapshot_latest_points_at_and_writes_do_not_collide(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for hour in range(5):  # four newer snapshots first
                normalized = redash.normalize(result([row(1)], f"2026-10-05T0{hour + 5}:00:00"))
                collector_payload = inventory.snapshot_payload(
                    inventory.ENVIRONMENTS["prod-clone"], inventory.assemble_rows(normalized.rows), normalized.retrieved_at,
                    acquisition=inventory.REDASH_ACQUISITION, expected_paths=redash.EXPECTED_REDASH_PATHS)
                inventory.write_snapshot(collector_payload, root, keep=4)
            older = redash.normalize(result([row(1)], "2026-10-01T00:00:00"))
            payload = inventory.snapshot_payload(
                inventory.ENVIRONMENTS["prod-clone"], inventory.assemble_rows(older.rows), older.retrieved_at,
                acquisition=inventory.REDASH_ACQUISITION, expected_paths=redash.EXPECTED_REDASH_PATHS)
            path = inventory.write_snapshot(payload, root, keep=4)
            self.assertTrue(path.exists())  # an oddly old stamp must not delete what latest.json names
            latest = json.loads((path.parent / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual((latest["file"], latest["row_count"]), (path.name, 1))
            self.assertEqual(inventory.load_latest(root, "prod-clone", max_age=timedelta(days=9999), now=NOW)["row_count"], 1)
        replaced = []
        with tempfile.TemporaryDirectory() as directory, patch.object(os, "replace", side_effect=lambda src, dst: replaced.append(Path(src).name)):
            target = Path(directory) / "x.json"
            inventory._write_json_atomic(target, {"a": 1})
            inventory._write_json_atomic(target, {"a": 1})
        self.assertEqual(len(set(replaced)), 2)  # each writer has its own temp file

    def test_environments_keep_backoffice_production_blocked(self):
        self.assertTrue(inventory.ENVIRONMENTS["prod-clone"].approved)
        with self.assertRaises(inventory.InventoryError) as raised:
            inventory.require_environment("prod")
        self.assertEqual(raised.exception.reason, "production_inventory_not_approved")


class _Response:
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

    def read(self, limit=-1):
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    """Scripted replies per (method, path); records every request (never the key's value in a URL)."""

    def __init__(self, replies):
        self.replies = {key: list(value) if isinstance(value, list) else [value] for key, value in replies.items()}
        self.requests = []

    def open(self, request, timeout=None):
        path = request.full_url.removeprefix(collector.BASE_URL)
        self.requests.append((request.get_method(), path, request.full_url, dict(request.header_items())))
        queue = self.replies.get((request.get_method(), path))
        if not queue:
            raise AssertionError(f"unexpected request {request.get_method()} {path}")
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, BaseException):
            raise reply
        return _Response(reply)


def http_error(code):
    return urllib.error.HTTPError(collector.BASE_URL + "/x", code, "x", {}, io.BytesIO(b""))


GET = ("GET", collector.RESULTS_PATH)
POST = ("POST", collector.REFRESH_PATH)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def run_collect(self, opener, **kwargs):
        return collector.collect(opener, FAKE_KEY, root=self.root, now=NOW, sleep=lambda seconds: None, **kwargs)

    def test_a_fresh_cached_result_is_collected_without_asking_redash_to_rerun(self):
        opener = FakeOpener({GET: result([row(1), row(2)], RETRIEVED_FRESH)})
        report = self.run_collect(opener, write_csv=True)
        self.assertEqual((report["result"], report["row_count"], report["refreshed"], report["data_age_minutes"]),
                         ("inventory_collected", 2, False, 30))
        self.assertEqual([request[0] for request in opener.requests], ["GET"])
        method, path, url, headers = opener.requests[0]
        self.assertEqual(url, "https://redash.pentera.io/api/queries/251/results.json")
        self.assertNotIn(FAKE_KEY, url)  # the key is in a header, never a URL
        self.assertEqual(headers["Authorization"], "Key " + FAKE_KEY)
        directory = inventory.env_dir(self.root, "prod-clone")
        self.assertEqual(sorted(entry.name for entry in directory.iterdir()),
                         ["latest.json", "redash-prod-clone-inventory-20261005T113000Z.csv",
                          "redash-prod-clone-inventory-20261005T113000Z.json"])
        self.assertNotIn(FAKE_KEY, "".join(entry.read_text(encoding="utf-8") for entry in directory.iterdir()))
        again = self.run_collect(FakeOpener({GET: result([row(1), row(2)], RETRIEVED_FRESH)}))
        self.assertEqual(again["result"], "inventory_unchanged")

    def test_a_stale_cached_result_is_refreshed_through_a_job(self):
        stale, fresh = result([row(1)], RETRIEVED_STALE), result([row(1), row(2)], RETRIEVED_FRESH)
        opener = FakeOpener({
            GET: stale, POST: {"job": {"id": "abc12345-def", "status": 1}},
            ("GET", "/api/jobs/abc12345-def"): [{"job": {"status": 2}}, {"job": {"status": 3, "query_result_id": 777}}],
            ("GET", "/api/query_results/777.json"): fresh})
        report = self.run_collect(opener)
        self.assertEqual((report["result"], report["row_count"], report["refreshed"]), ("inventory_collected", 2, True))
        self.assertEqual(opener.requests[1][0:2], ("POST", collector.REFRESH_PATH))

    def test_an_immediate_refresh_reply_is_used(self):
        fresh = result([row(1)], RETRIEVED_FRESH)
        opener = FakeOpener({GET: result([row(1)], RETRIEVED_STALE), POST: fresh})
        self.assertTrue(self.run_collect(opener)["refreshed"])

    def test_no_refresh_uses_the_cached_result_and_a_failed_refresh_falls_back_to_it(self):
        stale = result([row(1)], RETRIEVED_STALE)
        quiet = FakeOpener({GET: stale})
        self.assertEqual(self.run_collect(quiet, refresh=False)["data_age_minutes"], 240)
        self.assertEqual([request[0] for request in quiet.requests], ["GET"])
        denied = FakeOpener({GET: result([row(2)], RETRIEVED_STALE), POST: http_error(403)})
        report = self.run_collect(denied)
        self.assertEqual((report["result"], report["refreshed"], report["data_age_minutes"]),
                         ("inventory_collected", False, 240))

    def test_results_older_than_the_hard_limit_are_refused_and_nothing_is_written(self):
        opener = FakeOpener({GET: result([row(1)], RETRIEVED_ANCIENT), POST: http_error(403)})
        with self.assertRaises(collector.CollectorError) as raised:
            self.run_collect(opener)
        self.assertEqual(raised.exception.reason, "redash_results_stale")
        self.assertFalse(inventory.env_dir(self.root, "prod-clone").exists())

    def test_transport_failures_fail_closed_with_reason_codes_only(self):
        cases = ((http_error(401), "redash_auth_failed"), (http_error(403), "redash_auth_failed"),
                 (http_error(404), "redash_query_not_found"), (http_error(429), "redash_rate_limited"),
                 (http_error(500), "redash_http_error"), (http_error(302), "redash_redirect_refused"),
                 (urllib.error.URLError("boom " + FAKE_KEY), "redash_unreachable"), (TimeoutError(), "redash_unreachable"),
                 (b"<html>WAF block</html>", "redash_bad_json"))
        for reply, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(collector.CollectorError) as raised:
                self.run_collect(FakeOpener({GET: reply}))
            self.assertEqual(raised.exception.reason, reason)
            self.assertNotIn(FAKE_KEY, repr(raised.exception))

    def test_an_oversize_reply_is_refused(self):
        with patch.object(collector, "MAX_BYTES", 10), self.assertRaises(collector.CollectorError) as raised:
            self.run_collect(FakeOpener({GET: result([row(1)])}))
        self.assertEqual(raised.exception.reason, "redash_response_too_large")

    def test_a_wrong_shape_leaves_the_previous_snapshot_alone(self):
        self.run_collect(FakeOpener({GET: result([row(1), row(2)])}))
        directory = inventory.env_dir(self.root, "prod-clone")
        before = sorted((entry.name, entry.read_bytes()) for entry in directory.iterdir())
        broken = result([row(1)], RETRIEVED_FRESH, drop=("accountUuid",))
        with self.assertRaises(inventory.InventoryError):
            self.run_collect(FakeOpener({GET: broken}))
        self.assertEqual(before, sorted((entry.name, entry.read_bytes()) for entry in directory.iterdir()))

    def test_only_four_exact_method_and_path_pairs_are_ever_requested(self):
        opener = FakeOpener({})
        for method, path in (
                ("GET", "/api/queries/250/results.json"), ("GET", "/api/data_sources"), ("GET", "/api/queries"),
                ("GET", "/api/users/me"), ("GET", "/api/queries/251/../250/results.json"),
                ("GET", "/api/queries/251/%2e%2e/250/results.json"), ("GET", "/api/query_results/1.json?api_key=x"),
                ("GET", "/api/queries/251/fork"), ("POST", "/api/queries/251/fork"), ("DELETE", "/api/jobs/abc12345-def"),
                ("DELETE", "/api/queries/251/results"), ("POST", collector.RESULTS_PATH), ("GET", collector.REFRESH_PATH),
                ("POST", "/api/jobs/abc12345-def"), ("GET", "/api/jobs/abc12345-def/../x"), ("GET", "/api/jobs/short"),
                ("GET", "/api/query_results/12.json#frag"), ("GET", "/api/query_results/abc.json"),
                ("GET", "https://evil.example/api/queries/251/results.json")):
            with self.subTest(method=method, path=path), self.assertRaises(collector.CollectorError) as raised:
                collector._call(opener, FAKE_KEY, method, path)
            self.assertEqual(raised.exception.reason, "redash_path_not_allowed")
        self.assertEqual(opener.requests, [])
        for method, path in (("GET", collector.RESULTS_PATH), ("POST", collector.REFRESH_PATH),
                             ("GET", "/api/jobs/abc12345-def"), ("GET", "/api/query_results/777.json")):
            collector._call(FakeOpener({(method, path): {}}), FAKE_KEY, method, path)
        self.assertTrue(collector.BASE_URL.startswith("https://" + collector.REDASH_HOST))

    def test_redirects_are_never_followed(self):
        self.assertIsNone(collector._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/"))

    def test_a_real_redirect_over_a_real_socket_is_refused_and_not_followed(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/stolen")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(collector, "BASE_URL", f"http://127.0.0.1:{server.server_port}"), \
                    self.assertRaises(collector.CollectorError) as raised:
                collector._call(collector.build_opener(), FAKE_KEY, "GET", collector.RESULTS_PATH)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(raised.exception.reason, "redash_redirect_refused")
        self.assertEqual(len(seen), 1)  # the Location was never requested

    def test_a_shrunken_or_edited_result_is_refused_unless_the_shrink_is_accepted(self):
        self.run_collect(FakeOpener({GET: result([row(i) for i in range(1, 21)], "2026-10-05T11:10:00")}))
        shrunk = result([row(i) for i in range(1, 11)], RETRIEVED_FRESH)
        with self.assertRaises(collector.CollectorError) as raised:
            self.run_collect(FakeOpener({GET: shrunk}))
        self.assertEqual(raised.exception.reason, "redash_result_shrunk")
        self.assertEqual(len(inventory.load_latest(self.root, "prod-clone", max_age=timedelta(days=9), now=NOW)["tenants"]), 20)
        report = self.run_collect(FakeOpener({GET: shrunk}), accept_shrink=True)
        self.assertEqual((report["result"], report["row_count"]), ("inventory_collected", 10))
        small = result([row(i) for i in range(1, 10)], "2026-10-05T11:45:00")  # 10% smaller than 10 is still fine
        self.assertEqual(self.run_collect(FakeOpener({GET: small}))["row_count"], 9)

    def test_an_edited_query_or_a_broken_uuid_column_stops_the_run(self):
        with self.assertRaises(collector.CollectorError) as raised:
            self.run_collect(FakeOpener({GET: result([row(1, extraColumn="x")])}))
        self.assertEqual(raised.exception.reason, "redash_query_changed")
        for column in ("accountDomain", "alternateDomains", "lastScanStatusEnum"):
            with self.subTest(column=column), self.assertRaises(inventory.InventoryError):
                self.run_collect(FakeOpener({GET: result([row(1)], drop=(column,))}))
        all_null = result([row(i, accountUuid=None) for i in range(1, 6)])
        with self.assertRaises(collector.CollectorError) as raised:
            self.run_collect(FakeOpener({GET: all_null}))
        self.assertEqual(raised.exception.reason, "redash_uuid_missing")
        self.assertFalse(inventory.env_dir(self.root, "prod-clone").exists())

    def test_a_refresh_that_did_not_happen_is_flagged_and_data_from_the_future_is_refused(self):
        stale = result([row(1)], RETRIEVED_STALE)
        flagged = self.run_collect(FakeOpener({GET: stale, POST: http_error(403)}))
        self.assertEqual((flagged["refresh_failed"], flagged["refreshed"]), (True, False))
        quiet = self.run_collect(FakeOpener({GET: result([row(2)], RETRIEVED_STALE)}), refresh=False)
        self.assertFalse(quiet["refresh_failed"])  # asking for no refresh is not a failure
        fine = self.run_collect(FakeOpener({GET: result([row(3)], RETRIEVED_FRESH)}))
        self.assertFalse(fine["refresh_failed"])
        with self.assertRaises(collector.CollectorError) as raised:
            self.run_collect(FakeOpener({GET: result([row(4)], "2026-10-06T12:00:00")}))
        self.assertEqual(raised.exception.reason, "redash_results_future")

    def test_polling_is_bounded_and_a_canceled_job_is_a_failure(self):
        stale = result([row(1)], RETRIEVED_STALE)
        running = FakeOpener({GET: stale, POST: {"job": {"id": "abc12345-def"}},
                              ("GET", "/api/jobs/abc12345-def"): {"job": {"status": 2}}})
        ticks = iter(range(0, 100000, 100))
        with patch.object(collector.time, "monotonic", side_effect=lambda: next(ticks)):
            report = self.run_collect(running)
        self.assertEqual((report["refreshed"], report["refresh_failed"]), (False, True))
        self.assertLess(len(running.requests), 10)  # the deadline stopped the loop, not the poll cap
        canceled = FakeOpener({GET: result([row(2)], RETRIEVED_STALE), POST: {"job": {"id": "abc12345-def"}},
                               ("GET", "/api/jobs/abc12345-def"): {"job": {"status": 5}}})
        self.assertTrue(self.run_collect(canceled)["refresh_failed"])
        bad = FakeOpener({GET: result([row(3)], RETRIEVED_STALE), POST: {"job": {"id": "abc12345-def"}},
                          ("GET", "/api/jobs/abc12345-def"): {"job": {"status": 3, "query_result_id": "../1"}}})
        self.assertTrue(self.run_collect(bad)["refresh_failed"])

    def test_unchanged_is_only_claimed_when_the_stored_snapshot_still_verifies(self):
        self.run_collect(FakeOpener({GET: result([row(1)], RETRIEVED_FRESH)}))
        directory = inventory.env_dir(self.root, "prod-clone")
        snapshot = next(directory.glob("redash-prod-clone-inventory-*.json"))
        snapshot.write_text("{}", encoding="utf-8")  # corrupt the file latest.json points at
        again = self.run_collect(FakeOpener({GET: result([row(1)], RETRIEVED_FRESH)}))
        self.assertEqual(again["result"], "inventory_collected")  # rewritten, not "unchanged"
        self.assertEqual(inventory.load_latest(self.root, "prod-clone", max_age=timedelta(days=9), now=NOW)["row_count"], 1)

    def test_clone_csv_neutralises_formula_names(self):
        self.run_collect(FakeOpener({GET: result([row(1, accountName="=HYPERLINK(\"http://x\")"), row(2)])}), write_csv=True)
        directory = inventory.env_dir(self.root, "prod-clone")
        text = next(directory.glob("*.csv")).read_text(encoding="utf-8")
        self.assertIn("'=HYPERLINK", text)
        self.assertNotIn(",=HYPERLINK", text)

    def test_a_bad_refresh_reply_is_not_trusted(self):
        for reply in ({"job": {"id": "../etc"}}, {"job": {}}, {"unexpected": 1}):
            opener = FakeOpener({GET: result([row(1)], RETRIEVED_STALE), POST: reply})
            report = self.run_collect(opener)  # falls back to the cached result
            self.assertFalse(report["refreshed"])

    def test_probe_prints_shape_only(self):
        report = collector.probe(FakeOpener({GET: result([row(1), row(2)])}), FAKE_KEY, now=NOW)
        text = json.dumps(report)
        self.assertEqual((report["row_count"], report["normalize"], report["data_age_minutes"]), (2, "ok", 30))
        self.assertIn("accountName", report["columns"])
        for value in ("Example Tenant", "example.com", FAKE_KEY, "68c9"):
            self.assertNotIn(value, text)

    def test_probe_reports_why_normalisation_would_fail(self):
        report = collector.probe(FakeOpener({GET: result([row(1, lastReconScan="someday")])}), FAKE_KEY, now=NOW)
        self.assertEqual(report["normalize"], "redash_date_unparseable")


class MainTests(unittest.TestCase):
    def run_main(self, argv, opener=None, key=FAKE_KEY, key_error=None):
        home = Path(tempfile.mkdtemp())
        out = io.StringIO()
        with patch.object(collector, "state_dir", return_value=home), \
                patch.object(inventory, "default_root", return_value=home / "inv"), \
                patch.object(collector, "build_opener", return_value=opener), \
                patch.object(collector, "load_key", side_effect=key_error or (lambda: key)), \
                patch.object(collector, "datetime", wraps=datetime) as clock, redirect_stdout(out):
            clock.now.return_value = NOW
            code = collector.main(argv)
        return code, out.getvalue(), home

    def test_collect_prints_counts_and_logs_without_secrets_or_rows(self):
        code, output, home = self.run_main(["--collect"], FakeOpener({GET: result([row(1)])}))
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual((report["result"], report["row_count"]), ("inventory_collected", 1))
        logged = (home / "collector.log").read_text(encoding="utf-8")
        for text in (output, logged):
            for secret in (FAKE_KEY, "Example Tenant", "example.com"):
                self.assertNotIn(secret, text)

    def test_a_missed_refresh_exits_2_and_a_start_line_is_logged(self):
        code, output, home = self.run_main(["--collect"], FakeOpener({GET: result([row(1)], RETRIEVED_STALE),
                                                                    POST: http_error(403)}))
        self.assertEqual(code, 2)
        self.assertTrue(json.loads(output)["refresh_failed"])
        logged = (home / "collector.log").read_text(encoding="utf-8")
        self.assertIn("collect started", logged)
        self.assertEqual(self.run_main(["--collect", "--no-refresh"], FakeOpener({GET: result([row(1)], RETRIEVED_STALE)}))[0], 0)

    def test_the_key_prompt_refuses_a_non_console_or_an_echoing_prompt(self):
        with patch.object(collector.sys, "stdin", io.StringIO("")), self.assertRaises(collector.CollectorError) as raised:
            collector.read_key_hidden()
        self.assertEqual(raised.exception.reason, "redash_key_prompt_not_a_console")

        class Console(io.StringIO):
            def isatty(self):
                return True

        import getpass
        with patch.object(collector.sys, "stdin", Console()), \
                patch.object(collector.getpass, "getpass", side_effect=lambda *a: __import__("warnings").warn("echo", getpass.GetPassWarning)), \
                self.assertRaises(collector.CollectorError) as raised:
            collector.read_key_hidden()
        self.assertEqual(raised.exception.reason, "redash_key_prompt_not_hidden")
        with patch.object(collector.sys, "stdin", Console()), patch.object(collector.getpass, "getpass", return_value=FAKE_KEY):
            self.assertEqual(collector.read_key_hidden(), FAKE_KEY)

    def test_failures_exit_nonzero_with_a_reason_code(self):
        code, output, _home = self.run_main(["--collect"], FakeOpener({GET: http_error(401)}))
        self.assertEqual((code, json.loads(output)), (1, {"result": "redash_auth_failed"}))
        code, output, _home = self.run_main(["--probe"], None, key_error=collector.CollectorError("redash_key_unavailable"))
        self.assertEqual((code, json.loads(output)), (1, {"result": "redash_key_unavailable"}))
        code, output, _home = self.run_main(["--collect"], FakeOpener({GET: RuntimeError("boom " + FAKE_KEY)}))
        self.assertEqual((code, json.loads(output)), (1, {"result": "redash_collector_unavailable"}))
        self.assertNotIn(FAKE_KEY, output)


class ProductionGateTests(unittest.TestCase):
    """2026-10-05 owner decision: the duplicate validation for production is the production clone, fail closed."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def collect(self, *rows, retrieved=RETRIEVED_FRESH):
        collector.collect(FakeOpener({GET: result(list(rows), retrieved)}), FAKE_KEY, root=self.root, now=NOW)

    def gate(self, name="Brand New Customer", domains=("brandnew.example.com",), now=NOW, **kwargs):
        return inventory.production_duplicate_gate(self.root, name, domains, now=now, **kwargs)

    def test_a_name_primary_or_alternate_domain_match_blocks(self):
        self.collect(row(1, accountName="Example Customer", accountDomain="customer.example.com",
                         alternateDomains=["alt.customer.example.com"]), row(2))
        for name, domains, reasons in (
                ("  example   CUSTOMER ", ("x.example.org",), ["tenant_name"]),
                ("Someone Else", ("CUSTOMER.example.com.",), ["primary_domain"]),
                ("Someone Else", ("alt.customer.example.com",), ["alternate_domain"])):
            with self.subTest(reasons=reasons):
                gate = self.gate(name, domains)
                self.assertEqual((gate["result"], gate["blocks"]), ("duplicate_production_clone_match", True))
                self.assertEqual(gate["matches"][0]["reasons"], reasons)

    def test_a_deleted_tenant_still_blocks_and_says_so(self):
        self.collect(row(1, accountName="Gone Customer", isDeleted=True), row(2))
        gate = self.gate("Gone Customer")
        self.assertTrue(gate["blocks"])
        self.assertTrue(gate["matches"][0]["is_deleted"])

    def test_no_match_continues_to_the_live_check_and_is_never_a_clearance(self):
        self.collect(row(1), row(2))
        gate = self.gate()
        self.assertEqual((gate["result"], gate["blocks"], gate["tenants_checked"]), ("production_clone_no_match", False, 2))
        self.assertEqual(gate["environment_label"], "PROD (cloned copy via Redash)")

    def test_an_unusable_clone_blocks_instead_of_waving_the_onboarding_through(self):
        self.assertEqual(self.gate()["reason"], "inventory_snapshot_missing")  # nothing collected yet
        self.collect(row(1), row(2))
        stale = self.gate(now=NOW + timedelta(hours=7))  # collector stopped: older than the 6 h the gate trusts
        self.assertEqual((stale["result"], stale["blocks"], stale["reason"]),
                         ("production_clone_unavailable", True, "inventory_snapshot_stale"))
        self.assertFalse(self.gate(now=NOW + timedelta(hours=5))["blocks"])
        directory = inventory.env_dir(self.root, "prod-clone")
        snapshot = next(directory.glob("redash-prod-clone-inventory-*.json"))
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        data["tenants"][0]["account_name"] = "Tampered"
        snapshot.write_text(json.dumps(data), encoding="utf-8")
        tampered = self.gate()
        self.assertEqual((tampered["blocks"], tampered["reason"]), (True, "inventory_snapshot_tampered"))

    def test_a_clone_without_alternate_domains_is_incomplete_and_blocks(self):
        self.collect(row(1, alternateDomains=None), row(2, alternateDomains=None))
        gate = self.gate()
        self.assertEqual((gate["result"], gate["blocks"], gate["reason"]),
                         ("production_clone_incomplete", True, "alternate_domains_missing"))

    def test_the_dev_inventory_is_never_consulted_for_production(self):
        from integration.tests.test_leonardo_inventory import capture
        assembled = inventory.assemble_pages(capture(3, 2), page_size=2)
        inventory.write_snapshot(inventory.snapshot_payload(inventory.ENVIRONMENTS["dev"], assembled, NOW), self.root)
        gate = self.gate("Example Tenant 1", ("tenant1.example.com",))  # a Dev tenant, but no clone yet
        self.assertEqual((gate["result"], gate["blocks"]), ("production_clone_unavailable", True))


class DashboardEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        patcher = patch.object(dashboard, "inventory_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _collect(self):
        collector.collect(FakeOpener({GET: result([row(1), row(2)], RETRIEVED_FRESH)}), FAKE_KEY, root=self.root, now=NOW)

    def test_missing_clone_snapshot_says_how_it_is_collected(self):
        page = dashboard.render_inventory(env="prod-clone")
        self.assertIn("redash_inventory_collector.py --collect", page)
        self.assertIn("PROD (cloned copy via Redash)", page)
        self.assertNotIn("/attended/inventory-refresh", page)  # the clone refreshes by schedule, not by a button

    def test_clone_page_shows_the_data_age_without_co_links_or_a_refresh_button(self):
        self._collect()
        with patch.object(dashboard, "attended_leonardo_readbacks", side_effect=AssertionError("DEV readbacks never apply")):
            page = dashboard.render_inventory(env="prod-clone")
            sorted_page = dashboard.render_inventory(env="prod-clone", sort="scan", direction="desc", scan="COMPLETED")
        self.assertIn("Data as of 2026-10-05T11:30:00Z", page)
        self.assertIn("newest change in the source 2026-10-04T16:49:00Z", page)
        self.assertIn("Example Tenant 1", page)
        self.assertIn("no CO", page)
        self.assertNotIn("href='/co/", page)
        self.assertNotIn("/attended/inventory-refresh", page)
        self.assertIn("href='/tenants?", sorted_page)  # sorting and filtering stay on the clone's own tab
        self.assertIn("action='/tenants'", sorted_page)
        self.assertNotIn("/inventory?", sorted_page)
        self.assertIn("Tenants — Production (Redash clone)", page)
        self.assertNotIn("<script", page.lower())

    def test_precheck_page_shows_the_production_gate_without_touching_the_dev_answer(self):
        match = {"result": "duplicate_production_clone_match", "blocks": True, "tenants_checked": 5439,
                 "captured_at": "2026-10-05T17:00:00Z",
                 "matches": [{"id": "T9", "account_name": "<b>Acme</b>", "is_deleted": False, "created": 1759622400000,
                             "reasons": ["primary_domain"]}]}
        page = dashboard.page_duplicate_precheck("CO-0801", {"result": "inventory_no_match", "tenants_checked": 594,
                                                              "production_clone": match})
        self.assertIn("Production duplicate check", page)
        self.assertIn("This CO already has a tenant in production.", page)
        self.assertIn("<th>Created</th>", page)
        self.assertIn("<td>2025-10-05</td>", page)
        self.assertIn("&lt;b&gt;Acme&lt;/b&gt;", page)
        self.assertNotIn("<b>Acme</b>", page)
        none = dashboard.page_duplicate_precheck("CO-0801", {"result": "inventory_no_match", "tenants_checked": 594,
                                                              "production_clone": {"result": "production_clone_no_match",
                                                                                   "blocks": False, "tenants_checked": 5439,
                                                                                   "captured_at": "2026-10-05T17:00:00Z"}})
        self.assertIn("No match among 5439 production tenants", none)
        self.assertIn("This is not a clearance", none)
        gone = dashboard.page_duplicate_precheck("CO-0801", {"result": "inventory_no_match", "tenants_checked": 594,
                                                              "production_clone": {"result": "production_clone_unavailable",
                                                                                   "blocks": True,
                                                                                   "reason": "inventory_snapshot_stale"}})
        self.assertIn("inventory_snapshot_stale", gone)
        self.assertIn("A production onboarding would be blocked until it is", gone)
        bare = dashboard.page_duplicate_precheck("CO-0801", {"result": "inventory_no_match", "tenants_checked": 594})
        self.assertNotIn("Production duplicate check", bare)  # a Start run's report has no gate section

    def test_created_date_accepts_epoch_ms_or_iso_and_never_guesses(self):
        self.assertEqual(dashboard._created_date(1759622400000), "2025-10-05")
        self.assertEqual(dashboard._created_date("2026-10-04T16:49:00Z"), "2026-10-04")
        for value in (None, "", "yesterday", True, 10 ** 20, {"$date": 1}):
            self.assertEqual(dashboard._created_date(value), "—")

    def test_tampered_header_counts_cannot_inject_html(self):
        self._collect()
        directory = inventory.env_dir(self.root, "prod-clone")
        snapshot = next(directory.glob("redash-prod-clone-inventory-*.json"))
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        data.update({"deleted_count": "<img src=x onerror=alert(1)>", "uuid_missing_count": "<b>x</b>",
                     "schema_drift": "not a dict"})
        snapshot.write_text(json.dumps(data), encoding="utf-8")
        page = dashboard.render_inventory(env="prod-clone")
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("<b>x</b>", page)

    def test_dev_page_is_unchanged_and_unknown_environments_fall_back_to_it(self):
        self._collect()  # only a clone snapshot exists, so the DEV page has nothing to show
        for name in ("dev", "nonsense", ""):
            page = dashboard.render_inventory(env=name)
            self.assertIn("DEV (Leonardo Development)", page)
            self.assertIn("No tenant inventory has been exported yet", page)
            self.assertIn("/attended/inventory-refresh", page)
        self.assertNotIn("prod-clone", dashboard.render_inventory())  # no environment switch: one tab, one environment
        self.assertIn("DevOps — Leonardo Development tenants", dashboard.render_inventory())


class TenantsTabTests(unittest.TestCase):
    """2026-10-05: Tenants = production clone (with a duplicate check), DevOps = Leonardo Development."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for target in (patch.object(dashboard, "inventory_root", return_value=self.root),
                       patch.object(runner, "inventory_root", return_value=self.root)):
            target.start()
            self.addCleanup(target.stop)

    def _collect(self, *rows, retrieved=RETRIEVED_FRESH):
        collector.collect(FakeOpener({GET: result(list(rows) or [row(1), row(2)], retrieved)}), FAKE_KEY,
                          root=self.root, now=NOW)

    def test_sidebar_has_separate_tenants_and_devops_tabs(self):
        for active in ("tenants", "inventory"):
            shell = dashboard._app_shell("t", "", active=active)
            self.assertIn("<a href='/tenants'" + (" class='active'" if active == "tenants" else "") + ">Tenants</a>", shell)
            self.assertIn("<a href='/inventory'" + (" class='active'" if active == "inventory" else "") + ">DevOps</a>", shell)
            self.assertEqual(shell.count("class='active'"), 1)
        self._collect()
        self.assertIn("<a href='/tenants' class='active'>", dashboard.render_inventory(env="prod-clone"))
        self.assertIn("<a href='/inventory' class='active'>", dashboard.render_inventory(env="dev"))

    def test_tenants_tab_has_the_gate_note_and_no_manual_check_or_refresh_while_devops_keeps_refresh(self):
        self._collect()
        tenants = dashboard.render_inventory(env="prod-clone", top=dashboard.PRODUCTION_GATE_NOTE)
        self.assertIn("Every onboarding Start checks this production list first; a match stops it.", tenants)
        self.assertNotIn("Check a CO for duplicates in production", tenants)
        self.assertNotIn("production-duplicate-check", tenants)
        self.assertNotIn("<form method='post'", tenants)
        self.assertNotIn("/attended/inventory-refresh", tenants)
        devops = dashboard.render_inventory(env="dev")
        self.assertIn("/attended/inventory-refresh", devops)
        self.assertNotIn("production-duplicate-check", devops)

    def test_the_old_clone_url_redirects_keeping_the_filters(self):
        class _Request:
            def __init__(self, path):
                self.path, self.redirects, self.pages, self.headers = path, [], [], {}
            def send_redirect(self, location):
                self.redirects.append(location)
            def send_page(self, status, page):
                self.pages.append((int(status), page))
        with patch.object(dashboard, "login_required", return_value=False),                 patch.object(dashboard, "request_origin_problem", return_value=None):
            request = _Request("/inventory?env=prod-clone&q=acme&sort=scan&dir=desc&scan=COMPLETED&junk=1")
            dashboard.Handler.do_GET(request)
            self.assertEqual(request.redirects, ["/tenants?q=acme&sort=scan&dir=desc&scan=COMPLETED"])
            bare = _Request("/inventory?env=prod-clone")
            dashboard.Handler.do_GET(bare)
            self.assertEqual(bare.redirects, ["/tenants"])
            self._collect()
            tenants = _Request("/tenants")
            dashboard.Handler.do_GET(tenants)
            self.assertEqual((tenants.redirects, tenants.pages[0][0]), ([], 200))
            self.assertIn("Every onboarding Start checks this production list first", tenants.pages[0][1])
            self.assertNotIn("Check a CO for duplicates in production", tenants.pages[0][1])

    def test_the_manual_check_endpoint_and_form_are_gone(self):
        # Owner decision 2026-10-05: the production check is automatic at Start, never a manual option.
        self.assertNotIn("/attended/production-duplicate-check", dashboard.POST_ROUTES)
        for name in ("production_check_form", "production_check_result", "run_production_duplicate_check"):
            self.assertFalse(hasattr(dashboard, name), name)
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        self.assertNotIn("production-duplicate-check", source)


class ProductionDuplicateCheckRunnerTests(unittest.TestCase):
    """The read-only runner function and CLI behind the Tenants tab's check."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        source = MagicMock(tenant_name="Brand New Customer")
        self.contract = MagicMock(extra_lookups=None, primary_domain=lambda _s: "brandnew.example.com",
                                  load_source=MagicMock(return_value=source))
        for target in (patch.object(runner, "inventory_root", return_value=self.root),
                       patch.object(runner, "_log", return_value=MagicMock()),
                       patch.dict(runner.ROUTES, {runner.CE_ENGINE: self.contract})):
            target.start()
            self.addCleanup(target.stop)

    def _collect(self, *rows, now=NOW):
        fresh = (now - timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%S')
        collector.collect(FakeOpener({GET: result(list(rows), fresh)}), FAKE_KEY, root=self.root, now=now)

    def test_no_snapshot_fails_closed_and_stale_snapshots_too(self):
        report = runner.run_production_duplicate_check("CO-0801")
        self.assertEqual((report["result"], report["production_clone"]["blocks"], report["production_clone"]["reason"]),
                         ("production_clone_unavailable", True, "inventory_snapshot_missing"))
        self._collect(row(1), row(2), now=datetime.now(timezone.utc) - timedelta(hours=9))
        stale = runner.run_production_duplicate_check("CO-0801")["production_clone"]
        self.assertEqual((stale["blocks"], stale["reason"]), (True, "inventory_snapshot_stale"))

    def test_name_primary_and_alternate_domain_matches_block_and_only_the_clone_is_read(self):
        self._collect(row(1, accountName="brand  NEW customer"), row(2, accountDomain="BrandNew.example.com"),
                      row(3, alternateDomains=["brandnew.example.com"]), row(4), now=datetime.now(timezone.utc))
        with patch.object(inventory, "load_latest", wraps=inventory.load_latest) as load:
            report = runner.run_production_duplicate_check("CO-0801")
        self.assertEqual([call.args[1] for call in load.call_args_list], ["prod-clone"])  # never the DEV inventory
        gate = report["production_clone"]
        self.assertEqual((gate["result"], gate["blocks"], len(gate["matches"])), ("duplicate_production_clone_match", True, 3))
        self.assertEqual([m["reasons"] for m in gate["matches"]], [["tenant_name"], ["primary_domain"], ["alternate_domain"]])

    def test_a_no_match_does_not_block_but_is_not_a_clearance(self):
        self._collect(row(1), row(2), now=datetime.now(timezone.utc))
        gate = runner.run_production_duplicate_check("CO-0801")["production_clone"]
        self.assertEqual((gate["result"], gate["blocks"]), ("production_clone_no_match", False))

    def test_bad_input_is_refused_before_any_read(self):
        self.assertEqual(runner.run_production_duplicate_check("CO-1")["result"], "invalid_co_reference")
        self.assertEqual(runner.run_production_duplicate_check("CO-0801", "nope")["result"], "route_unsupported")
        self.contract.load_source.assert_not_called()
        self.contract.load_source.side_effect = RuntimeError("salesforce_source_unavailable")
        self.assertEqual(runner.run_production_duplicate_check("CO-0801")["result"], "salesforce_source_unavailable")

    def test_the_cli_prints_codes_ids_and_counts_but_no_names(self):
        self._collect(row(1, accountName="Secret Name Ltd"), row(2), now=datetime.now(timezone.utc))
        self.contract.load_source.return_value.tenant_name = "Secret Name Ltd"
        out = io.StringIO()
        with patch.object(sys, "argv", ["x", "--production-duplicate-check", "--co", "CO-0801"]),                 contextlib.redirect_stdout(out):
            self.assertEqual(runner.main(), 0)
        printed = json.loads(out.getvalue())
        self.assertEqual((printed["result"], printed["blocks"]), ("duplicate_production_clone_match", True))
        self.assertEqual(printed["matches"][0]["reasons"], ["tenant_name"])
        self.assertNotIn("Secret Name", out.getvalue())
        self.assertNotIn("@", out.getvalue())
        self.assertEqual(printed["salesforce_writeback"], "not_performed")


@unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
class KeyStoreTests(unittest.TestCase):
    def test_the_key_round_trips_and_is_not_stored_in_plain_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "query-251.key"
            collector.store_key("  " + FAKE_KEY + "\n", path)
            blob = path.read_bytes()
            self.assertNotIn(FAKE_KEY.encode(), blob)
            self.assertNotIn(FAKE_KEY[:12].encode(), blob)
            self.assertEqual(collector.load_key(path), FAKE_KEY)
            self.assertEqual([entry.name for entry in path.parent.iterdir()], ["query-251.key"])  # no temp file left

    def test_bad_keys_and_unreadable_blobs_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "query-251.key"
            for bad in ("", "short", "has space in it 1234567890123456", "x" * 80, "key;rm -rf 1234567890123456"):
                with self.subTest(bad=bad), self.assertRaises(collector.CollectorError) as raised:
                    collector.store_key(bad, path)
                self.assertEqual(raised.exception.reason, "redash_key_format_invalid")
            self.assertFalse(path.exists())
            with self.assertRaises(collector.CollectorError) as raised:
                collector.load_key(path)
            self.assertEqual(raised.exception.reason, "redash_key_unavailable")
            path.write_bytes(b"not a dpapi blob")
            with self.assertRaises(collector.CollectorError) as raised:
                collector.load_key(path)
            self.assertEqual(raised.exception.reason, "redash_key_unavailable")


if __name__ == "__main__":
    unittest.main()
