"""VM-started onboarding run (owner decision 2026-10-27, docs/62): the dashboard side, VM request route and button, PC start path and
worker, the form values for the create, and the dashboard start script. Locked by default; hermetic (no network, no browser)."""

from __future__ import annotations

import json
import re
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_create_ui as create_ui
from integration.onboarding import remote_start as rs
from integration.onboarding import vm_snapshot as vs
from integration.tests import test_vm_snapshot_dashboard as snap
from integration.tests.test_vm_proxy_mode import OWNER, PUBLIC, VIEWER, vm_env

REPO = Path(__file__).resolve().parents[2]
PATH = "/attended/remote-start-request"
REV = "2026-10-01T10:00:00.000+0000"  # the LastModifiedDate of the CO rows in the VM test snapshot


class RemoteStartValuesTests(unittest.TestCase):
    """``create_ui.remote_start_values``: the form values the page's one button would post, built server-side."""

    def inputs(self, approved=True):
        row = {"Onboarding_Approval_Status__c": "Approved" if approved else "Pending", "LastModifiedDate": "2026-10-27T10:00:00.000+0000"}
        return types.SimpleNamespace(reference="CO-0812", vm=False, row=row)

    def test_a_vm_or_a_locked_build_never_gets_values(self):
        vm = self.inputs()
        vm.vm = True
        self.assertEqual(create_ui.remote_start_values(vm), (None, "vm_read_only"))
        with patch.object(create_ui, "build_lock", return_value="production_runner_not_supported"):
            self.assertEqual(create_ui.remote_start_values(self.inputs()), (None, "production_runner_not_supported"))

    def test_with_every_gate_open_the_values_carry_a_token_the_create_will_consume(self):
        ctx = {"approval": {"approval_ref": "PROD-CE-0812-1"}, "route": "case_2_new_ce_only", "env": {}}
        with patch.object(create_ui, "build_lock", return_value=None), patch.object(create_ui, "gate", return_value=(None, ctx)):
            values, code = create_ui.remote_start_values(self.inputs())
        self.assertEqual(code, "")
        self.assertEqual(set(values), {"reference", "nonce"})
        binding = create_ui._create_binding("CO-0812", "2026-10-27T10:00:00.000+0000", "PROD-CE-0812-1", ctx)
        self.assertTrue(create_ui.TOKENS.consume("create", "CO-0812", binding, values["nonce"]))

    def test_a_missing_approval_on_an_approved_co_asks_for_the_one_click_approval(self):
        with patch.object(create_ui, "build_lock", return_value=None), \
                patch.object(create_ui, "gate", return_value=("approval_missing", {})):
            values, code = create_ui.remote_start_values(self.inputs())
        self.assertEqual((code, values["issue_approval"]), ("", "1"))
        self.assertTrue(create_ui.TOKENS.consume("create", "CO-0812", create_ui._binding(
            "CO-0812", "2026-10-27T10:00:00.000+0000", "no-approval", "create"), values["nonce"]))

    def test_any_other_refusal_or_an_unapproved_co_gets_no_values(self):
        with patch.object(create_ui, "build_lock", return_value=None), \
                patch.object(create_ui, "gate", return_value=("approval_expired", {})):
            self.assertEqual(create_ui.remote_start_values(self.inputs()), (None, "approval_expired"))
        with patch.object(create_ui, "build_lock", return_value=None), \
                patch.object(create_ui, "gate", return_value=("approval_missing", {})):
            self.assertEqual(create_ui.remote_start_values(self.inputs(approved=False)), (None, "approval_missing"))


