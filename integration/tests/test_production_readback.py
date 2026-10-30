"""Read-only production readback entry point (--production-readback): gate, guard, comparison, redaction, zero clicks.

All local: fake pages and fake Salesforce source; no browser, network or Salesforce.
"""

from __future__ import annotations

import contextlib
import dataclasses
from datetime import date, datetime, timezone
import inspect
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from integration.onboarding import production_contract as contract
from integration.onboarding import production_probe as probe
from integration.onboarding import session_readiness
from integration.tests import page_own_table as pot
from integration.tests import test_production_contract as fx
import tools.attended_ce_only_playwright as runner
from integration.tests.locked_build import locked_create_build  # noqa: E402

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
CO = "CO-0797"
GOOD_ENV = {probe.GATE_ENV: "1", probe.REF_ENV: "PROBE-D4-20261016"}
ORIGIN = "https://app.pentera.io"
SEARCH_URL = ORIGIN + contract.ACCOUNTS_PATH
ADD_URL = ORIGIN + "/api/v1/backoffice/account/add"
SF_UUID = fx.UUID


def make_source(uuid=SF_UUID):
    return runner.CeFillSource(
        reference=CO, source_revision="2026-10-16T00:00:00.000+0000", account_id="001AAAAAAAAAAAAAAA",
        account_name="Acme Test", email_domain=fx.DOMAIN, country="Chile", subscription_start=date(2026, 5, 21),
        subscription_end=date(2027, 5, 20), tenant_name=fx.NAME, primary_user_alias="acmetest",
        salesforce_account_uuid=uuid)


class FakePage(pot.PageOwnTable):
    """Records everything; the readback may only reload the page (its own table request, body rewritten): no click, no typing."""

    def __init__(self, reply=None, workers=0, attempt_write=False):
        self.url = ORIGIN + "/backoffice/tenantManagement"
        self.reply = reply if reply is not None else good_reply()
        self.workers = workers
        self.attempt_write = attempt_write
        self.form_open = False
        self.evaluated, self.clicks, self.typed = [], [], []
        self._init_table(header=True, passive={})

    def triples(self):
        """(method, url, outcome) of every request the guard routed."""
        return [(r.request.method, r.request.url, r.outcome) for r in self.routed]

    def evaluate(self, script, argument=None):
        self.evaluated.append(script)
        if script == runner.production_dry_run.SERVICE_WORKER_JS:
            return {"count": self.workers}
        raise AssertionError("unexpected evaluate: the readback evaluates nothing but the service-worker count")

    def _serve_table(self, value):
        self.search_argument = value
        if self.attempt_write:
            self.fire("POST", ADD_URL, "{}")
            self.fire("PUT", ORIGIN + "/api/v1/backoffice/account/" + "a" * 24 + "/edit", "{}")
        return self.reply

    # Anything that would act on the page: the readback must never call these.
    def click(self, *a, **k):
        self.clicks.append("click")

    def fill(self, *a, **k):
        self.typed.append("fill")

    def type(self, *a, **k):
        self.typed.append("type")

    def get_by_role(self, *a, **k):
        raise AssertionError("readback must not locate controls")

    def locator(self, *a, **k):
        raise AssertionError("readback must not locate controls (it never uses the in-app sort)")


def good_row(**over):
    return fx.row_flat(**{"userEmailDomains": [runner.CE_USER_EMAIL_DOMAIN], **over})


def good_reply(rows=None, total=None, status=200):
    rows = [good_row()] if rows is None else rows
    return {"status": status, "total": len(rows) if total is None else total, "rows": rows}


class Harness:
    def __init__(self, test, **page_kw):
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.test = test
        self.page = FakePage(**page_kw)
        self.opened = 0
        self.loaded = 0

    @contextlib.contextmanager
    def open_page(self, target, _wait):
        self.opened += 1
        self.target = target
        yield self.page

    def run(self, *, environ=None, source=None, loader=None, reference=CO, open_page=None):
        def load(_reference):
            self.loaded += 1
            return source if source is not None else make_source()

        with mock.patch("builtins.print"):
            line = runner.run_production_readback(
                reference, environ=dict(GOOD_ENV if environ is None else environ), now=NOW, report_base=self.tmp,
                load_source=loader or load, open_page=open_page or self.open_page)
        return json.loads(line), line

    def report(self):
        files = sorted((self.tmp / "production-probe").glob("readback-*.json"))
        self.test.assertEqual(len(files), 1, files)
        return json.loads(files[0].read_text(encoding="utf-8")), files[0].read_text(encoding="utf-8"), files[0]


