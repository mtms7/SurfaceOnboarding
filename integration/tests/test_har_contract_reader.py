"""tools/har_contract_reader.py: reads a HAR of a manual BackOffice create STRUCTURE-ONLY (owner decision 2026-10-21, path A).

It must show key names, value types and only non-identifying settings (booleans, small numbers, enum words, ISO-2 countries,
dates, the licence type); it must NEVER print a customer name, domain, e-mail, id, UUID, header, cookie or token. The synthetic HAR
below plants such values; none may appear in the output. Hermetic: temporary files only.
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools import har_contract_reader as reader

NAME, DOMAIN, EMAIL = "Secret Klinik GmbH", "secret-klinik.example", "milton.stevenson+secretklinik@pentera.io"
UID, UUID = "a1" * 12, "b2" * 16
TOKEN, COOKIE = "Bearer SECRETTOKEN123", "session=SECRETCOOKIE456"
CREATE_URL = "https://app.pentera.io/api/v1/backoffice/account/add"

BODY = {"accountName": NAME, "accountDomain": DOMAIN, "accountType": "customer", "accountCountryCode": "DE",
        "organizationEmail": EMAIL, "firstName": "Milton", "lastName": "Stevenson", "mfaRequired": True,
        "scanningInterval": "MONTHLY", "reconLevel": None, "campaignsTimeoutInHours": 90, "alternateDomains": [],
        "license": {"assetsNumber": "10000", "domainsNumber": "500", "subDomainsNumber": 500,
                    "licenseType": "prepaid annual subscription", "startDate": "2026-10-21T00:00:00.000Z",
                    "expirationDate": "2029-09-30"},
        "leakedCredentialsSettings": {"enabled": True, "scanningInterval": "WEEKLY", "domains": ["leaks.secret-klinik.example"],
                                      "spyCloudSettings": {"enabled": True}}}


def entry(method, url, status=200, body=None, response=None, headers=True):
    request = {"method": method, "url": url, "headers": [], "postData": {"mimeType": "application/json"}}
    if headers:
        request["headers"] = [{"name": "Authorization", "value": TOKEN}, {"name": "Cookie", "value": COOKIE},
                              {"name": "Content-Type", "value": "application/json"}]
    if body is not None:
        request["postData"]["text"] = json.dumps(body)
    return {"request": request, "response": {"status": status, "headers": [{"name": "Set-Cookie", "value": COOKIE}],
                                             "content": {"mimeType": "application/json", "text": json.dumps(response)
                                                         if response is not None else ""}}}


def har(entries):
    return {"log": {"version": "1.2", "entries": entries}}


FULL = har([
    entry("GET", "https://app.pentera.io/assets/index-abc123.js", headers=False),
    entry("POST", "https://app.pentera.io/api/v1/backoffice/getAllDetailedAccounts", body={"tableServerData": {"offset": 0}},
          response={"total_count": 1, "items": [{"id": UID}]}),
    entry("POST", CREATE_URL, body=BODY, response={"id": UID, "accountUuid": UUID, "accountName": NAME, "isActive": True}),
    entry("POST", "https://app.pentera.io/api/v1/backoffice/account/" + UID + "/edit", body={"x": 1}),
])


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, data, name="create.har"):
        path = self.dir / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def run_main(self, path):
        out = io.StringIO()
        with redirect_stdout(out):
            code = reader.main([str(path)])
        return code, out.getvalue()


class NoLeakTests(Base):
    def test_no_identifying_value_header_cookie_or_token_is_ever_printed(self):
        code, out = self.run_main(self.write(FULL))
        self.assertEqual(code, 0)
        for secret in (NAME, DOMAIN, EMAIL, UID, UUID, "SECRETTOKEN", "SECRETCOOKIE", "klinik", "secretklinik", "Milton",
                       "Stevenson", "leaks."):
            self.assertNotIn(secret, out, secret)
        self.assertEqual(len(out.strip().splitlines()), 1)  # one JSON line, easy to paste
        json.loads(out)

    def test_the_whole_report_is_free_of_secret_shaped_text_even_for_odd_values(self):
        odd = dict(BODY, accountName="Bearer abc.def.ghi", extra={"tok": "x" * 80, "ssn": "123-45-6789"})
        _code, out = self.run_main(self.write(har([entry("POST", CREATE_URL, body=odd, response={"id": UID})])))
        for secret in ("abc.def.ghi", "123-45-6789", "x" * 40):
            self.assertNotIn(secret, out)


class ShapeTests(Base):
    def report(self, data=FULL):
        _code, out = self.run_main(self.write(data))
        return json.loads(out)

    def test_the_create_body_shape_shows_keys_types_and_only_non_identifying_values(self):
        create = self.report()["create"]
        self.assertEqual((create["method"], create["status"], create["path"]), ("POST", 200, "/api/v1/backoffice/account/add"))
        body = create["request"]
        self.assertEqual(body["accountType"], "customer")
        self.assertEqual(body["accountCountryCode"], "DE")
        self.assertIs(body["mfaRequired"], True)
        self.assertEqual(body["scanningInterval"], "MONTHLY")
        self.assertEqual(body["campaignsTimeoutInHours"], 90)
        self.assertIsNone(body["reconLevel"])
        self.assertEqual(body["license"]["licenseType"], "prepaid annual subscription")
        self.assertEqual(body["license"]["assetsNumber"], "<string 5 chars>")  # numeric text stays a type marker (strings are hidden)
        self.assertEqual(body["license"]["subDomainsNumber"], 500)
        self.assertEqual(body["license"]["startDate"], "2026-10-21T00:00:00.000Z")
        self.assertEqual(body["leakedCredentialsSettings"]["scanningInterval"], "WEEKLY")
        self.assertIs(body["leakedCredentialsSettings"]["spyCloudSettings"]["enabled"], True)
        for hidden in ("accountName", "accountDomain", "organizationEmail", "firstName", "lastName"):
            self.assertRegex(body[hidden], r"^<string \d+ chars>$", hidden)
        self.assertEqual(body["alternateDomains"], {"list": 0})
        self.assertEqual(body["leakedCredentialsSettings"]["domains"],
                         {"list": 1, "items": ["<string %d chars>" % len("leaks.secret-klinik.example")]})

    def test_the_response_shape_marks_ids_by_kind_not_value(self):
        response = self.report()["create"]["response"]
        self.assertEqual(response["id"], "<hex 24>")
        self.assertEqual(response["accountUuid"], "<hex 32>")
        self.assertIs(response["isActive"], True)
        self.assertRegex(response["accountName"], r"^<string \d+ chars>$")

    def test_requests_are_listed_as_templates_without_static_files_or_ids(self):
        listed = self.report()["requests"]
        paths = [(item["method"], item["path"], item["status"]) for item in listed]
        self.assertIn(("POST", "/api/v1/backoffice/getAllDetailedAccounts", 200), paths)
        self.assertIn(("POST", "/api/v1/backoffice/account/add", 200), paths)
        self.assertIn(("POST", "/api/v1/backoffice/account/{id}/edit", 200), paths)
        self.assertFalse([p for p in paths if "assets" in p[1] or UID in p[1]])

    def test_credentials_in_the_file_are_flagged_by_count_and_never_shown(self):
        report = self.report()
        self.assertEqual(report["credentials_in_file"]["requests_with_authorization_or_cookie"], 3)
        self.assertIn("delete", report["warning"].lower())
        clean = self.report(har([entry("POST", CREATE_URL, body=BODY, response={"id": UID}, headers=False)]))
        self.assertEqual(clean["credentials_in_file"]["requests_with_authorization_or_cookie"], 0)

    def test_no_create_request_is_reported_as_such(self):
        report = self.report(har([entry("GET", "https://app.pentera.io/api/v1/x")]))
        self.assertIsNone(report["create"])
        self.assertIn("no_create_request", report["notes"])


class RobustnessTests(Base):
    def test_unreadable_or_oversize_or_wrong_files_are_refused_without_echoing_content(self):
        for content, reason in (("not json {", "har_unreadable"), ('{"log": {"entries": "x"}}', "har_unreadable"),
                                ("[]", "har_unreadable")):
            code, out = self.run_main(self.write(content))
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out), {"result": "refused", "reason": reason})
        code, out = self.run_main(self.dir / "missing.har")
        self.assertEqual((code, json.loads(out)["reason"]), (1, "har_unreadable"))
        with self.assertRaises(SystemExit):  # an argparse usage error never prints a path or content
            with redirect_stdout(io.StringIO()):
                reader.main([])

    def test_a_huge_file_is_refused_before_it_is_read(self):
        path = self.write(FULL)
        original = reader.MAX_BYTES
        reader.MAX_BYTES = 10
        try:
            code, out = self.run_main(path)
        finally:
            reader.MAX_BYTES = original
        self.assertEqual((code, json.loads(out)["reason"]), (1, "har_too_large"))

    def test_deep_and_wide_bodies_are_bounded(self):
        deep = current = {}
        for _ in range(60):
            current["n"] = {}
            current = current["n"]
        wide = {"k%d" % i: i for i in range(500)}
        code, out = self.run_main(self.write(har([entry("POST", CREATE_URL, body={"deep": deep, "wide": wide})])))
        self.assertEqual(code, 0)
        self.assertLess(len(out), 60_000)

    def test_the_module_has_no_network_no_subprocess_and_prints_one_line(self):
        text = Path(reader.__file__).read_text(encoding="utf-8")
        for forbidden in ("urllib", "socket", "subprocess", "http.client", "os.system", "import requests", "from requests"):
            self.assertNotIn(forbidden, text)
        self.assertFalse(re.search(r"print\((?!json\.dumps)", text))


if __name__ == "__main__":
    unittest.main()