class PcStartTests(unittest.TestCase):
    def test_it_goes_through_the_same_create_handler_as_the_button_with_the_owner_as_operator(self):
        seen = {}

        def fake_post(values, inputs, launch, issue=None):
            seen.update(values=dict(values), inputs=inputs, launch=launch, issue=issue)
            return "launched"

        row = {"LastModifiedDate": "2026-10-27T10:00:00.000+0000"}
        with patch.object(dashboard, "vm_mode", return_value=False), patch.object(dashboard, "_owner_session_live", return_value=True), \
                patch.object(dashboard, "detail_row", return_value=row), \
                patch.object(dashboard, "_create_ui_inputs", return_value="INPUTS") as make_inputs, \
                patch.object(create_ui, "remote_start_values", return_value=({"reference": "CO-0812", "nonce": "t"}, "")), \
                patch.object(create_ui, "handle_create_post", side_effect=fake_post):
            self.assertEqual(dashboard._start_remote_create("CO-0812", "owner@example.test", "2026-10-27T10:00:00.000+0000"), "launched")
        make_inputs.assert_called_once_with("CO-0812", row, "owner@example.test")
        self.assertEqual(seen["values"], {"reference": "CO-0812", "nonce": "t"})
        self.assertIsNot(seen["launch"], dashboard._launch_production_create_followed)  # wrapped: it adds the fresh sign-in switch
        self.assertEqual(seen["inputs"], "INPUTS")

    def test_it_refuses_on_the_vm_and_never_calls_the_create_when_a_gate_says_no(self):
        with patch.object(dashboard, "vm_mode", return_value=True):
            self.assertEqual(dashboard._start_remote_create("CO-0812", "owner@example.test", "x"), "vm_read_only")
        with patch.object(dashboard, "vm_mode", return_value=False), patch.object(dashboard, "_owner_session_live", return_value=True), \
                patch.object(dashboard, "detail_row", return_value={"LastModifiedDate": "x"}), \
                patch.object(dashboard, "_create_ui_inputs", return_value="INPUTS"), \
                patch.object(create_ui, "remote_start_values", return_value=(None, "approval_expired")), \
                patch.object(create_ui, "handle_create_post") as post:
            self.assertEqual(dashboard._start_remote_create("CO-0812", "owner@example.test", "x"), "approval_expired")
        post.assert_not_called()

    def test_a_salesforce_read_failure_is_a_refusal_not_a_crash(self):
        with patch.object(dashboard, "vm_mode", return_value=False), patch.object(dashboard, "_owner_session_live", return_value=True), \
                patch.object(dashboard, "detail_row", side_effect=dashboard.ReadUnavailable()):
            self.assertEqual(dashboard._start_remote_create("CO-0812", "owner@example.test", "x"), "salesforce_unavailable")

    def test_a_changed_revision_or_a_missing_dashboard_sign_in_stops_it_before_any_gate(self):
        with patch.object(dashboard, "vm_mode", return_value=False), patch.object(dashboard, "_owner_session_live", return_value=True), \
                patch.object(dashboard, "detail_row", return_value={"LastModifiedDate": "2026-10-27T11:00:00.000+0000"}), \
                patch.object(create_ui, "remote_start_values") as values:
            self.assertEqual(dashboard._start_remote_create("CO-0812", "owner@example.test", "2026-10-27T10:00:00.000+0000"),
                             "revision_changed")
            self.assertEqual(dashboard._start_remote_create("CO-0812", "owner@example.test", ""), "revision_changed")
        values.assert_not_called()
        with patch.object(dashboard, "vm_mode", return_value=False), patch.object(dashboard, "_owner_session_live", return_value=False), \
                patch.object(dashboard, "detail_row") as read:
            self.assertEqual(dashboard._start_remote_create("CO-0812", "owner@example.test", "x"), "dashboard_not_signed_in")
        read.assert_not_called()

    def test_the_owner_session_check_needs_a_live_session_of_exactly_that_owner(self):
        from time import monotonic
        with patch.dict(dashboard._dashboard_sessions, {"t1": (monotonic() + 600, "Owner@Example.Test"),
                                                        "t2": (monotonic() - 1, "other@example.test")}, clear=True):
            self.assertTrue(dashboard._owner_session_live("owner@example.test"))
            self.assertFalse(dashboard._owner_session_live("other@example.test"))  # expired
            self.assertFalse(dashboard._owner_session_live("nobody@example.test"))

    def test_a_bad_reference_is_refused_before_any_read(self):
        with patch.object(dashboard, "vm_mode", return_value=False), patch.object(dashboard, "detail_row") as read:
            self.assertEqual(dashboard._start_remote_create("CO-1; x", "owner@example.test", "x"), "invalid_request")
        read.assert_not_called()


