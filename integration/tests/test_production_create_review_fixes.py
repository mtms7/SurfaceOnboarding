"""Tests for the review fixes (docs/53): positive search control, launch ticket, pre-arm checks, strict readback expectations,
owner review of a not-clicked attempt, and the hardened UUID write. Fakes only."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from integration.onboarding import production_contract as contract
from integration.onboarding import production_create_live as live
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_create_ui as ui
from integration.onboarding import production_launch_ticket as ticket
from integration.tests import test_production_create_live as tl
from integration.tests import test_production_create_ui as tu
import tools.clear_production_attempt as clear

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
CO, REF, REV = "CO-0798", "PROD-CE-0798-20261017", "rev1"


class TicketTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_valid_ticket_is_consumed_once(self):
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW + timedelta(seconds=5)), ("milton", ""))
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW)[0], None)

    def test_binding_expiry_and_garbage(self):
        for args in ((CO, "other", REF), (CO, REV, "PROD-CE-0001-20261017")):
            ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
            self.assertEqual(ticket.consume(self.dir, *args, NOW)[0], None)
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW + ticket.TTL)[0], None)
        (self.dir / ticket.SUBDIR / (CO + ".json")).write_text("junk", encoding="utf-8")
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW)[0], None)
        with self.assertRaises(ValueError):
            ticket.mint(self.dir, "bad", REV, REF, "m", NOW)
        with self.assertRaises(ValueError):
            ticket.mint(self.dir, CO, REV, REF, "", NOW)

    def test_the_mac_protects_every_field_including_the_operator(self):
        for field, value in (("operator", "someone.else"), ("revision", "x"), ("expires_at", "2999-01-01T00:00:00Z"), ("nonce", "0" * 32)):
            path = ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
            body = json.loads(path.read_text(encoding="utf-8"))
            body[field] = value
            path.write_text(json.dumps(body), encoding="utf-8")
            probe = REV if field != "revision" else "x"
            self.assertEqual(ticket.consume(self.dir, CO, probe, REF, NOW)[0], None, field)

    def test_unsigned_ticket_and_a_different_key_are_refused(self):
        path = ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        body = json.loads(path.read_text(encoding="utf-8"))
        del body["mac"]
        path.write_text(json.dumps(body), encoding="utf-8")
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW)[0], None)
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        (self.dir / ticket.SUBDIR / ticket.KEY_NAME).write_text("ab" * 32, encoding="ascii")
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW)[0], None)

    def test_key_lives_in_the_state_folder_and_discard_removes_an_unused_ticket(self):
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertTrue((self.dir / ticket.SUBDIR / ticket.KEY_NAME).is_file())
        ticket.discard(self.dir, CO)
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW)[0], None)
        ignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("production-launch-tickets/", ignore)

    def test_missing_ticket_refuses(self):
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW), (None, "launch_ticket_invalid"))


def mini_page(guard, *, sw=0, pages=None):
    page = SimpleNamespace(url=tl.ORIGIN + "/x")
    page.evaluate = lambda js, arg=None: {"count": sw}
    page.context = SimpleNamespace(pages=[page] if pages is None else [page, *pages])
    return page


class KeyHardeningTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.key = self.dir / ticket.SUBDIR / ticket.KEY_NAME

    def test_only_a_32_byte_hex_key_is_accepted_everything_else_is_missing(self):
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        good = self.key.read_text(encoding="ascii")
        self.assertEqual(len(good), 64)
        for bad in ("", "ab" * 15, "ab" * 33, "xyz" + "0" * 61, "a" * 63, "ab" * 32 + "00"):
            self.key.write_text(bad, encoding="ascii")
            self.assertIsNone(ticket._read_key(self.dir), repr(bad))
            self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW)[0], None, repr(bad))
            ticket.mint(self.dir, CO, REV, REF, "milton", NOW)  # minting replaces an invalid key atomically
            self.assertEqual(len(self.key.read_text(encoding="ascii")), 64, repr(bad))

    def test_a_ticket_is_never_signed_with_an_empty_key(self):
        self.key.parent.mkdir(parents=True)
        self.key.write_text("", encoding="ascii")  # crash between create and write left a 0-byte key
        path = ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertEqual(len(self.key.read_text(encoding="ascii")), 64)
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW + timedelta(seconds=1)), ("milton", ""))
        self.assertFalse(path.exists())

    def test_a_crash_after_the_temp_write_leaves_no_half_written_key_to_read(self):
        original = ticket.os.link
        ticket.os.link = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))
        try:
            with self.assertRaises(RuntimeError):
                ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        finally:
            ticket.os.link = original
        self.assertIsNone(ticket._read_key(self.dir))  # nothing partial is ever at the key path
        self.assertEqual([p.name for p in (self.dir / ticket.SUBDIR).glob(".key.*")], [])
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertIsNotNone(ticket._read_key(self.dir))

    def test_a_second_mint_reuses_the_existing_valid_key(self):
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        first = self.key.read_text(encoding="ascii")
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertEqual(first, self.key.read_text(encoding="ascii"))


class RootAndReplayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_one_shared_root_for_mint_and_consume_and_never_the_repo(self):
        env = {ticket.ENV_ROOT: str(self.dir / "r")}
        self.assertEqual(ticket.ticket_root(env), ticket.ticket_root(env))
        repo = Path(ticket.__file__).resolve().parents[2]
        for bad in (str(repo), str(repo / "integration"), "relative/dir", str(Path.home() / "OneDrive" / "x"),
                    str(Path.home() / "Documents" / "x")):
            with self.assertRaises(ticket.TicketRootError, msg=bad):
                ticket.ticket_root({ticket.ENV_ROOT: bad})
        with self.assertRaises(ValueError):
            ticket.mint(repo, CO, REV, REF, "milton", NOW)
        self.assertEqual(ticket.consume(repo, CO, REV, REF, NOW)[0], None)
        self.assertFalse((repo / ticket.SUBDIR).exists())

    def test_default_root_is_outside_the_repo_and_never_falls_back(self):
        root = ticket.ticket_root({"LOCALAPPDATA": str(self.dir)} if ticket.os.name == "nt" else {})
        self.assertNotIn(Path(ticket.__file__).resolve().parents[2], root.resolve().parents)
        if ticket.os.name == "nt":
            with self.assertRaises(ticket.TicketRootError):
                ticket.ticket_root({})

    def test_runner_and_dashboard_resolve_the_same_parent(self):
        import tools.attended_ce_only_playwright as runner
        env = {ticket.ENV_ROOT: str(self.dir / "shared")}
        from unittest import mock
        with mock.patch.dict("os.environ", env):
            self.assertEqual(ticket.ticket_root(), self.dir / "shared")
            ticket.mint(ticket.ticket_root(), CO, REV, REF, "milton", NOW)
            self.assertEqual(ticket.consume(ticket.ticket_root(), CO, REV, REF, NOW + timedelta(seconds=1)), ("milton", ""))
        self.assertTrue(hasattr(runner, "run_production_create"))

    def test_a_copied_ticket_cannot_be_replayed_inside_the_ttl(self):
        path = ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        copy = path.read_text(encoding="utf-8")
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW + timedelta(seconds=1)), ("milton", ""))
        path.write_text(copy, encoding="utf-8")  # the attacker puts the copy back
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW + timedelta(seconds=2))[0], None)
        self.assertEqual(len((self.dir / ticket.SUBDIR / ticket.USED_NAME).read_text().split()), 1)

    def test_non_object_json_and_unreadable_used_log_refuse_cleanly(self):
        path = ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        for junk in ("[]", "[1,2]", "null", "\"x\"", "7"):
            path.write_text(junk, encoding="utf-8")
            self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW), (None, "launch_ticket_invalid"), junk)
            ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        used = self.dir / ticket.SUBDIR / ticket.USED_NAME
        used.mkdir()  # an unreadable nonce log must fail closed
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW)[0], None)


class HardeningFailureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.addCleanup(setattr, ticket, "HARDEN", ticket.HARDEN)

    def test_a_failed_hardening_means_no_ticket_and_it_is_checked_on_every_mint(self):
        calls = []
        ticket.HARDEN = lambda path: calls.append(path)
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertGreaterEqual(len(calls), 2)  # every mint, not only the first key creation

        def failing(path):
            raise OSError("ticket_folder_not_restricted")
        ticket.HARDEN = failing
        ticket.discard(self.dir, CO)
        with self.assertRaises(ValueError):
            ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertFalse((self.dir / ticket.SUBDIR / (CO + ".json")).exists())

    def test_the_dashboard_hook_treats_a_non_zero_icacls_exit_as_failure(self):
        import tools.serve_attended_open_onboardings_dashboard as dashboard
        from unittest import mock
        with mock.patch.object(dashboard.os, "name", "nt"), mock.patch.dict("os.environ", {"USERNAME": "me"}):
            with mock.patch.object(dashboard.subprocess, "run", return_value=SimpleNamespace(returncode=5)):
                with self.assertRaises(OSError):
                    dashboard._harden_ticket_folder(self.dir)
            with mock.patch.object(dashboard.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
                dashboard._harden_ticket_folder(self.dir)
            with mock.patch.object(dashboard.subprocess, "run", side_effect=OSError("x")):
                with self.assertRaises(OSError):
                    dashboard._harden_ticket_folder(self.dir)
        with mock.patch.object(dashboard.os, "name", "nt"), mock.patch.dict("os.environ", {"USERNAME": ""}):
            with self.assertRaises(OSError):
                dashboard._harden_ticket_folder(self.dir)

    def test_unc_and_shared_roots_are_refused(self):
        for bad in (r"\\server\share\x", "//server/share/x"):
            with self.assertRaises(ticket.TicketRootError, msg=bad):
                ticket.ticket_root({ticket.ENV_ROOT: bad})


class PreArmTests(unittest.TestCase):
    def test_service_worker_popup_and_extra_production_page_refuse(self):
        guard = live.LiveRequestGuard()
        self.assertIsNone(live.pre_arm_problem(mini_page(guard), guard))
        self.assertEqual(live.pre_arm_problem(mini_page(guard, sw=1), guard), "service_worker_present")
        other = SimpleNamespace(url=tl.ORIGIN + "/y")
        self.assertEqual(live.pre_arm_problem(mini_page(guard, pages=[other]), guard), "extra_pages_open")
        self.assertIsNone(live.pre_arm_problem(mini_page(guard, pages=[SimpleNamespace(url="http://127.0.0.1:8765/")]), guard))
        guard._on_popup(None)
        self.assertEqual(live.pre_arm_problem(mini_page(guard), guard), "extra_pages_open")

    def test_popup_handler_is_installed_and_a_blocked_pre_arm_means_no_click(self):
        guard = live.LiveRequestGuard()
        events = []
        page = tl.MiniPage(guard)
        page.on = lambda event, handler: events.append(event)
        page.evaluate = lambda js, arg=None: {"count": 1}
        routed, context_routed = [], []
        page.route = lambda pattern, handler: routed.append(pattern)
        page.context = SimpleNamespace(pages=[page], route=lambda pattern, handler: context_routed.append(pattern),
                                       on=lambda event, handler: events.append("ctx_" + event))
        guard.install(page)
        self.assertIn("popup", events)
        self.assertEqual(routed, ["**/*"])  # page level, like the dry run (2026-10-19 fix)
        self.assertEqual(context_routed, [])
        self.assertIn("ctx_page", events)
        clicks = live.LiveClickLog()
        self.assertEqual(live.click_confirm_once(page, guard, clicks), live.NOT_CLICKED)
        self.assertEqual((page.clicks, clicks.confirm_clicks, guard.armed), (0, 0, False))

    def test_scroll_precedes_the_hit_test_and_preflight_never_clicks(self):
        guard = live.LiveRequestGuard()
        order = []
        page = tl.MiniPage(guard)
        button = tl.MiniButton(page)
        page.get_by_role = lambda *a, **k: button
        button.scroll_into_view_if_needed = lambda: order.append("scroll")
        original = button.evaluate
        button.evaluate = lambda js: order.append("hit") or original(js)
        self.assertIsNone(live.confirm_preflight(page))
        self.assertEqual((order, page.clicks), (["scroll", "hit"], 0))
        for hit in (False, None):
            page.hit = hit
            self.assertEqual(live.confirm_preflight(page), "confirm_preflight_failed")


class ControlTests(unittest.TestCase):
    def test_clone_control_is_deterministic_live_and_unique(self):
        tenants = [{"account_name": "A Co", "account_domain": "a-co.example", "enabled": True},
                   {"account_name": "B Co", "account_domain": "b-co.example", "enabled": True},
                   {"account_name": "Dup", "account_domain": "d1.example", "enabled": True},
                   {"account_name": "Dup", "account_domain": "d2.example", "enabled": True},
                   {"account_name": "Gone", "account_domain": "g.example", "enabled": True, "is_deleted": True},
                   {"account_name": "Off", "account_domain": "o.example", "enabled": False}]
        first = live.clone_search_control(tenants)
        self.assertEqual(first, live.clone_search_control(list(reversed(tenants))))
        self.assertIn(first["name"], ("A Co", "B Co"))
        self.assertIsNone(live.clone_search_control([]))
        self.assertIsNone(live.clone_search_control("x"))

    def test_control_cannot_pass_through_the_name_field_or_a_shared_domain(self):
        leaky = [{"account_name": "acme.com", "account_domain": "acme.com", "enabled": True},
                 {"account_name": "Acme Holdings", "account_domain": "acme.example", "enabled": True},
                 {"account_name": "Zeta", "account_domain": "shared.example", "enabled": True},
                 {"account_name": "Other", "account_domain": "o.example", "alternate_domains": ["shared.example"], "enabled": True}]
        self.assertIsNone(live.clone_search_control(leaky))  # name holds the domain / its label; shared domain has two owners
        good = leaky + [{"account_name": "Quux Group", "account_domain": "q-g.example", "enabled": True}]
        self.assertEqual(live.clone_search_control(good)["name"], "Quux Group")
        self.assertIn("alternate", Path(live.__file__).read_text(encoding="utf-8"))

    def test_control_problem_table(self):
        control = {"name": "A Co", "domain": "a.example"}
        row = {"name": "a  co", "domain": "a.example"}
        ok = tl.reply([row])
        self.assertIsNone(live.control_problem(control, ok, ok))
        self.assertEqual(live.control_problem(None, ok, ok), "search_control_unavailable")
        self.assertEqual(live.control_problem(control, ok, tl.reply([])), "search_unproven")
        self.assertEqual(live.control_problem(control, tl.reply([]), ok), "search_unproven")
        self.assertEqual(live.control_problem(control, ok, {"status": 401}), "production_session_expired")
        clear_demo = tl.CLEAR_DEMO
        self.assertEqual(live.checked_duplicate_problem(control, (tl.reply([]), ok), tl.reply([]), tl.reply([]), clear_demo,
                                                         tl.NAME, tl.DOMAIN), "search_unproven")
        self.assertIsNone(live.checked_duplicate_problem(control, (ok, ok), tl.reply([]), tl.reply([]), clear_demo,
                                                          tl.NAME, tl.DOMAIN))
        self.assertEqual(live.checked_duplicate_problem(control, (ok, ok), tl.reply([]), tl.reply([row]), clear_demo,
                                                         tl.NAME, tl.DOMAIN), "duplicate_in_production")

    def test_control_problem_needs_untruncated_replies_and_exact_name_and_domain(self):
        control = {"name": "A Co", "domain": "A.Example."}
        row = {"name": "a  co", "domain": "a.example"}
        ok = tl.reply([row])
        self.assertIsNone(live.control_problem(control, ok, ok))  # case, spacing and a trailing dot are normalised
        ignored = tl.reply([row], total=500)  # the server ignored the filter: more matches than rows came back
        self.assertEqual(live.control_problem(control, ignored, ok), "search_unproven")
        self.assertEqual(live.control_problem(control, ok, ignored), "search_unproven")
        for bad_total in (None, "1", True, -1):
            self.assertEqual(live.control_problem(control, ok, {"status": 200, "total": bad_total, "rows": [row]}),
                             "search_unproven", bad_total)
            self.assertEqual(live.control_problem(control, {"status": 200, "total": bad_total, "rows": [row]}, ok),
                             "search_unproven", bad_total)
        other_domain = tl.reply([{"name": "a co", "domain": "b.example"}])
        self.assertEqual(live.control_problem(control, ok, other_domain), "search_unproven")  # right name, wrong domain
        self.assertEqual(live.control_problem(control, ok, tl.reply([{"name": "a co", "domain": None}])), "search_unproven")
        self.assertEqual(live.control_problem(control, ok, tl.reply([{"name": "someone else", "domain": "a.example"}])),
                         "search_unproven")
        self.assertEqual(live.control_problem(control, tl.reply([{"name": "someone else", "domain": "a.example"}]), ok),
                         "search_unproven")
        padded = tl.reply([{"name": "zzz", "domain": "z.example"}, row])  # the control sits among other rows: fine
        self.assertIsNone(live.control_problem(control, padded, padded))

    def test_the_live_pre_check_control_step_applies_the_same_rule(self):
        control = {"name": "A Co", "domain": "a.example"}
        row = {"name": "a co", "domain": "a.example"}
        ok, ignored = tl.reply([row]), tl.reply([row], total=9)
        clear_demo = tl.CLEAR_DEMO
        self.assertEqual(live.checked_duplicate_problem(control, (ignored, ok), tl.reply([]), tl.reply([]), clear_demo,
                                                         tl.NAME, tl.DOMAIN), "search_unproven")
        self.assertEqual(live.checked_duplicate_problem(control, (ok, tl.reply([{"name": "a co", "domain": "x.example"}])),
                                                         tl.reply([]), tl.reply([]), clear_demo, tl.NAME, tl.DOMAIN),
                         "search_unproven")


class StrictContractTests(unittest.TestCase):
    INTENDED = {**tl.INTENDED, "license_start": date(2026, 10, 16), "organization_email": "m+x@pentera.io"}

    def row(self, **over):
        base = tl.contract_row(**{"accountLicense.startDate": "2026-10-16T09:00:00.000Z",
                                  "accountLicense.expirationDate": "2027-05-20T07:00:00.000Z", "primaryUser.isMfaRequired": True,
                                  "primaryUser.email": "m+x@pentera.io",
                                  "campaignExecutionSettings.domainsMultiAttackStackSettings.enabled": False})
        base.update(over)
        return base

    def test_exact_row_passes_and_each_deviation_is_named(self):
        expected = contract.expected_readback_row(self.INTENDED, strict=True)
        self.assertEqual(contract.compare_readback(expected, self.row())[0], [])
        for key, bad, name in (("primaryUser.isMfaRequired", False, "mfaRequired"), ("primaryUser.email", "x@pentera.io", "organizationEmail"),
                               ("accountLicense.expirationDate", "2027-05-21T07:00:00.000Z", "licenseExpirationDate"),
                               ("accountLicense.startDate", "2026-10-20T09:00:00.000Z", "licenseStartDate")):
            self.assertEqual(contract.compare_readback(expected, self.row(**{key: bad}))[0], [name], key)

    def test_mfa_is_read_from_the_dev_verified_row_path_only(self):
        expected = contract.expected_readback_row(self.INTENDED, strict=True)
        base = self.row()
        for stale in ("primaryUser.isMfaRequired", "mfaRequired", "accountSettings.mfaRequired", "accountLicense.mfaRequired"):
            base.pop(stale, None)
        self.assertEqual(contract.compare_readback(expected, {**base, "primaryUser.isMfaRequired": True})[0], [])
        nested = {**base, "primaryUser": {"isMfaRequired": True}}
        self.assertEqual(contract.compare_readback(expected, nested)[0], [])
        self.assertEqual(contract.compare_readback(expected, {**base, "primaryUser.isMfaRequired": False})[0], ["mfaRequired"])
        self.assertEqual(contract.compare_readback(expected, {**base, "primaryUser.isMfaRequired": "true"})[0], ["mfaRequired"])
        self.assertEqual(contract.compare_readback(expected, base)[0], ["mfaRequired"])  # absent fails closed
        # a path the Dev probe never verified must not be able to satisfy the check
        self.assertEqual(contract.compare_readback(expected, {**base, "mfaRequired": True})[0], ["mfaRequired"])
        self.assertIn("primaryUser.isMfaRequired", contract.READBACK_PICK_PATHS)

    def test_multiple_attack_stacks_is_read_back_off_at_its_dev_verified_path(self):
        path = "campaignExecutionSettings.domainsMultiAttackStackSettings.enabled"
        expected = contract.expected_readback_row(self.INTENDED, strict=True)
        self.assertEqual(contract.compare_readback(expected, self.row())[0], [])
        self.assertEqual(contract.compare_readback(expected, self.row(**{path: True}))[0], ["multipleAttackStacksEnabled"])
        base = self.row()
        del base[path]
        self.assertEqual(contract.compare_readback(expected, base)[0], ["multipleAttackStacksEnabled"])
        self.assertNotIn("multipleAttackStacksEnabled", contract.expected_readback_row(self.INTENDED))
        # the two top-level flags are nullable and not in the create body: no readback check without a verified default
        for name in ("fullNucleiScanEnabled", "authenticatedTestingEnabled"):
            self.assertNotIn(name, expected)

    def test_start_day_tolerates_one_day_and_non_strict_is_unchanged(self):
        expected = contract.expected_readback_row(self.INTENDED, strict=True)
        self.assertEqual(contract.compare_readback(expected, self.row(**{"accountLicense.startDate": "2026-10-17T01:00:00.000Z"}))[0], [])
        self.assertNotIn("mfaRequired", contract.expected_readback_row(self.INTENDED))
        with self.assertRaises(ValueError):
            contract.expected_readback_row({**tl.INTENDED}, strict=True)


class OwnerReviewTests(tu.Base):
    OLD = tu.REF  # the attempt used (and consumed) this approval

    def seed(self, *kinds, revision=tu.REV):
        for kind in kinds:
            data = {"started": {"outcome": "attempt_started"}, "refused": {"outcome": "refused", "refusal_reason": "confirm_not_clicked"},
                    "cleared": {"outcome": "owner_review_cleared"}, "unknown": {"outcome": "uncertain_submit"}}[kind]
            pcr.record(self.dir, {"co_reference": tu.CO, "approval_ref": self.OLD, "salesforce_revision": revision, "route": tu.ROUTE,
                                  **data}, tu.NOW)

    def blocking(self):
        return ui.co_status(ui.load_co_records(self.dir, tu.CO))["blocking"]

    def test_cleared_not_clicked_no_longer_blocks_but_unknown_never_clears(self):
        self.seed("started", "refused")
        self.assertTrue(self.blocking())
        self.seed("cleared")
        self.assertFalse(self.blocking())
        self.assertTrue(ui.review_cleared(ui.load_co_records(self.dir, tu.CO)))
        self.seed("unknown")
        self.assertTrue(self.blocking())

    def test_a_later_crashed_attempt_under_the_same_reference_is_not_hidden(self):
        self.seed("started", "refused", "cleared")
        self.assertFalse(self.blocking())
        self.seed("started")  # a reused / reset reference starts again and crashes: no refusal, no clear for it
        self.assertTrue(self.blocking())

    def test_the_clear_is_keyed_by_revision_too(self):
        self.seed("started", "refused")
        self.seed("cleared", revision="another-revision")
        self.assertTrue(self.blocking())

    def run_tool(self, argv, answer="CLEAR CO-0798", tty=True):
        out = io.StringIO()
        code = clear.run(argv, parent=self.dir, now=tu.NOW, input_fn=lambda _p: answer, isatty=lambda: tty, out=out,
                         approvals=self.approvals)
        return code, json.loads(out.getvalue())

    def args(self, **over):
        values = {"--co": tu.CO, "--approval-ref": self.OLD, "--revision": tu.REV, "--operator": tu.OWNER}
        values.update(over)
        return [x for k, v in values.items() if v is not None for x in (k, v)]

    def test_tool_records_operator_time_and_reason_after_the_typed_phrase(self):
        self.seed("started", "refused")
        self.assertEqual(self.run_tool(self.args(), "yes")[1]["reason"], "phrase_mismatch")
        self.assertEqual(self.run_tool(self.args(), tty=False)[1]["reason"], "interactive_terminal_required")
        code, line = self.run_tool(self.args())
        self.assertEqual((code, line["result"]), (0, "recorded"))
        record = [r for r in ui.load_co_records(self.dir, tu.CO) if r["outcome"] == "owner_review_cleared"][0]
        self.assertEqual((record["operator"], record["refusal_reason"], record["salesforce_revision"], record["approval_ref"]),
                         (tu.OWNER, "owner_reviewed_confirm_not_clicked", tu.REV, self.OLD))
        self.assertIn("recorded_at", record)
        self.assertEqual(self.run_tool(self.args())[1]["reason"], "attempt_not_clearable")  # one clear per attempt

    def test_tool_needs_the_issuer_identity(self):
        self.seed("started", "refused")
        self.assertEqual(self.run_tool(self.args(**{"--operator": None}))[1]["reason"], "operator_required")
        self.assertEqual(self.run_tool(self.args(**{"--operator": "someone.else@example.com"}))[1]["reason"], "operator_not_owner")
        self.assertEqual(self.run_tool(self.args(**{"--operator": " "}))[1]["reason"], "operator_required")
        self.assertEqual(self.run_tool(self.args(**{"--revision": None}))[1]["reason"], "invalid_arguments")
        self.assertEqual(self.run_tool(["--co", tu.CO, "--uuid-write", "--operator", "x@example.com"])[1]["reason"], "operator_not_owner")

    def test_tool_refuses_unknown_wrong_revision_or_unfinished_attempts(self):
        self.seed("unknown")
        self.assertEqual(self.run_tool(self.args())[1]["reason"], "attempt_not_clearable")
        self.seed("started", "refused")
        self.assertEqual(self.run_tool(self.args())[1]["reason"], "attempt_not_clearable")  # an unknown outcome shares the pair
        self.assertEqual(self.run_tool(self.args(**{"--revision": "nope"}))[1]["reason"], "attempt_not_clearable")


class UuidHardeningTests(tu.UuidTests):
    def test_started_line_blocks_before_the_write_and_record_failure_is_reported(self):
        self.rows = [tu.row(), tu.row(Account_UUID__c=tu.UUID)]
        original = ui.record_uuid_write
        calls = []

        def failing(parent, co, uuid, result, now):
            calls.append(result)
            if result != "write_started":
                raise OSError("disk")
            return original(parent, co, uuid, result, now)
        ui.record_uuid_write = failing
        try:
            self.assertEqual(self.run_confirm(), "record_write_failed")
        finally:
            ui.record_uuid_write = original
        self.assertEqual(calls, ["write_started", "written_verified"])
        code, _ = ui.handle_uuid_review({"reference": tu.CO}, self.inputs(), self.rows[0])
        self.assertEqual(code, "uuid_write_needs_review")  # not swallowed: the intent line keeps blocking

    def test_no_write_when_the_intent_cannot_be_recorded(self):
        original = ui.record_uuid_write
        ui.record_uuid_write = lambda *a: (_ for _ in ()).throw(OSError("disk"))
        try:
            self.assertEqual(self.run_confirm(), "record_write_failed")
        finally:
            ui.record_uuid_write = original
        self.assertEqual(self.writes, [])

    def test_a_second_write_after_uncertain_needs_the_recorded_owner_review(self):
        self.rows = [tu.row(), tu.row(Account_UUID__c="f" * 32)]
        self.assertEqual(self.run_confirm(), "write_uncertain")
        self.assertEqual(ui.handle_uuid_review({"reference": tu.CO}, self.inputs(), tu.row())[0], "uuid_write_needs_review")
        out = io.StringIO()
        code = clear.run(["--co", tu.CO, "--uuid-write", "--operator", tu.OWNER], parent=self.dir, now=tu.NOW,
                         input_fn=lambda _p: "CLEAR UUID CO-0798", isatty=lambda: True, out=out, approvals=self.approvals)
        self.assertEqual(code, 0)
        line = [json.loads(x) for x in (self.dir / "production-create-records" / ui.UUID_WRITES_FILE).read_text().splitlines()][-1]
        self.assertEqual((line["result"], line["operator"], line["reason"]), ("owner_review_cleared", tu.OWNER, "owner_reviewed_uuid_write"))
        self.assertEqual(ui.handle_uuid_review({"reference": tu.CO}, self.inputs(), tu.row())[0], "ready")

    def test_a_row_that_changes_between_review_and_write_refuses(self):
        code, view = ui.handle_uuid_review({"reference": tu.CO}, self.inputs(), tu.row())
        reads = iter([tu.row(), tu.row(Account_UUID__c="c" * 32)])
        result = ui.handle_uuid_confirm({"reference": tu.CO, "nonce": view["token"]}, self.inputs(), lambda: next(reads),
                                        lambda argv: self.writes.append(argv) or True, "alias")
        self.assertEqual(result, "uuid_conflict")
        self.assertEqual(self.writes, [])


class CreatePostTicketTests(tu.PostTests):
    def test_a_launch_mints_a_ticket_bound_to_the_operator_and_a_mint_failure_launches_nothing(self):
        values = self.values()
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "launched")
        body = json.loads((self.ticket_root / ticket.SUBDIR / (tu.CO + ".json")).read_text(encoding="utf-8"))
        self.assertEqual((body["operator"], body["revision"], body["approval_ref"]), (tu.OWNER, tu.REV, tu.REF))
        self.setUp()
        self.unlocked()
        self.launched.clear()
        values = self.values()
        original = ticket.mint
        ticket.mint = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
        try:
            self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "launch_failed")
        finally:
            ticket.mint = original
        self.assertEqual(self.launched, [])

    def test_a_failed_launch_deletes_the_minted_ticket(self):
        self.assertEqual(ui.handle_create_post(self.values(), self.inputs(), lambda *_a: None), "launch_failed")
        self.assertFalse((self.ticket_root / ticket.SUBDIR / (tu.CO + ".json")).exists())


if __name__ == "__main__":
    unittest.main()
