"""End-to-end tests of the guarded LIVE production create (docs/53) against a fake page: every gate refusal, the live
duplicate pre-check (name, domain, demo domain, lookup error, a duplicate that appears late), exactly one Confirm click,
no second click after any outcome, the unknown-outcome state, readback mismatch, the UUID never printed, both locks False
means nothing is reachable, the approval is consumed once and revision drift refuses.
No browser, network, Salesforce, Redash or BackOffice: every external step is a fake."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from integration.onboarding import production_approval as approval
from integration.onboarding import production_contract as contract
from integration.onboarding import production_create_live as live
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_create_ui as ui
from integration.onboarding import production_dry_run as pdr
from integration.onboarding import production_launch_ticket as ticket
from integration.onboarding import session_readiness
from integration.tests import test_production_dry_run as dry
from integration.tests.test_production_create_live import contract_row, reply
import tools.attended_ce_only_playwright as runner

CO, REF, REV, NOW, ORIGIN = dry.CO, dry.REF, dry.REV, dry.NOW, dry.ORIGIN
ROUTE = "case_2_new_ce_only"
UUID = "0123456789abcdef0123456789abcdef"
SOURCE = dry.make_source()
NAME, DOMAIN = SOURCE.tenant_name, SOURCE.email_domain
DAY = date(2026, 10, 16)


ORG_EMAIL = runner.ce_production_intended(SOURCE, DAY)["organization_email"]
CONTROL = {"name": "Control Holdings", "domain": "control-holdings.example"}


def control_row():
    return {"name": CONTROL["name"], "domain": CONTROL["domain"], "alternates": [], "emails": [], "deleted": False}


CONTROL_SEARCH = {CONTROL["name"]: reply([control_row()]), CONTROL["domain"]: reply([control_row()])}


def good_clone(source, now):
    return {**dry.good_clone(source, now), "control": dict(CONTROL)}


def good_row(**over):
    strict = {"accountLicense.startDate": "2026-10-16T09:00:00.000Z", "accountLicense.expirationDate": "2027-05-20T07:00:00.000Z",
              "primaryUser.isMfaRequired": True, "primaryUser.email": ORG_EMAIL,
              "campaignExecutionSettings.domainsMultiAttackStackSettings.enabled": False}
    strict.update(over)
    return contract_row(accountName=NAME, accountDomain=DOMAIN, **strict)


def good_create_body(**changes):
    """The docs/49 s.1 body for the runner's intended values (shape fields get a sample), as the page would send it."""
    expected = contract.expected_create_payload({**runner.ce_production_intended(SOURCE, DAY), "country_code": "CL"})
    samples = {"non_empty_text": "x", "iso2": "CL", "iso_utc": "2026-10-16T12:00:00.000Z", "number": 5}
    flat = {}
    for key, want in expected.items():
        if isinstance(want, contract._Shape):
            name = want.name
            want = f"{name[len('iso_utc_on_'):]}T07:00:00.000Z" if name.startswith("iso_utc_on_") else samples[name]
        flat[key] = want
    for key, value in changes.items():
        flat[key.replace("__", ".")] = value
    body = {}
    for key, value in flat.items():
        node = body
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return json.dumps(body)


class ConfirmBtn:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def is_visible(self):
        return True

    def is_enabled(self):
        return self.page.confirm_enabled

    def scroll_into_view_if_needed(self):
        self.page.scrolled += 1

    def evaluate(self, js):
        assert js == pdr.HIT_TEST_JS
        self.page.hit_calls += 1
        if self.page.hits:
            return self.page.hits.pop(0)
        return self.page.hit

    def click(self, **kw):
        assert "force" not in kw, "a forced click must never be made"
        self.page.confirm_clicks += 1
        for _ in range(self.page.posts):
            outcome = self.page.fire("POST", ORIGIN + live.CREATE_PATH, self.page.create_body())
            if outcome == "continued" and self.page.create_status is not None:
                for handler in self.page.response_handlers:
                    handler(SimpleNamespace(request=SimpleNamespace(method="POST"), url=ORIGIN + live.CREATE_PATH,
                                            status=self.page.create_status))
        self.page.form_open = False
        for _ in range(self.page.popups_after_click):
            for handler in self.page.popup_handlers:
                handler(None)
        self.page.sw_count += self.page.workers_after_click


class Flip(dict):
    """Search replies by value; a value listed in ``later`` answers ``later[value]`` from its second lookup on."""

    def __init__(self, first, later):
        super().__init__(first)
        self.later, self.calls = later, {}

    def get(self, key, default=None):
        self.calls[key] = self.calls.get(key, 0) + 1
        if key in self.later and self.calls[key] >= 2:
            return self.later[key]
        return super().get(key, default)


class LivePage(dry.FakePage):
    def __init__(self, *, create_status=200, readbacks=None, confirm_enabled=True, posts=1, hit=True, body="good", **kw):
        super().__init__(**kw)
        self.body = body
        self.create_status, self.confirm_enabled, self.posts, self.hit = create_status, confirm_enabled, posts, hit
        self.confirm_clicks = 0
        self.scrolled = self.hit_calls = 0
        self.hits = []
        self.extra_pages = []
        self.popups_after_click = 0
        self.workers_after_click = 0
        self.popup_on_readback = False
        self.context = SimpleNamespace(pages=[self], route=lambda pattern, handler: self.routes.append((pattern, handler)),
                                       on=self._context_on)
        self.country_value = "CL"
        country, original = self.selects["accountCountry"], self.selects["accountCountry"].evaluate
        country.evaluate = lambda js: self.country_value if "el.value" in str(js) else original(js)
        self.readbacks = list(readbacks if readbacks is not None else [reply([good_row()])])
        self.readback_calls = 0
        self.popup_handlers = []
        self.on_read = {}  # search value -> callable run once when that value is read with the form open

    def create_body(self):
        return good_create_body() if self.body == "good" else self.body

    def _context_on(self, event, handler):
        self.on("popup" if event == "page" else event, handler)

    def on(self, event, handler):
        super().on(event, handler)
        if event == "popup":
            self.popup_handlers.append(handler)

    @property
    def response_handlers(self):
        return self.handlers.setdefault("response", [])

    def get_by_role(self, role, name=None, exact=False):
        if role == "button" and name == "Confirm" and self.form_open:
            return ConfirmBtn(self)
        return super().get_by_role(role, name, exact)

    def _serve_table(self, value):
        if self.confirm_clicks:  # after the Confirm click every table read is the post-click readback (identical bodies)
            self.readback_calls += 1
            if self.popup_on_readback and self.readback_calls > 1:  # a page opens AFTER the click, once the poll has begun
                for handler in self.popup_handlers:
                    handler(None)
            return self.readbacks.pop(0) if self.readbacks else reply([])
        if self.form_open and value in self.on_read:  # something changes the open form while the live reads run
            self.on_read.pop(value)()
        return super()._serve_table(value)


