# Production BackOffice CE-only: design and first-create checklist (2026-10-13)

**Status: proposal, local document only.** Nothing here authorizes a production BackOffice (BO) read or write, changes a guard, or enables `PRODUCTION_RUNNER_SUPPORTED`. Production BO writes stay blocked until a separate, recorded owner approval (AGENTS.md, docs/38 §3). Owner request (2026-10-13): "CE on BO" means the **CE-only** onboarding (Credential Exposure / New Product Onboarding) run against production BO instead of Leonardo Development (Dev); other cases follow later.

Basis: gap-list sweep of 2026-10-13 (read-only, code and docs; items read from code, not re-run). Re-check the cited lines before building.

## 1. What exists today (reused as is)

- Dev CE-only runs live from the attended dashboard with a fixed SOQL, revision-drift check, `salesforce_id_already_present` gate, duplicate check by name and domain, an uncertain submit never re-clicked, and read-after-write readback (`--readback-only`, `--duplicate-check`).
- Production reads that are approved: the Redash production clone only (`production_duplicate_gate`, fail closed when older than 6 h; `production_match` and `is_live_paid_tenant`, owner 2026-10-05/06). Direct BO inventory reads are not (`ENVIRONMENTS["prod"].approved=False`).
- Timing rule `onboarding_allowed_now` (production from start minus 2 days, earlier on a CSM request, owner 2026-10-04), coded but never exercised.

## 2. The production lock today (must stay true until the approval exists)

| Layer | Guard | Where |
|---|---|---|
| Config | `AppConfig` raises `production_and_unknown_targets_are_blocked` | `integration/onboarding/config.py` |
| Origin | only the Dev origin is accepted; the production origin constant is unused | `integration/onboarding/origin_policy.py` |
| Sessions | `production_unlock_problem` needs the env flag `SURFACE_ONBOARDING_PRODUCTION_SESSIONS=1`, an approval reference (`^[A-Z][A-Z0-9_-]{5,63}$`), issued and expires timestamps (timezone-aware, lifetime at most 31 days), and `PRODUCTION_RUNNER_SUPPORTED` | `session_readiness.py` |
| Runner support | `PRODUCTION_RUNNER_SUPPORTED = False` (hard-coded) | `session_readiness.py:64` |
| Runner | Dev URLs and origin are hard-coded; no production mode | `tools/attended_ce_only_playwright.py` |
| Salesforce | ID writeback off (owner 2026-10-01) | dashboard |

## 3. Proposed design (not built)

Principle: production support is added as **new, separately gated code** that cannot be reached until every layer above is deliberately opened, and each opening is its own reviewed change. Never relax a guard to make a test pass.

1. **Target as a value, not a constant.** The runner takes a validated `RunTarget` (environment key, exact origin, profile dir name) built from `ENVIRONMENTS`; Dev remains the default and every existing test keeps passing. A production `RunTarget` can only be constructed when `production_unlock_problem(...)` returns `None`; otherwise it raises with that reason.
2. **Exact origin allowlist.** Add `require_production_origin` next to `require_development_origin`: exact string match against `https://app.pentera.io`, rejecting every variation (scheme, port, subdomain, userinfo, trailing path tricks). Not wired to any caller until step 4 below.
3. **Separate profile and session.** Production uses `backoffice-production-automation`; Dev and production never share cookies. MFA stays attended; the operator types credentials into the real login page; no password, code, cookie or token is read, logged or persisted (AGENTS.md).
4. **Approval bound to one create.** A production write needs, in addition to the session unlock: an approval reference, a scope of **CE-only and exactly one named CO**, issued/expires times, and the CO's Salesforce revision at approval time. Revision drift, a different CO, a different route, or an expired approval fails closed. Approval is consumed by the create (one create per approval); a retry needs a fresh owner go. The approval file is edited only by the owner on a trusted desktop; `issued_by` is checked against the allowed issuers when the approval is issued, and the loader re-validates only its shape.
5. **Dry run first.** The first pass fills and verifies the form with the **Confirm button not clicked**, writes a redacted result (field names and pass/fail only, no values), and stops. The create is a second, explicitly requested action.
6. **Fail-closed on uncertainty.** Any of these stops the run with nothing created: stale clone (over 6 h) or clone mismatch; duplicate name or domain in production; more than one live paid tenant for the exact name (rule 2026-10-06); ambiguous Salesforce data (not exactly one valid email domain, mismatched source data); authentication or WAF failure; unexpected schema; the timing window not met without a recorded CSM request; an uncertain submit (never re-clicked). Implemented order (`production_create.evaluate_gate`): production target, session unlock reference equals the approval reference, current Salesforce revision equals the approval revision, approval check, then the route, domain, source, prior-UUID, clone, duplicate, live-paid, timing, sessions and schema rules; the whole gate runs again on a fresh clock reading just before the approval is consumed.
7. **Read-after-write.** After a create, readback in BO by tenant name and domain; the run is `readback_verified` only if the tenant exists with the expected fields. No claim of success without it (AGENTS.md).
8. **Rollback is a precondition, not an afterthought.** No BO tenant delete or disable path is documented. Before the first create the BO owner must state how a wrongly created tenant is removed or neutralized, and who does it.
9. **Salesforce writeback stays off** until the owner decides who writes the UUID and stage in production (Q13).

