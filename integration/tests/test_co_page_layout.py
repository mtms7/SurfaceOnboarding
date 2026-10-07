"""CO detail page layout (owner-approved 2026-10-07).

Header, tracker, pinned banners, ONE "What to do now" card, at most 10 key facts, folds, one footer line.
Every section reader is stubbed: nothing here reaches Salesforce or Leonardo.
"""
from __future__ import annotations

import contextlib
import re
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard

SURFACE = {"Onboarding_Product__c": "Surface", "Onboarding_Type__c": "New Product Onboarding"}
CASE3 = {"Onboarding_Product__c": "Surface & Credential Exposure", "Onboarding_Type__c": "New Product Onboarding"}
CE = {"Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding"}
RENEWAL = {"Onboarding_Product__c": "Surface & Credential Exposure", "Onboarding_Type__c": "Renewal of Existing Product"}
CASE4 = {"Onboarding_Product__c": "Surface & Credential Exposure",
         "Onboarding_Type__c": "Renewal of Surface + New Credential Exposure Module"}
SCOPE = {
    "tier": "prime", "scanning_interval": "Weekly", "main_domains": 1, "alternate_root_domains": 2,
    "requested_subdomains": 3, "number_of_domains": 3, "baseline_subdomains": 1000, "addon_subdomains": 400,
    "licensed_subdomains": 1400, "product_domains": None, "assets": 10000, "license_start": "2026-09-29",
    "license_end": "2027-09-28", "large_scope": True, "core_plus_present": False,
    "production_onboarding_day": "2026-10-20", "subscription_start": "2026-10-22", "leaked_credentials_domains": 1, "leaked_credentials_interval": "Weekly",
}
READBACK = {"surface_account_id": "A" * 20, "account_uuid": "a" * 32, "leonardo_state": "Account Scanning",
            "observed_on": "2026-10-06", "source": "Leonardo Development Details readback"}
SUBSCRIPTIONS = [
    {"Product_Full_Name__c": "Pentera Surface Prime - 1000 Subdomains", "DealHub_Status__c": "Pending",
     "DealHub_Subscription_Start_Date__c": "2026-10-27", "DealHub_Subscription_End_Date__c": "2029-10-26"},
    {"Product_Full_Name__c": "Pentera Core Plus Commercial - 500 End Points", "DealHub_Status__c": "Pending",
     "DealHub_Subscription_Start_Date__c": "2026-10-27", "DealHub_Subscription_End_Date__c": "2029-10-26"},
]
NOW = datetime(2026, 10, 7, 9, 0)


def surface_evaluation(reference="CO-0702", route=dashboard.SURFACE_ENGINE, *, core_plus=False):
    return dashboard.SurfaceScopePreflight(reference, "rev1", (), dict(SCOPE, core_plus_present=core_plus), core_plus, route)


def row_for(base, **extra):
    row = {"Name": "CO-0702", "Account_Name__c": "Acme", "Onboarding_Approval_Status__c": "Approved",
           "Onboarding_Stage__c": "New", "Main_Domain__c": "acme.example", "Email_Domains__c": "mail.example",
           "LastModifiedDate": "2026-10-07T08:00:00Z", **base}
    row.update(extra)
    return row


def render(row, reference="CO-0702", *, readback=None, runner=None, scan=None, validation=None, evaluation=None,
           ce_evaluation=None, spycloud=None, outcomes=None, tenant=None, reminders=None, confirmations=None,
           checks=None, mirrors=frozenset(), writebacks=None, commercial=None, notification="", rows=None):
    """Render page_detail with every reader stubbed."""
    with contextlib.ExitStack() as stack:
        enter = stack.enter_context
        enter(patch.object(dashboard, "attended_leonardo_readbacks",
                           return_value={reference: readback} if readback else {}))
        enter(patch.object(dashboard, "load_runner_state", return_value=runner or {}))
        enter(patch.object(dashboard, "attended_scan_statuses", return_value=scan or {}))
        enter(patch.object(dashboard, "attended_validations", return_value=validation or {}))
        enter(patch.object(dashboard, "load_user_created_confirmations", return_value=confirmations or {}))
        enter(patch.object(dashboard, "load_attended_reminders", return_value=reminders or {}))
        enter(patch.object(dashboard, "_stage_tenant", return_value=(tenant, "dev" if tenant else "", "2026-10-06T08:00")))
        enter(patch.object(dashboard, "attended_spycloud_states", return_value=spycloud or {}))
        enter(patch.object(dashboard, "attended_renewal_outcomes", return_value=outcomes or {}))
        enter(patch.object(dashboard, "dev_mirror_ids", return_value=mirrors))
        enter(patch.object(dashboard, "load_check_state", return_value=checks or {}))
        enter(patch.object(dashboard, "salesforce_id_writebacks", return_value=writebacks or {}))
        enter(patch.object(dashboard, "renewal_subscription_rows", return_value=list(rows or SUBSCRIPTIONS)))
        enter(patch.object(dashboard, "manual_start_ack_nonce", return_value=None))
        enter(patch.object(dashboard, "evaluate_surface_fill_preflight",
                           return_value=evaluation or surface_evaluation(reference)))
        if ce_evaluation is not None:
            enter(patch.object(dashboard, "evaluate_ce_only_fill_preflight", return_value=ce_evaluation))
        else:
            enter(patch.object(dashboard, "evaluate_ce_only_fill_preflight", side_effect=dashboard.ReadUnavailable()))
        enter(patch.object(dashboard, "sf_json", side_effect=AssertionError("no Salesforce in layout tests")))
        return dashboard.page_detail(reference, row, notification, commercial)


def ce_evaluation(reference="CO-0702"):
    return dashboard.CredentialExposureFillPreflight(reference, "rev1", 1, ())


