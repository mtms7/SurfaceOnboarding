from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from tools.attended_ce_only_playwright import classify_surface_domains, format_surface_domains

MAIN = "example.it"
CO0767_SHAPE = "a2aenergia.eu a2a.it unareti.it gruppoa2a.it"


class ClassifySplitTests(unittest.TestCase):
    def test_space_separated_entries_are_accepted(self):
        main, roots, subs = classify_surface_domains("a2a.it", CO0767_SHAPE)
        self.assertEqual(main, "a2a.it")
        self.assertEqual(roots, ("a2aenergia.eu", "gruppoa2a.it", "unareti.it"))
        self.assertEqual(subs, ())

    def test_wildcard_and_malformed_still_fail_closed(self):
        for value in ("*.other.example", "good.example *.bad.example", "user@other.example", "not-a-domain"):
            with self.subTest(value=value), self.assertRaises(runner.SurfaceSourceError) as caught:
                classify_surface_domains(MAIN, value)
            self.assertEqual(str(caught.exception), "surface_domains_invalid")
        with self.assertRaises(runner.SurfaceSourceError) as caught:
            classify_surface_domains(MAIN, "good.example 10.0.0.0/24")
        self.assertEqual(str(caught.exception), "surface_networks_not_supported")


class FormatTests(unittest.TestCase):
    def test_co0767_shape(self):
        out = format_surface_domains("a2a.it", CO0767_SHAPE)
        self.assertEqual(out["result"], "ok")
        self.assertEqual(out["main"], "a2a.it")
        self.assertIsNone(out["main_error"])
        self.assertEqual(out["alternate_domains"], "a2aenergia.eu, gruppoa2a.it, unareti.it")
        self.assertEqual(out["subdomains"], "")
        self.assertEqual(out["rejected"], [])
        self.assertEqual(out["notes"], ["space_separated", "main_repeated"])
        self.assertEqual(out["salesforce_clean_value"], "a2aenergia.eu, gruppoa2a.it, unareti.it")

    def test_form_separator_matches_the_runner_fill(self):
        self.assertEqual(runner.SURFACE_DOMAIN_SEPARATOR, ", ")
        out = format_surface_domains(MAIN, "b.example c.example")
        self.assertEqual(out["alternate_domains"], "b.example, c.example")

    def test_mixed_separators_tabs_newlines(self):
        out = format_surface_domains(MAIN, "c.example;b.example,\ta.example\r\nd.example ; e.example")
        self.assertEqual(out["alternate_domains"], "a.example, b.example, c.example, d.example, e.example")
        self.assertEqual(out["result"], "ok")
        self.assertNotIn("space_separated", out["notes"])

    def test_commas_with_spaces_are_not_flagged(self):
        out = format_surface_domains(MAIN, "a.example, b.example")
        self.assertEqual(out["notes"], [])

    def test_duplicates_main_repeated_uppercase_trailing_dot_www(self):
        out = format_surface_domains(MAIN, "A.Example a.example. www.a.example EXAMPLE.IT")
        self.assertEqual(out["alternate_domains"], "a.example")
        self.assertEqual(out["notes"], ["space_separated", "lowercased", "trailing_dot_removed",
                                        "duplicate_removed", "www_removed", "main_repeated"])
        self.assertEqual(out["result"], "ok")

    def test_http_url_is_reduced_to_its_host_like_the_runner(self):
        out = format_surface_domains(MAIN, "https://u.example/x")
        self.assertEqual(out["alternate_domains"], "u.example")
        self.assertEqual(out["notes"], ["url_reduced_to_host"])
        self.assertEqual(out["result"], "ok")

    def test_subdomains_are_split_out(self):
        out = format_surface_domains(MAIN, "z.example app.example.it b.example")
        self.assertEqual(out["alternate_domains"], "b.example, z.example")
        self.assertEqual(out["subdomains"], "app.example.it")
        self.assertEqual(out["salesforce_clean_value"], "app.example.it, b.example, z.example")

    def test_rejections(self):
        out = format_surface_domains(
            MAIN, "*.w.example ftp://u.example/x user@e.example co.uk 10.0.0.0/24 192.0.2.1 -bad-.example ok.example")
        reasons = {item["entry"]: item["reason"] for item in out["rejected"]}
        self.assertEqual(reasons, {
            "*.w.example": "wildcard", "ftp://u.example/x": "url", "user@e.example": "email",
            "co.uk": "public_suffix", "10.0.0.0/24": "network", "192.0.2.1": "network", "-bad-.example": "malformed"})
        self.assertEqual(out["alternate_domains"], "ok.example")
        self.assertEqual(out["salesforce_clean_value"], "ok.example")
        self.assertEqual(out["result"], "surface_domains_invalid")

    def test_empty_and_none(self):
        for value in (None, "", " , ; \n"):
            with self.subTest(value=value):
                out = format_surface_domains(MAIN, value)
                self.assertEqual((out["result"], out["alternate_domains"], out["subdomains"], out["notes"]),
                                 ("ok", "", "", []))
                self.assertIsNone(out["salesforce_clean_value"])

    def test_bad_main_never_raises(self):
        for value in (None, "", "app.example.it", "co.uk", "a b.example", "*.example", 5):
            with self.subTest(value=value):
                out = format_surface_domains(value, "a.example")
                self.assertIsNone(out["main"])
                self.assertEqual(out["main_error"], "surface_main_domain_invalid")
                self.assertEqual(out["result"], "surface_main_domain_invalid")
                self.assertEqual(out["alternate_domains"], "a.example")

    def test_non_string_alternates_never_raise(self):
        out = format_surface_domains(MAIN, 5)
        self.assertEqual(out["result"], "surface_domains_invalid")

    def test_result_is_consistent_with_classify(self):
        cases = [CO0767_SHAPE, "a.example b.example", "*.a.example", "a.example 10.0.0.0/24", "co.uk", "x@y.example",
                 "https://a.example", "", None, "A.EXAMPLE a.example", "app.example.it"]
        for main in (MAIN, "a2a.it", "app.example.it", "co.uk", None):
            for alt in cases:
                with self.subTest(main=main, alt=alt):
                    try:
                        classify_surface_domains(main, alt)
                        expected = "ok"
                    except runner.SurfaceSourceError as exc:
                        expected = str(exc)
                    self.assertEqual(format_surface_domains(main, alt)["result"], expected)


