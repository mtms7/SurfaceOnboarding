"""Readback diagnostic (docs/53 note 2026-10-18): which page-own read variant returns the compared row paths.

Variants of the page's OWN table request, all read-only, names only: A = page's own unique_fields kept and no projection
key (the diagnostic), B = the strict read (unique_fields override [accountName, accountDomain], NO projection; the primary
read). The projection variant was removed on 2026-10-18: a dotted projection made the server drop the compared paths. The
looser grammar exists ONLY for the readback probe class; create / dry-run / duplicate paths keep the strict mode.
Fake pages only: no browser, network, Salesforce or BackOffice.
"""

from __future__ import annotations

import inspect
import json
import unittest
from unittest import mock

from integration.onboarding import production_contract as contract
from integration.onboarding import production_dry_run as pdr
from integration.onboarding import production_probe as probe
from integration.tests import page_own_table as pot
from integration.tests import test_production_contract as fx
from integration.tests import test_production_page_read as pr
from integration.tests import test_production_readback as rb
import tools.attended_ce_only_playwright as runner

PICKS = set(contract.READBACK_PICK_PATHS)
LICENSE_PATHS = sorted(p for p in PICKS if p.startswith("accountLicense."))


def variant_of(body):
    server = body["tableServerData"]
    if "projection" in server:
        raise AssertionError("no variant may send a projection")
    return "B" if server.get("unique_fields") == list(probe.STRICT_UNIQUE_FIELDS) else "A"


class VariantPage(rb.FakePage):
    """Serves the reply registered for the variant the rewritten body represents (records the order)."""

    def __init__(self, replies, **kw):
        super().__init__(**kw)
        self.replies, self.order = replies, []

    def _serve_table(self, value):
        variant = variant_of(self.table_bodies[-1])
        self.order.append(variant)
        return self.replies.get(variant, self.reply)


def thin_row(**over):
    """A row as a server that drops the license paths would answer."""
    row = rb.good_row(**over)
    for path in LICENSE_PATHS + ["leakedCredentialsSettings.spyCloudSettings.enabled", "reconLevel", "scanningFrequency"]:
        row.pop(path, None)
    return row


class RewriteModeTests(unittest.TestCase):
    def test_default_mode_is_strict_and_unchanged(self):
        fields = probe.table_rewrite_fields("v")
        self.assertEqual(fields["unique_fields"], list(probe.STRICT_UNIQUE_FIELDS))
        self.assertNotIn("projection", fields)
        self.assertEqual(probe.table_rewrite_drops(probe.REWRITE_STRICT), ("projection",))  # the page's own one is removed
        self.assertEqual(inspect.signature(probe.table_rewrite_fields).parameters["mode"].default, probe.REWRITE_STRICT)
        self.assertEqual(inspect.signature(runner.ProductionPageReader.table).parameters["mode"].default, probe.REWRITE_STRICT)
        self.assertEqual(inspect.signature(runner.ProductionPageReader.readback).parameters["mode"].default, probe.REWRITE_STRICT)

    def test_no_mode_carries_a_projection_and_a_keeps_the_pages_unique_fields(self):
        a = probe.table_rewrite_fields("v", probe.REWRITE_PAGE_UNIQUE)
        b = probe.table_rewrite_fields("v", probe.REWRITE_STRICT)
        self.assertEqual(probe.REWRITE_MODES, (probe.REWRITE_STRICT, probe.REWRITE_PAGE_UNIQUE))
        self.assertFalse(hasattr(probe, "REWRITE_UNIQUE_ONLY"))  # strict IS the override-only variant now
        self.assertEqual(set(a), {"offset", "items_per_page", "filters"})
        self.assertEqual(set(b), {"offset", "items_per_page", "filters", "unique_fields"})
        self.assertEqual(b["unique_fields"], list(probe.STRICT_UNIQUE_FIELDS))
        for mode in probe.REWRITE_MODES:
            self.assertEqual(probe.table_rewrite_drops(mode), ("projection",))
        for fields in (a, b):
            self.assertEqual((fields["offset"], fields["items_per_page"]), (0, probe.TABLE_ROW_LIMIT))
            self.assertEqual(fields["filters"], {"and": [{"method": "search", "value": "v"}]})

    def test_unknown_mode_and_bad_value_raise(self):
        with self.assertRaises(ValueError):
            probe.table_rewrite_fields("v", "loose")
        for mode in probe.REWRITE_MODES:
            with self.assertRaises(ValueError):
                probe.table_rewrite_fields(" ", mode)
        with self.assertRaises(ValueError):
            probe.table_rewrite_drops("loose")

    def test_strict_grammar_refuses_what_the_probe_class_allows(self):
        server = pr.body()["tableServerData"]
        no_projection = dict(server)
        page_unique = {**server, "unique_fields": ["accountType", "licenseType", "firstName"]}
        with_projection = {**server, "projection": ["accountName"]}
        for label, trimmed in (("with_projection", with_projection), ("page_unique", page_unique),
                               ("no_unique", {k: v for k, v in server.items() if k != "unique_fields"})):
            raw = json.dumps({"tableServerData": trimmed})
            with self.subTest(label):
                self.assertIsNone(probe.read_body_problem(raw))  # the probe class
                self.assertEqual(probe.read_body_problem(raw, strict=True), "not_strict")  # create / dry-run / duplicate
        self.assertIsNone(probe.read_body_problem(json.dumps({"tableServerData": no_projection}), strict=True))
        self.assertIsNone(probe.read_body_problem(pr.text(), strict=True))
        # the strict flag never relaxes the base grammar
        for raw in (pr.text(delete=True), pr.text(offset=3), pr.text(items_per_page=5000), pr.text(filters={})):
            self.assertIsNotNone(probe.read_body_problem(raw, strict=True))
            self.assertIsNotNone(probe.read_body_problem(raw))


