"""VM Salesforce reader (docs/43 part A): read-only REST client with the CLI's response shape. Hermetic: a fake opener,
a fake signer, temporary files. No network, no process, no real key."""

import base64
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tools import vm_salesforce_reader as vr

NOW = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc)
CONFIG = vr.ReaderConfig("https://login.salesforce.com", "3MVG9" + "x" * 30, "svc@pentera.example", "/etc/key.pem", "v60.0")
INSTANCE = "https://pentera.my.salesforce.com"
TOKEN_VALUE = "00D-SECRET-TOKEN-VALUE"
SIGNATURE = b"fake-signature-bytes"


def fake_sign(key_file, message):
    assert key_file == CONFIG.private_key_file
    return SIGNATURE


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Answers by URL: ``routes`` maps a substring to a list of replies (dict -> JSON body, int -> HTTP error)."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def open(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url, dict(request.header_items()), request.data))
        for needle, replies in self.routes.items():
            if needle in request.full_url:
                reply = replies.pop(0) if len(replies) > 1 else replies[0]
                if isinstance(reply, int):
                    raise urllib.error.HTTPError(request.full_url, reply, "x", {}, io.BytesIO(b"body-with-secret"))
                body = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
                return Response(body)
        raise AssertionError("unexpected request " + request.full_url)


def login(token=TOKEN_VALUE, instance=INSTANCE):
    return {"access_token": token, "instance_url": instance}


def client(routes):
    opener = FakeOpener({"/services/oauth2/token": [login()], **routes})
    return vr.RestQueryClient(CONFIG, opener, fake_sign, clock=lambda: NOW), opener


class AssertionAndTokenTests(unittest.TestCase):
    def test_assertion_is_a_signed_rs256_jwt_with_the_expected_claims(self):
        header, claims, signature = vr.build_assertion(CONFIG, NOW, fake_sign).split(".")
        decode = lambda part: json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))  # noqa: E731
        self.assertEqual(decode(header), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(decode(claims), {"iss": CONFIG.client_id, "sub": CONFIG.username, "aud": CONFIG.login_url,
                                          "exp": int(NOW.timestamp()) + 180})
        self.assertEqual(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)), SIGNATURE)

    def test_token_exchange_posts_the_assertion_to_the_pinned_login_host_only(self):
        opener = FakeOpener({"/services/oauth2/token": [login()]})
        token = vr.fetch_token(CONFIG, opener, NOW, fake_sign)
        method, url, _headers, body = opener.requests[0]
        self.assertEqual((method, url), ("POST", "https://login.salesforce.com/services/oauth2/token"))
        self.assertIn(b"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer", body)
        self.assertEqual(token.instance_url, INSTANCE)
        self.assertNotIn(TOKEN_VALUE, repr(token))

    def test_bad_login_replies_are_refused(self):
        for reply in (login(instance="http://pentera.my.salesforce.com"), login(instance="https://evil.example.com"),
                      login(instance="https://pentera.my.salesforce.com.evil.example"), login(token=""), {"x": 1}, [1], b"not json"):
            with self.subTest(reply=str(reply)[:40]), self.assertRaises(vr.ReaderError):
                vr.fetch_token(CONFIG, FakeOpener({"/services/oauth2/token": [reply]}), NOW, fake_sign)

    def test_signing_failure_is_a_reason_code(self):
        def broken(*_args):
            raise vr.ReaderError("signing_failed")
        with self.assertRaises(vr.ReaderError) as caught:
            vr.fetch_token(CONFIG, FakeOpener({}), NOW, broken)
        self.assertEqual(caught.exception.reason, "signing_failed")

    def test_redirects_are_refused_so_the_bearer_token_cannot_leave(self):
        self.assertIsNone(vr._NoRedirect().redirect_request(object(), None, 302, "x", {}, "https://evil.example"))


