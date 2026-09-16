"""Portable attended, read-only production renewal tenant lookup.

Runs on Windows or Ubuntu 22 with an isolated Playwright environment. The
operator completes SSO/MFA in a fresh temporary profile. No tenant is opened,
edited, saved, or retained; the result is one masked disposition.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REFERENCE = re.compile(r"CO-[0-9]{4,10}$")
PRODUCTION_LOGIN = "https://app.pentera.io/login"
TENANT_MANAGEMENT = "https://app.pentera.io/backoffice/tenantManagement"
MAX_WAIT_SECONDS = 15 * 60


def sf_command() -> str:
    return os.environ.get("SURFACE_SF_CLI", "sf.cmd" if os.name == "nt" else "sf")


def _normal(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def ce_only_tenant_name(account_name: str) -> str:
    """Return the one allowed renewal tenant name in normalized form."""
    normalized = _normal(account_name)
    if not normalized:
        raise ValueError("account_name_unavailable")
    return normalized + " - ce only"


def classify_rows(rows: list[tuple[str, str]], *, expected_company_name: str) -> str:
    """Classify one exact CE-only tenant result without returning tenant data.

    Domain similarity is deliberately not a match condition: a renewal must
    resolve to exactly ``<Salesforce Account Name> - CE Only``.  This keeps a
    base Surface tenant, a similarly named tenant, and any duplicate result in
    the manual-review path.
    """
    expected = _normal(expected_company_name)
    if not expected:
        return "tenant_target_unavailable"
    matches = [(company, domain) for company, domain in rows if _normal(company) == expected]
    if not matches:
        return "no_exact_account_found"
    return "existing_account_found" if len(matches) == 1 else "ambiguous_match"


def source_keys(reference: str) -> str:
    if not REFERENCE.fullmatch(reference):
        raise RuntimeError("invalid_co_reference")
    query = "SELECT Account_Name__c, Onboarding_Type__c FROM Customer_Onboarding__c WHERE Name = '" + reference + "' LIMIT 2"
    try:
        done = subprocess.run([sf_command(), "data", "query", "--query", query, "--json"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True, timeout=45, check=False)
        payload = json.loads(done.stdout)
        rows = payload["result"]["records"]
        if done.returncode or payload["status"] != 0 or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError()
        row = rows[0]
        account_name = _normal(row.get("Account_Name__c"))
        if "renewal" not in _normal(row.get("Onboarding_Type__c")) or not account_name:
            raise ValueError()
        return ce_only_tenant_name(account_name)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("salesforce_source_unavailable") from exc


def table_rows(page: Any) -> list[tuple[str, str]]:
    rows = page.locator("tbody tr")
    if rows.count() > 100:
        raise RuntimeError("tenant_result_schema_unavailable")
    output: list[tuple[str, str]] = []
    for index in range(rows.count()):
        cells = rows.nth(index).locator("td")
        if cells.count() < 2:
            raise RuntimeError("tenant_result_schema_unavailable")
        output.append((cells.nth(0).inner_text(), cells.nth(1).inner_text()))
    return output


def run(reference: str) -> str:
    try:
        expected_company_name = source_keys(reference)
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return "playwright_runtime_unavailable"
    except RuntimeError as error:
        return str(error)
    with tempfile.TemporaryDirectory(prefix="surface-renewal-browser-") as profile:
        with sync_playwright() as playwright:
            context = None
            try:
                context = playwright.chromium.launch_persistent_context(profile, headless=False, viewport={"width": 1280, "height": 900})
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(PRODUCTION_LOGIN, wait_until="domcontentloaded", timeout=45_000)
                deadline = monotonic() + MAX_WAIT_SECONDS
                while monotonic() < deadline:
                    if page.url.startswith("https://app.pentera.io/") and "/login" not in page.url:
                        break
                    sleep(1)
                else:
                    return "production_login_timeout"
                page.goto(TENANT_MANAGEMENT, wait_until="domcontentloaded", timeout=45_000)
                if page.url.split("?", 1)[0] != TENANT_MANAGEMENT:
                    return "production_tenant_page_unavailable"
                page.get_by_role("button", name="Search", exact=True).click(timeout=10_000)
                search = page.get_by_role("textbox", name="Search", exact=True)
                if search.count() != 1:
                    return "tenant_search_schema_unavailable"
                search.fill(expected_company_name, timeout=10_000); search.press("Enter", timeout=10_000)
                page.locator("tbody tr").first.wait_for(state="attached", timeout=15_000)
                return classify_rows(table_rows(page), expected_company_name=expected_company_name)
            except Exception:
                return "production_lookup_unavailable"
            finally:
                if context is not None:
                    context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Attended, read-only production renewal tenant existence check.")
    parser.add_argument("--co", required=True)
    args = parser.parse_args()
    print(json.dumps({"result": run(args.co), "proceed": False, "action_required": "human_review_required"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