class WorkerWiringTests(unittest.TestCase):
    def test_no_worker_without_the_switch_or_on_the_vm(self):
        with patch.object(rs, "start_worker") as worker, patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(dashboard.start_remote_start_worker())
        worker.assert_not_called()
        with patch.object(rs.PcConfig, "from_env", return_value=object()), patch.object(dashboard, "vm_mode", return_value=True), \
                patch.object(rs, "start_worker") as worker:
            self.assertIsNone(dashboard.start_remote_start_worker())
        worker.assert_not_called()

    def test_the_worker_needs_production_sessions_to_be_on_as_well(self):
        config = object()
        with patch.object(rs.PcConfig, "from_env", return_value=config), patch.object(dashboard, "vm_mode", return_value=False), \
                patch.object(dashboard, "_production_sessions_enabled", return_value=False), patch.object(rs, "start_worker") as worker:
            self.assertIsNone(dashboard.start_remote_start_worker())
        worker.assert_not_called()
        with patch.object(rs.PcConfig, "from_env", return_value=config), patch.object(dashboard, "vm_mode", return_value=False), \
                patch.object(dashboard, "_production_sessions_enabled", return_value=True), \
                patch.object(rs, "start_worker", return_value="THREAD") as worker, \
                patch.object(dashboard.state_paths, "state_dir", return_value=None):
            self.assertEqual(dashboard.start_remote_start_worker(), "THREAD")
        self.assertIs(worker.call_args.args[0], config)
        self.assertIs(worker.call_args.args[2], dashboard._start_remote_create)