class Harness(dry.Harness):
    def __init__(self, test, **page_kw):
        super().__init__(test)
        search = page_kw.pop("search", None)
        search = search if search is not None else {}
        for key, value in CONTROL_SEARCH.items():
            search.setdefault(key, value)
        self.page = LivePage(search=search, **page_kw)
        self.dry_record = True

    def seed_dry_run(self):
        pcr.record(self.records, {"co_reference": CO, "approval_ref": REF, "outcome": "dry_run_passed", "route": ROUTE,
                                  "salesforce_revision": REV}, NOW - timedelta(minutes=30))

    def mint(self, revision=REV, operator="milton", when=NOW):
        return ticket.mint(self.records, CO, revision, REF, operator, when)

    def load_source(self, ref):
        # Changes to the OPEN, verified form are simulated in the window between the fill and the Confirm click: the
        # Salesforce re-read runs there (no BackOffice read runs while the form is open, owner decision 2026-10-18).
        if self.page.form_open:
            for key in list(self.page.on_read):
                self.page.on_read.pop(key)()
        return super().load_source(ref)

    def go(self, *, runner_flag=True, dry_flag=True, go=True, revision=REV, clone=good_clone, source=None, dry_run_record=True,
           with_ticket=True, operator="milton", ticket_when=NOW, rows=None, clock=None, fill_date=None):
        if dry_run_record:
            self.seed_dry_run()
        for record in rows or ():
            pcr.record(self.records, {"co_reference": CO, "route": ROUTE, **record}, NOW)
        if with_ticket:
            self.mint(revision, operator, ticket_when)
        env = dict(dry.UNLOCK)
        out = io.StringIO()
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", runner_flag), \
                mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", dry_flag), contextlib.redirect_stdout(out):
            line = runner.run_production_create(
                CO, revision, owner_go=go, environ=env, now=NOW, records_parent=self.records,
                approval_path=self.approval_path, load_source=(lambda _r: source) if source else self.load_source,
                clone_facts=clone, open_page=self.open_page, fill_date=fill_date or (lambda *_a: True),
                date_kept=lambda *_a: True, run_day=DAY, ticket_parent=self.records, org_probe=dry.ORG_PROBE, clock=clock)
        self.stdout, self.line = out.getvalue(), line
        return json.loads(line)

    def outcomes(self):
        return [pcr.load(p)["outcome"] for p in pcr.list_records(self.records, CO)]

    def loaded_records(self):
        return [pcr.load(p) for p in pcr.list_records(self.records, CO)]

    def confirm_ever_clicked(self):
        return self.page.confirm_clicks


class Base(unittest.TestCase):
    def harness(self, **kw):
        return Harness(self, **kw)

    def no_external_activity(self, h):
        self.assertEqual((h.opened, h.loaded, h.page.confirm_clicks, h.page.fetches), (0, 0, 0, []))


