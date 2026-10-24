"""Owner feedback 2026-10-18 (first production CO-0798 create): the production CO page is production/BackOffice first and
every panel shows the SAME create-gate result.

Before: the readiness list, the lock banner, the old Create card and the header chip judged the lock from the process
environment only (``session_readiness.production_unlock_problem(os.environ)``), which never holds the approval values the
real gate (``production_create_ui.gate``) merges from the approval record (``production_approval.unlock_environ``). So
they said "Locked - No valid production approval is configured" while the real gate rendered the Create button.

Also: the Sessions page shows a read-only BackOffice production row derived only from the runner's own create records, and
labels Leonardo as Dev. Hermetic: nothing is started, read or written outside a temporary folder.
"""
from __future__ import annotations

from datetime import timedelta
from html import escape
import re
import unittest
from unittest.mock import patch

from integration.onboarding import production_approval as pa
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_create_ui as create_ui
from integration.onboarding import production_views as views
from integration.onboarding import session_readiness as readiness
from integration.tests import test_production_first_dashboard as base
import tools.serve_attended_open_onboardings_dashboard as dashboard

REF = "CO-0780"
REV = "2026-10-16T08:00:00.000+0000"
OWNER = "owner@example.test"
ROW = {"Name": REF, "Account_Name__c": "State Land Service", "Account_UUID__c": None, "LastModifiedDate": REV,
       "Onboarding_Product__c": dashboard.CE_ROUTE_PRODUCT, "Onboarding_Type__c": dashboard.CE_ROUTE_TYPE,
       "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "New", "Email_Domains__c": "@vzd.example"}
PASSED = (None, {"approval": {"approval_ref": "PROD-CE-0780-1"}, "status": None, "env": None})
CONTRADICTIONS = ("Production create is locked", "No valid production approval is configured",
                  "not supported in this build", "Production sessions stay locked", "Create locked",
                  "Production create stays locked", "Runner unlocked by configuration")
DEV_TEXT = ("Leonardo", "Review &amp; start", "(Dev)", "(test)", "Dev start")
unlocked_build = base.unlocked_build
locked_create_build = base.locked_create_build


def card(page, title_id):
    start = page.index("aria-labelledby='" + title_id + "'")
    return page[start:page.index("</section>", start)]


def chips_of(html):
    return re.findall(r"<span class='chip [^']*'>([^<]*)</span>", html)


@unlocked_build
class PageStateTests(unittest.TestCase):
    """``create_ui.page_state``: one gate result, judged with the approval record's unlock values."""

    def setUp(self):
        self.env = base.Env()
        self.addCleanup(self.env.tmp.cleanup)
        pa.issue(self.env.approvals, "PROD-CE-0780-1", REF, pa.ALLOWED_ROUTE, REV, OWNER, frozenset({OWNER}),
                 base.NOW - timedelta(hours=1))

    def inputs(self, **over):
        data = dict(reference=REF, row=dict(ROW), now=base.NOW, environ={create_ui.SESSIONS_ENV: "1"}, operator=OWNER,
                    vm=False, approvals_path=self.env.approvals, records_parent=self.env.parent, clone=None, sf_ready=True,
                    row_fresh=True)
        data.update(over)
        return create_ui.Inputs(**data)

    def test_the_approval_record_unlocks_the_session_even_though_the_process_environment_has_no_approval(self):
        environ = {create_ui.SESSIONS_ENV: "1"}
        # The old panels asked this question and got "locked: no valid production approval is configured" ...
        self.assertEqual(readiness.production_unlock_problem(environ, base.NOW), "production_approval_missing")
        # ... while the real gate passes the session unlock (it stops later, at the stale clone).
        state = create_ui.page_state(self.inputs(environ=environ))
        self.assertEqual((state["phase"], state["reason"]), ("blocked", "clone_stale"))
        self.assertEqual(state["text"], create_ui.gate_text("clone_stale"))

    def test_phases(self):
        with patch.object(create_ui, "gate", return_value=PASSED):
            self.assertEqual(create_ui.page_state(self.inputs())["phase"], "ready")
        self.assertEqual(create_ui.page_state(self.inputs(vm=True))["phase"], "locked")
        with patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", False):
            state = create_ui.page_state(self.inputs())
        self.assertEqual((state["phase"], state["reason"]), ("locked", "production_runner_not_supported"))
        with patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False):
            self.assertEqual(create_ui.page_state(self.inputs())["phase"], "locked")
        self.assertEqual(create_ui.page_state(self.inputs(operator="someone@else.test"))["reason"], "operator_not_owner")

    def test_a_gate_crash_fails_closed(self):
        with patch.object(create_ui, "gate", side_effect=RuntimeError("boom")):
            state = create_ui.page_state(self.inputs())
        self.assertEqual((state["phase"], state["reason"]), ("blocked", "gate_unreadable"))
        self.assertNotIn("boom", state["text"])

    def test_build_lock_is_the_gate_s_own_first_refusals(self):
        self.assertEqual(create_ui.build_lock(True), "vm_read_only")
        with patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", False):
            self.assertEqual(create_ui.build_lock(False), "production_runner_not_supported")
        with patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False):
            self.assertEqual(create_ui.build_lock(False), "production_dry_run_not_supported")


