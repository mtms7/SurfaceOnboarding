"""Read a HAR of a manual BackOffice create STRUCTURE-ONLY (owner decision 2026-10-21, path A: the owner creates CO-0800 by hand).

    python tools/har_contract_reader.py <path-to-har>

Prints ONE JSON line: the create request's key names, value types and only non-identifying values (booleans, small numbers, enum
words such as WEEKLY, ISO-2 country codes, dates, the licence type), the response shape (ids shown only as ``<hex 24>`` / ``<hex 32>``),
and the list of API calls (method, path template, status). It NEVER prints a name, domain, e-mail, id, UUID, header, cookie or
token, and only counts how many requests carried an Authorization or Cookie header so the owner knows to delete the file.
Local and read-only: no network access, no child processes, nothing written. The HAR holds a live session: keep it outside the repository.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any

MAX_BYTES = 150_000_000
MAX_DEPTH = 8
MAX_KEYS = 80
MAX_ITEMS = 3
MAX_REQUESTS = 200
CREATE_SUFFIX = "/account/add"
_KEY = re.compile(r"[A-Za-z0-9_.\-]{1,60}")
_ENUM = re.compile(r"[A-Z][A-Z0-9_]{1,40}")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}(?:T[0-9:.]{1,16}Z?)?")
_HEX = re.compile(r"[0-9a-fA-F]{16,64}")
_ID_SEGMENT = re.compile(r"[0-9a-fA-F]{16,64}|\d{4,}")
_URL_PATH = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^/?#]*(/[^?#]*)?")  # the path only: no query, no fragment, no host
SAFE_WORDS = frozenset({"customer", "prepaid annual subscription"})
WARNING = ("This HAR contains a live session (Authorization/Cookie headers): keep it outside the repository, never paste it, and "
           "delete it after it has been read.")


def _show_string(value: str) -> Any:
    if value in SAFE_WORDS or _ENUM.fullmatch(value) or _ISO.fullmatch(value):
        return value
    if _HEX.fullmatch(value):
        return "<hex %d>" % len(value)
    return "<string %d chars>" % len(value)


def shape(value: Any, depth: int = 0) -> Any:
    """The structure of a JSON value: key names, types, and only non-identifying values."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) < 10_000_000 else "<int %d digits>" % len(str(abs(value)))
    if isinstance(value, float):
        return "<number>"
    if isinstance(value, str):
        return _show_string(value)
    if depth >= MAX_DEPTH:
        return "<nested>"
    if isinstance(value, list):
        out: dict[str, Any] = {"list": len(value)}
        if value:
            out["items"] = [shape(item, depth + 1) for item in value[:MAX_ITEMS]]
        return out
    if isinstance(value, dict):
        out = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_KEYS:
                out["<more keys>"] = len(value) - MAX_KEYS
                break
            out[key if isinstance(key, str) and _KEY.fullmatch(key) else "<key>"] = shape(item, depth + 1)
        return out
    return "<other>"


def _template(path: str) -> str:
    return "/".join("{id}" if _ID_SEGMENT.fullmatch(part) else part for part in path.split("/"))


def _json_text(content: Any) -> Any:
    """The parsed JSON of a HAR ``text`` field (maybe base64), or None."""
    if not isinstance(content, dict) or not isinstance(content.get("text"), str) or not content["text"]:
        return None
    text = content["text"]
    try:
        if content.get("encoding") == "base64":
            text = base64.b64decode(text).decode("utf-8", "replace")
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _carries(part: Any, names: tuple[str, ...]) -> bool:
    """True when the HAR request/response ``part`` has a header with one of ``names`` (the header VALUE is never read)."""
    headers = part.get("headers") if isinstance(part, dict) else None
    for header in headers if isinstance(headers, list) else ():
        name = header.get("name") if isinstance(header, dict) else None
        if isinstance(name, str) and name.casefold() in names:
            return True
    return False


def report(har: dict[str, Any]) -> dict[str, Any]:
    entries = har["log"]["entries"]
    requests, create, credentials, set_cookies = [], None, 0, 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("request"), dict):
            continue
        request = entry["request"]
        response = entry.get("response") if isinstance(entry.get("response"), dict) else {}
        credentials += 1 if _carries(request, ("authorization", "cookie")) else 0
        set_cookies += 1 if _carries(response, ("set-cookie",)) else 0
        method, url = request.get("method"), request.get("url")
        if not isinstance(method, str) or not isinstance(url, str):
            continue
        found = _URL_PATH.fullmatch(url)
        path = _template(found.group(1) or "/") if found else ""
        if not path.startswith("/api/"):
            continue  # static files and pages are not part of the contract
        status = response.get("status") if isinstance(response.get("status"), int) else None
        if len(requests) < MAX_REQUESTS:
            requests.append({"method": method, "path": path, "status": status})
        if create is None and method == "POST" and path.endswith(CREATE_SUFFIX):
            body = _json_text(request.get("postData"))
            reply = _json_text(response.get("content"))
            create = {"method": method, "path": path, "status": status,
                      "request": shape(body) if body is not None else "<no json body>",
                      "response": shape(reply) if reply is not None else "<no json body>"}
    out: dict[str, Any] = {"result": "read", "entries": len(entries), "requests": requests, "create": create,
                           "credentials_in_file": {"requests_with_authorization_or_cookie": credentials,
                                                   "responses_with_set_cookie": set_cookies}, "notes": []}
    if create is None:
        out["notes"].append("no_create_request")
    if credentials or set_cookies:
        out["warning"] = WARNING
    return out


def _refuse(reason: str) -> int:
    print(json.dumps({"result": "refused", "reason": reason}))
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("har", help="path of the HAR file (kept outside the repository)")
    args = parser.parse_args(argv)
    path = Path(args.har)
    try:
        if path.stat().st_size > MAX_BYTES:
            return _refuse("har_too_large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data["log"]["entries"], list):
            raise ValueError("shape")
    except (OSError, ValueError, KeyError, TypeError):
        return _refuse("har_unreadable")
    print(json.dumps(report(data), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