def do_now(page):
    start = page.index("<section class='card nowcard'")
    return page[start:page.index("</section>", start) + len("</section>")]


def key_facts(page):
    start = page.index("<section class='keyfacts'")
    return page[start:page.index("</section>", start)]


def fold(page, title):
    start = page.index("<h2 class='sum-h'>" + title + "</h2>")
    start = page.rindex("<details class='more'", 0, start)
    """From the fold's <details> tag up to the next top-level fold (or the footer)."""
    following = [page.find(marker, start + 10) for marker in ("<details class='more'><summary><h2 class='sum-h'>",
                                                               "<details class='more' open><summary><h2 class='sum-h'>",
                                                               "<p class='footline'>")]
    return page[start:min(index for index in following if index != -1)]


def fact_count(page):
    return key_facts(page).count("<dt>")


VERIFIED_RUN = {"CO-0702": {"source_revision": "rev1", "route": dashboard.SURFACE_ENGINE, "result": "readback_verified",
                            "completed_on": "2026-10-06T10:00:00", "license_start_entered": "2026-10-06",
                            "license_end_entered": "2027-10-05"}}
SCAN_DONE = {"CO-0702": {"execution_state": "done", "executions": [], "state": "scan_completed", "status_enum": "COMPLETED",
                         "last_recon_scan": None, "duration_ms": None, "observed_at": NOW, "expires_at": NOW}}


class LayoutOrderTests(unittest.TestCase):
    def test_sections_come_in_the_approved_order(self):
        checks = {"CO-0702": {"kind": "production_duplicate", "result": "duplicate_production_clone_match",
                              "detail": '{"matches": [{"name": "Acme", "id": "x"}]}'}}
        row = row_for(SURFACE, Alternate_Domains__c="A.example B.example")
        page = render(row, checks=checks)
        order = [page.index("<h1>CO-0702</h1>"), page.index("class='tracker'"), page.index("class='pinned'"),
                 page.index("id='now-title'"), page.index("class='keyfacts'"), page.index("<details class='more'"),
                 page.index("class='footline'")]
        self.assertEqual(order, sorted(order))
        self.assertLess(page.index("<h2 class='sum-h'>Salesforce record</h2>"), page.index("<h2 class='sum-h'>Diagnostics</h2>"))
        self.assertLess(page.index("<h2 class='sum-h'>Diagnostics</h2>"), page.index("class='footline'"))

    def test_header_carries_route_approval_tenant_and_refresh(self):
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN)
        head = page[page.index("<div class='page-head'>"):page.index("class='tracker'")]
        for text in ("CO-0702", "Acme", "Surface-only", "chip-ok'>Approved<", "Read from Salesforce at",
                     "name='refresh' value='1'", ">Refresh<"):
            self.assertIn(text, head)

    def test_tracker_note_is_only_the_ahead_chip(self):
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN)
        tracker = page[page.index("<nav class='tracker'"):page.index("</nav>")]
        self.assertIn("Salesforce still shows: New", tracker)
        self.assertNotIn("<form", tracker)
        self.assertNotIn("matches", tracker)