## 4. Tests that must exist before any production code is reachable (all local, no external calls)

- `PRODUCTION_RUNNER_SUPPORTED` is `False`; `production_unlock_problem` returns each reason in order (flag, reference, timestamps, expiry, lifetime over 31 days, runner not supported).
- `AppConfig` still raises for production and unknown targets.
- `require_production_origin` accepts the exact origin only; table of rejected variations.
- Building a production `RunTarget` raises unless every unlock input is valid; Dev `RunTarget` unchanged.
- A production create entry point refuses when: no approval; wrong CO; wrong route; revision drift; expired or consumed approval; stale clone; duplicate; more than one live paid tenant; dry-run mode (no Confirm click, asserted on a fake page object).
- The dashboard shows the production messages (`production_sessions_flag_missing`, `production_approval_missing`, `production_approval_expired`, `production_runner_not_supported`) and no production start button while locked.
- The offline-boundary and artifact guards still pass; no new network call outside the runner's existing browser path.

## 5. Decisions needed (owner unless stated)

| # | Decision | Owner |
|---|---|---|
| D1 | Written approval scope for production writes: CE-only, one named CO per approval, expiry, who may issue it | Owner (Milton); SecOps per plan §10 |
| D2 | Rollback method for a wrong tenant (delete/disable path, who does it) | Owner with the BO owner |
| D3 | Salesforce UUID/stage writeback in production: this tool or the CSM (Q13) | Owner |
| D4 | Permission for a **read-only** BO production probe (form fields, `getAllDetailedAccounts`, `account/add` response shape, tenant search/readback) with attended MFA | Owner; SecOps if required |
| D5 | Production BO operator handling at create (docs/38 S4: leave empty) confirmed for production | Owner |
| D6 | First candidate CO and its CSM timing (start minus 2 days, or CSM request) | Owner |

## 6. First-production-create checklist (every line must be true; any miss stops the run)

**Before the day (local and read-only)**
- [ ] D1-D6 decided and recorded in the plan with date; approval reference issued with expiry no more than 31 days out.
- [ ] Code for section 3 reviewed; section 4 tests pass locally and on the VM-style runtime; both guards pass; one commit per change.
- [ ] Fresh Redash clone collected by the operator (read-only); age under 6 h at run time.
- [ ] Candidate CO: fixed-query Salesforce validation passes (exactly one valid email domain, route `case_2_new_ce_only`, no prior UUID, revision recorded); `production_match` shows no live paid tenant with the exact name (or exactly one, as the rule requires); duplicate gate clear.
- [ ] D4 probe done and its schema findings recorded (field names only, no values) and compared with the Dev contract.

**On the day (attended)**
- [ ] Operator signs in to production BO in the dedicated automation profile with MFA; session probe `ready`.
- [ ] Production environment flag, approval reference and expiry set for this one CO only.
- [ ] Dry run passes with Confirm not clicked; redacted result reviewed by the owner.
- [ ] Owner says **go** for this single create; the run clicks Confirm once; an uncertain outcome is never re-clicked.
- [ ] Readback verifies the tenant (name, domain, key fields); result recorded as `readback_verified` or `unverified` honestly.

**After**
- [ ] Approval consumed and flags cleared; production session profile signed out; no secret persisted.
- [ ] Plan handoff updated with the dated outcome and any deviation; Salesforce writeback only if D3 allows it.

## 6.1 Running the D4 read-only probe (built 2026-10-16, not yet run)

Authority: D4 (docs/45), owner-accepted 2026-10-16. Read-only; it is not the production create and does not unlock it (`PRODUCTION_RUNNER_SUPPORTED` stays `False`; a production `RunTarget` still raises `ProductionLocked`).

