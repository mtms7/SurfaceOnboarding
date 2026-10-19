"""Unit tests for the read-only production view helpers (docs/46, docs/47). Local temp files only; no network."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from integration.onboarding import production_approval as pa
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_views as views
from integration.tests.test_redash_inventory import FAKE_KEY, GET, FakeOpener, result, row
import tools.redash_inventory_collector as collector

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def approval(ref="PROD-CE-0797-1", co="CO-0797", issued="2026-10-16T09:00:00Z", expires="2026-10-23T09:00:00Z", **extra):
    return {"approval_ref": ref, "co_reference": co, "route": pa.ALLOWED_ROUTE, "scope": pa.SCOPE,
            "source_revision": "SECRET-REVISION-1", "issued_by": "owner@example.test", "issued_at": issued,
            "expires_at": expires, **extra}


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def approvals(self, *records):
        path = self.dir / "production_create_approvals.json"
        path.write_text(json.dumps({item["approval_ref"]: item for item in records}), encoding="utf-8")
        return path


class ApprovalViewTests(TempCase):
    def test_states_valid_expired_consumed_and_no_source_revision(self):
        path = self.approvals(
            approval("PROD-CE-0797-1"),
            approval("PROD-CE-0798-1", co="CO-0798", issued="2026-10-01T09:00:00Z", expires="2026-10-08T09:00:00Z"),
            approval("PROD-CE-0799-1", co="CO-0799", consumed_at="2026-10-16T10:00:00Z"))
        listing = views.list_approvals(path, NOW)
        self.assertEqual(listing["state"], "ok")
        states = {item["co_reference"]: item["state"] for item in listing["items"]}
        self.assertEqual(states, {"CO-0797": "valid", "CO-0798": "expired", "CO-0799": "consumed"})
        self.assertNotIn("SECRET-REVISION-1", json.dumps(listing))
        self.assertNotIn("source_revision", json.dumps(listing))
        self.assertEqual(set(listing["items"][0]), {"approval_ref", "co_reference", "route", "scope", "issued_by", "issued_at",
                                                    "expires_at", "consumed_at", "state"})

    def test_revision_is_validated_like_the_approval_module(self):
        for bad in ("", "x" * 129, None, 5):
            with self.subTest(revision=bad):
                path = self.approvals(approval("PROD-CE-0797-1", source_revision=bad))
                self.assertEqual(views.list_approvals(path, NOW)["state"], "unreadable")
        path = self.approvals(approval("PROD-CE-0797-1", source_revision="x" * 128))
        self.assertEqual(views.list_approvals(path, NOW)["state"], "ok")
        self.assertIs(views.valid_revision, pa.valid_revision)

    def test_superseded_state_and_bad_superseded_stamp(self):
        path = self.approvals(approval("PROD-CE-0797-1", superseded_at="2026-10-16T10:00:00Z"))
        self.assertEqual(views.list_approvals(path, NOW)["items"][0]["state"], "superseded")
        path = self.approvals(approval("PROD-CE-0797-1", superseded_at="soon"))
        self.assertEqual(views.list_approvals(path, NOW)["state"], "unreadable")

    def test_newest_first(self):
        path = self.approvals(approval("PROD-CE-0797-1", issued="2026-10-10T09:00:00Z", expires="2026-10-17T09:00:00Z"),
                              approval("PROD-CE-0797-2", issued="2026-10-15T09:00:00Z", expires="2026-10-22T09:00:00Z"))
        self.assertEqual([i["approval_ref"] for i in views.list_approvals(path, NOW)["items"]],
                         ["PROD-CE-0797-2", "PROD-CE-0797-1"])

    def test_missing_file_is_empty_on_the_desktop_and_unavailable_on_a_server_that_never_receives_it(self):
        path = self.dir / "production_create_approvals.json"
        self.assertEqual(views.list_approvals(path, NOW), {"state": "ok", "reason": "", "items": []})
        self.assertEqual(views.approval_for_co(path, "CO-0797", NOW)["state"], "none")
        unavailable = views.list_approvals(path, NOW, missing_is_unavailable=True)
        self.assertEqual((unavailable["state"], unavailable["items"]), ("unavailable", []))
        self.assertEqual(views.approval_for_co(path, "CO-0797", NOW, missing_is_unavailable=True)["state"], "unavailable")

    def test_corrupt_or_unexpected_files_fail_closed(self):
        path = self.dir / "production_create_approvals.json"
        for content in ("garbage", "[]", json.dumps({"PROD-CE-0797-1": {"approval_ref": "PROD-CE-0797-1"}}),
                        json.dumps({"OTHER-KEY-1": approval("PROD-CE-0797-1")}),
                        json.dumps({"PROD-CE-0797-1": approval(extra_field="x")}),
                        json.dumps({"PROD-CE-0797-1": approval(route="surface")})):
            path.write_text(content, encoding="utf-8")
            self.assertEqual(views.list_approvals(path, NOW)["state"], "unreadable", content)
            self.assertEqual(views.approval_for_co(path, "CO-0797", NOW)["state"], "unreadable", content)

    def test_for_a_co_a_valid_approval_wins_else_the_newest(self):
        path = self.approvals(approval("PROD-CE-0797-1", issued="2026-10-01T09:00:00Z", expires="2026-10-08T09:00:00Z"),
                              approval("PROD-CE-0797-2"),
                              approval("PROD-CE-0797-3", issued="2026-10-02T09:00:00Z", expires="2026-10-09T09:00:00Z"))
        found = views.approval_for_co(path, "CO-0797", NOW)
        self.assertEqual((found["state"], found["item"]["approval_ref"]), ("valid", "PROD-CE-0797-2"))
        self.assertEqual(views.approval_for_co(path, "CO-0001", NOW)["state"], "none")
        only_old = self.approvals(approval("PROD-CE-0797-1", issued="2026-10-01T09:00:00Z", expires="2026-10-08T09:00:00Z"),
                                  approval("PROD-CE-0797-3", issued="2026-10-02T09:00:00Z", expires="2026-10-09T09:00:00Z"))
        found = views.approval_for_co(only_old, "CO-0797", NOW)
        self.assertEqual((found["state"], found["item"]["approval_ref"]), ("expired", "PROD-CE-0797-3"))

    def test_the_expiry_moment_is_expired_and_a_future_issue_is_not_valid(self):
        edge = self.approvals(approval(issued="2026-10-09T12:00:00Z", expires="2026-10-16T12:00:00Z"))
        self.assertEqual(views.approval_for_co(edge, "CO-0797", NOW)["state"], "expired")
        future = self.approvals(approval(issued="2026-10-17T12:00:00Z", expires="2026-10-24T12:00:00Z"))
        self.assertEqual(views.approval_for_co(future, "CO-0797", NOW)["state"], "expired")

    def test_reading_never_changes_the_file(self):
        path = self.approvals(approval())
        before = path.read_bytes()
        views.list_approvals(path, NOW)
        views.approval_for_co(path, "CO-0797", NOW)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["production_create_approvals.json"])


class SummaryApprovalViewTests(TempCase):
    def write(self, **records):
        path = self.dir / "onboarding_review_approvals.json"
        path.write_text(json.dumps(records), encoding="utf-8")
        return path

    def record(self, **extra):
        return {"operator": "owner", "approved_at": "2026-10-16T10:00:00Z", "expires_at": "2026-10-16T14:00:00Z",
                "route": "case_2_new_ce_only", "source_revision": "SECRET-REV", "summary_hash": "a" * 64, **extra}

    def test_states_and_no_hash_or_revision(self):
        path = self.write(**{"CO-0001": self.record(), "CO-0002": self.record(expires_at="2026-10-16T11:00:00Z"),
                             "CO-0003": self.record(consumed_at="2026-10-16T10:30:00Z")})
        listing = views.list_summary_approvals(path, NOW)
        self.assertEqual({i["co_reference"]: i["state"] for i in listing["items"]},
                         {"CO-0001": "valid", "CO-0002": "expired", "CO-0003": "consumed"})
        dumped = json.dumps(listing)
        self.assertNotIn("SECRET-REV", dumped)
        self.assertNotIn("a" * 64, dumped)

    def test_missing_unavailable_and_corrupt(self):
        path = self.dir / "onboarding_review_approvals.json"
        self.assertEqual(views.list_summary_approvals(path, NOW), {"state": "ok", "reason": "", "items": []})
        self.assertEqual(views.list_summary_approvals(path, NOW, missing_is_unavailable=True)["state"], "unavailable")
        path.write_text("garbage", encoding="utf-8")
        self.assertEqual(views.list_summary_approvals(path, NOW)["state"], "unreadable")
        self.assertEqual(views.list_summary_approvals(path, NOW, missing_is_unavailable=True)["state"], "unreadable")


class MarkupIssuerTests(TempCase):
    def test_an_issuer_with_markup_makes_the_file_unreadable(self):
        path = self.approvals(approval(issued_by="<script>x</script>"))
        self.assertEqual(views.list_approvals(path, NOW)["state"], "unreadable")


def good_record(co="CO-0797", outcome="dry_run_passed", **extra):
    return {"co_reference": co, "route": "case_2_new_ce_only", "approval_ref": "PROD-CE-0797-1", "outcome": outcome,
            "tenant_name": "Acme Corp", "domains": ["acme.example"], "account_id": "a" * 24, "account_uuid": "b" * 32,
            "plan_summary": "CE, 1 year", "form_fields_checked": {"tenant_name": "pass", "domains": "fail"},
            "response_shape": ["id", "uuid"], "salesforce_revision": "SECRET-REVISION-1", **extra}


class CreateRecordViewTests(TempCase):
    def test_no_folder_is_empty_on_the_desktop_and_unavailable_where_it_is_never_published(self):
        self.assertEqual(views.create_records_by_co(self.dir), {"state": "ok", "reason": "", "by_co": {}})
        self.assertEqual(views.latest_create_record(self.dir, "CO-0797")["state"], "none")
        self.assertEqual(views.create_records_by_co(self.dir, missing_is_unavailable=True)["state"], "unavailable")
        self.assertEqual(views.latest_create_record(self.dir, "CO-0797", missing_is_unavailable=True)["state"], "unavailable")

    def test_newest_record_per_co_with_only_allowlisted_fields(self):
        pcr.record(self.dir, good_record(outcome="dry_run_passed"), NOW)
        pcr.record(self.dir, good_record(outcome="readback_verified"), NOW + timedelta(minutes=5))
        pcr.record(self.dir, good_record("CO-0800", outcome="refused", refusal_reason="production_locked"), NOW)
        found = views.latest_create_record(self.dir, "CO-0797")
        self.assertEqual(found["state"], "found")
        record = found["record"]
        self.assertEqual(record["outcome"], "readback_verified")
        self.assertEqual(set(record), {"co_reference", "outcome", "recorded_at", "redactions", "domains", "form_checks",
                                       "editable", "route", "approval_ref", "refusal_reason", "tenant_name", "account_id",
                                       "account_uuid", "plan_summary"})
        self.assertEqual(record["form_checks"], {"pass": 1, "fail": 1})
        dumped = json.dumps(found)
        self.assertNotIn("SECRET-REVISION-1", dumped)
        self.assertNotIn("response_shape", dumped)
        self.assertEqual(views.create_records_by_co(self.dir)["by_co"]["CO-0800"]["refusal_reason"], "production_locked")
        self.assertEqual(views.latest_create_record(self.dir, "CO-0001")["state"], "none")

    def test_one_corrupt_record_makes_the_whole_answer_unreadable(self):
        pcr.record(self.dir, good_record(), NOW)
        bad = self.dir / pcr.RECORDS_SUBDIR / "CO-0800-20261016T120000Z.json"
        for content in ("garbage", json.dumps({"co_reference": "CO-0800", "outcome": "created_unverified",
                                              "recorded_at": "2026-10-16T12:00:00Z", "redactions": 0, "unknown_key": "x"})):
            bad.write_text(content, encoding="utf-8")
            found = views.create_records_by_co(self.dir)
            self.assertEqual((found["state"], found["by_co"]), ("unreadable", {}), content)
            self.assertEqual(views.latest_create_record(self.dir, "CO-0797")["state"], "unreadable")

    def test_secret_like_text_never_reaches_a_view(self):
        pcr.record(self.dir, good_record(plan_summary="password=hunter2 Bearer abcdefghijklmnop"), NOW)
        dumped = json.dumps(views.create_records_by_co(self.dir))
        self.assertNotIn("hunter2", dumped)
        self.assertNotIn("Bearer", dumped)


class CreateStateTests(unittest.TestCase):
    def state(self, records, locked=True):
        return views.create_state(records, "CO-0797", locked=locked)

    def test_locked_none_and_outcomes(self):
        ok = {"state": "ok", "by_co": {}}
        self.assertEqual(self.state(ok)["label"], "Locked")
        self.assertEqual(self.state(ok, locked=False)["label"], "Dry run ready")
        for outcome, label in (("dry_run_passed", "Dry run passed"), ("readback_verified", "Verified"),
                               ("created_unverified", "Created, not verified"), ("uncertain_submit", "Uncertain: do not retry"),
                               ("refused", "Refused")):
            view = self.state({"state": "ok", "by_co": {"CO-0797": {"outcome": outcome}}})
            self.assertEqual(view["label"], label)
        self.assertEqual(self.state({"state": "ok", "by_co": {"CO-0797": {"outcome": "readback_verified"}}})["steps_done"], 4)

    def test_unreadable_and_unavailable_never_look_like_none_or_created(self):
        self.assertEqual(self.state({"state": "unreadable"})["label"], "Records unreadable")
        self.assertEqual(self.state({"state": "unavailable"})["label"], "Unavailable on this server")
        for state in ("unreadable", "unavailable"):
            self.assertNotIn(self.state({"state": state})["label"], ("Locked", "Verified", "Dry run ready"))


class ProductionCheckTests(unittest.TestCase):
    def check(self, status, **extra):
        return views.production_check({"status": status, "reason": "", "candidates": [], **extra})

    def test_chips(self):
        self.assertEqual((self.check("not_in_production")["label"], self.check("not_in_production")["blocked"]), ("Clear", False))
        self.assertEqual(self.check("exists_matches")["label"], "Duplicate")
        self.assertEqual(self.check("exists_other")["label"], "Other tenants only")
        self.assertEqual(self.check("exists_other")["code"], "other_tenants")
        live = [{"live_paid": True}, {"live_paid": True}, {"live_paid": False}]
        self.assertEqual(self.check("ambiguous", candidates=live)["label"], "Live paid x 2")
        self.assertEqual(self.check("exists_differs", candidates=live)["label"], "Live paid x 2")
        self.assertEqual(self.check("clone_unavailable", reason="inventory_snapshot_stale")["label"], "Clone stale")
        self.assertEqual(self.check("clone_unavailable", reason="inventory_snapshot_missing")["label"], "Clone unavailable")

    def test_onboarded_is_not_a_duplicate_and_warns_on_deviations(self):
        clean = self.check("onboarded", review={"warnings": []})
        self.assertEqual((clean["label"], clean["code"], clean["css"], clean["blocked"]), ("Onboarded", "onboarded", "chip-ok", False))
        warned = self.check("onboarded", review={"warnings": ["spycloud_off"]})
        self.assertEqual((warned["label"], warned["css"], warned["blocked"]), ("Onboarded", "chip-warn", False))

    def test_identity_mismatch_relabels_onboarded_and_twins_do_not_claim_live_paid(self):
        chip = self.check("onboarded", review={"warnings": ["identity_mismatch"]})
        self.assertEqual((chip["label"], chip["css"]), ("Onboarded: review", "chip-warn"))
        twins = [{"live_paid": False}, {"live_paid": False}]
        chip = self.check("ambiguous", candidates=twins)
        self.assertEqual((chip["label"], chip["code"], chip["blocked"]), ("Ambiguous", "ambiguous", True))
        self.assertEqual(self.check("ambiguous", candidates=[{"live_paid": True}, {"live_paid": True}])["label"], "Live paid x 2")

    def test_a_deleted_or_disabled_uuid_owner_is_onboarded_review_not_clean(self):
        for code in ("tenant_deleted", "tenant_disabled"):
            chip = self.check("onboarded", review={"warnings": [code]})
            self.assertEqual((chip["label"], chip["css"]), ("Onboarded: review", "chip-warn"))

    def test_a_duplicate_says_the_uuid_check_did_not_run_when_it_did_not(self):
        chip = self.check("exists_matches", uuid_checked=False)
        self.assertIn("UUID check runs on the VM dashboard", chip["reason"])
        self.assertNotIn("UUID check", self.check("exists_matches", uuid_checked=True)["reason"])

    def test_uuid_not_in_clone_and_not_checked_are_blocked_and_never_say_clone_unavailable(self):
        self.assertTrue(self.check("uuid_not_in_clone")["blocked"])
        self.assertEqual(self.check("uuid_not_in_clone")["label"], "UUID set, not in clone")
        chip = self.check("not_checked", reason="production_inputs_not_prepared")
        self.assertEqual((chip["label"], chip["blocked"]), ("Not checked", True))
        self.assertIn("only prepared for CE-only", chip["reason"])
        self.assertNotIn("Clone", chip["label"] + chip["reason"].split("clone itself")[0])

    def test_anything_unexpected_is_blocked_never_clear(self):
        for bad in (None, {}, {"status": "bogus"}, "x", {"status": None}):
            chip = views.production_check(bad)
            self.assertTrue(chip["blocked"], bad)
            self.assertNotEqual(chip["label"], "Clear")
        for status in ("exists_matches", "exists_differs", "exists_other", "ambiguous", "clone_unavailable", "not_checked", "uuid_not_in_clone"):
            self.assertTrue(self.check(status)["blocked"])


class DuplicateValidationTests(unittest.TestCase):
    CANDIDATES = [
        {"name": "Acme Corp", "domain": "other.example", "live_paid": True, "deleted": False, "license_type": "PREPAID_ANNUAL"},
        {"name": "Different", "domain": "ACME.example", "live_paid": False, "deleted": False, "license_type": "Trial"},
        {"name": "Third", "domain": "third.example", "live_paid": True, "deleted": False, "license_type": "PREPAID_ANNUAL"}]

    def test_by_name_by_domain_and_live_paid_exact_name(self):
        match = {"status": "exists_other", "candidates": self.CANDIDATES, "tenants_checked": 1204}
        result_ = views.duplicate_validation(match, ["  acme   CORP "], ["acme.example"])
        self.assertTrue(result_["available"])
        self.assertEqual([c["name"] for c in result_["by_name"]], ["Acme Corp"])
        self.assertEqual([c["name"] for c in result_["by_domain"]], ["Different"])
        self.assertEqual((result_["other"], result_["live_paid_exact"], result_["tenants_checked"]), (1, 1, 1204))

    def test_no_clone_is_unavailable_never_zero_matches(self):
        for match in (None, {"status": "clone_unavailable"}, {"status": "weird"}, "x"):
            self.assertFalse(views.duplicate_validation(match, ["Acme"], ["acme.example"])["available"])
        clear = views.duplicate_validation({"status": "not_in_production", "candidates": []}, ["Acme"], [])
        self.assertTrue(clear["available"])
        self.assertEqual((clear["by_name"], clear["by_domain"], clear["live_paid_exact"]), ([], [], 0))


class TimingWindowTests(unittest.TestCase):
    def test_open_closed_unknown(self):
        today = date(2026, 10, 16)
        self.assertEqual(views.timing_window("2026-10-18", today, 2)["state"], "open")
        self.assertEqual(views.timing_window("2026-10-19", today, 2)["state"], "closed")
        self.assertEqual(views.timing_window("2026-10-19", today, 2)["opens"], "2026-10-17")
        for bad in (None, "", "Not verified", "2026-13-40"):
            self.assertEqual(views.timing_window(bad, today, 2)["state"], "unknown")


class CloneStatusTests(unittest.TestCase):
    CAPTURED = datetime(2026, 10, 5, 11, 30, tzinfo=timezone.utc)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        collector.collect(FakeOpener({GET: result([row(1)], "2026-10-05T11:30:00")}), FAKE_KEY, root=self.root,
                          now=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))

    def test_fresh_boundary_stale_and_missing(self):
        fresh = views.clone_status(self.root, self.CAPTURED + timedelta(hours=2, minutes=10))
        self.assertEqual((fresh["state"], fresh["age_seconds"], fresh["limit_seconds"]), ("fresh", 7800, 21600))
        self.assertEqual(views.clone_status(self.root, self.CAPTURED + timedelta(hours=6))["state"], "fresh")
        stale = views.clone_status(self.root, self.CAPTURED + timedelta(hours=6, seconds=1))
        self.assertEqual((stale["state"], stale["age_seconds"]), ("stale", 21601))
        missing = views.clone_status(self.root / "nowhere", self.CAPTURED)
        self.assertEqual((missing["state"], missing["age_seconds"]), ("unusable", None))
        self.assertTrue(missing["reason"])

    def test_the_data_age_is_the_newest_change_and_gates_trust_with_the_query_age(self):
        now = self.CAPTURED + timedelta(hours=1)
        info = views.clone_status(self.root, now)
        self.assertEqual((info["state"], info["reason"]), ("fresh", ""))
        self.assertIsInstance(info["data_age_seconds"], int)
        self.assertEqual(info["data_limit_seconds"], views.DATA_LIMIT_SECONDS)
        # the query just ran (age < 6 h) but the cloned data is a day and a half old: not trusted
        with mock.patch.object(views, "_data_age_seconds", return_value=views.DATA_LIMIT_SECONDS + 1):
            late = views.clone_status(self.root, now)
        self.assertEqual((late["state"], late["reason"], late["age_seconds"]), ("stale", "data_stale", 3600))
        with mock.patch.object(views, "_data_age_seconds", return_value=views.DATA_LIMIT_SECONDS):
            self.assertEqual(views.clone_status(self.root, now)["state"], "fresh")
        with mock.patch.object(views, "_data_age_seconds", return_value=None):
            unknown = views.clone_status(self.root, now)
        self.assertEqual((unknown["state"], unknown["reason"]), ("stale", "data_age_unknown"))

    def test_data_age_helper_reads_only_the_recorded_stamp(self):
        now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(views._data_age_seconds({"source": {"data_latest_modified": "2026-10-19T10:00:00Z"}}, now), 7200)
        for bad in ({}, {"source": None}, {"source": {"data_latest_modified": None}}, {"source": {"data_latest_modified": "x"}}):
            self.assertIsNone(views._data_age_seconds(bad, now))

    def test_a_tampered_snapshot_is_unusable(self):
        latest = self.root / "prod-clone" / "latest.json"
        latest.write_text("{}", encoding="utf-8")
        self.assertEqual(views.clone_status(self.root, self.CAPTURED)["state"], "unusable")

    def test_age_words(self):
        self.assertEqual(views.age_words(7800), "2 h 10 min")
        self.assertEqual(views.age_words(2100), "35 min")
        self.assertEqual(views.age_words(None), "unknown")


class LockReasonTests(unittest.TestCase):
    def test_known_and_unknown_codes_have_plain_text(self):
        for code in views.LOCK_REASON_TEXT:
            self.assertTrue(views.lock_reason_text(code).endswith("."))
        self.assertIn("not unlocked", views.lock_reason_text("something_new"))
        self.assertIn("not unlocked", views.lock_reason_text(None))


class BoundaryTests(unittest.TestCase):
    def test_the_module_only_reads(self):
        source = Path(views.__file__).read_text(encoding="utf-8")
        for forbidden in ("write_text", "write_bytes", "open(", "os.replace", "mkdir", "unlink", "subprocess", "socket",
                          "urllib", "requests", "import tools", "from tools", "consume(", ".issue(", ".record(", "os.environ"):
            self.assertFalse(forbidden in source, forbidden)


if __name__ == "__main__":
    unittest.main()