class DomainsCliTests(unittest.TestCase):
    def _run(self, rows):
        out = io.StringIO()
        with patch.object(sys, "argv", ["x", "--co", "CO-0767", "--domains"]), \
                patch.object(runner, "_sf_records", return_value=rows) as sf, contextlib.redirect_stdout(out):
            self.assertEqual(runner.main(), 0)
        return json.loads(out.getvalue()), sf

    def test_prints_preview_and_makes_one_read(self):
        payload, sf = self._run([{"Name": "CO-0767", "Main_Domain__c": "a2a.it", "Alternate_Domains__c": CO0767_SHAPE}])
        self.assertEqual(sf.call_count, 1)
        query = sf.call_args.args[0]
        self.assertIn("Name = 'CO-0767'", query)
        self.assertIn("LIMIT 2", query)
        self.assertEqual(payload["co"], "CO-0767")
        self.assertEqual(payload["result"], "ok")
        self.assertEqual(payload["alternate_domains"], "a2aenergia.eu, gruppoa2a.it, unareti.it")
        self.assertEqual(payload["salesforce_writeback"], "not_performed")
        self.assertEqual(payload["leonardo_write"], "not_performed")

    def test_fails_closed(self):
        for rows in ([], [{"Name": "CO-0767"}, {"Name": "CO-0767"}], [{"Name": "CO-0001"}], ["x"]):
            with self.subTest(rows=rows):
                payload, _ = self._run(rows)
                self.assertEqual(payload["result"], "domains_source_unavailable")
        out = io.StringIO()
        with patch.object(sys, "argv", ["x", "--co", "CO-0767", "--domains"]), \
                patch.object(runner, "_sf_records", side_effect=ValueError("salesforce_cli_timeout")), \
                contextlib.redirect_stdout(out):
            runner.main()
        self.assertEqual(json.loads(out.getvalue())["result"], "domains_source_unavailable")

    def test_invalid_reference(self):
        self.assertEqual(runner.run_domains_preview("bad")["result"], "invalid_co_reference")


