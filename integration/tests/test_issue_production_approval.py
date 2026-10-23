"""Tests for tools/issue_production_approval.py (owner-run D1 approval issuer). No Salesforce, network or browser."""

import contextlib
import io
import json
import multiprocessing
import re
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from integration.onboarding import production_approval as pa
from integration.onboarding import session_readiness
from tools import issue_production_approval as tool
from integration.tests.locked_build import locked_create_build  # noqa: E402

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
OWNER = "owner@example.test"
CO = "CO-0797"
REV = "2026-10-15T09:30:00.000+0000"
REF = "PROD-CE-0797-20261016"
ORG = "00D1t000000ABCDEAA"
GOOD_ENV = {"SURFACE_ONBOARDING_APPROVAL_ISSUERS": OWNER, "SURFACE_SF_EXPECTED_ORG_ID": ORG}
GOOD_ROW = {"Name": CO, "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
            "Onboarding_Approval_Status__c": "Approved", "Email_Domains__c": "@customer.example",
            "Account_UUID__c": None, "LastModifiedDate": REV, "Account__c": "0011t00000ABCDEFGH",
            "Account_Name__c": "Customer Co"}


class Run:
    """One invocation of tool.main with fakes; captures stdout and exit code."""

    def __init__(self, test, argv, row=GOOD_ROW, env=None, tty=True, typed="ISSUE CO-0797", reader=None, org=ORG, now=NOW,
                 typing_error=None):
        self.reads = []

        def fake_reader(co):
            self.reads.append(co)
            if isinstance(row, Exception):
                raise row
            return None if row is None else dict(row)

        self.typed_prompts = []

        def fake_input(prompt=""):
            self.typed_prompts.append(prompt)
            if typing_error is not None:
                raise typing_error
            return typed

        def fake_org():
            if isinstance(org, Exception):
                raise org
            return org

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.code = tool.main(argv, reader=reader or fake_reader, now_fn=lambda: now,
                                  environ=dict(GOOD_ENV) if env is None else env,
                                  input_fn=fake_input, isatty_fn=lambda: tty, state_path=test.path, org_probe=fake_org)
        self.out = out.getvalue()
        self.lines = [line for line in self.out.splitlines() if line.strip()]

    def result(self):
        return json.loads(self.lines[-1])

    def reason(self):
        result = self.result()
        assert result["result"] == "refused", self.out
        return result["reason"]




def _issue_worker(path, co, ref, barrier, queue):
    """Child process: issue one approval for `co` after the barrier; report the outcome."""
    from integration.onboarding import production_approval as approval
    barrier.wait(30)
    try:
        approval.issue(Path(path), ref, co, "case_2_new_ce_only", "rev-" + co, OWNER, frozenset({OWNER}), NOW)
        queue.put("issued")
    except approval.ApprovalRefused as refused:
        queue.put(refused.reason)


def _race_worker(path, kind, barrier, queue):
    """Child process: either consume REF or issue a newer revision for the same CO, released together."""
    from integration.onboarding import production_approval as approval
    barrier.wait(30)
    if kind == "consume":
        queue.put(("consume", approval.consume(Path(path), REF, CO, "case_2_new_ce_only", REV, NOW)))
        return
    try:
        approval.issue(Path(path), "PROD-CE-0797-NEXT", CO, "case_2_new_ce_only", "rev-new", OWNER, frozenset({OWNER}), NOW)
        queue.put(("issue", "issued"))
    except approval.ApprovalRefused as refused:
        queue.put(("issue", refused.reason))


class IssueToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / pa.STATE_FILE_NAME
        self.lock = self.path.with_name(self.path.name + pa.LOCK_SUFFIX)

    def run_tool(self, extra=(), **kwargs):
        return Run(self, ["--co", CO, *extra], **kwargs)

    def assertRefused(self, run, reason):
        self.assertEqual(run.code, 2, run.out)
        self.assertEqual(run.reason(), reason)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.lock.exists())

    def records(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    # --- eligibility refusals -------------------------------------------------------------------------------
    def test_every_source_refusal_writes_nothing(self):
        cases = [
            ("co_not_found", None),
            ("co_not_found", {**GOOD_ROW, "Name": "CO-0001"}),
            ("source_unreadable", RuntimeError("boom")),
            ("route_not_ce_only", {**GOOD_ROW, "Onboarding_Product__c": "Surface"}),
            ("route_not_ce_only", {**GOOD_ROW, "Onboarding_Type__c": "Renewal"}),
            ("approval_status_not_approved", {**GOOD_ROW, "Onboarding_Approval_Status__c": "Pending"}),
            ("email_domain_not_single", {**GOOD_ROW, "Email_Domains__c": "a.example, b.example"}),
            ("email_domain_not_single", {**GOOD_ROW, "Email_Domains__c": "not a domain"}),
            ("email_domain_not_single", {**GOOD_ROW, "Email_Domains__c": None}),
            ("account_uuid_present", {**GOOD_ROW, "Account_UUID__c": "a" * 32}),
            ("source_revision_missing", {**GOOD_ROW, "LastModifiedDate": None}),
            ("source_revision_missing", {**GOOD_ROW, "LastModifiedDate": ""}),
        ]
        for reason, row in cases:
            with self.subTest(reason=reason, row=str(row)[:60]):
                self.assertRefused(self.run_tool(row=row), reason)

    # --- argument validation (before any read) --------------------------------------------------------------
    def test_invalid_arguments_refuse_before_reading(self):
        bad = [("invalid_co_reference", ["--co", "CO-1"]),
               ("invalid_approval_ref", ["--co", CO, "--approval-ref", "bad ref"]),
               ("invalid_lifetime", ["--co", CO, "--lifetime-days", "32"]),
               ("invalid_lifetime", ["--co", CO, "--lifetime-days", "0"]),
               ("invalid_lifetime", ["--co", CO, "--lifetime-days", "-3"]),
               ("invalid_arguments", ["--co", CO, "--lifetime-days", "x"]),
               ("invalid_arguments", ["--bogus"]),
               ("invalid_arguments", [])]
        for reason, argv in bad:
            with self.subTest(argv=argv):
                run = Run(self, argv)
                self.assertRefused(run, reason)
                self.assertEqual(run.reads, [])

    def test_lifetime_boundary_is_exactly_31_days(self):
        run = self.run_tool(["--lifetime-days", "31"])
        self.assertEqual(run.code, 0, run.out)
        self.assertEqual(run.result()["expires_at"], "2026-11-16T12:00:00Z")
        self.assertEqual(self.records()[REF]["expires_at"], "2026-11-16T12:00:00Z")
        self.path.unlink()
        self.assertEqual(self.run_tool(["--lifetime-days", "1"]).result()["expires_at"], "2026-10-17T12:00:00Z")
        self.path.unlink()
        # The record layer itself refuses 31 days + 1 second (the tool only passes whole days).
        with self.assertRaises(pa.ApprovalRefused) as caught:
            pa.issue(self.path, REF, CO, "case_2_new_ce_only", REV, OWNER, frozenset({OWNER}), NOW,
                     lifetime=timedelta(days=31, seconds=1))
        self.assertEqual(caught.exception.reason, "invalid_lifetime")
        pa.issue(self.path, REF, CO, "case_2_new_ce_only", REV, OWNER, frozenset({OWNER}), NOW, lifetime=timedelta(days=31))

    def test_issuer_rules(self):
        self.assertRefused(self.run_tool(["--issuer", "other@example.test"]), "issuer_not_allowed")
        self.assertRefused(self.run_tool(env={"SURFACE_SF_EXPECTED_ORG_ID": ORG}), "issuer_list_ambiguous")
        two = {"SURFACE_ONBOARDING_APPROVAL_ISSUERS": "a@example.test,b@example.test", "SURFACE_SF_EXPECTED_ORG_ID": ORG}
        self.assertRefused(self.run_tool(env=two), "issuer_list_ambiguous")
        self.assertRefused(self.run_tool(["--issuer", "c@example.test"], env=two), "issuer_not_allowed")
        ok = self.run_tool(["--issuer", "B@Example.test", "--dry-run"], env=two)
        self.assertEqual(ok.code, 0)
        self.assertIn("Issuer: b@example.test", ok.out)

    def test_dashboard_users_fallback_only_when_exactly_one(self):
        one = {"SURFACE_DASHBOARD_ALLOWED_USERS": OWNER, "SURFACE_SF_EXPECTED_ORG_ID": ORG}
        run = self.run_tool(["--dry-run"], env=one)
        self.assertEqual(run.code, 0, run.out)
        self.assertIn("Issuer: " + OWNER, run.out)
        many = {"SURFACE_DASHBOARD_ALLOWED_USERS": OWNER + ",b@example.test", "SURFACE_SF_EXPECTED_ORG_ID": ORG}
        self.assertRefused(self.run_tool(env=many), "issuer_list_ambiguous")
        self.assertRefused(self.run_tool(["--issuer", OWNER], env=many), "issuer_list_ambiguous")
        both = {**many, "SURFACE_ONBOARDING_APPROVAL_ISSUERS": "c@example.test"}  # explicit list wins
        self.assertRefused(self.run_tool(["--issuer", OWNER], env=both), "issuer_not_allowed")

    # --- live Salesforce in the pinned org, desktop only -------------------------------------------------------
    def test_vm_runtime_is_refused_before_any_read(self):
        for value in ("vm", "VM", " vm "):
            run = self.run_tool(env={**GOOD_ENV, "SURFACE_ONBOARDING_RUNTIME": value})
            self.assertRefused(run, "desktop_only")
            self.assertEqual(run.reads, [])
        self.assertEqual(self.run_tool(["--dry-run"], env={**GOOD_ENV, "SURFACE_ONBOARDING_RUNTIME": "desktop"}).code, 0)

    def test_org_must_be_pinned_and_match(self):
        cases = [("other org", {}, "00D1t000000ZZZZZZZ"), ("not pinned", {"SURFACE_SF_EXPECTED_ORG_ID": ""}, ORG),
                 ("unknown org", {}, None), ("probe error", {}, RuntimeError("sf failed")),
                 ("bad org shape", {}, "not-an-org")]
        for label, change, org in cases:
            with self.subTest(label):
                run = self.run_tool(env={**GOOD_ENV, **change}, org=org)
                self.assertRefused(run, "salesforce_org_not_pinned")
                self.assertEqual(run.reads, [])
        self.assertRefused(self.run_tool(env={"SURFACE_ONBOARDING_APPROVAL_ISSUERS": OWNER}), "salesforce_org_not_pinned")
        self.assertEqual(self.run_tool(["--dry-run"], org=ORG[:15]).code, 0)  # 15 and 18 character forms match

    # --- dry run ---------------------------------------------------------------------------------------------
    def test_dry_run_prints_summary_and_writes_nothing(self):
        run = self.run_tool(["--dry-run"])
        self.assertEqual(run.code, 0)
        for text in (CO, "case_2_new_ce_only", REV, OWNER, REF, "2026-10-16T12:00:00Z", "2026-10-23T12:00:00Z",
                     "State file: " + pa.STATE_FILE_NAME,
                     "This approves ONE production create for this CO only; it does not unlock anything."):
            self.assertIn(text, run.out)
        self.assertNotIn(str(self.path.parent), run.out)
        self.assertEqual(list(self.path.parent.iterdir()), [])  # no state file, no lock file, nothing
        self.assertEqual(run.typed_prompts, [])

    # --- confirmation ----------------------------------------------------------------------------------------
    def test_wrong_phrase_writes_nothing(self):
        for typed in ("", "issue CO-0797", "Issue CO-0797", "ISSUE CO-0798", "ISSUE CO-0797 ", " ISSUE CO-0797",
                      "ISSUE  CO-0797", "yes", "ISSUE"):
            with self.subTest(typed=typed):
                self.assertRefused(self.run_tool(typed=typed), "confirmation_phrase_mismatch")

    def test_eof_and_interrupt_at_the_prompt_write_nothing(self):
        for error in (EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.assertRefused(self.run_tool(typing_error=error), "confirmation_phrase_mismatch")

    def test_non_interactive_writes_nothing(self):
        run = self.run_tool(tty=False)
        self.assertRefused(run, "not_interactive")
        self.assertEqual(run.typed_prompts, [])

    def test_correct_phrase_writes_one_bound_record(self):
        run = self.run_tool()
        self.assertEqual(run.code, 0, run.out)
        self.assertEqual(run.result(), {"result": "issued", "approval_ref": REF, "expires_at": "2026-10-23T12:00:00Z"})
        records = self.records()
        self.assertEqual(list(records), [REF])
        record = records[REF]
        self.assertEqual((record["co_reference"], record["route"], record["source_revision"], record["issued_by"],
                          record["scope"]), (CO, "case_2_new_ce_only", REV, OWNER, "ce_only_single_co"))
        self.assertNotIn("consumed_at", record)
        self.assertIsNone(pa.check(self.path, REF, CO, "case_2_new_ce_only", REV, NOW))
        self.assertFalse(self.lock.exists())

    def test_custom_ref_and_lifetime(self):
        run = self.run_tool(["--approval-ref", "PROD-CE-0797-CUSTOM", "--lifetime-days", "2"])
        self.assertEqual(run.code, 0, run.out)
        self.assertEqual(run.result()["expires_at"], "2026-10-18T12:00:00Z")
        self.assertIn("PROD-CE-0797-CUSTOM", self.records())

    def test_other_co_refused_while_one_active_and_reuse_refused(self):
        self.assertEqual(self.run_tool().code, 0)
        other = Run(self, ["--co", "CO-0800"], row={**GOOD_ROW, "Name": "CO-0800"}, typed="ISSUE CO-0800")
        self.assertEqual((other.code, other.reason()), (2, "other_approval_active"))
        again = self.run_tool(["--approval-ref", REF])
        self.assertEqual((again.code, again.reason()), (2, "approval_ref_reused"))
        self.assertEqual(list(self.records()), [REF])

    def test_second_live_approval_for_the_same_co_and_revision_is_refused(self):
        self.assertEqual(self.run_tool().code, 0)
        again = self.run_tool(["--approval-ref", "PROD-CE-0797-SECOND"])
        self.assertEqual((again.code, again.reason()), (2, "approval_already_active_for_co"))
        self.assertEqual(list(self.records()), [REF])

    def test_changed_revision_supersedes_the_old_approval(self):
        self.assertEqual(self.run_tool().code, 0)
        new_rev = "2026-10-16T10:00:00.000+0000"
        later = datetime(2026, 10, 16, 13, 0, 0, tzinfo=timezone.utc)
        run = self.run_tool(["--approval-ref", "PROD-CE-0797-SECOND"], row={**GOOD_ROW, "LastModifiedDate": new_rev}, now=later)
        self.assertEqual(run.code, 0, run.out)
        records = self.records()
        self.assertEqual(set(records), {REF, "PROD-CE-0797-SECOND"})  # nothing deleted
        self.assertEqual(records[REF]["superseded_at"], "2026-10-16T13:00:00Z")
        self.assertNotIn("superseded_at", records["PROD-CE-0797-SECOND"])
        self.assertEqual(pa.check(self.path, REF, CO, "case_2_new_ce_only", REV, later), "approval_superseded")
        self.assertIsNone(pa.check(self.path, "PROD-CE-0797-SECOND", CO, "case_2_new_ce_only", new_rev, later))
        status = self.run_tool(["--status"], now=later)
        self.assertEqual(status.code, 0)
        self.assertIn("superseded", status.out)
        self.assertIn("valid", status.out)

    def test_unreadable_state_file_refuses(self):
        self.path.write_text("not json", encoding="utf-8")
        run = self.run_tool()
        self.assertEqual((run.code, run.reason()), (2, "approval_unreadable"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "not json")
        self.assertFalse(self.lock.exists())

    def test_held_lock_fails_closed(self):
        self.lock.write_text("", encoding="utf-8")
        run = self.run_tool()
        self.assertEqual((run.code, run.reason()), (2, "approval_locked"))
        self.assertFalse(self.path.exists())
        self.assertTrue(self.lock.exists())  # a lock this tool did not make is never removed

    def test_unexpected_error_is_one_line_without_values(self):
        def boom(co):
            raise KeyError("SECRET-VALUE-0011t00000ABCDEFGH")

        run = self.run_tool(reader=boom)
        self.assertEqual(run.code, 2)
        self.assertEqual(run.result(), {"result": "refused", "reason": "source_unreadable"})
        self.assertNotIn("SECRET", run.out)

        original = tool.build_approval
        self.addCleanup(setattr, tool, "build_approval", original)

        def explode(*args, **kwargs):
            raise ValueError("SECRET-VALUE")

        tool.build_approval = explode
        run = self.run_tool()
        self.assertEqual((run.code, run.result()), (2, {"result": "refused", "reason": "unexpected_error"}))
        self.assertNotIn("SECRET", run.out)
        self.assertNotIn("Traceback", run.out)
        self.assertFalse(self.path.exists())

    # --- output hygiene --------------------------------------------------------------------------------------
    def test_output_has_no_other_email_or_salesforce_id(self):
        row = {**GOOD_ROW, "Primary_User_Name__c": "someone@customer.example", "Account__c": "0011t00000ABCDEFGH",
               "Id": "a0B1t00000XYZABCDE"}
        for extra in (["--dry-run"], []):
            run = self.run_tool(extra, row=row)
            self.assertIn(OWNER, run.out)
            self.assertEqual(set(re.findall(r"[\w.+-]+@[\w.-]+", run.out)), {OWNER})
            for forbidden in ("0011t", "a0B1t", "customer.example", "someone", ORG):
                self.assertNotIn(forbidden, run.out)
            if self.path.exists():
                self.path.unlink()

    # --- status ----------------------------------------------------------------------------------------------
    def test_status_is_read_only_and_never_reads_salesforce(self):
        def forbidden(*args):
            raise AssertionError("status must not read Salesforce")

        run = self.run_tool(["--status"], reader=forbidden, org=AssertionError("no org probe"))
        self.assertEqual((run.code, run.reads), (0, []))
        self.assertIn("none", run.out)
        self.assertEqual(list(self.path.parent.iterdir()), [])
        self.run_tool()
        before = self.path.read_bytes()
        run = self.run_tool(["--status"], reader=forbidden, org=AssertionError("no org probe"))
        self.assertEqual(run.code, 0)
        self.assertIn("valid", run.out)
        self.assertIn(REF, run.out)
        self.assertIn("2026-10-23T12:00:00Z", run.out)
        self.assertNotIn(REV, run.out)
        self.assertNotIn("co_reference", run.out)
        self.assertEqual(self.path.read_bytes(), before)

    def test_status_states_and_other_co(self):
        self.run_tool()
        self.assertIn("none", Run(self, ["--co", "CO-0800", "--status"]).out)
        records = self.records()
        records[REF]["consumed_at"] = "2026-10-16T13:00:00Z"
        self.path.write_text(json.dumps(records), encoding="utf-8")
        self.assertIn("consumed", self.run_tool(["--status"]).out)
        records[REF].pop("consumed_at")
        records[REF]["expires_at"] = "2026-10-16T11:00:00Z"
        self.path.write_text(json.dumps(records), encoding="utf-8")
        self.assertIn("expired", self.run_tool(["--status"]).out)
        self.path.write_text("garbage", encoding="utf-8")
        run = self.run_tool(["--status"])
        self.assertEqual((run.code, run.reason()), (2, "approval_unreadable"))

    # --- wiring ----------------------------------------------------------------------------------------------
    def test_default_ref_matches_the_reference_pattern(self):
        self.assertEqual(tool.default_approval_ref("CO-0797", NOW), REF)
        self.assertEqual(tool.default_approval_ref("CO-1234567", NOW), "PROD-CE-1234567-20261016")
        for co in ("CO-0797", "CO-1234", "CO-1234567890"):
            self.assertIsNotNone(session_readiness.REFERENCE_PATTERN.fullmatch(tool.default_approval_ref(co, NOW)))
        self.assertEqual(tool.STATE_FILE_NAME, "production_create_approvals.json")
        self.assertEqual(tool.default_state_path().name, "production_create_approvals.json")

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_locks_stay_false_and_the_tool_does_not_reference_them(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # unlocked for the CO-0800 dry run only; the create stays locked
        source = Path(tool.__file__).read_text(encoding="utf-8")
        self.assertNotIn("PRODUCTION_RUNNER_SUPPORTED", source)
        self.assertNotIn("PRODUCTION_DRY_RUN_SUPPORTED", source)
        self.assertEqual(self.run_tool().code, 0)  # works while locked
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # unlocked for the CO-0800 dry run only; the create stays locked

    def test_eligibility_matches_the_dashboard_rules(self):
        from tools import serve_attended_open_onboardings_dashboard as dashboard
        self.assertEqual((tool.CE_PRODUCT, tool.CE_TYPE, tool.CE_ENGINE),
                         (dashboard.CE_ROUTE_PRODUCT, dashboard.CE_ROUTE_TYPE, dashboard.CE_ENGINE))
        self.assertEqual(tool.APPROVED, "Approved")
        domains = ["@a.example", "a.example", "A.EXAMPLE", "a.example,b.example", "a.example b.example", "", "  ", None,
                   "bad", "-a.example", "a-.example", "a.example;", "@@a.example", "a..example", "a.b.c.example",
                   "x_y.example", "a.example, ", "https://a.example"]
        for raw in domains:
            row = {**GOOD_ROW, "Email_Domains__c": raw}
            self.assertEqual(tool.eligibility_problem(row, CO) is None, dashboard.ce_only_eligible(row), repr(raw))
        for product, kind in (("Surface", "New Product Onboarding"), ("Credential Exposure", "Renewal"),
                              ("credential exposure", "New Product Onboarding"), (None, None)):
            row = {**GOOD_ROW, "Onboarding_Product__c": product, "Onboarding_Type__c": kind}
            self.assertEqual(tool.eligibility_problem(row, CO) is None, dashboard.ce_only_eligible(row), (product, kind))
        self.assertEqual(dashboard.route_for(GOOD_ROW), tool.CE_ENGINE)

    def test_tool_has_no_network_or_process_imports(self):
        import ast
        tree = ast.parse(Path(tool.__file__).read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertIn("argparse", roots)  # the guard does see imports
        self.assertFalse(roots & {"subprocess", "socket", "urllib", "http", "requests", "aiohttp", "httpx", "playwright"})

    # --- concurrency (real lock file; real processes for two issuers) --------------------------------------------
    def test_two_concurrent_issues_for_different_cos_exactly_one_succeeds(self):
        context = multiprocessing.get_context("spawn")
        for attempt in range(3):
            if self.path.exists():
                self.path.unlink()
            barrier, queue = context.Barrier(2), context.Queue()
            procs = [context.Process(target=_issue_worker,
                                     args=(str(self.path), co, "PROD-CE-%s-20261016" % co[3:], barrier, queue))
                     for co in ("CO-0797", "CO-0800")]
            for proc in procs:
                proc.start()
            outcomes = sorted(queue.get(timeout=90) for _ in procs)
            for proc in procs:
                proc.join(30)
            self.assertEqual(outcomes.count("issued"), 1, outcomes)
            self.assertIn(outcomes[0], ("approval_locked", "issued", "other_approval_active"))
            self.assertEqual(len(self.records()), 1)
            self.assertFalse(self.lock.exists())

    def test_a_consume_racing_an_issue_never_loses_consumed_at(self):
        for attempt in range(25):
            if self.path.exists():
                self.path.unlink()
            pa.issue(self.path, REF, CO, "case_2_new_ce_only", REV, OWNER, frozenset({OWNER}), NOW)
            barrier = threading.Barrier(2)
            results = {}

            def consume():
                barrier.wait()
                results["consume"] = pa.consume(self.path, REF, CO, "case_2_new_ce_only", REV, NOW)

            def issue_new():
                barrier.wait()
                try:
                    pa.issue(self.path, "PROD-CE-0797-NEXT", CO, "case_2_new_ce_only", "rev-new", OWNER,
                             frozenset({OWNER}), NOW)
                    results["issue"] = "issued"
                except pa.ApprovalRefused as refused:
                    results["issue"] = refused.reason

            threads = [threading.Thread(target=consume), threading.Thread(target=issue_new)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
            records = self.records()
            if results["consume"] is None:
                self.assertIn("consumed_at", records[REF], results)
                self.assertNotIn("superseded_at", records[REF])
            else:
                self.assertEqual(results["issue"], "issued", results)
                self.assertIn("superseded_at", records[REF])
            self.assertFalse(self.lock.exists())

    def test_a_consume_racing_an_issue_in_two_processes_never_loses_consumed_at(self):
        context = multiprocessing.get_context("spawn")
        for attempt in range(6):
            if self.path.exists():
                self.path.unlink()
            pa.issue(self.path, REF, CO, "case_2_new_ce_only", REV, OWNER, frozenset({OWNER}), NOW)
            barrier, queue = context.Barrier(2), context.Queue()
            procs = [context.Process(target=_race_worker, args=(str(self.path), kind, barrier, queue))
                     for kind in ("consume", "issue")]
            for proc in procs:
                proc.start()
            results = dict(queue.get(timeout=90) for _ in procs)
            for proc in procs:
                proc.join(30)
            record = self.records()[REF]
            if results["consume"] is None:
                self.assertIn("consumed_at", record, results)
            self.assertFalse(self.lock.exists())

    def test_issue_keeps_a_consume_written_before_it(self):
        pa.issue(self.path, REF, CO, "case_2_new_ce_only", REV, OWNER, frozenset({OWNER}), NOW)
        self.assertIsNone(pa.consume(self.path, REF, CO, "case_2_new_ce_only", REV, NOW))
        pa.issue(self.path, "PROD-CE-0800-20261016", "CO-0800", "case_2_new_ce_only", "r2", OWNER, frozenset({OWNER}), NOW)
        self.assertIn("consumed_at", self.records()[REF])


if __name__ == "__main__":
    unittest.main()
