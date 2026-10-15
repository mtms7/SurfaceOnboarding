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


class CloneRootTests(unittest.TestCase):
    """Owner decision 2026-10-14: the clone travels in the signed snapshot, so it is read next to the Dev inventory."""

    def test_the_clone_root_is_the_inventory_root_everywhere(self):
        default = Path("desktop") / "inventory"
        with clean_env():
            self.assertEqual(state_paths.clone_inventory_root(default), state_paths.inventory_root(default))
            self.assertEqual(inventory.clone_root(), inventory.default_root())
            self.assertEqual(dashboard.clone_root(), dashboard.inventory_root())
        with clean_env(SURFACE_ONBOARDING_STATE_DIR=str(Path(tempfile.gettempdir()) / "state")):
            self.assertEqual(state_paths.clone_inventory_root(default), state_paths.inventory_root(default))

    def test_on_the_vm_the_clone_is_read_from_the_current_snapshot(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            root = vs.snapshots_root(state)
            name = "snapshot-20261014T120000Z"
            (root / name).mkdir(parents=True)
            vs._switch_pointer(root, name)
            with clean_env(SURFACE_ONBOARDING_RUNTIME="vm", SURFACE_ONBOARDING_STATE_DIR=str(state)), \
                    patch.object(inventory, "load_latest",
                                 lambda r, env, **kw: seen.append((Path(r), env)) or {"tenants": []}):
                dashboard._clone_info()
                self.assertEqual(runner.clone_root(), dashboard.clone_root())
        self.assertEqual(seen[0], (root / name / "leonardo-inventory", "prod-clone"))

    def test_the_vm_has_no_redash_collection_writing_into_a_snapshot(self):
        with clean_env(SURFACE_ONBOARDING_RUNTIME="vm"):
            self.assertEqual(collector.state_dir(), Path(state_paths.VM_DEFAULT_STATE_DIR) / "redash")


class UnitTemplateTests(unittest.TestCase):
    FOLDER = Path(__file__).resolve().parents[1] / "deployment" / "vm_pilot"
    HARDENING = ("NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes", "MemoryMax=", "CPUQuota=", "TasksMax=",
                 "CapabilityBoundingSet=", "SystemCallFilter=@system-service", "ReadOnlyPaths=/opt/surface-onboarding")

    def unit(self, name):
        return (self.FOLDER / name).read_text(encoding="utf-8")

    def test_the_salesforce_refresh_unit_keeps_the_session_in_memory_and_is_gated(self):
        text = self.unit("surface-onboarding-sf-refresh.service.template")
        for needed in ("ConditionPathExists=/etc/surface-onboarding/approved-sf-reader", "User=surface-onboarding", "Type=oneshot",
                       "--transport cli --session-dir /run/surface-onboarding-sf --key-file /etc/surface-onboarding/snapshot.key",
                       "ReadWritePaths=/var/lib/surface-onboarding /run/surface-onboarding-sf",
                       "SURFACE_SF_SESSION_DIR=/run/surface-onboarding-sf", *[h for h in self.HARDENING]):
            self.assertIn(needed, text)
        self.assertNotRegex(text, r"(?m)^Environment=SURFACE_ONBOARDING_RUNTIME")
        # No stored credential of any kind: no key/config groups, no service identity file, no HOME on a disk path.
        for forbidden in ("SupplementaryGroups", "sf-reader.pem", "sf-reader.json", "SURFACE_VM_SF_CONFIG_FILE", "/var/lib/surface-onboarding/sf",
                          "password=", "token="):
            self.assertNotIn(forbidden, text, forbidden)
        # The only relaxed hardening line is documented: Node's JIT needs it.
        self.assertIn("MemoryDenyWriteExecute=no", text)
        self.assertIn("JIT", text)

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


class PublishClonePairTests(unittest.TestCase):
    """The desktop publisher includes the collector's clone as its own pair (owner decision 2026-10-14)."""

    def make(self, root, env, prefix, stamp="20261014T120000Z", name=None):
        folder = Path(root) / env
        folder.mkdir(parents=True)
        file_name = name or f"{prefix}inventory-{stamp}.json"
        (folder / "latest.json").write_text('{"file": "%s"}' % file_name, encoding="utf-8")
        (folder / file_name).write_text('{"tenants": []}', encoding="utf-8")

    def collect(self, dev_root, clone_root):
        import tools.publish_vm_snapshot as publisher

        with patch.object(inventory, "default_root", return_value=Path(dev_root)), \
                patch.object(inventory, "clone_root", return_value=Path(clone_root)):
            return publisher.collect_inventory_files()

    def test_both_pairs_are_collected_under_their_own_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make(tmp, "dev", "leonardo-dev-")
            self.make(tmp, "prod-clone", "redash-prod-clone-")
            files = self.collect(tmp, tmp)
        self.assertEqual(sorted(files), sorted([vs.INVENTORY_DEV_LATEST, vs.INVENTORY_DEV_SNAPSHOT,
                                                vs.INVENTORY_CLONE_LATEST, vs.INVENTORY_CLONE_SNAPSHOT]))

    def test_a_missing_clone_or_one_with_the_wrong_prefix_is_simply_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make(tmp, "dev", "leonardo-dev-")
            self.assertEqual(sorted(self.collect(tmp, tmp)), sorted([vs.INVENTORY_DEV_LATEST, vs.INVENTORY_DEV_SNAPSHOT]))
        with tempfile.TemporaryDirectory() as tmp:
            self.make(tmp, "prod-clone", "", name="leonardo-dev-inventory-20261014T120000Z.json")
            self.assertEqual(self.collect(tmp, tmp), {})

    def test_an_oversize_clone_is_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make(tmp, "prod-clone", "redash-prod-clone-")
            with patch.object(vs, "MAX_FILE_BYTES", 5):
                self.assertEqual(self.collect(tmp, tmp), {})
