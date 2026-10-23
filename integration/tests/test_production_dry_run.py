"""Tests for the production CE-only DRY RUN (docs/42 section 6.2): pure logic, the separate lock, the request guard,
the click guard and an end-to-end run against a fake page. No browser, network, Salesforce, Redash or BackOffice."""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from integration.onboarding import production_approval as approval
from integration.onboarding import production_create as pc
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_dry_run as pdr
from integration.onboarding import production_probe as probe
from integration.onboarding import session_readiness
from integration.tests import page_own_table as pot
from integration.onboarding.run_target import ProductionLocked, production_dry_run_target, production_target
import tools.attended_ce_only_playwright as runner
from integration.tests.locked_build import locked_create_build  # noqa: E402

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
ORIGIN = "https://app.pentera.io"
REF = "PROD-CE-0797-20261016"
CO = "CO-0797"
REV = "2026-10-16T09:00:00.000+0000"
POISON_NAME = "Zeta Quux Holdings"
POISON_DOMAIN = "zetaquux.example"
ORG = "00D5g0000012345EAA"
ORG_PROBE = lambda: [{"Id": ORG}]  # noqa: E731
UNLOCK = {"SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1", "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": REF,
          "SURFACE_SF_EXPECTED_ORG_ID": ORG}


def make_source(**over):
    data = dict(reference=CO, source_revision=REV, account_id="001AAAAAAAAAAAAAAA", account_name=POISON_NAME,
                email_domain=POISON_DOMAIN, country="Chile", subscription_start=date(2026, 5, 21),
                subscription_end=date(2027, 5, 20), tenant_name=POISON_NAME + " - CE Only",
                primary_user_alias="zetaquux", salesforce_id_present=False)
    data.update(over)
    return runner.CeFillSource(**data)


def good_clone(_source, _now):
    return {"collected_at": NOW - timedelta(hours=1), "clone_matches": True, "duplicate_found": False,
            "live_paid_tenant_count": 0}


# --- the production form as recorded by the 2026-10-16 probe (names and labels only) ------------------------------------

PROBE_FIELDS = [
    ("", "Company name", "text", None, ""), ("accountType", "", "select", 3, ""),
    ("", "Company primary domain", "text", None, ""), ("", "Alternate Domains (Comma Separated Values)", "text", None, ""),
    ("", "SubDomains (Comma Separated Values)", "text", None, ""),
    ("", "User email domains (Comma Separated Values)", "text", None, ""),
    ("", "Networks (Comma Separated Values)", "text", None, ""), ("accountCountry", "", "select", 251, ""),
    ("", "First name", "text", None, ""), ("", "Last name", "text", None, ""),
    ("", "Organization Email", "text", None, ""), ("", "Phone number", "text", None, ""),
    ("", "Job title", "text", None, ""), ("mfaRequired", "", "hidden", None, ""),
    ("mfaRequired", "", "checkbox", None, "Toggle_Switch-mfaRequired"),
    ("", "Select Operator Accounts", "text", None, ""), ("scanningInterval", "", "select", 4, ""),
    ("", "Scan now", "checkbox", None, ""),
    ("notificationsAllowed", "", "checkbox", None, ""), ("multipleUsersAllowed", "", "checkbox", None, ""),
    ("apiAccessAllowed", "", "checkbox", None, ""), ("phishingEnabled", "", "checkbox", None, ""),
    ("leakedCredentialsAllowed", "", "checkbox", None, ""),
    ("leakedCredentialsScanningInterval", "", "select", 4, ""),
    ("", "Leaked Credentials scanned domains (Comma Separated Values)", "text", None, ""),
    ("provisioningEnabled", "", "checkbox", None, ""), ("subDomainsNumberAllowed", "", "checkbox", None, ""),
    ("licenseType", "", "select", 5, ""), ("", "Number of assets", "text", None, ""),
    ("", "Number of domains", "text", None, ""), ("", "Number of subdomains", "number", None, ""),
    ("", "", "text", None, "AddEditTenantModal-date-startDate"),
    ("", "", "text", None, "AddEditTenantModal-date-expirationDate"),
]


VALID_READ_BODY = json.dumps({"tableServerData": {"offset": 0, "items_per_page": 100,
                                                  "filters": {"and": [{"method": "search", "value": "x"}]}}})


def form_of(fields):
    return {"fields": [{"name": n, "label": label, "type": t, "required": False, "option_count": c, "data_am": a}
                       for n, label, t, c, a in fields], "buttons": ["Advanced options", "Cancel", "Confirm"]}


# --- fake page ------------------------------------------------------------------------------------------------------

FakeRequest, FakeRoute = pot.FakeRequest, pot.FakeRoute


class Missing:
    def count(self):
        return 0

    @property
    def first(self):
        raise AssertionError("first of a missing locator")


class Ctl:
    """One fake control. A checkbox toggles on click; a select keeps the chosen option; a text keeps filled text."""

    def __init__(self, page, kind, key, value="", checked=False, options=None, mangle=None, enabled=True):
        self.page, self.kind, self.key = page, kind, key
        self.hit = key not in page.hit_fail
        self.adv = kind == "checkbox" and key in page.advanced_state  # an advanced switch: its state lives on the page
        self.scrolled = False  # a control below the fold is hit-testable only after it was scrolled into view
        self.value, self.checked, self.options, self.mangle, self.enabled = value, checked, options or [], mangle, enabled

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def is_enabled(self):
        return self.enabled

    def is_checked(self):
        return self.page.advanced_state[self.key] if self.adv else self.checked

    def click(self, **kw):
        assert "force" not in kw, "a forced click must never be made"
        if self.key in self.page.click_fail:
            raise RuntimeError("element intercepts pointer events")
        self.page.clicks.append(self.key)
        if self.kind == "checkbox" and self.key not in self.page.sticky:  # a sticky switch ignores the click
            self.checked = not self.is_checked()
            if self.adv:
                self.page.advanced_state[self.key] = self.checked

    def fill(self, value, **_kw):
        assert "\n" not in value and "\r" not in value, "no newline may ever be typed"
        self.value = self.mangle(value) if self.mangle else value

    def input_value(self):
        return self.value

    def select_option(self, label=None, **_kw):
        if self.page.write_on_select:
            self.page.fire("POST", ORIGIN + "/api/v1/backoffice/account/add")
        self.value = label

    def scroll_into_view_if_needed(self, **_kw):
        self.scrolled = True

    def evaluate(self, js):
        if js == pdr.HIT_TEST_JS:  # elementFromPoint finds nothing outside the viewport
            return self.hit and (self.key not in self.page.below_fold or self.scrolled)
        return self.value if self.kind == "select" else 0

    def locator(self, _selector):
        return SimpleNamespace(all_text_contents=lambda: list(self.options))


class Many:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    @property
    def first(self):
        return self.items[0]


class Button:
    def __init__(self, page, name, effect=None):
        self.page, self.name, self.effect = page, name, effect

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def is_visible(self):
        return not self.page.form_open if self.name == "Add Account" else True

    def is_enabled(self):
        return self.page.confirm_enabled if self.name == "Confirm" else True

    def scroll_into_view_if_needed(self):
        self.page.scrolled += 1

    def evaluate(self, js):
        assert js == pdr.HIT_TEST_JS
        return self.page.confirm_hit

    def click(self, **kw):
        assert "force" not in kw, "a forced click must never be made"
        self.page.clicks.append(self.name)
        if self.name == "Confirm":
            raise AssertionError("Confirm must never be clicked")
        if self.effect:
            self.effect()


