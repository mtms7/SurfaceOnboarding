"""CO-0844 (2026-11-11): the real Add User form and the way control is given back, read in the owner's controlled session.

Add User opens ``.administration-users-add-user`` (no role=dialog) with First name / Last name / Organization email, a native
``select#roleNames`` (Admin = "admin") and a "Confirm" button (disabled until valid). Control is given back through the tenant
app's user menu (``button-User_Icon``) > "Log Out", which sends ``giveUpControl``.
"""
from __future__ import annotations

import contextlib
import unittest
from types import SimpleNamespace

import tools.attended_ce_only_playwright as runner
from integration.onboarding import production_case3_primary_user as primary

USER = SimpleNamespace(first_name="Ada", last_name="Lovelace", email="ada@example.test")


class Node:
    def __init__(self, page, key, items=1):
        self.page, self.key, self.items = page, key, items

    # locator-like API
    def count(self):
        return self.items

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self.page.clicks.append(self.key)
        if self.key == "logout":
            self.page.logout_box = True  # BackOffice asks "Log out of your account" (Cancel / Log out)
        if self.key == "logout_confirm":
            self.page.fire("POST", primary.GIVE_UP_PATH, self.page.give_up_status)
        if self.key == "confirm":
            self.page.fire("POST", primary.ADD_PATH, self.page.add_status)

    def fill(self, value, timeout=None):
        self.page.filled[self.key] = value

    def select_option(self, value=None, timeout=None):
        self.page.selected = value

    def input_value(self, timeout=None):
        return self.page.selected or ""

    def is_enabled(self, timeout=None):
        return self.page.confirm_enabled

    def filter(self, has_text=None):
        return Node(self.page, "logout" if self.page.has_logout else "none", 1 if self.page.has_logout else 0)

    def get_by_label(self, pattern):
        for key in ("first", "last", "email"):
            if pattern.search({"first": "First name *", "last": "Last name *", "email": "Organization email *"}[key]):
                return Node(self.page, key)
        return Node(self.page, "none", 0)

    get_by_placeholder = get_by_label

    def locator(self, selector):
        return self.page.locator(selector)

    def get_by_role(self, role, name=None):
        """Inside the Add User box only Cancel / Confirm exist (the page's own "Add User" button is outside it)."""
        if self.key == "modal":
            if role == "button" and name is not None and name.search("Confirm"):
                return Node(self.page, "confirm")
            return Node(self.page, "none", 0)
        return self.page.get_by_role(role, name)


class FakePage:
    def __init__(self, *, modal=True, role_select=True, confirm_enabled=True, has_logout=True, give_up_status=200,
                 add_status=200, confirm_box=True):
        self.modal, self.role_select, self.confirm_enabled, self.has_logout = modal, role_select, confirm_enabled, has_logout
        self.give_up_status, self.add_status, self.confirm_box, self.logout_box = give_up_status, add_status, confirm_box, False
        self.clicks, self.filled, self.selected, self.responses = [], {}, None, []
        self.keyboard = SimpleNamespace(press=lambda key: self.clicks.append("key:" + key))

    def fire(self, method, path, status):
        self.responses.append(SimpleNamespace(request=SimpleNamespace(method=method), url="https://app.pentera.io" + path,
                                              status=status))

    def wait_for_timeout(self, ms):
        pass

    def locator(self, selector):
        if selector == runner.ADD_USER_MODAL_SELECTOR:
            return Node(self, "modal", 1 if self.modal else 0)
        if selector == runner.ROLE_SELECT_SELECTOR:
            return Node(self, "role", 1 if self.role_select else 0)
        if selector == runner.USER_MENU_SELECTOR:
            return Node(self, "user_menu")
        if selector == ".hover-menu-item":
            return Node(self, "menu")
        return Node(self, "none", 0)

    def get_by_role(self, role, name=None):
        if role == "button" and name is not None and name.search("Add User"):
            return Node(self, "add_user")
        if role == "button" and name is not None and name.search("Confirm"):
            return Node(self, "confirm")
        if role == "button" and name is not None and name.search("Log out") and self.logout_box and self.confirm_box:
            return Node(self, "logout_confirm")
        return Node(self, "none", 0)

    @contextlib.contextmanager
    def expect_response(self, predicate, timeout=None):
        holder = SimpleNamespace()
        start = len(self.responses)
        yield holder
        matched = [r for r in self.responses[start:] if predicate(r)]
        if not matched:
            raise TimeoutError("no response")
        holder.value = matched[0]


class ReleaseTests(unittest.TestCase):
    def test_log_out_in_the_user_menu_gives_control_back(self):
        page, diag = FakePage(), {}
        self.assertTrue(runner._pu_release(page, diag))  # noqa: SLF001
        self.assertEqual(page.clicks, ["user_menu", "logout", "logout_confirm"])
        self.assertEqual((diag["release_controls"], diag["release_confirm"]), ("1", "1"))

    def test_without_the_log_out_confirm_control_is_not_given_back(self):
        """CO-0844 22:22Z: Log Out was clicked but its confirm box was not, so no giveUpControl was sent."""
        page, diag = FakePage(confirm_box=False), {}
        self.assertFalse(runner._pu_release(page, diag))  # noqa: SLF001
        self.assertEqual(diag["release_confirm"], "0")

    def test_no_log_out_item_or_a_failed_reply_is_not_released(self):
        page, diag = FakePage(has_logout=False), {}
        self.assertFalse(runner._pu_release(page, diag))  # noqa: SLF001
        self.assertEqual(diag["release_controls"], "0")
        self.assertFalse(runner._pu_release(FakePage(give_up_status=500), {}))  # noqa: SLF001


class AddFormTests(unittest.TestCase):
    def add(self, page):
        guard, diag, recorded = SimpleNamespace(add_armed=False, add_mismatch=0), {}, []
        return runner._pu_fill_and_add(page, guard, USER, diag, recorded.append), diag, recorded  # noqa: SLF001

    def test_fills_the_person_picks_admin_and_confirms(self):
        page = FakePage()
        result, diag, recorded = self.add(page)
        self.assertEqual(result, "add_accepted")
        self.assertEqual(page.filled, {"first": "Ada", "last": "Lovelace", "email": "ada@example.test"})
        self.assertEqual(page.selected, "admin")
        self.assertEqual((diag["modal"], diag["role_controls"], diag["submit_buttons"]), ("1", "1", "1"))
        self.assertEqual(recorded, ["write_started"])
        self.assertIn("confirm", page.clicks)

    def test_missing_role_or_disabled_confirm_sends_nothing(self):
        for page in (FakePage(role_select=False), FakePage(confirm_enabled=False)):
            result, _diag, recorded = self.add(page)
            self.assertEqual(result, "add_form_unavailable")
            self.assertEqual(recorded, [])
            self.assertNotIn("confirm", page.clicks)

    def test_a_rejected_add_is_reported(self):
        self.assertEqual(self.add(FakePage(add_status=400))[0], "add_rejected")


if __name__ == "__main__":
    unittest.main()
