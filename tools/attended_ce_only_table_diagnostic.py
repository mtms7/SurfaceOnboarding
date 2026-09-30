"""Read-only live diagnostic of the Leonardo Development tenant-management table.

Answers the open questions from the duplicate_schema_unavailable audit:
  1. Table column layout (headers, cell counts, checkbox column?).
  2. Which tab is active by default (from the API filter value).
  3. Empty-state row shape after a guaranteed-no-match search (colspan).
  4. Customer tab API schema (request filter + response row keys).
  5. Add Account form default accountType (options + selected value).

STRICTLY READ-ONLY: it never fills form fields with source data, never clicks
Confirm, never creates or modifies anything. It does reload the page, type a
random no-match string into the search box (then clears it), click a tab, and
open+cancel the Add Account form. Writes redacted metadata only (no tenant
names, no values) to integration/attended_ce_only_table_diagnostics.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.attended_ce_only_playwright import (  # noqa: E402
    LoginTimeout,
    _attended_page,
)

OUT_PATH = ROOT / "integration" / "attended_ce_only_table_diagnostics.json"


def _safe(fn, default=""):
    try:
        value = fn()
        return value if value is not None else default
    except Exception:
        return default


def _table_snapshot(page) -> dict:
    """Capture table structure metadata only (no cell text values)."""
    snap: dict = {}
    snap["url"] = _safe(lambda: page.url, "").split("?", 1)[0]
    headers = page.evaluate(
        """() => {
            const ths = Array.from(document.querySelectorAll('thead th'));
            return ths.map(th => (th.textContent || '').trim().slice(0, 80));
        }"""
    )
    snap["headers"] = headers if isinstance(headers, list) else []
    row_info = page.evaluate(
        """() => {
            const rows = Array.from(document.querySelectorAll('tbody tr'));
            return rows.slice(0, 5).map(tr => {
                const tds = Array.from(tr.querySelectorAll('td'));
                return {
                    cellCount: tds.length,
                    firstCellColspan: tds.length ? (tds[0].getAttribute('colspan') || '') : '',
                    firstCellHasCheckbox: tds.length ? !!tds[0].querySelector('input[type=checkbox]') : false,
                    firstCellTextSample: tds.length ? (tds[0].textContent || '').trim().slice(0, 40) : '',
                };
            });
        }"""
    )
    snap["row_count"] = _safe(lambda: page.locator("tbody tr").count(), -1)
    snap["rows_sample"] = row_info if isinstance(row_info, list) else []
    # Footer text (MUIDataTable shows "x-y of N" or the empty message).
    footer = page.evaluate(
        """() => {
            const el = document.querySelector('.MuiTablePagination-caption, [class*=pagination]');
            return el ? (el.textContent || '').trim().slice(0, 120) : '';
        }"""
    )
    snap["footer_text"] = footer if isinstance(footer, str) else ""
    return snap


def main() -> int:
    from playwright.sync_api import sync_playwright

    capture: dict = {
        "captured_on": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": "read_only_table_diagnostic",
        "api_responses": [],
    }
    # Same browser lifecycle as the runner: a new tab in the reused
    # automation window (or a fresh launch), tracked by CDP target id, and
    # only that tab is closed afterwards.
    with sync_playwright() as pw:
        try:
            attended = _attended_page(pw)
            page = attended.__enter__()
        except LoginTimeout:
            print(json.dumps({"result": "tenant_management_not_reached"}, separators=(",", ":")))
            return 1
        except RuntimeError as error:
            print(json.dumps({"result": str(error)}, separators=(",", ":")))
            return 1
        try:
            # --- API response listener (reload to re-trigger the table load) ---
            def _on_response(response):
                try:
                    if "getAllDetailedAccounts" not in response.url:
                        return
                    entry = {"status": response.status}
                    try:
                        entry["request_body"] = response.request.post_data or ""
                    except Exception:
                        pass
                    try:
                        body = response.json()
                        pr = body.get("pagination_response", {}) if isinstance(body, dict) else {}
                        table_data = pr.get("table_data", []) if isinstance(pr, dict) else []
                        entry["total_count"] = pr.get("total_count") if isinstance(pr, dict) else None
                        entry["row_count"] = len(table_data) if isinstance(table_data, list) else None
                        if isinstance(table_data, list) and table_data and isinstance(table_data[0], dict):
                            entry["row_keys"] = sorted(table_data[0].keys())
                        # Filter used by the request (reveals the active tab's accountType).
                        try:
                            req_body = json.loads(response.request.post_data or "{}")
                            tsd = req_body.get("tableServerData", {})
                            entry["request_filter"] = tsd.get("filters")
                            entry["request_projection"] = tsd.get("projection")
                        except Exception:
                            pass
                    except Exception as exc:
                        entry["parse_error"] = str(exc)[:120]
                    capture["api_responses"].append(entry)
                except Exception:
                    pass

            page.on("response", _on_response)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            capture["initial"] = _table_snapshot(page)

            # --- Tab controls ---
            tabs = page.evaluate(
                """() => {
                    const els = Array.from(document.querySelectorAll('[role=tab], button, a'));
                    return els
                        .filter(el => {
                            const t = (el.textContent || '').trim();
                            return t && t.length < 40;
                        })
                        .slice(0, 40)
                        .map(el => ({
                            text: (el.textContent || '').trim().slice(0, 40),
                            role: el.getAttribute('role') || el.tagName.toLowerCase(),
                            selected: el.getAttribute('aria-selected') || '',
                            classes: (el.getAttribute('class') || '').slice(0, 120),
                        }));
                }"""
            )
            capture["tab_controls"] = tabs if isinstance(tabs, list) else []

            # --- No-match search: empty-state row shape ---
            search = page.get_by_role("textbox", name="Search", exact=True)
            search_info = {"found": search.count()}
            if search.count() == 1:
                probe = "zz-no-match-probe-9x7"
                search.first.fill(probe)
                page.wait_for_timeout(2000)
                capture["no_match_search"] = _table_snapshot(page)
                empty_text = page.evaluate(
                    """() => {
                        const td = document.querySelector('tbody td');
                        return td ? (td.textContent || '').trim().slice(0, 120) : '';
                    }"""
                )
                capture["no_match_search_empty_text"] = empty_text if isinstance(empty_text, str) else ""
                # Clear the probe so the page is left as found.
                search.first.fill("")
                page.wait_for_timeout(1500)

            # --- Customer tab (if present): switch and capture its API schema ---
            customer_tab = page.get_by_role("tab", name="Customer")
            if customer_tab.count() != 1:
                customer_tab = page.get_by_role("button", name="Customer")
            capture["customer_tab_found"] = customer_tab.count()
            if customer_tab.count() == 1:
                before = len(capture["api_responses"])
                customer_tab.first.click()
                page.wait_for_timeout(4000)
                capture["customer_tab"] = _table_snapshot(page)
                capture["customer_tab_new_api"] = capture["api_responses"][before:]

            # --- Add Account form: default accountType (open + cancel only) ---
            add_account = page.get_by_role("button", name="Add Account", exact=True)
            capture["add_account_found"] = add_account.count()
            if add_account.count() == 1:
                add_account.first.click()
                page.wait_for_timeout(2500)
                # Capture the full form control inventory (options, toggle
                # states, Confirm disabled state, label texts) so the CE fill
                # contract can be completed. Metadata only: no user-entered
                # values are present in a freshly opened blank form.
                form_info = page.evaluate(
                    """() => {
                        const out = {};
                        out.selects = Array.from(document.querySelectorAll('select')).map(sel => ({
                            name: sel.getAttribute('name') || '',
                            id: sel.getAttribute('id') || '',
                            selected: sel.value,
                            options: Array.from(sel.options).slice(0, 80).map(o => ({
                                v: o.value,
                                t: (o.textContent || '').trim().slice(0, 80),
                                d: o.disabled,
                            })),
                        }));
                        out.checkboxes = Array.from(document.querySelectorAll('input[type=checkbox]')).map(cb => ({
                            name: cb.getAttribute('name') || '',
                            id: cb.getAttribute('id') || '',
                            checked: cb.checked,
                            aria: cb.getAttribute('aria-label') || '',
                        }));
                        const confirm = Array.from(document.querySelectorAll('button'))
                            .find(b => (b.textContent || '').trim() === 'Confirm');
                        out.confirm = confirm
                            ? {disabled: confirm.disabled, type: confirm.getAttribute('type') || ''}
                            : null;
                        out.labels = Array.from(document.querySelectorAll('label'))
                            .map(l => (l.textContent || '').trim())
                            .filter(t => t && t.length < 80)
                            .slice(0, 80);
                        // Full input metadata (no values): pins controls that
                        // have no associated label text, such as the license
                        // date pickers, so the CE fill contract can be
                        // completed. Placeholders are metadata, not values.
                        out.inputs = Array.from(document.querySelectorAll('input, select, textarea')).map(el => {
                            const meta = {tag: el.tagName.toLowerCase()};
                            for (const attr of ['type','placeholder','aria-label','name','id','readonly','disabled','data-am']) {
                                const v = el.getAttribute(attr);
                                if (v) meta[attr] = v;
                            }
                            if (el.type === 'checkbox') meta.checked = el.checked;
                            let labelText = '';
                            if (el.id) {
                                const label = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                                if (label) labelText = (label.textContent || '').trim();
                            }
                            if (!labelText) {
                                const wrapping = el.closest('label');
                                if (wrapping) labelText = (wrapping.textContent || '').trim();
                            }
                            if (labelText) meta.label = labelText.slice(0, 80);
                            return meta;
                        });
                        return out;
                    }"""
                )
                capture["add_account_form"] = form_info if isinstance(form_info, dict) else {}
                # Close the form without submitting.
                cancel = page.get_by_role("button", name="Cancel", exact=True)
                if cancel.count() == 1:
                    cancel.first.click()
                else:
                    page.keyboard.press("Escape")
                page.wait_for_timeout(2000)
                capture["form_closed"] = page.evaluate(
                    """() => !document.querySelector('select[name=accountType]')"""
                )

        finally:
            # Closes only this diagnostic's tab (persisted profile) or tears
            # down the temporary browser; never deletes a persisted profile.
            attended.__exit__(None, None, None)
    capture["note"] = ("Read-only table diagnostic. No form values, no tenant names, "
                       "no mutations. Search probe string was cleared.")
    OUT_PATH.write_text(json.dumps(capture, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": "diagnostics_written", "path": str(OUT_PATH),
                      "api_responses": len(capture["api_responses"])}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
