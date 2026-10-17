"""Tests for the pure live-create building blocks (docs/53): the one-create request guard, the one-Confirm click log, the
confirm click, the live duplicate verdict and the bounded readback. Fakes only: no browser, network, Salesforce or BackOffice."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from integration.onboarding import production_contract as contract
from integration.onboarding import production_create_live as live
from integration.onboarding import production_dry_run as pdr

ORIGIN = "https://app.pentera.io"
CREATE_URL = ORIGIN + live.CREATE_PATH
NAME = "Acme Test - CE Only"
DOMAIN = "acme-test.example"
UUID = "0123456789abcdef0123456789abcdef"
ROW_ID = "0123456789abcdef01234567"
INTENDED = {"tenant_name": NAME, "primary_domain": DOMAIN, "email_domain": "pentera.io", "country_code": "CL",
            "license_end": "2027-05-20"}


class Route:
    def __init__(self, method, url):
        self.request = SimpleNamespace(method=method, url=url)
        self.outcome = None

    def continue_(self, **_kw):
        self.outcome = "continued"

    def abort(self, *_a):
        self.outcome = "aborted"


def respond(guard, status, method="POST", url=CREATE_URL):
    guard.on_response(SimpleNamespace(request=SimpleNamespace(method=method), url=url, status=status))


class MiniButton:
    def __init__(self, page):
        self.page = page

    def count(self):
        return self.page.buttons

    @property
    def first(self):
        return self

    def is_visible(self):
        return True

    def is_enabled(self):
        return self.page.enabled

    def evaluate(self, js):
        assert js == pdr.HIT_TEST_JS
        return self.page.hit

    def click(self, **kw):
        assert "force" not in kw
        self.page.clicks += 1
        if self.page.raise_on_click:
            raise RuntimeError("detached")
        for _ in range(self.page.posts):
            route = Route("POST", CREATE_URL)
            self.page.guard.handle(route)
            if route.outcome == "continued" and self.page.status is not None:
                respond(self.page.guard, self.page.status)


class MiniPage:
    def __init__(self, guard, *, buttons=1, enabled=True, hit=True, status=200, posts=1, raise_on_click=False):
        self.guard, self.buttons, self.enabled, self.hit = guard, buttons, enabled, hit
        self.status, self.posts, self.raise_on_click = status, posts, raise_on_click
        self.clicks = 0
        self.waits = 0

    def get_by_role(self, role, name=None, exact=False):
        assert (role, name, exact) == ("button", "Confirm", True)
        return MiniButton(self)

    def wait_for_timeout(self, _ms):
        self.waits += 1


class RequestGuardTests(unittest.TestCase):
    def test_decision_allows_the_create_post_only_when_armed_and_only_on_the_exact_path(self):
        self.assertEqual(live.live_request_decision("POST", CREATE_URL, armed=True), (True, "armed_create_post"))
        self.assertEqual(live.live_request_decision("POST", CREATE_URL, armed=False), (False, "create_post_not_armed"))
        for url in (CREATE_URL + "/x", CREATE_URL + "2", ORIGIN + "/api/v1/backoffice/account/edit",
                    "https://evil.example" + live.CREATE_PATH, "http://app.pentera.io" + live.CREATE_PATH,
                    ORIGIN + "/api/v1/backoffice/account/other/add"):
            self.assertFalse(live.live_request_decision("POST", url, armed=True)[0], url)
        for method in ("PUT", "PATCH", "DELETE"):
            self.assertFalse(live.live_request_decision(method, CREATE_URL, armed=True)[0], method)
        self.assertTrue(live.live_request_decision("GET", ORIGIN + "/api/v1/authenticated/getAuthorities", armed=False)[0])
        self.assertTrue(live.live_request_decision("POST", ORIGIN + pdr.ACCOUNTS_PATH, armed=False)[0])

    def test_unarmed_create_is_aborted_and_counted(self):
        guard = live.LiveRequestGuard()
        route = Route("POST", CREATE_URL)
        guard.handle(route)
        self.assertEqual(route.outcome, "aborted")
        self.assertEqual((guard.create_blocked, guard.create_requests(), guard.blocked_api_writes()), (1, 0, 1))
        self.assertEqual(guard.pre_click_create_attempts(), 1)

    def test_armed_guard_lets_exactly_one_create_through_then_aborts_the_rest(self):
        guard = live.LiveRequestGuard()
        guard.arm()
        first, second, third = (Route("POST", CREATE_URL) for _ in range(3))
        guard.handle(first)
        guard.arm()  # a re-arm after use must not work
        guard.handle(second)
        guard.handle(third)
        self.assertEqual([first.outcome, second.outcome, third.outcome], ["continued", "aborted", "aborted"])
        self.assertEqual((guard.create_requests(), guard.create_blocked), (1, 2))
        self.assertFalse(guard.armed)
        respond(guard, 200)
        self.assertEqual(guard.create_statuses(), [200])

    def test_other_writes_stay_blocked_even_when_armed(self):
        guard = live.LiveRequestGuard()
        guard.arm()
        for method, url in (("POST", ORIGIN + "/api/v1/backoffice/account/edit"), ("PUT", CREATE_URL),
                            ("POST", "https://other.example/x"), ("DELETE", ORIGIN + "/api/v1/backoffice/account/1")):
            route = Route(method, url)
            guard.handle(route)
            self.assertEqual(route.outcome, "aborted", (method, url))
        self.assertTrue(guard.armed)  # none of them used the one create

    def test_unreadable_request_is_aborted(self):
        guard = live.LiveRequestGuard()
        route = SimpleNamespace(request=None, outcome=None)
        route.abort = lambda *_a: setattr(route, "outcome", "aborted")
        guard.handle(route)
        self.assertEqual(route.outcome, "aborted")


class ClickLogTests(unittest.TestCase):
    def test_exactly_one_confirm_click_and_never_forced(self):
        clicks, seen = live.LiveClickLog(), []
        control = SimpleNamespace(click=lambda **kw: seen.append(kw))
        clicks.click(live.CLICK_CONFIRM_CREATE, control, "Confirm", timeout=5)
        self.assertEqual((clicks.confirm_clicks, len(seen)), (1, 1))
        with self.assertRaises(pdr.ClickRefused):
            clicks.click(live.CLICK_CONFIRM_CREATE, control, "Confirm")
        self.assertEqual(len(seen), 1)

    def test_wrong_label_or_force_is_refused_before_any_click(self):
        for label, kw in (("Save", {}), ("confirm", {}), ("Confirm ", {}), ("Confirm", {"force": True})):
            clicks, seen = live.LiveClickLog(), []
            control = SimpleNamespace(click=lambda **k: seen.append(k))
            with self.assertRaises(pdr.ClickRefused):
                clicks.click(live.CLICK_CONFIRM_CREATE, control, label, **kw)
            self.assertEqual((seen, clicks.confirm_clicks), ([], 0))

    def test_a_click_that_raises_is_still_counted_so_it_can_never_repeat(self):
        clicks = live.LiveClickLog()

        def boom(**_kw):
            raise RuntimeError("x")
        with self.assertRaises(RuntimeError):
            clicks.click(live.CLICK_CONFIRM_CREATE, SimpleNamespace(click=boom), "Confirm")
        self.assertEqual(clicks.confirm_clicks, 1)
        with self.assertRaises(pdr.ClickRefused):
            clicks.click(live.CLICK_CONFIRM_CREATE, SimpleNamespace(click=boom), "Confirm")

    def test_the_dry_run_allowlist_is_unchanged(self):
        clicks = live.LiveClickLog()
        control = SimpleNamespace(click=lambda **_k: None)
        with self.assertRaises(pdr.ClickRefused):
            clicks.click(pdr.CLICK_SET_TOGGLE, control, "Confirm")  # a toggle named like Confirm
        with self.assertRaises(pdr.ClickRefused):
            clicks.click("submit_form", control, "Save")
        clicks.click(pdr.CLICK_CANCEL_CREATE_FORM, control, "Cancel")
        self.assertEqual(clicks.kinds, [pdr.CLICK_CANCEL_CREATE_FORM])
        self.assertEqual(pdr.ClickLog().kinds, [])
        self.assertNotIn(live.CLICK_CONFIRM_CREATE, pdr.ALLOWED_CLICKS)


class ConfirmClickTests(unittest.TestCase):
    def setUp(self):
        self.guard, self.clicks = live.LiveRequestGuard(), live.LiveClickLog()

    def go(self, **kw):
        page = MiniPage(self.guard, **kw)
        return page, live.click_confirm_once(page, self.guard, self.clicks)

    def test_one_click_one_post_status_200_is_submitted(self):
        page, result = self.go()
        self.assertEqual((result, page.clicks, self.guard.create_requests()), (live.SUBMITTED, 1, 1))
        self.assertFalse(self.guard.armed)

    def test_no_response_is_uncertain_and_polls_are_bounded(self):
        page, result = self.go(status=None)
        self.assertEqual((result, page.clicks), (live.UNCERTAIN, 1))
        self.assertEqual(page.waits, live.RESPONSE_POLLS)
        self.assertFalse(self.guard.armed)

    def test_non_200_is_uncertain(self):
        for status in (201, 400, 403, 500):
            self.setUp()
            self.assertEqual(self.go(status=status)[1], live.UNCERTAIN, status)

    def test_a_second_create_attempt_makes_the_outcome_uncertain(self):
        page, result = self.go(posts=2)
        self.assertEqual((result, page.clicks, self.guard.create_requests(), self.guard.create_blocked),
                         (live.UNCERTAIN, 1, 1, 1))

    def test_unusable_control_means_nothing_was_clicked(self):
        for kw in ({"buttons": 0}, {"buttons": 2}, {"enabled": False}, {"hit": False}):
            self.setUp()
            page, result = self.go(**kw)
            self.assertEqual((result, page.clicks, self.clicks.confirm_clicks), (live.NOT_CLICKED, 0, 0), kw)
            self.assertFalse(self.guard.armed)

    def test_an_exception_during_the_click_is_uncertain_and_never_repeated(self):
        page, result = self.go(raise_on_click=True)
        self.assertEqual((result, page.clicks), (live.UNCERTAIN, 1))
        self.assertFalse(self.guard.armed)
        again = live.click_confirm_once(page, self.guard, self.clicks)
        self.assertEqual((again, page.clicks), (live.NOT_CLICKED, 1))  # refused by the click log: no second click

    def test_a_second_call_after_success_never_clicks_again(self):
        page, first = self.go()
        second = live.click_confirm_once(page, self.guard, self.clicks)
        self.assertEqual((first, second, page.clicks), (live.SUBMITTED, live.NOT_CLICKED, 1))

    def test_constants_match_the_orchestrator(self):
        from integration.onboarding import production_create as pc
        self.assertEqual((live.SUBMITTED, live.UNCERTAIN, live.NOT_CLICKED), (pc.SUBMITTED, pc.UNCERTAIN, pc.NOT_CLICKED))


def reply(rows, total=None, status=200):
    return {"status": status, "total": len(rows) if total is None else total, "rows": rows}


def row(name="Other Co", domain="other.example", **over):
    return {"name": name, "domain": domain, "alternates": [], "emails": [], "deleted": False, **over}


CLEAR_DEMO = {"status": 200, "hit": False, "count": 3}
_UNSET = object()


class DuplicateTests(unittest.TestCase):
    def verdict(self, name_reply=_UNSET, domain_reply=_UNSET, demo=_UNSET):
        return live.live_duplicate_problem(reply([]) if name_reply is _UNSET else name_reply,
                                           reply([]) if domain_reply is _UNSET else domain_reply,
                                           CLEAR_DEMO if demo is _UNSET else demo, NAME, DOMAIN)

    def test_all_clear(self):
        self.assertIsNone(self.verdict())

    def test_duplicate_by_exact_name_even_with_other_domain(self):
        self.assertEqual(self.verdict(name_reply=reply([row(name=NAME.upper())])), "duplicate_in_production")

    def test_duplicate_by_domain_in_the_name_search_rows(self):
        self.assertEqual(self.verdict(name_reply=reply([row(alternates=[DOMAIN])])), "duplicate_in_production")

    def test_any_row_of_the_domain_search_refuses_even_without_its_fields(self):
        for rows in ([row()], [{"name": None, "domain": None, "alternates": [], "emails": [], "deleted": False}]):
            self.assertEqual(self.verdict(domain_reply=reply(rows)), "duplicate_in_production")
        self.assertEqual(self.verdict(domain_reply={"status": 200, "total": 3, "rows": []}), "duplicate_in_production")

    def test_demo_domain_refuses(self):
        self.assertEqual(self.verdict(demo={"status": 200, "hit": True, "count": 3}), "demo_domain_conflict")

    def test_lookup_errors_and_odd_replies_refuse(self):
        bad = ({"status": 500, "total": None, "rows": None}, {"status": 0}, {"status": 200, "total": None, "rows": None},
               {"status": 200, "total": 1, "rows": "x"}, {"status": 200, "total": -1, "rows": []}, "text", None, [])
        for item in bad:
            self.assertEqual(self.verdict(name_reply=item), "duplicate_check_unavailable", item)
            self.assertEqual(self.verdict(domain_reply=item), "duplicate_check_unavailable", item)
            self.assertEqual(self.verdict(demo=item), "duplicate_check_unavailable", item)

    def test_session_expiry_and_ambiguity(self):
        self.assertEqual(self.verdict(name_reply={"status": 401, "total": None, "rows": None}), "production_session_expired")
        self.assertEqual(self.verdict(domain_reply={"status": 403, "total": None, "rows": None}), "production_session_expired")
        self.assertEqual(self.verdict(name_reply=reply([row()], total=5)), "duplicate_ambiguous")

    def test_search_body_has_no_projection_so_domains_come_back(self):
        body = live.live_search_body(NAME)["tableServerData"]
        self.assertNotIn("projection", body)
        self.assertEqual(body["filters"], {"and": [{"method": "search", "value": NAME}]})
        with self.assertRaises(ValueError):
            live.live_search_body(" ")


def contract_row(**over):
    flat = {
        "accountName": NAME, "accountDomain": DOMAIN, "accountType": "Customer", "accountCountryCode": "CL",
        "accountUuid": UUID, "id": ROW_ID, "scanningInterval": "NO_SCHEDULE", "scanningFrequency": "NO_SCHEDULE",
        "leakedCredentialsScanningInterval": "WEEKLY", "reconLevel": "ALL_ASSETS", "userEmailDomains": ["pentera.io"],
        "accountLicense.assetsNumber": 1, "accountLicense.domainsNumber": 1, "accountLicense.subDomainsNumber": 1,
        "accountLicense.leakedCredentialsAllowed": True, "accountLicense.provisioningEnabled": True,
        "accountLicense.notificationsAllowed": False, "accountLicense.multipleUsersAllowed": False,
        "accountLicense.apiAccessAllowed": False, "accountLicense.phishingEnabled": False,
        "leakedCredentialsSettings.spyCloudSettings.enabled": True,
    }
    for paths in contract.ROW_FLAG_PATHS.values():
        flat[paths[0]] = False
    flat.update(over)
    return flat


EXPECTED = contract.expected_readback_row(INTENDED)


class ReadbackTests(unittest.TestCase):
    def analyse(self, rows, **kw):
        return live.analyse_readback(reply(rows, **kw), INTENDED, EXPECTED)

    def test_exact_row_has_no_mismatches_and_exposes_identity_fields(self):
        state, found = self.analyse([contract_row()])
        self.assertEqual(state, "found")
        self.assertEqual(found["contract_mismatches"], [])
        self.assertEqual((found["tenant_name"], found["domains"], found["account_id"], found["account_uuid"]),
                         (NAME, [DOMAIN], ROW_ID, UUID))

    def test_each_wrong_field_is_named(self):
        for key, bad in (("scanningInterval", "DAILY"), ("accountLicense.assetsNumber", "1"), ("accountType", "customer"),
                         ("leakedCredentialsSettings.spyCloudSettings.enabled", False), ("reconLevel", "SOME")):
            state, found = self.analyse([contract_row(**{key: bad})])
            self.assertEqual(state, "found")
            self.assertIn(key if key != "accountLicense.assetsNumber" else key, found["contract_mismatches"], key)

    def test_a_missing_or_malformed_uuid_adds_account_uuid(self):
        for bad in (None, "xyz", UUID.upper(), UUID[:-1]):
            row_ = contract_row()
            row_["accountUuid"] = bad
            if bad is None:
                del row_["accountUuid"]
            found = self.analyse([row_])[1]
            self.assertIn("accountUuid", found["contract_mismatches"], bad)

    def test_row_problems_never_verify(self):
        cases = ([contract_row(), contract_row(accountDomain="other.example")], [contract_row(accountDomain="other.example")],
                 [contract_row(), contract_row(accountUuid="f" * 32, id="e" * 24)],
                 [contract_row(accountName=NAME.upper())])
        for rows in cases:
            state, found = self.analyse(rows)
            self.assertEqual(state, "found")
            self.assertTrue(found["contract_mismatches"] and all(m.startswith("row_") for m in found["contract_mismatches"]), rows)
            self.assertNotIn("account_uuid", found)
        state, found = self.analyse([contract_row()], total=3)
        self.assertEqual(found["contract_mismatches"], ["row_ambiguous_rows"])

    def test_not_found_session_and_odd_replies(self):
        self.assertEqual(self.analyse([])[0], "not_found")
        self.assertEqual(live.analyse_readback({"status": 401}, INTENDED, EXPECTED)[0], "session_expired")
        for item in ({"status": 500}, {"status": 200, "total": None, "rows": None}, "x", None):
            self.assertEqual(live.analyse_readback(item, INTENDED, EXPECTED), ("unavailable", None))

    def test_polling_is_bounded_and_stops_on_found(self):
        waits, calls = [], []
        replies = iter([reply([]), reply([]), reply([contract_row()])])

        def fetch():
            calls.append(1)
            return next(replies)
        found = live.poll_readback(fetch, INTENDED, EXPECTED, waits.append, attempts=6, wait_ms=7)
        self.assertEqual((found["contract_mismatches"], len(calls), waits), ([], 3, [7, 7]))
        calls.clear()
        self.assertIsNone(live.poll_readback(lambda: calls.append(1) or reply([]), INTENDED, EXPECTED, lambda _m: None,
                                             attempts=4))
        self.assertEqual(len(calls), 4)

    def test_polling_stops_at_once_on_session_or_error(self):
        calls = []
        for item in ({"status": 401}, {"status": 500}):
            calls.clear()
            self.assertIsNone(live.poll_readback(lambda: calls.append(1) or item, INTENDED, EXPECTED, lambda _m: None))
            self.assertEqual(len(calls), 1)

        def boom():
            raise RuntimeError("x")
        self.assertIsNone(live.poll_readback(boom, INTENDED, EXPECTED, lambda _m: None))
        self.assertIsNone(live.poll_readback(lambda: reply([]), INTENDED, EXPECTED, boom))


class SourceHygieneTests(unittest.TestCase):
    def test_module_opens_nothing_and_never_logs(self):
        text = Path(live.__file__).read_text(encoding="utf-8")
        for forbidden in ("import subprocess", "import socket", "urllib", "import requests", "import playwright", "from playwright", "print(", "logging",
                          "open(", "write_text", "os.environ"):
            self.assertNotIn(forbidden, text)

    def test_create_path_exists_only_here_and_in_the_exact_form(self):
        text = Path(live.__file__).read_text(encoding="utf-8")
        self.assertEqual(live.CREATE_PATH, "/api/v1/backoffice/account/add")
        self.assertEqual(len(re.findall(r"/api/v1/backoffice/account/add", text)), 1)

    def test_live_reason_codes_are_machine_names(self):
        for code in live.LIVE_REASONS:
            self.assertRegex(code, r"^[a-z][a-z0-9_]{2,80}$")
        self.assertEqual(live.live_known_reason("Bearer abc"), "unexpected_error")
        self.assertEqual(live.live_known_reason("confirm_not_clicked"), "confirm_not_clicked")
        self.assertEqual(live.live_known_reason("clone_stale"), "clone_stale")


if __name__ == "__main__":
    unittest.main()
