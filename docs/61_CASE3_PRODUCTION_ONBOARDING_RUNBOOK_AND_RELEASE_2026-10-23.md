# Case 3 production onboarding: owner runbook and release options (2026-10-23)

Case 3 = Surface & Credential Exposure, New Product Onboarding. Written from the code and the plan as they stand on `main` today. Sources: docs/53, docs/59, docs/60, plan section 18 ("Path B" entries), `integration/onboarding/case3_followup.py`, `integration/onboarding/production_create_ui.py`, `integration/onboarding/session_readiness.py`, `tools/issue_production_approval.py`, `tools/revoke_production_approval.py`, `COMMAND_TO_PASTE.md`.

**Status of this file.** Documentation only. Nothing was run, nothing external was contacted. Examples use CO-0802 and "Marketing Alliance Group". No IDs, tokens or customer data appear here.

**What is not in the files I read:** the result of the CO-0802 create. The plan's last entry is the G0 preflight and the review fixes. `COMMAND_TO_PASTE.md` (2026-10-22/23) says both constants are True on `main` and the create is waiting for the owner. Treat the CO-0802 outcome as unknown until the owner records it.

---

## 1. What the automated Case 3 onboarding does, from Approved to Onboarded

Legend: **AUTO** = the code does it. **OWNER** = your click, your typed words or your MFA. **MANUAL** = a person does it outside the automation.