BASE_LINE_KEYS = {"result", "reason", "tenant_found", "mismatches", "uuid_ok", "salesforce_uuid_matches",
                  "production_write", "salesforce_writeback"}
LINE_KEYS = BASE_LINE_KEYS | {"variant_result", "variant_absent"}  # the readback diagnostic (names only)


class GateTests(unittest.TestCase):
    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_flags_stay_false(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # unlocked for the CO-0800 dry run only; the create stays locked
        text = Path(session_readiness.__file__).read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^PRODUCTION_DRY_RUN_SUPPORTED = True\b")
        self.assertRegex(text, r"(?m)^PRODUCTION_RUNNER_SUPPORTED = True  # RELEASED 2026-10-23")  # unlock window: pinned by test_unlock_state

    def test_missing_gate_reads_nothing_and_opens_nothing(self):
        for env, reason in (({}, "gate_missing"), ({probe.GATE_ENV: "0", probe.REF_ENV: "PROBE-D4-20261016"}, "gate_missing"),
                            ({probe.GATE_ENV: "1"}, "gate_reference_invalid"),
                            ({probe.GATE_ENV: "1", probe.REF_ENV: "bad"}, "gate_reference_invalid")):
            with self.subTest(env=env):
                h = Harness(self)
                line, _ = h.run(environ=env)
                self.assertEqual((line["result"], line["reason"]), ("production_readback_refused", reason))
                self.assertEqual((h.loaded, h.opened), (0, 0))
                self.assertFalse((h.tmp / "production-probe").exists())

    def test_invalid_reference(self):
        h = Harness(self)
        line, _ = h.run(reference="CO-1; DROP")
        self.assertEqual(line["reason"], "invalid_co_reference")
        self.assertEqual((h.loaded, h.opened), (0, 0))

    def test_production_runner_flag_is_not_needed_or_read(self):
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", False), \
                mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False):
            h = Harness(self)
            line, _ = h.run()
        self.assertEqual(line["result"], "production_readback_complete")


class SalesforceTests(unittest.TestCase):
    def test_salesforce_failure_refuses_before_any_browser(self):
        h = Harness(self)

        def boom(_ref):
            raise RuntimeError("salesforce_fill_source_unavailable")

        line, text = h.run(loader=boom)
        self.assertEqual((line["result"], line["reason"], line["tenant_found"]), ("production_readback_refused", "salesforce_read_failed", False))
        self.assertEqual(h.opened, 0)
        self.assertEqual(h.report()[0]["reason"], "salesforce_read_failed")

    def test_route_mismatch_is_its_own_code(self):
        h = Harness(self)

        def mismatch(_ref):
            raise runner.RouteMismatch()

        line, _ = h.run(loader=mismatch)
        self.assertEqual(line["reason"], "route_not_ce_only_new")
        self.assertEqual(h.opened, 0)

    def test_expired_subscription_makes_intended_unavailable(self):
        h = Harness(self)
        base = make_source()
        line, _ = h.run(source=dataclasses.replace(base, subscription_end=base.subscription_start))
        self.assertEqual(line["reason"], "intended_unavailable")
        self.assertEqual(h.opened, 0)

    def test_source_uuid_is_not_in_repr_or_equality(self):
        a, b = make_source(SF_UUID), make_source("")
        self.assertEqual(a, b)
        self.assertNotIn(SF_UUID, repr(a))


class ReadbackRunTests(unittest.TestCase):
    def test_complete_run_exact_line_and_report(self):
        h = Harness(self)
        line, text = h.run()
        self.assertEqual(set(line), LINE_KEYS)
        absent = sorted(set(contract.READBACK_PICK_PATHS) - set(good_row()))  # candidate paths the good row does not carry
        self.assertEqual({k: line[k] for k in BASE_LINE_KEYS},
                         {"result": "production_readback_complete", "reason": "compared", "tenant_found": True,
                          "mismatches": [], "uuid_ok": True, "salesforce_uuid_matches": True,
                          "production_write": "not_performed", "salesforce_writeback": "not_performed"})
        self.assertEqual(line["variant_result"], {"A": "ok", "B": "ok"})
        self.assertEqual(line["variant_absent"], {"A": absent, "B": absent})
        report, report_text, path = h.report()
        self.assertEqual(path.name, "readback-20261016T120000Z.json")
        for key in LINE_KEYS:
            self.assertEqual(report[key], line[key])
        for secret in (SF_UUID, fx.NAME, fx.DOMAIN, fx.INTERNAL, fx.ROW_ID):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, report_text)
        self.assertEqual((h.page.clicks, h.page.typed), ([], []))
        self.assertEqual((h.page.header_clicks, h.page.dispatches), (0, 0))  # a reload only: the sort header is never touched
        self.assertEqual(report["clicks"], 0)

    def test_uses_probe_target_exact_origin_and_dedicated_profile(self):
        h = Harness(self)
        h.run()
        self.assertEqual(h.target.origin, ORIGIN)
        self.assertEqual(h.target.profile_dir_name, "backoffice-production-automation")

    def test_guard_installed_before_any_page_script_and_search_body_is_exact(self):
        h = Harness(self)
        h.run()
        self.assertEqual(h.page.routes[0][0], "**/*")
        self.assertEqual(h.page.evaluated[0], runner.production_dry_run.SERVICE_WORKER_JS)
        server = h.page.table_bodies[0]["tableServerData"]  # the primary read
        self.assertEqual(h.page.table_values[0], fx.NAME)
        self.assertNotIn("projection", server)  # full rows: the picker reduces them in Python (docs/53 2026-10-18)
        self.assertIn("accountUuid", contract.READBACK_PICK_PATHS)  # the page's own request with ONLY these fields rewritten
        self.assertEqual(server["filters"], {"and": [{"method": "search", "value": fx.NAME}]})
        self.assertEqual((server["offset"], server["items_per_page"]), (0, contract.SEARCH_PAGE_SIZE))
        self.assertEqual(server["unique_fields"], ["accountName", "accountDomain"])
        self.assertEqual(server["sort"], json.loads(pot.PAGE_DEFAULT_BODY)["tableServerData"]["sort"])  # kept as the page sent it
        # the primary (strict) read, then the diagnostic variant A (docs/53 note 2026-10-18): two reloads, nothing else
        self.assertEqual(h.page.triples(), [("POST", SEARCH_URL, "continued")] * 2)
        self.assertEqual((h.page.reloads, h.page.gotos), (2, 0))

    def test_mismatches_are_field_names_only(self):
        row = good_row(accountType="customer", **{"accountLicense.assetsNumber": "1"})
        h = Harness(self, reply=good_reply([row]))
        line, text = h.run()
        self.assertEqual(line["result"], "production_readback_complete")
        self.assertEqual(line["mismatches"], ["accountLicense.assetsNumber", "accountType"])
        self.assertTrue(line["tenant_found"])

    def test_salesforce_uuid_empty_false_true(self):
        for uuid, want in (("", None), (fx.OTHER_UUID, False), (SF_UUID, True)):
            with self.subTest(uuid=bool(uuid)):
                h = Harness(self)
                line, text = h.run(source=make_source(uuid))
                self.assertIs(line["salesforce_uuid_matches"], want)
                self.assertNotIn(fx.OTHER_UUID, text)
                self.assertNotIn(SF_UUID, text)

    def test_malformed_tenant_uuid_gives_uuid_ok_false(self):
        h = Harness(self, reply=good_reply([good_row(accountUuid="NOT-HEX")]))
        line, _ = h.run()
        self.assertIs(line["uuid_ok"], False)
        self.assertIs(line["salesforce_uuid_matches"], False)

    def test_identical_rows_of_one_tenant_are_one(self):
        h = Harness(self, reply=good_reply([good_row(), good_row()]))
        line, _ = h.run()
        self.assertEqual((line["result"], line["tenant_found"]), ("production_readback_complete", True))

    def test_two_distinct_tenants_are_ambiguous(self):
        h = Harness(self, reply=good_reply([good_row(), good_row(accountUuid=fx.OTHER_UUID, id="b" * 24)]))
        line, _ = h.run()
        self.assertEqual((line["result"], line["reason"], line["tenant_found"]), ("production_readback_refused", "ambiguous_rows", False))
        self.assertEqual(line["mismatches"], [])

    def test_name_only_match_refuses(self):
        h = Harness(self, reply=good_reply([good_row(accountDomain="other.example")]))
        line, _ = h.run()
        self.assertEqual((line["result"], line["reason"], line["tenant_found"]), ("production_readback_refused", "name_only_match", False))

    def test_no_row_and_reply_shapes_refuse(self):
        cases = (
            (good_reply([]), "tenant_not_found"),
            (good_reply([good_row()], total=5), "ambiguous_rows"),
            (good_reply(status=401), "session_expired"),
            (good_reply(status=403), "session_expired"),
            (good_reply(status=500), "search_unavailable"),
            ({"status": 200, "total": None, "rows": None}, "search_unavailable"),
            ("junk", "search_unavailable"),
            ({"status": 0}, "search_unavailable"),
        )
        for reply, reason in cases:
            with self.subTest(reason=reason, status=str(reply)[:20]):
                h = Harness(self, reply=reply)
                line, _ = h.run()
                self.assertEqual((line["result"], line["reason"], line["tenant_found"]), ("production_readback_refused", reason, False))

    def test_service_worker_refuses_before_search(self):
        for workers, reason in ((1, "service_worker_present"), (None, "service_worker_check_failed")):
            with self.subTest(workers=workers):
                h = Harness(self, workers=workers)
                line, _ = h.run()
                self.assertEqual(line["reason"], reason)
                self.assertEqual(h.page.table_values, [])
                self.assertEqual(h.page.triples(), [])

    def test_guard_blocks_a_write_attempt_and_refuses(self):
        h = Harness(self, attempt_write=True)
        line, text = h.run()
        self.assertEqual((line["result"], line["reason"]), ("production_readback_refused", "write_request_blocked"))
        self.assertIn(("POST", ADD_URL, "aborted"), h.page.triples())
        self.assertTrue(all(outcome == "aborted" for method, url, outcome in h.page.triples() if url == ADD_URL or method == "PUT"))
        self.assertEqual(h.page.triples()[0], ("POST", SEARCH_URL, "continued"))
        report, _, _ = h.report()
        blocked = {(item["method"], item["path"]): item["count"] for item in report["blocked_requests"]}
        self.assertEqual(blocked[("POST", "/api/v1/backoffice/account/add")], 2)  # one per read: primary (B) + diagnostic A
        self.assertEqual(sum(blocked.values()), 4)
        self.assertEqual(h.page.clicks, [])
        self.assertEqual(line["production_write"], "not_performed")

    def test_sign_in_timeout_and_runtime_errors_map_to_codes(self):
        for message, reason in (("production_dry_run_login_timeout", "sign_in_timeout"),
                                ("playwright_runtime_unavailable", "playwright_runtime_unavailable"),
                                ("browser_tab_unavailable", "browser_unavailable")):
            with self.subTest(message=message):
                h = Harness(self)

                @contextlib.contextmanager
                def failing(_target, _wait, message=message):
                    raise RuntimeError(message)
                    yield  # pragma: no cover

                line, _ = h.run(open_page=failing)
                self.assertEqual((line["result"], line["reason"]), ("production_readback_refused", reason))
                self.assertEqual(h.page.evaluated, [])

    def test_unexpected_exception_in_page_is_a_fixed_code(self):
        h = Harness(self)
        h.page.reload = mock.Mock(side_effect=ValueError("secret text " + SF_UUID))
        # the reader swallows page errors (nothing rewritten, no reply) -> search_unavailable; text never leaks
        line, text = h.run()
        self.assertEqual(line["reason"], "search_unavailable")
        self.assertNotIn(SF_UUID, text)

    def test_report_write_failure_refuses_without_losing_fields(self):
        h = Harness(self)
        with mock.patch.object(contract, "write_readback_report", side_effect=OSError("disk")):
            line, _ = h.run()
        self.assertEqual(line["reason"], "report_unavailable")
        self.assertEqual(line["result"], "production_readback_refused")

    def test_line_is_redaction_checked(self):
        h = Harness(self)
        with mock.patch.object(runner, "readback_analyse", return_value=runner._readback_fields(
                "production_readback_complete", "compared", True, ["a@b.example"], True, None)):
            line, text = h.run()
        self.assertEqual(line["reason"], "line_not_redacted")
        self.assertNotIn("b.example", text)


