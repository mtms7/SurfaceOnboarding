"""Production-checked Onboardings: review summary, production-only duplicate validation, one-use approval (docs/43 s4).

Hermetic: every Salesforce, clone and Leonardo dependency is patched; approvals live in a temporary file. The approval
POST records a decision only and starts nothing."""

import contextlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import review_approval as ra

CE_ROW = {"Name": "CO-0780", "Account_Name__c": "State Land Service", "Onboarding_Product__c": dashboard.CE_ROUTE_PRODUCT,
          "Onboarding_Type__c": dashboard.CE_ROUTE_TYPE}
MANUAL_ROW = {"Name": "CO-0769", "Account_Name__c": "Illy", "Onboarding_Product__c": "Credential Exposure",
              "Onboarding_Type__c": "Renewal of Existing Product"}
INPUTS = {"engine": dashboard.CE_ENGINE, "co_source": {"tenant_names": ("State Land Service",), "domains": ("vzd.example",)},
          "plan": {"primary_domain": "vzd.example", "alternate_domains": (), "license_type": "Credential Exposure",
                   "license_end": "2027-07-01", "license_start": "2026-07-01", "spycloud_shown": True}}


def match(status="not_in_production", age=600, **extra):
    base = {"status": status, "reason": "", "fields": [], "differs": [], "candidates": [], "target": None,
            "captured_at": "2026-10-13T11:00:00Z", "age_seconds": age, "tenants_checked": 120, "route": "ce"}
    base.update(extra)
    return base


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "approvals.json"
        self.row = dict(CE_ROW)
        self.revision = "rev-1"
        self.blockers = ()
        self.match = match()
        self.inputs = INPUTS
        self.leonardo_calls = 0
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)

    @contextlib.contextmanager
    def patched(self):
        def forbidden(*_args, **_kwargs):
            self.leonardo_calls += 1
            raise AssertionError("Leonardo must not be consulted")

        evaluation = lambda *_a, **_k: dashboard.CredentialExposureFillPreflight(  # noqa: E731
            "CO-0780", self.revision, 1, self.blockers)
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(dashboard, "REVIEW_APPROVAL_PATH", self.path))
            # /onboard is now the production landing: its start dates, approvals and records stay local and hermetic.
            enter(patch.object(dashboard, "queue_start_dates", return_value={}))
            enter(patch.object(dashboard, "prefetch_display_read"))
            enter(patch.object(dashboard, "PRODUCTION_APPROVAL_PATH", Path(self.tmp.name) / "production_create_approvals.json"))
            enter(patch.object(dashboard, "production_records_parent", return_value=Path(self.tmp.name)))
            enter(patch.object(dashboard, "detail_row", side_effect=lambda ref: dict(self.row)))
            enter(patch.object(dashboard, "evaluate_ce_only_fill_preflight", side_effect=evaluation))
            enter(patch.object(dashboard, "evaluate_surface_fill_preflight", side_effect=evaluation))
            enter(patch.object(dashboard, "production_review_inputs", side_effect=lambda *a: self.inputs))
            enter(patch.object(dashboard, "_match_of", side_effect=lambda *a: dict(self.match)))
            enter(patch.object(dashboard, "run_inventory_precheck", side_effect=forbidden))
            enter(patch.object(dashboard, "queue_rows", side_effect=lambda: [dict(self.row, **{"Account__r.Name": "State Land"}),
                                                                           dict(MANUAL_ROW)]))
            enter(patch.object(dashboard, "login_required", return_value=False))
            enter(patch.object(dashboard, "request_origin_problem", return_value=None))
            yield

    def get(self, path):
        class Request:
            headers = {}

            def __init__(self):
                self.path, self.redirects, self.pages = path, [], []

            def send_redirect(self, location):
                self.redirects.append(location)

            def send_page(self, status, page):
                self.pages.append((int(status), page))

        request = Request()
        with self.patched():
            dashboard.Handler.do_GET(request)
        return request

    def post(self, summary_hash=None, revision=None, reference="CO-0780"):
        class Request:
            headers = {}

            def __init__(self):
                self.redirects, self.pages = [], []

            def send_redirect(self, location):
                self.redirects.append(location)

            def send_page(self, status, page):
                self.pages.append((int(status), page))

        request = Request()
        with self.patched():
            summary, _ = dashboard.build_review_summary(reference)
            form = {"summary_hash": [summary_hash or ra.summary_hash(summary)],
                    "source_revision": [revision if revision is not None else summary["source_revision"]]}
            dashboard.Handler._post_review_approve(request, reference, form)
        return request

    def approval(self):
        return ra.load(self.path).get("CO-0780")


