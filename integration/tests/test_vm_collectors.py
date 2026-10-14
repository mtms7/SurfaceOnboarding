"""VM collectors (docs/43 part B): the Redash key from a root-owned file instead of DPAPI, and the writable production-clone
folder that a snapshot ingest can never replace. Hermetic: temporary files and patched environment only."""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.redash_inventory_collector as collector
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import state_paths
from integration.onboarding import vm_snapshot as vs

KEY = "A" * 40
POSIX = unittest.skipUnless(os.name == "posix", "POSIX file modes")


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SURFACE_")}
    env.update(extra)
    return patch.dict(os.environ, env, clear=True)


class KeyFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "redash-key"

    def write(self, text=KEY, mode=0o640):
        self.path.write_text(text, encoding="ascii")
        if os.name == "posix":
            os.chmod(self.path, mode)

    @POSIX
    def test_a_private_regular_file_with_the_key_shape_loads(self):
        for mode in (0o640, 0o600, 0o400):
            self.write(KEY + "\n", mode)
            self.assertEqual(collector.load_key_file(self.path), KEY)

    @POSIX
    def test_unsafe_files_are_refused_with_a_reason_code_only(self):
        for mode in (0o660, 0o604, 0o666):
            self.write(mode=mode)
            with self.subTest(mode=oct(mode)), self.assertRaises(collector.CollectorError) as caught:
                collector.load_key_file(self.path)
            self.assertEqual(caught.exception.reason, "redash_key_file_unsafe")
        self.write()
        link = Path(self.tmp.name) / "link"
        link.symlink_to(self.path)
        with self.assertRaises(collector.CollectorError) as caught:
            collector.load_key_file(link)
        self.assertEqual(caught.exception.reason, "redash_key_file_unsafe")
        with self.assertRaises(collector.CollectorError):
            collector.load_key_file(self.tmp.name)

    @POSIX
    def test_bad_content_missing_or_oversize_files_are_refused(self):
        for text in ("short", "has spaces " + "A" * 30, "A" * 65, "", "ключ" * 10):
            try:
                self.write(text)
            except UnicodeError:
                continue
            with self.subTest(text=text[:10]), self.assertRaises(collector.CollectorError):
                collector.load_key_file(self.path)
        self.write("A" * 300)
        with self.assertRaises(collector.CollectorError):
            collector.load_key_file(self.path)
        with self.assertRaises(collector.CollectorError) as caught:
            collector.load_key_file(Path(self.tmp.name) / "missing")
        self.assertEqual(caught.exception.reason, "redash_key_unavailable")

    @POSIX
    def test_load_key_uses_the_file_from_the_environment_and_never_falls_back_to_dpapi(self):
        self.write()
        with clean_env(SURFACE_REDASH_KEY_FILE=str(self.path)):
            self.assertEqual(collector.load_key(), KEY)
        self.write(mode=0o666)
        with clean_env(SURFACE_REDASH_KEY_FILE=str(self.path)), \
                patch.object(collector, "_dpapi", side_effect=AssertionError("DPAPI must not be used")):
            with self.assertRaises(collector.CollectorError):
                collector.load_key()

    def test_without_the_variable_the_desktop_path_is_unchanged(self):
        with clean_env(), patch.object(collector, "key_path", return_value=Path(self.tmp.name) / "none"):
            with self.assertRaises(collector.CollectorError) as caught:
                collector.load_key()
        self.assertEqual(caught.exception.reason, "redash_key_unavailable")

    def test_the_key_never_appears_in_an_error_message(self):
        self.write("short-but-not-the-key" if os.name != "posix" else KEY + "x", mode=0o666)
        with clean_env(SURFACE_REDASH_KEY_FILE=str(self.path)), self.assertRaises(collector.CollectorError) as caught:
            collector.load_key()
        self.assertNotIn(KEY, str(caught.exception))