**Command** (PowerShell, attended desktop, from the repo root):

```
$env:SURFACE_ONBOARDING_PRODUCTION_PROBE = "1"
$env:SURFACE_ONBOARDING_PRODUCTION_PROBE_REF = "PROBE-D4-20261016"   # ^[A-Z][A-Z0-9_-]{5,63}$
python tools/attended_ce_only_playwright.py --production-probe
Remove-Item Env:SURFACE_ONBOARDING_PRODUCTION_PROBE, Env:SURFACE_ONBOARDING_PRODUCTION_PROBE_REF
```

**What you do:** a Chrome window opens on `https://app.pentera.io/backoffice/tenantManagement` in the dedicated profile `backoffice-production-automation` (never the Dev profile). Sign in yourself, MFA included; the code types nothing and waits up to 15 minutes. After sign-in leave the window alone; the probe attaches to its own tab, reads, closes its tab and then closes the automation window.

**What it does (read only):** opens the "Add Account" form without typing in it, reads each control's label, name, type and required flag, then cancels the form (Escape if no Cancel); opens one row's action menu to see whether Edit exists, never clicking Edit; checks Search exists. Clicks are limited to those three kinds and the code has no path to Confirm. Every request that is not GET/HEAD is aborted, except two read POSTs the Dev runner already uses (`getAllDetailedAccounts`, `account/<id>/campaign/executions`); attempted writes are counted, not sent.

**Sign-in wait (fixed after the second live run):** the probe opens or reuses one visible window of the dedicated profile, navigates that same tab with a GET to `https://app.pentera.io/backoffice/tenantManagement`, brings it to the front and prints "Sign in with SSO and MFA in the window titled app.pentera.io; waiting up to 15 minutes" (a reminder "waiting for sign-in" every 60 s). Sign-in counts as complete only when, on 3 consecutive polls 1 s apart, the path starts with `/backoffice` and contains no "login", none of "Log In" / "Sign in with SSO" / "Forgot your password?" is visible, and a table, navigation element or other non-login control is visible. Nothing is clicked or typed while waiting and nothing is inspected before then. After 15 minutes, or if the page is on a login path at the end, the result is `production_probe_login_timeout`, never `production_probe_complete`.

**Settling and diagnostics (added after the first live run was inconclusive):** after sign-in the probe records the path, GETs `/backoffice/tenantManagement` once if elsewhere, waits up to 20 s for network idle and up to 20 s for a table row or empty-state text, then inspects. It tries the create-control labels Add Account, Add account, Create Account, New Account, Add, Create (exact visible text, button then link, exactly one match) and records which matched. If none match it still finishes `production_probe_complete` with `create_control_found` failed and a `visible_controls` list (up to 60 buttons/links: tag, role, text up to 40 chars with emails and ids masked, enabled flag), the page title and the first 3 headings.

**What is recorded:** `production-probe/<UTC stamp>.json` in the state folder (default `integration/production-probe/`, git-ignored): page titles and URL paths without query strings, form control metadata, Search/Edit presence, API call method/path templates/status codes, blocked-request counts, which Dev-contract labels and names production lacks, and pass/fail checks. One line is printed: result, checks passed/failed, file name. The writer refuses to save a report containing email-, UUID-, token- or password-shaped text.

**What is not recorded or done:** no field values, request/response bodies, headers, cookies, tokens, passwords or MFA codes; no create, edit or search typing; no Salesforce access; no tenant name or id (ids in paths become `{id}`). Not covered by the probe: the `account/add` response shape (only visible at a real create) and tenant search/readback by typing (it does not type into Search).

See 6.3 for the read-only readback of a created tenant (same gate, same window and guard).

Result codes: `production_probe_complete`, `production_probe_login_timeout`, `production_probe_flag_missing`, `production_probe_reference_invalid`, `production_probe_page_unavailable`, `playwright_runtime_unavailable`. Afterwards re-check the checklist line "D4 probe done" and compare `dev_contract` with the Dev contract before any dry run.

## 6.2 Running the production DRY RUN (built 2026-10-16, locked, not yet run)

