"""VM Salesforce reader (docs/43 part A): a read-only REST client that answers the dashboard's fixed ``sf data query``
calls with the same response shape, authenticated with a non-human OAuth JWT bearer identity (no human ``sf`` session).

Safety properties (each is tested):
* Configuration is a JSON file of identity values and a *path* to the private key; the key never enters this process's
  memory beyond what ``openssl`` is handed through its own file argument, and nothing here prints or stores a token, a
  key, an assertion or a response body. Errors carry only a value-free reason code.
* Only the exact CLI call shape ``["data", "query", "--query", <SOQL>, "--json"]`` is answered, and only a single
  ``SELECT`` statement (no ``;``). Everything else is refused, so there is no write path and no other API surface.
* Hosts are pinned: the login URL must be ``https://<name>.salesforce.com`` and the instance URL returned by the login must
  be too; redirects are refused so the bearer token cannot leave Salesforce; paging links must stay under the query path.
* Private files only: the key file and the config must be regular files, not links, not group-writable and not
  world-accessible (key), owned by root or the running user.
* Bounded: request timeout, page count, record count and response size are capped; any overrun fails closed.

This module lives in ``tools/`` (the offline-boundary guard forbids transport inside ``integration/onboarding``). It is not wired into the dashboard or the runner: ``tools/vm_refresh_snapshot.py`` uses it on a timer. Nothing
here runs unless an operator installs the credentials and the units (docs/43 section 6).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Sequence

CONFIG_ENV = "SURFACE_VM_SF_CONFIG_FILE"
CONFIG_KEYS = frozenset({"login_url", "client_id", "username", "private_key_file", "api_version"})
LOGIN_URL = re.compile(r"https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.salesforce\.com")
CLIENT_ID = re.compile(r"[A-Za-z0-9._]{20,128}")
USERNAME = re.compile(r"[^\s@<>'\"]{1,64}@[^\s@<>'\"]{1,190}")
API_VERSION = re.compile(r"v[0-9]{2}\.0")
SELECT_ONLY = re.compile(r"\s*SELECT\s", re.IGNORECASE)
# The one non-query read the dashboard makes: the Open_Onboardings list view's results (GET only, exact path).
LISTVIEW_RESULTS = re.compile(r"/services/data/v[0-9]{2}\.0/sobjects/Customer_Onboarding__c/listviews/[A-Za-z0-9]{15,18}/results")
JWT_LIFETIME_SECONDS = 180
REQUEST_TIMEOUT = 30
MAX_PAGES = 10
MAX_RECORDS = 10_000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 8192
_USER_AGENT = "SurfaceOnboardingVmReader/1"


class ReaderError(Exception):
    """Fail-closed outcome; ``reason`` is a stable, value-free code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ReaderConfig:
    login_url: str
    client_id: str
    username: str
    private_key_file: str
    api_version: str


@dataclass(frozen=True)
class Token:
    access_token: str = field(repr=False)
    instance_url: str


def _private_file(path: Path, *, secret: bool, reason: str) -> None:
    """Regular file, not a link, not group-writable, (for secrets) no access for others, owned by root or this user."""
    try:
        if path.is_symlink():
            raise ReaderError(reason)
        info = path.stat()
    except OSError:
        raise ReaderError(reason) from None
    forbidden = 0o027 if secret else 0o022
    if not stat.S_ISREG(info.st_mode) or info.st_mode & forbidden or info.st_size > MAX_FILE_BYTES:
        raise ReaderError(reason)
    if hasattr(os, "getuid") and info.st_uid not in (0, os.getuid()):
        raise ReaderError(reason)


def load_config(path: str | os.PathLike[str]) -> ReaderConfig:
    target = Path(path)
    _private_file(target, secret=False, reason="config_file_unsafe")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReaderError("config_unreadable") from None
    if not isinstance(raw, dict) or set(raw) != CONFIG_KEYS or not all(isinstance(raw[key], str) for key in CONFIG_KEYS):
        raise ReaderError("config_invalid")
    if not LOGIN_URL.fullmatch(raw["login_url"]) or not CLIENT_ID.fullmatch(raw["client_id"]) \
            or not USERNAME.fullmatch(raw["username"]) or not API_VERSION.fullmatch(raw["api_version"]) \
            or not os.path.isabs(raw["private_key_file"]):
        raise ReaderError("config_invalid")
    return ReaderConfig(**{key: raw[key] for key in sorted(CONFIG_KEYS)})


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def openssl_sign(key_file: str, message: bytes) -> bytes:
    """RS256 signature through the ``openssl`` CLI (no Python crypto dependency); the key is only a file argument."""
    try:
        done = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key_file], input=message, capture_output=True,
                              timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        raise ReaderError("signing_unavailable") from None
    if done.returncode or not done.stdout:
        raise ReaderError("signing_failed")
    return done.stdout