class ReaderModeTests(unittest.TestCase):
    def read(self, mode):
        page = pr.Page(readbacks=[{"status": 200, "total": 0, "rows": []}])
        reader = pr.reader(page, in_app=False)
        status, _body = reader.table("Acme - CE Only", mode=mode)
        return page, status

    def test_variant_a_keeps_the_pages_unique_fields_and_sends_no_projection(self):
        page, status = self.read(probe.REWRITE_PAGE_UNIQUE)
        server = page.table_bodies[0]["tableServerData"]
        own = json.loads(pot.PAGE_DEFAULT_BODY)["tableServerData"]
        self.assertEqual(status, 200)
        self.assertNotIn("projection", server)
        self.assertEqual(server["unique_fields"], own["unique_fields"])
        self.assertEqual((server["offset"], server["items_per_page"]), (0, probe.TABLE_ROW_LIMIT))
        self.assertEqual(server["sort"], own["sort"])
        self.assertEqual(server["filters"], {"and": [{"method": "search", "value": "Acme - CE Only"}]})
        self.assertEqual((page.reloads, page.header_clicks, page.dispatches), (1, 0, 0))

    def test_variant_b_is_strict_and_overrides_unique_fields_only(self):
        page, _ = self.read(probe.REWRITE_STRICT)
        server = page.table_bodies[0]["tableServerData"]
        self.assertNotIn("projection", server)
        self.assertEqual(server["unique_fields"], list(probe.STRICT_UNIQUE_FIELDS))

    def test_default_mode_sends_the_override_and_no_projection_even_though_the_page_sent_one(self):
        self.assertIn("projection", json.loads(pot.PAGE_DEFAULT_BODY)["tableServerData"])
        page = pr.Page(readbacks=[{"status": 200, "total": 0, "rows": []}])
        pr.reader(page, in_app=False).table("x")
        server = page.table_bodies[0]["tableServerData"]
        self.assertEqual(server["unique_fields"], list(probe.STRICT_UNIQUE_FIELDS))
        self.assertNotIn("projection", server)

    def test_create_dry_run_and_duplicate_paths_stay_strict(self):
        page = pr.Page()
        reader = pr.reader(page)
        reader.search("Acme")
        reader.session_problem()
        runner._pdr_duplicate_problem(page, "Acme", ("acme.example",))
        reader.readback("Acme")
        self.assertTrue(page.table_bodies)
        for body in page.table_bodies:
            server = body["tableServerData"]
            self.assertEqual(server["unique_fields"], list(probe.STRICT_UNIQUE_FIELDS))
            self.assertNotIn("projection", server)
            self.assertIsNone(probe.read_body_problem(json.dumps(body), strict=True))
        for function in (runner._pdr_duplicate_problem, runner.ProductionPageReader.search,
                         runner.ProductionPageReader.session_problem):
            self.assertNotIn("REWRITE_", inspect.getsource(function))  # no way to ask for a looser mode
        diagnostic = inspect.getsource(runner.ProductionPageReader.readback_diagnostic)
        self.assertIn("REWRITE_PAGE_UNIQUE", diagnostic)
        self.assertNotIn("REWRITE_", inspect.getsource(runner.ProductionPageReader.search))

    def test_a_body_outside_the_grammar_is_not_sent_in_any_mode(self):
        hostile = json.dumps({"tableServerData": {**json.loads(pot.PAGE_DEFAULT_BODY)["tableServerData"], "delete": True}})
        for mode in probe.REWRITE_MODES:
            with self.subTest(mode=mode), mock.patch.object(pot, "PAGE_DEFAULT_BODY", hostile):
                page = pr.Page()
                guard = probe.RequestGuard(require_read_body=True)
                guard.install(page)
                self.assertEqual(pr.reader(page, in_app=False).table("x", mode=mode), (None, None))
                self.assertEqual(page.table_bodies, [])
                self.assertEqual(page.routed[-1].outcome, "aborted")  # the request guard refused the unrewritten body

    def test_the_diagnostic_is_one_read_of_variant_a_and_fails_closed(self):
        page = pr.Page()
        calls = []

        def fake_table(value, *, form_open=False, mode=probe.REWRITE_STRICT):
            calls.append((value, mode))
            return (200, {"pagination_response": {"total_count": 0, "table_data": []}})

        reader = pr.reader(page, in_app=False)
        with mock.patch.object(reader, "table", side_effect=fake_table):
            out = reader.readback_diagnostic("N")
        self.assertEqual(calls, [("N", probe.REWRITE_PAGE_UNIQUE)])  # B is the primary strict read the caller already made
        self.assertEqual(out, {"A": {"status": 200, "total": 0, "rows": []}})
        with mock.patch.object(reader, "table", return_value=(None, None)):
            self.assertEqual(reader.readback_diagnostic("N")["A"]["status"], 0)  # None => unavailable


