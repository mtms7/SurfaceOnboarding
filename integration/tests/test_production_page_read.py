"""Production reads through the page's OWN table request (docs/48 note 2026-10-18): the cookie-only in-page fetch was refuted
live (401 after a human SSO+MFA sign-in), so the runner now lets the page send its own getAllDetailedAccounts request and
rewrites ONLY the ``tableServerData`` JSON body. Covered here: the body grammar the guards enforce, the rewrite, the fail-
closed outcomes, the status-only session signals, the demo-domain signal, and that no production read path ever calls
``page.evaluate`` with a fetch. Fake pages only: no browser, network, Salesforce or BackOffice."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from integration.onboarding import production_contract as contract
from integration.onboarding import production_create_live as live
from integration.onboarding import production_dry_run as pdr
from integration.onboarding import production_probe as probe
from integration.tests import page_own_table as fake
from integration.tests.page_own_table import ORIGIN, PageOwnTable
import tools.attended_ce_only_playwright as runner

TABLE_URL = fake.TABLE_URL
ADD_URL = ORIGIN + live.CREATE_PATH


def body(value="Acme - CE Only", **over):
    """A valid rewritten read body: name + domain unique_fields, NO projection (docs/53 2026-10-18)."""
    server = {"offset": 0, "items_per_page": 100, "sort": {"direction": "desc", "key": "lastReconScan"},
              "filters": {"and": [{"method": "search", "value": value}]}, "unique_fields": ["accountName", "accountDomain"]}
    server.update(over)
    return {"tableServerData": server}


def text(**over):
    return json.dumps(body(**over))


class Page(PageOwnTable):
    """A fake production page: the table answers from ``search`` (value -> reduced reply)."""

    def __init__(self, search=None, *, header=True, passive=None, demo=None, readbacks=None):
        self.form_open = False
        self.search = search or {}
        self.readbacks = list(readbacks or [])
        self.evaluated, self.clicks = [], []
        self.context = SimpleNamespace(route=lambda pattern, handler: self.routes.append((pattern, handler)), on=lambda *_a: None,
                                       pages=[self])
        self.url = ORIGIN + "/backoffice/tenantManagement"
        self._init_table(header=header, passive=passive if passive is not None else
                         {pdr.AUTHORITIES_PATH: {"status": 200}, pdr.ACCOUNT_INFO_PATH: {"status": 200}},
                         demo=demo, demo_domain="zetaquux.example")

    def _serve_table(self, value):
        if value not in self.search and self.readbacks:  # a read the test queued as a readback (the bodies are identical)
            return self.readbacks.pop(0)
        return self.search.get(value, {"status": 200, "total": 0, "rows": []})

    def evaluate(self, script, arg=None):
        self.evaluated.append(script)
        raise AssertionError("a production read must not evaluate a script in the page")

    def locator(self, selector):
        if selector == fake.HEADER_SELECTOR:
            control = self._header()
            return control if control is not None else SimpleNamespace(count=lambda: 0)
        raise AssertionError("unexpected locator " + selector)


def reader(page, **kw):
    return runner.ProductionPageReader(page, **kw)


class ReadBodyGrammarTests(unittest.TestCase):
    def test_the_verified_search_and_or_variants_pass(self):
        for raw in (text(), json.dumps(body()).encode("utf-8"),
                    text(filters={"and": [{"or": [{"method": "search", "value": "a", "field": "accountName"},
                                                  {"method": "equals", "value": "b", "field": "accountDomain"}]}]}),
                    text(projection=["accountName"]), text(unique_fields=["accountName"])):
            self.assertIsNone(probe.read_body_problem(raw), raw)
        server = body()["tableServerData"]
        for missing in ("sort", "unique_fields", "projection"):
            trimmed = {k: v for k, v in server.items() if k != missing}
            self.assertIsNone(probe.read_body_problem(json.dumps({"tableServerData": trimmed})), missing)

    def test_everything_else_is_refused(self):
        server = body()["tableServerData"]
        bad = {
            "unreadable": "{not json", "none": None, "empty": "", "list": "[]", "number": 5,
            "no_table": json.dumps({}), "table_not_object": json.dumps({"tableServerData": []}),
            "extra_top_key": json.dumps({**body(), "action": "delete"}),
            "writeish_key": text(delete=True), "write_key_2": text(update={"x": 1}), "command": text(command="drop"),
            "offset_nonzero": text(offset=100), "offset_bool": text(offset=False), "offset_missing": json.dumps(
                {"tableServerData": {k: v for k, v in server.items() if k != "offset"}}),
            "page_zero": text(items_per_page=0), "page_big": text(items_per_page=101), "page_str": text(items_per_page="10"),
            "page_bool": text(items_per_page=True), "page_missing": json.dumps(
                {"tableServerData": {k: v for k, v in server.items() if k != "items_per_page"}}),
            "filters_empty": text(filters={}), "filters_missing": json.dumps(
                {"tableServerData": {k: v for k, v in server.items() if k != "filters"}}),
            "filters_list": text(filters=[]), "filters_or_top": text(filters={"or": [{"method": "search", "value": "x"}]}),
            "filters_extra": text(filters={"and": [{"method": "search", "value": "x"}], "not": []}),
            "two_clauses": text(filters={"and": [{"method": "search", "value": "x"}, {"method": "search", "value": "y"}]}),
            "method_write": text(filters={"and": [{"method": "delete", "value": "x"}]}),
            "method_missing": text(filters={"and": [{"value": "x"}]}),
            "leaf_extra": text(filters={"and": [{"method": "search", "value": "x", "set": "y"}]}),
            "value_empty": text(filters={"and": [{"method": "search", "value": " "}]}),
            "value_number": text(filters={"and": [{"method": "search", "value": 7}]}),
            "value_long": text(filters={"and": [{"method": "search", "value": "x" * 201}]}),
            "value_control": text(filters={"and": [{"method": "search", "value": "a\nb"}]}),
            "simple_equals": text(filters={"and": [{"method": "equals", "value": "x"}]}),
            "or_empty": text(filters={"and": [{"or": []}]}),
            "or_bad_leaf": text(filters={"and": [{"or": [{"method": "search", "value": "x", "script": "y"}]}]}),
            "or_field_bad": text(filters={"and": [{"or": [{"method": "search", "value": "x", "field": "a b;"}]}]}),
            "sort_extra": text(sort={"direction": "asc", "key": "accountName", "cmd": "x"}),
            "sort_not_object": text(sort="accountName"),
            "unique_not_list": text(unique_fields="accountName"), "unique_not_str": text(unique_fields=[1]),
            "projection_not_list": text(projection="accountName"), "projection_bad": text(projection=["a b;"]),
            "projection_nested": text(projection=[{"a": 1}]),
            "huge": json.dumps(body("x" * 100) | {"pad": "p" * 30_000}),
        }
        for name, raw in bad.items():
            with self.subTest(name):
                self.assertIsNotNone(probe.read_body_problem(raw))

    def test_rewrite_fields_carry_exactly_the_search_grammar_and_no_projection(self):
        fields = probe.table_rewrite_fields("Acme - CE Only")
        self.assertEqual(fields, {"offset": 0, "items_per_page": probe.TABLE_ROW_LIMIT,
                                  "filters": {"and": [{"method": "search", "value": "Acme - CE Only"}]},
                                  "unique_fields": ["accountName", "accountDomain"]})
        self.assertNotIn("projection", fields)
        self.assertEqual(probe.table_rewrite_drops(), ("projection",))  # the page's own projection is removed from its body
        self.assertEqual(probe.TABLE_ROW_LIMIT, 100)
        self.assertEqual(contract.SEARCH_PAGE_SIZE, probe.TABLE_ROW_LIMIT)
        for value in ("", "  ", None, 5, "a\nb", "x" * 201):
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.table_rewrite_fields(value)

    def test_rewrite_fields_always_validate_as_a_strict_read_body(self):
        server = {"sort": {"direction": "asc", "key": "x"}, **probe.table_rewrite_fields("v")}
        self.assertIsNone(probe.read_body_problem(json.dumps({"tableServerData": server}), strict=True))

    def test_a_strict_body_with_a_projection_is_refused_but_the_base_grammar_tolerates_it(self):
        with_projection = text(projection=["accountName", "accountDomain"])
        self.assertEqual(probe.read_body_problem(with_projection, strict=True), "not_strict")
        self.assertIsNone(probe.read_body_problem(with_projection))  # the page's own loads send one for other lists
        self.assertIsNone(probe.read_body_problem(text(), strict=True))

    def test_no_projection_constant_is_left_in_the_runner(self):
        self.assertFalse(hasattr(runner, "PDR_SEARCH_PROJECTION"))


class GuardBodyTests(unittest.TestCase):
    """The production guards let ONE kind of POST through: a validated read body on getAllDetailedAccounts."""

    GUARDS = (("probe", lambda: probe.RequestGuard(require_read_body=True)), ("dry_run", pdr.DryRunRequestGuard),
              ("live", live.LiveRequestGuard))

    def setUp(self):
        self.page = Page()

    def test_a_validated_body_continues_and_is_counted_by_path_only(self):
        for name, make in self.GUARDS:
            with self.subTest(name):
                page, guard = Page(), make()
                guard.install(page)
                self.assertEqual(page.fire("POST", TABLE_URL, text()), "continued")
                self.assertEqual(guard.api_calls[("POST", contract.ACCOUNTS_PATH)]["count"], 1)
                self.assertEqual(guard.blocked, {})

    def test_unexpected_bodies_are_aborted(self):
        for name, make in self.GUARDS:
            for label, raw in (("writeish_key", text(delete=True)), ("extra_top", json.dumps({**body(), "action": "x"})),
                               ("offset", text(offset=50)), ("big_page", text(items_per_page=5000)),
                               ("empty_filters", text(filters={})), ("bad_method", text(filters={"and": [{"method": "x", "value": "y"}]})),
                               ("none", None), ("junk", "not json")):
                with self.subTest(guard=name, case=label):
                    page, guard = Page(), make()
                    guard.install(page)
                    self.assertEqual(page.fire("POST", TABLE_URL, raw), "aborted")
                    self.assertEqual(guard.blocked[("POST", contract.ACCOUNTS_PATH)], 1)
                    self.assertEqual(guard.api_calls, {})
                    self.assertEqual(guard.blocked_api_writes() if hasattr(guard, "blocked_api_writes") else 1, 1)

    def test_a_request_without_a_body_attribute_is_aborted(self):
        for name, make in self.GUARDS:
            with self.subTest(name):
                guard, route = make(), fake.FakeRoute("POST", TABLE_URL)
                guard.handle(route)
                self.assertTrue(route.abort_called and not route.continue_called)

    def test_a_valid_body_on_another_path_is_aborted(self):
        for name, make in self.GUARDS:
            for url in (ADD_URL, ORIGIN + "/api/v1/backoffice/account/" + "a" * 24 + "/edit", ORIGIN + "/api/v1/authenticated/accountInfo",
                        "https://evil.example" + contract.ACCOUNTS_PATH, ORIGIN + contract.ACCOUNTS_PATH + "/extra"):
                with self.subTest(guard=name, url=url):
                    page, guard = Page(), make()
                    guard.install(page)
                    self.assertEqual(page.fire("POST", url, text()), "aborted")

    def test_put_patch_delete_are_always_aborted(self):
        for name, make in self.GUARDS:
            for method in ("PUT", "PATCH", "DELETE"):
                with self.subTest(guard=name, method=method):
                    page, guard = Page(), make()
                    guard.install(page)
                    self.assertEqual(page.fire(method, TABLE_URL, text()), "aborted")

    def test_request_decision_is_body_aware(self):
        self.assertEqual(pdr.request_decision("POST", TABLE_URL, text()), (True, "allowlisted_read_post"))
        self.assertFalse(pdr.request_decision("POST", TABLE_URL, text(delete=True))[0])
        self.assertFalse(pdr.request_decision("POST", TABLE_URL)[0])  # no body known: never a clearance
        self.assertTrue(pdr.request_decision("GET", ORIGIN + "/x")[0])

    def test_the_live_guard_allows_exactly_one_armed_create_and_no_second(self):
        page, guard = Page(), live.LiveRequestGuard()
        guard.install(page)
        self.assertEqual(page.fire("POST", ADD_URL, "{}"), "aborted")  # not armed
        guard.arm()
        self.assertEqual(page.fire("POST", ADD_URL, "{}"), "continued")
        self.assertEqual(page.fire("POST", ADD_URL, "{}"), "aborted")  # a second create, even re-armed
        guard.arm()
        self.assertEqual(page.fire("POST", ADD_URL, "{}"), "aborted")
        self.assertEqual(guard.create_requests(), 1)
        self.assertEqual(guard.create_blocked, 3)  # unarmed, after the create, and re-armed after the create
        self.assertEqual(page.fire("POST", TABLE_URL, text()), "continued")  # reads still pass after the create
        self.assertEqual(page.fire("POST", TABLE_URL, text(delete=True)), "aborted")


class ReducerTests(unittest.TestCase):
    def test_search_reply_keeps_only_the_non_secret_row_fields(self):
        row = {"accountName": "N", "accountDomain": "d.example", "alternateDomains": ["a.example", 7],
               "userEmailDomains": ["e.example"], "isDeleted": True, "token": fake.SECRET, "primaryUser": {"email": fake.SECRET}}
        out = pdr.search_reply(200, {"pagination_response": {"total_count": 1, "table_data": [row]}})
        self.assertEqual(out, {"status": 200, "total": 1, "rows": [{"name": "N", "domain": "d.example", "alternates": ["a.example"],
                                                                      "emails": ["e.example"], "deleted": True}]})
        self.assertNotIn(fake.SECRET, json.dumps(out))

    def test_full_rows_without_a_projection_feed_the_duplicate_verdicts(self):
        """The server now answers FULL rows (no projection): the reducer picks name, domains, flag in Python."""
        def full(**over):
            row = {"accountName": "Other Co", "accountDomain": "other.example", "alternateDomains": [], "userEmailDomains": [],
                   "isDeleted": False, "id": "i" * 24, "accountUuid": "u" * 32, "token": fake.SECRET,
                   "accountLicense": {"assetsNumber": 1, "startDate": "2026-10-16T00:00:00.000Z"},
                   "primaryUser": {"email": fake.SECRET, "isMfaRequired": True}, "blob": ["x" * 200] * 400}
            row.update(over)
            return row

        def reduced(*rows):
            return pdr.search_reply(200, {"pagination_response": {"total_count": len(rows), "table_data": list(rows)}})

        target, domains, demo = "Acme - CE Only", ("acme.example", "mail.acme.example"), {"status": 200, "hit": False, "count": 1}
        clear = reduced(full())
        self.assertEqual(set(clear["rows"][0]), {"name", "domain", "alternates", "emails", "deleted"})
        self.assertNotIn(fake.SECRET, json.dumps(clear))
        self.assertLess(len(json.dumps(clear)), 400)  # the large unrelated row content is never retained
        self.assertIsNone(pdr.duplicate_verdict([clear, clear], demo, target, domains))
        for label, row in (("name", full(accountName="acme - ce  only")), ("primary_domain", full(accountDomain="ACME.example.")),
                           ("alternate_domain", full(alternateDomains=["x.example", "Acme.example"])),
                           ("email_domain", full(userEmailDomains=["pentera.io", "mail.acme.example"])),
                           ("deleted_row_still_counts", full(accountName=target, isDeleted=True))):
            with self.subTest(label):
                self.assertEqual(pdr.duplicate_verdict([reduced(row)], demo, target, domains), "duplicate_in_production")
        self.assertTrue(reduced(full(isDeleted=True))["rows"][0]["deleted"])
        self.assertEqual(pdr.duplicate_verdict([reduced(full(), full(), full(), full())], demo, target, domains), None)
        big = pdr.search_reply(200, {"pagination_response": {"total_count": 100, "table_data": [full() for _ in range(100)]}})
        self.assertEqual((len(big["rows"]), big["total"]), (100, 100))
        self.assertLess(len(json.dumps(big)), 20_000)

    def test_a_full_row_through_the_page_own_request_reaches_the_live_checked_verdict(self):
        row = {"accountName": "Held Co", "accountDomain": "held.example", "alternateDomains": ["Target.example"],
               "userEmailDomains": ["mail.example"], "isDeleted": False, "token": fake.SECRET}
        page = Page(search={"Target Co": {"status": 200, "total": 0, "rows": []}, "target.example": {"status": 200, "total": 1, "rows": [row]},
                            "Held Co": {"status": 200, "total": 1, "rows": [row]}, "held.example": {"status": 200, "total": 1, "rows": [row]}})
        r = reader(page)
        replies = [r.search(v) for v in ("Held Co", "held.example", "Target Co", "target.example")]
        self.assertEqual(replies[3]["rows"], [{"name": "Held Co", "domain": "held.example", "alternates": ["Target.example"],
                                               "emails": ["mail.example"], "deleted": False}])
        self.assertEqual(live.checked_duplicate_problem({"name": "Held Co", "domain": "held.example"}, (replies[0], replies[1]),
                                                        replies[3], replies[2], {"status": 200, "hit": False, "count": 1},
                                                        "Target Co", "target.example"), "duplicate_in_production")  # via the ALTERNATE domain of a full row
        for body_ in page.table_bodies:
            self.assertNotIn("projection", body_["tableServerData"])
        self.assertNotIn(fake.SECRET, json.dumps(replies))

    def test_search_reply_fails_closed_on_every_odd_shape(self):
        unexpected = {"status": 200, "total": None, "rows": None}
        good = {"pagination_response": {"total_count": 0, "table_data": []}}
        for status, payload, want in (
                (200, None, unexpected), (200, [], unexpected), (200, {}, unexpected), (200, {"pagination_response": []}, unexpected),
                (200, {"pagination_response": {"total_count": "1", "table_data": []}}, unexpected),
                (200, {"pagination_response": {"total_count": True, "table_data": []}}, unexpected),
                (200, {"pagination_response": {"total_count": 1, "table_data": {}}}, unexpected),
                (200, {"pagination_response": {"total_count": 1, "table_data": ["junk"]}}, unexpected),
                (200, good, {"status": 200, "total": 0, "rows": []}),
                (401, good, {"status": 401, "total": None, "rows": None}),
                (500, good, {"status": 500, "total": None, "rows": None}),
                (None, good, {"status": 0, "total": None, "rows": None}), ("200", good, {"status": 0, "total": None, "rows": None}),
                (True, good, {"status": 0, "total": None, "rows": None})):
            with self.subTest(status=status, payload=str(payload)[:30]):
                self.assertEqual(pdr.search_reply(status, payload), want)

    def test_demo_reply_compares_in_python_and_returns_only_a_boolean_and_a_count(self):
        listing = {"demoDomains": ["hit.example", "Other.Example.", fake.SECRET]}
        self.assertEqual(pdr.demo_reply(200, listing, ["HIT.example."]), {"status": 200, "hit": True, "count": 3})
        self.assertEqual(pdr.demo_reply(200, listing, ["miss.example"]), {"status": 200, "hit": False, "count": 3})
        self.assertNotIn(fake.SECRET, json.dumps(pdr.demo_reply(200, listing, ["x.example"])))
        for status, payload in ((200, None), (200, {}), (200, {"demoDomains": "x"}), (200, {"demoDomains": ["a", 3]})):
            self.assertEqual(pdr.demo_reply(status, payload, ["x"]), {"status": 200, "hit": None, "count": None})
        self.assertEqual(pdr.demo_reply(401, None, ["x"]), {"status": 401, "hit": None, "count": None})
        self.assertEqual(pdr.demo_reply(None, listing, ["x"]), {"status": 0, "hit": None, "count": None})

    def test_readback_reply_picks_only_the_compared_paths(self):
        row = {"accountName": "N", "accountUuid": "u", "accountLicense": {"assetsNumber": 1}, "primaryUser": {"isMfaRequired": True},
               "userEmailDomains": ["a.example", 3], "token": fake.SECRET, "secrets": {"api": fake.SECRET}, "id": None}
        out = contract.readback_reply(200, {"pagination_response": {"total_count": 1, "table_data": [row]}})
        self.assertEqual(out["status"], 200)
        self.assertEqual(out["rows"], [{"accountName": "N", "accountUuid": "u", "accountLicense.assetsNumber": 1,
                                        "primaryUser.isMfaRequired": True, "id": None}])
        self.assertNotIn(fake.SECRET, json.dumps(out))
        flat = contract.readback_reply(200, {"pagination_response": {"total_count": 1, "table_data": [{"accountLicense.assetsNumber": 1}]}})
        self.assertEqual(flat["rows"], [{"accountLicense.assetsNumber": 1}])
        for status, payload in ((200, None), (200, {"pagination_response": {"total_count": "x", "table_data": []}}),
                                (200, {"pagination_response": {"total_count": 1, "table_data": ["x"]}})):
            self.assertEqual(contract.readback_reply(status, payload), {"status": 200, "total": None, "rows": None})
        self.assertEqual(contract.readback_reply(403, None), {"status": 403, "total": None, "rows": None})
        self.assertEqual(contract.readback_reply(None, None), {"status": 0, "total": None, "rows": None})


class SessionVerdictTests(unittest.TestCase):
    def test_statuses_only(self):
        self.assertIsNone(pdr.session_verdict(200, {}))
        self.assertIsNone(pdr.session_verdict(200, {pdr.AUTHORITIES_PATH: 200, pdr.ACCOUNT_INFO_PATH: 200}))
        for table, seen, reason in (
                (401, {}, "production_session_expired"), (403, {}, "production_session_expired"),
                (200, {pdr.AUTHORITIES_PATH: 401}, "production_session_expired"),
                (200, {pdr.ACCOUNT_INFO_PATH: 403}, "production_session_expired"),
                (500, {pdr.AUTHORITIES_PATH: 401}, "production_session_expired"),
                (500, {}, "production_session_not_ready"), (None, {}, "production_session_not_ready"),
                (0, {}, "production_session_not_ready"), ("200", {}, "production_session_not_ready"),
                (True, {}, "production_session_not_ready"),
                (200, {pdr.ACCOUNT_INFO_PATH: 500}, "production_session_not_ready")):
            with self.subTest(table=table, seen=seen):
                self.assertEqual(pdr.session_verdict(table, seen), reason)
        self.assertEqual(pdr.session_verdict(200, None), None)


class RewriteTests(unittest.TestCase):
    def test_only_the_table_server_data_fields_change(self):
        page = Page()
        status, payload = reader(page).table("Acme - CE Only")
        self.assertEqual(status, 200)
        self.assertIsNotNone(payload)
        sent = page.table_bodies[-1]
        page_own = json.loads(fake.PAGE_DEFAULT_BODY)
        self.assertEqual(set(sent), {"tableServerData"})  # no top-level key added
        server = sent["tableServerData"]
        self.assertEqual(server["sort"], page_own["tableServerData"]["sort"])  # the page's own sort is kept
        self.assertEqual((server["offset"], server["items_per_page"]), (0, probe.TABLE_ROW_LIMIT))
        self.assertEqual(server["filters"], {"and": [{"method": "search", "value": "Acme - CE Only"}]})
        self.assertEqual(server["unique_fields"], ["accountName", "accountDomain"])
        self.assertNotIn("projection", server)  # the page's own projection is dropped: a dotted projection hid compared paths live
        self.assertEqual(set(server), set(page_own["tableServerData"]) - {"projection"})

    def test_the_rewrite_goes_through_the_guard_not_around_it(self):
        page, guard = Page(), pdr.DryRunRequestGuard()
        guard.install(page)
        reader(page).table("x")
        route = page.routed[-1]
        self.assertTrue(route.fell_back and not route.abort_called)  # fallback hands the rewritten request to the guard
        self.assertEqual(guard.api_calls[("POST", contract.ACCOUNTS_PATH)]["count"], 1)
        self.assertEqual(guard.blocked, {})

    def test_the_page_s_unrewritten_request_is_aborted_by_the_guard(self):
        page, guard = Page(), pdr.DryRunRequestGuard()
        guard.install(page)
        self.assertEqual(page.fire("POST", TABLE_URL, fake.PAGE_DEFAULT_BODY), "aborted")  # empty filters, offset 20, 25 rows

    def test_route_is_removed_afterwards_and_only_the_table_path_is_routed(self):
        page = Page()
        patterns = []
        original = page.route
        page.route = lambda pattern, handler: (patterns.append(pattern), original(pattern, handler))
        reader(page).table("x")
        self.assertEqual(page.routes, [])
        self.assertEqual(len(patterns), 1)
        self.assertTrue(patterns[0].search(TABLE_URL))
        self.assertTrue(patterns[0].search(TABLE_URL + "?x=1"))
        self.assertFalse(patterns[0].search(ORIGIN + "/api/v1/backoffice/account/add"))
        self.assertFalse(patterns[0].search("https://evil.example" + contract.ACCOUNTS_PATH))
        self.assertFalse(patterns[0].search("http://app.pentera.io" + contract.ACCOUNTS_PATH))

    def test_a_rewrite_that_did_not_happen_gives_none(self):
        for label, raw in (("not_json", "{nope"), ("no_table_key", json.dumps({"other": 1})), ("table_not_object", json.dumps({"tableServerData": 5}))):
            with self.subTest(label):
                page = Page()
                original = fake.PAGE_DEFAULT_BODY
                try:
                    fake.PAGE_DEFAULT_BODY = raw
                    self.assertEqual(reader(page).table("x"), (None, None))
                finally:
                    fake.PAGE_DEFAULT_BODY = original

    def test_a_page_request_that_is_not_a_post_is_left_alone(self):
        page = Page()
        calls = []
        original = page._table_request

        def with_get():
            calls.append(page.fire("GET", TABLE_URL))
            original()
        page._table_request = with_get
        self.assertEqual(reader(page).table("x")[0], 200)
        self.assertEqual(calls, [None])  # the GET fell through the rewrite handler untouched
        self.assertEqual(len(page.table_bodies), 1)

    def test_a_page_that_never_answers_gives_none(self):
        page = Page()
        page.skip_table_response = True
        self.assertEqual(reader(page).table("x"), (None, None))

    def test_a_reply_to_some_other_request_does_not_count(self):
        page = Page()
        stray = fake.FakeResponse("POST", TABLE_URL, 200, {"pagination_response": {"total_count": 0, "table_data": []}})
        original = page._table_request

        def only_stray():
            page.responses.append(stray)  # an app refresh in flight: not the rewritten request
            page.skip_table_response = True
            original()
        page._table_request = only_stray
        self.assertEqual(reader(page).table("x"), (None, None))

    def test_a_rewrite_whose_fallback_raises_is_not_recorded_and_fails_closed(self):
        page = Page()
        original = fake.FakeRoute.fallback
        calls = []

        def broken(route, **kw):
            calls.append(sorted(kw))
            if "post_data" in kw:
                raise RuntimeError("route already handled")
            return original(route, **kw)
        with mock.patch.object(fake.FakeRoute, "fallback", broken):
            self.assertEqual(reader(page).table("x"), (None, None))  # the page's own unchanged body must not be trusted
        self.assertGreaterEqual(len(calls), 2)  # every failed rewrite attempt (reload, navigation retry) was passed on unchanged
        self.assertEqual(calls[0::2], [["post_data"]] * (len(calls) // 2))
        self.assertEqual(calls[1::2], [[]] * (len(calls) // 2))

    def test_a_reply_to_a_request_whose_body_was_not_rewritten_does_not_count(self):
        page = Page()
        original = fake.FakeRoute.fallback

        def ignores_override(route, **kw):  # fallback returns normally but the body stays the page's own
            return original(route)
        with mock.patch.object(fake.FakeRoute, "fallback", ignores_override):
            self.assertEqual(reader(page).table("x"), (None, None))

    def test_a_reply_to_a_request_with_a_different_search_value_does_not_count(self):
        page = Page()
        original = fake.FakeRoute.fallback

        def other_value(route, **kw):
            if "post_data" in kw:
                other = json.loads(kw["post_data"])
                other["tableServerData"]["filters"] = {"and": [{"method": "search", "value": "someone else"}]}
                kw = {"post_data": json.dumps(other)}
            return original(route, **kw)
        with mock.patch.object(fake.FakeRoute, "fallback", other_value):
            self.assertEqual(reader(page).table("x"), (None, None))

    def test_a_reply_to_a_request_that_regained_a_projection_does_not_count(self):
        page = Page()
        original = fake.FakeRoute.fallback

        def with_projection(route, **kw):
            if "post_data" in kw:
                other = json.loads(kw["post_data"])
                other["tableServerData"]["projection"] = ["accountName"]
                kw = {"post_data": json.dumps(other)}
            return original(route, **kw)
        with mock.patch.object(fake.FakeRoute, "fallback", with_projection):
            self.assertEqual(reader(page).table("x"), (None, None))

    def test_a_reply_to_the_matching_rewritten_request_counts(self):
        page = Page(search={"x": {"status": 200, "total": 0, "rows": []}})
        self.assertEqual(reader(page).table("x")[0], 200)

    def test_headers_and_cookies_are_never_read(self):
        page = Page(search={"x": {"status": 200, "total": 0, "rows": []}})
        reader(page).search("x")  # FakeResponse.headers raises if touched
        self.assertEqual(page.evaluated, [])

    def test_response_body_unreadable_fails_closed(self):
        page = Page()
        original = page._table_request

        def gone():
            original()
            page.responses[-1]._unreadable = True
        page._table_request = gone
        self.assertEqual(reader(page).search("x"), {"status": 200, "total": None, "rows": None})


class TriggerTests(unittest.TestCase):
    def test_first_trigger_is_a_reload_then_the_in_app_sort(self):
        page = Page()
        r = reader(page)
        r.table("a")
        self.assertEqual((page.reloads, page.header_clicks), (1, 0))
        r.table("b")
        self.assertEqual((page.reloads, page.header_clicks), (1, 1))

    def test_in_app_sort_is_a_logged_click(self):
        page, clicks = Page(), pdr.ClickLog()
        r = reader(page, click_log=clicks)
        r.table("a")
        r.table("b")
        self.assertEqual(clicks.kinds, [pdr.CLICK_SORT_HEADER])
        self.assertIn(pdr.CLICK_SORT_HEADER, pdr.ALLOWED_CLICKS)

    def test_without_the_sort_header_it_reloads(self):
        page = Page(header=False)
        r = reader(page)
        for value in ("a", "b", "c"):
            self.assertEqual(r.table(value)[0], 200)
        self.assertEqual((page.reloads, page.header_clicks), (3, 0))

    def test_read_only_commands_never_click_and_always_reload(self):
        page = Page()
        r = reader(page, in_app=False)
        for value in ("a", "b"):
            r.table(value)
        self.assertEqual((page.reloads, page.header_clicks, page.dispatches), (2, 0, 0))

    def test_with_the_form_open_no_read_runs_at_all(self):
        # Owner decision 2026-10-18 (live: no sort header is ever found): a read requested with the form open is refused.
        page = Page()
        r = reader(page)
        r.table("warm")  # the first read reloads (form closed)
        page.form_open = True
        reloads = page.reloads
        self.assertEqual(r.table("b", form_open=True), (None, None))
        self.assertEqual((page.reloads, page.dispatches, page.header_clicks), (reloads, 0, 0))
        self.assertEqual(r.last_read, {"form_open_read_refused": "fail"})

    def test_with_the_form_open_and_no_header_it_fails_closed_without_reloading(self):
        page = Page(header=False)
        page.form_open = True
        self.assertEqual(reader(page).table("x", form_open=True), (None, None))
        self.assertEqual((page.reloads, page.gotos), (0, 0))

    def test_a_timed_out_reload_is_retried_once_by_navigation(self):
        page = Page()
        answers = iter([True, False])
        original = page._table_request

        def flaky():
            if next(answers):
                page.skip_table_response = True
            else:
                page.skip_table_response = False
            original()
        page._table_request = flaky
        status, _ = reader(page).table("x")
        self.assertEqual(status, 200)
        self.assertEqual((page.reloads, page.gotos), (1, 1))


class DialogHeaderTests(unittest.TestCase):
    """Review finding 1: with the Add Account form open the sort trigger must never touch a button inside a dialog."""

    def test_the_header_lookup_skips_a_dialog_button_and_takes_the_table_s(self):
        page = Page()
        page.dialog_header = True
        found = runner._in_app_sort_header(page)
        self.assertIsNotNone(found)
        self.assertFalse(found.in_dialog)

    def test_a_dialog_only_header_is_never_returned(self):
        page = Page(header=False)
        page.dialog_header = True
        self.assertIsNone(runner._in_app_sort_header(page))

    def test_a_dispatched_read_with_the_form_open_never_touches_the_dialog_button(self):
        page = Page()
        page.dialog_header = True
        r = reader(page)
        r.table("warm")
        page.form_open = True
        self.assertEqual(r.table("b", form_open=True), (None, None))  # refused: nothing dispatched, nothing clicked
        self.assertEqual((page.dispatches, page.dialog_header_clicks), (0, 0))

    def test_with_only_a_dialog_header_the_read_fails_closed_without_any_click_or_reload(self):
        page = Page(header=False)
        page.dialog_header = True
        page.form_open = True
        self.assertEqual(reader(page).table("x", form_open=True), (None, None))
        self.assertEqual((page.dialog_header_clicks, page.dispatches, page.header_clicks, page.reloads, page.gotos),
                         (0, 0, 0, 0, 0))

    def test_a_header_that_cannot_be_classified_is_not_used(self):
        page = Page()

        def broken(self, js, *_a):
            raise RuntimeError("detached")
        with mock.patch.object(fake.FakeHeaderControl, "evaluate", broken):
            self.assertIsNone(runner._in_app_sort_header(page))

    def test_a_header_that_detaches_between_the_check_and_the_click_is_never_clicked(self):
        page = Page()
        page.form_open = True
        calls = []

        def detaching(self, js, *_a):
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError("element is not attached to the DOM")
            return True
        with mock.patch.object(fake.FakeHeaderControl, "evaluate", detaching):
            self.assertEqual(reader(page).table("x", form_open=True), (None, None))
        self.assertEqual((page.dispatches, page.header_clicks, page.dialog_header_clicks, page.reloads, page.gotos),
                         (0, 0, 0, 0, 0))

    def test_the_classified_element_is_the_one_that_is_clicked(self):
        page = Page()
        page.form_open = True
        dialog = fake.FakeHeaderControl(page, in_dialog=True)
        dom = {}

        class Outside(fake.FakeHeaderControl):
            def evaluate(self, js, *_a):
                answer = super().evaluate(js)
                dom["el"] = dialog  # the DOM changes right after the answer: a lazy locator now points at the dialog button
                return answer
        outside = Outside(page)
        dom["el"] = outside

        class Pinned:
            def __init__(self, element):
                self.element = element

            def evaluate(self, js, *_a):
                return self.element.evaluate(js)

            def dispatch_event(self, name, *_a, **_k):
                return self.element.dispatch_event(name)

            def click(self, **kw):
                return self.element.click(**kw)

        class Lazy(Pinned):
            """A locator: every call resolves the element again."""

            def __init__(self):
                pass

            @property
            def element(self):
                return dom["el"]

            def element_handle(self, **_kw):
                return Pinned(dom["el"])

        class Headers:
            def count(self):
                return 1

            def nth(self, _index):
                return Lazy()
        page.locator = lambda selector: Headers() if selector == fake.HEADER_SELECTOR else None
        header = runner._in_app_sort_header(page)  # the classifier pins ONE element (form-open reads are refused now)
        self.assertTrue(runner._sort_header_still_outside(header))
        header.dispatch_event("click")
        self.assertEqual((page.dispatches, page.dialog_header_clicks), (1, 0))

    def test_the_dialog_selector_covers_the_usual_modal_markers(self):
        for marker in ("[role=dialog]", "[aria-modal=true]", ".modal", "dialog"):
            self.assertIn(marker, runner.SEARCH_DIALOG_SELECTOR)


class SessionTests(unittest.TestCase):
    def test_a_200_on_the_pages_own_reply_and_clean_passive_signals_is_signed_in(self):
        self.assertIsNone(reader(Page()).session_problem())

    def test_401_or_403_on_the_pages_own_reply_is_expired(self):
        for status in (401, 403):
            page = Page(search={runner.PDR_SESSION_CHECK_VALUE: {"status": status, "total": None, "rows": None}})
            self.assertEqual(reader(page).session_problem(), "production_session_expired", status)

    def test_no_reply_or_another_status_is_not_ready(self):
        page = Page()
        page.skip_table_response = True
        self.assertEqual(reader(page).session_problem(), "production_session_not_ready")
        page = Page(search={runner.PDR_SESSION_CHECK_VALUE: {"status": 500, "total": None, "rows": None}})
        self.assertEqual(reader(page).session_problem(), "production_session_not_ready")
        page = Page(search={runner.PDR_SESSION_CHECK_VALUE: {"status": 0}})  # the server never answered
        self.assertEqual(reader(page).session_problem(), "production_session_not_ready")

    def test_a_passive_401_from_the_pages_own_calls_is_expired_and_stays_sticky(self):
        page = Page(passive={pdr.AUTHORITIES_PATH: {"status": 401}, pdr.ACCOUNT_INFO_PATH: {"status": 200}})
        r = reader(page)
        self.assertEqual(r.session_problem(), "production_session_expired")
        page.passive = {pdr.AUTHORITIES_PATH: {"status": 200}}  # a later 200 never erases an earlier refusal
        self.assertEqual(r.session_problem(), "production_session_expired")

    def test_a_passive_server_error_is_not_ready_and_unseen_passive_calls_are_ignored(self):
        page = Page(passive={pdr.ACCOUNT_INFO_PATH: {"status": 500}})
        self.assertEqual(reader(page).session_problem(), "production_session_not_ready")
        self.assertIsNone(reader(Page(passive={})).session_problem())  # the page issued neither call: only the table counts

    def test_only_status_codes_are_taken_from_the_passive_responses(self):
        page = Page()
        reader(page).session_problem()  # FakeResponse.headers / bodies of passive GETs raise if read
        self.assertEqual(page.evaluated, [])

    def test_with_the_form_open_the_session_check_is_refused_fail_closed(self):
        page = Page()
        r = reader(page)
        self.assertIsNone(r.session_problem())
        page.form_open = True
        self.assertEqual(r.session_problem(form_open=True), "production_session_not_ready")  # never read with the form open
        self.assertEqual(page.dispatches, 0)


class DemoTests(unittest.TestCase):
    def read(self, page, *domains, warm=True):
        r = reader(page)
        if warm:
            r.session_problem()  # the first (reload) read lets the page issue its own start-up calls
        return r.demo_reply(domains)

    def test_a_demo_list_the_page_fetched_itself_is_compared_in_python(self):
        page = Page(demo={"status": 200, "hit": False, "count": 3})
        self.assertEqual(self.read(page, "zetaquux.example"), {"status": 200, "hit": False, "count": 2})
        page = Page(demo={"status": 200, "hit": True, "count": 3})
        self.assertEqual(self.read(page, "zetaquux.example")["hit"], True)

    def test_a_demo_list_the_page_never_requested_is_unavailable(self):
        page = Page(demo=None)
        self.assertEqual(self.read(page, "zetaquux.example"), {"status": 0, "hit": None, "count": None})
        self.assertEqual(pdr.demo_verdict(self.read(Page(), "x.example")), "duplicate_check_unavailable")

    def test_401_and_odd_shapes(self):
        self.assertEqual(self.read(Page(demo={"status": 401}), "x.example")["status"], 401)
        self.assertEqual(pdr.demo_verdict(self.read(Page(demo={"status": 401}), "x.example")), "production_session_expired")
        odd = self.read(Page(demo={"status": 200, "hit": None}), "x.example")
        self.assertEqual(odd, {"status": 200, "hit": None, "count": None})

    def test_the_list_is_read_once_right_after_the_trigger_and_kept_in_memory(self):
        page = Page(demo={"status": 200, "hit": False, "count": 3})
        r = reader(page)
        r.session_problem()
        first = r.demo_reply(("zetaquux.example",))
        page.handlers["response"].clear()  # nothing more can be observed and the body would be gone after a navigation
        self.assertEqual(r.demo_reply(("zetaquux.example",)), first)

    def test_the_demo_list_is_never_logged_or_returned(self):
        page = Page(demo={"status": 200, "hit": True, "count": 3})
        out = self.read(page, "zetaquux.example")
        self.assertNotIn(fake.SECRET, json.dumps(out))
        self.assertEqual(set(out), {"status", "hit", "count"})


class NoFetchTests(unittest.TestCase):
    def test_no_in_page_fetch_script_exists_any_more(self):
        for module, names in ((pdr, ("SESSION_GET_JS", "DEMO_DOMAINS_JS", "ACCOUNT_SEARCH_JS", "fetch_allowed", "FETCH_GET_PATHS",
                                      "FETCH_POST_PATHS", "build_search_body")),
                              (contract, ("READBACK_JS",)), (live, ("live_search_body",)),
                              (runner, ("_pdr_page_get", "_pdr_evaluate"))):
            for name in names:
                self.assertFalse(hasattr(module, name), f"{module.__name__}.{name}")
        for module in (pdr, contract, live, probe):
            for name, value in vars(module).items():
                if isinstance(value, str) and name.isupper():
                    self.assertNotIn("fetch(", value, f"{module.__name__}.{name}")

    def test_every_production_read_path_leaves_page_evaluate_unused(self):
        page = Page(search={"n": {"status": 200, "total": 0, "rows": []}}, demo={"status": 200, "hit": False, "count": 1})
        self.assertIsNone(runner._pdr_session_problem(page))
        self.assertIsNone(runner._pdr_duplicate_problem(page, "n", ("d.example",)))
        runner._pcr_search(page, "n")
        runner._pdr_reader(page).readback("n")
        self.assertEqual(page.evaluated, [])
        self.assertTrue(page.fetches)  # the reads did happen, through the page's own requests

    def test_the_source_of_the_reader_holds_no_fetch_credentials_or_header_access(self):
        import inspect
        source = inspect.getsource(runner.ProductionPageReader) + inspect.getsource(runner._pdr_reader)
        for needle in ("fetch(", "credentials", "page.evaluate", ".headers", "all_headers", "header_value", "cookies(",
                       "storage_state", "localStorage", "Authorization", "request.header"):
            self.assertNotIn(needle, source)


class ReaderCacheTests(unittest.TestCase):
    def test_one_reader_per_page_and_options_update(self):
        page = Page()
        first = runner._pdr_reader(page)
        self.assertIs(runner._pdr_reader(page), first)
        clicks = pdr.ClickLog()
        self.assertIs(runner._pdr_reader(page, click_log=clicks), first)
        self.assertIs(first.click_log, clicks)
        runner._pdr_reader(page, in_app=False)
        self.assertFalse(first.in_app)
        self.assertIsNot(runner._pdr_reader(Page()), first)

    def test_a_page_that_cannot_listen_still_reads_but_has_no_demo_signal(self):
        page = Page(demo={"status": 200, "hit": False, "count": 1})
        page.on = lambda *_a: (_ for _ in ()).throw(RuntimeError("no events"))
        r = reader(page)
        self.assertIsNone(r.session_problem())
        self.assertEqual(r.demo_reply(("d.example",))["status"], 0)


if __name__ == "__main__":
    unittest.main()
