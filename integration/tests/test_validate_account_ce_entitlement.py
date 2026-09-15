import unittest
from unittest.mock import patch

from tools.validate_account_ce_entitlement import ValidationUnavailable, dealhub_quantity_field


class AccountCeEntitlementScriptTests(unittest.TestCase):
    def test_quantity_field_is_resolved_by_label(self):
        response = {"status": 0, "result": {"fields": [
            {"label": "Other", "name": "Other__c"},
            {"label": "Dealhub Quantity", "name": "DealHub_Quantity__c"},
        ]}}
        with patch("tools.validate_account_ce_entitlement.sf_json", return_value=response):
            self.assertEqual(dealhub_quantity_field(), "DealHub_Quantity__c")

    def test_ambiguous_quantity_field_fails_closed(self):
        response = {"status": 0, "result": {"fields": [
            {"label": "Dealhub Quantity", "name": "One__c"},
            {"label": "Dealhub Quantity", "name": "Two__c"},
        ]}}
        with patch("tools.validate_account_ce_entitlement.sf_json", return_value=response):
            with self.assertRaises(ValidationUnavailable):
                dealhub_quantity_field()


if __name__ == "__main__":
    unittest.main()