class PinnedBannerTests(unittest.TestCase):
    def assert_pinned(self, page, text):
        self.assertIn(text, page)
        self.assertGreater(page.index(text), page.index("class='pinned'"))
        self.assertLess(page.index(text), page.index("id='now-title'"))  # before the card, so before every fold

    def test_domain_banners_with_the_copy_button_are_pinned_outside_the_fold(self):
        page = render(row_for(SURFACE, Alternate_Domains__c="A.example B.example"))
        self.assert_pinned(page, "The Salesforce value was cleaned")
        self.assert_pinned(page, "id='copy-domains'")
        self.assertIn("<script src='/static/copy-domains.js'></script>", page)
        # The domain lists themselves are inside the Salesforce record fold.
        self.assertGreater(page.index("Domains for the tenant"), page.index("<h2 class='sum-h'>Salesforce record</h2>"))

    def test_main_domain_error_and_rejected_entries_are_pinned(self):
        page = render(row_for(SURFACE, Main_Domain__c="not a domain", Alternate_Domains__c="*.wild.example"))
        self.assert_pinned(page, "Main_Domain__c is not a single registrable domain.")
        self.assert_pinned(page, "These Alternate Domains entries are rejected:")

    def test_production_duplicate_and_unavailable_markers_are_pinned(self):
        match = {"CO-0702": {"kind": "production_duplicate", "result": "duplicate_production_clone_match",
                             "detail": '{"matches": [{"name": "Acme", "id": "abc"}]}'}}
        self.assert_pinned(render(row_for(SURFACE), checks=match), "This CO already has a tenant in production.")
        unavailable = {"CO-0702": {"kind": "production_duplicate", "result": "production_clone_unavailable",
                                   "detail": '{"reason": "stale"}'}}
        self.assert_pinned(render(row_for(SURFACE), checks=unavailable), "Production check unavailable")

    def test_salesforce_id_present_is_pinned_and_blocks_the_start(self):
        page = render(row_for(SURFACE, Surface_Account_ID__c="x" * 24))
        self.assert_pinned(page, "Already has an ID in Salesforce")
        self.assertNotIn("action='/attended/start-surface-runner'", page)
        self.assertIn("Blocked: this CO already has an ID in Salesforce", do_now(page))

    def test_dev_duplicate_and_create_uncertain_are_pinned(self):
        dup = {"CO-0702": {"source_revision": "rev1", "route": dashboard.SURFACE_ENGINE, "result": "duplicate_found",
                           "completed_on": "2026-10-06T10:00:00"}}
        page = render(row_for(SURFACE), runner=dup)
        self.assert_pinned(page, "<code>duplicate_found</code>")  # the outcome banner of the DEV duplicate result
        self.assertNotIn("action='/attended/start-surface-runner'", page)
        uncertain = {"CO-0702": {"source_revision": "rev1", "route": dashboard.SURFACE_ENGINE,
                                 "result": "confirm_no_signal", "license_start_entered": "2026-10-06",
                                 "license_end_entered": "2027-10-05"}}
        with patch.object(dashboard, "create_uncertain", return_value=True):
            page = render(row_for(SURFACE), runner=uncertain)
        self.assert_pinned(page, "Create uncertain — verify in Leonardo")
        self.assert_pinned(page, "action='/attended/verify-uncertain'")

    def test_write_uncertain_id_writeback_is_pinned(self):
        page = render(row_for(SURFACE), writebacks={"CO-0702": {"result": "write_uncertain"}})
        self.assert_pinned(page, "Salesforce ID write-back uncertain.")

    def test_validation_drift_scan_failure_and_spycloud_on_are_pinned(self):
        validation = {"CO-0702": {"primary_user_matches": True, "primary_user_checked_on": "2026-10-06", "plan_note": "",
                                  "checks": [{"check": "Tier", "group": "Licence", "status": "drift", "expected": "a", "found": "b"}],
                                  "observed_at": NOW, "expires_at": NOW}}
        scan = {"CO-0702": {"state": "scan_failed", "status_enum": "FAILED", "last_recon_scan": None, "duration_ms": None,
                            "observed_at": NOW, "expires_at": NOW}}
        spycloud = {"CO-0702": {"outcome": "spycloud_dry_run_on", "mode": "dry_run", "observed_at": NOW, "ok": False}}
        page = render(row_for(CASE3), readback=READBACK, runner=VERIFIED_RUN, validation=validation, scan=scan,
                      spycloud=spycloud, evaluation=surface_evaluation(route=dashboard.CASE3_ENGINE))
        self.assert_pinned(page, "Validation found 1 difference(s)")
        self.assert_pinned(page, "Scan failed.")
        self.assert_pinned(page, "SpyCloud is still ON.")
        # A warn / blocked state opens "Tenant checks".
        self.assertIn("<details class='more' open><summary><h2 class='sum-h'>Tenant checks</h2>", page)
        self.assertNotIn("<details class='more' open><summary><h2 class='sum-h'>Tenant checks</h2>",
                         render(row_for(CASE3), readback=READBACK, runner=VERIFIED_RUN,
                                evaluation=surface_evaluation(route=dashboard.CASE3_ENGINE)))

    def test_renewal_attempted_unverified_is_pinned(self):
        outcome = {"CO-0702": {"result": "renewal_saved_unverified", "mode": "confirm_write",
                               "leonardo_write": "attempted_unverified", "observed_at": NOW, "changes": 1,
                               "added_domains": 0, "old_expiration": None, "new_expiration": None}}
        page = render(row_for(RENEWAL), outcomes=outcome)
        self.assert_pinned(page, "The last renewal save is unverified.")

    def test_approval_not_approved_and_source_changed_are_pinned(self):
        page = render(row_for(SURFACE, Onboarding_Approval_Status__c="Pending"))
        self.assert_pinned(page, "Source requirements need review")
        page = render(row_for(SURFACE, Onboarding_Approval_Status__c="Pending"),
                      commercial={"commercial_ready": True, "manual_review_required": False})
        self.assert_pinned(page, "Approval Status is Pending, not Approved.")
        old = {"CO-0702": {"source_revision": "older", "route": dashboard.SURFACE_ENGINE, "result": "preflight_blocked"}}
        self.assert_pinned(render(row_for(SURFACE), runner=old), "The CO changed in Salesforce since the last attended run")

    def test_case4_blocked_banner_stays_visible(self):
        row = row_for(CASE4, Name="CO-0741", Onboarding_Comments__c="2026-08-01 - 2029-07-31")
        page = render(row, reference="CO-0741")
        self.assert_pinned(page, "Case 4 comments validation")
        self.assertIn("case_not_mapped_yet", page[:page.index("id='now-title'")])