class WiringTests(unittest.TestCase):
    """Through the real browser helper (fake Playwright): same sign-in wait, same profile, same cleanup."""

    def run_wired(self, wait_result, page=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        page = page or FakePage()
        tab = mock.Mock(persist=False, profile_dir=tmp)
        fake_module = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(), "playwright.sync_api": fake_module}), \
                mock.patch.object(runner, "_open_automation_tab", return_value=tab) as opened, \
                mock.patch.object(runner, "_backoffice_signed_in", return_value=wait_result) as waited, \
                mock.patch.object(runner, "_probe_attach", return_value=(mock.Mock(), page)), \
                mock.patch.object(runner, "_probe_snapshot", return_value=("/backoffice/tenantManagement",
                                                                           {"texts": ["Add Account"], "tables": 1, "navs": 1})), \
                mock.patch.object(runner, "_release_automation_tab") as released, \
                mock.patch.object(page, "goto", create=True), \
                mock.patch.object(page, "bring_to_front", create=True), \
                mock.patch("builtins.print"):
            line = json.loads(runner.run_production_readback(
                CO, environ=dict(GOOD_ENV), now=NOW, report_base=tmp, load_source=lambda _r: make_source(),
                wait_seconds=7))
        return line, opened, waited, released, page

    def test_login_timeout_inspects_nothing(self):
        line, opened, waited, released, page = self.run_wired(False)
        self.assertEqual((line["result"], line["reason"]), ("production_readback_refused", "sign_in_timeout"))
        self.assertEqual(opened.call_args.args[1], ORIGIN + "/backoffice/tenantManagement")
        self.assertEqual(opened.call_args.args[2].profile_dir_name, "backoffice-production-automation")
        self.assertEqual((waited.call_args.args[0], waited.call_args.args[2]), (page, 7))  # 2026-10-30: plus the auth watch
        released.assert_called_once()
        self.assertEqual((page.routes, page.evaluated, page.clicks), ([], [], []))

    def test_signed_in_run_completes_with_zero_clicks(self):
        line, opened, waited, released, page = self.run_wired(True)
        self.assertEqual((line["result"], line["reason"]), ("production_readback_complete", "compared"))
        released.assert_called_once()
        self.assertEqual((page.clicks, page.typed), ([], []))

    def test_main_dispatch_and_exclusivity(self):
        with mock.patch.object(runner, "run_production_readback", return_value='{"result":"x"}') as ran, \
                mock.patch.object(runner.sys, "argv", ["runner", "--production-readback", "--co", CO]), \
                mock.patch("builtins.print"):
            self.assertEqual(runner.main(), 0)
        ran.assert_called_once_with(CO, route=runner.CE_ENGINE, tenant_name=None)  # Case 3 added route and short name (2026-10-21)
        bad = (["--production-readback"], ["--production-readback", "--co", CO, "--revision", "x"],
               ["--production-readback", "--co", CO, "--dry-run"], ["--production-readback", "--co", CO, "--confirm-write"],
               ["--production-readback", "--co", CO, "--production-probe"],
               ["--production-readback", "--co", CO, "--production-dry-run", "--revision", "x"])
        for argv in bad:
            with self.subTest(argv=argv), mock.patch.object(runner, "run_production_readback") as ran, \
                    mock.patch.object(runner, "run_production_dry_run") as dry, \
                    mock.patch.object(runner.sys, "argv", ["runner", *argv]), \
                    mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                runner.main()
            ran.assert_not_called()
            dry.assert_not_called()