| # | Step | Who | Detail |
|---|---|---|---|
| 0 | Prerequisites | OWNER | See the list below. |
| 1 | Short tenant name | OWNER | Only if the Salesforce account name is over 40 characters (docs/60). Confirm the short name on the CO page. It is stored in `integration/onboarding_tenant_names.json` (local, not in Git; moves if `SURFACE_ONBOARDING_STATE_DIR` is set). A name of 40 characters or fewer needs no confirmation. |
| 2 | Issue the approval | OWNER | `tools/issue_production_approval.py`. One CO, bound to the CO revision and to the exact tenant name. Typed phrase `ISSUE <CO>`. Seven days by default. |
| 3 | Unlock commit (dry run, then create) | OWNER | A reviewed commit sets the lock constants to True (section 3). |
| 4 | Start the dashboard with production sessions | OWNER | The lock constants are read at start. SSO sign-in to Salesforce. |
| 5 | Dry run | OWNER click, then AUTO | Dashboard button "Run production dry run" on the CO page. It signs in to BackOffice production (SSO + MFA by you), fills the real Add Account form, checks every control and cancels. It creates nothing and does not use up the approval. A pass is valid for a create for 24 hours. |
| 6 | Create | OWNER click, then AUTO | The card "Create in production" appears only if every gate passes. You type the CO reference, tick the authorization and click ONCE. You sign in with MFA in the window titled app.pentera.io. The runner re-checks Salesforce, runs the live duplicate check (name, main domain, CE e-mail domain), consumes the approval, clicks Confirm once, reads the tenant back and compares it with the Case 3 contract. |
| 7 | Result word | AUTO | `created_verified` or one of the stop words in section 2. |
| 8 | Review of the Salesforce write | OWNER click | After `created_verified` the card shows "Review the Salesforce write". The review writes nothing. It shows the two field names (`Surface_Account_ID__c`, `Account_UUID__c`), only the last four characters of each value, and the stage move Request Approved -> Account Scanning. |
| 9 | Confirm the write | OWNER typed + click, then AUTO | Type the CO reference, tick the authorization, click "Confirm write to Salesforce". The code writes the two IDs, reads them back, and only then makes ONE separate write: Request Approved -> Account Scanning. Each write has an intent line on disk first and is never repeated. The IDs come from the verified readback, never from the create response (it carries none). |
| 10 | Card says "Onboarded" | AUTO | Both writes read back as verified. |
| 11 | Operator account | MANUAL | The automated create leaves Operator Account empty. The Technical Advisor assigns the operator in BackOffice (CO-0802: the owner's notes say Ryan Slosser). The Technical Advisor -> operator mapping is not built. |
| 12 | Maximum scan Duration check | MANUAL | Open Edit Account in BackOffice and read the value. See section 4 (open item). |
| 13 | Next-day scan check | MANUAL | There is no automated Case 3 scan check. The scan runs for hours. Next day, read Duration Per Scan by hand, as for CO-0800. |
| 14 | Primary user and "User Created" | MANUAL | Create the primary user and set the User Created stage by hand. The automation stops at Account Scanning. Later stages stay manual. |

**Prerequisites (step 0):**
- CO is Approved (already reviewed by you; domains too), route Surface & Credential Exposure / New Product Onboarding, stage Request Approved.
- `Surface_Account_ID__c` and `Account_UUID__c` are both empty.
- One valid main domain and one CE e-mail domain.
- The tier is exactly Go or Prime. Go = Monthly, 500 subdomains. Prime = Weekly, 1000. Leaked Credentials runs Weekly for both.
- No subdomain add-ons on a Go CO (see section 4).
- Subscription End Date is present: it becomes the licence end.
- Pentera VPN on. Redash clone under 6 hours old at the click.
- Windows desktop (PC tab). The VM never shows a control.
- Pinned Salesforce alias `surface-onboarding` and the expected org Id (env variable or the launcher file). Missing means `salesforce_org_not_pinned`.
- No other live approval. Only one approval may be live at a time.

**Approval tools.**
- Issue: `tools/issue_production_approval.py --co CO-0802`. Use `--dry-run` first (shows the summary, writes nothing). `--status` lists the CO's approvals.
- Revoke: `tools/revoke_production_approval.py --approval-ref <ref>`. It retires ONE unused approval (typed `REVOKE <ref>`). It unlocks and creates nothing. Use it when a CO was created by hand or dropped. Example from the plan: CO-0800's approval was never consumed, so it blocked CO-0802 with `other_approval_active` until it was revoked.
- A changed CO revision or a changed tenant name makes the approval useless (`approval_revision_drift`, `approval_tenant_name_drift`). Issue a new one.

---

## 2. What blocks the create button, and what to do

### 2.1 Gate order (first refusal wins; all re-judged at the click)

1. Desktop, not the VM.
2. Both lock constants True.
3. A valid, unused, unexpired approval for this CO; you are its issuer; sessions flag set.
4. Approval bound to the current Salesforce revision.
5. CO route is CE-only New or Case 3 New.
6. Case 3: the tenant name is confirmed and the approval is bound to exactly that name.
7. Salesforce data fresh (15 minutes) and Salesforce session ready.
8. No Account UUID yet, and (Case 3) no Surface Account ID yet.
9. Redash clone under 6 hours.
10. A passing dry run for this CO and approval, under 24 hours.
11. No earlier attempt waiting for your review.
12. No run already in progress.

Then the typed CO reference and the authorization tick. The one-time token is spent by the first click, whatever the outcome.

### 2.2 Refusal codes on the page (from `GATE_TEXT`)

| Code | Meaning | What to do |
|---|---|---|
| `vm_read_only` | You are on the VM. | Use the PC tab. |
| `production_runner_not_supported` | Create lock is False in this build. | Owner decision: unlock (section 3). |
| `production_dry_run_not_supported` | Dry-run lock is False. | Same. |
| `production_locked` | Sessions flag or approval times missing. | Restart the dashboard with `-ProductionSessions`. |
| `operator_not_owner` | You are not the approval issuer. | Sign in as the issuer. |
| `approval_missing` / `approval_expired` / `approval_consumed` / `approval_superseded` / `approval_unreadable` | No usable approval. | Issue a new one. If another CO holds the live one, revoke it first. If consumed, do not reuse: check BackOffice. |
| `approval_revision_drift` | CO changed in Salesforce after the approval. | Issue a new approval. |
| `approval_tenant_name_drift` / `approval_tenant_name_missing` / `tenant_name_not_confirmed` | Name differs, absent or unconfirmed. | Confirm the name on the CO page, then issue a new approval. |
| `approval_route_mismatch` / `route_not_ce_only_new` | Wrong route or wrong approval type. | Check the CO's product and type. |
| `salesforce_not_fresh` | Salesforce data over 15 minutes. | Refresh the page. |
| `salesforce_session_not_ready` | Salesforce session not ready. | Sign in again (SSO). |
| `salesforce_prior_uuid_present` / `surface_account_id_present` | The CO already has an ID. | Check whether a tenant already exists. Do not create. |
| `clone_stale` | Clone over 6 hours or unusable. | Refresh the clone (VPN on). |
| `dry_run_required` / `dry_run_old` | No passing dry run, or over 24 hours. | Run the dry run again. |
| `prior_attempt_needs_review` | An earlier attempt waits for your review. | Check BackOffice by hand. The CO stays blocked. |
| `records_unreadable` / `gate_unreadable` | The page could not judge the gate. | Nothing is allowed. Paste the sentence to Claude. |
| `launch_in_progress` / `launch_failed` | A run is active, or the runner did not start (nothing created). | Wait, or paste the sentence to Claude. |
| `confirmation_invalid` | Token missing, expired or used. | Reload the page. Do not resubmit from the browser history. |
| `typed_reference_mismatch` / `authorization_missing` | Typed CO reference wrong, or box not ticked. | The token is spent: reload and start again. |

Runner refusals before the click (for example `duplicate_in_production`, `demo_domain_conflict`, `duplicate_check_unavailable`, `case3_tier_values_mismatch`, `salesforce_org_not_pinned`, `form_changed_during_checks`, `advanced_mismatch`): nothing was created. The approval is NOT consumed when the refusal comes before the consume step (docs/53). Fix the named cause. If in doubt, paste the card to Claude.

### 2.3 Result words after a create click

| Word | Meaning | What you do |
|---|---|---|
| `created_verified` ("Created and verified") | Tenant exists, readback and create body match the Case 3 contract. | Step 8: review the Salesforce write. |
| `created_mismatch` ("Created, readback differs: stop") | A tenant exists but the readback or body differs. Field names are in the record. | Stop. Never create again. Paste the card to Claude. You decide any edit in BackOffice. |
| `create_outcome_unknown` (record `created_unverified` with reason `readback_unavailable`, `uncertain_submit`, or `attempt_started` with nothing after) | The click may or may not have created a tenant. | Check BackOffice by hand. The CO stays blocked until you have reviewed it. |
| `confirm_not_clicked` | Confirm could not be used. Nothing was sent. The approval was already consumed. | Check BackOffice. A new approval and a new dry run are needed. |

**Rule: never click twice. After any result that is not `created_verified`, never create by hand and never retry.** A hand-made tenant after an uncertain click can duplicate the account.

### 2.4 Result words of the Salesforce write (step 9)

| Result | Meaning | What you do |
|---|---|---|
| `completed` | IDs and stage written and read back. | Manual steps 11-14. |
| `stopped` (ids `write_rejected` / `write_uncertain`) | Salesforce refused the ID write, or the result is unclear. The stage is skipped. | Check the CO in Salesforce. The page never repeats a write. Paste the result to Claude. |
| `stopped` (stage `write_rejected` / `write_uncertain`) | IDs are written, the stage move failed or is unclear. | Check the stage in Salesforce. Do not click again. |
| `tenant_not_verified` | No verified create record for this CO. | Nothing is written. |
| `different_value` | A field already holds another value. | Nothing is overwritten. Check Salesforce. |
| `ids_write_already_recorded` / `ids_write_needs_review` / `stage_write_needs_review` | An earlier write is recorded or unclear. | Check Salesforce by hand. No retry. |
| `revision_changed` | CO changed since the review. | Review again. |
| `already_complete` | IDs and stage are already there. | Nothing to do. |
| `record_write_failed` | The result could not be saved. | Check Salesforce. Further writes stay blocked. |

Deleting `case3-followup-writes.jsonl` would reset the no-retry memory (plan, review (f)). Do not delete it.

---

## 3. Lock policy and release options

### 3.1 The two constants

Both are hard-coded in `integration/onboarding/session_readiness.py`. No environment value or approval can open them.
- `PRODUCTION_DRY_RUN_SUPPORTED`: opens only the production dry run (fill the form and cancel). It does not open the create.
- `PRODUCTION_RUNNER_SUPPORTED`: opens the create path and the guarded follow-up writes. The follow-up (`case3_followup`) needs BOTH True.

With either False, the production CO pages show no form, button or input and every production POST is refused before any read.

### 3.2 How the unlocks and relocks worked

Rule used each time: a reviewed unlock commit, one attended attempt, then a relock by `git revert`. The operator pushes.

| CO | Unlock commits | Relock commits | Outcome in the commit history |
|---|---|---|---|
| CO-0798 (CE-only), first window | `853cf99` (dry run), `1b98591` (create) | `52b8347` (reverts both; the create waited for the VM path) | The first live attempt refused `production_session_not_ready` before the form. No tenant. |
| CO-0798, second window | `c95112a` (reverts the relock `52b8347`, create unlocked) | `d450984` (reverts `c95112a`) | CO-0798 onboarded (memory note; plan handoff `599413e`). |
| CO-0800 (Case 3) | `43eb6e2` (dry run only) | `26ca72c` (reverts `43eb6e2`) | The create stayed locked. The owner created CO-0800 by hand (path A). |
| CO-0802 (Case 3) | `f18821a` (dry run), `02191d1` (create) | Not yet on `main`. Planned: `git revert --no-edit 02191d1` then `git revert --no-edit f18821a`. | Create outcome not recorded in the files I read. |

### 3.3 Two release options (owner decides)

**Option A: keep both constants True permanently in `main`.**
Each create still needs all of this:
- a fresh one-CO approval bound to the revision and the tenant name;
- a passing dry run under 24 hours;
- a clone under 6 hours ("fresh clone");
- the typed CO reference and the authorization tick;
- your SSO + MFA in the BackOffice window, on the desktop, as the approval issuer.

Benefits:
- No unlock and relock commits for each CO. Fewer commits and fewer chances to leave a lock in the wrong state.
- The path is the same every time.

Risks, stated plainly:
- The lock stops being a separate decision. The only things between a CO and a production create are the approval and your own clicks.
- The dashboard would offer the production create button whenever a CO has a valid approval and every other gate passes. The dry-run button would be offered on every CO that has a valid approval.
- Any future code change that weakens a gate would ship in a build that is already unlocked. Today a weak gate in a locked build cannot be reached.
- The follow-up writes to Salesforce (IDs and stage) are also reachable at all times for a CO with a verified record. A verified record may come from the read-only `--production-readback` (plan, review (f), noted not changed).
- The tests change. Today most tests written for the locked build run with the create lock patched to False through `integration/tests/locked_build.py` (`locked_create_build`). `integration/tests/test_unlock_state.py` pins the unlocked state and the exact reviewed lines. A permanent release means deleting `locked_build.py`, removing its patch from the test modules that use it (commit `02191d1` touched more than a dozen), and replacing `test_unlock_state.py` with permanent tests that pin True. The old "locked build shows nothing" tests would need a new way to prove the locked paths, for example by patching in the tests only.
- If the owner is away, anyone with the desktop session, the SSO login and an unexpired approval can start a create. The approval lifetime is up to 7 days by default (31 maximum).

**Option B: unlock only per CO, relock after.**
Benefits:
- The default build cannot create or write. A mistake, a stray click or a bad change cannot reach production.
- Each unlock is a reviewed commit that names the CO and the approval.
- Tests keep proving the locked build.

Costs:
- Two commits to unlock and two reverts to relock for every CO.
- A forgotten relock leaves Option A's risks in place without the review. CO-0802's relock is still open today.
- The dashboard must be restarted after each change.
- More owner steps per CO.

No recommendation is made here. Decision needed from the owner: A or B. If A, ask Claude for a reviewed commit that makes the state permanent and replaces the locked-build tests. If B, keep `locked_build.py` and `test_unlock_state.py` and follow the checklist in section 5.

---

## 4. Known limitations and open items

Each item below is from the code or the plan. Nothing here is guessed.

1. **Operator account is empty at create.** The Technical Advisor -> BackOffice operator mapping is not built (plan, path B). The Technical Advisor assigns the operator by hand after the create.
2. **No automated Case 3 scan check.** The CE scan check is built for CE-only. For Case 3 the owner reads Duration Per Scan by hand the next day. The executions UI read failed on CO-0800 (plan, 2026-10-21) and was not fixed.
3. **Maximum scan Duration: OPEN.** The runner types 90 (`SURFACE_MAX_SCAN_DURATION_HOURS = "90"`) and the create body carried 90 (`campaignsTimeoutInHours`, docs/59: 90, form default 24). The owner saw 24 in BackOffice's Edit Account for CO-0802. The root cause is under investigation. It is not known whether the value was not stored, was reset, or is shown from another field. The readback does not compare this field (row path unverified). Until it is closed, check this value by hand after every create (step 12).
4. **Subdomain add-ons make a Go CO refuse.** The typed subdomain count includes add-ons, and the builder knows only the tier base (500 Go, 1000 Prime). A mismatch refuses `case3_tier_values_mismatch` before the click. This is deliberately conservative.
5. **A mismatch after the click gives `created_unverified`.** The create body and the readback are compared only after the click (same as CE-only). The approval is already consumed. The tenant exists. Do not create again.
6. **The approval is consumed before the click.** A failure between the consume and the click costs a new approval and a new dry run.
7. **No delete or disable path** for a wrongly created tenant (D2). You decide any rename or edit in BackOffice.
8. **Long names.** A short name is needed above 40 characters. The confirmed short-name file is `integration/onboarding_tenant_names.json` (one local record per CO: confirmed name, a hash of the account name, operator, time). A corrupt or unreadable file refuses (`tenant_names_unreadable`). A changed Salesforce account name invalidates the record. The file is not in Git and not backed up by the repository. CO-0802 has a 24-character name, so it needs no confirmation.
9. **Static outbound IP** stays OFF. The owner turns it on by hand when asked (docs/59).
10. **Manual steps after the automation:** operator account, Maximum scan Duration check, next-day scan check, primary user creation, User Created stage.
11. **Follow-up reads any verified Case 3 record of the CO**, including one from the read-only readback. This was noted in review (f) and not changed.
12. **Same-user trust limit.** Anyone running as the same Windows user can edit the approval file or mint a launch ticket (docs/53). Keep the desktop session to yourself while a run is active.
13. **Docs/53 is the CE-only runbook.** Its text says the constants are False. That was true when it was written. For the current state read section 3 here.

---

## 5. Release checklist for the NEXT Case 3 CO (PC tab)

Run every command in a PowerShell PC tab at the repo root. Paths use forward slashes. Replace `CO-0802` and `<REV>` as needed. Never paste passwords, MFA codes or tokens into chat.

Note: the current `COMMAND_TO_PASTE.md` does not contain the three environment variables below. The values are the ones the code and the dashboard launcher use: the issuer list is the owner address from the launcher default, the target org is the launcher default alias, and the org Id is read from the launcher file so it is never typed or shown.

**A. Before you start**
- [ ] CO is Approved, Case 3 New, Request Approved, both ID fields empty (CO page shows it).
- [ ] Tier is Go or Prime. No subdomain add-ons on a Go CO.
- [ ] Account name over 40 characters? Confirm the short name on the CO page first.
- [ ] No other live approval. If one exists and is unused, revoke it (step C3).
- [ ] Pentera VPN on.

**B. Environment (PC tab, once per tab)**

    cd "C:/Users/Milton Stevenson/Documents/Surface/Workato"
    $env:SURFACE_ONBOARDING_APPROVAL_ISSUERS = "milton.stevenson@pentera.io"
    $env:SURFACE_SF_TARGET_ORG = "surface-onboarding"
    $env:SURFACE_SF_EXPECTED_ORG_ID = (Get-Content -LiteralPath "$env:LOCALAPPDATA/SurfaceOnboarding/salesforce-expected-org-id.txt" -TotalCount 1).Trim()

- [ ] Environment set in this tab.

**C. Approval**
1. [ ] Look first (writes nothing):

       python tools/issue_production_approval.py --co CO-0802 --status
       python tools/issue_production_approval.py --co CO-0802 --dry-run

2. [ ] Check CO, route, tenant name and expiry in the summary. Issue it (interactive; type `ISSUE CO-0802`):

       python tools/issue_production_approval.py --co CO-0802

3. [ ] Only if an unused approval of another CO blocks you (`other_approval_active`). Look, then retire it (type `REVOKE <ref>`):

       python tools/revoke_production_approval.py --approval-ref <ref> --dry-run
       python tools/revoke_production_approval.py --approval-ref <ref>

**D. Unlock (Option B only; skip if Option A is chosen and merged)**
- [ ] Reviewed commit sets `PRODUCTION_DRY_RUN_SUPPORTED = True` (names the CO and the approval).
- [ ] Reviewed commit sets `PRODUCTION_RUNNER_SUPPORTED = True` (after a passing dry run).
- [ ] Full suite ran OK on the unlocked tree. You push, not Claude.

**E. Dashboard and dry run**
1. [ ] Restart the dashboard with production sessions (constants are read at start):

       powershell -File tools/start_attended_dashboard.ps1 -Restart -ProductionSessions -PythonPath .venv-renewal-playwright/Scripts/python.exe

2. [ ] Open `http://127.0.0.1:8012/co/CO-0802?env=prod`. Sign in with Salesforce SSO + MFA if asked.
3. [ ] Click "Run production dry run". Sign in to BackOffice (SSO + MFA) in the window titled app.pentera.io. Leave that window alone.
4. [ ] Result is `dry_run_passed`. Note the time: the create is allowed for 24 hours after it.

**F. Create (your one click)**
- [ ] Clone under 6 hours at the click (refresh with VPN on if the card says `clone_stale`).
- [ ] Card "Create in production" shows the exact tenant name. If it says "Blocked", read section 2.2.
- [ ] Remember: Operator Account stays empty.
- [ ] Type `CO-0802`, tick the authorization, click "Create in production" ONCE. Sign in with MFA if asked. Watch the progress bar.
- [ ] Result is `created_verified`. Anything else: STOP. No second click, no hand-made tenant, paste the card to Claude (section 2.3).

**G. Salesforce write (your confirmation)**
- [ ] Click "Review the Salesforce write". Check the two field names, the last four characters and the stage move.
- [ ] Type `CO-0802`, tick the authorization, click "Confirm write to Salesforce". Wait for "Onboarded". If it stops, do not click again (section 2.4).

**H. Manual steps in BackOffice**
- [ ] Technical Advisor assigns the Operator Account.
- [ ] Open Edit Account and read Maximum scan Duration. Note the value (open item 3). Expected 90.
- [ ] Next day: read Duration Per Scan and the last scan state.
- [ ] Create the primary user. Set the User Created stage in Salesforce.

**I. Relock (Option B only)** (PC tab; you push)

    git revert --no-edit 02191d1
    git revert --no-edit f18821a

- [ ] Replace the two hashes with the unlock commits of this CO. Revert the create unlock first, then the dry-run unlock.
- [ ] Restart the dashboard without `-ProductionSessions`:

       powershell -File tools/start_attended_dashboard.ps1 -Restart -PythonPath .venv-renewal-playwright/Scripts/python.exe

- [ ] Open the CO page: it shows no create button.
- [ ] Record the result and the date in plan section 18.