class DoNowTests(unittest.TestCase):
    def test_waiting_for_approval_has_no_button(self):
        page = render(row_for(SURFACE, Onboarding_Approval_Status__c="Pending"))
        card = do_now(page)
        self.assertIn("Waiting for approval in Salesforce", card)
        self.assertNotIn("<form", card)
        self.assertNotIn("<button", card)
        page = render(row_for(SURFACE, Onboarding_Approval_Status__c="Pending"),
                      commercial={"commercial_ready": True, "manual_review_required": True})
        card = do_now(page)
        self.assertIn("Source ready to onboard — manual review required", card)
        self.assertIn("will not be overwritten automatically", card)
        self.assertNotIn("<form", card)

    def test_ready_surface_shows_the_start_form_exactly_as_before(self):
        evaluation = surface_evaluation()
        page = render(row_for(SURFACE), evaluation=evaluation)
        card = do_now(page)
        self.assertIn("Start onboarding", card)
        self.assertIn(dashboard._surface_start_form(evaluation), card)  # identical markup: fields, nonce-bound hidden inputs
        self.assertEqual(card.count("<button type='submit'>Start Onboarding</button>"), 1)
        for text in ("name='scope_reviewed' value='1' required", "name='attended_create_authorized' value='1' required",
                     "name='source_revision' value='rev1'", "name='scope_digest' value='" + evaluation.scope_digest + "'",
                     "action='/attended/start-surface-runner'", "<h3 class='sub-h'>Scope to review</h3>"):
            self.assertIn(text, card)
        self.assertNotIn("Source ready to onboard", page)
        self.assertEqual(page.count("action='/attended/start-surface-runner'"), 1)

    def test_ready_ce_shows_the_start_form_exactly_as_before(self):
        evaluation = ce_evaluation()
        page = render(row_for(CE), ce_evaluation=evaluation)
        card = do_now(page)
        self.assertIn(dashboard._ce_only_start_form(evaluation), card)
        self.assertEqual(page.count("action='/attended/start-ce-only-runner'"), 1)

    def test_running_failed_and_uncertain_runs(self):
        running = {"CO-0702": {"source_revision": "rev1", "route": dashboard.SURFACE_ENGINE, "started_on": "2026-10-07T09:00:00"}}
        card = do_now(render(row_for(SURFACE), runner=running))
        self.assertIn("Onboarding is running", card)
        self.assertIn("View progress", card)
        self.assertNotIn("start-surface-runner", card)
        failed = {"CO-0702": {"source_revision": "rev1", "route": dashboard.SURFACE_ENGINE,
                              "result": "max_scan_duration_schema_unavailable", "completed_on": "2026-10-06T10:00:00"}}
        card = do_now(render(row_for(SURFACE), runner=failed))
        self.assertIn("The last run failed", card)
        self.assertIn("action='/attended/reset-ce-only-runner'", card)
        self.assertNotIn("start-surface-runner", card)
        with patch.object(dashboard, "create_uncertain", return_value=True):
            card = do_now(render(row_for(SURFACE), runner=failed))
        self.assertIn("Verify the tenant in Leonardo", card)
        self.assertNotIn("start-surface-runner", card)
        self.assertNotIn("reset-ce-only-runner", card)

    def test_blocked_scope_lists_the_blocker_without_a_start(self):
        blocked = dashboard.SurfaceScopePreflight("CO-0702", "rev1", ("surface_networks_not_supported",))
        card = do_now(render(row_for(SURFACE), evaluation=blocked))
        self.assertIn("Resolve the blockers, then start", card)
        self.assertIn("surface_networks_not_supported", card)
        self.assertNotIn("Start Onboarding", card)

    def test_tenant_created_scan_not_done_verifies_then_scan_settings(self):
        card = do_now(render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN))
        self.assertIn("Verify the tenant", card)
        self.assertIn("action='/attended/validate'", card)
        self.assertIn("action='/attended/mark-scan-settings-off'", card)
        self.assertNotIn("mark-operator-assigned", card)  # only after the scan finished
        self.assertNotIn("start-surface-runner", card)
        self.assertEqual(card.count(dashboard.REMINDER_NOTE), 1)

    def test_case3_and_ce_ask_for_spycloud_off(self):
        run = {"CO-0702": {**VERIFIED_RUN["CO-0702"], "route": dashboard.CASE3_ENGINE}}
        card = do_now(render(row_for(CASE3), readback=READBACK, runner=run,
                             evaluation=surface_evaluation(route=dashboard.CASE3_ENGINE)))
        self.assertIn("action='/attended/spycloud-check'", card)
        self.assertIn("--spycloud-off --confirm-write", card)
        ce_run = {"CO-0702": {**VERIFIED_RUN["CO-0702"], "route": dashboard.CE_ENGINE}}
        card = do_now(render(row_for(CE), readback=READBACK, runner=ce_run, ce_evaluation=ce_evaluation()))
        self.assertIn("action='/attended/spycloud-check'", card)
        self.assertIn("Assign the Operator Account", card)  # Case 2: text only, no reminder button
        self.assertNotIn("mark-operator-assigned", card)
        self.assertIn("action='/attended/confirm-user-created'", card)  # no scan stage in Case 2

    def test_scan_completed_assigns_the_operator_then_confirms_the_user(self):
        tenant = {"operator_assigned": False, "primary_user": {"present": True}}
        card = do_now(render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN, scan=SCAN_DONE, tenant=tenant,
                             evaluation=surface_evaluation(core_plus=True)))
        self.assertIn("Assign the operator, then confirm the customer user", card)
        self.assertIn("action='/attended/mark-operator-assigned'", card)
        self.assertIn("action='/attended/mark-ce-enabled'", card)  # Case 1 with Core Plus
        self.assertIn("action='/attended/confirm-user-created'", card)
        self.assertIn("No operator assigned", card)

    def test_user_created_points_to_the_salesforce_ids_and_opens_them(self):
        tenant = {"operator_assigned": True, "primary_user": {"present": True}}
        validation = {"CO-0702": {"primary_user_matches": True, "primary_user_checked_on": "2026-10-06", "plan_note": "",
                                  "checks": [], "observed_at": NOW, "expires_at": NOW}}
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN, scan=SCAN_DONE, tenant=tenant,
                      validation=validation, reminders={"CO-0702": {"scan_settings_off_on": "2026-10-06T10:00:00",
                                                                    "operator_assigned_on": "2026-10-06T10:00:00"}})
        card = do_now(page)
        self.assertIn("Update Salesforce stage and IDs (production only)", card)
        self.assertNotIn("<form", card)
        self.assertIn("<details class='more' open><summary><h2 class='sum-h'>Salesforce record</h2>", page)
        self.assertIn("<details class='more source-ready' open aria-labelledby='salesforce-ids-title'>", page)

    def test_manual_confirmation_offers_undo_at_user_created(self):
        tenant = {"operator_assigned": True, "primary_user": {"present": False}}
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN, scan=SCAN_DONE, tenant=tenant,
                      confirmations={"CO-0702": {"confirmed": True, "confirmed_on": "2026-10-06", "confirmed_by": "op"}})
        card = do_now(page)
        self.assertIn("action='/attended/unconfirm-user-created'", card)
        self.assertIn("Confirmed manually on 2026-10-06", card)

    def test_onboarding_completed_is_done(self):
        page = render(row_for(SURFACE, Onboarding_Stage__c="Onboarding Completed"), readback=READBACK, runner=VERIFIED_RUN,
                      scan=SCAN_DONE, tenant={"operator_assigned": True, "primary_user": {"present": True}},
                      confirmations={"CO-0702": {"confirmed": True, "confirmed_on": "2026-10-06", "confirmed_by": "op"}})
        self.assertIn(">Done</b>", do_now(page))

    def test_confirm_form_is_unchanged(self):
        tenant = {"operator_assigned": True, "primary_user": {"present": False}}
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN, scan=SCAN_DONE, tenant=tenant)
        form = ("<form method='post' action='/attended/confirm-user-created'><input type='hidden' name='reference' "
                "value='CO-0702'><button type='submit' class='ghost sm'>Confirm user created</button></form>")
        self.assertIn(form, do_now(page))

    def test_unmapped_route_points_to_diagnostics(self):
        page = render(row_for({"Onboarding_Product__c": "Surface", "Onboarding_Type__c": "Something else"}))
        self.assertIn("No automated route for this CO", do_now(page))
        self.assertIn("action='/attended/leonardo-session-check'", fold(page, "Diagnostics"))
        self.assertNotIn("action='/attended/leonardo-session-check'", page[:page.index("<h2 class='sum-h'>Diagnostics</h2>")])


