from __future__ import annotations

import json
import subprocess
import unittest

from integration.onboarding.salesforce_detail import DETAIL_QUERY_FIELDS
from tools.serve_attended_co0717_viewer import AttendedReadError, SOQL, read_co_0717_once


def successful_cli_result() -> subprocess.CompletedProcess[str]:
    record = {field: "example" for field in DETAIL_QUERY_FIELDS}
    record.update({"Name": "CO-0717", "LastModifiedDate": "2026-09-11T12:00:00.000+0000", "Account_UUID__c": None,
                   "attributes": {"type": "Customer_Onboarding__c"}})
    return subprocess.CompletedProcess(["sf"], 0, json.dumps({"status": 0, "result": {"records": [record]}}), "")


class AttendedCO0717ViewerTests(unittest.TestCase):
    def test_fixed_query_renders_only_escaped_transient_detail(self):
        calls: list[tuple[object, ...]] = []

        def run(*args, **_kwargs):
            calls.append(args)
            return successful_cli_result()

        page = read_co_0717_once(run)
        self.assertIn("Customer Onboarding CO-0717", page)
        self.assertIn("Not populated", page)
        self.assertIn("not stored, cached, forwarded to the VM", page)
        self.assertNotIn("<script", page)
        self.assertEqual(calls[0][0][0], "sf.cmd")
        self.assertIn(SOQL, calls[0][0])
        self.assertNotIn("UPDATE", SOQL)

    def test_bad_cli_result_is_masked_and_cannot_render(self):
        with self.assertRaisesRegex(AttendedReadError, "unavailable"):
            read_co_0717_once(lambda *_args, **_kwargs: subprocess.CompletedProcess(["sf"], 1, "", "sensitive"))
        invalid = successful_cli_result()
        invalid.stdout = json.dumps({"status": 0, "result": {"records": []}})
        with self.assertRaisesRegex(AttendedReadError, "not_available"):
            read_co_0717_once(lambda *_args, **_kwargs: invalid)
