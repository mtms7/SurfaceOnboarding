"""The confirmed short tenant name (owner decision 2026-10-21, docs/60): one local record per CO, bound to the Salesforce account name.

A long account name (over 40 characters) must be confirmed by the owner before a production dry run or create; the confirmed name keeps
the first two words and has at most 40 characters. The record never guesses: a corrupt file raises, a changed account name makes the
old decision invalid. Hermetic: temporary files only.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from integration.onboarding import tenant_name_decision as decision

FULL = "Barmherzige Brüder gemeinnützige Träger GmbH"
SHORT = "Barmherzige Brüder gemeinnützige TG"
NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)
CO = "CO-0800"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / decision.STATE_FILE_NAME

    def confirm(self, name=SHORT, full=FULL, co=CO, operator="owner@example.test"):
        return decision.confirm(self.path, co, full, name, operator, NOW)


class ValidationTests(Base):
    def test_a_name_that_keeps_the_first_two_words_and_fits_is_accepted(self):
        self.assertEqual(decision.validate(FULL, SHORT), SHORT)
        self.assertEqual(decision.validate(FULL, "  Barmherzige   Brüder gemeinnützige TG "), SHORT)

    def test_the_full_name_is_accepted_only_when_it_is_short_enough(self):
        self.assertEqual(decision.validate("Advanced Technology International", "Advanced Technology International"),
                         "Advanced Technology International")
        with self.assertRaises(decision.TenantNameRefused):
            decision.validate(FULL, FULL)

    def test_refusals_carry_stable_codes(self):
        cases = ((FULL, "", "tenant_name_invalid"), (FULL, "   ", "tenant_name_invalid"), (FULL, None, "tenant_name_invalid"),
                 (FULL, "Barmherzige Brüder\ngemeinnützige TG", "tenant_name_invalid"),
                 (FULL, "Barmherzige Schwestern gemeinnützige TG", "tenant_name_first_words_changed"),
                 (FULL, "Barmherzige Brüder " + "x" * 30, "tenant_name_too_long"),
                 (FULL, "Barmherzige", "tenant_name_first_words_changed"))
        for full, name, code in cases:
            with self.subTest(name=name), self.assertRaises(decision.TenantNameRefused) as caught:
                decision.validate(full, name)
            self.assertEqual(str(caught.exception), code)

    def test_needs_confirmation_is_only_for_names_over_the_limit(self):
        self.assertTrue(decision.needs_confirmation(FULL))
        self.assertFalse(decision.needs_confirmation("Advanced Technology International"))
        self.assertFalse(decision.needs_confirmation("A" * 40))
        self.assertTrue(decision.needs_confirmation("A" * 41))


class StoreTests(Base):
    def test_confirm_then_read_back_the_same_name_for_the_same_account_name(self):
        self.confirm()
        self.assertEqual(decision.confirmed_name(self.path, CO, FULL), SHORT)
        record = json.loads(self.path.read_text(encoding="utf-8"))[CO]
        self.assertEqual(set(record), {"confirmed_name", "full_name_hash", "operator", "confirmed_at"})
        self.assertNotIn(FULL, self.path.read_text(encoding="utf-8"))  # the account name itself is not stored, only its hash

    def test_a_changed_account_name_or_another_co_has_no_decision(self):
        self.confirm()
        self.assertIsNone(decision.confirmed_name(self.path, CO, FULL + " Holding"))
        self.assertIsNone(decision.confirmed_name(self.path, "CO-0801", FULL))

    def test_a_missing_file_means_no_decision_and_a_corrupt_file_fails_closed(self):
        self.assertIsNone(decision.confirmed_name(self.path, CO, FULL))
        self.path.write_text("not json {", encoding="utf-8")
        with self.assertRaises(decision.TenantNameUnavailable):
            decision.confirmed_name(self.path, CO, FULL)
        with self.assertRaises(decision.TenantNameUnavailable):
            self.confirm()  # never overwrites a file it cannot read
        self.path.write_text(json.dumps({CO: {"confirmed_name": 5}}), encoding="utf-8")
        with self.assertRaises(decision.TenantNameUnavailable):
            decision.confirmed_name(self.path, CO, FULL)

    def test_a_later_confirmation_replaces_the_earlier_one_and_keeps_other_cos(self):
        self.confirm()
        decision.confirm(self.path, "CO-0801", "Another Long Account Name Holding Group International Ltd",
                         "Another Long Account Name HGIL", "owner@example.test", NOW)
        self.confirm(name="Barmherzige Brüder gemeinnützige Träger")
        self.assertEqual(decision.confirmed_name(self.path, CO, FULL), "Barmherzige Brüder gemeinnützige Träger")
        self.assertEqual(decision.confirmed_name(
            self.path, "CO-0801", "Another Long Account Name Holding Group International Ltd"), "Another Long Account Name HGIL")

    def test_bad_inputs_are_refused_before_anything_is_written(self):
        for kwargs in (dict(co="CO-1; DROP"), dict(operator=""), dict(operator="a b"), dict(name="Wrong Words gemeinnützige TG")):
            with self.subTest(kwargs=kwargs), self.assertRaises(decision.TenantNameRefused):
                self.confirm(**kwargs)
        self.assertFalse(self.path.exists())

    def test_concurrent_confirmations_leave_a_readable_file(self):
        names = ["Barmherzige Brüder gemeinnützige %s" % suffix for suffix in ("TG", "T", "Tr", "Trä")]
        threads = [threading.Thread(target=lambda n=n: self.confirm(name=n)) for n in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertIn(decision.confirmed_name(self.path, CO, FULL), names)


class ResolveTests(Base):
    def test_a_short_account_name_needs_nothing_and_is_used_as_is(self):
        result = decision.resolve(self.path, CO, "Advanced Technology International")
        self.assertEqual((result.name, result.confirmed, result.needs_confirmation),
                         ("Advanced Technology International", False, False))

    def test_a_long_name_without_a_decision_needs_confirmation_and_keeps_the_full_name(self):
        result = decision.resolve(self.path, CO, FULL)
        self.assertEqual((result.name, result.confirmed, result.needs_confirmation), (FULL, False, True))

    def test_a_long_name_with_a_decision_uses_the_confirmed_name(self):
        self.confirm()
        result = decision.resolve(self.path, CO, FULL)
        self.assertEqual((result.name, result.confirmed, result.needs_confirmation), (SHORT, True, False))

    def test_a_corrupt_file_leaves_a_long_name_unconfirmed_and_never_a_guess(self):
        self.path.write_text("{", encoding="utf-8")
        result = decision.resolve(self.path, CO, FULL)
        self.assertEqual((result.name, result.confirmed, result.needs_confirmation, result.unavailable), (FULL, False, True, True))

    def test_the_module_has_no_network_and_no_subprocess(self):
        text = Path(decision.__file__).read_text(encoding="utf-8")
        for forbidden in ("urllib", "socket", "subprocess", "requests", "playwright"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
