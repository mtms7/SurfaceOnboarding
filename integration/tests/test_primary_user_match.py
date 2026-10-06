from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner


class PrimaryUserMatchTests(unittest.TestCase):
    SF = {"name": "Mauro Martellosio", "email": "Mauro@Example.com"}

    def row(self, **user):
        return {"primaryUser": {"firstName": "Mauro", "lastName": "Martellosio", "email": "mauro@example.com", **user}}

    def test_match_is_case_and_space_insensitive(self):
        self.assertEqual(runner.primary_user_match(self.row(), self.SF), (True, "primary_user_matches"))
        loose = self.row(firstName="  MAURO ", lastName="martellosio", email=" MAURO@EXAMPLE.COM ")
        self.assertIs(runner.primary_user_match(loose, {"name": "mauro  Martellosio", "email": "mauro@example.com"})[0], True)

    def test_name_only_when_salesforce_has_no_email(self):
        self.assertIs(runner.primary_user_match(self.row(email="other@example.com"),
                                                {"name": "Mauro Martellosio", "email": ""})[0], True)

    def test_mismatch_and_missing_fail_closed_with_reason_codes(self):
        self.assertEqual(runner.primary_user_match(self.row(lastName="Martelosio"), self.SF), (False, "primary_user_name_differs"))
        self.assertEqual(runner.primary_user_match(self.row(email="x@example.com"), self.SF), (False, "primary_user_email_differs"))
        self.assertEqual(runner.primary_user_match(self.row(email=None), self.SF), (False, "primary_user_email_differs"))
        self.assertEqual(runner.primary_user_match({}, self.SF), (False, "primary_user_missing_on_tenant"))
        self.assertEqual(runner.primary_user_match({"primaryUser": None}, self.SF), (False, "primary_user_missing_on_tenant"))
        self.assertEqual(runner.primary_user_match(self.row(), {"name": "", "email": ""}), (None, "salesforce_primary_user_missing"))
        self.assertEqual(runner.primary_user_match(self.row(), None), (None, "salesforce_primary_user_missing"))

    def test_validate_row_reports_without_values_or_drift(self):
        name = "Primary user is the Salesforce primary user"
        for row, status, found in ((self.row(), "ok", "yes"), ({}, "info", "not yet")):
            check = {c["check"]: c for c in runner.validate_row(row, None, None, None, self.SF)}[name]
            self.assertEqual((check["status"], check["found"]), (status, found))
        self.assertNotIn(name, {c["check"] for c in runner.validate_row({}, None)})

    def test_only_the_boolean_reason_and_date_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "VALIDATION_PATH", Path(directory) / "validation.json"), \
                    patch.object(runner, "write_scan_status"), \
                    patch.object(runner, "_salesforce_primary_user", return_value=self.SF):
                result = runner._record_validation("CO-0767", runner.SURFACE_ENGINE, self.row(), None, "", None)
                text = runner.VALIDATION_PATH.read_text(encoding="utf-8")
        self.assertIn(result, ("validation_recorded", "validation_drift_found"))
        stored = json.loads(text)["CO-0767"]
        self.assertIs(stored["primary_user_matches"], True)
        self.assertEqual(stored["primary_user_reason"], "primary_user_matches")
        self.assertEqual(len(stored["primary_user_checked_on"]), 10)
        for value in ("Mauro", "Martellosio", "example.com"):
            self.assertNotIn(value, text)

    def test_unreadable_salesforce_user_records_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "VALIDATION_PATH", Path(directory) / "validation.json"), \
                    patch.object(runner, "write_scan_status"), \
                    patch.object(runner, "_salesforce_primary_user", return_value=None):
                runner._record_validation("CO-0767", runner.SURFACE_ENGINE, self.row(), None, "", None)
                stored = json.loads(runner.VALIDATION_PATH.read_text(encoding="utf-8"))["CO-0767"]
        self.assertIsNone(stored["primary_user_matches"])
        self.assertEqual(stored["primary_user_reason"], "salesforce_primary_user_unreadable")


if __name__ == "__main__":
    unittest.main()
