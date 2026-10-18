"""Tests for the redacted, append-only production-create record (owner decision D2)."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from integration.onboarding import production_create_record as pcr

NOW = datetime(2026, 10, 16, 9, 30, 15, tzinfo=timezone.utc)


def good(**over):
    data = {
        "co_reference": "CO-0797",
        "route": "credential_exposure",
        "approval_ref": "APPR-1",
        "outcome": "dry_run_passed",
        "tenant_name": "Acme Corp",
        "domains": ["acme.com", "mail.acme.com"],
        "account_id": "a" * 24,
        "account_uuid": "0123456789abcdef0123456789abcdef",
        "plan_summary": "Credential Exposure, 1 year",
        "form_fields_checked": {"tenant_name": "pass", "domains": "fail"},
        "response_shape": ["id", "uuid", "name"],
        "salesforce_revision": "r29",
    }
    data.update(over)
    return data


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)

    def write(self, data=None, now=NOW):
        return pcr.record(self.state, good() if data is None else data, now)


class TestAllowlistAndShape(Base):
    def test_location_and_name(self):
        path = self.write()
        self.assertEqual(path.parent, self.state / "production-create-records")
        self.assertEqual(path.name, "CO-0797-20261016T093015Z.json")

    def test_unknown_keys_dropped(self):
        path = self.write(good(password="x", extra="y", raw_payload={"a": 1}))
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("password", on_disk)
        self.assertNotIn("extra", on_disk)
        self.assertNotIn("raw_payload", on_disk)
        self.assertEqual(on_disk["redactions"], 0)

    def test_recorded_at_set_from_now_not_input(self):
        path = self.write(good(recorded_at="1999-01-01T00:00:00Z"))
        self.assertEqual(pcr.load(path)["recorded_at"], "2026-10-16T09:30:15Z")

    def test_non_utc_now_converted(self):
        tz = timezone(timedelta(hours=3))
        path = self.write(now=datetime(2026, 10, 16, 12, 30, 15, tzinfo=tz))
        self.assertTrue(path.name.endswith("20261016T093015Z.json"))

    def test_naive_now_rejected(self):
        with self.assertRaises(pcr.ProductionCreateRecordError):
            self.write(now=datetime(2026, 10, 16))

    def test_invalid_outcome_and_missing_co_rejected(self):
        for bad in (good(outcome="created"), good(co_reference=""), good(co_reference="../x"),
                    good(co_reference="a b")):
            with self.assertRaises(pcr.ProductionCreateRecordError):
                self.write(bad)
        data = good()
        del data["co_reference"]
        with self.assertRaises(pcr.ProductionCreateRecordError):
            self.write(data)

    def test_all_outcomes_accepted(self):
        for outcome in ("dry_run_passed", "refused", "created_unverified", "readback_verified",
                        "uncertain_submit", "attempt_started"):
            self.write(good(outcome=outcome), now=NOW + timedelta(seconds=1))

    def test_form_fields_values_restricted(self):
        path = self.write(good(form_fields_checked={"a": "pass", "b": "secret value", "c": True}))
        self.assertEqual(pcr.load(path)["form_fields_checked"], {"a": "pass", "b": "fail", "c": "fail"})

    def test_response_shape_names_only(self):
        path = self.write(good(response_shape=["id", {"nested": "value"}, 5]))
        shape = pcr.load(path)["response_shape"]
        self.assertEqual(shape, ["id"])

    def test_payload_mismatches_are_field_names_only(self):
        names = ["scanNow", "license.assetsNumber", "unexpected:brandNewFlag", "has a space", "unexpected:" + "A" * 60 + ".b",
                 {"nested": "value"}, 5]
        path = self.write(good(payload_mismatches=names))
        self.assertEqual(pcr.load(path)["payload_mismatches"],
                         ["scanNow", "license.assetsNumber", "unexpected:brandNewFlag", pcr.REDACTED, pcr.REDACTED])

    def test_account_uuid_and_ids_not_redacted(self):
        path = self.write(good(account_id="0123456789abcdef01234567"))
        rec = pcr.load(path)
        self.assertEqual(rec["account_uuid"], "0123456789abcdef0123456789abcdef")
        self.assertEqual(rec["account_id"], "0123456789abcdef01234567")
        self.assertEqual(rec["redactions"], 0)


class TestRedaction(Base):
    def test_redaction_table(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
        blob = "A" * 44
        hexblob = "0123456789abcdef" * 3
        cases = [
            jwt, "Bearer abc", "my Password is", "COOKIE: a=b", "Authorization header",
            "MFA code 123456", "api token", blob, hexblob, "x " + blob + " y",
        ]
        for value in cases:
            path = self.write(good(plan_summary=value), now=NOW + timedelta(seconds=cases.index(value)))
            rec = pcr.load(path)
            self.assertEqual(rec["plan_summary"], "[redacted]", value)
            self.assertEqual(rec["redactions"], 1, value)

    def test_not_redacted(self):
        for value in ("Credential Exposure, 1 year", "acme.com", "mail.acme.com", "a" * 39):
            path = self.write(good(plan_summary=value), now=NOW + timedelta(seconds=len(value)))
            self.assertEqual(pcr.load(path)["plan_summary"], value)

    def test_redaction_in_lists_and_dicts_counted(self):
        # Names are matched by shape, not keyword: a malformed name (space etc.) is what gets redacted.
        path = self.write(good(domains=["ok.com", "Bearer zzz"], response_shape=["id", "Bearer zzz"],
                               form_fields_checked={"Bearer zzz": "pass", "ok": "pass"}, tenant_name="my cookie"))
        rec = pcr.load(path)
        self.assertEqual(rec["domains"], ["ok.com", "[redacted]"])
        self.assertEqual(rec["response_shape"], ["id", "[redacted]"])
        self.assertIn("ok", rec["form_fields_checked"])
        self.assertNotIn("Bearer zzz", rec["form_fields_checked"])
        self.assertEqual(rec["tenant_name"], "[redacted]")
        self.assertEqual(rec["redactions"], 4)

    def test_no_secret_lands_on_disk(self):
        secrets = ["Bearer abc123", "hunter2 password", "sessionCookie=1", "Authorization: x", "mfa 555555",
                   "tok" "en=abc", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop", "Z" * 50]
        for s in secrets:
            data = good(tenant_name=s, plan_summary=s, domains=[s], response_shape=[s],
                        form_fields_checked={s: "pass"}, approval_ref=s, refusal_reason=s,
                        route=s, account_id=s, account_uuid=s, salesforce_revision=s)
            path = self.write(data, now=NOW + timedelta(seconds=secrets.index(s)))
            text = path.read_text(encoding="utf-8")
            for fragment in ("abc123", "hunter2", "sessionCookie", "555555", "tok" "en=abc", "eyJ", "ZZZZZ",
                             "Authorization"):
                self.assertNotIn(fragment, text)


class TestMachineNames(Base):
    def test_reason_codes_with_secret_words_survive(self):
        for reason in ("approval_token_missing", "mfa_required", "cookie_expired", "production_locked_x"):
            path = self.write(good(outcome="refused", refusal_reason=reason),
                              now=NOW + timedelta(seconds=len(reason)))
            rec = pcr.load(path)
            self.assertEqual(rec["refusal_reason"], reason)
            self.assertEqual(rec["redactions"], 0)

    def test_bad_reason_shapes_redacted(self):
        for reason in ("Approval_missing", "has space", "a:b", "ab", "1abc", "x" * 82, "Bearer abc", "",
                       "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"):
            path = self.write(good(outcome="refused", refusal_reason=reason),
                              now=NOW + timedelta(seconds=len(reason) + 1))
            rec = pcr.load(path)
            self.assertEqual(rec["refusal_reason"], "[redacted]", reason)
            self.assertEqual(rec["redactions"], 1, reason)

    def test_reason_boundary_lengths(self):
        self.assertEqual(pcr.load(self.write(good(refusal_reason="abc")))["refusal_reason"], "abc")
        long_ok = "a" + "b" * 80
        path = self.write(good(refusal_reason=long_ok), now=NOW + timedelta(seconds=1))
        self.assertEqual(pcr.load(path)["refusal_reason"], long_ok)

    def test_response_and_form_names_with_secret_words_survive(self):
        path = self.write(good(response_shape=["access_token", "id", "user.password_hash", "set-cookie"],
                               form_fields_checked={"api_token": "pass", "mfa_enabled": "fail"}))
        rec = pcr.load(path)
        self.assertEqual(rec["response_shape"], ["access_token", "id", "user.password_hash", "set-cookie"])
        self.assertEqual(rec["form_fields_checked"], {"api_token": "pass", "mfa_enabled": "fail"})
        self.assertEqual(rec["redactions"], 0)

    def test_bad_names_redacted(self):
        path = self.write(good(response_shape=["a b", "x" * 81, "näme"], form_fields_checked={"a b": "pass"}))
        rec = pcr.load(path)
        self.assertEqual(rec["response_shape"], ["[redacted]"] * 3)
        self.assertEqual(rec["form_fields_checked"], {"[redacted]": "pass"})
        self.assertEqual(rec["redactions"], 4)

    def test_blob_or_jwt_shaped_name_still_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
        path = self.write(good(response_shape=["Z" * 50, jwt], form_fields_checked={"Z" * 50: "pass"}))
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("ZZZZZ", text)
        self.assertNotIn("eyJ", text)

    def test_free_text_values_keep_keyword_rule(self):
        path = self.write(good(plan_summary="access_token here", tenant_name="Bearer x", domains=["my.cookie.com x"]))
        rec = pcr.load(path)
        self.assertEqual(rec["plan_summary"], "[redacted]")
        self.assertEqual(rec["tenant_name"], "[redacted]")
        self.assertEqual(rec["domains"], ["[redacted]"])

    def test_corrupt_machine_names_on_disk_fail_closed(self):
        path = self.write()
        rec = json.loads(path.read_text(encoding="utf-8"))
        for bad in ({**rec, "refusal_reason": "Has Space"}, {**rec, "response_shape": ["a b"]},
                    {**rec, "form_fields_checked": {"a b": "pass"}}):
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(pcr.ProductionCreateRecordError):
                pcr.load(path)


class TestSizeAndControl(Base):
    def test_trim_300_and_control_chars(self):
        value = "line1\nline2\r\tend\x00\x1b" + " bbbb" * 100
        path = self.write(good(plan_summary=value))
        got = pcr.load(path)["plan_summary"]
        self.assertLessEqual(len(got), 300)
        self.assertFalse(any(ord(c) < 32 or ord(c) == 127 for c in got))
        self.assertTrue(got.startswith("line1"))

    def test_list_limits(self):
        path = self.write(good(domains=[f"d{i}.com" for i in range(500)]))
        self.assertLessEqual(len(pcr.load(path)["domains"]), pcr.MAX_LIST_ITEMS)

    def test_non_string_scalars_coerced_or_dropped(self):
        path = self.write(good(tenant_name=12345, plan_summary={"a": 1}, salesforce_revision=7))
        rec = pcr.load(path)
        self.assertEqual(rec["tenant_name"], "12345")
        self.assertNotIn("plan_summary", rec)
        self.assertEqual(rec["salesforce_revision"], "7")


class TestAppendOnly(Base):
    def test_collision_gets_counter_and_never_overwrites(self):
        first = self.write(good(tenant_name="First"))
        second = self.write(good(tenant_name="Second"))
        third = self.write(good(tenant_name="Third"))
        self.assertEqual(len({first, second, third}), 3)
        self.assertEqual(second.name, "CO-0797-20261016T093015Z-2.json")
        self.assertEqual(third.name, "CO-0797-20261016T093015Z-3.json")
        self.assertEqual(pcr.load(first)["tenant_name"], "First")
        self.assertEqual(pcr.load(second)["tenant_name"], "Second")

    def test_no_temp_files_left(self):
        self.write()
        self.write()
        names = os.listdir(self.state / "production-create-records")
        self.assertTrue(all(n.endswith(".json") for n in names), names)

    def test_permissions_where_supported(self):
        path = self.write()
        if os.name != "posix":
            self.skipTest("POSIX permissions only")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)


class TestLoadAndList(Base):
    def test_round_trip(self):
        path = self.write()
        rec = pcr.load(path)
        self.assertEqual(rec["co_reference"], "CO-0797")
        self.assertEqual(rec["domains"], ["acme.com", "mail.acme.com"])
        self.assertEqual(rec["outcome"], "dry_run_passed")
        self.assertEqual(rec["redactions"], 0)

    def test_corrupt_files_fail_closed(self):
        path = self.write()
        good_rec = json.loads(path.read_text(encoding="utf-8"))
        bad_texts = [
            "", "{not json", "[]", "null",
            json.dumps({**good_rec, "outcome": "bogus"}),
            json.dumps({**good_rec, "unknown": 1}),
            json.dumps({**good_rec, "domains": "acme.com"}),
            json.dumps({**good_rec, "redactions": "0"}),
            json.dumps({**good_rec, "plan_summary": "Bearer abc"}),
            json.dumps({k: v for k, v in good_rec.items() if k != "co_reference"}),
        ]
        for text in bad_texts:
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(pcr.ProductionCreateRecordError, msg=text):
                pcr.load(path)

    def test_load_missing_file_fails_closed(self):
        with self.assertRaises(pcr.ProductionCreateRecordError):
            pcr.load(self.state / "nope.json")

    def test_list_records_sorted_and_filtered(self):
        a = self.write(good(co_reference="CO-0797"), now=NOW)
        b = self.write(good(co_reference="CO-0800"), now=NOW + timedelta(seconds=1))
        c = self.write(good(co_reference="CO-0797"), now=NOW + timedelta(seconds=2))
        self.assertEqual(pcr.list_records(self.state), [a, b, c])
        self.assertEqual(pcr.list_records(self.state, "CO-0797"), [a, c])
        self.assertEqual(pcr.list_records(self.state, "CO-9999"), [])

    def test_list_empty_when_no_folder(self):
        self.assertEqual(pcr.list_records(self.state), [])

    def test_list_fails_closed_on_corrupt_record(self):
        path = self.write()
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(pcr.ProductionCreateRecordError):
            pcr.list_records(self.state)


class TestEditableParameters(Base):
    def test_all_present(self):
        rec = pcr.load(self.write())
        self.assertEqual(pcr.editable_parameters(rec),
                         ["account_id", "account_uuid", "domains", "plan_summary", "tenant_name"])

    def test_missing_empty_and_redacted_excluded(self):
        path = self.write(good(domains=[], plan_summary="Bearer x", account_id="", account_uuid=None))
        rec = pcr.load(path)
        self.assertEqual(pcr.editable_parameters(rec), ["tenant_name"])

    def test_refused_without_params(self):
        path = self.write({"co_reference": "CO-1", "outcome": "refused", "refusal_reason": "approval_missing"})
        self.assertEqual(pcr.editable_parameters(pcr.load(path)), [])


if __name__ == "__main__":
    unittest.main()
