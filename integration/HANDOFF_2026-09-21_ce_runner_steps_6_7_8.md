# Handoff — CE-only attended runner, plan §18 steps 6–8

**Date:** 2026-09-21
**Status:** MID-FLIGHT. Local edits only. **The code does not currently run the Start
endpoint correctly** (see "Critical mid-flight state"). No external writes were made.

## 1. Current outcome (one line)

Steps 6 (revision-bound one-time authorization) and 7 (post-Confirm readback capture) are
**implemented in the runner module and partially wired into the dashboard**, but the
dashboard Start/GET handlers are **not yet updated**, so the Start endpoint is broken
(one-arg call to a now two-arg function). Step 8 (focused tests) is **not started**.

## 2. What was completed this session (all local)

### A. `tools/attended_ce_only_playwright.py` — FULLY REWRITTEN (untracked/new file)
Syntax verified OK. Changes:
- CLI now requires `--revision` (the source revision acknowledged by the dashboard).
- New: `RunnerStateUnavailable` exception.
- New constants: `RUNNER_STATE_PATH` (`integration/attended_ce_only_runner_state.json`),
  `READBACK_PATH` (`integration/attended_leonardo_readbacks.json`), `READBACK_SOURCE`
  (`"Leonardo Development Details readback"`), `READBACK_STATE` (`"Account Scanning"`),
  `SURFACE_ACCOUNT_ID_PATTERN` (`[A-Za-z0-9]{16,64}`), `ACCOUNT_UUID_PATTERN` (`[a-f0-9]{32}`).
- New state-file helpers: `_write_json_atomic`, `load_runner_state`, `record_runner_start`
  (idempotent per revision; a new revision supersedes), `record_runner_result` (first result
  wins; only for the matching revision; creates a missing record).
- New readback: `write_readback_evidence` (appends one entry in the exact dashboard schema,
  preserves other COs, refuses to overwrite corrupt existing evidence), `_detail_value`,
  `_readback_details` (opens the exact tenant row, reads the three labeled values).
- `_finish` records the final result best-effort and returns it.
- `run(reference, acknowledged_revision, *, review_wait_seconds=MAX_WAIT_SECONDS)`:
  - Fails closed with `source_revision_drift` if the fresh read differs from the
    acknowledged revision (before any browser launch).
  - After fill, waits for the operator's Confirm by polling for the filled form to close
    (`operator_review_timeout_no_create` on timeout).
  - Verifies the tenant exists (`operator_cancelled_no_create` if the form closed but no
    tenant was found), then reads back and validates the three values
    (`readback_schema_unavailable` / `readback_value_mismatch`), then writes local evidence
    (`readback_verified` on success, `readback_write_unavailable` on write failure).
  - Still never clicks Confirm and never updates Salesforce.
- Preserved: `ce_only_names`, `one_email_domain`, `source_for_fill`, `_exact_tenant_rows`.

### B. `tools/serve_attended_open_onboardings_dashboard.py` — PARTIALLY EDITED (incomplete)
Done:
- Import added: `RunnerStateUnavailable, load_runner_state, record_runner_start` from
  `tools.attended_ce_only_playwright`.
- `start_attended_ce_only_runner(reference, revision)` — now two-arg; passes `--revision`.
- New pure gate `evaluate_ce_only_start(acknowledged_revision, evaluation, state)` returning
  one of: `revision_acknowledgement_missing`, `preflight_blocked`, `source_revision_changed`,
  `revision_already_acknowledged`, `start`.
- New helper `_ce_only_start_form(evaluation)` — renders the form with a hidden
  `source_revision` field bound to the displayed revision.
- `page_co0702_fill_preflight(evaluation, runner_state=None)` — displays the source revision,
  shows/hides the Start form based on the state record, and switches the safety note.

## 3. CRITICAL mid-flight state (must fix before anything runs)

- **Line ~1117 (POST `/attended/start-co0702-ce-only-runner`)** still calls
  `start_attended_ce_only_runner(reference)` with **one** argument, but the function now
  requires `(reference, revision)` → **TypeError if the endpoint is hit.**
- The POST handler does **not yet** enforce revision binding or the one-time guard, and does
  **not** record the start. The new `evaluate_ce_only_start` / `load_runner_state` /
  `record_runner_start` are imported but unused.
- **Line ~1106 (GET `/attended/rerun-co0702-fill-preflight`)** calls
  `page_co0702_fill_preflight(evaluation)` — this works (default `runner_state=None`) but does
  **not** load/pass the runner state, so the page will not show last-run state.
- The module imports cleanly and the existing page function is backward compatible
  (`runner_state` defaults to `None`), so the test suite should still import and the existing
  CE-only page assertions should still hold — but this has **not been re-verified** this session.