def build_assertion(config: ReaderConfig, now: datetime, sign: Callable[[str, bytes], bytes] = openssl_sign) -> str:
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("ascii"))
    claims = _b64(json.dumps({"iss": config.client_id, "sub": config.username, "aud": config.login_url,
                              "exp": int(now.timestamp()) + JWT_LIFETIME_SECONDS}, separators=(",", ":")).encode("ascii"))
    unsigned = header + "." + claims
    return unsigned + "." + _b64(sign(config.private_key_file, unsigned.encode("ascii")))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # the bearer token must never follow a redirect
        return None


def build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect)


def _open(opener: Any, request: urllib.request.Request) -> Any:
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise ReaderError("http_" + str(error.code) if error.code in (400, 401, 403, 404, 429) else "http_error") from None
    except (OSError, ValueError):
        raise ReaderError("network_error") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise ReaderError("response_too_large")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise ReaderError("response_invalid") from None


def fetch_token(config: ReaderConfig, opener: Any, now: datetime,
                sign: Callable[[str, bytes], bytes] = openssl_sign) -> Token:
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": build_assertion(config, now, sign)}).encode("ascii")
    request = urllib.request.Request(config.login_url + "/services/oauth2/token", data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": _USER_AGENT})
    reply = _open(opener, request)
    if not isinstance(reply, dict):
        raise ReaderError("response_invalid")
    token, instance = reply.get("access_token"), reply.get("instance_url")
    if not isinstance(token, str) or not token or not isinstance(instance, str) or not LOGIN_URL.fullmatch(instance):
        raise ReaderError("response_invalid")
    return Token(token, instance)


class RestQueryClient:
    """Read-only SOQL over REST for one identity; one token is reused and renewed once on a 401."""

    def __init__(self, config: ReaderConfig, opener: Any | None = None,
                 sign: Callable[[str, bytes], bytes] = openssl_sign,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._config, self._opener, self._sign, self._clock = config, opener or build_opener(), sign, clock
        self._token: Token | None = None

    def _get(self, path: str) -> Any:
        for attempt in (1, 2):
            if self._token is None:
                self._token = fetch_token(self._config, self._opener, self._clock(), self._sign)
            request = urllib.request.Request(self._token.instance_url + path, method="GET", headers={
                "Authorization": "Bearer " + self._token.access_token, "Accept": "application/json",
                "User-Agent": _USER_AGENT})
            try:
                return _open(self._opener, request)
            except ReaderError as error:
                if error.reason == "http_401" and attempt == 1:
                    self._token = None
                    continue
                raise
        raise ReaderError("http_401")

    def listview_results(self, path: str) -> dict[str, Any]:
        """GET the results of the Customer_Onboarding__c list view at exactly ``path`` (the CLI's ``api request rest``)."""
        if not isinstance(path, str) or not LISTVIEW_RESULTS.fullmatch(path):
            raise ReaderError("path_not_allowed")
        reply = self._get(path)
        if not isinstance(reply, dict) or not isinstance(reply.get("records"), list):
            raise ReaderError("response_invalid")
        if len(reply["records"]) > MAX_RECORDS:
            raise ReaderError("too_many_records")
        return reply

    def query(self, soql: str) -> list[dict[str, Any]]:
        if not isinstance(soql, str) or not SELECT_ONLY.match(soql) or ";" in soql or len(soql) > 4000:
            raise ReaderError("query_not_allowed")
        base = f"/services/data/{self._config.api_version}/query"
        path = base + "?" + urllib.parse.urlencode({"q": soql})
        records: list[dict[str, Any]] = []
        for _page in range(MAX_PAGES):
            reply = self._get(path)
            page = reply.get("records") if isinstance(reply, dict) else None
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise ReaderError("response_invalid")
            records.extend(page)
            if len(records) > MAX_RECORDS:
                raise ReaderError("too_many_records")
            if reply.get("done") is not False:
                return records
            following = reply.get("nextRecordsUrl")
            if not isinstance(following, str) or not following.startswith(base + "/") or "?" in following or ".." in following:
                raise ReaderError("response_invalid")
            path = following
        raise ReaderError("too_many_pages")


def make_sf_json(client: RestQueryClient, unavailable: type[Exception]) -> Callable[..., object]:
    """A drop-in for the dashboard's ``sf_json``: answers only the two exact CLI calls it makes, in the CLI's shapes.

    ``data query --query <SELECT> --json`` and ``api request rest <list view results path> --method GET``.
    """

    def sf_json(args: Sequence[str], label: str | None = None) -> object:
        if len(args) == 6 and list(args[:3]) == ["api", "request", "rest"] and list(args[4:]) == ["--method", "GET"]:
            try:
                return client.listview_results(args[3])
            except ReaderError as error:
                raise unavailable() from error
        if len(args) != 5 or list(args[:3]) != ["data", "query", "--query"] or args[4] != "--json":
            raise unavailable()
        try:
            records = client.query(args[3])
        except ReaderError as error:
            raise unavailable() from error
        return {"status": 0, "result": {"records": records, "totalSize": len(records), "done": True}}

    return sf_json
