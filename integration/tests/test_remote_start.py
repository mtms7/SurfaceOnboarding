"""VM-started onboarding run on the owner's PC (owner decision 2026-10-27, docs/62): the pure logic, the VM request file, the signed poll over
the restricted push channel, and the PC-side acceptance gates. Locked by default; hermetic: temporary folders, a fake HMAC key, a fake ssh,
no network, no Salesforce, no BackOffice, no browser."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tools.ingest_clone_push as ingest
from integration.onboarding import remote_start as rs
from integration.onboarding import vm_snapshot as vs

KEY = b"unit-test-remote-start-key-0123456789-abcdef-NOT-REAL"
NOW = datetime(2026, 10, 27, 20, 0, 0, tzinfo=timezone.utc)
OWNER = "owner@example.test"
REV = "2026-10-27T10:00:00.000+0000"


def ask(state, co, operator, now):
    return rs.request_remote_start(state, co, operator, now, REV)


class VmRequestTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.state = Path(self._temp.name)

    def test_a_valid_request_is_written_with_a_nonce_and_a_five_minute_expiry_and_nothing_else(self):
        code, request = ask(self.state, "CO-0812", OWNER, NOW)
        self.assertEqual(code, "requested")
        self.assertEqual(set(request), {"co_reference", "requested_by", "requested_at", "expires_at", "nonce", "delivered_at", "revision"})
        self.assertEqual(request["expires_at"], "2026-10-27T20:05:00Z")
        self.assertRegex(request["nonce"], r"^[0-9a-f]{32}$")
        self.assertIsNone(request["delivered_at"])
        stored = json.loads((self.state / rs.REQUEST_FILE).read_text(encoding="utf-8"))
        self.assertEqual(stored, request)

    def test_bad_references_and_missing_operators_are_refused(self):
        for reference in ("", "co-0812", "CO-12", "CO-0812; rm", "../CO-0812", None):
            self.assertEqual(ask(self.state, reference, OWNER, NOW)[0], "bad_reference", reference)
        self.assertEqual(ask(self.state, "CO-0812", "", NOW)[0], "bad_operator")
        self.assertFalse((self.state / rs.REQUEST_FILE).exists())

    def test_one_pending_request_at_a_time_then_a_cool_down_per_co_and_an_hourly_cap(self):
        self.assertEqual(ask(self.state, "CO-0812", OWNER, NOW)[0], "requested")
        self.assertEqual(ask(self.state, "CO-0806", OWNER, NOW + timedelta(seconds=30))[0], "request_pending")
        rs.deliver(self.state, NOW + timedelta(seconds=40))
        self.assertEqual(ask(self.state, "CO-0812", OWNER, NOW + timedelta(minutes=2))[0], "too_soon")
        self.assertEqual(ask(self.state, "CO-0806", OWNER, NOW + timedelta(minutes=2))[0], "requested")
        rs.deliver(self.state, NOW + timedelta(minutes=2, seconds=5))
        self.assertEqual(ask(self.state, "CO-0757", OWNER, NOW + timedelta(minutes=4))[0], "requested")
        rs.deliver(self.state, NOW + timedelta(minutes=4, seconds=5))
        self.assertEqual(ask(self.state, "CO-0801", OWNER, NOW + timedelta(minutes=6))[0], "rate_limited")
        self.assertEqual(ask(self.state, "CO-0801", OWNER, NOW + timedelta(minutes=70))[0], "requested")

    def test_a_request_is_delivered_once_and_only_while_unexpired(self):
        _, request = ask(self.state, "CO-0812", OWNER, NOW)
        line = rs.deliver(self.state, NOW + timedelta(seconds=20))
        self.assertEqual(line, f"remote-start CO-0812 {request['nonce']} 2026-10-27T20:05:00Z {OWNER} {REV}")
        self.assertEqual(rs.deliver(self.state, NOW + timedelta(seconds=40)), "none")  # one delivery per nonce
        ask(self.state, "CO-0806", OWNER, NOW + timedelta(minutes=2))
        self.assertEqual(rs.deliver(self.state, NOW + timedelta(minutes=9)), "none")  # expired before the PC asked

    def test_a_damaged_request_file_delivers_nothing(self):
        (self.state / rs.REQUEST_FILE).write_text("{not json", encoding="utf-8")
        self.assertEqual(rs.deliver(self.state, NOW), "none")
        (self.state / rs.REQUEST_FILE).write_text(json.dumps({"co_reference": "CO-0812; x", "nonce": "z"}), encoding="utf-8")
        self.assertEqual(rs.deliver(self.state, NOW), "none")

    def test_a_missing_or_malformed_revision_is_refused(self):
        for revision in (None, "", "yesterday", "2026-10-27"):
            self.assertEqual(rs.request_remote_start(self.state, "CO-0812", OWNER, NOW, revision)[0], "bad_revision", revision)
        self.assertFalse((self.state / rs.REQUEST_FILE).exists())

    def test_a_request_is_refused_and_removed_when_the_log_that_counts_the_rate_cannot_be_written(self):
        (self.state / rs.REQUEST_LOG).mkdir()  # a directory where the log file must be: appending fails
        self.assertEqual(ask(self.state, "CO-0812", OWNER, NOW)[0], "store_unavailable")
        self.assertFalse((self.state / rs.REQUEST_FILE).exists())

    def test_the_request_log_holds_words_only(self):
        ask(self.state, "CO-0812", OWNER, NOW)
        text = (self.state / rs.REQUEST_LOG).read_text(encoding="utf-8")
        for line in text.splitlines():
            self.assertEqual(set(json.loads(line)), {"co_reference", "requested_by", "requested_at", "result"})


class PollChannelTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.state = Path(self._temp.name) / "state"
        self.state.mkdir()
        self.key_file = Path(self._temp.name) / "snapshot.key"
        self.key_file.write_bytes(KEY)

    def push(self, raw):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ingest.main(["--key-file", str(self.key_file), "--state-dir", str(self.state)], stdin=io.BytesIO(raw))
        return code, buffer.getvalue().strip()

    def test_the_poll_bundle_gets_the_request_line_once_through_the_existing_gate_program(self):
        _, request = ask(self.state, "CO-0812", OWNER, datetime.now(timezone.utc))
        poll = rs.build_poll_bundle(KEY, datetime.now(timezone.utc))
        code, line = self.push(poll)
        self.assertEqual(code, 0)
        self.assertEqual(line, f"remote-start CO-0812 {request['nonce']} {request['expires_at']} {OWNER} {REV}")
        self.assertEqual(self.push(rs.build_poll_bundle(KEY, datetime.now(timezone.utc))), (0, "none"))

    def test_an_empty_vm_answers_none(self):
        self.assertEqual(self.push(rs.build_poll_bundle(KEY, datetime.now(timezone.utc))), (0, "none"))

    def test_a_poll_bundle_with_a_bad_signature_extra_files_or_a_future_stamp_is_refused_and_delivers_nothing(self):
        ask(self.state, "CO-0812", OWNER, datetime.now(timezone.utc))
        files = vs.verify_bundle(rs.build_poll_bundle(KEY, datetime.now(timezone.utc)), KEY).files
        bad = vs.build_bundle(files, b"another-key-0123456789-abcdefghij-NOT-REAL-xx", vs.REMOTE_POLL_PRODUCER, datetime.now(timezone.utc))
        self.assertEqual(self.push(bad)[0], 2)
        extra = vs.build_bundle({**files, vs.QUEUE_FILE: b"{}"}, KEY, vs.REMOTE_POLL_PRODUCER, datetime.now(timezone.utc))
        self.assertEqual(self.push(extra), (2, "refused:poll_bundle_has_other_files"))
        future = rs.build_poll_bundle(KEY, datetime.now(timezone.utc) + timedelta(hours=2))
        self.assertEqual(self.push(future), (2, "refused:bundle_from_the_future"))
        self.assertEqual(self.push(rs.build_poll_bundle(KEY, datetime.now(timezone.utc)))[1].split()[0], "remote-start")  # still pending

    def test_a_poll_bundle_is_never_ingested_as_a_snapshot(self):
        with self.assertRaises(vs.SnapshotError):
            vs.ingest_bundle(rs.build_poll_bundle(KEY, NOW), KEY, self.state)

    def test_the_reply_is_one_short_line_without_secrets(self):
        ask(self.state, "CO-0812", OWNER, datetime.now(timezone.utc))
        _code, line = self.push(rs.build_poll_bundle(KEY, datetime.now(timezone.utc)))
        self.assertNotIn(KEY.decode(), line)
        self.assertEqual(len(line.split()), 6)  # remote-start CO nonce expires requester revision: nothing else


class PcAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.state = Path(self._temp.name)

    def reply(self, co="CO-0812", nonce="a" * 32, expires=NOW + timedelta(minutes=5)):
        return f"remote-start {co} {nonce} {expires.strftime('%Y-%m-%dT%H:%M:%SZ')} {OWNER} {REV}"

    def test_the_reply_parser_is_strict(self):
        parsed = rs.parse_reply(self.reply())
        self.assertEqual((parsed.co_reference, parsed.nonce), ("CO-0812", "a" * 32))
        for line in ("", "none", "remote-start CO-0812", self.reply("co-0812"), self.reply(nonce="zz"), self.reply() + " extra",
                     "REMOTE-START CO-0812 " + "a" * 32 + " 2026-10-27T20:05:00Z " + OWNER + " " + REV, self.reply().replace("T20", " 20"),
                     "remote-start CO-0812 " + "a" * 32 + " 2026-10-27T20:05:00Z", self.reply().replace(REV, "not-a-revision"),
                     self.reply().replace(OWNER, "no at sign")):
            self.assertIsNone(rs.parse_reply(line), line)

    def test_accept_refuses_expired_replayed_and_over_rate_requests_and_records_the_nonce(self):
        inbox = rs.Inbox(self.state)
        request = rs.parse_reply(self.reply())
        self.assertIsNone(inbox.accept(request, NOW))
        self.assertEqual(inbox.accept(request, NOW + timedelta(seconds=5)), "replay")
        self.assertEqual(inbox.accept(rs.parse_reply(self.reply(nonce="b" * 32, expires=NOW - timedelta(seconds=1))), NOW), "expired")
        # a request that claims a lifetime longer than the VM ever grants is refused (a forged or stale line)
        self.assertEqual(inbox.accept(rs.parse_reply(self.reply(nonce="c" * 32, expires=NOW + timedelta(hours=2))), NOW), "bad_lifetime")
        again = rs.Inbox(self.state)  # a restart of the dashboard keeps the memory of used nonces
        self.assertEqual(again.accept(request, NOW + timedelta(seconds=10)), "replay")

    def test_accept_caps_runs_per_hour_across_restarts(self):
        inbox = rs.Inbox(self.state)
        for index in range(rs.MAX_PER_HOUR):
            self.assertIsNone(inbox.accept(rs.parse_reply(self.reply(nonce=f"{index:032x}")), NOW + timedelta(minutes=index)))
        extra = rs.parse_reply(self.reply(nonce="d" * 32, expires=NOW + timedelta(minutes=9)))
        self.assertEqual(rs.Inbox(self.state).accept(extra, NOW + timedelta(minutes=5)), "rate_limited")

    def test_the_inbox_file_holds_nonces_and_times_only(self):
        rs.Inbox(self.state).accept(rs.parse_reply(self.reply()), NOW)
        data = json.loads((self.state / rs.INBOX_FILE).read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"seen"})
        self.assertEqual(set(data["seen"][0]), {"nonce", "co_reference", "accepted_at"})


class ConfigAndPollTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name)
        (self.tmp / "snapshot.key").write_bytes(KEY)
        (self.tmp / "push_key").write_text("x", encoding="utf-8")
        self.env = {rs.ENABLE_ENV: "1", rs.OPERATOR_ENV: OWNER, rs.PUSH_KEY_ENV: str(self.tmp / "push_key"),
                    rs.HMAC_KEY_ENV: str(self.tmp / "snapshot.key"), rs.HOST_ENV: "172.26.37.20"}

    def test_it_is_locked_by_default_and_needs_every_setting(self):
        self.assertIsNone(rs.PcConfig.from_env({}))
        self.assertIsNone(rs.PcConfig.from_env({**self.env, rs.ENABLE_ENV: "0"}))
        self.assertIsNone(rs.PcConfig.from_env({**self.env, rs.ENABLE_ENV: "true"}))  # exactly "1"
        for name in (rs.OPERATOR_ENV, rs.PUSH_KEY_ENV, rs.HMAC_KEY_ENV, rs.HOST_ENV):
            self.assertIsNone(rs.PcConfig.from_env({k: v for k, v in self.env.items() if k != name}), name)
        self.assertIsNone(rs.PcConfig.from_env({**self.env, rs.HOST_ENV: "evil.example.com; calc"}))
        self.assertIsNone(rs.PcConfig.from_env({**self.env, rs.PUSH_KEY_ENV: str(self.tmp / "missing")}))
        config = rs.PcConfig.from_env(self.env)
        self.assertEqual((config.operator, config.host), (OWNER, "172.26.37.20"))

    def test_poll_once_sends_the_signed_poll_to_the_forced_command_and_returns_one_parsed_line(self):
        config = rs.PcConfig.from_env(self.env)
        seen = {}

        def fake_ssh(argv, data):
            seen["argv"], seen["data"] = argv, data
            return 0, "remote-start CO-0812 " + "a" * 32 + " 2026-10-27T20:05:00Z " + OWNER + " " + REV + "\n"

        result = rs.poll_once(config, fake_ssh, NOW)
        self.assertEqual(result.co_reference, "CO-0812")
        self.assertEqual(vs.verify_bundle(seen["data"], KEY).manifest["producer"], vs.REMOTE_POLL_PRODUCER)
        joined = " ".join(seen["argv"])
        for needle in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes", "clone-push", OWNER.split("@")[0] if False else "workato@172.26.37.20"):
            self.assertIn(needle, joined)
        self.assertNotIn(KEY.decode(), joined)

    def test_poll_once_returns_none_for_none_garbage_errors_and_timeouts(self):
        config = rs.PcConfig.from_env(self.env)
        for exit_code, text in ((0, "none\n"), (0, "garbage\n"), (255, ""), (0, "refused:not_approved\n"), (2, "refused:bad_zip\n")):
            self.assertIsNone(rs.poll_once(config, lambda argv, data, e=exit_code, t=text: (e, t), NOW), text)

        def boom(argv, data):
            raise OSError("no ssh")

        self.assertIsNone(rs.poll_once(config, boom, NOW))


class ProcessTests(unittest.TestCase):
    """One poll tick on the PC: poll, accept, start through the SAME create path as the button, log words only."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name)
        (self.tmp / "snapshot.key").write_bytes(KEY)
        (self.tmp / "push_key").write_text("x", encoding="utf-8")
        self.config = rs.PcConfig.from_env({rs.ENABLE_ENV: "1", rs.OPERATOR_ENV: OWNER, rs.PUSH_KEY_ENV: str(self.tmp / "push_key"),
                                            rs.HMAC_KEY_ENV: str(self.tmp / "snapshot.key"), rs.HOST_ENV: "172.26.37.20"})
        self.state = self.tmp / "state"
        self.started = []

    def start(self, reference, operator, revision):
        self.started.append((reference, operator, revision))
        return "launched"

    def ssh(self, reply):
        return lambda argv, data: (0, reply)

    def line(self, nonce="a" * 32, minutes=5):
        return f"remote-start CO-0812 {nonce} {(NOW + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')} {OWNER} {REV}\n"

    def test_a_fresh_request_starts_one_run_as_the_configured_owner_and_is_logged_in_words(self):
        word = rs.process(self.config, rs.Inbox(self.state), self.ssh(self.line()), NOW, self.start, self.state)
        self.assertEqual(word, "launched")
        self.assertEqual(self.started, [("CO-0812", OWNER, REV)])
        log = (self.state / rs.RUN_LOG).read_text(encoding="utf-8").splitlines()
        self.assertEqual(set(json.loads(log[0])), {"co_reference", "result", "at"})

    def test_nothing_happens_for_no_request_a_replay_or_a_refusal(self):
        inbox = rs.Inbox(self.state)
        self.assertEqual(rs.process(self.config, inbox, self.ssh("none\n"), NOW, self.start, self.state), "idle")
        self.assertEqual(rs.process(self.config, inbox, self.ssh(self.line()), NOW, self.start, self.state), "launched")
        self.assertEqual(rs.process(self.config, inbox, self.ssh(self.line()), NOW + timedelta(seconds=20), self.start, self.state), "replay")
        self.assertEqual(rs.process(self.config, inbox, self.ssh(self.line("b" * 32, minutes=-1)), NOW, self.start, self.state), "expired")
        self.assertEqual(len(self.started), 1)

    def test_a_refusal_from_the_create_gates_is_logged_and_nothing_else_runs(self):
        word = rs.process(self.config, rs.Inbox(self.state), self.ssh(self.line()), NOW, lambda r, o, v: "approval_missing", self.state)
        self.assertEqual(word, "approval_missing")

    def test_a_crashing_start_is_contained_and_logged(self):
        def boom(reference, operator, revision):
            raise RuntimeError("boom")

        self.assertEqual(rs.process(self.config, rs.Inbox(self.state), self.ssh(self.line()), NOW, boom, self.state), "start_failed")

    def test_only_the_configured_owner_may_ask(self):
        other = f"remote-start CO-0812 {'e' * 32} {(NOW + timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')} intruder@example.test {REV}\n"
        self.assertEqual(rs.process(self.config, rs.Inbox(self.state), self.ssh(other), NOW, self.start, self.state), "wrong_requester")
        self.assertEqual(self.started, [])

    def test_a_damaged_inbox_file_fails_closed_and_is_not_overwritten(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / rs.INBOX_FILE).write_text("{not json", encoding="utf-8")
        word = rs.process(self.config, rs.Inbox(self.state), self.ssh(self.line()), NOW, self.start, self.state)
        self.assertEqual(word, "inbox_unreadable")
        self.assertEqual(self.started, [])
        self.assertEqual((self.state / rs.INBOX_FILE).read_text(encoding="utf-8"), "{not json")

    def test_the_run_log_stays_bounded(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / rs.RUN_LOG).write_text("\n".join(json.dumps({"co_reference": "CO-0001", "result": "x", "at": "t"})
                                                      for _ in range(rs.MAX_LOG_LINES + 5)) + "\n", encoding="utf-8")
        rs.process(self.config, rs.Inbox(self.state), self.ssh(self.line()), NOW, self.start, self.state)
        self.assertLessEqual(len((self.state / rs.RUN_LOG).read_text(encoding="utf-8").splitlines()), rs.KEEP_LOG_LINES + 1)

    def test_the_worker_does_not_exist_when_locked(self):
        self.assertIsNone(rs.start_worker(None, self.state, self.start))


if __name__ == "__main__":
    unittest.main()