def _row(main, alternates):
    return {"Onboarding_Product__c": "Surface", "Onboarding_Type__c": "New Product Onboarding",
            "Onboarding_Approval_Status__c": "Pending", "Onboarding_Stage__c": "New",
            "Main_Domain__c": main, "Alternate_Domains__c": alternates}


class DashboardCardTests(unittest.TestCase):
    def test_clean_card_has_no_notice_or_script(self):
        card = dashboard._domains_card(_row(MAIN, "b.example, a.example"))
        self.assertIn("Domains for the tenant", card)
        self.assertIn("<dd>a.example, b.example</dd>", card)
        self.assertIn("chip-ok", card)
        self.assertNotIn("was cleaned", card)
        self.assertNotIn("<script", card)

    def test_cleaned_card_states_the_cleaned_value_as_text(self):
        # Owner 2026-10-07: show what was modified as plain text; no input box, no Copy button, no script.
        card = dashboard._domains_card(_row("a2a.it", CO0767_SHAPE))
        self.assertIn("Alternate Domains cleaned", card)
        self.assertIn("entries were separated by spaces", card)
        self.assertIn(": a2aenergia.eu, gruppoa2a.it, unareti.it</div>", card)
        for absent in ("<input", "copy-domains", "Copy</button>", "<script"):
            self.assertNotIn(absent, card)

    def test_csp_allows_no_script_at_all(self):
        self.assertNotIn("script-src", dashboard.PAGE_CSP)
        self.assertNotIn("nonce", dashboard.PAGE_CSP)
        self.assertIn("default-src 'none'", dashboard.PAGE_CSP)
        self.assertFalse(hasattr(dashboard, "COPY_SCRIPT_PATH"))

    def test_rejected_card_is_blocked_and_escaped(self):
        card = dashboard._domains_card(_row(MAIN, "ok.example *.<b>x</b>.example ftp://u.example/<i>"))
        self.assertIn("chip-bad", card)
        self.assertIn("Nothing can run until Salesforce is corrected", card)
        self.assertIn("wildcards are not supported", card)
        self.assertIn("a URL, not a domain", card)
        self.assertNotIn("<b>x</b>", card)
        self.assertNotIn("<i>", card)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", card)

    def test_clean_value_is_attribute_escaped(self):
        card = dashboard._domains_card(_row(MAIN, "a.example B.example"))
        self.assertNotIn("value=''", card)
        self.assertIn("a.example, b.example", card)

    def test_bad_main_is_blocked(self):
        card = dashboard._domains_card(_row("app.example.it", "a.example"))
        self.assertIn("Main_Domain__c is not a single registrable domain", card)

    def test_card_is_on_surface_and_case_4_to_6_renewal_pages_only(self):
        page = dashboard.page_detail("CO-0767", _row("a2a.it", CO0767_SHAPE))
        self.assertIn("Domains for the tenant", page)
        renewal = _row("a2a.it", CO0767_SHAPE)
        renewal["Onboarding_Product__c"] = "Surface & Credential Exposure"
        renewal["Onboarding_Type__c"] = "Renewal of Existing Product"
        with patch.object(dashboard, "renewal_subscription_rows", return_value=[]):
            self.assertIn("Domains for the tenant", dashboard.page_detail("CO-0767", renewal))
        other = _row("a2a.it", CO0767_SHAPE)
        other["Onboarding_Product__c"] = "Credential Exposure"
        self.assertNotIn("Domains for the tenant", dashboard.page_detail("CO-0768", other))


if __name__ == "__main__":
    unittest.main()