**Status: `PRODUCTION_DRY_RUN_SUPPORTED` is hard-coded `False` in `integration/onboarding/session_readiness.py` and stays `False` until the owner says go** (owner decision 2026-10-16: no production run until told). It is a separate lock from `PRODUCTION_RUNNER_SUPPORTED` (also `False`, never touched): the dry-run target `run_target.production_dry_run_target` raises `ProductionLocked("production_dry_run_not_supported")` while the flag is `False`, the live `production_target` ignores the dry-run flag, and the dry run ignores the runner flag. With the flag `False` the command below writes one `refused` record and does nothing else (no Salesforce read, no browser, no network). Flipping the flag is its own reviewed change after the owner's go; nobody edits it as part of a run.

**What it does (one CE-only New CO, first candidate CO-0797):** the real production Add Account form is opened, the intended values are typed, the Advanced section is expanded and its controls only READ, every control is read back, and the form is Cancelled. Confirm/Submit/Save are never clicked (a click guard refuses any control named like them), `click_confirm` is deliberately `NotImplementedError`, and every request except GET/HEAD and the one read POST `getAllDetailedAccounts` is aborted (an `account/add` or `account/edit` attempt is blocked, counted and refuses the run as `write_request_blocked`). The approval is never consumed.

**Owner steps** (PowerShell, attended desktop, repo root):

1. Say **go** for the dry run. A reviewed change then sets `PRODUCTION_DRY_RUN_SUPPORTED = True` (nothing else changes).
2. Collect a fresh Redash clone (under 6 h at run time; the dry run refuses `clone_stale` / `clone_missing` otherwise).
3. Issue the one-CO approval (docs/45 D1: CE-only, CO-0797, 7 days, issued by you) bound to the CURRENT Salesforce revision of the CO (`production_approval.issue`). Run, from the repo root, in an interactive terminal with `SURFACE_ONBOARDING_APPROVAL_ISSUERS` set to the issuer address(es) (comma separated; if unset, `SURFACE_DASHBOARD_ALLOWED_USERS` is used only when it holds exactly one address, otherwise `issuer_list_ambiguous`; `--issuer EMAIL` picks one of several), `SURFACE_SF_EXPECTED_ORG_ID` set (the pinned alias must be that org, else `salesforce_org_not_pinned`) and the desktop runtime (`SURFACE_ONBOARDING_RUNTIME=vm` is refused as `desktop_only`): `python tools/issue_production_approval.py --co CO-0797 --dry-run` to see the summary (nothing written), then `python tools/issue_production_approval.py --co CO-0797` and type `ISSUE CO-0797` (default ref `PROD-CE-0797-<UTC date>`, 7 days, max 31 via `--lifetime-days`; `--approval-ref` overrides). `--status` lists the CO's approvals without Salesforce. It reads the CO once with the dashboard's read-only helper, refuses unless CE-only New, Approved, one valid email domain, no Account UUID and a revision, writes nothing to Salesforce, prints only the state file name, refuses a second live approval for the same CO and revision (`approval_already_active_for_co`; a changed revision marks the old record `superseded_at`, never deleted), and does not unlock anything. A changed revision means the approval is refused as `approval_revision_drift`; issue a new one.
4. Set the session flag and the approval reference (the issue and expiry times are read from the approval record), then run with the same revision:

```
$env:SURFACE_ONBOARDING_PRODUCTION_SESSIONS = "1"
$env:SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF = "PROD-CE-0797-20261016"   # the approval reference, ^[A-Z][A-Z0-9_-]{5,63}$
python tools/attended_ce_only_playwright.py --production-dry-run --co CO-0797 --revision "<Salesforce revision>"
Remove-Item Env:SURFACE_ONBOARDING_PRODUCTION_SESSIONS, Env:SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF
```

5. A Chrome window opens in the profile `backoffice-production-automation`; sign in yourself with SSO and MFA (the code types nothing there and waits up to 15 minutes). Leave the window alone afterwards.
6. Read the one printed line and the record `production-create-records/CO-0797-<UTC stamp>.json` (state folder, git-ignored). `dry_run_passed` means every check below passed; anything else is `refused` with a reason code. Send the record's field names, pass/fail and `advanced_*` lines to the main session; it holds no values.