class SummaryTests(unittest.TestCase):
    def test_summary_names_only(self):
        row = rb.good_row()
        reply = {"status": 200, "total": 1, "rows": [row]}
        summary = contract.variant_summary(reply, fx.NAME, fx.DOMAIN)
        self.assertEqual((summary["result"], summary["rows"], summary["total"]), ("ok", 1, 1))
        self.assertEqual(summary["present"], sorted(p for p in PICKS if p in row))
        self.assertEqual(summary["absent"], sorted(p for p in PICKS if p not in row))
        self.assertEqual(set(summary), {"result", "rows", "total", "present", "absent"})
        self.assertNotIn(fx.NAME, json.dumps(summary))
        self.assertNotIn(fx.UUID, json.dumps(summary))

    def test_result_words(self):
        good = rb.good_row()
        other = rb.good_row(accountUuid=fx.OTHER_UUID, id="b" * 24)
        cases = {
            "unavailable": (None, {"status": 0, "total": None, "rows": None}, {"status": 500, "total": None, "rows": None},
                            {"status": 200, "total": None, "rows": None}, "junk"),
            "session_expired": ({"status": 401}, {"status": 403}),
            "not_found": ({"status": 200, "total": 0, "rows": []},),
            "ambiguous": ({"status": 200, "total": 5, "rows": [good]}, {"status": 200, "total": 2, "rows": [good, other]}),
        }
        for word, replies in cases.items():
            for reply in replies:
                with self.subTest(word=word, reply=str(reply)[:30]):
                    summary = contract.variant_summary(reply, fx.NAME, fx.DOMAIN)
                    self.assertEqual(summary["result"], word)
                    self.assertEqual((summary["present"], summary["absent"]), ([], []))

    def test_line_fields_are_a_fixed_enum_and_validate(self):
        ok = contract.variant_summary({"status": 200, "total": 1, "rows": [thin_row()]}, fx.NAME, fx.DOMAIN)
        down = contract.variant_summary(None, fx.NAME, fx.DOMAIN)
        fields = contract.variant_line_fields({"A": ok, "B": down})
        self.assertEqual(set(fields), {"variant_result", "variant_absent"})
        self.assertEqual(fields["variant_result"], {"A": "ok", "B": "unavailable"})
        self.assertEqual(fields["variant_absent"]["B"], [])
        self.assertIn("accountLicense.assetsNumber", fields["variant_absent"]["A"])
        self.assertIsNone(contract.variant_line_problem(fields))
        self.assertIsNone(pdr.line_problem(json.dumps(fields)))

    def test_nothing_value_like_passes_the_variant_validator(self):
        fields = contract.variant_line_fields({v: contract.variant_summary(None, "n", "d") for v in "AB"})
        bad = []
        for mutate in (
            lambda f: f["variant_absent"].update(A=[fx.UUID]), lambda f: f["variant_absent"].update(A=["a@b.example"]),
            lambda f: f["variant_absent"].update(A=["accountName "]), lambda f: f["variant_absent"].update(A=["notAPickPath"]),
            lambda f: f["variant_absent"].update(A=["accountName", "accountName"]),
            lambda f: f["variant_absent"].update(A=["id", "accountName"]),  # not sorted
            lambda f: f["variant_absent"].update(A=sorted(PICKS) + ["x"]), lambda f: f["variant_absent"].update(A="accountName"),
            lambda f: f["variant_absent"].update(A=[5]), lambda f: f["variant_absent"].update(Z=[]),
            lambda f: f["variant_absent"].pop("A"), lambda f: f["variant_result"].update(A="some free text"),
            lambda f: f["variant_result"].update(A=fx.UUID), lambda f: f["variant_result"].update(Z="ok"),
            lambda f: f.update(extra="x"), lambda f: f.pop("variant_result"),
        ):
            copy = json.loads(json.dumps(fields))
            mutate(copy)
            bad.append(copy)
        for copy in bad:
            with self.subTest(copy=json.dumps(copy)[:70]):
                self.assertIsNotNone(contract.variant_line_problem(copy))
        self.assertIsNotNone(contract.variant_line_problem("x"))
        self.assertIsNotNone(contract.variant_line_problem(None))

    def test_rows_and_total_are_bounded_ints(self):
        summary = contract.variant_summary({"status": 200, "total": 10 ** 9, "rows": [rb.good_row()]}, fx.NAME, fx.DOMAIN)
        self.assertEqual(summary["result"], "ambiguous")
        self.assertTrue(all(type(summary[k]) is int and 0 <= summary[k] <= 1000 for k in ("rows", "total")))