class RenewalDoNowTests(unittest.TestCase):
    """Owner decision 2026-10-07: renewals (Cases 4-6) start from the dashboard like Start onboarding."""
    MIRRORED = dict(readback=READBACK, mirrors=frozenset({READBACK["surface_account_id"]}))
    REV = "2026-10-07T08:00:00Z"

    def outcome(self, result, mode="dry_run", write="not_performed", **extra):
        return {"CO-0702": {"result": result, "mode": mode, "leonardo_write": write, "observed_at": NOW, "changes": 2,
                            "added_domains": extra.pop("added_domains", 0), "old_expiration": "2026-10-26",
                            "new_expiration": "2029-10-26", **extra}}

    def run_record(self, result=None, uncertain=None, revision=REV, **extra):
        record = {"source_revision": revision, "route": "case_6_renew_both", "started_on": "2026-10-07T09:00:00", **extra}
        if result:
            record.update({"result": result, "completed_on": "2026-10-07T09:10:00"})
        if uncertain:
            record["uncertain"] = uncertain
        return {"CO-0702": record}

    def card(self, base=RENEWAL, **kwargs):
        return do_now(render(row_for(base), **kwargs))

    def test_start_form_is_modelled_on_start_onboarding(self):
        card = self.card()
        self.assertIn("Start renewal", card)
        self.assertIn("<form class='start-form' method='post' action='/attended/start-renewal'>", card)
        self.assertIn("name='reference' value='CO-0702'", card)
        self.assertIn("name='source_revision' value='" + self.REV + "'", card)
        self.assertRegex(card, r"name='nonce' value='[A-Za-z0-9_-]{20,}'")
        self.assertIn("name='plan_reviewed' value='1' required", card)
        self.assertIn("name='attended_renewal_authorized' value='1' required", card)
        self.assertEqual(card.count("<button"), 1)
        self.assertNotIn("--mirror-renewal", card)  # the old CLI step text is gone
        self.assertNotIn("(CLI)", card)

    def test_plan_summary_names_every_decided_rule(self):
        card = self.card()
        for text in ("Will be created from the production clone", "read from the mirror", "2029-10-26 (DealHub term end)",
                     "Start date (Q2):</b> Unchanged", "Number of domains:</b> Kept", "Added subdomains (Q3)",
                     "production duplicate gate", "Leonardo Development only", "production BackOffice is never touched"):
            self.assertIn(text, card)
        mirrored = self.card(outcomes=self.outcome("renewal_dry_run_planned"), **self.MIRRORED)
        self.assertIn("Exists (verified)", mirrored)
        self.assertIn("2026-10-26 → 2029-10-26 (DealHub term end)", mirrored)
        self.assertNotIn("Will be created", mirrored)

    def test_form_for_cases_4_5_and_6_only(self):
        case5 = {"Onboarding_Product__c": "Surface & Credential Exposure",
                 "Onboarding_Type__c": "Renewal of Credential Exposure Module + New Surface Product"}
        for base in (RENEWAL, CASE4, case5):
            with self.subTest(type=base["Onboarding_Type__c"]):
                self.assertIn("action='/attended/start-renewal'", self.card(base))
        for product in ("Surface", "Credential Exposure"):
            with self.subTest(single=product):
                page = render(row_for({"Onboarding_Product__c": product, "Onboarding_Type__c": "Renewal of Existing Product"}))
                self.assertNotIn("start-renewal", page)
                self.assertIn("No automated route for this renewal", do_now(page))

    def test_no_form_until_approved(self):
        page = render(row_for(RENEWAL, Onboarding_Approval_Status__c="Pending"))
        self.assertNotIn("start-renewal", page)
        self.assertIn("Waiting for approval in Salesforce", do_now(page))

    def test_running_failed_uncertain_and_done_states(self):
        card = self.card(runner=self.run_record())
        self.assertIn("Renewal is running", card)
        self.assertIn("View progress", card)
        self.assertNotIn("start-renewal", card)
        card = self.card(runner=self.run_record("renewal_domains_mismatch_manual_review"))
        self.assertIn("The last renewal run stopped", card)
        self.assertIn("Salesforce domains differ from the domains on the tenant", card)
        self.assertIn("action='/attended/reset-ce-only-runner'", card)
        self.assertNotIn("start-renewal", card)
        card = self.card(runner=self.run_record("renewal_save_no_signal", uncertain="renewal_write"))
        self.assertIn("Verify the Dev tenant in Leonardo", card)
        self.assertIn("action='/attended/verify-uncertain'", card)
        self.assertNotIn("reset-ce-only-runner", card)
        self.assertNotIn("start-renewal", card)
        card = self.card(runner=self.run_record("mirror_create_unverified", uncertain="mirror_create"))
        self.assertIn("Dev mirror uncertain", card)
        self.assertNotIn("verify-uncertain", card)
        card = self.card(runner=self.run_record("renewal_edit_verified"))
        self.assertIn("Renewal is current in Leonardo Development", card)
        self.assertIn("Renewal applied (Dev)", card)
        self.assertNotIn("start-renewal", card)

    def test_a_run_of_another_revision_shows_the_form_again(self):
        card = self.card(runner=self.run_record("renewal_edit_verified", revision="older"))
        self.assertIn("action='/attended/start-renewal'", card)
        self.assertIn("different source revision", card)
        card = self.card(runner=self.run_record(revision="older"))  # still running: no second start
        self.assertNotIn("start-renewal", card)
        self.assertIn("has not reported a result", card)

    def test_plan_blockers_keep_the_form_away(self):
        rows = [dict(SUBSCRIPTIONS[0]), dict(SUBSCRIPTIONS[1], DealHub_Subscription_End_Date__c="2028-10-26")]
        card = do_now(render(row_for(RENEWAL), rows=rows))
        self.assertIn("one tenant, one licence term", card)
        self.assertIn("Resolve the blockers, then start", card)
        self.assertNotIn("<form", card)

    def test_last_stop_reasons_are_notes_and_do_not_hide_the_form(self):
        for result, text in (("renewal_expiration_would_shorten", "would shorten"),
                             ("renewal_new_domain_in_production", "Domain gate (Q3)"),
                             ("renewal_domains_mismatch_manual_review", "Salesforce domains differ")):
            with self.subTest(result=result):
                card = self.card(outcomes=self.outcome(result), **self.MIRRORED)
                self.assertIn(text, card)
                self.assertIn("action='/attended/start-renewal'", card)

    def test_cli_outcomes_without_a_run_record_keep_their_meaning(self):
        card = self.card(outcomes=self.outcome("renewal_edit_verified", "confirm_write", "verified"), **self.MIRRORED)
        self.assertIn("Renewal is current in Leonardo Development", card)
        self.assertNotIn("start-renewal", card)
        card = self.card(outcomes=self.outcome("renewal_saved_unverified", "confirm_write", "attempted_unverified"), **self.MIRRORED)
        self.assertIn("Verify the Dev tenant in Leonardo before any retry", card)
        self.assertNotIn("start-renewal", card)

    def test_diagnostics_keeps_a_one_line_cli_hint(self):
        page = render(row_for(RENEWAL))
        self.assertIn("--co CO-0702 --renew</code> (dry run)", fold(page, "Diagnostics"))
        self.assertNotIn("--co CO-0702 --renew", page[:page.index("<h2 class='sum-h'>Diagnostics</h2>")])

    def test_five_line_summary_and_full_plan_in_a_fold(self):
        page = render(row_for(RENEWAL))
        card = do_now(page)
        for label in ("Term", "Expiry", "Apply from", "Blockers", "Domain gate (Q3)"):
            self.assertIn("<dt>" + label + "</dt>", card)
        self.assertEqual(card.count("<dt>"), 5)
        self.assertIn("Renewal plan · Case 6", fold(page, "Run history"))
        self.assertNotIn("Renewal plan · Case 6", page[:page.index("<h2 class='sum-h'>Run history</h2>")])
        self.assertNotIn("applied by CLI", page)
        self.assertNotIn("Applied only by the CLI", page)

    def test_renewal_facts_include_the_run_state(self):
        page = render(row_for(RENEWAL), outcomes=self.outcome("renewal_dry_run_planned"))
        facts = key_facts(page)
        for label in ("Existing tenant", "Expiry", "Start date", "Apply from", "Added domains · Q3 gate", "Domains check",
                      "Last renewal run", "Dev mirror"):
            self.assertIn("<dt>" + label + "</dt>", facts)