@unlocked_build
class ReviewPageTests(unittest.TestCase):
    """``/onboard/CO-x``: production first, no Dev section, no duplicated sheet, one gate result everywhere."""

    def setUp(self):
        self.env = base.Env()
        self.addCleanup(self.env.tmp.cleanup)

    def review(self, gate=None, vm=False):
        with base.patched(self.env, unavailable=vm), patch.object(dashboard, "vm_mode", return_value=vm), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(create_ui, "TOKENS", create_ui.OneTimeTokens()):
            if gate is None:
                return dashboard.page_onboard_review(base.summary_for(), base.EXTRAS, "", row=dict(ROW))
            with patch.object(create_ui, "gate", return_value=gate):
                return dashboard.page_onboard_review(base.summary_for(), base.EXTRAS, "", row=dict(ROW))

    @staticmethod
    def main(page):
        return page[page.index("<main"):]

    def test_gate_passed_every_panel_agrees_and_the_create_button_shows(self):
        main = self.main(self.review(PASSED))
        self.assertIn("<button type='submit'>Onboard in production</button>", main)
        for text in CONTRADICTIONS:
            self.assertNotIn(text, main, text)
        readiness_card = card(main, "co-ready-title")
        self.assertNotIn("Locked", chips_of(readiness_card))
        for label in ("Production runner unlocked", "Production session", "Owner go"):
            item = readiness_card[readiness_card.index(label):]
            self.assertEqual(chips_of(item)[0], "Ready", label)
        self.assertIn("Create ready", chips_of(main[:main.index("</div>", main.index("page-head"))]))

    def test_an_approved_co_with_no_approval_yet_reads_ready_for_the_one_click(self):
        # Owner decision 2026-10-23: the missing approval is recorded by the Onboard in production click, not a blocker.
        main = self.main(self.review(("approval_missing", {"approval": None, "status": None, "env": None})))
        self.assertIn("Onboard in production</button>", main)
        for text in CONTRADICTIONS:
            self.assertNotIn(text, main, text)
        readiness_card = card(main, "co-ready-title")
        unlock = readiness_card[readiness_card.index("Production runner unlocked"):]
        self.assertEqual(chips_of(unlock)[0], "Ready")
        owner = readiness_card[readiness_card.index("Owner go"):]
        self.assertEqual(chips_of(owner)[0], "Ready")
        self.assertNotIn("Dry run", main)
        self.assertIn("Create ready", main)

    def test_the_real_gate_without_an_approval_in_the_process_environment_is_not_called_locked(self):
        # No patched gate: the real one runs (no approval file, so it stops at approval_missing).
        main = self.main(self.review())
        for text in CONTRADICTIONS:
            self.assertNotIn(text, main, text)
        self.assertIn("Onboard in production</button>", main)

    def test_locked_build_says_locked_everywhere_with_the_gate_reason(self):
        with locked_create_build:   # a context manager: it must win over the class-level unlocked_build
            main = self.main(self.review())
        create = card(main, "co-create-title")
        self.assertIn("Locked", chips_of(create))
        self.assertIn(create_ui.gate_text("production_runner_not_supported"), create)
        readiness_card = card(main, "co-ready-title")
        unlock = readiness_card[readiness_card.index("Production runner unlocked"):]
        self.assertEqual(chips_of(unlock)[0], "Locked")
        self.assertIn("Create locked", main)
        self.assertNotIn("<form method='post' action='" + create_ui.CREATE_PATH, main)

    def test_no_dev_or_leonardo_content_on_desktop_or_vm(self):
        for vm in (False, True):
            main = self.main(self.review(PASSED if not vm else None, vm=vm))
            for text in DEV_TEXT:
                self.assertNotIn(text, main, (vm, text))

    def test_no_duplicated_sheet_banner_or_create_cards(self):
        main = self.main(self.review(PASSED))
        self.assertNotIn("Production onboarding sheet", main)
        self.assertNotIn("Production BackOffice</h3>", main)
        self.assertEqual(main.count("aria-labelledby='co-create-title'"), 1)
        self.assertEqual(main.count("aria-labelledby='co-ready-title'"), 1)
        self.assertEqual(main.count("class='pill'>Create</h2>"), 0)          # the old second Create card
        self.assertNotIn("UUID writeback to Salesforce</h2>", main)        # the create card owns the UUID step
        self.assertLessEqual(main.count("Before you start"), 0)            # the readiness card is the one checklist
        self.assertEqual(main.count("Add Account values"), 1)

    def test_production_sections_come_first_in_order(self):
        main = self.main(self.review(PASSED))
        marks = ["<h1>" + REF, "co-create-title", "co-ready-title", "Salesforce facts", "Production checks", "Add Account values",
                 "Approval</h2>", "D2 record"]
        positions = [main.index(mark) for mark in marks]
        self.assertEqual(positions, sorted(positions))

    def test_vm_keeps_the_desktop_address_and_never_a_control(self):
        main = self.main(self.review(vm=True))
        self.assertIn(dashboard.DESKTOP_DASHBOARD_ADDRESS, main)
        self.assertIn(escape(create_ui.gate_text("vm_read_only")), main)
        self.assertNotIn("<form method='post' action='" + create_ui.CREATE_PATH, main)