class FakePage(pot.PageOwnTable):
    """A production page whose table answers from ``search`` (value -> reduced reply). Reads are the PAGE's own table
    requests (see page_own_table): ``session`` is the status of its own start-up calls, ``demo`` the demo-domain list it
    fetches on load (reduced {status, hit, count}); a sort header exists unless ``header=False``."""

    def __init__(self, *, advanced=None, form_fields=None, text_labels=None, mangle=None, write_on_select=False,
                 session=None, search=None, demo=None, advanced_button=True, sw_count=0, hit_fail=(),
                 click_fail=(), add_label="Add Account", header=True, below_fold=(), sticky=()):
        self.hit_fail, self.click_fail, self.sw_count, self.add_label = set(hit_fail), set(click_fail), sw_count, add_label
        self.below_fold, self.sticky = set(below_fold), set(sticky)
        self.confirm_enabled, self.confirm_hit, self.scrolled = True, True, 0
        self.url = ORIGIN + "/backoffice/tenantManagement"
        self.clicks, self.keys = [], []
        self.form_open, self.expanded = False, False
        self.write_on_select = write_on_select
        self.advanced_button = advanced_button
        self.form_fields = form_fields if form_fields is not None else PROBE_FIELDS
        self.advanced = advanced if advanced is not None else {n: False for n in pdr.ADVANCED_NAMES}
        self.advanced_state = dict(self.advanced)
        self.spy_state = True  # the informational SpyCloud box (ON is the server default); None = absent (live 2026-10-18)
        self.extra_fields = []  # controls that appear in the expanded form after the fill (a re-render)
        self.session = session or {pdr.AUTHORITIES_PATH: {"status": 200}, pdr.ACCOUNT_INFO_PATH: {"status": 200}}
        self.search = search if search is not None else {}
        self.demo = demo or {"status": 200, "hit": False, "count": 3}
        self._init_table(header=header, passive=self.session, demo=self.demo, demo_domain=POISON_DOMAIN)
        self.keyboard = SimpleNamespace(press=self._press)
        self.typed = []
        self.texts = {}
        self.boxes = {}
        self.selects = {}
        self._build(text_labels or {}, mangle)

    # -- controls
    def _build(self, relabel, mangle):
        for name, label, kind, count, data_am in self.form_fields:
            shown = relabel.get(label, label)
            if kind in ("text", "number") and shown and shown != "Select Operator Accounts":
                self.texts[shown] = Ctl(self, "text", shown, mangle=mangle if shown == "Company name" else None)
            elif kind == "select":
                options = ["Chile", "United States of America", "Other"] if name == "accountCountry" else []
                self.selects[name] = Ctl(self, "select", name, options=options)
            elif kind == "checkbox" and name:
                self.boxes[name] = Ctl(self, "checkbox", name)
            elif kind == "checkbox" and label == "Scan now":
                self.texts["Scan now"] = Ctl(self, "checkbox", "scan_now")

    def _press(self, key):
        assert key not in ("Enter", "NumpadEnter") and "\n" not in key, "no submit-capable key"
        self.keys.append(key)
        if key == "Escape":
            self.form_open = False

    def _open(self):
        self.form_open = True

    def _close(self):
        self.form_open = False

    def _expand(self):
        self.expanded = True

    def _advanced_boxes(self):
        return {n: Ctl(self, "checkbox", n, checked=self.advanced_state[n]) for n in self.advanced_state}

    # -- playwright surface (routing, the page's own table request, reload, expect_response: PageOwnTable)
    def wait_for_timeout(self, _ms):
        pass

    def evaluate(self, script, arg=None):
        if script == probe.FORM_JS:
            if not self.form_open:
                return None
            fields = list(self.form_fields)
            if self.expanded:
                fields += [(n, "", "checkbox", None, "") for n in list(self.advanced_state) + ["spyCloudEnabled"]
                           if n in self.advanced_state or (n == "spyCloudEnabled" and self.spy_state is not None)]
            return form_of(fields + list(self.extra_fields))
        if script == pdr.SERVICE_WORKER_JS:
            return {"count": self.sw_count}
        raise AssertionError("unexpected evaluate: a production read never evaluates a script in the page")

    def _serve_table(self, value):
        return self.search.get(value, {"status": 200, "total": 0, "rows": []})

    def get_by_role(self, role, name=None, exact=False):
        if role == "button" and name == self.add_label == "Add Account":
            return Button(self, "Add Account", self._open)
        if role == "button" and name == "Cancel" and self.form_open:
            return Button(self, "Cancel", self._close)
        if role == "button" and name == "Confirm" and self.form_open:
            return Button(self, "Confirm")
        return Missing()

    def get_by_label(self, pattern):
        if not self.form_open:
            return Missing()
        hits = [c for label, c in self.texts.items() if pattern.search(label)]
        return Many(hits) if hits else Missing()

    def get_by_text(self, text, exact=False):
        if text == "Advanced options" and self.form_open and self.advanced_button:
            return Button(self, "Advanced options", self._expand)
        return Missing()

    def locator(self, selector):
        if selector == pot.HEADER_SELECTOR:  # the table header lives behind the form, so it is found either way
            return self._header() or Missing()
        if not self.form_open:
            return Missing()
        match = re.fullmatch(r'select\[name="(\w+)"\]', selector)
        if match:
            return self.selects.get(match.group(1)) or Missing()
        match = re.fullmatch(r'input\[type=checkbox\]\[name="(\w+)"\]', selector)
        if match:
            key = match.group(1)
            if key in self.boxes:
                return self.boxes[key]
            if self.expanded and key in self.advanced_state:
                box = self._advanced_boxes()[key]
                return box
            if self.expanded and key == "spyCloudEnabled" and self.spy_state is not None:
                return Ctl(self, "checkbox", key, checked=self.spy_state)
            return Missing()
        if selector == runner.OPERATOR_ACCOUNT_INPUT_SELECTOR:
            return Ctl(self, "text", "operator")
        return Missing()


# --- harness --------------------------------------------------------------------------------------------------------

class Harness:
    def __init__(self, test, **page_kw):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.records = self.tmp / "state"
        self.approval_path = self.tmp / "approvals.json"
        approval.issue(self.approval_path, REF, CO, "case_2_new_ce_only", REV, "milton", frozenset({"milton"}),
                       NOW - timedelta(hours=1))
        self.page = FakePage(**page_kw)
        self.opened = 0
        self.loaded = 0

    @contextlib.contextmanager
    def open_page(self, _target, _wait):
        self.opened += 1
        yield self.page

    def load_source(self, _ref):
        self.loaded += 1
        return make_source()

    def run(self, *, environ=None, revision=REV, clone=good_clone, supported=True, reference=CO, source=None,
            clock=None):
        env = dict(UNLOCK if environ is None else environ)
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", supported):
            line = runner.run_production_dry_run(
                reference, revision, environ=env, now=NOW, records_parent=self.records, approval_path=self.approval_path,
                load_source=(lambda _r: source) if source else self.load_source, clone_facts=clone,
                open_page=self.open_page, fill_date=lambda *_a: True, date_kept=lambda *_a: True, clock=clock,
                org_probe=ORG_PROBE)
        return json.loads(line)

    def record(self):
        files = sorted((self.records / "production-create-records").glob("*.json"))
        self.test.assertEqual(len(files), 1, files)
        return json.loads(files[0].read_text(encoding="utf-8")), files[0].read_text(encoding="utf-8")

    def approval_unused(self):
        return approval.check(self.approval_path, REF, CO, "case_2_new_ce_only", REV, NOW)


# --- the separate lock ----------------------------------------------------------------------------------------------

class LockTests(unittest.TestCase):
    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_flags_are_hard_coded_false_and_separate(self):
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # unlocked for the CO-0800 dry run only; the create stays locked
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        text = Path(session_readiness.__file__).read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^PRODUCTION_DRY_RUN_SUPPORTED = True\b")
        self.assertRegex(text, r"(?m)^PRODUCTION_RUNNER_SUPPORTED = True  # owner go 2026-10-22")  # unlock window: pinned by test_unlock_state

    def test_dry_run_target_is_locked_with_its_own_reason(self):
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False), \
                self.assertRaises(ProductionLocked) as caught:
            production_dry_run_target({**UNLOCK, **approval.unlock_environ(_record())}, NOW)
        self.assertEqual(caught.exception.reason, "production_dry_run_not_supported")

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_dry_run_flag_never_opens_the_live_target(self):
        env = {**UNLOCK, **approval.unlock_environ(_record())}
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
            target = production_dry_run_target(env, NOW)
            self.assertEqual((target.origin, target.profile_dir_name), (ORIGIN, "backoffice-production-automation"))
            with self.assertRaises(ProductionLocked) as caught:
                production_target(env, NOW)
        self.assertEqual(caught.exception.reason, "production_runner_not_supported")

    def test_runner_flag_never_opens_the_dry_run(self):
        env = {**UNLOCK, **approval.unlock_environ(_record())}
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", True), \
                mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False):
            with self.assertRaises(ProductionLocked) as caught:
                production_dry_run_target(env, NOW)
        self.assertEqual(caught.exception.reason, "production_dry_run_not_supported")

    def test_same_unlock_inputs_apply_before_the_flag(self):
        record = _record()
        good = approval.unlock_environ(record)
        cases = [({}, "production_sessions_flag_missing"),
                 ({"SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1"}, "production_approval_missing"),
                 ({**UNLOCK, **good, "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": "bad"}, "production_approval_missing"),
                 ({**UNLOCK, **{k: v.replace("2026", "2020") for k, v in good.items() if "ISSUED" in k or "EXPIRES" in k},
                   "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": REF}, "production_approval_expired")]
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
            for env, reason in cases:
                with self.subTest(reason=reason), self.assertRaises(ProductionLocked) as caught:
                    production_dry_run_target(env, NOW)
                self.assertEqual(caught.exception.reason, reason)

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_pc_run_uses_the_dry_run_target_only_for_a_dry_run(self):
        harness = Harness(self)
        records = []
        inputs = _inputs(harness, environ={**UNLOCK, **approval.unlock_environ(_record())})
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
            dry = pc.run(inputs, True, lambda _i: {"x": "pass"}, lambda _i: None, lambda _i: None,
                         lambda d: records.append(d) or harness.tmp / "r.json")
            live = pc.run(inputs, False, lambda _i: {"x": "pass"}, lambda _i: None, lambda _i: None,
                          lambda d: records.append(d) or harness.tmp / "r.json", owner_go=True)
        self.assertEqual(dry.outcome, "dry_run_passed")
        self.assertEqual((live.outcome, live.reason), ("refused", "production_locked:production_runner_not_supported"))
        self.assertIsNone(harness.approval_unused())  # a dry run never consumes the approval

    def test_underscore_seams_are_ignored_without_the_test_hook_variable(self):
        harness = Harness(self)
        env = {**UNLOCK, **approval.unlock_environ(_record())}

        def always_open(_environ, _now):
            return production_dry_run_target.__globals__["RunTarget"]("backoffice_production", ORIGIN, "p")
        records = []
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False):
            for hook, expect in ((None, "production_locked:production_dry_run_not_supported"),
                                 ("0", "production_locked:production_dry_run_not_supported"),
                                 ("true", "production_locked:production_dry_run_not_supported"),
                                 ("1", None)):
                inputs = _inputs(harness, environ={**env, **({"SURFACE_ONBOARDING_TEST_HOOKS": hook} if hook else {})})
                inputs = pc.dataclasses.replace(inputs, environ={k: v for k, v in inputs.environ.items()
                                                                 if hook or k != "SURFACE_ONBOARDING_TEST_HOOKS"})
                result = pc.run(inputs, True, lambda _i: {"x": "pass"}, lambda _i: None, lambda _i: None,
                                lambda d: records.append(d) or Path("r"), _production_target_fn=always_open,
                                _approval_check_fn=lambda *a: "approval_missing" if hook != "1" else None)
                with self.subTest(hook=hook):
                    self.assertEqual(result.reason if result.outcome == "refused" else None, expect)

    def test_dry_run_locked_by_default(self):
        harness = Harness(self)
        records = []
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False):
            result = pc.run(_inputs(harness, environ={**UNLOCK, **approval.unlock_environ(_record())}), True,
                            lambda _i: {"x": "pass"}, lambda _i: None, lambda _i: None, lambda d: records.append(d) or Path("r"))
        self.assertEqual(result.reason, "production_locked:production_dry_run_not_supported")