class VmRequestRouteTests(snap.SnapshotDashboardCase):
    """The one VM POST this decision adds: an owner-only request that carries no create authority."""

    def on(self):
        return vm_env(SURFACE_ONBOARDING_REMOTE_START="1", SURFACE_ONBOARDING_REMOTE_START_OWNER=OWNER,
                      SURFACE_ONBOARDING_STATE_DIR=str(self.state))

    def post_request(self, email=OWNER, reference="CO-0001"):
        return self.post(PATH, email, reference=reference)

    def request_file(self):
        return self.state / rs.REQUEST_FILE

    def test_it_is_locked_by_default(self):
        status, _, _ = self.post_request()
        self.assertEqual(status, 403)
        self.assertFalse(self.request_file().exists())

    def test_the_owner_can_request_an_approved_ce_only_co_and_nothing_else_is_written(self):
        with self.on():
            status, headers, _ = self.post_request()
        self.assertEqual(status, 303)
        self.assertIn("/co/CO-0001", headers.get("Location", ""))
        request = json.loads(self.request_file().read_text(encoding="utf-8"))
        self.assertEqual((request["co_reference"], request["requested_by"], request["delivered_at"]), ("CO-0001", OWNER, None))
        self.assertEqual(request["revision"], REV)  # the revision the owner saw in the snapshot travels with the request

    def test_an_operator_who_is_not_the_configured_owner_cannot_ask(self):
        with vm_env(SURFACE_ONBOARDING_REMOTE_START="1", SURFACE_ONBOARDING_REMOTE_START_OWNER="someone.else@example.test",
                    SURFACE_ONBOARDING_STATE_DIR=str(self.state)):
            status, _, body = self.post_request()  # OWNER is an operator on the VM but not the configured remote-start owner
        self.assertEqual(status, 403)
        self.assertIn("not_the_owner", body)
        self.assertFalse(self.request_file().exists())

    def test_without_an_owner_setting_the_feature_stays_off(self):
        with vm_env(SURFACE_ONBOARDING_REMOTE_START="1", SURFACE_ONBOARDING_STATE_DIR=str(self.state)):
            self.assertEqual(self.post_request()[0], 403)
        self.assertFalse(self.request_file().exists())

    def test_viewers_strangers_and_bad_references_cannot(self):
        with self.on():
            self.assertEqual(self.post_request(VIEWER)[0], 403)
            self.assertEqual(self.post_request("stranger@example.com")[0], 403)
            self.assertIn(self.post_request(reference="CO-1; x")[0], (400, 409))
        self.assertFalse(self.request_file().exists())

    def test_an_unsupported_route_is_refused(self):
        with self.on():
            status, _, body = self.post_request(reference="CO-0002")  # Surface only: no automated production create
        self.assertEqual(status, 409)
        self.assertIn("route_not_supported", body)
        self.assertFalse(self.request_file().exists())

    def test_a_second_request_while_one_is_pending_is_refused(self):
        with self.on():
            self.assertEqual(self.post_request()[0], 303)
            status, _, body = self.post_request()
        self.assertEqual(status, 409)
        self.assertIn("request_pending", body)

    def test_a_co_that_is_not_approved_in_the_snapshot_is_refused(self):
        details = json.loads(snap.FILES[vs.CO_DETAILS_FILE])
        details["details"]["CO-0001"]["Onboarding_Approval_Status__c"] = "Pending"
        files = {**snap.FILES, vs.CO_DETAILS_FILE: json.dumps(details).encode()}
        vs.ingest_bundle(vs.build_bundle(files, snap.KEY, "desktop", datetime.now(timezone.utc)), snap.KEY, self.state)
        dashboard.clear_display_cache()
        with self.on():
            status, _, body = self.post_request()
        self.assertEqual(status, 409)
        self.assertIn("not_approved", body)

    def test_the_page_button_exists_only_when_enabled_and_for_an_eligible_co(self):
        _, _, off = self.request("GET", "/co/CO-0001?env=prod", {dashboard.PROXY_IDENTITY_HEADER: OWNER})
        self.assertNotIn(PATH, off)
        with self.on():
            _, _, viewer = self.request("GET", "/co/CO-0001?env=prod", {dashboard.PROXY_IDENTITY_HEADER: VIEWER})
        self.assertNotIn(PATH, viewer)  # a viewer is not offered a button it cannot use
        with self.on():
            _, _, on = self.request("GET", "/co/CO-0001?env=prod", {dashboard.PROXY_IDENTITY_HEADER: OWNER})
            _, _, other = self.request("GET", "/co/CO-0002?env=prod", {dashboard.PROXY_IDENTITY_HEADER: OWNER})
        self.assertIn(PATH, on)
        self.assertIn("SSO and MFA", on)
        self.assertNotIn(PATH, other)  # Surface only: not eligible


class StartScriptTests(unittest.TestCase):
    def test_the_dashboard_start_script_has_an_off_by_default_remote_start_switch(self):
        text = (REPO / "tools" / "start_attended_dashboard.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$RemoteStart", text)
        self.assertIn("SURFACE_ONBOARDING_REMOTE_START", text)
        self.assertRegex(text, r"(?s)if \(\$RemoteStart\).*?else.*?Remove-Item")
        for name in ("SURFACE_ONBOARDING_REMOTE_START_OPERATOR", "SURFACE_REMOTE_START_VM_HOST", "SURFACE_REMOTE_START_PUSH_KEY",
                     "SURFACE_REMOTE_START_HMAC_KEY"):
            self.assertGreaterEqual(text.count(name), 2, name)  # set when on AND cleared when off, not only the enable variable
        self.assertNotRegex(text, r"(?i)password\s*=|Bearer\s+[A-Za-z0-9]|BEGIN (OPENSSH|RSA) PRIVATE")


if __name__ == "__main__":
    unittest.main()