class QueryTests(unittest.TestCase):
    def test_one_page_is_returned_with_a_bearer_header_on_the_instance_host(self):
        rest, opener = client({"/query": [{"done": True, "records": [{"Name": "CO-0001"}]}]})
        self.assertEqual(rest.query("SELECT Name FROM Customer_Onboarding__c"), [{"Name": "CO-0001"}])
        method, url, headers, _ = opener.requests[1]
        self.assertTrue(url.startswith(INSTANCE + "/services/data/v60.0/query?q=SELECT+Name"))
        self.assertEqual(headers["Authorization"], "Bearer " + TOKEN_VALUE)
        self.assertEqual(method, "GET")

    def test_paging_follows_only_links_under_the_query_path(self):
        base = "/services/data/v60.0/query/01gXYZ-2000"
        rest, opener = client({"/query": [{"done": False, "records": [{"n": 1}], "nextRecordsUrl": base},
                                          {"done": True, "records": [{"n": 2}]}]})
        self.assertEqual(rest.query("SELECT n FROM X"), [{"n": 1}, {"n": 2}])
        self.assertEqual(opener.requests[2][1], INSTANCE + base)
        for bad in ("https://evil.example/x", "/services/data/v60.0/sobjects/Account", base + "?x=1", base + "/../x", None):
            rest, _ = client({"/query": [{"done": False, "records": [], "nextRecordsUrl": bad}]})
            with self.subTest(bad=bad), self.assertRaises(vr.ReaderError):
                rest.query("SELECT n FROM X")

    def test_only_a_single_select_is_allowed(self):
        rest, opener = client({})
        for soql in ("DELETE FROM Account", "UPDATE Account SET Name='x'", "SELECT Id FROM Account; DELETE FROM Account",
                     "", "  insert into x", "SELECTId FROM Account", "SELECT Id FROM Account" + " " * 4000, None, 7):
            with self.subTest(soql=str(soql)[:30]), self.assertRaises(vr.ReaderError) as caught:
                rest.query(soql)
            self.assertEqual(caught.exception.reason, "query_not_allowed")
        self.assertEqual(opener.requests, [])  # nothing was sent, not even the login

    def test_a_401_renews_the_token_once_then_fails(self):
        rest, opener = client({"/services/oauth2/token": [login(), login("00D-NEW")],
                               "/query": [401, {"done": True, "records": []}]})
        self.assertEqual(rest.query("SELECT Id FROM X"), [])
        self.assertEqual([r[1].split("/services")[1].split("?")[0] for r in opener.requests],
                         ["/oauth2/token", "/data/v60.0/query", "/oauth2/token", "/data/v60.0/query"])
        rest, _ = client({"/query": [401]})
        with self.assertRaises(vr.ReaderError) as caught:
            rest.query("SELECT Id FROM X")
        self.assertEqual(caught.exception.reason, "http_401")

    def test_http_errors_carry_only_a_code_never_the_body(self):
        for status, reason in ((403, "http_403"), (429, "http_429"), (500, "http_error"), (404, "http_404")):
            rest, _ = client({"/query": [status]})
            with self.subTest(status=status), self.assertRaises(vr.ReaderError) as caught:
                rest.query("SELECT Id FROM X")
            self.assertEqual(caught.exception.reason, reason)
            self.assertNotIn("secret", str(caught.exception))

    def test_bad_shapes_and_limits_fail_closed(self):
        for reply in ({"done": True}, {"done": True, "records": "x"}, {"done": True, "records": [1]}, [1], b"{"):
            rest, _ = client({"/query": [reply]})
            with self.subTest(reply=str(reply)[:30]), self.assertRaises(vr.ReaderError):
                rest.query("SELECT Id FROM X")
        with patch.object(vr, "MAX_RECORDS", 2):
            rest, _ = client({"/query": [{"done": True, "records": [{}, {}, {}]}]})
            with self.assertRaises(vr.ReaderError) as caught:
                rest.query("SELECT Id FROM X")
            self.assertEqual(caught.exception.reason, "too_many_records")
        with patch.object(vr, "MAX_PAGES", 2):
            link = "/services/data/v60.0/query/01g-1"
            rest, _ = client({"/query": [{"done": False, "records": [], "nextRecordsUrl": link}]})
            with self.assertRaises(vr.ReaderError) as caught:
                rest.query("SELECT Id FROM X")
            self.assertEqual(caught.exception.reason, "too_many_pages")
        with patch.object(vr, "MAX_RESPONSE_BYTES", 10):
            rest, _ = client({"/query": [{"done": True, "records": [{"Name": "x" * 50}]}]})
            with self.assertRaises(vr.ReaderError) as caught:
                rest.query("SELECT Id FROM X")
            self.assertEqual(caught.exception.reason, "response_too_large")