**Order of checks (all fail closed, first refusal wins):** (1) unlock target: `PRODUCTION_DRY_RUN_SUPPORTED`, sessions flag, approval reference, exact origin `https://app.pentera.io`; then the fixed Salesforce reads of the Dev runner, the clone, and the ordered gate (approval bound to the CO, route `case_2_new_ce_only` and the current revision; single valid email domain; no prior UUID; fresh clone; no duplicate or second live paid tenant in the clone; timing). No browser opens before this passes. (2) Session, from the page's OWN traffic and status codes only (docs/48 note 2026-10-18; the earlier cookie-only `fetch` of `getAuthorities` / `accountInfo` was refuted live): the status of the page's own rewritten `getAllDetailedAccounts` reply must be 200 and the statuses of its own start-up calls to `getAuthorities` / `accountInfo` are noted when it makes them (no token, cookie, header or body is read), else `production_session_expired` (401/403) / `production_session_not_ready`. (3) Intended values come from the Dev CE-only plan, mapped to the production names and labels (names exact, labels whitespace-tolerant). (4) Duplicate pre-check through the page's own table request (body rewritten, see docs/48 note 2026-10-18): `getAllDetailedAccounts` name searches for the tenant name and the primary domain (Dev grammar), exact name or domain on any row (also alternate and email domain lists, deleted rows included) refuses `duplicate_in_production`; more matches than rows `duplicate_ambiguous`; any non-200 or odd shape `duplicate_check_unavailable`; primary or Salesforce email domain in `demoAccountDomains` `demo_domain_conflict`. (5) Open the form, fill, expand "Advanced options" and read `automatedDiscoveryEnabled`, `subDomainsReconEnabled`, `webDictionaryBruteForceEnabled`, `webDorkingEnabled`, `fullNucleiScanEnabled`, `authenticatedTestingEnabled`, `staticOutboundIpEnabled`, `aiEnabled`, `multipleAttackStacksEnabled` (and `spyCloudEnabled`, informational, no Dev target). The record states `advanced_section` found/not_found and each control as on/off/absent; `advanced_mismatches` lists controls whose state differs from the Dev target (all OFF) or that are absent. A differing state refuses `advanced_mismatch` (docs/53, 2026-10-18); an absent control the Dev create sets refuses `advanced_control_absent` / `advanced_section_not_found`. (6) Every control is read back (text, select, toggle, both dates, blank fields, empty operator); select option counts must equal the probe (3, 251, 4, 4, 5); a required control outside the recorded form, or a missing recorded control, refuses `unexpected_schema`. The session is checked again and any blocked write request refuses. (7) The form is Cancelled (`form_not_cancelled` refuses if it stays open). (8) One redacted record.

**Hardening after review (2026-10-16):** the run refuses `service_worker_present` (and records nothing else) if the page has any service-worker registration, because the request guard cannot see worker or WebSocket traffic; toggles are clicked only after an `elementFromPoint` check that the toggle or its own label is hit, with no forced click ever (`toggle_click_failed`); only the exact button "Add Account" is ever clicked; no Enter key or newline is ever sent; the clock is read again at the end and a lapsed approval or a clone over 6 h refuses (`approval_expired_during_run`, `clone_stale_during_run`); the form is closed by Cancel then Escape and counts as closed only when it is gone and "Add Account" is visible again, otherwise `form_left_open` and a printed instruction to close it yourself; `refusal_reason` comes from a fixed list (`unexpected_error` otherwise); `--revision` must match `[A-Za-z0-9_.:+-]{1,64}` (the `+` is allowed because Salesforce stamps end in `+0000`); only same-origin GET/HEAD and same-site (`pentera.io`) static assets (js, css, png, svg, woff, woff2, ico, json) are allowed through the guard; the underscore test seams of `production_create.run` work only when `SURFACE_ONBOARDING_TEST_HOOKS` is exactly `1` in the run's environment.