class SummaryTests(Case):
    def test_summary_carries_what_will_be_sent_and_never_consults_leonardo(self):
        with self.patched():
            summary, extras = dashboard.build_review_summary("CO-0780")
        self.assertEqual(summary["blockers"], [])
        self.assertEqual(summary["route"], dashboard.CE_ENGINE)
        self.assertEqual(summary["source_revision"], "rev-1")
        self.assertEqual(summary["values"]["tenant_names"], ["State Land Service"])
        self.assertEqual(summary["values"]["domains"], ["vzd.example"])
        self.assertEqual(summary["values"]["plan"]["license_end"], "2027-07-01")
        self.assertEqual(summary["production_check"]["status"], "not_in_production")
        self.assertIs(summary["leonardo_consulted"], False)
        self.assertEqual(extras["age_seconds"], 600)
        self.assertEqual(self.leonardo_calls, 0)

    def test_clone_age_and_capture_time_do_not_change_the_hash(self):
        with self.patched():
            first, _ = dashboard.build_review_summary("CO-0780")
            self.match = match(age=1200, captured_at="2026-10-13T11:10:00Z")
            second, _ = dashboard.build_review_summary("CO-0780")
        self.assertEqual(ra.summary_hash(first), ra.summary_hash(second))

    def test_every_uncertainty_is_a_blocker(self):
        cases = {
            "production_exists_matches": dict(match=match("exists_matches")),
            "production_exists_differs": dict(match=match("exists_differs")),
            "production_exists_other": dict(match=match("exists_other")),
            "production_ambiguous": dict(match=match("ambiguous")),
            "production_clone_unavailable": dict(match=match("clone_unavailable")),
            "production_clone_stale": dict(match=match(age=6 * 3600 + 1)),
            "production_clone_age_unknown": dict(match=match(age=None)),
            "source_revision_missing": dict(revision=""),
            "evaluation_blocker": dict(blockers=("evaluation_blocker",)),
            "no_automated_route": dict(row=dict(MANUAL_ROW)),
        }
        for code, change in cases.items():
            with self.subTest(code=code):
                for key, value in change.items():
                    setattr(self, key, value)
                with self.patched():
                    summary, _ = dashboard.build_review_summary("CO-0780")
                self.assertIn(code, summary["blockers"])
                self.setUp()

    def test_unreadable_values_block(self):
        with self.patched(), patch.object(dashboard, "production_review_inputs", side_effect=RuntimeError("boom")):
            summary, _ = dashboard.build_review_summary("CO-0780")
        self.assertIn("summary_source_unreadable", summary["blockers"])

    def test_salesforce_read_failure_propagates(self):
        with self.patched(), patch.object(dashboard, "detail_row", side_effect=dashboard.ReadUnavailable("x")):
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.build_review_summary("CO-0780")


class PageTests(Case):
    def test_queue_page_lists_cos_and_links_supported_routes_only(self):
        request = self.get("/onboard")
        status, page = request.pages[0]
        self.assertEqual(status, 200)
        self.assertIn("href='/onboard/CO-0780'", page)
        self.assertNotIn("href='/onboard/CO-0769'", page)
        self.assertIn("No automated route", page)
        self.assertIn("Leonardo is not consulted", page)

    def test_review_page_shows_data_production_check_and_an_enabled_approve_button(self):
        status, page = self.get("/onboard/CO-0780").pages[0]
        self.assertEqual(status, 200)
        for text in ("State Land Service", "vzd.example", "2027-07-01", "Duplicate validation: production only",
                     "not in production", "Not approved yet", "Approve this summary", "creates nothing"):
            self.assertIn(text, page)
        self.assertNotIn("disabled aria-disabled", page)
        self.assertEqual(self.leonardo_calls, 0)

    def test_blocked_page_disables_approval_and_lists_the_reason(self):
        self.match = match("exists_matches")
        status, page = self.get("/onboard/CO-0780").pages[0]
        self.assertIn("disabled aria-disabled='true'", page)
        self.assertIn("already matches this CO", page)

    def test_unreadable_approval_file_is_never_an_approval(self):
        self.path.write_text("garbage", encoding="utf-8")
        status, page = self.get("/onboard/CO-0780").pages[0]
        self.assertEqual(status, 200)
        self.assertIn("cannot be read", page)
        self.assertNotIn("<strong>Approved</strong>", page)