class SfJsonAdapterTests(unittest.TestCase):
    class Unavailable(Exception):
        pass

    def test_cli_shape_for_the_exact_call_and_nothing_else(self):
        rest, _ = client({"/query": [{"done": True, "records": [{"Name": "CO-0001"}]}]})
        sf_json = vr.make_sf_json(rest, self.Unavailable)
        reply = sf_json(["data", "query", "--query", "SELECT Name FROM X", "--json"], "label")
        self.assertEqual(reply, {"status": 0, "result": {"records": [{"Name": "CO-0001"}], "totalSize": 1, "done": True}})
        for args in (["org", "display", "--json"], ["data", "query", "--query", "SELECT 1", "--json", "--use-tooling-api"],
                     ["data", "update", "record", "--sobject", "X"], ["data", "query", "--query", "SELECT Id FROM X"],
                     ["data", "query", "--query", "DELETE FROM X", "--json"], []):
            with self.subTest(args=args), self.assertRaises(self.Unavailable):
                sf_json(args)

    def test_reader_errors_become_the_callers_unavailable_exception(self):
        rest, _ = client({"/query": [500]})
        with self.assertRaises(self.Unavailable):
            vr.make_sf_json(rest, self.Unavailable)(["data", "query", "--query", "SELECT Id FROM X", "--json"])


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sf-reader.json"
        self.good = {"login_url": CONFIG.login_url, "client_id": CONFIG.client_id, "username": CONFIG.username,
                     "private_key_file": CONFIG.private_key_file, "api_version": CONFIG.api_version}

    def write(self, data, mode=0o640):
        self.path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        if os.name == "posix":
            os.chmod(self.path, mode)

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_a_valid_private_config_loads(self):
        self.write(self.good)
        self.assertEqual(vr.load_config(self.path), CONFIG)

    def test_invalid_contents_are_refused(self):
        bad_values = [{**self.good, "login_url": value} for value in (
            "http://login.salesforce.com", "https://login.salesforce.com.evil.example", "https://evil.example",
            "https://login.salesforce.com/", "https://.salesforce.com", "login.salesforce.com")]
        bad_values += [{**self.good, "client_id": "short"}, {**self.good, "username": "no-at-sign"},
                       {**self.good, "api_version": "60.0"}, {**self.good, "private_key_file": "relative/key.pem"},
                       {k: v for k, v in self.good.items() if k != "username"}, {**self.good, "extra": "x"},
                       {**self.good, "client_id": 5}]
        for data in bad_values:
            with self.subTest(data=str(data)[:60]), patch.object(vr, "_private_file", lambda *a, **k: None):
                self.write(data)
                with self.assertRaises(vr.ReaderError):
                    vr.load_config(self.path)
        with patch.object(vr, "_private_file", lambda *a, **k: None):
            self.write("not json")
            with self.assertRaises(vr.ReaderError):
                vr.load_config(self.path)
            with self.assertRaises(vr.ReaderError):
                vr.load_config(Path(self.tmp.name) / "missing.json")

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_group_writable_config_and_unsafe_key_files_are_refused(self):
        self.write(self.good, mode=0o660)
        with self.assertRaises(vr.ReaderError) as caught:
            vr.load_config(self.path)
        self.assertEqual(caught.exception.reason, "config_file_unsafe")
        key = Path(self.tmp.name) / "key.pem"
        key.write_text("pem", encoding="ascii")
        for mode, ok in ((0o640, True), (0o600, True), (0o604, False), (0o660, False), (0o666, False)):
            os.chmod(key, mode)
            if ok:
                vr._private_file(key, secret=True, reason="key_file_unsafe")
            else:
                with self.subTest(mode=oct(mode)), self.assertRaises(vr.ReaderError):
                    vr._private_file(key, secret=True, reason="key_file_unsafe")
        link = Path(self.tmp.name) / "link.pem"
        link.symlink_to(key)
        os.chmod(key, 0o600)
        with self.assertRaises(vr.ReaderError):
            vr._private_file(link, secret=True, reason="key_file_unsafe")
        with self.assertRaises(vr.ReaderError):
            vr._private_file(Path(self.tmp.name), secret=True, reason="key_file_unsafe")


class SourceBoundaryTests(unittest.TestCase):
    def test_no_write_verbs_or_foreign_hosts_in_the_module(self):
        source = Path(vr.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('method="POST"') + source.count("method='POST'"), 1, "only the token exchange may POST")
        for forbidden in ("PATCH", "DELETE", "PUT", "sobjects", "composite", "tooling", "http://"):
            self.assertNotIn(forbidden, source.replace("DELETE FROM", ""))


if __name__ == "__main__":
    unittest.main()
