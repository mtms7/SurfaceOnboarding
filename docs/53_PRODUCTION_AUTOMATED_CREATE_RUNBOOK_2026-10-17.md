# Production automated CE-only create: owner runbook (2026-10-17)

Status: built as LOCAL CODE + TESTS ONLY. `PRODUCTION_DRY_RUN_SUPPORTED` and `PRODUCTION_RUNNER_SUPPORTED` are both `False` in `integration/onboarding/session_readiness.py`. Nothing here ran against BackOffice, Salesforce, Redash or the VM. Unlocking and the live create for CO-0798 are separate owner approvals.

## What exists

| Piece | File |
|---|---|
| Live building blocks (one-create request guard, one-Confirm click log, confirm click, live duplicate verdict, bounded readback) | `integration/onboarding/production_create_live.py` |
| Orchestrator (new: required live duplicate check, `not_clicked`, contract-compare gate) | `integration/onboarding/production_create.py` |
| Runner: `--production-create --co CO --revision REV --owner-go` | `tools/attended_ce_only_playwright.py` (`run_production_create`) |
| Dashboard card, gates, typed confirmation, one-time tokens, UUID write | `integration/onboarding/production_create_ui.py` + small hooks in `tools/serve_attended_open_onboardings_dashboard.py` |
| Tests | `integration/tests/test_production_create_{live,ui,runner}.py` |

While either constant is `False`: the production CO pages contain no form, button or input; every POST to `/attended/production-create`, `/attended/production-uuid-review` and `/attended/production-uuid-confirm` is refused before anything is read or started; the runner flag refuses before any Salesforce read or browser. The VM never shows a control.

## What one click does (in this order)

1. Dashboard (desktop, you signed in as the approval issuer) re-reads the CO from Salesforce and re-judges every gate (below). One-time token, typed CO reference, authorization box.
2. Runner starts: dry-run constant, live target (runner constant, sessions flag, approval reference, exact origin), Salesforce source, Redash clone facts, ordered gate. You sign in with SSO and MFA in the production window if asked.
3. In the page: service worker check, session check, early live duplicate check, form fill with the docs/49 and sheet values (start date = day of creation, expiry = start + 1 year - 1 day capped at the subscription end), every control read back.
4. Final gate with a fresh clock, then the LIVE duplicate pre-check (BackOffice itself: exact name with " - CE Only", primary domain text search, demo-domain list; any match, ambiguity or error refuses), then the `attempt_started` record, then the approval is consumed, then ONE click on Confirm. The request guard lets exactly one `POST /api/v1/backoffice/account/add` through; a second is aborted and counted.
5. Readback by `getAllDetailedAccounts` (read only, up to 6 tries, 3 s apart), compared with docs/49 (`production_contract.compare_readback`): zero mismatches required, the accountUuid is extracted into the redacted record only (never printed).
6. Result word on the CO page. The UUID write to Salesforce is a separate step (D3) with its own button and confirmation.

## Owner steps and which need an explicit go

| # | Step | Go needed |
|---|---|---|
| 1 | Refresh the Redash clone (< 6 h at the moment of the click) | no |
| 2 | `python tools/issue_production_approval.py --co CO-0798` (typed `ISSUE CO-0798`; one CO, 7 days, consumed by the create) | YES (D1) |
| 3 | Reviewed commit flipping `PRODUCTION_DRY_RUN_SUPPORTED = True` (nothing else) | YES |
| 4 | Dry run: `python tools/attended_ce_only_playwright.py --production-dry-run --co CO-0798 --revision <REV>` with `SURFACE_ONBOARDING_PRODUCTION_SESSIONS=1` and `SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF=<ref>`; passes only if the record says `dry_run_passed` | YES |
| 5 | Reviewed commit flipping `PRODUCTION_RUNNER_SUPPORTED = True` (the dry-run constant must already be True) | YES |
| 6 | Start the dashboard on the desktop with the sessions flag, sign in (Salesforce SSO). Open `/onboard/CO-0798`: the "Create in production" card appears only if every gate passes | no |
| 7 | Type `CO-0798`, tick the authorization, click "Create in production". Sign in with MFA in the production window if asked and leave it open | YES (this click) |
| 8 | Read the result word on the CO page | no |
| 9 | If `created_verified`: "Write Account UUID to Salesforce", then "Confirm write to Salesforce" (field `Account_UUID__c` only, read-after-write) | YES (D3, one go per write) |
| 10 | Reviewed commit setting both constants back to `False` (relock) | YES |

The 24 h limit: the passing dry run must be at most 24 hours old when the card renders.

## Gates for the control (first refusal wins; all re-judged at POST)

Desktop not VM; both constants True; sessions flag and an unexpired approval reference; you are the approval issuer; a valid unused one-CO approval bound to the CURRENT Salesforce revision (`approval_revision_drift` otherwise); CE-only New route; Salesforce data fresh and Salesforce session ready; no Account UUID yet; clone fresh (< 6 h); a passing dry run for this CO and approval (24 h); no earlier attempt waiting for your review; no run already launched. The token is spent by the first POST whatever its outcome.

## Result states

| State | Meaning | What you do |
|---|---|---|
| `created_verified` | Tenant exists, readback matches docs/49 exactly | Step 9 (UUID write) |
| `created_mismatch` | A tenant exists but the readback differs (field names are in the record `readback_mismatches`) | Stop. Never create again. Open the record, decide an edit in BackOffice (D2) |
| `create_outcome_unknown` | The click may or may not have created a tenant: no/ambiguous response, readback never found the row, an interrupted attempt, or `attempt_started` with nothing after it | Check BackOffice by hand. The CO stays blocked until you review. Never retry blindly; a new create needs a new approval and your explicit clearing of the review |
| `confirm_not_clicked` (refusal) | Confirm was missing, disabled or covered: nothing was sent, but the approval was already consumed | Check BackOffice, then a new approval |
| refusals before the click (`duplicate_in_production`, `demo_domain_conflict`, `duplicate_check_unavailable`, `clone_stale`, `approval_revision_drift`, `dry_run_required`, ...) | Nothing was created and the approval is NOT consumed (consumption happens only after the live duplicate check) | Fix the named cause |

Records are append-only in `production-create-records/`; UUID write results are appended to `production-create-records/production-uuid-writes.jsonl` (the value is stored as a SHA-256 only). A recorded UUID write (even a rejected one) blocks another automatic write: check Salesforce first.

## Recovery (D2)

No delete or disable path is known. A wrongly created tenant is recorded (tenant name, account id, UUID, domains, plan, key form fields; no secrets) and YOU decide whether to rename or fix parameters by edit in BackOffice. Never re-click Create after an uncertain outcome.

## Residual risks

- The create HAR was observed once (CO-0797). The form-to-payload mapping and the readback grammar (`unique_fields` name + domain) are verified only by tests and the earlier read-only probe until the dry run and the first create.
- The Confirm "element under the pointer" check and the Dev helpers (`_locate_confirm_button`, `_confirm_enabled`) are reused from the Dev runner but have never run against production.
- A click can be sent and the response lost: that is `create_outcome_unknown` by design, not a retry.
- The approval is consumed before the click: a failure between consume and click costs a new approval.
- No delete path (D2). The domain duplicate check is a text search: a partial match refuses (false positives are accepted).
- The runner window can wait up to 15 minutes for MFA after you click; the gates are re-run with a fresh clock before the approval is consumed.
- The dashboard's "fresh Salesforce data" at page render is the short display read; the POST re-reads the CO from Salesforce.
- Same-second records order by rank in the status logic, but the older `create_records_by_co` landing chip still shows the last file name in a same-second tie.