## 4. Exact remaining work (resume order)

1. **POST start handler** (`tools/serve_attended_open_onboardings_dashboard.py`, ~line 1108–1118):
   - Read `acknowledged_revision = exact_form_value(form, "source_revision")`.
   - Load `state = load_runner_state()` (catch `RunnerStateUnavailable` → 503).
   - `decision = evaluate_ce_only_start(acknowledged_revision, evaluation, state)`.
   - Branch: `source_revision_changed` / `revision_already_acknowledged` / other non-`start`
     → 409 with a specific message; only on `start` proceed.
   - Call `start_attended_ce_only_runner(reference, evaluation.source_revision)`.
   - On success, `record_runner_start(reference, evaluation.source_revision,
     datetime.now().isoformat(timespec="seconds"))`; on write failure → 503 "do not retry,
     inspect the state file".
2. **GET preflight handler** (~line 1100–1106): load `state = load_runner_state()`
   (catch `RunnerStateUnavailable` → pass `None`) and pass it to
   `page_co0702_fill_preflight(evaluation, state)`.
3. **Step 8 — tests:**
   - New `integration/tests/test_attended_ce_only_runner.py`: state-file round-trip +
     one-time/supersede semantics, first-writer-wins result, corrupt-file fail-closed;
     readback evidence schema + preserve-other-COs + reject-invalid + refuse-corrupt;
     `ce_only_names` 15-char boundary; `one_email_domain` edge cases; `source_for_fill`
     failure modes (mock `subprocess.run`); `_exact_tenant_rows` classification (fake page);
     `_readback_details` (fake page); end-to-end `run()` with a fake `playwright.sync_api`
     injected via `sys.modules` (drift-stop-before-browser, timeout, cancel, readback-verified,
     value-mismatch).
   - Add dashboard tests to `integration/tests/test_attended_open_onboardings_dashboard.py`
     for `evaluate_ce_only_start` and the new page rendering (revision displayed, hidden field
     present, form hidden when current revision acknowledged/in-progress, form shown for a new
     revision).
4. **`.gitignore`**: add `integration/attended_ce_only_runner_state.json`.
5. **Plan §18 note**: record that steps 6–8 were completed locally on 2026-09-21 with test
   results; attended Dev run still pending (step 9 records the commit hash + actual Dev outcome
   after the run).
6. **Run the full suite**: `python -m unittest discover` from repo root (PowerShell; use `;`).

## 5. Test status

- Not re-run this session after the edits. Last known (prior session): 238 tests, 230 pass,
  1 expected Windows skip, 7 pre-existing `invalid_poll_timezone` errors in
  `integration/tests/test_scaffold.py` (Windows Python 3.12 missing `tzdata`; unrelated to this
  work, files untouched).
- The 2 new CE-only dashboard helper tests (`ce_only_names`, `one_email_domain`) passed before
  this session's edits.

## 6. Security posture

- Local edits + local test runs only. **No** Workato, Salesforce, OPA, Donatello, or
  BackOffice mutations. No credentials/tokens persisted.
- The attended CO-0702 Dev run (the only external Leonardo write path) is a separate
  operator-attended action requiring explicit approval and has **not** been performed.

## 7. Owner decisions (unchanged, from plan §18)

- Tenant name: `<Account Name> - CE Only` (regular hyphen).
- Primary User: `Milton Stevenson`.
- Email: `milton.stevenson+<alias>@pentera.io`; alias = names ≤15 chars → remove
  spaces/symbols, longer → word initials.
- Only Email Domains is a pass/fail validation gate.

## 8. How to resume (safe next actions)

1. Fix the POST start handler (item 4.1) — this un-breaks the Start endpoint and adds the
   revision/one-time gate.
2. Fix the GET preflight handler (item 4.2).
3. Add the tests (item 4.3), update `.gitignore` (4.4) and the plan note (4.5).
4. Run `python -m unittest discover`; confirm the 7 tzdata errors are the only failures.
5. Commit the slice (code + tests + plan note).
6. Then, separately and only with explicit approval: restart the dashboard once via
   `tools/start_attended_dashboard.ps1 -Restart` and perform the attended CO-0702 Dev run per
   plan §18.

## 9. Uncommitted work (git)

- `M integration/IMPLEMENTATION_PLAN.md` (plan §18 from prior session)
- `M integration/tests/test_attended_open_onboardings_dashboard.py` (2 CE-only tests, prior session)
- `M tools/serve_attended_open_onboardings_dashboard.py` (partial edits this session — INCOMPLETE)
- `?? tools/attended_ce_only_playwright.py` (full rewrite this session)
- Nothing staged.