**Not verified on the real form (first dry run will show):** the Advanced section selectors and whether all nine controls exist there; that the page-context `fetch` carries the session (REFUTED live 2026-10-18: both searches answered 401, the page uses an in-page token header; replaced by the Dev technique of rewriting the page's own table request, which is itself not yet verified against production); the `getAllDetailedAccounts` sort/unique_fields/projection body (the search filter grammar is the Dev one, the rest follows the HAR); that the Cancel button and the date picker behave as in Dev; whether `pentera.io` (the form's user email domain) is itself a demo domain (only the customer's primary and Salesforce email domain are compared); and that the label of "Select Operator Accounts" is the placeholder text. The `account/add` response and the real create click remain out of scope (`click_confirm` raises `NotImplementedError`).

## 6.3 Running the production READ-ONLY readback (built 2026-10-16, not yet run)

Authority: the same probe-class gate as 6.1 (D4, docs/45). Read-only: it does not unlock the create or the dry run (`PRODUCTION_RUNNER_SUPPORTED` and `PRODUCTION_DRY_RUN_SUPPORTED` stay `False` and are neither read nor changed). Use it after a manual or later automated create to check ONE tenant against the CE-only contract in docs/49 section 3, and to compare its Account UUID with Salesforce.

**Command** (PowerShell, attended desktop, from the repo root):

```
$env:SURFACE_ONBOARDING_PRODUCTION_PROBE = "1"
$env:SURFACE_ONBOARDING_PRODUCTION_PROBE_REF = "PROBE-D4-20261016"   # ^[A-Z][A-Z0-9_-]{5,63}$
python tools/attended_ce_only_playwright.py --production-readback --co CO-0797
Remove-Item Env:SURFACE_ONBOARDING_PRODUCTION_PROBE, Env:SURFACE_ONBOARDING_PRODUCTION_PROBE_REF
```

**What it does:** (1) the gate and the exact origin `https://app.pentera.io`; (2) the fixed Salesforce read of the Dev runner (`ce_fill_source`: no new query) gives the intended CE-only values (from the Dev fill plan: tenant name ` - CE Only`, primary domain, internal user email domain) and the CO's `Account_UUID__c`, before any browser opens; (3) the same visible window of the profile `backoffice-production-automation`, the same sign-in wait as 6.1 (3 consecutive authenticated polls, 15 minutes; you sign in with SSO and MFA) and the same request guard (GET/HEAD and the two allow-listed read POSTs; any `account/add`, `account/edit` or other write is aborted, counted and refuses the run); (4) a service-worker check; (5) ONE search through the page's OWN `getAllDetailedAccounts` request (a reload; only `tableServerData` is rewritten: offset 0, 100 rows, `filters {and:[{method:"search", value:<name>}]}`, `unique_fields` name + domain, the compared fields as projection) and only the compared fields are kept; (6) the single row whose `accountName` AND `accountDomain` match exactly is compared with the contract (`production_contract.compare_readback`, type-sensitive: license numbers must be numbers 1/1/1 on the row) and its `accountUuid` is compared with Salesforce. It types nothing, clicks nothing, never opens the Add Account form and only reloads the page it is on.

**Printed line and file:** one JSON line `{"result":"production_readback_complete"|"production_readback_refused","reason":...,"tenant_found":bool,"mismatches":[field names],"uuid_ok":bool,"salesforce_uuid_matches":true|false|null,"production_write":"not_performed","salesforce_writeback":"not_performed"}` and the same content (plus schema, probe reference, CO reference, time, API call and blocked-request names, `clicks: 0`) in `production-probe/readback-<UTC stamp>.json` of the state folder (git-ignored). `complete` means one tenant was found and compared: read `mismatches` (empty means it matches docs/49); `uuid_ok` is true for a 32 lowercase-hex `accountUuid`; `salesforce_uuid_matches` is `null` when Salesforce has no UUID yet. Neither UUID, name, domain nor email is printed or stored; the writer refuses an unredacted report.

**Refusal reasons:** `gate_missing`, `gate_reference_invalid`, `invalid_co_reference`, `origin_not_approved`, `salesforce_read_failed`, `route_not_ce_only_new`, `intended_unavailable`, `sign_in_timeout`, `playwright_runtime_unavailable`, `browser_unavailable`, `service_worker_present`, `service_worker_check_failed`, `session_expired`, `search_unavailable`, `tenant_not_found`, `ambiguous_rows` (two distinct tenants, or more matches than rows), `name_only_match` (a same-name row with another domain exists), `write_request_blocked`, `report_unavailable`, `line_not_redacted`, `readback_crashed`. Rows that carry the same non-empty `accountUuid` and `id` count as one tenant; a duplicate name is never resolved by guessing.

**Limits (unverified until the first run):** the rewritten body's `unique_fields` (name + domain) and the explicit `projection` of the compared paths are assumptions the server may reject (a non-200 or odd reply refuses the run); the readback row paths for `reconLevel`, `scanningFrequency`, the eight flags and `accountCountryCode` follow the Dev validation paths and the create-response names, so a missing path shows up as a mismatch NAME, not a value; `accountCountryCode` is checked as an upper-case ISO-2 shape (no country-name mapping exists in the repo); license dates are not compared. The create BODY check (`production_contract.compare_payload`) exists for the future create path and is not used by this run.

## 6a. Out of scope for this proposal

Other onboarding cases in production, the VM runner (phase 2), unattended MFA, any change to the Dev flow, and any relaxation of an existing gate.