def _record():
    return {"approval_ref": REF, "issued_at": (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (NOW + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")}


def _inputs(harness, **over):
    data = dict(co_reference=CO, route="case_2_new_ce_only", approval_ref=REF, approval_path=harness.approval_path,
                source_revision=REV, source_revision_now=REV, now=NOW, environ=dict(UNLOCK),
                sf_email_domains=(POISON_DOMAIN,), sf_source_consistent=True, prior_account_uuid=None,
                clone_collected_at=NOW - timedelta(hours=1), clone_matches=True, duplicate_found=False,
                live_paid_tenant_count=0, timing_allowed=True, csm_request_recorded=False, sessions_ready=True,
                schema_ok=True, expected_tenant_name="n", expected_domains=(POISON_DOMAIN,))
    data.update(over)
    return pc.CreateInputs(**data)


class CreateModuleTests(unittest.TestCase):
    def test_form_refusal_carries_reason_and_fields(self):
        harness = Harness(self)
        records = []
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
            def fill(_i):
                raise pc.FormRefusal("unexpected_schema", {"text.company_name": "pass"})
            result = pc.run(_inputs(harness, environ={**UNLOCK, **approval.unlock_environ(_record())}), True, fill,
                            lambda _i: None, lambda _i: None, lambda d: records.append(d) or Path("r"))
        self.assertEqual((result.outcome, result.reason), ("refused", "unexpected_schema"))
        self.assertEqual(records[-1]["form_fields_checked"], {"text.company_name": "pass"})

    def test_form_refusal_bad_reason_becomes_form_fill_failed(self):
        self.assertEqual(pc.FormRefusal("Bad Reason!").reason, "form_fill_failed")

    def test_precheck_reason_replaces_only_the_generic_code(self):
        harness = Harness(self)
        env = {**UNLOCK, **approval.unlock_environ(_record())}
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
            gate = lambda i: pc.evaluate_gate(i, production_dry_run_target)
            self.assertEqual(gate(_inputs(harness, environ=env, sessions_ready=False)), "sessions_not_ready")
            self.assertEqual(gate(_inputs(harness, environ=env, sessions_ready=False, precheck_reason="production_session_expired")),
                             "production_session_expired")
            self.assertEqual(gate(_inputs(harness, environ=env, duplicate_found=True, precheck_reason="Not A Code")),
                             "duplicate_in_production")
            self.assertEqual(gate(_inputs(harness, environ=env, precheck_reason="production_session_expired")), None)

    def test_record_keeps_advanced_names_and_states_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pcr.record(tmp, {"co_reference": CO, "outcome": "refused", "refusal_reason": "advanced_control_absent",
                                    "advanced_section": "found",
                                    "advanced_controls": {"aiEnabled": "off", "webDorkingEnabled": "absent",
                                                          "bad name!": "on", "x": "SECRET-VALUE"},
                                    "advanced_mismatches": ["webDorkingEnabled"]}, NOW)
            data = pcr.load(path)
        self.assertEqual(data["advanced_section"], "found")
        self.assertEqual(data["advanced_controls"], {"aiEnabled": "off", "webDorkingEnabled": "absent"})
        self.assertEqual(data["advanced_mismatches"], ["webDorkingEnabled"])
        self.assertNotIn("SECRET", json.dumps(data))


# --- pure logic -----------------------------------------------------------------------------------------------------

class LabelTests(unittest.TestCase):
    def test_whitespace_is_collapsed_but_text_is_exact(self):
        rx = pdr.label_regex("User email domains  (Comma Separated Values)")
        self.assertTrue(rx.search("User email domains (Comma Separated Values)"))
        self.assertTrue(rx.search("User email domains   (Comma Separated Values) *"))
        self.assertFalse(rx.search("User email domain (Comma Separated Values)"))
        self.assertFalse(rx.search("Alternate User email domains (Comma Separated Values)"))
        self.assertFalse(pdr.label_regex("Company name").search("Company primary domain"))
        self.assertTrue(pdr.label_regex("Company name").search("Company name"))
        self.assertEqual(pdr.label_key("  Company   name * "), "Company name")


class MappingTests(unittest.TestCase):
    def plan(self):
        return runner.build_ce_only_fill(make_source(), date(2026, 5, 21))

    def test_dev_plan_maps_onto_production_names(self):
        intended = pdr.map_plan(self.plan(), runner.SELECT_NAMES, runner.EARLY_SELECTS)
        texts = dict(intended.texts)
        self.assertEqual(set(texts), set(pdr.TEXT_LABELS))
        self.assertEqual(texts["User email domains (Comma Separated Values)"], runner.CE_USER_EMAIL_DOMAIN)
        self.assertEqual(texts["Company primary domain"], POISON_DOMAIN)
        self.assertEqual({n for n, _o, _e in intended.selects}, set(pdr.SELECT_NAMES))
        self.assertEqual({n for n, _o, early in intended.selects if early}, {"accountType", "licenseType", "scanningInterval"})
        self.assertEqual({n for n, _t in intended.toggles}, set(pdr.TOGGLE_NAMES))
        self.assertEqual(dict(intended.advanced_targets), {n: False for n in pdr.ADVANCED_NAMES})
        self.assertEqual(dict(intended.toggles)["leakedCredentialsAllowed"], True)
        self.assertEqual(intended.scan_now, False)
        self.assertEqual([k for k, _ in intended.dates], ["license_start", "license_end"])
        self.assertEqual(len(pdr.field_keys(intended)), len(set(pdr.field_keys(intended))))

    def test_unmapped_values_fail_closed(self):
        for edit, code in ((lambda p: p["texts"].update({"Surprise": "x"}), "unmapped_text_label"),
                           (lambda p: p["texts"].pop("Company name"), "text_labels_incomplete"),
                           (lambda p: p["selects"].update({"Mystery": "x"}), "unmapped_select"),
                           (lambda p: p["checkboxes"].update({"newToggle": True}), "unmapped_toggle"),
                           (lambda p: p["checkboxes"].pop("aiEnabled"), "toggles_incomplete"),
                           (lambda p: p.pop("license_end"), "plan_shape_unexpected")):
            plan = self.plan()
            edit(plan)
            with self.subTest(code=code), self.assertRaises(pdr.MappingError) as caught:
                pdr.map_plan(plan, runner.SELECT_NAMES, runner.EARLY_SELECTS)
            self.assertEqual(caught.exception.args[0], code)


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.intended = pdr.map_plan(runner.build_ce_only_fill(make_source(), date(2026, 5, 21)), runner.SELECT_NAMES,
                                     runner.EARLY_SELECTS)

    def reads(self):
        return {"texts": dict(self.intended.texts), "blanks": {label: "" for label in self.intended.blanks},
                "selects": {n: o for n, o, _e in self.intended.selects}, "toggles": dict(self.intended.toggles),
                "dates": {"license_start": True, "license_end": True}, "operator_empty": True, "scan_now": False}

    def test_all_pass_and_each_mismatch_fails_only_its_key(self):
        self.assertEqual(set(pdr.compare_reads(self.intended, self.reads()).values()), {"pass"})
        for edit, key in ((lambda r: r["texts"].update({"Company name": "other"}), "text.company_name"),
                          (lambda r: r["blanks"].update({"Phone number": "123"}), "blank.phone_number"),
                          (lambda r: r["selects"].update({"accountType": "Trial"}), "select.accountType"),
                          (lambda r: r["toggles"].update({"mfaRequired": False}), "toggle.mfaRequired"),
                          (lambda r: r["dates"].update({"license_end": False}), "date.license_end"),
                          (lambda r: r.update({"operator_empty": False}), "operator_account_empty"),
                          (lambda r: r.update({"scan_now": True}), "toggle.scan_now"),
                          (lambda r: r["texts"].pop("Company name"), "text.company_name")):
            reads = self.reads()
            edit(reads)
            out = pdr.compare_reads(self.intended, reads)
            with self.subTest(key=key):
                self.assertEqual({k for k, v in out.items() if v == "fail"}, {key})

    def test_country_alias_override(self):
        reads = self.reads()
        reads["selects"]["accountCountry"] = "United States of America"
        self.assertEqual(pdr.compare_reads(self.intended, reads)["select.accountCountry"], "fail")
        self.assertEqual(pdr.compare_reads(self.intended, reads, {"accountCountry": "United States of America"})
                         ["select.accountCountry"], "pass")


class SchemaTests(unittest.TestCase):
    def sanitized(self, fields=None, **edit):
        form = probe.sanitize_form(form_of(fields if fields is not None else PROBE_FIELDS))
        return form

    def test_recorded_probe_form_is_expected(self):
        self.assertIsNone(pdr.schema_problem(self.sanitized()))

    def test_option_counts_must_equal_the_probe(self):
        self.assertEqual(pdr.EXPECTED_OPTION_COUNTS, {"accountType": 3, "accountCountry": 251, "scanningInterval": 4,
                                                      "leakedCredentialsScanningInterval": 4, "licenseType": 5})
        for index, (name, label, kind, count, am) in enumerate(PROBE_FIELDS):
            if kind == "select":
                fields = list(PROBE_FIELDS)
                fields[index] = (name, label, kind, count + 1, am)
                with self.subTest(name=name):
                    self.assertEqual(pdr.schema_problem(self.sanitized(fields)), "unexpected_schema")

    def test_unknown_required_field_is_refused_but_unknown_optional_is_not(self):
        form = self.sanitized()
        form["fields"].append({"label": "New mandatory thing", "name": "newThing", "type": "text", "required": True,
                               "option_count": None, "data_am": ""})
        self.assertEqual(pdr.schema_problem(form), "unexpected_schema")
        form["fields"][-1]["required"] = False
        self.assertIsNone(pdr.schema_problem(form))

    def test_missing_expected_control_and_unreadable_form(self):
        without_phone = [f for f in PROBE_FIELDS if f[1] != "Phone number"]
        self.assertEqual(pdr.schema_problem(self.sanitized(without_phone)), "unexpected_schema")
        without_date = [f for f in PROBE_FIELDS if f[4] != "AddEditTenantModal-date-startDate"]
        self.assertEqual(pdr.schema_problem(self.sanitized(without_date)), "unexpected_schema")
        for bad in (None, {}, {"fields": []}, "x"):
            self.assertEqual(pdr.schema_problem(bad), "create_form_unavailable")

    def test_advanced_names_are_known_only_after_expansion(self):
        form = self.sanitized()
        form["fields"].append({"label": "", "name": "aiEnabled", "type": "checkbox", "required": True,
                               "option_count": None, "data_am": ""})
        self.assertEqual(pdr.schema_problem(form), "unexpected_schema")
        self.assertIsNone(pdr.schema_problem(form, after_advanced=True))


class AdvancedTests(unittest.TestCase):
    targets = {n: False for n in pdr.ADVANCED_NAMES}

    def test_report_states_and_mismatches(self):
        states = {n: False for n in pdr.ADVANCED_NAMES}
        states.update(aiEnabled=True, automatedDiscoveryEnabled=True, spyCloudEnabled=True)
        report = pdr.advanced_report(True, states, self.targets)
        self.assertEqual(report["advanced_section"], "found")
        self.assertEqual(report["advanced_controls"]["aiEnabled"], "on")
        self.assertEqual(report["advanced_controls"]["fullNucleiScanEnabled"], "off")
        self.assertEqual(report["advanced_controls"]["spyCloudEnabled"], "on")
        self.assertEqual(sorted(report["advanced_mismatches"]), ["aiEnabled", "automatedDiscoveryEnabled"])
        self.assertEqual(pdr.advanced_problem(report), "advanced_mismatch")
        self.assertIn("advanced_mismatch", pdr.KNOWN_REASONS)

    def test_all_off_has_no_problem_and_info_control_never_gates(self):
        states = {n: False for n in pdr.ADVANCED_NAMES}
        states["spyCloudEnabled"] = True  # informational: no Dev target
        report = pdr.advanced_report(True, states, self.targets)
        self.assertEqual(report["advanced_mismatches"], [])
        self.assertIsNone(pdr.advanced_problem(report))

    def test_the_three_payload_off_flags_gate_too(self):
        for name in ("fullNucleiScanEnabled", "authenticatedTestingEnabled", "multipleAttackStacksEnabled"):
            states = {n: False for n in pdr.ADVANCED_NAMES}
            states[name] = True
            report = pdr.advanced_report(True, states, self.targets)
            self.assertEqual(report["advanced_mismatches"], [name])
            self.assertEqual(pdr.advanced_problem(report), "advanced_mismatch")

    def test_absent_control_is_a_mismatch_and_refuses(self):
        states = {n: False for n in pdr.ADVANCED_NAMES}
        states["webDorkingEnabled"] = None
        report = pdr.advanced_report(True, states, self.targets)
        self.assertEqual(report["advanced_controls"]["webDorkingEnabled"], "absent")
        self.assertEqual(report["advanced_mismatches"], ["webDorkingEnabled"])
        self.assertEqual(pdr.advanced_problem(report), "advanced_control_absent")

    def test_no_section_at_all(self):
        report = pdr.advanced_report(False, {}, self.targets)
        self.assertEqual(report["advanced_section"], "not_found")
        self.assertEqual(len(report["advanced_mismatches"]), 9)
        self.assertEqual(pdr.advanced_problem(report), "advanced_section_not_found")


class VerdictTests(unittest.TestCase):
    def rows(self, *rows, total=None):
        return {"status": 200, "total": len(rows) if total is None else total, "rows": list(rows)}

    def row(self, name="Someone Else", domain="other.example", alternates=(), emails=()):
        return {"name": name, "domain": domain, "alternates": list(alternates), "emails": list(emails), "deleted": False}

    def test_search_verdict(self):
        name, domains = "Grupo Patio - CE Only", ("patio.cl",)
        self.assertIsNone(pdr.search_verdict(self.rows(), name, domains))
        self.assertIsNone(pdr.search_verdict(self.rows(self.row()), name, domains))
        for reply, reason in ((self.rows(self.row(name="  grupo   PATIO - ce only ")), "duplicate_in_production"),
                              (self.rows(self.row(domain="PATIO.cl.")), "duplicate_in_production"),
                              (self.rows(self.row(alternates=["patio.cl"])), "duplicate_in_production"),
                              (self.rows(self.row(emails=["patio.cl"])), "duplicate_in_production"),
                              (dict(self.rows(self.row()), total=2), "duplicate_ambiguous"),
                              ({"status": 500, "total": None, "rows": None}, "duplicate_check_unavailable"),
                              ({"status": 200, "total": None, "rows": None}, "duplicate_check_unavailable"),
                              ({"status": 401, "total": None, "rows": None}, "production_session_expired"),
                              (self.rows("junk"), "duplicate_check_unavailable"),
                              (None, "duplicate_check_unavailable")):
            with self.subTest(reply=reply):
                self.assertEqual(pdr.search_verdict(reply, name, domains), reason)

    def test_demo_and_combined_verdict(self):
        clear = self.rows()
        self.assertIsNone(pdr.duplicate_verdict([clear, clear], {"status": 200, "hit": False}, "n", ("d.example",)))
        self.assertEqual(pdr.duplicate_verdict([clear], {"status": 200, "hit": True}, "n", ("d.example",)),
                         "demo_domain_conflict")
        for demo in ({"status": 500, "hit": None}, {"status": 200, "hit": None}, {"status": 200, "hit": "no"}, None):
            self.assertEqual(pdr.duplicate_verdict([clear], demo, "n", ("d.example",)), "duplicate_check_unavailable")
        self.assertEqual(pdr.duplicate_verdict([], {"status": 200, "hit": False}, "n", ("d.example",)),
                         "duplicate_check_unavailable")

    @unittest.skipUnless(shutil.which("node"), "node is needed to run the in-page scripts")
    def test_the_only_in_page_script_left_is_the_service_worker_count(self):
        program = ("const f = (" + pdr.SERVICE_WORKER_JS + ");\n"
                   "Object.defineProperty(globalThis, 'navigator', {value: {serviceWorker: {getRegistrations: async () => [1, 2]}}, configurable: true});\n"
                   "f().then(r => console.log(JSON.stringify(r)));")
        done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"count": 2})

    def test_the_pages_own_table_request_passes_through_the_request_guard(self):
        page = FakePage(search={"x": {"status": 200, "total": 0, "rows": []}})
        guard = pdr.DryRunRequestGuard()
        guard.install(page)
        self.assertEqual(runner._pcr_search(page, "x")["status"], 200)
        self.assertEqual(page.fire("POST", ORIGIN + "/api/v1/backoffice/account/add", "{}"), "aborted")
        self.assertTrue(page.routed[-1].abort_called and not page.routed[-1].continue_called)
        self.assertEqual(guard.api_calls[("POST", pdr.ACCOUNTS_PATH)]["count"], 1)

    def test_revision_pattern(self):
        for good in (REV, "r29", "a.b:c_d-e+f", "x" * 64):
            self.assertTrue(pdr.revision_ok(good), good)
        for bad in ("", "x" * 65, "has space", "semi;colon", "new\nline", "<script>", "a/b", None, 5):
            self.assertFalse(pdr.revision_ok(bad), bad)

    def test_reason_enum(self):
        for code in ("clone_stale", "production_locked_production_dry_run_not_supported", "unexpected_error"):
            self.assertEqual(pdr.known_reason(code), code)
        for bad in ("secret text zzz", "", None, "Clone_Stale", "production_locked_other", 5):
            self.assertEqual(pdr.known_reason(bad), "unexpected_error")
        self.assertEqual(runner._pdr_reason("production_locked:production_approval_missing"),
                         "production_locked_production_approval_missing")

    def test_service_worker_verdict(self):
        self.assertIsNone(pdr.service_worker_verdict({"count": 0}))
        self.assertEqual(pdr.service_worker_verdict({"count": 1}), "service_worker_present")
        for bad in ({"count": None}, {}, None, {"count": "0"}, {"count": True}):
            self.assertEqual(pdr.service_worker_verdict(bad), "service_worker_check_failed")

    def test_values_with_control_characters_are_never_typed(self):
        for bad in ("a\nb", "a\rb", "a\x00b"):
            plan = runner.build_ce_only_fill(make_source(), date(2026, 5, 21))
            plan["texts"]["Company name"] = bad
            with self.assertRaises(pdr.MappingError) as caught:
                pdr.map_plan(plan, runner.SELECT_NAMES, runner.EARLY_SELECTS)
            self.assertEqual(caught.exception.args[0], "value_has_control_character")


class GuardTests(unittest.TestCase):
    def test_request_decision_table(self):
        api = ORIGIN + "/api/v1/backoffice/"
        for method, url, allowed in (
                ("GET", ORIGIN + "/backoffice/tenantManagement", True), ("HEAD", api + "x", True),
                ("GET", ORIGIN + pdr.ACCOUNT_INFO_PATH, True),
                ("GET", "https://cdn.pentera.io/static/app.js", True), ("HEAD", "https://pentera.io/logo.PNG", True),
                ("GET", "https://cdn.pentera.io/api/v1/anything", False), ("GET", "https://evil.example/app.js", False),
                ("GET", "https://pentera.io.evil.example/app.js", False), ("GET", "http://cdn.pentera.io/app.js", False),
                ("GET", "https://fonts.example/font.woff2", False), ("GET", "https://cdn.pentera.io/app.exe", False),
                ("GET", "https://cdn.pentera.io/noextension", False), ("POST", "https://cdn.pentera.io/app.js", False),
                ("POST", api + "getAllDetailedAccounts", True),
                ("POST", api + "getAllDetailedAccounts?x=1", True),
                ("POST", "https://evil.example/api/v1/backoffice/getAllDetailedAccounts", False),
                ("POST", api + "account/add", False), ("POST", api + "account/abc123abc123abc123/edit", False),
                ("PUT", api + "account/abc123abc123abc123/edit", False), ("PATCH", api + "account/add", False),
                ("DELETE", api + "account/abc123abc123abc123", False),
                ("POST", api + "account/abc123abc123abc123/campaign/executions", False),
                ("POST", ORIGIN + "/api/v1/auth/verify", False), ("POST", "https://telemetry.example/x", False)):
            with self.subTest(method=method, url=url):
                self.assertEqual(pdr.request_decision(method, url, VALID_READ_BODY)[0], allowed)

    def test_guard_aborts_account_writes_and_counts_them(self):
        guard, page = pdr.DryRunRequestGuard(), FakePage()
        guard.install(page)
        self.assertEqual(page.fire("POST", ORIGIN + "/api/v1/backoffice/account/add"), "aborted")
        self.assertEqual(page.fire("POST", ORIGIN + "/api/v1/backoffice/account/abc123abc123abc123/edit"), "aborted")
        self.assertEqual(page.fire("POST", "https://telemetry.example/x"), "aborted")
        self.assertEqual(page.fire("POST", ORIGIN + "/api/v1/backoffice/getAllDetailedAccounts", VALID_READ_BODY), "continued")
        self.assertEqual(page.fire("POST", ORIGIN + "/api/v1/backoffice/getAllDetailedAccounts"), "aborted")  # no body: no clearance
        self.assertEqual(page.fire("GET", ORIGIN + "/api/v1/authenticated/getAuthorities"), "continued")
        self.assertEqual(guard.blocked_api_writes(), 3)  # the telemetry POST is blocked but is not a production API write
        self.assertTrue(any(w.endswith("/account/add") for (_m, w) in guard.blocked))
        self.assertEqual(page.fire("GET", "https://tracker.example/pixel"), "aborted")
        self.assertEqual(page.fire("GET", "https://cdn.pentera.io/a.css"), "continued")
        self.assertEqual(guard.blocked_api_writes(), 3)  # cross-origin GETs are counted but are not API writes

    def test_click_log_allows_only_the_allowlist_and_never_confirm_submit_save(self):
        clicked = []
        locator = SimpleNamespace(click=lambda **kw: clicked.append(kw))
        log = pdr.ClickLog()
        log.click(pdr.CLICK_OPEN_CREATE_FORM, locator, "Add Account", timeout=1)
        log.click(pdr.CLICK_CANCEL_CREATE_FORM, locator, "Cancel")
        log.click(pdr.CLICK_EXPAND_ADVANCED, locator, "Advanced options")
        log.click(pdr.CLICK_SET_TOGGLE, locator, "mfaRequired")
        self.assertEqual(len(clicked), 4)
        with self.assertRaises(pdr.ClickRefused):
            log.click(pdr.CLICK_SET_TOGGLE, locator, "mfaRequired", force=True)
        self.assertEqual(pdr.CREATE_OPEN_LABELS, frozenset({"add account"}))
        for generic in ("Add", "Create", "Create Account", "New Account"):
            with self.assertRaises(pdr.ClickRefused):
                log.click(pdr.CLICK_OPEN_CREATE_FORM, locator, generic)
        self.assertEqual(len(clicked), 4)
        for kind, label in ((pdr.CLICK_CANCEL_CREATE_FORM, "Confirm"), (pdr.CLICK_SET_TOGGLE, "Submit"),
                            (pdr.CLICK_EXPAND_ADVANCED, "  save  "), (pdr.CLICK_OPEN_CREATE_FORM, "Confirm"),
                            (pdr.CLICK_OPEN_CREATE_FORM, "Delete everything"), ("confirm_create", "Cancel"),
                            ("submit", "Add Account")):
            with self.subTest(kind=kind, label=label), self.assertRaises(pdr.ClickRefused):
                log.click(kind, locator, label)
        self.assertEqual(len(clicked), 4)


# --- end to end against the fake page ----------------------------------------------------------------------------------

class FlowTests(unittest.TestCase):
    def test_locked_by_default_does_nothing_external(self):
        h = Harness(self)
        out = h.run(supported=False)
        self.assertEqual(out["result"], "production_locked_production_dry_run_not_supported")
        self.assertEqual((h.loaded, h.opened), (0, 0))
        data, _text = h.record()
        self.assertEqual((data["outcome"], data["refusal_reason"]), ("refused", "production_locked_production_dry_run_not_supported"))
        self.assertIsNone(h.approval_unused())

    def test_every_env_gate_refuses_before_any_read(self):
        h = Harness(self)
        for env, reason in (({}, "production_locked_production_sessions_flag_missing"),
                            ({"SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1"}, "production_locked_production_approval_missing"),
                            ({**UNLOCK, "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": "other-ref"},
                             "production_locked_production_approval_missing")):
            with self.subTest(reason=reason):
                self.assertEqual(h.run(environ=env)["result"], reason)
        self.assertEqual((h.loaded, h.opened), (0, 0))

    def test_passing_dry_run_fills_verifies_cancels_and_never_confirms(self):
        h = Harness(self)
        out = h.run()
        self.assertEqual((out["result"], out["outcome"]), ("dry_run_passed", "dry_run_passed"))
        self.assertEqual(out["production_write"], "not_performed")
        page = h.page
        self.assertEqual(page.clicks[0], "Add Account")
        self.assertEqual(page.clicks[-1], "Cancel")
        self.assertNotIn("Confirm", page.clicks)
        self.assertFalse(page.form_open)
        self.assertIn("Advanced options", page.clicks)
        for name in pdr.ADVANCED_NAMES:  # advanced controls are read, never clicked
            self.assertNotIn(name, page.clicks)
        self.assertEqual(set(page.fetches), {("POST", pdr.ACCOUNTS_PATH)})  # only the page's own, rewritten table requests
        values = page.table_values
        self.assertEqual((values.count(POISON_NAME + " - CE Only"), values.count(POISON_DOMAIN)), (1, 1))  # name and domain
        self.assertEqual(values.count(runner.PDR_SESSION_CHECK_VALUE), 1)  # before the form only (no read while it is open)
        self.assertEqual(page.reloads, 1)  # only the first read reloads; the later ones use the sort header
        self.assertEqual((page.dispatches, page.reads_with_form_open), (0, 0))
        data, text = h.record()
        self.assertEqual(data["outcome"], "dry_run_passed")
        self.assertTrue(data["form_fields_checked"])
        self.assertEqual(set(data["form_fields_checked"].values()), {"pass"})
        self.assertIn("text.company_name", data["form_fields_checked"])
        self.assertEqual(data["advanced_section"], "found")
        self.assertEqual(set(data["advanced_controls"]), {*pdr.ADVANCED_NAMES, "spyCloudEnabled"})
        self.assertEqual(data["advanced_mismatches"], [])
        self.assertEqual(data["redactions"], 0)
        for value in (POISON_NAME, POISON_DOMAIN, "pentera.io", "zetaquux", "milton", "stevenson"):
            self.assertNotIn(value.casefold(), (text + json.dumps(out)).casefold())
        self.assertIsNone(h.approval_unused())  # the approval was not consumed
        self.assertEqual(page.fire("POST", ORIGIN + "/api/v1/backoffice/account/add"), "aborted")

    def test_typed_values_reach_the_form_with_whitespace_tolerant_labels(self):
        h = Harness(self, text_labels={"User email domains (Comma Separated Values)": "User email domains  (Comma Separated Values)"})
        self.assertEqual(h.run()["result"], "dry_run_passed")
        self.assertEqual(h.page.texts["Company name"].value, POISON_NAME + " - CE Only")
        self.assertEqual(h.page.texts["Company primary domain"].value, POISON_DOMAIN)
        self.assertEqual(h.page.texts["User email domains  (Comma Separated Values)"].value, "pentera.io")
        self.assertEqual(h.page.selects["accountType"].value, "Customer")
        self.assertEqual(h.page.selects["leakedCredentialsScanningInterval"].value, "Weekly")
        self.assertTrue(h.page.boxes["leakedCredentialsAllowed"].checked)
        self.assertFalse(h.page.boxes["phishingEnabled"].checked)

    def test_gate_refusals_open_no_browser(self):
        cases = []
        h = Harness(self)
        cases.append((h, dict(revision="other-rev"), "approval_revision_drift"))
        cases.append((Harness(self), dict(clone=lambda *_a: {"collected_at": NOW - timedelta(hours=7), "clone_matches": True,
                                                              "duplicate_found": False, "live_paid_tenant_count": 0}), "clone_stale"))
        cases.append((Harness(self), dict(clone=lambda *_a: {}), "clone_missing"))
        cases.append((Harness(self), dict(clone=lambda *_a: {"collected_at": NOW, "clone_matches": True,
                                                              "duplicate_found": True, "live_paid_tenant_count": 0}),
                      "duplicate_in_production"))
        cases.append((Harness(self), dict(clone=lambda *_a: {"collected_at": NOW, "clone_matches": True,
                                                              "duplicate_found": False, "live_paid_tenant_count": 2}),
                      "multiple_live_paid_tenants"))
        cases.append((Harness(self), dict(source=make_source(salesforce_id_present=True)), "salesforce_prior_uuid_present"))
        for harness, kwargs, reason in cases:
            with self.subTest(reason=reason):
                out = harness.run(**kwargs)
                self.assertEqual((out["result"], out["outcome"]), (reason, "refused"))
                self.assertEqual(harness.opened, 0)
                self.assertEqual(harness.record()[0]["refusal_reason"], reason)
                self.assertIsNone(harness.approval_unused())

    def test_wrong_co_or_missing_approval_refuses(self):
        h = Harness(self)
        out = h.run(source=make_source(reference="CO-0001"), reference="CO-0001")
        self.assertEqual(out["result"], "approval_co_mismatch")
        self.assertEqual(h.opened, 0)
        other = Harness(self)
        other.approval_path.unlink()
        self.assertEqual(other.run()["result"], "production_locked_production_approval_missing")
        self.assertEqual(other.opened, 0)

    def test_source_problems_refuse_with_a_code(self):
        h = Harness(self)

        def boom(_ref):
            raise runner.RouteMismatch()
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
            line = runner.run_production_dry_run(CO, REV, environ=dict(UNLOCK), now=NOW, records_parent=h.records,
                                                 approval_path=h.approval_path, load_source=boom, clone_facts=good_clone,
                                                 open_page=h.open_page, org_probe=ORG_PROBE)
        self.assertEqual(json.loads(line)["result"], "ce_route_mismatch")
        self.assertEqual(h.opened, 0)

    def test_invalid_arguments(self):
        h = Harness(self)
        for reference, revision in (("bad", REV), (CO, ""), (CO, "x" * 200)):
            line = runner.run_production_dry_run(reference, revision, environ=dict(UNLOCK), now=NOW,
                                                 records_parent=h.records, approval_path=h.approval_path)
            self.assertEqual(json.loads(line)["result"], "production_dry_run_arguments_invalid")
        self.assertFalse(h.records.exists())

    def test_session_failures_refuse_before_any_form_or_search(self):
        check = runner.PDR_SESSION_CHECK_VALUE
        refused = {"status": 0}
        cases = (  # (page kwargs, reason): the page's own start-up calls, or the status of its own table reply
            (dict(session={pdr.AUTHORITIES_PATH: {"status": 401}, pdr.ACCOUNT_INFO_PATH: {"status": 200}}), "production_session_expired"),
            (dict(session={pdr.AUTHORITIES_PATH: {"status": 403}, pdr.ACCOUNT_INFO_PATH: {"status": 200}}), "production_session_expired"),
            (dict(session={pdr.AUTHORITIES_PATH: {"status": 200}, pdr.ACCOUNT_INFO_PATH: {"status": 401}}), "production_session_expired"),
            (dict(session={pdr.AUTHORITIES_PATH: {"status": 500}, pdr.ACCOUNT_INFO_PATH: {"status": 200}}), "production_session_not_ready"),
            (dict(search={check: {"status": 401, "total": None, "rows": None}}), "production_session_expired"),
            (dict(search={check: {"status": 403, "total": None, "rows": None}}), "production_session_expired"),
            (dict(search={check: {"status": 500, "total": None, "rows": None}}), "production_session_not_ready"),
            (dict(search={check: refused}), "production_session_not_ready"))  # the page never answered its own request
        for kwargs, reason in cases:
            h = Harness(self, **kwargs)
            with self.subTest(reason=reason, kwargs=sorted(kwargs)):
                out = h.run()
                self.assertEqual(out["result"], reason)
                self.assertEqual(h.page.clicks, [])
                self.assertEqual(set(h.page.table_values), {check})  # no name or domain search ran
                self.assertEqual(h.record()[0]["refusal_reason"], reason)

    def test_duplicate_demo_and_doubt_refuse_before_the_form(self):
        name = POISON_NAME + " - CE Only"
        row = {"name": name, "domain": "x.example", "alternates": [], "emails": [], "deleted": False}
        for kwargs, reason in (
                (dict(search={name: {"status": 200, "total": 1, "rows": [row]}}), "duplicate_in_production"),
                (dict(search={POISON_DOMAIN: {"status": 200, "total": 1, "rows": [dict(row, name="Other", domain=POISON_DOMAIN)]}}),
                 "duplicate_in_production"),
                (dict(search={name: {"status": 200, "total": 5, "rows": [row]}}), "duplicate_ambiguous"),
                (dict(search={name: {"status": 500, "total": None, "rows": None}}), "duplicate_check_unavailable"),
                (dict(demo={"status": 200, "hit": True, "count": 3}), "demo_domain_conflict"),
                (dict(demo={"status": 404, "hit": None, "count": None}), "duplicate_check_unavailable")):
            h = Harness(self, **kwargs)
            with self.subTest(reason=reason):
                self.assertEqual(h.run()["result"], reason)
                self.assertEqual(h.page.clicks, [])
                self.assertEqual(h.record()[0]["refusal_reason"], reason)
                self.assertIsNone(h.approval_unused())

    def test_unexpected_schema_refuses_and_still_cancels(self):
        bad = list(PROBE_FIELDS)
        bad[1] = ("accountType", "", "select", 4, "")
        extra = list(PROBE_FIELDS) + [("brandNew", "Brand new", "text", None, "")]
        for fields in (bad, [f for f in PROBE_FIELDS if f[1] != "Job title"]):
            h = Harness(self, form_fields=fields)
            with self.subTest(len=len(fields)):
                self.assertEqual(h.run()["result"], "unexpected_schema")
                self.assertEqual(h.page.clicks, ["Add Account", "Cancel"])
                self.assertFalse(h.page.form_open)
        required = form_of(extra)
        required["fields"][-1]["required"] = True
        h = Harness(self, form_fields=extra)
        original = h.page.evaluate

        def evaluate(script, arg=None):
            out = original(script, arg)
            if script == probe.FORM_JS and isinstance(out, dict):
                out["fields"][-1]["required"] = True
            return out
        h.page.evaluate = evaluate
        self.assertEqual(h.run()["result"], "unexpected_schema")
        self.assertEqual(h.page.clicks[-1], "Cancel")
        self.assertNotIn("Confirm", h.page.clicks)

    def test_advanced_switches_that_are_on_are_set_off_by_a_plain_click(self):
        # Live 2026-10-18 (CO-0798 dry run #2): production defaults three advanced switches ON; the CE-only target is OFF.
        expected = Harness(self).run()["result"]
        states = {n: False for n in pdr.ADVANCED_NAMES}
        states.update(automatedDiscoveryEnabled=True, subDomainsReconEnabled=True, webDictionaryBruteForceEnabled=True)
        h = Harness(self, advanced=states)
        out = h.run()
        self.assertEqual(out["result"], expected)
        data, _ = h.record()
        self.assertEqual(data["advanced_mismatches"], [])
        self.assertEqual(set(data["advanced_controls"][n] for n in pdr.ADVANCED_NAMES), {"off"})
        self.assertEqual(sorted(c for c in h.page.clicks if c in pdr.ADVANCED_NAMES),
                         ["automatedDiscoveryEnabled", "subDomainsReconEnabled", "webDictionaryBruteForceEnabled"])
        self.assertNotIn("spyCloudEnabled", h.page.clicks)  # informational: never changed
        self.assertNotIn("Confirm", h.page.clicks)

    def test_advanced_switches_already_off_are_not_clicked(self):
        h = Harness(self)
        h.run()
        self.assertFalse([c for c in h.page.clicks if c in pdr.ADVANCED_NAMES or c == "spyCloudEnabled"])

    def test_an_advanced_switch_that_cannot_be_set_refuses_with_its_stage(self):
        on = {**{n: False for n in pdr.ADVANCED_NAMES}, "automatedDiscoveryEnabled": True}
        for kwargs, stage in ((dict(sticky={"automatedDiscoveryEnabled"}), "state"),
                              (dict(hit_fail={"automatedDiscoveryEnabled"}), "hit_test")):
            with self.subTest(stage=stage):
                h = Harness(self, advanced=on, **kwargs)
                self.assertEqual(h.run()["result"], "toggle_click_failed")
                fields = h.record()[0]["form_fields_checked"]
                self.assertEqual(fields.get(f"toggle.automatedDiscoveryEnabled.stage_{stage}"), "fail")
                self.assertEqual(h.page.clicks[-1], "Cancel")
                self.assertNotIn("Confirm", h.page.clicks)
                if stage == "hit_test":
                    self.assertNotIn("automatedDiscoveryEnabled", h.page.clicks)  # never clicked when covered

    def test_advanced_absence_refuses(self):

        missing = Harness(self, advanced={n: False for n in pdr.ADVANCED_NAMES if n != "fullNucleiScanEnabled"})
        out = missing.run()
        self.assertEqual(out["result"], "advanced_control_absent")
        data, _ = missing.record()
        self.assertEqual(data["advanced_controls"]["fullNucleiScanEnabled"], "absent")
        self.assertEqual(data["advanced_mismatches"], ["fullNucleiScanEnabled"])
        self.assertEqual(missing.page.clicks[-1], "Cancel")

        nosection = Harness(self, advanced_button=False)
        self.assertEqual(nosection.run()["result"], "advanced_section_not_found")
        self.assertEqual(nosection.record()[0]["advanced_section"], "not_found")

    def test_a_value_that_does_not_read_back_fails_verification(self):
        h = Harness(self, mangle=lambda value: value + "!")
        out = h.run()
        self.assertEqual(out["result"], "fill_value_mismatch")  # the immediate read-back inside the fill
        self.assertEqual(h.page.clicks[-1], "Cancel")
        self.assertNotIn("Confirm", h.page.clicks)

    def test_verification_marks_the_failing_control_only(self):
        h = Harness(self)
        h.page.texts["Phone number"].value = "5551234"  # a pre-filled blank control: stays, verification fails
        h.page.texts["Phone number"].mangle = lambda _v: "5551234"
        out = h.run()
        self.assertEqual(out["result"], "form_verification_failed")
        data, _ = h.record()
        self.assertEqual({k for k, v in data["form_fields_checked"].items() if v == "fail"}, {"blank.phone_number"})
        self.assertEqual(h.page.clicks[-1], "Cancel")

    def test_an_attempted_account_write_is_blocked_and_refuses(self):
        h = Harness(self, write_on_select=True)
        out = h.run()
        self.assertEqual(out["result"], "write_request_blocked")
        writes = [r for r in h.page.routed if r.request.url.endswith("/account/add")]
        self.assertTrue(writes)
        self.assertTrue(all(r.abort_called and not r.continue_called for r in writes))
        self.assertEqual(h.page.clicks[-1], "Cancel")
        self.assertNotIn("Confirm", h.page.clicks)
        # Live 2026-10-18 (dry run #5): the record names WHICH request was blocked (method and path template only)
        data, text = h.record()
        self.assertEqual(data["blocked_requests"], ["POST /api/v1/backoffice/account/add"])
        self.assertEqual(data["redactions"], 0)

    def refreshing(self, h, *extra_posts):
        original = h.page._open
        routes = []

        def open_and_refresh():  # the page sends its own unfiltered table request while the form opens (live dry run #6)
            original()
            routes.append(h.page.fire("POST", ORIGIN + pdr.ACCOUNTS_PATH, pot.PAGE_DEFAULT_BODY))
            for url in extra_posts:
                h.page.fire("POST", ORIGIN + url, "{}")
        h.page._open = open_and_refresh
        return routes

    def test_the_pages_own_table_refresh_is_aborted_but_is_not_a_write_attempt(self):
        h = Harness(self)
        outcomes = self.refreshing(h)
        self.assertEqual(h.run()["result"], "dry_run_passed")
        self.assertEqual(outcomes, ["aborted"])  # still never sent (not a validated search body)
        self.assertEqual(h.page.reads_with_form_open, 0)

    def test_a_real_write_next_to_a_table_refresh_still_refuses_and_names_only_the_write(self):
        h = Harness(self)
        self.refreshing(h, "/api/v1/backoffice/account/add")
        self.assertEqual(h.run()["result"], "write_request_blocked")
        self.assertEqual(h.record()[0]["blocked_requests"], ["POST /api/v1/backoffice/account/add"])

    def test_the_guard_counts_every_blocked_api_write_but_a_table_shaped_refresh(self):
        guard = pdr.DryRunRequestGuard()
        page = FakePage()
        guard.install(page)
        table = ORIGIN + pdr.ACCOUNTS_PATH
        self.assertEqual(page.fire("POST", table, pot.PAGE_DEFAULT_BODY), "aborted")  # the page's own load: aborted
        self.assertEqual((guard.blocked_api_writes(), guard.blocked_api_write_names()), (0, []))
        for url, body in ((table, json.dumps({"tableServerData": {"offset": 0, "items_per_page": 1, "filters": {"delete": True}}})),
                          (table + "?op=x", pot.PAGE_DEFAULT_BODY),
                          (table, json.dumps({"tableServerData": {"offset": 0, "items_per_page": 1, "projection": ["x" * 30000]}})),
                          (table, json.dumps({"tableServerData": {"offset": 0, "items_per_page": 1, "delete": True}})),
                          (table, json.dumps({"tableServerData": {"offset": 0, "items_per_page": 1}, "action": "x"})),
                          (table, None), (table, "not json"),
                          (ORIGIN + "/api/v1/backoffice/account/add", "{}")):
            self.assertEqual(page.fire("POST", url, body), "aborted")
        self.assertEqual(guard.blocked_api_writes(), 8)  # nested write-ish, query string, oversize, write-ish key,
        # extra top level, no body, junk, account/add (review nits 2026-10-18: only a clean table-shaped load is a refresh)
        self.assertEqual(guard.blocked_api_write_names(), ["POST /api/v1/backoffice/account/add", "POST " + pdr.ACCOUNTS_PATH])

    def test_a_session_expired_before_the_form_refuses_without_opening_it(self):
        # 2026-10-18: no read runs inside the open form any more; the session is judged right before it opens.
        h = Harness(self, search={runner.PDR_SESSION_CHECK_VALUE: {"status": 401, "total": None, "rows": None}})
        self.assertEqual(h.run()["result"], "production_session_expired")
        self.assertNotIn("Add Account", h.page.clicks)
        self.assertEqual(h.page.reads_with_form_open, 0)

    def test_form_that_does_not_cancel_is_refused(self):
        h = Harness(self)
        h.page._close = lambda: None
        h.page.keyboard = SimpleNamespace(press=lambda _k: None)
        said = []
        with mock.patch("builtins.print", side_effect=lambda *a, **k: said.append(" ".join(map(str, a)))):
            out = h.run()
        self.assertTrue(any("FORM LEFT OPEN" in line for line in said))
        self.assertEqual(out["result"], "form_left_open")
        self.assertEqual(h.record()[0]["refusal_reason"], "form_left_open")

    def test_no_control_with_confirm_submit_save_semantics_is_ever_clicked(self):
        for kwargs in ({}, dict(mangle=lambda v: v + "!"), dict(write_on_select=True), dict(advanced_button=False),
                       dict(session={pdr.AUTHORITIES_PATH: {"status": 401}, pdr.ACCOUNT_INFO_PATH: {"status": 200}})):
            h = Harness(self, **kwargs)
            h.run()
            lowered = [str(c).casefold() for c in h.page.clicks]
            self.assertFalse([c for c in lowered if any(w in c for w in ("confirm", "submit", "save"))], kwargs)

    def test_toggle_hit_test_failure_refuses_without_clicking_it(self):
        h = Harness(self, hit_fail={"leakedCredentialsAllowed"})
        out = h.run()
        self.assertEqual(out["result"], "toggle_click_failed")
        data, _ = h.record()
        self.assertEqual(data["form_fields_checked"], {"toggle.leakedCredentialsAllowed": "fail",
                                                       "toggle.leakedCredentialsAllowed.stage_hit_test": "fail"})
        self.assertNotIn("leakedCredentialsAllowed", h.page.clicks)
        self.assertEqual(h.page.clicks[-1], "Cancel")

    def test_a_toggle_below_the_fold_is_scrolled_into_view_before_its_hit_test(self):
        # Live 2026-10-18 (CO-0798 dry run): refused toggle_click_failed on the first toggle of the long form; the hit
        # test ran on a control outside the viewport, where elementFromPoint finds nothing (the Confirm path scrolls first).
        expected = Harness(self).run()["result"]
        h = Harness(self, below_fold={"leakedCredentialsAllowed"})
        out = h.run()
        self.assertEqual(out["result"], expected)
        self.assertNotEqual(out["result"], "toggle_click_failed")
        self.assertIn("leakedCredentialsAllowed", h.page.clicks)

    def test_no_table_read_runs_while_the_form_is_open(self):
        # Live 2026-10-18 (CO-0798 dry runs #3/#4): no sort header is ever found, so a read with the form open cannot work;
        # owner decision: every read runs with the form closed, right before it opens, and none while it is open.
        h = Harness(self)
        h.page.dispatch_ignored = True  # the live symptom no longer matters: nothing is read with the form open
        self.assertEqual(h.run()["result"], "dry_run_passed")
        self.assertEqual((h.page.reads_with_form_open, h.page.dispatches), (0, 0))

    def test_the_reader_refuses_a_read_requested_with_the_form_open(self):
        page = FakePage(search={"x": {"status": 200, "total": 0, "rows": []}})
        page.form_open = True
        reader = runner.ProductionPageReader(page)
        self.assertEqual(reader.table("x", form_open=True), (None, None))
        self.assertEqual(reader.last_read, {"form_open_read_refused": "fail"})
        self.assertEqual((page.dispatches, page.reloads, page.header_clicks, page.reads_with_form_open), (0, 0, 0, 0))

    def test_toggle_click_failure_never_retries_with_force(self):
        h = Harness(self, click_fail={"leakedCredentialsAllowed"})  # Ctl.click asserts there is no force=True retry
        out = h.run()
        self.assertEqual(out["result"], "toggle_click_failed")
        self.assertEqual(h.record()[0]["form_fields_checked"].get("toggle.leakedCredentialsAllowed.stage_click"), "fail")
        self.assertNotIn("Confirm", h.page.clicks)
        self.assertEqual(h.page.clicks[-1], "Cancel")
        self.assertNotIn("force=True", _glue_source())

    def test_service_worker_refuses_before_any_interaction(self):
        for count in (1, 3):
            h = Harness(self, sw_count=count)
            out = h.run()
            self.assertEqual(out["result"], "service_worker_present")
            self.assertEqual((h.page.clicks, h.page.fetches), ([], []))
            data, _ = h.record()
            self.assertEqual(set(data) - {"co_reference", "outcome", "recorded_at", "redactions", "route",
                                          "approval_ref", "refusal_reason", "salesforce_revision"}, set())

    def test_service_worker_check_that_fails_refuses(self):
        h = Harness(self)
        original = h.page.evaluate

        def evaluate(script, arg=None):
            if script == pdr.SERVICE_WORKER_JS:
                raise RuntimeError("no page")
            return original(script, arg)
        h.page.evaluate = evaluate
        self.assertEqual(h.run()["result"], "service_worker_check_failed")
        self.assertEqual(h.page.clicks, [])

    def test_no_enter_or_newline_is_ever_sent(self):
        h = Harness(self)  # FakePage._press and Ctl.fill assert on Enter and on newlines
        self.assertEqual(h.run()["result"], "dry_run_passed")
        self.assertFalse({"Enter", "NumpadEnter"} & set(h.page.keys))
        source = _glue_source()
        self.assertNotIn("Enter", source)
        self.assertNotIn("press_sequentially", source)

    def test_time_is_rejudged_with_a_fresh_clock_at_the_end(self):
        for later, reason in ((NOW + timedelta(days=8), "approval_expired_during_run"),
                              (NOW + timedelta(hours=6), "clone_stale_during_run"),
                              (NOW - timedelta(hours=3), "approval_expired_during_run")):  # before the approval was issued
            h = Harness(self)
            with self.subTest(reason=reason):
                out = h.run(clock=lambda later=later: later)
                self.assertEqual((out["result"], out["outcome"]), (reason, "refused"))
                self.assertEqual(h.record()[0]["refusal_reason"], reason)
                self.assertEqual(h.page.clicks[-1], "Cancel")
                self.assertIsNone(h.approval_unused())
        ok = Harness(self)
        self.assertEqual(ok.run(clock=lambda: NOW + timedelta(minutes=20))["result"], "dry_run_passed")

    def test_only_the_exact_add_account_button_is_clicked(self):
        h = Harness(self, add_label="Add")
        out = h.run()
        self.assertEqual(out["result"], "create_form_unavailable")
        self.assertEqual(h.page.clicks, [])
        for generic in ('"Create"', '"Add"', "'Add'", "'Create'"):
            self.assertNotIn(generic, _glue_source())

    def test_form_is_closed_by_escape_when_cancel_does_not_work(self):
        h = Harness(self)
        h.page._close = lambda: None
        self.assertEqual(h.run()["result"], "dry_run_passed")
        self.assertIn("Escape", h.page.keys)

    def test_navigation_or_missing_add_button_does_not_count_as_closed(self):
        h = Harness(self)

        def navigate_away():
            h.page.form_open = False
            h.page.url = ORIGIN + "/login"
        h.page._close = navigate_away
        h.page.keyboard = SimpleNamespace(press=lambda _k: None)
        self.assertEqual(h.run()["result"], "form_left_open")
        self.assertFalse(runner._pdr_form_closed(SimpleNamespace(
            url=ORIGIN + "/backoffice/tenantManagement", evaluate=lambda _s: (_ for _ in ()).throw(RuntimeError("crash")))))

    def test_unknown_errors_become_unexpected_error_never_their_text(self):
        h = Harness(self)

        def boom(_ref):
            raise RuntimeError("secret text zzz")
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
            line = runner.run_production_dry_run(CO, REV, environ=dict(UNLOCK), now=NOW, records_parent=h.records,
                                                 approval_path=h.approval_path, load_source=boom, clone_facts=good_clone,
                                                 open_page=h.open_page, org_probe=ORG_PROBE)
        self.assertEqual(json.loads(line)["result"], "unexpected_error")
        data, text = h.record()
        self.assertEqual(data["refusal_reason"], "unexpected_error")
        self.assertNotIn("zzz", text + line)

    def test_invalid_revision_is_refused_before_anything(self):
        h = Harness(self)
        for bad in ("two words", "x" * 65, "a;b", "line\nbreak"):
            line = runner.run_production_dry_run(CO, bad, environ=dict(UNLOCK), now=NOW, records_parent=h.records,
                                                 approval_path=h.approval_path, load_source=h.load_source)
            self.assertEqual(json.loads(line)["result"], "production_dry_run_arguments_invalid")
        self.assertEqual(h.loaded, 0)

    def test_finish_line_is_checked_for_secret_shaped_text(self):
        clean = SimpleNamespace(outcome="dry_run_passed", reason="", record_path=Path("CO-0797-20261016T120000Z.json"),
                                record_error=None)
        self.assertEqual(json.loads(runner._pdr_finish_line(clean))["result"], "dry_run_passed")
        for name in ("owner@example.com.json", "0123456789abcdef0123456789abcdef.json"):
            leaky = SimpleNamespace(outcome="dry_run_passed", reason="", record_path=Path(name), record_error=None)
            self.assertEqual(json.loads(runner._pdr_finish_line(leaky))["result"], "dry_run_line_not_redacted")
        odd = SimpleNamespace(outcome="refused", reason="owner@example.com said no", record_path=None, record_error=None)
        self.assertEqual(json.loads(runner._pdr_finish_line(odd))["result"], "unexpected_error")

    def test_confirm_and_readback_are_not_implemented(self):
        for fn in (runner._pdr_click_confirm, runner._pdr_read_back):
            with self.assertRaises(NotImplementedError):
                fn(None)
        glue = _glue_source()
        self.assertNotIn('name="Confirm"', glue)
        self.assertNotRegex(glue, r"\.click\([^)]*Confirm")
        self.assertNotIn("account/add", glue.replace("docs", ""))

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_live_create_stays_locked(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # unlocked for the CO-0800 dry run only; the create stays locked
        h = Harness(self)
        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True):
            records = []
            result = pc.run(_inputs(h, environ={**UNLOCK, **approval.unlock_environ(_record())}), False,
                            lambda _i: {"x": "pass"}, runner._pdr_click_confirm, runner._pdr_read_back,
                            lambda d: records.append(d) or Path("r"), owner_go=True, prior_records=[
                                {"co_reference": CO, "approval_ref": REF, "outcome": "dry_run_passed"}])
        self.assertEqual(result.reason, "production_locked:production_runner_not_supported")


def _glue_source():
    text = Path(runner.__file__).read_text(encoding="utf-8")
    start = text.index("# --- Production CE-only DRY RUN")
    return text[start:text.index("def main() -> int:")]


class ConfirmPreflightTests(unittest.TestCase):
    """The dry run runs the locate / enabled / scroll / hit-test checks of Confirm (never clicking it) so a NOT_CLICKED after
    the approval is consumed cannot first appear in the live run."""

    def attempt(self, **flags):
        h = Harness(self)
        for key, value in flags.items():
            setattr(h.page, key, value)
        return h, h.run()

    def test_a_usable_confirm_passes_and_is_scrolled_into_view_but_never_clicked(self):
        h, result = self.attempt()
        self.assertEqual(result["result"], "dry_run_passed")
        self.assertGreaterEqual(h.page.scrolled, 1)
        self.assertNotIn("Confirm", h.page.clicks)

    def test_disabled_covered_or_unreadable_confirm_refuses_the_dry_run(self):
        for flags in ({"confirm_enabled": False}, {"confirm_hit": False}, {"confirm_hit": None}):
            h, result = self.attempt(**flags)
            self.assertEqual(result["result"], "confirm_preflight_failed", flags)
            self.assertNotIn("Confirm", h.page.clicks)
            self.assertFalse(h.page.form_open)


class MainTests(unittest.TestCase):
    def test_flag_dispatches_and_is_exclusive(self):
        with mock.patch.object(runner, "run_production_dry_run", return_value='{"result":"x"}') as ran, \
                mock.patch.object(runner.sys, "argv", ["runner", "--production-dry-run", "--co", CO, "--revision", REV]):
            self.assertEqual(runner.main(), 0)
        ran.assert_called_once_with(CO, REV, route="case_2_new_ce_only")  # the default route stays CE-only
        with mock.patch.object(runner, "run_production_dry_run", return_value='{"result":"x"}') as ran, \
                mock.patch.object(runner.sys, "argv", ["runner", "--production-dry-run", "--co", CO, "--revision", REV,
                                                       "--route", "case_3_combined_baseline"]):
            self.assertEqual(runner.main(), 0)
        ran.assert_called_once_with(CO, REV, route="case_3_combined_baseline")  # owner decision 2026-10-20
        for extra in ([], ["--co", CO], ["--co", CO, "--revision", REV, "--dry-run"],
                      ["--co", CO, "--revision", REV, "--confirm-write"],
                      ["--co", CO, "--revision", REV, "--route", "case_1_new_surface_only"],
                      ["--co", CO, "--revision", REV, "--production-probe"]):
            with mock.patch.object(runner.sys, "argv", ["runner", "--production-dry-run", *extra]), \
                    mock.patch.object(runner, "run_production_dry_run") as ran, self.assertRaises(SystemExit):
                runner.main()
            ran.assert_not_called()

    def test_dev_dry_run_flag_is_unchanged(self):
        with mock.patch.object(runner, "run", return_value="dry_run_passed") as ran, \
                mock.patch.object(runner.sys, "argv", ["runner", "--dry-run", "--co", CO, "--revision", REV]):
            self.assertEqual(runner.main(), 0)
        ran.assert_called_once()


if __name__ == "__main__":
    unittest.main()