class RunTests(unittest.TestCase):
    def test_two_reads_a_after_the_primary_and_the_line_names_only(self):
        replies = {"A": rb.good_reply([rb.good_row()]), "B": rb.good_reply([thin_row()])}
        h = rb.Harness(self)
        h.page = VariantPage(replies)
        line, text = h.run()
        self.assertEqual(h.page.order, ["B", "A"])  # the primary (strict, B) read first, then the diagnostic variant A
        self.assertEqual(h.page.reloads, 2)
        self.assertEqual((h.page.clicks, h.page.typed, h.page.header_clicks, h.page.dispatches), ([], [], 0, 0))
        self.assertEqual(line["variant_result"], {"A": "ok", "B": "ok"})
        self.assertEqual(line["variant_absent"]["A"], sorted(p for p in PICKS if p not in rb.good_row()))
        self.assertIn("accountLicense.apiAccessAllowed", line["variant_absent"]["B"])
        self.assertNotIn("accountLicense.apiAccessAllowed", line["variant_absent"]["A"])
        expected = contract.expected_readback_row(fx.intended())
        license_names = {p for p in expected if p.startswith("accountLicense.")}
        self.assertTrue(license_names and license_names <= set(line["mismatches"]))  # the verdict is the primary (B) read's, unchanged
        for secret in (fx.UUID, fx.NAME, fx.DOMAIN, fx.INTERNAL, fx.ROW_ID):
            self.assertNotIn(secret, text)
        self.assertIsNone(pdr.line_problem(text))
        report, report_text, _ = h.report()
        self.assertEqual(set(report["variants"]), {"A", "B"})
        self.assertEqual(report["variants"]["A"]["result"], "ok")
        self.assertEqual((report["variants"]["B"]["rows"], report["variants"]["B"]["total"]), (1, 1))
        self.assertEqual(report["variants"]["B"]["absent"], line["variant_absent"]["B"])
        self.assertEqual(report["variants"]["A"]["present"], sorted(p for p in PICKS if p in rb.good_row()))
        self.assertEqual(report["variant_absent"], line["variant_absent"])
        for secret in (fx.UUID, fx.NAME, fx.DOMAIN, fx.INTERNAL, fx.ROW_ID):
            self.assertNotIn(secret, report_text)
        guard_bodies = [body["tableServerData"] for body in h.page.table_bodies]
        self.assertEqual(["projection" in s for s in guard_bodies], [False, False])
        self.assertEqual([s["unique_fields"] for s in guard_bodies],
                         [list(probe.STRICT_UNIQUE_FIELDS), json.loads(pot.PAGE_DEFAULT_BODY)["tableServerData"]["unique_fields"]])

    def test_the_picked_organization_email_is_compared_but_never_printed_or_recorded(self):
        email = "test.user+acme@" + fx.INTERNAL
        row = rb.good_row(**{"primaryUser.email": email})
        h = rb.Harness(self, reply=rb.good_reply([row]))
        line, text = h.run()
        report, report_text, _ = h.report()
        self.assertEqual(line["result"], "production_readback_complete")
        for blob in (text, report_text):
            self.assertNotIn(email, blob)
            self.assertNotIn("test.user", blob)

    def test_a_failed_variant_is_unavailable_and_the_run_still_completes(self):
        h = rb.Harness(self)
        h.page = VariantPage({"A": {"status": 0}})
        line, _ = h.run()
        self.assertEqual(line["result"], "production_readback_complete")
        self.assertEqual(line["variant_result"], {"A": "unavailable", "B": "ok"})
        self.assertEqual(line["variant_absent"]["A"], [])

    def test_a_variant_that_raises_is_unavailable(self):
        h = rb.Harness(self)
        with mock.patch.object(runner.ProductionPageReader, "readback_diagnostic", side_effect=RuntimeError("secret " + fx.UUID)):
            line, text = h.run()
        self.assertEqual(line["result"], "production_readback_complete")
        self.assertEqual(line["variant_result"], {"A": "unavailable", "B": "ok"})
        self.assertNotIn(fx.UUID, text)

    def test_session_expired_primary_skips_the_variant_read(self):
        h = rb.Harness(self, reply=rb.good_reply(status=401))
        line, _ = h.run()
        self.assertEqual(line["reason"], "session_expired")
        self.assertEqual(h.page.reloads, 1)
        self.assertEqual(line["variant_result"], {"A": "unavailable", "B": "session_expired"})

    def test_a_blocked_write_during_the_diagnostic_still_refuses_and_keeps_the_variants(self):
        h = rb.Harness(self, attempt_write=True)
        line, _ = h.run()
        self.assertEqual((line["result"], line["reason"]), ("production_readback_refused", "write_request_blocked"))
        self.assertIn("variant_result", line)

    def test_refusals_before_the_page_carry_no_variant_keys(self):
        h = rb.Harness(self)
        line, _ = h.run(environ={})
        self.assertNotIn("variant_result", line)
        self.assertNotIn("variants", json.dumps(line))

    def test_a_bad_variant_field_makes_the_line_not_redacted(self):
        h = rb.Harness(self)
        bad = {"variant_result": {"A": "ok", "B": "ok"}, "variant_absent": {"A": ["a@b.example"], "B": []}}
        with mock.patch.object(contract, "variant_line_fields", return_value=bad):
            line, text = h.run()
        self.assertEqual(line["reason"], "line_not_redacted")
        self.assertNotIn("b.example", text)

    def test_verdict_logic_and_paths_are_untouched_by_the_diagnostic(self):
        h = rb.Harness(self, reply=rb.good_reply([rb.good_row(accountType="customer")]))
        line, _ = h.run()
        self.assertEqual(line["mismatches"], ["accountType"])


if __name__ == "__main__":
    unittest.main()