class CloneFolderTests(unittest.TestCase):
    def test_clone_root_is_its_own_folder_whenever_a_state_dir_is_configured(self):
        default = Path("desktop") / "inventory"
        with clean_env():
            self.assertEqual(state_paths.clone_inventory_root(default), default)
        with clean_env(SURFACE_ONBOARDING_STATE_DIR=str(Path(tempfile.gettempdir()) / "state")):
            self.assertEqual(state_paths.clone_inventory_root(default), Path(tempfile.gettempdir()) / "state" / "clone-inventory")
        with clean_env(SURFACE_ONBOARDING_RUNTIME="vm"):
            self.assertEqual(state_paths.clone_inventory_root(default),
                             Path(state_paths.VM_DEFAULT_STATE_DIR) / "clone-inventory")

    def test_the_clone_folder_is_outside_every_snapshot_directory(self):
        with clean_env(SURFACE_ONBOARDING_RUNTIME="vm"):
            clone = state_paths.clone_inventory_root(Path("x"))
            snapshots = vs.snapshots_root(state_paths.VM_DEFAULT_STATE_DIR)
        self.assertNotIn(snapshots, clone.parents)

    def test_collector_dashboard_and_runner_agree_on_the_folder(self):
        with clean_env(SURFACE_ONBOARDING_RUNTIME="vm"):
            expected = Path(state_paths.VM_DEFAULT_STATE_DIR) / "clone-inventory"
            self.assertEqual(inventory.clone_root(), expected)
            self.assertEqual(dashboard.clone_root(), expected)
            self.assertEqual(runner.clone_root(), expected)
            self.assertEqual(collector.state_dir(), Path(state_paths.VM_DEFAULT_STATE_DIR) / "redash")

    def test_desktop_defaults_are_unchanged(self):
        with clean_env():
            self.assertEqual(inventory.clone_root(), inventory.default_root())
            self.assertEqual(dashboard.clone_root(), dashboard.inventory_root())

    def test_a_vm_clone_snapshot_is_read_from_the_clone_folder_not_the_published_snapshot(self):
        seen = []
        with clean_env(SURFACE_ONBOARDING_RUNTIME="vm"), \
                patch.object(inventory, "load_latest", lambda root, env, **kw: seen.append((Path(root), env)) or {"tenants": []}):
            dashboard._clone_info()
        self.assertEqual(seen[0], (Path(state_paths.VM_DEFAULT_STATE_DIR) / "clone-inventory", "prod-clone"))


class UnitTemplateTests(unittest.TestCase):
    FOLDER = Path(__file__).resolve().parents[1] / "deployment" / "vm_pilot"
    HARDENING = ("NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes", "MemoryMax=", "CPUQuota=", "TasksMax=",
                 "CapabilityBoundingSet=", "SystemCallFilter=@system-service", "ReadOnlyPaths=/opt/surface-onboarding")

    def unit(self, name):
        return (self.FOLDER / name).read_text(encoding="utf-8")

    def test_the_salesforce_refresh_unit_is_gated_credentialed_and_not_in_vm_runtime(self):
        text = self.unit("surface-onboarding-sf-refresh.service.template")
        for needed in ("ConditionPathExists=/etc/surface-onboarding/approved-sf-reader", "User=surface-onboarding",
                       "SupplementaryGroups=surface-sf-reader", "Type=oneshot", "SURFACE_VM_SF_CONFIG_FILE=/etc/surface-onboarding/sf-reader.json",
                       "tools/vm_refresh_snapshot.py --key-file /etc/surface-onboarding/snapshot.key", *self.HARDENING):
            self.assertIn(needed, text)
        self.assertNotRegex(text, r"(?m)^Environment=SURFACE_ONBOARDING_RUNTIME")
        self.assertNotIn("sf-reader.pem", text.replace("holds the private key", ""), "the key path is read from the config, not the unit")

    def test_the_redash_unit_is_gated_credentialed_and_exit_2_is_not_a_failure(self):
        text = self.unit("surface-onboarding-redash-collect.service.template")
        for needed in ("ConditionPathExists=/etc/surface-onboarding/approved-redash-collector",
                       "SupplementaryGroups=surface-redash-reader", "SURFACE_REDASH_KEY_FILE=/etc/surface-onboarding/redash-key",
                       "redash_inventory_collector.py --collect", "SuccessExitStatus=2", *self.HARDENING):
            self.assertIn(needed, text)

    def test_the_dashboard_unit_never_gets_either_credential_group(self):
        text = self.unit("surface-onboarding-dashboard.service.template")
        for group in ("surface-sf-reader", "surface-redash-reader", "sf-reader", "redash-key"):
            self.assertNotIn(group, text)

    def test_timers_point_at_their_services_and_are_install_wanted_by_timers(self):
        for timer, service in (("sf-refresh", "sf-refresh"), ("redash-collect", "redash-collect")):
            text = self.unit(f"surface-onboarding-{timer}.timer.template")
            self.assertIn(f"Unit=surface-onboarding-{service}.service", text)
            self.assertIn("WantedBy=timers.target", text)
            self.assertIn("OnUnitActiveSec=", text)


class BannerTests(unittest.TestCase):
    def banner(self, producer):
        manifest = {"created_at": "2026-10-14T08:00:00Z", "producer": producer, "files": []}
        with patch.object(dashboard, "snapshot_root", return_value=Path("x")), \
                patch.object(vs, "read_current_manifest", return_value=manifest):
            return dashboard.snapshot_banner(datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc))

    def test_a_vm_refresh_says_so_and_a_desktop_publish_keeps_its_wording(self):
        self.assertIn("refreshed on the VM from Salesforce", self.banner(vs.PRODUCER_VM_READER))
        self.assertIn("(published from the desktop)", self.banner("desktop"))
        self.assertNotIn("stale", self.banner(vs.PRODUCER_VM_READER))


if __name__ == "__main__":
    unittest.main()