class TenantIdsBlockTests(unittest.TestCase):
    """Owner decision 2026-10-07: Account ID / Account UUID per product, FULL, copyable, labelled with the environment."""
    FULL_ID, FULL_UUID = READBACK["surface_account_id"], READBACK["account_uuid"]

    def block(self, base, **kwargs):
        page = render(row_for(base), **kwargs)
        start = page.find("<section class='keyfacts idbar'")
        return "" if start == -1 else page[start:page.index("</section>", start)]

    def code(self, value):
        return "<code style='user-select:all'>" + value + "</code>"

    def test_credential_exposure_shows_only_the_account_uuid(self):
        run = {"CO-0702": {**VERIFIED_RUN["CO-0702"], "route": dashboard.CE_ENGINE}}
        block = self.block(CE, readback=READBACK, runner=run, ce_evaluation=ce_evaluation())
        self.assertIn("<dt>Account UUID</dt>", block)
        self.assertIn(self.code(self.FULL_UUID), block)
        self.assertNotIn("Account ID", block)
        self.assertIn("Leonardo Development", block)

    def test_surface_shows_only_the_account_id(self):
        block = self.block(SURFACE, readback=READBACK, runner=VERIFIED_RUN)
        self.assertIn("<dt>Account ID (Surface Account ID)</dt>", block)
        self.assertIn(self.code(self.FULL_ID), block)
        self.assertNotIn("Account UUID", block)

    def test_case3_and_renewals_show_both_in_full(self):
        run = {"CO-0702": {**VERIFIED_RUN["CO-0702"], "route": dashboard.CASE3_ENGINE}}
        cases = [(CASE3, dict(readback=READBACK, runner=run,
                              evaluation=surface_evaluation(route=dashboard.CASE3_ENGINE, core_plus=True))),
                 (RENEWAL, dict(readback=READBACK, mirrors=frozenset({self.FULL_ID}))), (CASE4, dict(readback=READBACK))]
        for base, kwargs in cases:
            with self.subTest(type=base["Onboarding_Type__c"]):
                block = self.block(base, **kwargs)
                self.assertIn(self.code(self.FULL_ID), block)
                self.assertIn(self.code(self.FULL_UUID), block)
                self.assertIn("Leonardo Development", block)

    def test_renewal_block_marks_the_dev_mirror(self):
        block = self.block(RENEWAL, readback=READBACK, mirrors=frozenset({self.FULL_ID}))
        self.assertIn("DEV mirror", block)

    def test_missing_values_say_not_captured(self):
        block = self.block(CASE3, evaluation=surface_evaluation(route=dashboard.CASE3_ENGINE, core_plus=True))
        self.assertEqual(block.count("Not captured"), 2)
        self.assertEqual(self.block(RENEWAL).count("Not captured"), 2)

    def test_values_are_escaped_and_the_short_id_is_gone(self):
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN)
        self.assertNotIn("AAAAAAAA…", page)
        self.assertNotIn("Tenant id (first 8 characters)", page)
        evil = dashboard._tenant_ids_block(SimpleNamespace(
            id_route=dashboard.SURFACE_ENGINE, readback={"surface_account_id": "<script>x</script>"}, mirror=False))
        self.assertNotIn("<script>", evil)
        self.assertIn("&lt;script&gt;", evil)

    def test_unmapped_route_shows_nothing(self):
        self.assertEqual(self.block({"Onboarding_Product__c": "Surface", "Onboarding_Type__c": "Something else"}), "")

    def test_renewal_engines_map_both_fields_and_the_ids_fold_no_longer_says_undecided(self):
        for engine in sorted(dashboard.RENEWAL_ENGINES):
            self.assertEqual([key for key, _f, _l in dashboard.SALESFORCE_ID_MAPPING[engine]],
                             ["surface_account_id", "account_uuid"])
        page = render(row_for(RENEWAL), readback=READBACK, mirrors=frozenset({self.FULL_ID}))
        self.assertNotIn("Mapping not decided", page)
        self.assertIn("Salesforce IDs", page)
        self.assertFalse(dashboard.ID_WRITEBACK_ENABLED)

    def test_salesforce_write_stays_off_for_renewals(self):
        row = {"Surface_Account_ID__c": None, "Account_UUID__c": None}
        plan = dashboard.salesforce_id_writeback_plan(dashboard.route_for(RENEWAL), READBACK, row)
        self.assertEqual(plan["status"], "mapping_not_decided")  # route_for stays create-only: no write path for renewals