class CodeShapeTests(unittest.TestCase):
    def test_readback_code_has_no_action_on_the_page(self):
        source = inspect.getsource(runner.run_production_readback) + inspect.getsource(runner.readback_analyse)
        source += Path(contract.__file__).read_text(encoding="utf-8")
        for needle in (".click(", ".fill(", ".type(", ".check(", ".select_option(", ".press", "ClickLog", "keyboard",
                       "get_by_role", "page.goto", "NotImplemented"):
            self.assertNotIn(needle, source)
        for needle in ("account/add", "account/edit"):
            self.assertNotIn(needle, inspect.getsource(runner.run_production_readback))

    def test_the_guard_requires_a_validated_read_body_and_the_reader_never_clicks(self):
        source = inspect.getsource(runner.run_production_readback)
        self.assertIn("RequestGuard(require_read_body=True)", source)
        self.assertIn("in_app=False", source)  # every read is a reload: zero clicks
        self.assertNotIn("page.evaluate(production_contract", source)


if __name__ == "__main__":
    unittest.main()


class ReadbackReasonMappingTests(unittest.TestCase):
    def test_every_row_refusal_code_keeps_its_own_stable_reason(self):
        # select_unique_row refuses only with these codes; a missing mapping would degrade to `search_unavailable`.
        for code in ("no_match", "multiple_matches", "name_only_match", "case_variant_match"):
            self.assertIn(code, runner.READBACK_ROW_REASONS, code)
            self.assertNotEqual(runner.READBACK_ROW_REASONS[code], "search_unavailable", code)

    def test_a_case_only_name_difference_is_refused_with_its_own_code(self):
        rows = [{"accountName": "Acme - CE Only", "accountDomain": "acme.example", "accountUuid": "a" * 32, "id": "i" * 24}]
        result = contract.select_unique_row(rows, "ACME - CE ONLY", "acme.example")
        self.assertIn("case_variant_match", repr(result))

