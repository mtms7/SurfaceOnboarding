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

## 6a. Out of scope for this proposal

Other onboarding cases in production, the VM runner (phase 2), unattended MFA, any change to the Dev flow, and any relaxation of an existing gate.
