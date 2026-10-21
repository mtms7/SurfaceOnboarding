"""Why the Details click path fails in the production scan check (live 2026-10-21: ``executions_problem`` = ``details_open`` for CO-0800).

``_open_row_action`` now tells WHICH of its three clicks failed (row, menu, item) and how many rows, matching rows and row-menu buttons the
page showed (integers only, never a name or id); ``read_scan_executions`` carries that on the error and the scan observation keeps it as a
validated token plus counts. Hermetic: a fake page, no browser.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from integration.onboarding import production_scan_check as scan
import tools.attended_ce_only_playwright as runner

NOW = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)


class Loc:
    def __init__(self, page, key, fail=False, count=0):
        self.page, self.key, self.fail, self._count = page, key, fail, count
        self.first = self

    def filter(self, has_text=None):
        return Loc(self.page, self.key + ":filtered", self.page.fail_at == self.key + ":filtered", self.page.counts.get(self.key + ":filtered", 0))

    def click(self, timeout=None):
        self.page.clicked.append(self.key)
        if self.fail:
            raise TimeoutError("not clickable")

    def count(self):
        return self._count


class Item:
    def __init__(self, name):
        self.name = name

    def get_attribute(self, attribute):
        return self.name if attribute == "data-am" else None


class Items:
    def __init__(self, names):
        self.names = names

    def count(self):
        return len(self.names)

    def nth(self, index):
        return Item(self.names[index])


class Page:
    def __init__(self, fail_at=None, counts=None, no_count=False, menu_items=()):
        self.fail_at, self.counts, self.clicked, self.no_count = fail_at, counts or {}, [], no_count
        self.menu_items = list(menu_items)

    def locator(self, selector):
        if selector == runner.ROW_MENU_ITEMS_SELECTOR:
            return Items(self.menu_items)
        key = {"tr, [role='row']": "row", runner.SCAN_EXEC_ROW_MENU_SELECTOR: "menu"}.get(selector, "item")
        loc = Loc(self, key, self.fail_at == key, self.counts.get(key, 0))
        if self.no_count:
            loc.count = None  # a fake without count(): the diagnostic must not break the real error
        return loc


class RowActionTests(unittest.TestCase):
    def fail(self, where, counts=None, **kw):
        page = Page(where, counts, **kw)
        with self.assertRaises(Exception) as caught:
            runner._open_row_action(page, "Example TG", runner.SCAN_EXEC_DETAILS_SELECTOR)
        return caught.exception

    def test_each_failing_click_names_its_stage(self):
        for where, stage in (("row:filtered", "row"), ("menu", "menu"), ("item", "item")):
            error = self.fail(where)
            self.assertEqual(error.row_action_stage, stage, where)

    def test_the_counts_are_integers_only(self):
        error = self.fail("row:filtered", {"row": 20, "row:filtered": 0, "menu": 0})
        self.assertEqual(error.row_action_counts, {"rows": 20, "matching_rows": 0, "menu_buttons": 0})

    def test_the_menu_item_names_on_screen_are_kept_as_tokens_when_the_item_click_fails(self):
        error = self.fail("item", menu_items=["Button-Grid_Edit", "Button-Grid_Info", "weird name with spaces", "Button-Grid_" + "x" * 80])
        self.assertEqual(error.row_action_menu_items, ["Button-Grid_Edit", "Button-Grid_Info"])
        self.assertEqual(self.fail("row:filtered").row_action_menu_items, [])  # only after the menu was opened

    def test_a_page_that_cannot_count_still_raises_the_original_error(self):
        error = self.fail("menu", no_count=True)
        self.assertIsInstance(error, TimeoutError)
        self.assertEqual(error.row_action_stage, "menu")
        self.assertEqual(getattr(error, "row_action_counts", {}), {})

    def test_a_disallowed_item_still_raises_before_any_click(self):
        page = Page()
        with self.assertRaises(ValueError):
            runner._open_row_action(page, "x", "button.delete")
        self.assertEqual(page.clicked, [])


class ExecutionsErrorTests(unittest.TestCase):
    def test_the_executions_error_carries_the_stage_and_counts(self):
        class P(Page):
            keyboard = type("K", (), {"press": staticmethod(lambda *_a, **_k: None)})()

            def on(self, *_a):
                pass

            def remove_listener(self, *_a):
                pass

            def wait_for_timeout(self, *_a):
                pass

        with self.assertRaises(RuntimeError) as caught:
            runner.read_scan_executions(P("row:filtered", {"row": 20, "row:filtered": 0, "menu": 0}), "Example TG", "0" * 24,
                                        runner.production_probe.PRODUCTION_ORIGIN)
        error = caught.exception
        self.assertEqual((error.step, error.stage), ("details_open", "row"))
        self.assertEqual(error.counts, {"rows": 20, "matching_rows": 0, "menu_buttons": 0})
        self.assertEqual(error.menu_items, [])


class RecordTests(unittest.TestCase):
    def read(self, reply):
        with tempfile.TemporaryDirectory() as tmp:
            return json.loads(scan.record(Path(tmp), "CO-0800", reply, NOW).read_text(encoding="utf-8"))

    def test_the_observation_keeps_a_valid_stage_and_integer_counts_only(self):
        good = {"state": "executions_unavailable", "found": True, "fields": {}, "executions_problem": "details_open",
                "executions_stage": "row", "executions_counts": {"rows": 20, "matching_rows": 0, "menu_buttons": 0}}
        data = self.read(good)
        self.assertEqual((data["executions_stage"], data["executions_counts"]),
                         ("row", {"rows": 20, "matching_rows": 0, "menu_buttons": 0}))

    def test_menu_item_names_are_validated_tokens(self):
        reply = {"state": "executions_unavailable", "found": True, "fields": {}, "executions_problem": "details_open",
                 "executions_menu_items": ["Button-Grid_Edit", "Example Klinik", "Button-Grid_Details", 5, "Button-Grid_" + "y" * 80]}
        data = self.read(reply)
        self.assertEqual(data["executions_menu_items"], ["Button-Grid_Edit", "Button-Grid_Details"])

    def test_anything_else_is_dropped(self):
        bad = {"state": "executions_unavailable", "found": True, "fields": {}, "executions_problem": "details_open",
               "executions_stage": "Example Klinik", "executions_counts": {"rows": "twenty", "matching_rows": -1, "secret": 5,
                                                                          "menu_buttons": True}}
        data = self.read(bad)
        self.assertNotIn("executions_stage", data)
        self.assertEqual(data.get("executions_counts", {}), {})
        self.assertNotIn("Example", json.dumps(data))


if __name__ == "__main__":
    unittest.main()