@unlocked_build
class ProductionViewClosingTests(unittest.TestCase):
    """``/co/CO-x?env=prod``: the closing line follows the same gate, never "locked" while the build is unlocked."""

    def setUp(self):
        self.env = base.Env()
        self.addCleanup(self.env.tmp.cleanup)

    def page(self, vm=False):
        with base.patched(self.env, unavailable=vm), patch.object(dashboard, "vm_mode", return_value=vm), \
                patch.object(dashboard, "load_check_state", return_value={}), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            return dashboard.page_detail_production(REF, dict(ROW), base.match(), country="Latvia")

    def test_unlocked_build_does_not_claim_a_lock(self):
        page = self.page()
        self.assertNotIn(views.GUIDE_CLOSING, page)
        self.assertIn(dashboard.PROD_VIEW_UNLOCKED_CLOSING, page)

    def test_locked_build_keeps_the_guidance_only_line(self):
        with locked_create_build:   # a context manager: it must win over the class-level unlocked_build
            self.assertIn(views.GUIDE_CLOSING, self.page())

    def test_vm_names_the_desktop(self):
        self.assertIn(escape(create_ui.gate_text("vm_read_only")), self.page(vm=True))


class BackOfficeSessionStatusTests(unittest.TestCase):
    """``production_views.backoffice_session_status``: from the runner's own records only (newest across COs)."""

    def setUp(self):
        self.env = base.Env()
        self.addCleanup(self.env.tmp.cleanup)

    def status(self, **kwargs):
        return views.backoffice_session_status(self.env.parent, **kwargs)

    def write(self, outcome, moment, co="CO-0798", **extra):
        self.env.write_record({"co_reference": co, "outcome": outcome, "route": "case_2_new_ce_only", **extra}, moment)

    def test_no_records(self):
        got = self.status()
        self.assertEqual(got["state"], "none")
        self.assertEqual(self.status(missing_is_unavailable=True)["state"], "unavailable")

    def test_a_run_that_reached_the_form_was_signed_in(self):
        self.write("dry_run_passed", base.NOW - timedelta(hours=3))
        got = self.status()
        self.assertEqual(got["state"], "signed_in")
        self.assertIn("CO-0798", got["detail"])
        self.assertIn("2026-10-16 09:00 UTC", got["detail"])

    def test_the_newest_record_across_cos_decides(self):
        self.write("readback_verified", base.NOW - timedelta(hours=3), co="CO-0798")
        self.write("refused", base.NOW - timedelta(hours=1), co="CO-0700", refusal_reason="production_session_expired")
        self.assertEqual(self.status()["state"], "sign_in_failed")
        self.write("refused", base.NOW, co="CO-0701", refusal_reason="production_dry_run_login_timeout")
        self.assertEqual(self.status()["state"], "sign_in_failed")

    def test_other_refusals_do_not_confirm_a_sign_in(self):
        self.write("refused", base.NOW, refusal_reason="clone_stale")
        self.assertEqual(self.status()["state"], "not_confirmed")

    def test_unreadable_records(self):
        folder = self.env.parent / pcr.RECORDS_SUBDIR
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "CO-0798-20261016T120000Z.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.status()["state"], "unreadable")

    def test_every_state_has_a_chip_and_no_secret_words(self):
        for state in ("signed_in", "sign_in_failed", "not_confirmed", "none", "unavailable", "unreadable"):
            css, label = views.BACKOFFICE_SESSION_CHIPS[state]
            self.assertTrue(css.startswith("chip-") and label)
        texts = [views.BACKOFFICE_SESSION_NOTE] + [label for _css, label in views.BACKOFFICE_SESSION_CHIPS.values()]
        for text in texts:
            for word in ("token", "password"):
                self.assertNotIn(word, text.lower())