class LockTests(Base):
    def test_constants_are_hard_coded_false(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # dry run unlocked by owner go 2026-10-18 (docs/53 step 3); the relock commit reverts this

    def test_both_locks_false_reaches_nothing(self):
        h = self.harness()
        result = h.go(runner_flag=False, dry_flag=False)
        self.assertEqual(result["result"], "production_locked_production_dry_run_not_supported")
        self.no_external_activity(h)
        self.assertEqual(h.outcomes(), ["dry_run_passed", "refused"])
        self.assertTrue(h.approval_unused() is None)

    def test_runner_lock_alone_reaches_nothing(self):
        h = self.harness()
        result = h.go(runner_flag=False, dry_flag=True)
        self.assertEqual(result["result"], "production_locked_production_runner_not_supported")
        self.no_external_activity(h)

    def test_dry_run_lock_alone_reaches_nothing(self):
        h = self.harness()
        self.assertEqual(h.go(runner_flag=True, dry_flag=False)["result"],
                         "production_locked_production_dry_run_not_supported")
        self.no_external_activity(h)

    def test_the_real_constants_refuse_without_any_patch(self):
        h = self.harness()
        h.seed_dry_run()
        out = runner.run_production_create(CO, REV, owner_go=True, environ=dict(dry.UNLOCK), now=NOW,
                                           records_parent=h.records, approval_path=h.approval_path,
                                           load_source=h.load_source, open_page=h.open_page)
        # dry run unlocked by owner go 2026-10-18 (docs/53 step 3): the create lock alone still refuses, before anything runs
        self.assertIn("production_locked_production_runner_not_supported", out)
        self.no_external_activity(h)

    def test_the_old_dry_run_stubs_still_refuse_to_confirm(self):
        for fn in (runner._pdr_click_confirm, runner._pdr_read_back):
            with self.assertRaises(NotImplementedError):
                fn(None)


class GateRefusalTests(Base):
    def test_owner_go_missing(self):
        for go in (False, None):
            h = self.harness()
            self.assertEqual(h.go(go=go)["result"], "owner_go_missing")
            self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
            self.assertIsNone(h.approval_unused())

    def test_arguments_and_revision_drift(self):
        h = self.harness()
        self.assertEqual(h.go(revision="bad revision!")["result"], "production_create_arguments_invalid")
        self.no_external_activity(h)
        h = self.harness()
        drifted = dry.make_source(source_revision="2026-10-17T00:00:00.000+0000")
        self.assertEqual(h.go(source=drifted)["result"], "approval_revision_drift")
        self.assertEqual((h.opened, h.page.confirm_clicks), (0, 0))
        self.assertIsNone(h.approval_unused())

    def test_a_dry_run_is_required(self):
        h = self.harness()
        self.assertEqual(h.go(dry_run_record=False)["result"], "dry_run_required")
        self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
        self.assertIsNone(h.approval_unused())

    def test_stale_or_missing_clone_wrong_co_and_other_gates_refuse_before_the_browser(self):
        cases = {
            "clone_stale": lambda s, n: {**good_clone(s, n), "collected_at": NOW - timedelta(hours=7)},
            "clone_missing": lambda s, n: {**good_clone(s, n), "collected_at": None},
            "duplicate_in_production": lambda s, n: {**good_clone(s, n), "duplicate_found": True},
            "multiple_live_paid_tenants": lambda s, n: {**good_clone(s, n), "live_paid_tenant_count": 2},
        }
        for reason, clone in cases.items():
            h = self.harness()
            self.assertEqual(h.go(clone=clone)["result"], reason)
            self.assertEqual((h.opened, h.page.confirm_clicks), (0, 0), reason)
            self.assertIsNone(h.approval_unused())

    def test_prior_unreviewed_attempt_blocks(self):
        for outcome in ("attempt_started", "uncertain_submit", "created_unverified", "readback_verified"):
            h = self.harness()
            h.seed_dry_run()
            pcr.record(h.records, {"co_reference": CO, "approval_ref": REF, "outcome": outcome, "route": ROUTE}, NOW)
            self.assertEqual(h.go(dry_run_record=False)["result"], "prior_attempt_needs_review", outcome)
            self.assertEqual((h.opened, h.page.confirm_clicks), (1, 0), outcome)

    def test_session_expiry_before_the_form(self):
        h = self.harness(session={pdr.AUTHORITIES_PATH: {"status": 401, "names": None},
                                  pdr.ACCOUNT_INFO_PATH: {"status": 200, "names": None}})
        self.assertEqual(h.go()["result"], "production_session_expired")
        self.assertEqual(h.page.confirm_clicks, 0)
        self.assertIsNone(h.approval_unused())


class LiveDuplicateTests(Base):
    def expect_refusal(self, h, reason):
        result = h.go()
        self.assertEqual(result["result"], reason)
        self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
        self.assertIsNone(h.approval_unused())  # nothing consumed
        self.assertEqual(h.outcomes()[-1], "refused")
        self.assertNotIn("attempt_started", h.outcomes())

    def test_duplicate_by_exact_name_with_suffix(self):
        row = {"name": NAME, "domain": "another.example", "alternates": [], "emails": [], "deleted": False}
        self.expect_refusal(self.harness(search={NAME: reply([row])}), "duplicate_in_production")

    def test_duplicate_by_primary_domain_even_without_row_fields(self):
        row = {"name": None, "domain": None, "alternates": [], "emails": [], "deleted": False}
        self.expect_refusal(self.harness(search={DOMAIN: reply([row])}), "duplicate_in_production")

    def test_demo_domain(self):
        self.expect_refusal(self.harness(demo={"status": 200, "hit": True, "count": 4}), "demo_domain_conflict")

    def test_lookup_error_is_never_a_clearance(self):
        broken = {"status": 500, "total": None, "rows": None}
        for search in ({NAME: broken}, {DOMAIN: broken}):
            self.expect_refusal(self.harness(search=search), "duplicate_check_unavailable")
        self.expect_refusal(self.harness(demo={"status": 500, "hit": None, "count": None}), "duplicate_check_unavailable")

    def test_a_duplicate_that_appears_after_the_form_was_filled_stops_before_consume_and_click(self):
        row = {"name": "Someone Else", "domain": DOMAIN, "alternates": [], "emails": [], "deleted": False}
        h = self.harness(search=Flip({"unused": reply([])}, {DOMAIN: reply([row])}))
        self.expect_refusal(h, "duplicate_in_production")
        self.assertGreaterEqual(h.page.search.calls[DOMAIN], 2)
        self.assertNotIn("Confirm", h.page.clicks)

    def test_session_lost_at_the_final_check(self):
        class ThirdRefused(dict):
            calls = 0

            def get(self, key, default=None):
                if key == runner.PDR_SESSION_CHECK_VALUE:
                    self.calls += 1
                    if self.calls >= 2:  # the early check, then the live pre-check right before the form opens
                        return {"status": 401, "total": None, "rows": None}
                return super().get(key, default)
        h = self.harness(search=ThirdRefused())
        result = h.go()
        self.assertIn(result["result"], ("production_session_expired",))
        self.assertEqual(h.page.confirm_clicks, 0)
        self.assertNotIn("Add Account", h.page.clicks)
        self.assertIsNone(h.approval_unused())

    def test_without_a_sort_header_every_read_reloads_with_the_form_closed(self):
        h = self.harness(header=False)  # the live case (2026-10-18): no sort header is ever found
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertEqual((h.page.reads_with_form_open, h.page.dispatches, h.page.gotos), (0, 0, 0))

    def test_every_live_read_is_the_pages_own_rewritten_request_and_the_form_is_never_reloaded(self):
        h = self.harness()
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertEqual(h.page.reloads, 1)  # the first read only
        self.assertEqual((h.page.dispatches, h.page.reads_with_form_open), (0, 0))  # nothing read while the form is open
        self.assertEqual(set(h.page.fetches), {("POST", pdr.ACCOUNTS_PATH)})
        for body in h.page.table_bodies:  # every read body is the validated grammar
            self.assertIsNone(runner.production_probe.read_body_problem(json.dumps(body)))

    def test_every_live_read_runs_with_the_form_closed(self):
        h = self.harness()
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertEqual((h.page.reads_with_form_open, h.page.dispatches), (0, 0))

    def test_a_duplicate_found_right_before_the_form_opens_refuses_before_it_opens(self):
        row = {"name": "Someone Else", "domain": DOMAIN, "alternates": [], "emails": [], "deleted": False}
        h = self.harness(search=Flip({"unused": reply([])}, {DOMAIN: reply([row])}))  # clean early, a match pre-form
        self.expect_refusal(h, "duplicate_in_production")
        self.assertNotIn("Add Account", h.page.clicks)  # the form was never opened

    def test_pre_form_reads_older_than_the_bound_refuse_before_consume_and_click(self):
        moment = [NOW]

        def slow_fill(*_a):
            moment[0] = NOW + runner.PCR_PRE_FORM_MAX_AGE + timedelta(seconds=1)
            return True
        h = self.harness()
        self.assertEqual(h.go(clock=lambda: moment[0], fill_date=slow_fill)["result"], "duplicate_check_stale")
        self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
        self.assertIsNone(h.approval_unused())

    def test_reads_going_stale_during_the_salesforce_reread_refuse_before_consume_and_click(self):
        moment = [NOW]
        h = self.harness()
        # the hook fires in the Salesforce re-read, i.e. AFTER the duplicate hook judged the age (review fix 2026-10-18)
        h.page.on_read["late"] = lambda: moment.__setitem__(0, NOW + runner.PCR_PRE_FORM_MAX_AGE + timedelta(seconds=1))
        self.assertEqual(h.go(clock=lambda: moment[0])["result"], "form_changed_during_checks")
        self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
        self.assertIsNone(h.approval_unused())
        self.assertNotIn("attempt_started", h.outcomes())

    def test_the_pages_own_table_refresh_during_the_fill_is_aborted_and_the_create_proceeds(self):
        h = self.harness()
        original, outcomes = h.page._open, []

        def open_and_refresh():  # live dry run #6: the page refreshes its own table while the form is open
            original()
            outcomes.append(h.page.fire("POST", dry.ORIGIN + pdr.ACCOUNTS_PATH, dry.pot.PAGE_DEFAULT_BODY))
        h.page._open = open_and_refresh
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertEqual(outcomes, ["aborted"])

    def test_a_stray_create_post_during_the_fill_is_blocked(self):
        h = self.harness(write_on_select=True)
        self.assertEqual(h.go()["result"], "write_request_blocked")
        self.assertEqual(h.page.confirm_clicks, 0)
        self.assertIsNone(h.approval_unused())


class CreateTests(Base):
    def test_happy_path_one_click_verified_and_the_uuid_is_never_printed(self):
        h = self.harness()
        result = h.go()
        self.assertEqual(result["result"], "created_verified")
        self.assertEqual((h.page.confirm_clicks, h.page.readback_calls), (1, 1))
        self.assertEqual(h.outcomes().count("attempt_started"), 1)
        self.assertEqual(sorted(h.outcomes()), sorted(["dry_run_passed", "attempt_started", "readback_verified"]))
        self.assertEqual(h.approval_unused(), "approval_consumed")
        self.assertEqual(len([r for r in h.page.routed if r.request.method == "POST" and r.request.url.endswith(live.CREATE_PATH)]), 1)
        self.assertNotIn(UUID, h.line)
        self.assertNotIn(UUID, h.stdout)
        self.assertEqual(result["salesforce_writeback"], "not_performed")
        verified = [r for r in h.loaded_records() if r["outcome"] == "readback_verified"][0]
        self.assertEqual(verified["account_uuid"], UUID)  # D2/D3: the redacted record is where it is kept
        self.assertEqual(ui.co_status(h.loaded_records())["code"], ui.CREATED_VERIFIED)

    def test_a_second_run_never_clicks_again_after_any_outcome(self):
        for page_kw in ({}, {"create_status": None}, {"create_status": 500}, {"readbacks": [reply([good_row(scanningInterval="DAILY")])]}):
            h = self.harness(**page_kw)
            h.go()
            first_clicks = h.page.confirm_clicks
            self.assertEqual(first_clicks, 1, page_kw)
            again = h.go(dry_run_record=False)
            self.assertEqual(h.page.confirm_clicks, first_clicks, page_kw)
            self.assertIn(again["result"], ("approval_consumed", "prior_attempt_needs_review"), page_kw)

    def test_no_response_is_unknown_and_blocks_the_co_without_a_readback(self):
        for status in (None, 500, 403):
            h = self.harness(create_status=status)
            result = h.go()
            self.assertEqual(result["result"], "create_outcome_unknown", status)
            self.assertEqual((h.page.confirm_clicks, h.page.readback_calls), (1, 0), status)
            self.assertEqual(h.approval_unused(), "approval_consumed")
            self.assertEqual(ui.co_status(h.loaded_records())["code"], ui.CREATE_OUTCOME_UNKNOWN)
            self.assertTrue(ui.co_status(h.loaded_records())["blocking"])

    def test_two_create_requests_from_one_click_are_unknown_and_only_one_goes_out(self):
        h = self.harness(posts=2)
        self.assertEqual(h.go()["result"], "create_outcome_unknown")
        sent = [r for r in h.page.routed if r.request.url.endswith(live.CREATE_PATH)]
        self.assertEqual([r.outcome for r in sent], ["continued", "aborted"])

    def test_readback_mismatch_stops_records_and_never_retries(self):
        h = self.harness(readbacks=[reply([good_row(scanningInterval="DAILY", **{"accountLicense.reconLevel": "X"})])])
        result = h.go()
        self.assertEqual(result["result"], "created_mismatch")
        self.assertEqual(h.page.confirm_clicks, 1)
        record = [r for r in h.loaded_records() if r["outcome"] == "created_unverified"][0]
        self.assertEqual(record["refusal_reason"], "readback_contract_mismatch")
        self.assertEqual(sorted(record["readback_mismatches"]), ["reconLevel", "scanningInterval"])
        self.assertEqual(ui.co_status(h.loaded_records())["code"], ui.CREATED_MISMATCH)

    def test_wrong_domain_name_or_uuid_is_a_mismatch(self):
        for row in (good_row(accountUuid="zz"), good_row(id="")):
            h = self.harness(readbacks=[reply([row])])
            self.assertEqual(h.go()["result"], "created_mismatch")
            self.assertEqual(h.page.confirm_clicks, 1)

    def test_row_never_appears_polling_is_bounded_and_unknown(self):
        h = self.harness(readbacks=[])
        self.assertEqual(h.go()["result"], "create_outcome_unknown")
        self.assertEqual((h.page.confirm_clicks, h.page.readback_calls), (1, live.READBACK_ATTEMPTS))

    def test_row_appears_late_still_verifies_with_one_click(self):
        h = self.harness(readbacks=[reply([]), reply([]), reply([good_row()])])
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertEqual((h.page.confirm_clicks, h.page.readback_calls), (1, 3))

    def test_a_confirm_that_fails_the_preflight_refuses_before_the_consume(self):
        for kw in ({"confirm_enabled": False}, {"hit": False}, {"hit": None}):
            h = self.harness(**kw)
            self.assertEqual(h.go()["result"], "confirm_preflight_failed", kw)
            self.assertEqual(h.page.confirm_clicks, 0, kw)
            self.assertIsNone(h.approval_unused(), kw)
            self.assertNotIn("attempt_started", h.outcomes())
            self.assertFalse(h.page.form_open)

    def test_a_confirm_that_stops_being_usable_after_the_consume_sends_nothing_and_blocks_the_co(self):
        for hits in ([True, False], [True, None]):
            h = self.harness()
            h.page.hits = list(hits)
            result = h.go()
            self.assertEqual(result["result"], "confirm_not_clicked", hits)
            self.assertEqual(h.page.confirm_clicks, 0, hits)
            self.assertEqual(h.approval_unused(), "approval_consumed")
            self.assertEqual(h.outcomes().count("attempt_started"), 1)
            self.assertTrue(ui.co_status(h.loaded_records())["blocking"])
            self.assertFalse(h.page.form_open, "the half-filled form is cancelled")
            self.assertGreaterEqual(h.page.scrolled, 2, "scrolled into view before every hit test")

    def test_the_attempt_is_recorded_before_the_click_and_the_form_values_are_the_production_plan(self):
        h = self.harness()
        h.go()
        started = [r for r in h.loaded_records() if r["outcome"] == "attempt_started"][0]
        self.assertTrue(all(v == "pass" for v in started["form_fields_checked"].values()))
        self.assertEqual(h.page.texts["Company name"].value, NAME)
        self.assertEqual(h.page.texts["Company primary domain"].value, DOMAIN)
        self.assertEqual(h.page.texts["User email domains (Comma Separated Values)"].value, "pentera.io")
        self.assertEqual(h.page.selects["accountType"].value, "Customer")
        self.assertEqual(h.page.selects["scanningInterval"].value, "None")
        self.assertEqual(h.page.selects["leakedCredentialsScanningInterval"].value, "Weekly")
        self.assertEqual(h.page.texts["Number of assets"].value, "1")

    def test_no_record_contains_a_secret_shaped_value(self):
        h = self.harness()
        h.go()
        for path in pcr.list_records(h.records, CO):
            text = Path(path).read_text(encoding="utf-8")
            for fragment in ("Bearer", "password", "cookie", "eyJ"):
                self.assertNotIn(fragment, text)


class AdvancedMismatchTests(Base):
    def test_an_advanced_control_that_cannot_be_set_off_refuses_before_the_consume_and_the_click(self):
        states = {n: False for n in dry.pdr.ADVANCED_NAMES}
        states["fullNucleiScanEnabled"] = True
        h = self.harness(advanced=states, sticky={"fullNucleiScanEnabled"})
        self.assertEqual(h.go()["result"], "toggle_click_failed")
        self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
        self.assertIsNone(h.approval_unused())
        self.assertNotIn("attempt_started", h.outcomes())
        record = [r for r in h.loaded_records() if r["outcome"] == "refused"][0]
        self.assertEqual(record["refusal_reason"], "toggle_click_failed")

    def test_advanced_controls_on_by_default_are_set_off_and_the_create_proceeds(self):
        expected = self.harness().go()["result"]
        states = {n: False for n in dry.pdr.ADVANCED_NAMES}
        states.update(automatedDiscoveryEnabled=True, subDomainsReconEnabled=True, webDictionaryBruteForceEnabled=True)
        h = self.harness(advanced=states)
        self.assertEqual(h.go()["result"], expected)
        self.assertFalse(any(h.page.advanced_state.values()))


class CreateBodyTests(Base):
    def verdict(self, **kw):
        h = self.harness(**kw)
        return h, h.go()["result"]

    def test_a_body_that_matches_the_contract_verifies(self):
        h, result = self.verdict()
        self.assertEqual(result, "created_verified")
        record = [r for r in h.loaded_records() if r["outcome"] == "readback_verified"][0]
        self.assertEqual(record.get("payload_mismatches"), [])

    def test_a_deviating_body_is_created_mismatch_with_names_only_and_never_a_pre_send_abort(self):
        body = good_create_body(scanNow=True, license__assetsNumber=1, brandNewFlag="customer-secret-value",
                                accountDomain="other-customer.example")
        h, result = self.verdict(body=body)
        self.assertEqual(result, "created_mismatch")
        self.assertEqual(h.page.confirm_clicks, 1)  # the create was sent: no pre-send abort
        record = [r for r in h.loaded_records() if r["outcome"] == "created_unverified"][0]
        self.assertEqual(record["refusal_reason"], "readback_payload_mismatch")
        self.assertEqual(sorted(record["payload_mismatches"]),
                         ["accountDomain", "license.assetsNumber", "scanNow", "unexpected:brandNewFlag"])
        text = "".join(Path(p).read_text(encoding="utf-8") for p in pcr.list_records(h.records, CO))
        for leaked in ("customer-secret-value", "other-customer.example", ORG_EMAIL):  # tenant name/domain are recorded by the readback anyway
            self.assertNotIn(leaked, text)
        self.assertNotIn("readback_verified", h.outcomes())

    def test_a_missing_or_unreadable_body_never_verifies(self):
        for body in (None, "not json"):
            h, result = self.verdict(body=body)
            self.assertEqual(result, "created_mismatch", body)
            record = [r for r in h.loaded_records() if r["outcome"] == "created_unverified"][0]
            self.assertEqual(record["payload_mismatches"], ["body_unreadable"])


class SearchControlTests(Base):
    """The generic search filter is unproven for domains: a control tenant from the clone must come back first."""

    def refused(self, h, reason, **kw):
        result = h.go(**kw)
        self.assertEqual(result["result"], reason)
        self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
        self.assertIsNone(h.approval_unused())
        self.assertNotIn("attempt_started", h.outcomes())

    def test_no_control_in_the_clone_refuses(self):
        h = self.harness()
        self.refused(h, "search_control_unavailable", clone=dry.good_clone)

    def test_a_control_the_searches_do_not_return_means_the_search_is_unproven(self):
        for search in ({CONTROL["domain"]: reply([])}, {CONTROL["name"]: reply([])},
                       {CONTROL["domain"]: reply([{**control_row(), "name": "Somebody Else"}])},
                       {CONTROL["domain"]: {"status": 500, "total": None, "rows": None}},
                       {CONTROL["name"]: reply([control_row()], total=400)},  # the filter was ignored: truncated
                       {CONTROL["domain"]: reply([{**control_row(), "domain": "other.example"}])}):
            h = self.harness(search=dict(search))
            self.refused(h, "search_unproven")

    def test_empty_result_for_the_target_is_a_clearance_only_after_a_passing_control(self):
        h = self.harness()
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertIn(CONTROL["domain"], h.page.search.keys())
        record = [r for r in h.loaded_records() if r["outcome"] == "attempt_started"][0]
        self.assertEqual(len(record.get("search_control", "x" * 16)), 16)

    def test_control_passes_and_the_target_is_found_refuses(self):
        row = {"name": "Someone Else", "domain": DOMAIN, "alternates": [], "emails": [], "deleted": False}
        self.refused(self.harness(search={DOMAIN: reply([row])}), "duplicate_in_production")

    def test_the_control_runs_in_the_same_session_before_the_target_and_is_recorded_only_as_a_hash(self):
        h = self.harness()
        h.go()
        values = h.page.table_values
        self.assertGreaterEqual(len(values), 4)
        self.assertLess(values.index(CONTROL["name"]), values.index(NAME))
        self.assertLess(values.index(CONTROL["domain"]), values.index(DOMAIN))
        refusal = self.harness(search={CONTROL["domain"]: reply([])})
        refusal.go()
        text = "".join(Path(p).read_text(encoding="utf-8") for p in pcr.list_records(refusal.records, CO))
        self.assertNotIn(CONTROL["name"], text)
        self.assertNotIn(CONTROL["domain"], text)


class FormRecheckTests(Base):
    """Review finding 1: the verified form is compared again after the live reads; a dialog header is never clicked."""

    def changed(self, h):
        result = h.go()
        self.assertEqual(result["result"], "form_changed_during_checks")
        self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
        self.assertIsNone(h.approval_unused())  # nothing consumed
        self.assertNotIn("attempt_started", h.outcomes())
        self.assertEqual(h.outcomes()[-1], "refused")

    def test_a_text_value_changed_during_the_duplicate_reads_refuses_before_consume_and_click(self):
        h = self.harness()
        h.page.on_read[NAME] = lambda: setattr(h.page.texts["Company name"], "value", "Changed Co")
        self.changed(h)

    def test_a_select_changed_during_the_duplicate_reads_refuses(self):
        h = self.harness()
        h.page.on_read[DOMAIN] = lambda: setattr(h.page.selects["accountType"], "value", "Trial")
        self.changed(h)

    def test_an_advanced_control_flipped_during_the_duplicate_reads_refuses(self):
        h = self.harness()
        h.page.on_read[NAME] = lambda: h.page.advanced_state.update(aiEnabled=True)
        self.changed(h)

    def test_spycloud_flipped_during_the_duplicate_reads_refuses_before_consume_and_click(self):
        h = self.harness()
        h.page.on_read[NAME] = lambda: setattr(h.page, "spy_state", False)
        self.changed(h)

    def test_spycloud_absent_at_the_fill_and_at_the_recheck_is_stable(self):
        # Live 2026-10-18 (CO-0798 dry run #2): the production Add form has no SpyCloud switch ("absent"); the readback
        # still requires leakedCredentialsSettings.spyCloudSettings.enabled = True (the server default).
        h = self.harness()
        h.page.spy_state = None
        self.assertEqual(h.go()["result"], "created_verified")

    def test_spycloud_appearing_during_the_duplicate_reads_refuses(self):
        h = self.harness()
        h.page.spy_state = None
        h.page.on_read[NAME] = lambda: setattr(h.page, "spy_state", True)
        self.changed(h)

    def test_an_unknown_extra_control_appearing_during_the_reads_refuses(self):
        h = self.harness()
        h.page.on_read[NAME] = lambda: h.page.extra_fields.append(("", "Surprise setting", "text", None, ""))
        self.changed(h)

    def test_a_known_control_removed_during_the_reads_refuses(self):
        h = self.harness()
        h.page.on_read[NAME] = lambda: setattr(h.page, "form_fields",
                                               [f for f in h.page.form_fields if f[0] != "notificationsAllowed"])
        self.changed(h)

    def test_the_recheck_result_covers_every_fill_key_and_the_extra_items(self):
        seen = {}
        original = runner._pdr_recheck_form

        def spy(*args, **kw):
            seen.update(original(*args, **kw))
            return seen
        h = self.harness()
        with mock.patch.object(runner, "_pdr_recheck_form", spy):
            self.assertEqual(h.go()["result"], "created_verified")
        self.assertLessEqual({"advanced_controls", "country_code", "spycloud_state", "form_schema"}, set(seen))

    def test_the_country_changed_during_the_duplicate_reads_refuses(self):
        h = self.harness()
        h.page.on_read[NAME] = lambda: setattr(h.page, "country_value", "US")
        self.changed(h)

    def test_an_unchanged_form_still_creates(self):
        h = self.harness()
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertEqual(h.page.confirm_clicks, 1)

    def test_a_dialog_header_button_is_never_clicked_during_the_live_reads(self):
        h = self.harness()
        h.page.dialog_header = True
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertEqual(h.page.dialog_header_clicks, 0)
        self.assertEqual((h.page.dispatches, h.page.reads_with_form_open), (0, 0))

    def test_a_dialog_only_header_is_never_clicked_and_the_reads_reload_with_the_form_closed(self):
        h = self.harness(header=False)
        h.page.dialog_header = True
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertEqual((h.page.dialog_header_clicks, h.page.dispatches, h.page.reads_with_form_open), (0, 0, 0))

    def test_the_new_reasons_are_in_the_fixed_live_enum(self):
        for code in ("form_changed_during_checks", "form_recheck_missing"):
            self.assertIn(code, live.LIVE_REASONS)


class SourceRecheckTests(Base):
    def run_with(self, second):
        h = self.harness()
        calls = []

        def load(_ref):
            calls.append(1)
            if len(calls) == 1:
                return SOURCE
            if isinstance(second, Exception):
                raise second
            return second
        h.load_source = load
        result = h.go()
        return h, result

    def test_revision_drift_or_a_new_uuid_between_the_form_and_the_click_refuses_before_the_consume(self):
        for second, reason in ((dry.make_source(source_revision="2026-10-17T00:00:00.000+0000"), "approval_revision_drift"),
                               (dry.make_source(salesforce_id_present=True), "salesforce_prior_uuid_present"),
                               (RuntimeError("salesforce_fill_source_unavailable"), "source_recheck_unavailable")):
            h, result = self.run_with(second)
            self.assertEqual(result["result"], reason, reason)
            self.assertEqual(h.page.confirm_clicks, 0)
            self.assertIsNone(h.approval_unused())
            self.assertNotIn("attempt_started", h.outcomes())
            self.assertFalse(h.page.form_open)


class LaunchTicketTests(Base):
    def refused(self, h, reason, **kw):
        self.assertEqual(h.go(**kw)["result"], reason)
        self.assertEqual((h.opened, h.page.confirm_clicks), (0, 0))
        self.assertIsNone(h.approval_unused())

    def test_the_cli_flag_alone_never_launches(self):
        self.refused(self.harness(), "launch_ticket_invalid", with_ticket=False)

    def test_wrong_binding_expired_or_reused_tickets_refuse(self):
        h = self.harness()
        h.mint(revision="other-revision")
        self.refused(h, "launch_ticket_invalid", with_ticket=False)
        self.refused(self.harness(), "launch_ticket_invalid", ticket_when=NOW - timedelta(minutes=3))
        h = self.harness()
        h.go()
        h.page.confirm_clicks = 0
        again = h.go(with_ticket=False, dry_run_record=False)
        self.assertEqual(again["result"], "launch_ticket_invalid")
        self.assertEqual(h.page.confirm_clicks, 0)

    def test_the_ticket_must_be_minted_for_the_approval_issuer(self):
        self.refused(self.harness(), "operator_not_owner", operator="someone.else")

    def test_dry_run_age_is_enforced_by_the_runner(self):
        h = self.harness()
        pcr.record(h.records, {"co_reference": CO, "approval_ref": REF, "outcome": "dry_run_passed", "route": ROUTE},
                   NOW - timedelta(hours=25))
        self.refused(h, "dry_run_old", dry_run_record=False)


class StrictReadbackTests(Base):
    def test_license_dates_mfa_and_organization_email_are_compared_by_name_only(self):
        bad = good_row(**{"accountLicense.expirationDate": "2027-06-01T07:00:00.000Z",
                          "accountLicense.startDate": "2026-12-01T09:00:00.000Z", "primaryUser.isMfaRequired": False,
                          "primaryUser.email": "someone.else@pentera.io"})
        h = self.harness(readbacks=[reply([bad])])
        self.assertEqual(h.go()["result"], "created_mismatch")
        record = [r for r in h.loaded_records() if r["outcome"] == "created_unverified"][0]
        self.assertEqual(sorted(record["readback_mismatches"]),
                         ["licenseExpirationDate", "licenseStartDate", "mfaRequired", "organizationEmail"])
        text = "".join(Path(p).read_text(encoding="utf-8") for p in pcr.list_records(h.records, CO))
        self.assertNotIn(ORG_EMAIL, text)
        self.assertNotIn("someone.else", text)

    def test_missing_paths_are_mismatches_not_silently_ignored(self):
        row_ = good_row()
        for key in ("primaryUser.isMfaRequired", "primaryUser.email", "accountLicense.startDate", "accountLicense.expirationDate"):
            del row_[key]
        h = self.harness(readbacks=[reply([row_])])
        self.assertEqual(h.go()["result"], "created_mismatch")

    def test_epoch_millisecond_dates_are_accepted(self):
        row_ = good_row(**{"accountLicense.startDate": 1792141200000, "accountLicense.expirationDate": 1810796400000})
        h = self.harness(readbacks=[reply([row_])])
        self.assertEqual(h.go()["result"], "created_verified")


class ContextGuardAndCountryTests(Base):
    def test_the_guard_is_installed_at_context_level_and_a_popup_after_the_click_is_unknown(self):
        h = self.harness()
        h.page.popups_after_click = 1
        result = h.go()
        self.assertEqual(result["result"], "create_outcome_unknown")
        self.assertEqual((h.page.confirm_clicks, h.page.readback_calls), (1, 0))
        self.assertEqual(h.approval_unused(), "approval_consumed")
        self.assertTrue(ui.co_status(h.loaded_records())["blocking"])
        self.assertTrue(h.page.routes)

    def test_a_popup_after_the_poll_window_turns_a_matching_readback_into_unknown(self):
        h = self.harness(readbacks=[reply([]), reply([good_row()])])
        h.page.popup_on_readback = True
        result = h.go()
        self.assertEqual(result["result"], "create_outcome_unknown")
        self.assertNotIn("readback_verified", h.outcomes())
        self.assertEqual(h.page.confirm_clicks, 1)
        self.assertTrue(ui.co_status(h.loaded_records())["blocking"])

    def test_a_service_worker_appearing_after_the_click_is_unknown(self):
        h = self.harness()
        h.page.workers_after_click = 1
        self.assertEqual(h.go()["result"], "create_outcome_unknown")
        self.assertEqual(h.page.readback_calls, 0)

    def test_an_unroutable_context_stops_the_run_before_any_form_work(self):
        h = self.harness()
        h.page.context = SimpleNamespace(pages=[h.page])  # no route(): cannot guard popups
        result = h.go()
        self.assertEqual(result["result"], "production_create_crashed")
        self.assertEqual(h.page.confirm_clicks, 0)

    def test_country_is_compared_exactly_with_the_value_the_form_submits(self):
        h = self.harness(readbacks=[reply([good_row(accountCountryCode="US")])])
        self.assertEqual(h.go()["result"], "created_mismatch")
        record = [r for r in h.loaded_records() if r["outcome"] == "created_unverified"][0]
        self.assertIn("accountCountryCode", record["readback_mismatches"])

    def test_an_unreadable_country_value_refuses_before_the_consume(self):
        for value in (None, "Chile", "cl"):
            h = self.harness()
            h.page.country_value = value
            self.assertEqual(h.go()["result"], "country_code_unavailable", value)
            self.assertEqual(h.page.confirm_clicks, 0)
            self.assertIsNone(h.approval_unused())
            self.assertFalse(h.page.form_open)

    def test_the_dry_run_age_is_rechecked_at_click_time(self):
        h = self.harness()
        calls = [0]

        def clock():  # the pre-form read stamp, the fresh gate and the read age at NOW; the dry-run age check 25 h later
            calls[0] += 1
            return NOW if calls[0] < 4 else NOW + timedelta(hours=25)
        # stamp() is fixed in the harness; use the runner's own clock hook to move time forward for the late check
        h.seed_dry_run()
        h.mint()
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", True), \
                mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True), contextlib.redirect_stdout(io.StringIO()):
            line = runner.run_production_create(
                CO, REV, owner_go=True, environ=dict(dry.UNLOCK), now=NOW, records_parent=h.records, approval_path=h.approval_path,
                load_source=h.load_source, clone_facts=good_clone, open_page=h.open_page, fill_date=lambda *_a: True,
                date_kept=lambda *_a: True, run_day=DAY, clock=clock, ticket_parent=h.records, org_probe=dry.ORG_PROBE)
        self.assertEqual(json.loads(line)["result"], "dry_run_old")
        self.assertEqual(h.page.confirm_clicks, 0)
        self.assertIsNone(h.approval_unused())

    def test_org_is_pinned_by_id_when_an_expected_org_is_configured(self):
        env = {**dry.UNLOCK, "SURFACE_SF_EXPECTED_ORG_ID": "00D5g0000012345EAA"}
        for probe, reason in ((lambda: [{"Id": "00D5g0000099999EAA"}], "salesforce_org_not_pinned"),
                              (lambda: [], "salesforce_org_not_pinned"), (lambda: 1 / 0, "salesforce_org_not_pinned")):
            h = self.harness()
            h.seed_dry_run()
            h.mint()
            with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", True), \
                    mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
                out = runner.run_production_create(CO, REV, owner_go=True, environ=env, now=NOW, records_parent=h.records,
                                                   approval_path=h.approval_path, load_source=h.load_source,
                                                   open_page=h.open_page, ticket_parent=h.records, org_probe=probe)
            self.assertIn(reason, out)
            self.assertEqual((h.opened, h.loaded), (0, 0))
        self.assertEqual(runner._pcr_org_problem({}), "salesforce_org_not_pinned")
        self.assertIsNone(runner._pcr_org_problem(env, lambda: [{"Id": "00D5g0000012345XYZ"}]))

    def test_a_missing_or_malformed_expected_org_id_refuses_the_run_never_skips_the_pin(self):
        import tempfile
        probe = lambda: [{"Id": dry.ORG}]  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(runner._pcr_org_problem({"LOCALAPPDATA": tmp}, probe), "salesforce_org_not_pinned")
            self.assertEqual(runner._pcr_org_problem({}, probe), "salesforce_org_not_pinned")
            self.assertEqual(runner._pcr_org_problem({"SURFACE_SF_EXPECTED_ORG_ID": "short"}, probe), "salesforce_org_not_pinned")
            folder = Path(tmp) / "SurfaceOnboarding"
            folder.mkdir()
            (folder / "salesforce-expected-org-id.txt").write_text(dry.ORG, encoding="ascii")  # the launcher file
            self.assertIsNone(runner._pcr_org_problem({"LOCALAPPDATA": tmp}, probe))
            self.assertEqual(runner._pcr_org_problem({"LOCALAPPDATA": tmp}, lambda: [{"Id": "00D5g0000099999EAA"}]),
                             "salesforce_org_not_pinned")
            (folder / "salesforce-expected-org-id.txt").write_text("not an org id", encoding="ascii")
            self.assertEqual(runner._pcr_org_problem({"LOCALAPPDATA": tmp}, probe), "salesforce_org_not_pinned")

    def test_a_run_without_any_expected_org_refuses_before_any_read(self):
        h = self.harness()
        h.seed_dry_run()
        h.mint()
        env = {k: v for k, v in dry.UNLOCK.items() if k != "SURFACE_SF_EXPECTED_ORG_ID"}
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", True),                 mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
            out = runner.run_production_create(CO, REV, owner_go=True, environ={**env, "LOCALAPPDATA": str(h.records)}, now=NOW,
                                               records_parent=h.records, approval_path=h.approval_path, load_source=h.load_source,
                                               open_page=h.open_page, ticket_parent=h.records, org_probe=dry.ORG_PROBE)
        self.assertIn("salesforce_org_not_pinned", out)
        self.assertEqual((h.opened, h.loaded), (0, 0))

    def test_the_dry_run_also_refuses_an_alias_override(self):
        h = self.harness()
        line = runner.run_production_dry_run(CO, REV, environ={**dry.UNLOCK, "SURFACE_SF_TARGET_ORG": "other"}, now=NOW,
                                             records_parent=h.records, approval_path=h.approval_path,
                                             load_source=h.load_source, open_page=h.open_page)
        self.assertIn("salesforce_alias_not_pinned", line)
        self.no_external_activity(h)

    def test_an_alias_override_in_the_environment_refuses(self):
        h = self.harness()
        with mock.patch.dict("os.environ", {}, clear=False):
            out = runner.run_production_create(CO, REV, owner_go=True, environ={**dry.UNLOCK, "SURFACE_SF_TARGET_ORG": "other"},
                                               now=NOW, records_parent=h.records, approval_path=h.approval_path,
                                               load_source=h.load_source, open_page=h.open_page)
        self.assertIn("salesforce_alias_not_pinned", out)
        self.no_external_activity(h)


class ClearedAttemptTests(Base):
    OLD = "PROD-CE-0001-20261016"

    def rows(self, *outcomes):
        rev = {"approval_ref": self.OLD, "salesforce_revision": REV}
        table = {"started": {"outcome": "attempt_started", **rev},
                 "refused": {"outcome": "refused", "refusal_reason": "confirm_not_clicked", **rev},
                 "cleared": {"outcome": "owner_review_cleared", **rev},
                 "unknown": {"outcome": "uncertain_submit", **rev}}
        return [table[o] for o in outcomes]

    def test_a_not_clicked_attempt_blocks_until_the_owner_review_is_recorded(self):
        h = self.harness()
        self.assertEqual(h.go(rows=self.rows("started", "refused"))["result"], "prior_attempt_needs_review")
        h = self.harness()
        self.assertEqual(h.go(rows=self.rows("started", "refused", "cleared"))["result"], "created_verified")
        self.assertEqual(h.page.confirm_clicks, 1)

    def test_an_unknown_or_unfinished_attempt_can_never_be_cleared(self):
        for rows in (("started", "cleared"), ("started", "unknown", "cleared"), ("unknown", "cleared")):
            h = self.harness()
            self.assertEqual(h.go(rows=self.rows(*rows))["result"], "prior_attempt_needs_review", rows)
            self.assertEqual(h.page.confirm_clicks, 0)


class SearchProbeTests(Base):
    ENV = {"SURFACE_ONBOARDING_PRODUCTION_PROBE": "1", "SURFACE_ONBOARDING_PRODUCTION_PROBE_REF": "PROBE-SEARCH-20261017"}

    def probe(self, h, clone=good_clone, env=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            line = runner.run_production_search_probe(environ=self.ENV if env is None else env, now=NOW, report_base=h.records,
                                                      clone_facts=clone, open_page=h.open_page)
        return json.loads(line)

    def test_proven_when_both_searches_return_the_control_and_nothing_is_clicked(self):
        h = self.harness()
        result = self.probe(h)
        self.assertEqual((result["result"], result["name_search_proven"], result["domain_search_proven"]),
                         ("production_search_probe_complete", True, True))
        self.assertEqual((h.page.confirm_clicks, h.page.clicks), (0, []))
        files = list((h.records / "production-probe").glob("readback-*.json"))
        self.assertEqual(len(files), 1)
        self.assertNotIn(CONTROL["name"], files[0].read_text(encoding="utf-8"))

    def test_unproven_domain_search_is_reported(self):
        h = self.harness(search={CONTROL["domain"]: reply([])})
        result = self.probe(h)
        self.assertEqual((result["result"], result["reason"], result["domain_search_proven"]),
                         ("production_search_probe_refused", "search_unproven", False))

    def test_a_filter_the_server_ignored_is_unproven_even_when_the_control_is_in_the_rows(self):
        for key in ("name", "domain"):
            h = self.harness(search={CONTROL[key]: reply([control_row()], total=400)})
            result = self.probe(h)
            self.assertEqual((result["result"], result["reason"]), ("production_search_probe_refused", "search_unproven"), key)
            self.assertFalse(result[f"{key}_search_proven"], key)

    def test_a_domain_search_whose_row_has_another_domain_is_unproven(self):
        h = self.harness(search={CONTROL["domain"]: reply([{**control_row(), "domain": "other.example"}])})
        result = self.probe(h)
        self.assertEqual((result["reason"], result["name_search_proven"], result["domain_search_proven"]),
                         ("search_unproven", True, False))

    def test_the_report_line_keeps_its_shape(self):
        result = self.probe(self.harness())
        self.assertEqual(list(result), ["result", "reason", "name_search_proven", "domain_search_proven"])
        self.assertEqual(result["reason"], "proven")

    def test_no_control_or_no_gate(self):
        self.assertEqual(self.probe(self.harness(), clone=dry.good_clone)["reason"], "search_control_unavailable")
        h = self.harness()
        self.assertEqual(self.probe(h, env={})["result"], "production_search_probe_refused")
        self.assertEqual(h.opened, 0)


class GlueHygieneTests(unittest.TestCase):
    def test_account_add_appears_in_the_live_glue_only_through_the_live_module(self):
        text = Path(runner.__file__).read_text(encoding="utf-8")
        start = text.index("# --- Production CE-only LIVE CREATE")
        glue = text[start:text.index("def _autopublish_vm_snapshot")]
        self.assertNotIn("account/add", glue)
        self.assertNotIn('name="Confirm"', glue)

    def test_cli_flag_dispatch_and_exclusivity(self):
        with mock.patch.object(runner, "run_production_create", return_value='{"result":"x"}') as ran, \
                mock.patch.object(runner.sys, "argv", ["r", "--production-create", "--co", CO, "--revision", REV, "--owner-go"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 0)
        ran.assert_called_once_with(CO, REV, owner_go=True)
        for extra in ([], ["--co", CO], ["--co", CO, "--revision", REV, "--dry-run"],
                      ["--co", CO, "--revision", REV, "--production-dry-run"],
                      ["--co", CO, "--revision", REV, "--route", "case_1_new_surface_only"]):
            with mock.patch.object(runner.sys, "argv", ["r", "--production-create", *extra]), \
                    mock.patch.object(runner, "run_production_create") as ran, self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                runner.main()
            ran.assert_not_called()
        with mock.patch.object(runner.sys, "argv", ["r", "--owner-go", "--co", CO]), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main()


if __name__ == "__main__":
    unittest.main()