class KeyFactsTests(unittest.TestCase):
    def test_key_facts_never_exceed_the_cap_in_any_route_or_stage(self):
        tenant_done = {"operator_assigned": True, "primary_user": {"present": True}}
        validation = {"CO-0702": {"primary_user_matches": True, "primary_user_checked_on": "2026-10-06", "plan_note": "",
                                  "checks": [], "observed_at": NOW, "expires_at": NOW}}
        spycloud = {"CO-0702": {"outcome": "spycloud_off_verified", "mode": "standalone", "observed_at": NOW, "ok": True}}
        scan = {"CO-0702": {"state": "scan_completed", "status_enum": "COMPLETED", "last_recon_scan": None,
                            "duration_ms": None, "observed_at": NOW, "expires_at": NOW, "execution_state": "done", "executions": []}}
        cases = []
        for base, route, evaluation in ((SURFACE, dashboard.SURFACE_ENGINE, surface_evaluation()),
                                        (CASE3, dashboard.CASE3_ENGINE, surface_evaluation(route=dashboard.CASE3_ENGINE, core_plus=True))):
            run = {"CO-0702": {**VERIFIED_RUN["CO-0702"], "route": route}}
            cases += [(base, dict(evaluation=evaluation)),
                      (base, dict(evaluation=evaluation, readback=READBACK, runner=run)),
                      (base, dict(evaluation=evaluation, readback=READBACK, runner=run, scan=scan, tenant=tenant_done,
                                  validation=validation, spycloud=spycloud))]
        ce_run = {"CO-0702": {**VERIFIED_RUN["CO-0702"], "route": dashboard.CE_ENGINE}}
        cases += [(CE, dict(ce_evaluation=ce_evaluation())),
                  (CE, dict(ce_evaluation=ce_evaluation(), readback=READBACK, runner=ce_run, tenant=tenant_done,
                            validation=validation, spycloud=spycloud)),
                  (RENEWAL, {}), (CASE4, {}),
                  (RENEWAL, dict(readback=READBACK, mirrors=frozenset({READBACK["surface_account_id"]}))),
                  (SURFACE, dict(commercial=None))]
        for index, (base, kwargs) in enumerate(cases):
            with self.subTest(case=index, product=base["Onboarding_Product__c"], type=base["Onboarding_Type__c"]):
                page = render(row_for(base), **kwargs)
                count = fact_count(page)
                self.assertGreaterEqual(count, 2)
                self.assertLessEqual(count, dashboard.KEY_FACTS_MAX)
        self.assertEqual(dashboard.KEY_FACTS_MAX, 10)
        many = dashboard._key_facts_grid([(str(n), "x") for n in range(25)])
        self.assertEqual(many.count("<dt>"), 10)

    def test_surface_before_start_facts(self):
        page = render(row_for(SURFACE, Alternate_Domains__c="a.example, b.example"))
        facts = key_facts(page)
        for label in ("Product", "Salesforce", "Main domain", "Alternate · sub", "Licence plan", "Tier · interval",
                      "Duplicate check", "Onboarding day", "Scope flags"):
            self.assertIn("<dt>" + label + "</dt>", facts)
        self.assertIn("Prime · Weekly · 1400 licensed", facts)

    def test_case3_adds_email_domains_and_leaked_credentials_interval(self):
        page = render(row_for(CASE3), evaluation=surface_evaluation(route=dashboard.CASE3_ENGINE))
        self.assertIn("<dt>Email domains</dt><dd>1 · LC Weekly</dd>", key_facts(page))

    def test_case2_before_start_has_email_domains_and_duplicate_state_only(self):
        page = render(row_for(CE), ce_evaluation=ce_evaluation())
        facts = key_facts(page)
        for label in ("Main domain", "Email domains", "Duplicate check"):
            self.assertIn("<dt>" + label + "</dt>", facts)
        self.assertNotIn("Licence plan", facts)

    def test_after_create_facts_by_route(self):
        scan = {"CO-0702": {"state": "scan_started", "status_enum": "RUNNING", "last_recon_scan": None, "duration_ms": None,
                            "observed_at": NOW, "expires_at": NOW}}
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN, scan=scan)
        facts = key_facts(page)
        for label in ("Licence", "Validation", "Scan", "Operator", "User created"):
            self.assertIn("<dt>" + label + "</dt>", facts)
        self.assertNotIn("<dt>Tenant</dt>", facts)  # the short tenant id was replaced by the full ID block (2026-10-07)
        self.assertNotIn("<dt>SpyCloud</dt>", facts)
        self.assertIn("2026-10-06 → 2027-10-05", facts)
        ce_run = {"CO-0702": {**VERIFIED_RUN["CO-0702"], "route": dashboard.CE_ENGINE}}
        facts = key_facts(render(row_for(CE), readback=READBACK, runner=ce_run, ce_evaluation=ce_evaluation()))
        self.assertIn("<dt>SpyCloud</dt>", facts)
        self.assertNotIn("<dt>Scan</dt>", facts)  # Case 2 has no scan


