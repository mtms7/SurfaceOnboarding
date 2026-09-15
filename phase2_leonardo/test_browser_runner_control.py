from __future__ import annotations

import unittest

from phase2_leonardo.browser_runner_control import BrowserRunnerControlPlane, ControlPlaneBlocked


class TestOnlyProtector:
    """In-memory test double; deliberately not cryptography or deployment code."""
    def __init__(self): self.items: dict[str, bytes] = {}
    def seal(self, plaintext: bytes) -> str:
        ref = f"test-{len(self.items)}"; self.items[ref] = plaintext; return ref
    def open(self, ref: str) -> bytes: return self.items[ref]
    def destroy(self, ref: str) -> None: self.items.pop(ref, None)


class BrowserRunnerControlTests(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.protector = TestOnlyProtector()
        self.control = BrowserRunnerControlPlane(self.protector, now=lambda: self.clock[0])

    def grant(self):
        return self.control.create_grant(reference="CO-0740", correlation_id="opaque-1", approved=True, manifest=b"synthetic")

    def test_manual_login_is_required_and_consumes_the_grant(self):
        grant = self.grant()
        with self.assertRaises(ControlPlaneBlocked): self.control.release_manifest_once(grant.grant_id)
        self.control.record_manual_login(grant.grant_id, "leonardo-development")
        self.assertEqual(self.control.release_manifest_once(grant.grant_id), b"synthetic")
        self.assertFalse(self.protector.items)
        with self.assertRaises(ControlPlaneBlocked): self.control.release_manifest_once(grant.grant_id)

    def test_expiry_and_destination_allowlist_fail_closed(self):
        grant = self.grant()
        with self.assertRaises(ControlPlaneBlocked): self.control.record_manual_login(grant.grant_id, "production")
        self.clock[0] += 901
        self.control.expire()
        self.assertFalse(self.protector.items)
        with self.assertRaises(ControlPlaneBlocked): self.control.record_manual_login(grant.grant_id, "salesforce")

    def test_unapproved_and_invalid_grants_are_rejected(self):
        with self.assertRaises(ControlPlaneBlocked):
            self.control.create_grant(reference="CO-0740", correlation_id="opaque", approved=False, manifest=b"x")
        with self.assertRaises(ControlPlaneBlocked):
            self.control.create_grant(reference="bad", correlation_id="opaque", approved=True, manifest=b"x")


if __name__ == "__main__":
    unittest.main()