@unlocked_build
class SessionsPageTests(unittest.TestCase):
    """``/connection``: a read-only BackOffice production row; Leonardo labelled Dev."""

    def setUp(self):
        self.env = base.Env()
        self.addCleanup(self.env.tmp.cleanup)

    def page(self):
        with patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "production_records_parent", return_value=self.env.parent), \
                patch.object(dashboard, "_state_not_published", return_value=False):
            return dashboard.page_salesforce_unavailable(failed=False)

    def test_backoffice_production_row_from_the_runner_records(self):
        page = self.page()
        self.assertIn("<th>BackOffice production</th>", page)
        self.assertIn(views.BACKOFFICE_SESSION_CHIPS["none"][1], page)
        self.env.write_record({"co_reference": "CO-0798", "outcome": "readback_verified", "route": "case_2_new_ce_only"},
                              base.NOW)
        page = self.page()
        self.assertIn(views.BACKOFFICE_SESSION_CHIPS["signed_in"][1], page)
        self.assertIn("app.pentera.io", page)

    def test_backoffice_row_comes_first_and_there_is_no_leonardo_row(self):
        page = self.page()
        self.assertNotIn("Leonardo", page)  # owner decision 2026-10-19
        self.assertLess(page.index("<th>BackOffice production</th>"), page.index("<th>Salesforce</th>"))

    def test_the_backoffice_row_has_no_control_and_no_secret_words(self):
        page = self.page()
        row = page[page.index("<th>BackOffice production</th>"):]
        row = row[:row.index("</tr>")]
        for tag in ("<form", "<button", "<input", "<a "):
            self.assertNotIn(tag, row)
        self.assertNotIn("value='backoffice_production'", page)   # production still cannot be prepared from here
        self.assertNotIn("token", page.lower())
        self.assertNotIn("password", page.lower())

    def test_the_environment_chip_follows_the_build_lock_not_the_process_environment(self):
        self.assertNotIn("BackOffice production locked", self.page())
        with locked_create_build:
            self.assertIn("BackOffice production locked", self.page())


if __name__ == "__main__":
    unittest.main()