class FoldAndFooterTests(unittest.TestCase):
    def test_diagnostics_holds_the_one_off_repair_actions_and_the_source_revision(self):
        row = row_for(CASE4, Name="CO-0741", Onboarding_Comments__c=None, Onboarding_Stage__c="New",
                      Onboarding_Approval_Status__c="Pending")
        page = render(row, reference="CO-0741")
        diagnostics = fold(page, "Diagnostics")
        self.assertIn("action='/attended/rerun-comment-evaluation'", diagnostics)
        self.assertNotIn("rerun-comment-evaluation", page[:page.index("<h2 class='sum-h'>Diagnostics</h2>")])
        row = row_for(CASE4, Name="CO-0745", Onboarding_Comments__c=None, Onboarding_Stage__c="New",
                      Onboarding_Approval_Status__c="Pending")
        page = render(row, reference="CO-0745")
        self.assertIn("action='/attended/rerun-co0745-renewal-evaluation'", fold(page, "Diagnostics"))
        self.assertIn("action='/attended/production-renewal-preflight'", fold(page, "Diagnostics"))
        # The Start section of an automated route keeps its source revision in Diagnostics.
        page = render(row_for(SURFACE))
        self.assertIn("Source revision <code>rev1</code>", fold(page, "Diagnostics"))

    def test_duplicate_reset_form_is_in_diagnostics(self):
        dup = {"CO-0702": {"source_revision": "rev1", "route": dashboard.SURFACE_ENGINE, "result": "duplicate_found",
                           "completed_on": "2026-10-06T10:00:00"}}
        page = render(row_for(SURFACE), runner=dup)
        self.assertIn("action='/attended/reset-ce-only-runner'", fold(page, "Diagnostics"))
        self.assertNotIn("reset-ce-only-runner", do_now(page))

    def test_tenant_checks_and_salesforce_record_folds(self):
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN)
        checks = fold(page, "Tenant checks")
        for text in ("Surface validation", "Leonardo scan status", "Leonardo Development readback"):
            self.assertIn(text, checks)
        self.assertNotIn("<dt>Environment</dt>", checks)
        record = fold(page, "Salesforce record")
        for text in ("<dt>Onboarding Approval Status</dt>", "Domains for the tenant", "Salesforce IDs · "):
            self.assertIn(text, record)

    def test_run_history_holds_the_outcome_and_the_licence_dates_entered(self):
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN)
        history = fold(page, "Run history")
        self.assertIn("Licence dates entered in Leonardo Development: <b>2026-10-06 → 2027-10-05</b>", history)
        self.assertIn("readback_verified", history)

    def test_one_footer_line_and_no_repeated_notes(self):
        page = render(row_for(SURFACE), readback=READBACK, runner=VERIFIED_RUN)
        self.assertEqual(page.count("class='footline'"), 1)
        self.assertEqual(page.count(dashboard.DETAIL_FOOTER_NOTE), 1)
        self.assertEqual(dashboard.DETAIL_FOOTER_NOTE,
                         "Read-only view from Salesforce and local Leonardo Development evidence; nothing here writes to "
                         "Salesforce except the explicit confirmation forms.")
        self.assertTrue(page.index("class='footline'") > page.index("<h2 class='sum-h'>Salesforce record</h2>"))
        self.assertEqual(len(re.findall(r"class='footline'", page)), 1)
        self.assertNotIn("Read-only preview from Salesforce; nothing is written back.", page)


if __name__ == "__main__":
    unittest.main()
