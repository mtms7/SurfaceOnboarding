"""Redash query 253 (inventory v2) beside the live query 251: per-query schema, URL paths, key names, v2 snapshot rows,
and the clone push (builder + VM ingest) accepting v1 and v2.

Hermetic and portable (Windows and Linux): fake rows, fake key, temporary folders, no network, no Redash."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import tools.build_clone_push_bundle as builder
import tools.ingest_clone_push as ingest
import tools.redash_inventory_collector as collector
from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import redash_inventory as redash
from integration.onboarding import vm_snapshot as vs
from integration.tests import test_clone_push as cp
from integration.tests import test_redash_inventory as base

NOW = cp.NOW
FAKE_KEY = base.FAKE_KEY


def row253(index: int, **overrides):
    values = {key: value for key, value in cp.row(index).items() if key != "leakedCredentialsSettings.spyCloudSettings"}
    values.update({
        "_created": "2026-08-11 13:57:03", "userEmailDomains": ["customer.example.com"], "accountCountryCode": "US",
        "enumerationScanningInterval": "DAILY", "leakedCredentialsScanningInterval": "WEEKLY",
        "subDomainsCount": 7, "additionalNetworksCount": 0, "pendingValidationAssetsCount": 2,
        "leakedCredentialsScannedDomains": ["a.example.com"], "staticOutboundIpEnabled": False,
        "fullNucleiScanEnabled": True, "aiEnabled": True, "webAiAttackerEnabled": None, "vodBetaEnabled": False,
        "webDictionaryBruteForceEnabled": True, "webDorkingEnabled": False, "subDomainsReconEnabled": True,
        "authenticatedTestingEnabled": False, "automatedDiscoveryEnabled": True,
        "domainsMultiAttackStackEnabled": True, "domainsMultiAttackStackAssetsPerStack": 5.0,
        "subDomainsMultiAttackStackEnabled": False, "subDomainsMultiAttackStackAssetsPerStack": None,
        "primaryUserMfaRequired": True, "primaryUserPresent": True, "spyCloudEnabled": True,
        "accountLicense.reconLevel": "FULL", "accountLicense.notificationsAllowed": True,
        "accountLicense.multipleUsersAllowed": True, "accountLicense.apiAccessAllowed": False,
        "accountLicense.phishingEnabled": False, "accountLicense.provisioningEnabled": True,
        "accountLicense.allowedModules": ["recon", "validation"], "accountLicense.elevatedFeaturesEnabled": False,
        "lastLeakedCredentialsExecutionStatus": "DONE", "lastLeakedCredentialsExecutionStart": "2026-10-19T11:45:00.000Z",
        "lastLeakedCredentialsExecutionEnd": "2026-10-19T11:55:00.000Z", "leakedCredentialsExecutionsCount": 1,
    })
    values.update(overrides)
    return values


def reply(rows, retrieved="2026-10-17T11:30:00"):
    names = list(rows[0])
    return {"query_result": {"retrieved_at": retrieved, "data": {
        "columns": [{"name": name, "type": "string"} for name in names], "rows": rows}}}


def snapshot_253(root: Path, rows: int, age=timedelta(minutes=30), **overrides) -> dict:
    items = [row253(index, **overrides) for index in range(1, rows + 1)]
    normalized = redash.normalize(reply(items, (cp.NOW - age).strftime("%Y-%m-%dT%H:%M:%S")), 253)
    payload = inventory.snapshot_payload(
        inventory.require_environment("prod-clone"), inventory.assemble_rows(normalized.rows), normalized.retrieved_at,
        acquisition=inventory.REDASH_ACQUISITION, source=redash.source_meta(normalized, NOW, refreshed=False),
        expected_paths=redash.expected_paths(253))
    payload = redash.attach_v2(payload, normalized)
    inventory.write_snapshot(payload, root, keep=4)
    return payload


class NormalizeTests(unittest.TestCase):
    def test_253_columns_are_typed_flat_and_null_safe(self):
        normalized = redash.normalize(reply([row253(1)]), 253)
        self.assertEqual((normalized.query_id, normalized.unexpected_columns, normalized.missing_columns), (253, (), ()))
        v2 = next(iter(normalized.v2_rows.values()))
        self.assertEqual(v2["account_country_code"], "US")
        self.assertEqual(v2["user_email_domains"], ["customer.example.com"])
        self.assertIs(v2["static_outbound_ip_enabled"], False)
        self.assertEqual(v2["domains_multi_attack_stack_assets_per_stack"], 5)  # float 5.0 -> int
        self.assertEqual(v2["operator_count"], 1)
        self.assertIs(v2["spy_cloud_enabled"], True)
        self.assertNotIn("web_ai_attacker_enabled", v2)  # None is omitted, never guessed
        self.assertNotIn("sub_domains_multi_attack_stack_assets_per_stack", v2)
        # the v1 row still gets the license fields and the single SpyCloud boolean
        built = normalized.rows[0]
        self.assertEqual(built["accountLicense"]["reconLevel"], "FULL")
        self.assertIs(inventory.spycloud_enabled(built), True)
        minimized = inventory.minimize_row(built)
        self.assertIs(minimized["license"]["provisioning_enabled"], True)
        self.assertEqual(minimized["license"]["allowed_modules"], ["recon", "validation"])

    def test_wrong_types_fail_closed(self):
        for column, bad in (("aiEnabled", "yes"), ("subDomainsCount", True), ("subDomainsCount", -1),
                            ("accountCountryCode", 5), ("userEmailDomains", "not json"), ("primaryUserPresent", 1)):
            with self.subTest(column=column), self.assertRaises(inventory.InventoryError) as raised:
                redash.normalize(reply([row253(1, **{column: bad})]), 253)
            self.assertEqual(raised.exception.reason, "redash_schema_unavailable")

    def test_a_bad_domain_list_cell_is_withheld_not_the_whole_clone(self):
        """Owner decision 2026-10-19 (option A): only userEmailDomains / leakedCredentialsScannedDomains cells that are real
        lists but not hostname lists (too many items, an email, an odd character) become unknown; the count is recorded."""
        many = ["host%d.example.com" % n for n in range(51)]
        cases = (("userEmailDomains", ["ok.example.com", "SECRET person@example.com"]),
                 ("userEmailDomains", many), ("leakedCredentialsScannedDomains", many),
                 ("leakedCredentialsScannedDomains", ["bad!char.com"]))
        for column, bad in cases:
            with self.subTest(column=column, size=len(bad)):
                normalized = redash.normalize(reply([row253(1), row253(2, **{column: bad})]), 253)
                self.assertEqual(dict(normalized.withheld_cells), {column: 1})
                self.assertEqual(len(normalized.rows), 2)
                tenant = normalized.v2_rows[normalized.rows[1]["id"]]
                self.assertNotIn(redash.v2_key(column), tenant)  # unknown: omitted, like any null
                self.assertNotIn("SECRET", json.dumps([normalized.rows, dict(normalized.v2_rows)]))
                self.assertEqual(redash.source_meta(normalized, NOW, refreshed=False)["withheld_list_cells"], {column: 1})

    def test_only_those_two_list_cells_are_withheld_everything_else_stays_strict(self):
        for column, bad in (("userEmailDomains", "not json"), ("leakedCredentialsScannedDomains", 5),
                            ("subDomainsCount", True), ("lastLeakedCredentialsExecutionStatus", "done!")):
            with self.subTest(column=column), self.assertRaises(inventory.InventoryError) as raised:
                redash.normalize(reply([row253(1, **{column: bad})]), 253)
            self.assertEqual(raised.exception.reason, "redash_schema_unavailable")
        clean = redash.normalize(reply([row253(1)]), 253)
        self.assertEqual(dict(clean.withheld_cells), {})
        self.assertEqual(redash.source_meta(clean, NOW, refreshed=False)["withheld_list_cells"], {})

    def test_strict_columns_per_query(self):
        v251 = redash.normalize(reply([row253(1)]), 251)  # 253's extra columns are unexpected for 251
        self.assertIn("accountCountryCode", v251.unexpected_columns)  # (the report is capped at 20 names)
        self.assertEqual(len(v251.unexpected_columns), 20)
        self.assertEqual(v251.v2_rows, {})
        self.assertNotIn("v2", json.dumps(redash.normalize(reply([base.row(1)]), 251).rows))
        extra = redash.normalize(reply([row253(1, surpriseColumn=1)]), 253)
        self.assertEqual(extra.unexpected_columns, ("surpriseColumn",))
        short = row253(1)
        del short["aiEnabled"]
        with self.assertRaises(inventory.InventoryError) as raised:  # query 253: a missing column is an error, not a count
            redash.normalize(reply([short]), 253)
        self.assertEqual(raised.exception.reason, "redash_columns_missing")
        with self.assertRaises(inventory.InventoryError):  # required columns stay required
            redash.normalize(reply([{key: value for key, value in row253(1).items() if key != "accountName"}]), 253)

    def test_253_accepts_the_251_spycloud_spellings(self):
        for column, value in (("leakedCredentialsSettings.spyCloudSettings.enabled", False),
                              ("leakedCredentialsSettings.spyCloudSettings", {"enabled": False})):
            item = row253(1)
            del item["spyCloudEnabled"]
            item[column] = value
            normalized = redash.normalize(reply([item]), 253)
            self.assertEqual(normalized.unexpected_columns, ())
            self.assertIs(inventory.spycloud_enabled(normalized.rows[0]), False)

    def test_unsupported_query_ids_are_refused(self):
        for bad in (0, 252, 254, "253", None, True):
            with self.subTest(bad=bad), self.assertRaises(inventory.InventoryError):
                redash.normalize(reply([row253(1)]), bad)


class SelectionTests(unittest.TestCase):
    def test_argument_beats_env_beats_default(self):
        self.assertEqual(collector.resolve_query_id(None, {}), 251)
        self.assertEqual(collector.resolve_query_id(None, {collector.QUERY_ID_ENV: "253"}), 253)
        self.assertEqual(collector.resolve_query_id(251, {collector.QUERY_ID_ENV: "253"}), 251)
        self.assertEqual(collector.resolve_query_id(253, {}), 253)
        for bad in ("abc", "252", "0"):
            with self.assertRaises(collector.CollectorError) as raised:
                collector.resolve_query_id(None, {collector.QUERY_ID_ENV: bad})
            self.assertEqual(raised.exception.reason, "redash_query_id_unsupported")

    def test_paths_are_exact_per_query(self):
        self.assertEqual(collector.results_path(), collector.RESULTS_PATH)
        self.assertEqual(collector.results_path(253), "/api/queries/253/results.json")
        opener = base.FakeOpener({("GET", "/api/queries/251/results.json"): {}, ("POST", "/api/queries/253/results"): {}})
        with self.assertRaises(collector.CollectorError) as raised:  # 251's path is not allowed while reading 253
            collector._call(opener, FAKE_KEY, "GET", "/api/queries/251/results.json", None, 253)
        self.assertEqual(raised.exception.reason, "redash_path_not_allowed")
        with self.assertRaises(collector.CollectorError):
            collector._call(opener, FAKE_KEY, "POST", "/api/queries/253/results", {}, 251)
        with self.assertRaises(collector.CollectorError) as unsupported:  # an unsupported id has no allowed path
            collector._call(opener, FAKE_KEY, "GET", "/api/queries/252/results.json", None, 252)
        self.assertEqual(unsupported.exception.reason, "redash_query_id_unsupported")
        self.assertEqual(opener.requests, [])

    def test_key_file_and_env_names_are_per_query(self):
        with patch.object(collector, "state_dir", return_value=Path("S")):
            self.assertEqual(collector.key_path(), Path("S") / "query-251.key")
            self.assertEqual(collector.key_path(253), Path("S") / "query-253.key")
        self.assertEqual(collector.key_file_env(), "SURFACE_REDASH_KEY_FILE")
        self.assertEqual(collector.key_file_env(253), "SURFACE_REDASH_KEY_FILE_253")

    @unittest.skipUnless(os.name == "posix", "private-file mode checks are POSIX only")
    def test_load_key_uses_the_selected_querys_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder, "k251"), Path(folder, "k253")
            first.write_text("A" * 40 + "\n", encoding="ascii")
            second.write_text("B" * 40 + "\n", encoding="ascii")
            for path in (first, second):
                path.chmod(0o600)
            env = {"SURFACE_REDASH_KEY_FILE": str(first), "SURFACE_REDASH_KEY_FILE_253": str(second)}
            with patch.dict(os.environ, env):
                self.assertEqual(collector.load_key(), "A" * 40)
                self.assertEqual(collector.load_key(query_id=253), "B" * 40)

    def test_main_refuses_an_unsupported_id_without_output_of_secrets(self):
        out = io.StringIO()
        temp = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with contextlib.redirect_stdout(out), patch.object(collector, "state_dir", return_value=Path(temp.name)):  # never the real log
            code = collector.main(["--probe", "--query-id", "999"])
        self.assertEqual((code, json.loads(out.getvalue())), (1, {"result": "redash_query_id_unsupported"}))


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_collect_253_writes_a_v2_snapshot_that_loads_and_verifies(self):
        path = ("GET", "/api/queries/253/results.json")
        report = collector.collect(base.FakeOpener({path: reply([row253(1), row253(2)])}), FAKE_KEY, root=self.root,
                                   now=NOW, refresh=False, query_id=253)
        self.assertEqual((report["result"], report["row_count"], report["query_id"], report["row_schema"]),
                         ("inventory_collected", 2, 253, "v2"))
        self.assertNotIn(FAKE_KEY, json.dumps(report))
        loaded = inventory.load_latest(self.root, "prod-clone", max_age=timedelta(days=1), now=NOW)
        self.assertEqual((loaded["row_schema"], loaded["source"]["query_id"]), ("v2", 253))
        self.assertEqual(loaded["schema_drift"], {"unknown": [], "missing": [], "type_changed": []})
        self.assertEqual(redash.validate_row_schema(loaded), "v2")
        self.assertEqual(loaded["tenants"][0]["v2"]["account_country_code"], "US")

    def test_collect_251_is_unchanged_v1(self):
        report = collector.collect(base.FakeOpener({base.GET: base.result([base.row(1)])}), FAKE_KEY, root=self.root,
                                   now=base.NOW, refresh=False)
        self.assertNotIn("query_id", report)
        self.assertNotIn("row_schema", report)
        loaded = inventory.load_latest(self.root, "prod-clone", max_age=timedelta(days=1), now=base.NOW)
        self.assertNotIn("row_schema", loaded)
        self.assertEqual(redash.validate_row_schema(loaded), "v1")
        self.assertNotIn("v2", loaded["tenants"][0])

    def test_changed_253_query_stops_and_keeps_the_previous_snapshot(self):
        path = ("GET", "/api/queries/253/results.json")
        collector.collect(base.FakeOpener({path: reply([row253(1)])}), FAKE_KEY, root=self.root, now=NOW,
                          refresh=False, query_id=253)
        before = (self.root / "prod-clone" / "latest.json").read_bytes()
        with self.assertRaises(collector.CollectorError) as raised:
            collector.collect(base.FakeOpener({path: reply([row253(1, newColumn=1)])}), FAKE_KEY, root=self.root,
                              now=NOW, refresh=False, query_id=253)
        self.assertEqual(raised.exception.reason, "redash_query_changed")
        self.assertEqual((self.root / "prod-clone" / "latest.json").read_bytes(), before)

    def test_probe_reports_253_shape_only(self):
        path = ("GET", "/api/queries/253/results.json")
        report = collector.probe(base.FakeOpener({path: reply([row253(1)])}), FAKE_KEY, now=NOW, query_id=253)
        self.assertEqual((report["normalize"], report["query_id"], report["unexpected_columns"]), ("ok", 253, []))
        self.assertNotIn("customer.example.com", json.dumps(report))

    def test_probe_reports_withheld_list_cells_as_counts(self):
        path = ("GET", "/api/queries/253/results.json")
        report = collector.probe(base.FakeOpener({path: reply([row253(1), row253(2, userEmailDomains=["a@b.example"])])}),
                                 FAKE_KEY, now=NOW, query_id=253)
        self.assertEqual((report["normalize"], report["withheld_list_cells"]), ("ok", {"userEmailDomains": 1}))
        self.assertNotIn("a@b", json.dumps(report))

    def test_probe_names_the_failing_columns_without_a_value(self):
        path = ("GET", "/api/queries/253/results.json")
        bad = row253(2)
        bad["lastLeakedCredentialsExecutionStatus"] = "Done with SECRET value!"
        bad["lastLeakedCredentialsExecutionEnd"] = "not a date SECRET"
        report = collector.probe(base.FakeOpener({path: reply([row253(1), bad])}), FAKE_KEY, now=NOW, query_id=253)
        self.assertEqual(report["normalize"], "redash_schema_unavailable")
        self.assertEqual(report["failing_columns"], {"lastLeakedCredentialsExecutionEnd": 1, "lastLeakedCredentialsExecutionStatus": 1})
        self.assertNotIn("SECRET", json.dumps(report))

    def test_probe_says_why_a_list_column_fails_without_a_value(self):
        path = ("GET", "/api/queries/253/results.json")
        many, odd = row253(2), row253(3)
        many["alternateDomains"] = ["host%d.example.com" % n for n in range(redash.ALT_DOMAINS_MAX_ITEMS + 1)]
        odd["alternateDomains"] = ["ok.example.com", "SECRET person@example.com"]
        report = collector.probe(base.FakeOpener({path: reply([row253(1), many, odd])}), FAKE_KEY, now=NOW, query_id=253)
        self.assertEqual(report["failing_columns"], {"alternateDomains": 2})
        self.assertEqual(report["failing_list_reasons"], {"alternateDomains:item_not_hostname": 1, "alternateDomains:too_many_items": 1})
        self.assertNotIn("SECRET", json.dumps(report))

    def test_probe_sizes_the_failing_lists_without_a_value(self):
        path = ("GET", "/api/queries/253/results.json")
        many, odd = row253(2), row253(3)
        many["alternateDomains"] = ["host%d.example.com" % n for n in range(redash.ALT_DOMAINS_MAX_ITEMS + 70)]
        odd["alternateDomains"] = ["ok.example.com", "person@example.com", "SECRET has space", "x" * 300, "bad!char.com"]
        report = collector.probe(base.FakeOpener({path: reply([row253(1), many, odd])}), FAKE_KEY, now=NOW, query_id=253)
        detail = report["failing_list_detail"]["alternateDomains"]
        self.assertEqual(detail["max_items"], redash.ALT_DOMAINS_MAX_ITEMS + 70)
        self.assertEqual(detail["item_flags"], {"has_at": 1, "has_space": 1, "over_253_chars": 1, "other_character": 1})
        self.assertGreater(report["max_row_list_chars"], 0)
        self.assertNotIn("SECRET", json.dumps(report))

    def test_probe_of_a_good_result_has_no_failing_columns(self):
        path = ("GET", "/api/queries/253/results.json")
        report = collector.probe(base.FakeOpener({path: reply([row253(1)])}), FAKE_KEY, now=NOW, query_id=253)
        self.assertNotIn("failing_columns", report)


class PushTests(cp.Base):
    def v2_bundle(self, rows=20, mutate=None, created=NOW, age=timedelta(minutes=30)) -> bytes:
        shutil.rmtree(self.desktop, ignore_errors=True)
        payload = snapshot_253(self.desktop, rows, age)
        if mutate is not None:  # a correctly hashed but malformed snapshot
            mutate(payload)
            payload["rows_sha256"] = inventory._rows_sha256(payload["tenants"])  # noqa: SLF001
            shutil.rmtree(self.desktop, ignore_errors=True)
            inventory.write_snapshot(payload, self.desktop, keep=4)
        files = builder.publisher._inventory_pair(self.desktop, "prod-clone", "redash-prod-clone-",
                                                  vs.INVENTORY_CLONE_LATEST, vs.INVENTORY_CLONE_SNAPSHOT)
        return vs.build_bundle(files, cp.KEY, ingest.PRODUCER, created)

    def refused(self, raw, now=NOW):
        with self.assertRaises(ingest.PushRefused) as raised:
            ingest.ingest(raw, cp.KEY, self.state, now)
        return raised.exception.code

    def test_v2_clone_is_built_ingested_and_served(self):
        self.v2_bundle()
        out = self.tmp / "bundle.zip"
        counts = builder.build(str(self.key_file), out, root=self.desktop, now=NOW)
        self.assertEqual(counts["rows"], 20)
        message = ingest.ingest(out.read_bytes(), cp.KEY, self.state, NOW)
        self.assertTrue(message.startswith("ingested"))
        served = self.served()
        self.assertEqual(served["row_schema"], "v2")
        self.assertEqual(served["tenants"][0]["v2"]["primary_user_mfa_required"], True)

    def test_a_v1_clone_still_loads_and_a_v2_can_replace_it(self):
        self.assertTrue(ingest.ingest(self.bundle(rows=20, age=timedelta(minutes=50)), cp.KEY, self.state, NOW)
                        .startswith("ingested"))
        self.assertNotIn("row_schema", self.served())
        self.assertTrue(ingest.ingest(self.v2_bundle(), cp.KEY, self.state, NOW).startswith("ingested"))
        self.assertEqual(self.served()["row_schema"], "v2")

    def test_shrink_floor_and_high_water_apply_to_v2_unchanged(self):
        ingest.ingest(self.v2_bundle(rows=20), cp.KEY, self.state, NOW)
        later = NOW + timedelta(minutes=10)
        shrunk = self.v2_bundle(rows=17, created=later, age=timedelta(minutes=20))
        self.assertEqual(self.refused(shrunk, later), "result_shrunk")  # 17 < 90% of 20
        self.assertEqual(self.served()["row_count"], 20)

    def test_malformed_v2_rows_are_refused_and_the_current_clone_kept(self):
        ingest.ingest(self.bundle(rows=20, age=timedelta(minutes=50)), cp.KEY, self.state, NOW)
        before = self.served()["rows_sha256"]
        later = NOW + timedelta(minutes=5)
        mutations = {
            "unknown key": lambda p: p["tenants"][0]["v2"].update({"surprise": 1}),
            "wrong type": lambda p: p["tenants"][0]["v2"].update({"ai_enabled": "yes"}),
            "bool as int": lambda p: p["tenants"][0]["v2"].update({"sub_domains_count": True}),
            "null value": lambda p: p["tenants"][0]["v2"].update({"ai_enabled": None}),
            "missing v2": lambda p: p["tenants"][0].pop("v2"),
            "list too long": lambda p: p["tenants"][0]["v2"].update({"user_email_domains": ["a.example.com"] * 501}),
            "unknown marker": lambda p: p.update({"row_schema": "v3"}),
            "v1 with v2 rows": lambda p: p.update({"row_schema": "v1"}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                raw = self.v2_bundle(mutate=mutate, created=later, age=timedelta(minutes=20))
                self.assertEqual(self.refused(raw, later), "row_schema_invalid")
                self.assertEqual(self.served()["rows_sha256"], before)

    def test_builder_refuses_to_sign_a_malformed_v2_clone(self):
        self.v2_bundle(mutate=lambda p: p["tenants"][0]["v2"].update({"surprise": 1}))
        with self.assertRaises(builder.BuildRefused) as raised:
            builder.build(str(self.key_file), self.tmp / "b.zip", root=self.desktop, now=NOW)
        self.assertEqual(raised.exception.code, "row_schema_invalid")
        self.assertFalse((self.tmp / "b.zip").exists())

    def test_v2_snapshot_stays_inside_the_size_limits(self):
        snapshot_253(self.desktop, 50)
        size = max(path.stat().st_size for path in (self.desktop / "prod-clone").glob("redash-*.json"))
        per_row = size / 50
        self.assertLess(per_row * 6000, vs.MAX_FILE_BYTES)  # about 5,450 tenants today; headroom up to 6,000


class RegistrationScriptTests(unittest.TestCase):
    def test_the_task_always_passes_its_query_id_and_checks_that_ids_key_file(self):
        text = (Path(__file__).resolve().parents[2] / "tools" / "register_redash_inventory_task.ps1").read_text(encoding="utf-8")
        self.assertIn("$queryArgs = ' --query-id {0}' -f $QueryId", text)  # 251 too: the env var can never override
        self.assertNotIn("if ($QueryId -eq 251) { '' }", text)
        self.assertIn("query-{0}.key' -f $QueryId", text)


class ShapeTests(unittest.TestCase):
    """Review 2026-10-17: PII-shaped free text, huge numbers and oversized rows never enter the clone."""

    def refused(self, **overrides):
        with self.assertRaises(inventory.InventoryError) as raised:
            redash.normalize(reply([row253(1, **overrides)]), 253)
        return raised.exception.reason

    def test_hostname_lists_reject_pii_shaped_items_and_oversize(self):
        for column in ("alternateDomains", "accountLicense.allowedModules"):
            many = redash.ALT_DOMAINS_MAX_ITEMS + 1 if column == "alternateDomains" else 51  # alternate domains: own bound
            for bad in (["jane@example.com"], ["two words.example.com"], ["a" * 254], ["ok.example.com"] * many, [5], ["tab	.example"]):
                with self.subTest(column=column, bad=bad[:1]):
                    self.assertEqual(self.refused(**{column: bad}), "redash_schema_unavailable")
        # owner decision 2026-10-19: these two lists are withheld (unknown + counted), never stored, never the whole clone
        for column in sorted(redash.WITHHOLDABLE_LIST_COLUMNS):
            for bad in (["jane@example.com"], ["two words.example.com"], ["a" * 254], ["ok.example.com"] * 51, [5], ["tab	.example"]):
                with self.subTest(column=column, withheld=bad[:1]):
                    normalized = redash.normalize(reply([row253(1, **{column: bad})]), 253)
                    self.assertEqual(dict(normalized.withheld_cells), {column: 1})
                    stored = json.dumps([normalized.rows, dict(normalized.v2_rows)])
                    self.assertNotIn(redash.v2_key(column), dict(normalized.v2_rows)[normalized.rows[0]["id"]])
                    for fragment in ("jane@", "two words", "aaaaaaaaaa", "tab\t"):
                        self.assertNotIn(fragment, stored)
        redash.normalize(reply([row253(1, userEmailDomains=["ok.example.com"] * 3, alternateDomains=["Alt.Example.com."])]), 253)

    def test_country_enums_and_text_shapes(self):
        for column, bad in (("accountCountryCode", "us"), ("accountCountryCode", "USA"), ("accountCountryCode", "Jane Doe"),
                            ("enumerationScanningInterval", "Jane Doe <jane@example.com>"), ("accountLicense.reconLevel", "x" * 33),
                            ("leakedCredentialsScanningInterval", "a@b")):
            with self.subTest(column=column):
                self.assertEqual(self.refused(**{column: bad}), "redash_schema_unavailable")

    def test_huge_numbers_are_a_clean_refusal_not_a_traceback(self):
        for column, bad in (("subDomainsCount", 10 ** 400), ("enumerationScanningInterval", 10 ** 400),
                            ("accountLicense.assetsNumber", 10 ** 400), ("lastReconScanDurationMilliseconds", 10 ** 400),
                            ("_modified", 10 ** 400), ("_modified", float("1e400")), ("subDomainsCount", float("inf"))):
            with self.subTest(column=column):
                self.assertIn(self.refused(**{column: bad}), ("redash_schema_unavailable", "redash_date_unparseable"))

    def test_source_query_must_agree_with_the_row_schema(self):
        payload = snapshot_253(Path(__import__("tempfile").mkdtemp()), 3)
        self.assertEqual(redash.validate_row_schema(payload), "v2")
        for mutate in (lambda p: p["source"].update(query_id=251), lambda p: p["source"].update(query_id=999),
                       lambda p: p.pop("source"), lambda p: p.update(row_schema="v1")):
            clone = json.loads(json.dumps(payload))
            mutate(clone)
            with self.assertRaises(inventory.InventoryError) as raised:
                redash.validate_row_schema(clone)
            self.assertEqual(raised.exception.reason, "inventory_row_schema_invalid")
        v1 = json.loads(json.dumps(payload))
        v1.pop("row_schema")
        for tenant in v1["tenants"]:
            tenant.pop("v2")
        v1["source"]["query_id"] = 253  # a v1 snapshot that claims query 253
        with self.assertRaises(inventory.InventoryError):
            redash.validate_row_schema(v1)

    def test_a_row_of_maximal_valid_lists_stays_inside_the_file_limit(self):
        host = "h" * 60 + ".example.com"  # 72 chars
        lists = {"alternateDomains": [host] * 4, "userEmailDomains": [host] * 4, "leakedCredentialsScannedDomains": [host] * 4,
                 "accountLicense.allowedModules": ["m" * 20] * 3}
        redash.normalize(reply([row253(1, **lists)]), 253)  # inside the per-row list budget: accepted
        over = [host] * 49  # under the 50-item cap, over the 1,500-character row budget: only the budget can refuse
        self.assertEqual(self.refused(**{**lists, "userEmailDomains": over}), "redash_schema_unavailable")
        many = [f"alt{n}.example.com" for n in range(1622)]  # query 253: real-sized alternate domains pass (own bounds)
        self.assertEqual(redash.normalize(reply([row253(1, **{**lists, "alternateDomains": many})]), 253).rows[0]["alternateDomains"], many)
        root = Path(__import__("tempfile").mkdtemp())
        snapshot_253(root, 20, **lists, accountName="N" * 120)
        size = max(path.stat().st_size for path in (root / "prod-clone").glob("redash-*.json"))
        # 6,000 worst-case rows plus the whole-result alternate-domain bounds, at the measured cost per character and per item
        def alt_size(alts):
            alt_root = Path(__import__("tempfile").mkdtemp())
            snapshot_253(alt_root, 20, **{**lists, "alternateDomains": alts}, accountName="N" * 120)
            return max(path.stat().st_size for path in (alt_root / "prod-clone").glob("redash-*.json")) / 20
        base = alt_size([])
        narrow = alt_size([f"d{n:04d}.co" for n in range(1000)]) - base  # 1,000 items of 8 characters
        wide = alt_size([f"d{n:04d}" + "x" * 40 + ".co" for n in range(1000)]) - base  # 1,000 items of 48 characters
        per_char = (wide - narrow) / 40_000
        per_item = (narrow - per_char * 8000) / 1000
        worst = (size / 20 * 6000 + per_char * redash.ALT_DOMAINS_MAX_TOTAL_CHARS
                 + per_item * redash.ALT_DOMAINS_MAX_TOTAL_ITEMS)
        self.assertLess(worst, vs.MAX_FILE_BYTES)


if __name__ == "__main__":
    unittest.main()


class CeScanColumnTests(unittest.TestCase):
    """docs/56: the four campaignExecutions columns of query 253 (date and enum kinds, no free text)."""

    def v2(self, **overrides):
        return next(iter(redash.normalize(reply([row253(1, **overrides)]), 253).v2_rows.values()))

    def test_the_four_columns_are_typed(self):
        v2 = self.v2()
        self.assertEqual(v2["ce_status"], "DONE")
        self.assertEqual(v2["ce_start"], 1792410300000)
        self.assertEqual(v2["ce_end"], 1792410900000)
        self.assertEqual(v2["ce_count"], 1)

    def test_no_execution_yet_keeps_only_the_zero_count(self):
        v2 = self.v2(lastLeakedCredentialsExecutionStatus=None, lastLeakedCredentialsExecutionStart=None,
                     lastLeakedCredentialsExecutionEnd=None, leakedCredentialsExecutionsCount=0)
        self.assertEqual(v2["ce_count"], 0)
        self.assertNotIn("ce_status", v2)
        self.assertNotIn("ce_end", v2)

    def test_running_execution_has_no_end(self):
        v2 = self.v2(lastLeakedCredentialsExecutionStatus="PENDING", lastLeakedCredentialsExecutionEnd=None)
        self.assertEqual(v2["ce_status"], "PENDING")
        self.assertNotIn("ce_end", v2)

    def test_free_text_or_wrong_types_fail_closed(self):
        for column, bad in (("lastLeakedCredentialsExecutionStatus", "done; see user@example.com"),
                            ("lastLeakedCredentialsExecutionStatus", "done"), ("lastLeakedCredentialsExecutionStatus", 3),
                            ("lastLeakedCredentialsExecutionStart", "not a date"),
                            ("lastLeakedCredentialsExecutionEnd", True),
                            ("leakedCredentialsExecutionsCount", -1), ("leakedCredentialsExecutionsCount", "x")):
            with self.subTest(column=column, bad=bad), self.assertRaises(redash.InventoryError):
                redash.normalize(reply([row253(1, **{column: bad})]), 253)

    def test_a_missing_column_stops_the_collector(self):
        raw = row253(1)
        del raw["lastLeakedCredentialsExecutionStatus"]
        with self.assertRaises(redash.InventoryError) as caught:
            redash.normalize(reply([raw]), 253)
        self.assertEqual(str(caught.exception), "redash_columns_missing")

    def test_stored_values_round_trip_through_the_schema_check(self):
        normalized = redash.normalize(reply([row253(1)]), 253)
        payload = {"row_schema": "v2", "source": {"query_id": 253},
                   "tenants": [{"id": next(iter(normalized.v2_rows)), "v2": dict(next(iter(normalized.v2_rows.values())))}]}
        self.assertEqual(redash.validate_row_schema(payload), "v2")
        payload["tenants"][0]["v2"]["ce_status"] = "done"
        with self.assertRaises(redash.InventoryError):
            redash.validate_row_schema(payload)
