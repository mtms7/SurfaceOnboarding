import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from phase1_validator.server import (
    POLICY_VERSION,
    ValidatorHandler,
    _load_json_without_duplicate_keys,
    compare,
    compare_bound,
)


class CompareTests(unittest.TestCase):
    def test_mismatch_requires_csm_review(self):
        result = compare(
            {
                "co_number": "SHADOW-TEST-001",
                "dealhub": {
                    "product": "Credential Exposure",
                    "start_date": "2026-04-28",
                    "end_date": "2027-04-27",
                    "status": "Active",
                    "subscription_name": "CE Core",
                    "license_type": "Annual",
                    "quantity": 1,
                },
                "contract": {
                    "product": "Credential Exposure",
                    "start_date": "2026-04-28",
                    "end_date": "2026-04-27",
                    "status": "Active",
                    "subscription_name": "CE Core",
                    "license_type": "Annual",
                    "quantity": 1,
                    "source_file_name": "masked-contract.pdf",
                },
            }
        )

        self.assertEqual(result["decision"], "manual_review_required")
        self.assertFalse(result["proceed"])
        self.assertEqual(result["action_required"], "contact_csm")
        self.assertEqual(result["mismatches"][0]["field"], "end_date")
        self.assertNotIn("masked-contract.pdf", json.dumps(result))

    def test_contract_metadata_is_never_reflected_in_output(self):
        sensitive_filename = "customer-name-signed-order-form.pdf"
        evidence = {
            "product": "Credential Exposure",
            "start_date": "2026-04-28",
            "end_date": "2027-04-27",
            "status": "Active",
            "subscription_name": "CE Core",
            "license_type": "Annual",
            "quantity": 1,
        }

        result = compare(
            {
                "co_number": "SHADOW-TEST-003",
                "dealhub": evidence,
                "contract": {
                    **evidence,
                    "end_date": "2026-04-27",
                    "source_file_name": sensitive_filename,
                    "document_url": "https://example.invalid/private-contract",
                    "raw_ocr_text": "must never be reflected",
                },
            }
        )

        serialized = json.dumps(result)
        self.assertNotIn(sensitive_filename, serialized)
        self.assertNotIn("example.invalid", serialized)
        self.assertNotIn("must never be reflected", serialized)

    def test_match_is_review_only(self):
        evidence = {
            "product": "Credential Exposure",
            "start_date": "2026-04-28",
            "end_date": "2027-04-27",
            "status": "Active",
            "subscription_name": "CE Core",
            "license_type": "Annual",
            "quantity": 1,
        }
        result = compare(
            {
                "co_number": "SHADOW-TEST-002",
                "dealhub": evidence,
                "contract": {**evidence, "source_file_name": "masked-contract.pdf"},
            }
        )

        self.assertEqual(result["decision"], "approved_for_review")
        self.assertFalse(result["proceed"])
        self.assertEqual(result["action_required"], "human_review_required")
        self.assertEqual(result["mismatches"], [])


class BoundCompareTests(unittest.TestCase):
    def payload(self):
        evidence = {
            "product": "Credential Exposure",
            "start_date": "2026-04-28",
            "end_date": "2027-04-27",
            "status": "Active",
            "subscription_name": "CE Core",
            "license_type": "Annual",
            "quantity": 1,
        }
        return {
            "co_number": "CO-0697",
            "sf_record_id": "a5KR500000lTRcuMAG",
            "source_revision": "2026-08-19T22:33:25.000+00:00",
            "request_id": "52bd2117-4bd7-4609-984f-477a9e40596c",
            "policy_version": POLICY_VERSION,
            "dealhub": evidence,
            "contract": dict(evidence),
        }

    def test_match_is_bound_and_still_never_proceeds(self):
        result = compare_bound(self.payload())

        self.assertEqual(result["decision"], "approved_for_review")
        self.assertFalse(result["proceed"])
        self.assertEqual(result["co_number"], "CO-0697")
        self.assertEqual(result["sf_record_id"], "a5KR500000lTRcuMAG")
        self.assertEqual(result["policy_version"], POLICY_VERSION)
        self.assertEqual(len(result["evidence_hash"]), 64)
        self.assertEqual(len(result["result_hash"]), 64)
        self.assertEqual(result, compare_bound(self.payload()))

    def test_mismatch_never_reflects_source_values(self):
        payload = self.payload()
        payload["contract"]["end_date"] = "2026-04-27"
        result = compare_bound(payload)
        serialized = json.dumps(result)

        self.assertEqual(
            result["mismatches"],
            [{"field": "end_date", "type": "source_data_mismatch"}],
        )
        self.assertNotIn("2027-04-27", serialized)
        self.assertNotIn("2026-04-27", serialized)

    def test_unknown_fields_are_rejected(self):
        payload = self.payload()
        payload["contract"]["source_file_name"] = "must-not-enter-workato.pdf"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            compare_bound(payload)

    def test_missing_normalized_key_is_rejected(self):
        payload = self.payload()
        del payload["dealhub"]["quantity"]
        with self.assertRaisesRegex(ValueError, "missing fields"):
            compare_bound(payload)

    def test_empty_normalized_values_fail_closed(self):
        payload = self.payload()
        payload["contract"] = {field: "" for field in payload["contract"]}
        result = compare_bound(payload)
        self.assertEqual(result["decision"], "manual_review_required")
        self.assertFalse(result["proceed"])
        self.assertEqual(len(result["mismatches"]), 7)

    def test_invalid_date_quantity_and_revision_are_rejected(self):
        payload = self.payload()
        payload["dealhub"]["start_date"] = "04/28/2026"
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            compare_bound(payload)

        payload = self.payload()
        payload["dealhub"]["quantity"] = True
        with self.assertRaisesRegex(ValueError, "positive integer"):
            compare_bound(payload)

        payload = self.payload()
        payload["source_revision"] = "2026-08-19T22:33:25"
        with self.assertRaisesRegex(ValueError, "timezone"):
            compare_bound(payload)

    def test_revision_change_changes_both_hashes(self):
        first = compare_bound(self.payload())
        changed = self.payload()
        changed["source_revision"] = "2026-08-19T22:34:25.000+00:00"
        second = compare_bound(changed)
        self.assertNotEqual(first["evidence_hash"], second["evidence_hash"])
        self.assertNotEqual(first["result_hash"], second["result_hash"])

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            _load_json_without_duplicate_keys(b'{"co_number":"CO-1","co_number":"CO-2"}')


class BoundHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ValidatorHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def payload(self):
        evidence = {
            "product": "Credential Exposure",
            "start_date": "2026-04-28",
            "end_date": "2027-04-27",
            "status": "Active",
            "subscription_name": "CE Core",
            "license_type": "Annual",
            "quantity": 1,
        }
        return {
            "co_number": "CO-0697",
            "sf_record_id": "a5KR500000lTRcuMAG",
            "source_revision": "2026-08-19T22:33:25.000+00:00",
            "request_id": "52bd2117-4bd7-4609-984f-477a9e40596c",
            "policy_version": POLICY_VERSION,
            "dealhub": evidence,
            "contract": dict(evidence),
        }

    def post(self, body: bytes):
        request = urllib.request.Request(
            f"{self.base_url}/v1/validate-bound",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=2)

    def test_bound_route_returns_redacted_no_store_response(self):
        with self.post(json.dumps(self.payload()).encode("utf-8")) as response:
            result = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertFalse(result["proceed"])
            self.assertEqual(result["mismatches"], [])

    def test_bound_route_rejects_duplicate_json_keys(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(b'{"co_number":"CO-1","co_number":"CO-2"}')
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