class ApproveTests(Case):
    def test_approve_records_the_summary_bound_approval_and_starts_nothing(self):
        with patch.object(dashboard.Handler, "_claim_and_launch", side_effect=AssertionError("no launch")), \
                patch.object(dashboard, "record_runner_start", side_effect=AssertionError("no run record")):
            request = self.post()
        self.assertEqual(request.redirects, ["/onboard/CO-0780?notice=approved"])
        record = self.approval()
        self.assertEqual(record["operator"], "desktop-operator")
        self.assertEqual(record["route"], dashboard.CE_ENGINE)
        self.assertEqual(record["source_revision"], "rev-1")
        status, page = self.get("/onboard/CO-0780").pages[0]
        self.assertIn("<strong>Approved</strong>", page)

    def test_a_stale_page_cannot_approve_a_changed_co(self):
        with self.patched():
            old, _ = dashboard.build_review_summary("CO-0780")
        stale_hash = ra.summary_hash(old)
        self.inputs = {**INPUTS, "co_source": {"tenant_names": ("State Land Service Fixed",), "domains": ("vzd.example",)}}
        request = self.post(summary_hash=stale_hash)
        self.assertEqual(request.redirects, ["/onboard/CO-0780?notice=changed"])
        self.assertIsNone(self.approval())

    def test_a_changed_revision_cannot_be_approved_from_an_old_page(self):
        request = self.post(revision="rev-0")
        self.assertEqual(request.redirects, ["/onboard/CO-0780?notice=changed"])
        self.assertIsNone(self.approval())

    def test_blocked_summaries_cannot_be_approved_even_with_a_forged_post(self):
        self.match = match("exists_differs")
        request = self.post()
        self.assertEqual(request.redirects, ["/onboard/CO-0780?notice=blocked"])
        self.assertIsNone(self.approval())

    def test_fixing_the_co_invalidates_the_old_approval_and_a_new_one_is_needed(self):
        self.post()
        self.revision = "rev-2"
        self.inputs = {**INPUTS, "co_source": {"tenant_names": ("State Land Service Fixed",), "domains": ("vzd.example",)}}
        status, page = self.get("/onboard/CO-0780").pages[0]
        self.assertIn("Earlier approval no longer applies", page)
        self.assertNotIn("<strong>Approved</strong>", page)
        self.post()
        status, page = self.get("/onboard/CO-0780").pages[0]
        self.assertIn("<strong>Approved</strong>", page)
        self.assertEqual(self.approval()["source_revision"], "rev-2")

    def test_an_expired_approval_does_not_apply(self):
        self.post()
        data = ra.load(self.path)
        data["CO-0780"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.path.write_text(__import__("json").dumps(data), encoding="utf-8")
        status, page = self.get("/onboard/CO-0780").pages[0]
        self.assertIn("Earlier approval expired", page)

    def test_salesforce_read_failure_records_nothing(self):
        with self.patched(), patch.object(dashboard, "detail_row", side_effect=dashboard.ReadUnavailable("x")):
            class Request:
                headers = {}
                redirects, pages = [], []

                def send_redirect(self, location):
                    self.redirects.append(location)

                def send_page(self, status, page):
                    self.pages.append((int(status), page))

            request = Request()
            dashboard.Handler._post_review_approve(request, "CO-0780", {})
        self.assertEqual(request.redirects, [])
        self.assertFalse(self.path.exists())


class RouteWiringTests(unittest.TestCase):
    def test_route_is_a_post_route_and_refused_on_the_vm_for_everyone(self):
        self.assertIn("/attended/review-approve", dashboard.POST_ROUTES)
        self.assertNotIn("/attended/review-approve", dashboard.VM_OPERATOR_POSTS)

    def test_the_review_code_never_reaches_leonardo_or_a_browser(self):
        import inspect
        import re
        source = "".join(inspect.getsource(obj) for obj in (
            dashboard.build_review_summary, dashboard.page_onboard_review, dashboard.render_onboard_queue,
            dashboard.Handler._post_review_approve)).lower()
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        for forbidden in ("run_inventory_precheck", "run_duplicate_check", "playwright", "subprocess", "sf_write_json",
                          "start_attended", "_claim_and_launch", "record_runner_start", "leonardo.pentera"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
