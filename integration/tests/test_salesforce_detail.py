from __future__ import annotations

import json
import unittest

from integration.onboarding.salesforce_detail import (DETAIL_QUERY_FIELDS, CustomerOnboardingDetail,
                                                       parse_co_0717_detail_document)


def document(*, account: str = "Example Account", uuid: str | None = None) -> str:
    record = {field: "example" for field in DETAIL_QUERY_FIELDS}
    record.update({
        "Name": "CO-0717",
        "LastModifiedDate": "2026-09-11T12:00:00.000+0000",
        "Account_Name__c": account,
        "Account_UUID__c": uuid,
    })
    return json.dumps([record])


class SalesforceDetailContractTests(unittest.TestCase):
    def test_exact_single_co_is_accepted_only_in_memory(self):
        detail = parse_co_0717_detail_document(document())
        self.assertIsInstance(detail, CustomerOnboardingDetail)
        self.assertEqual(detail.value_for("Account_Name__c"), "Example Account")
        self.assertIsNone(detail.value_for("Account_UUID__c"))

    def test_wrong_scope_duplicate_keys_and_schema_drift_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unexpected_salesforce_detail_schema"):
            parse_co_0717_detail_document(document().replace("CO-0717", "CO-0718"))
        with self.assertRaisesRegex(ValueError, "invalid_salesforce_detail_document"):
            parse_co_0717_detail_document('[{"Name":"CO-0717","Name":"CO-0717"}]')
        with self.assertRaisesRegex(ValueError, "ambiguous_salesforce_detail_result"):
            parse_co_0717_detail_document('[]')

    def test_invalid_revision_or_value_never_constructs_a_detail(self):
        invalid_revision = json.loads(document())
        invalid_revision[0]["LastModifiedDate"] = "unknown"
        with self.assertRaisesRegex(ValueError, "invalid_salesforce_detail_revision"):
            parse_co_0717_detail_document(json.dumps(invalid_revision))
        too_long = json.loads(document())
        too_long[0]["Onboarding_Comments__c"] = "x" * 32769
        with self.assertRaisesRegex(ValueError, "invalid_salesforce_detail_value"):
            parse_co_0717_detail_document(json.dumps(too_long))

    def test_render_escapes_customer_values_and_marks_blanks(self):
        detail = parse_co_0717_detail_document(document(account="<not-html>", uuid=None))
        from integration.onboarding.salesforce_detail import render_attended_co_0717_detail
        page = render_attended_co_0717_detail(detail)
        self.assertIn("&lt;not-html&gt;", page)
        self.assertIn("Not populated", page)
        self.assertNotIn("<script", page)
