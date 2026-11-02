"""Owner report 2026-11-02: CO-0825 (UUID set by hand, tenant in the US environment, so not in the EU clone) showed
"Clear" / "Next up" on the dashboard while a direct match said "UUID set, not in clone". One Salesforce read of the CO
failed after the restart; the display cache kept that failure (None) and the match ran as if the CO had no UUID.
Now a failed read of the CO is "Not checked" (never "Clear") and is never cached.
Hermetic: fake reads, a fake clone; nothing reads Salesforce.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_match as match
from integration.onboarding import production_views as views

CO, PRODUCT, KIND = "CO-0825", "Credential Exposure", "New Product Onboarding"
SNAPSHOT = {"tenants": [{"account_name": "Other", "account_uuid": "b" * 32, "alternate_domains": []}], "captured_at": None}
IDENTITY = {"uuid": "a" * 32, "names": ("Nez Perce",), "domains": ("example.org",)}


class IdentityUnreadNotClearTests(unittest.TestCase):
    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)
        token = dashboard._display_reads.set(True)
        self.addCleanup(dashboard._display_reads.reset, token)
        self.identity = mock.Mock(return_value=None)
        self.full = mock.Mock(side_effect=lambda *a, **k: {"status": "uuid_not_in_clone" if k.get("account_uuid")
                                                           else "not_in_production", "reason": ""})
        for patch in (mock.patch.object(runner, "_production_expectation", return_value=("ce", {"s": 1}, {"p": 1})),
                      mock.patch.object(runner, "_identity_for", self.identity),
                      mock.patch.object(runner, "clone_root", return_value=Path("unused")),
                      mock.patch.object(match, "load_snapshot", return_value=SNAPSHOT),
                      mock.patch.object(match, "production_match", self.full),
                      mock.patch.object(runner, "_snapshot_account_uuid", return_value=None),
                      mock.patch.object(runner, "_apply_verified_tenant", side_effect=lambda result, *_: result),
                      mock.patch("integration.onboarding.state_paths.vm_mode", return_value=False)):
            patch.start()
            self.addCleanup(patch.stop)

    def show(self, stamp):
        return dashboard.production_match_display(CO, PRODUCT, KIND, stamp)

    def test_an_unreadable_co_is_not_checked_never_clear(self):
        result = self.show("s1")
        self.assertEqual((result["status"], result["reason"]), ("not_checked", "co_identity_unreadable"))
        self.full.assert_not_called()  # never a match run as if the CO had no UUID
        chip = views.production_check(result)
        self.assertEqual(chip["label"], "Not checked")
        self.assertTrue(chip["blocked"])
        self.assertIn("Account UUID", chip["reason"])

    def test_a_failed_identity_read_is_not_cached(self):
        self.show("s1")
        self.identity.return_value = IDENTITY
        result = self.show("s2")  # a new stamp redoes the match; the failed read is tried again
        self.assertEqual(self.identity.call_count, 2)
        self.assertEqual(result["status"], "uuid_not_in_clone")
        self.assertEqual(self.full.call_args.kwargs["account_uuid"], IDENTITY["uuid"])

    def test_a_good_identity_read_is_still_cached(self):
        self.identity.return_value = IDENTITY
        self.show("s1")
        self.show("s2")
        self.assertEqual(self.identity.call_count, 1)

    def test_the_direct_runner_match_fails_closed_too(self):
        token = runner.MATCH_READS.set(None)
        self.addCleanup(runner.MATCH_READS.reset, token)
        result = runner.production_match_for(CO, PRODUCT, KIND)
        self.assertEqual(result["status"], "not_checked")
        self.full.assert_not_called()

    def test_the_vm_path_is_unchanged(self):
        with mock.patch("integration.onboarding.state_paths.vm_mode", return_value=True), \
                mock.patch.object(runner, "_production_expectation", return_value=("ce", {"s": 1}, {"p": 1})):
            result = runner.production_match_for(CO, PRODUCT, KIND)
        self.identity.assert_not_called()
        self.assertEqual(result["status"], "not_in_production")


if __name__ == "__main__":
    unittest.main()
