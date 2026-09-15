# Phase 2 Leonardo Safe Execution Handoff

Date: 2026-08-24  
Owner: Milton Stevenson  
Target for this phase: Leonardo Development (`https://leonardo.dev.app.pentera.io/`)  
Status: Local design only; no Leonardo integration, authentication, request, or account creation has been authorized or performed.

## Executive decision

The fastest safe path for this week is an **approval-driven, attended Leonardo Development workflow**:

1. Salesforce remains read-only and supplies the Customer Onboarding source record.
2. The existing Workato queue, OPA validation, and manual CSE approval gate prepare an immutable, masked execution manifest.
3. An authorized operator signs in to Leonardo Development with MFA.
4. Initially, the operator creates the synthetic account manually from the approved manifest. If the Leonardo owner and SecOps separately approve browser automation, Playwright may fill the form in a dedicated, isolated runner and must pause before `Confirm`.
5. The authorized human reviews the complete form and performs the one approved submission.
6. A separate read-only verification reopens or searches for the created account, captures its stable UUID, and compares all approved fields and toggles.
7. Any Salesforce writeback remains a separate future action requiring explicit authorization.

Do **not** install Playwright, Chromium, or a reusable browser profile on `workato-opa-01` (`172.26.37.20`) as the default implementation. That host is the minimal hardened Workato OPA/validator host. A browser runtime would materially expand its attack surface and would require a separate SecOps architecture approval.

The target architecture should still be an owner-supported Leonardo service contract. The attended workflow is a controlled bridge, not the final production integration.

## What is verified

- Workato has a dashboard-safe queue view and a restricted private table. Salesforce source fields and private onboarding content are not exposed in the public dashboard-safe view.
- Queue synchronization and reconciliation remain inactive except for the previously authorized, limited tests documented in the Phase 1/queue handoffs.
- The OPA notes validator is healthy on local port `8788` and Phase 1 sanitization tests passed.
- The Workflow App has a blank manual CSE approval table, `surface_onboarding_cse_approval_gate_v1` (table `139401`).
- `Surface Onboarding Leonardo Approval Gate (Inactive)` (recipe `75088933`) is deliberately fail-closed.
- `Surface Onboarding CSE Decision Recorder (Inactive)` (recipe `75430449`) is only a trigger scaffold and has no action steps.
- The current pilot access group contains only Milton. It does not provide requester/approver separation and must not authorize a Leonardo action.
- Read-only Leonardo discovery inventoried the Add Account fields, license inputs, attack modules, settings, duplicate-search UI, and Audits view. Several security-relevant form controls have visible defaults, so every toggle must be mapped and verified explicitly.
- The existing `outputs/vcenterrdp.pentera.rnd.har` file is not a Leonardo capture. A sanitized local inspection found 90 entries, zero requests to `leonardo.dev.app.pentera.io`, and zero matching `/api/v1/backoffice`, `/api/v1/auth`, or `/api/v1/authenticated` paths.

## What is not yet verified

- No supported Leonardo API or automation contract has been supplied.
- The URL `/api/v1/backoffice/getAllDetailedAccounts` names a detailed-account search/list operation. The URL alone does not establish the method, request schema, authentication model, permissions, rate limit, stability, or support status, and it is not a creation operation.
- Historical Donatello observations such as `/api/v1/backoffice/account/add` do not establish a Leonardo contract and must not be called or copied into production design without Leonardo-owner approval.
- Leonardo's duplicate rules for alternate domains, email domains, subdomains, networks, and operator accounts are unresolved.
- The reliable post-create UUID/readback method is unresolved.
- Leonardo Audits displayed zero rows during discovery; the owner must clarify whether that is normal data state or a permission limitation.
- No synthetic Leonardo Development create has been authorized.

## Why HAR replay is not the integration

A HAR can contain request and response headers, cookies, bodies, tokens, customer data, and timing information. It can help an authorized engineer document candidate methods, paths, and field names after redaction, or mock a local test. It must not be used to replay a privileged browser session or infer a supported integration.

If a future Leonardo HAR is authorized for discovery:

- capture only in Leonardo Development with a synthetic account;
- exclude login, MFA, token refresh, cookies, authorization headers, contacts, domains, comments, and attachments;
- retain only method, normalized path, status class, content type, and field names;
- never commit the raw HAR or copy it into Workato, chat, tickets, logs, or the OPA VM;
- delete the raw capture according to the approved evidence-retention policy;
- require Leonardo owner validation before using any observed private endpoint.

Playwright supports HAR files primarily to mock network traffic in tests. That capability does not turn a browser's private endpoint into a supported service API.

## Recommended architecture

```text
Salesforce Customer Onboarding (read-only)
                   |
                   v
Workato restricted queue + dashboard-safe projection
                   |
                   v
OPA Phase 1 validator (fail closed)
                   |
                   v
Manual CSE validation and approval gate
                   |
                   v
Immutable, masked execution manifest + idempotency lock
                   |
                   +------------------------------+
                   |                              |
             This-week bridge              Target architecture
                   |                              |
      Attended manual/Playwright          Supported Leonardo service
      on separate hardened runner         with scoped machine identity
                   |                              |
                   +---------------+--------------+
                                   v
                       Leonardo Development only
                                   |
                                   v
                 Independent read-only verification
                                   |
                                   v
                    Workato result/evidence record
                                   |
                          [separate approval]
                                   v
                     Salesforce UUID writeback
```

### Executor boundary

For an approved Playwright bridge, use a separate, hardened, ephemeral automation runner or an approved administrator workstation. Do not make the OPA VM the browser runner.

Minimum runner controls:

- exact destination allowlist for `https://leonardo.dev.app.pentera.io/`; production hostname hard-blocked;
- no public inbound access; EDR, patching, restricted administrator access, and outbound filtering;
- a fresh, non-persistent browser context per run and disposal immediately afterward;
- operator completes MFA interactively for attended runs;
- no saved password, TOTP seed, MFA code, bearer token, cookie, local/session storage, or reusable browser profile;
- no TLS bypass, WAF bypass, CAPTCHA bypass, or origin-changing redirect;
- no HAR, trace, video, page HTML, or screenshot retention by default; any approved screenshot must be masked before retention;
- short execution timeout, one job at a time, and an explicit kill switch;
- structured redacted logs using only correlation IDs and outcome categories.

If Leonardo later supplies an approved service identity, it must be independently scoped to Development, use least privilege, rotate through an approved secret manager, and must not weaken MFA on human accounts.

## Workato Phase 2 component map

All new recipes remain inactive until a separately authorized test.

1. **Surface Onboarding CSE Decision Recorder (Inactive)** — finish the existing scaffold so an approved Workflow App decision can update only the approval-control row. A requester cannot set approver identity, approval status, expiry, policy version, or `leonardo_request_allowed`.
2. **Surface Onboarding Leonardo Approval Gate (Inactive)** — read exactly one approval row and return `proceed=false` unless source key, CO number, source revision, approver group, CSE decision, policy version, approval time, and expiry all match.
3. **Surface Onboarding Leonardo Manifest Builder (Inactive)** — combine only validated source values with explicit defaults/toggles and create a versioned execution manifest. It must not call Leonardo.
4. **Surface Onboarding Leonardo Preflight (Inactive)** — initially a manual/read-only function that records duplicate-search and access-readiness evidence. If a supported read endpoint is later approved, the connector belongs here.
5. **Surface Onboarding Leonardo Execution Dispatcher (Inactive)** — future component that releases one approved manifest to the dedicated executor. It must reject production, expired approvals, duplicate or uncertain prior attempts, and unsupported schemas.
6. **Surface Onboarding Leonardo Result Reconciliation (Inactive)** — accept only a signed/verified result, compare every requested field and toggle against an independent readback, and record the Leonardo UUID and verification state.
7. **Surface Onboarding Salesforce UUID Writeback (Inactive)** — future, separately approved action. It must never be coupled to the create click and must run only after complete read-after-write verification.

The existing dashboard-safe Workflow App remains read-only. Private comments, contacts, domains, attachments, and raw manifests remain in restricted storage and must not be copied into the safe view.

## Execution manifest contract

Use JSON Schema or an equivalent strict Workato schema. Reject missing, extra, null, ambiguous, or type-mismatched fields.

```json
{
  "contract_version": "leonardo-dev-create-v1",
  "target_environment": "leonardo-development",
  "correlation_id": "opaque-job-id",
  "idempotency_key": "sha256(target|sf_record_id|source_revision|engine_value)",
  "source": {
    "sf_record_id": "Salesforce record ID",
    "co_number": "CO number",
    "source_revision": "immutable revision"
  },
  "routing": {
    "engine_value": "one approved Cases 1-6 engine value",
    "policy_version": "approved policy version"
  },
  "approval": {
    "request_key": "approval request key",
    "approved_by": "verified Workato identity",
    "approved_at": "UTC timestamp",
    "expires_at": "UTC timestamp",
    "operation": "fill_and_pause",
    "manifest_sha256": "hash of the canonical reviewed manifest"
  },
  "account": {
    "company_name": "restricted value",
    "account_type": "Customer or Demo",
    "primary_domain": "restricted value",
    "alternate_domains": [],
    "subdomains": [],
    "email_domains": [],
    "networks": [],
    "country": "approved enum"
  },
  "primary_user": {
    "first_name": "restricted value",
    "last_name": "restricted value",
    "email": "restricted value",
    "phone": "restricted value",
    "job_title": "restricted value",
    "mfa_enabled": true,
    "operator_account": "owner-approved reference"
  },
  "settings": {
    "scanning_interval": "approved enum",
    "scan_now": false,
    "maximum_scan_duration_hours": 24,
    "automated_discovery": false,
    "recon_subdomains": false,
    "multiple_attack_stacks": false,
    "mas_for_subdomains": false,
    "web_dictionary_brute_force": false,
    "web_dorking": false,
    "nuclei": false,
    "authenticated_testing": false,
    "static_outbound_ip": false,
    "ai": false,
    "notifications": false,
    "multiple_users": false,
    "api_access": false
  },
  "attack_modules": {
    "phishing": false,
    "leaked_credentials": false,
    "leaked_credentials_interval": "None",
    "leaked_credentials_domains": [],
    "spycloud": "owner-confirmed"
  },
  "license": {
    "include_provisioning": false,
    "include_subdomains": false,
    "type": "approved Leonardo enum",
    "number_of_assets": 0,
    "number_of_domains": 0,
    "number_of_subdomains": 0,
    "start_date": "YYYY-MM-DD",
    "expiration_date": "YYYY-MM-DD"
  }
}
```

The Boolean values above are schema examples, not business defaults. The authoritative Cases 1-6/Guru mapping must set each value. The runner must never inherit a visible Leonardo default silently.

The first deployed contract permits only `validate_only`, `read_only_preflight`, `fill_and_pause`, and `read_only_verify`. It contains no unattended `create` operation. Changing any reviewed field changes `manifest_sha256` and requires a new approval.

### Result contract

```json
{
  "contract_version": "leonardo-dev-result-v1",
  "correlation_id": "same opaque job ID",
  "idempotency_key": "same deterministic key",
  "target_environment": "leonardo-development",
  "outcome": "verified|blocked|failed|uncertain",
  "leonardo_account_uuid": "present only after verified readback",
  "verification": {
    "method": "owner-approved read-only UI or service lookup",
    "all_fields_match": true,
    "all_settings_match": true,
    "verified_at": "UTC timestamp"
  },
  "error": {
    "category": "redacted category only",
    "retry_allowed": false
  }
}
```

Do not include the raw request, raw response, page HTML, authentication material, contact details, domains, comments, or attachments in operational logs.

## State machine and idempotency

Allowed states:

```text
queued
  -> validation_blocked
  -> awaiting_cse_approval
  -> approval_denied_or_expired
  -> approved_for_preflight
  -> duplicate_blocked
  -> ready_for_attended_execution
  -> submitted
  -> verification_pending
  -> verified
  -> writeback_pending
  -> complete
```

Any connection loss, timeout, unexpected response, missing success UUID, or partial page transition after submission becomes `uncertain`. An uncertain attempt must not retry automatically. It requires a read-only reconciliation by exact name/domain/UUID and an owner decision.

Before execution, acquire a single-flight lock on the deterministic idempotency key. Its inputs must include the target environment, Salesforce record/revision, case engine, approval revision, and canonical manifest hash. Block if a prior record for that key is pending, verified, or uncertain. Exact account-name and primary-domain duplicate checks are mandatory; the remaining collision rules require Leonardo-owner approval.

## Browser workflow if separately approved

1. Validate the manifest schema and signature/hash locally.
2. Assert `target_environment == leonardo-development` and exact origin.
3. Open a fresh non-persistent browser context.
4. Let the authorized operator complete login and MFA. Do not capture or persist authentication state.
5. Run a read-only duplicate check before opening Add Account.
6. Assert page identity, tenant/environment banner, expected labels, allowed enumerations, and control count.
7. Fill every field and explicitly set every toggle from the manifest using stable label/role locators.
8. Re-read the visible form and compare it with the manifest.
9. Present a masked summary and pause before `Confirm`.
10. The human approver performs the one authorized confirmation.
11. Do not retry after any ambiguous submit result.
12. Perform an independent read-only lookup/reopen and compare the UUID, dates, quantities, domains, user data, modules, and every security-relevant setting.
13. Dispose of the browser context and emit only the redacted result contract.

Early Development tests must remain attended. Headless unattended creation is out of scope until the supported integration, machine identity, audit trail, and recovery model are approved.

## Fail-closed stop conditions

Stop without submission if any of the following occurs:

- missing, denied, expired, duplicated, self-approved, or mismatched approval;
- requester and approver are the same person during the controlled test;
- unsupported Case 1-6 route or incomplete Guru mapping;
- Salesforce/DealHub mismatch, ambiguous Onboarding Comments, or invalid dates/quantities;
- duplicate or collision result;
- existing pending, verified, or uncertain idempotency record;
- wrong hostname, environment, tenant, page, account type, or operator;
- 401, 403, 409, or 429 response, WAF challenge, CAPTCHA, unexpected login/MFA prompt, or redirect outside the allowlist;
- changed/missing field, enum, selector, default, CSRF behavior, or response schema;
- inability to verify Audits/UUID/readback;
- partial form or partial submit result;
- any attempt to disable MFA, use production, inspect browser private data, or retain raw secrets/payloads.

The global blocked message should remain:

> Manual CSE validation is required because this is a special CO or one or more source values do not match the approved onboarding parameters. No Leonardo creation was performed.

## Logging and evidence

Allowed evidence fields:

- CO number and Salesforce record ID;
- Workato job ID, runner job ID, and correlation ID;
- engine value, policy version, contract version, source revision, and idempotency key;
- approval identity, decision, UTC timestamp, and expiry;
- action name, target environment, status, error category, and timing;
- verified Leonardo UUID;
- optional salted domain fingerprint if the Security/Data owner approves it.

Forbidden evidence fields:

- passwords, MFA codes/seeds, cookies, auth/CSRF headers, bearer tokens, browser storage, saved profiles;
- raw HARs, request/response bodies, query strings, page HTML, traces, videos, or unmasked screenshots;
- Onboarding Comments, contacts, domains, attachments, credentials, or document contents.

## This-week implementation plan

### Day 1 — contract and ownership

- Complete the sanitized Cases 1-6 field/toggle/license mapping from verified Guru guidance.
- Ask the Leonardo owner for a supported service contract and confirm whether attended browser automation is permitted in Development.
- Name separate requester and CSE approver groups; the existing single-user pilot remains non-authoritative.
- Define duplicate/collision rules, UUID readback, Audits visibility, and synthetic fixture cleanup/retention.

Exit: owner decisions recorded; no external mutation.

### Day 2 — local runner proof

- Scaffold a local `phase2_leonardo` package with strict request/result schemas, redaction, origin allowlisting, idempotency, and mock HTML fixtures.
- Test every Cases 1-6 mapping against fixtures without opening Leonardo.
- Test stop conditions, selector/schema drift, duplicate, expired approval, and uncertain outcome behavior.

Exit: local tests pass; no credentials or external requests.

### Day 3 — inactive Workato orchestration

- Finish the inactive CSE decision-recorder mapping with synthetic Workato-only data after separate authorization.
- Add the inactive manifest builder and preflight scaffolds.
- Verify all deny paths return `proceed=false` and the dashboard-safe table remains free of private data.

Exit: inactive recipes only; Salesforce and Leonardo unchanged.

### Day 4 — owner/security review

- Review runner placement, MFA handling, redaction, network allowlist, approvals, recovery, and test fixture with SecOps and Leonardo owner.
- If approved, perform one read-only Leonardo Development dry run that navigates, searches the synthetic key, fills nothing, submits nothing, and retains no session artifact.

Exit: written go/no-go for one synthetic Development create.

### Day 5 — separately authorized synthetic Development create

- Obtain explicit one-run creation authorization immediately before the test.
- Ensure requester and approver are different people.
- Use the approved manifest and attended manual or Playwright-assisted form filling.
- Human performs the single confirmation.
- Read back the UUID and every field/toggle independently; record redacted evidence.
- Do not write Salesforce and do not access production.

Exit: verified synthetic result or an `uncertain/blocked` record with no automatic retry.

## Required approvals and blockers

| Blocker | Owner decision required |
|---|---|
| No supported Leonardo integration contract | Leonardo Engineering/Product owner must provide/approve a versioned service interface, or explicitly approve attended UI automation in Development. |
| Browser runner location | SecOps must approve the isolated runner architecture. Adding it to `172.26.37.20` requires a separate exception and is not recommended. |
| Single-user approval group | Workato owner and CSE management must create and verify separate requester/approver groups and membership. |
| Incomplete Cases 1-6 toggle mapping | CSE process owner must approve the authoritative Guru-derived mapping and every security/license default. |
| Duplicate and collision semantics | Leonardo owner must define exact rules for all domain, network, user, operator, and account collisions. |
| UUID/readback and zero-row Audits | Leonardo owner must confirm a reliable read-only verification method and permissions. |
| Synthetic record and cleanup | Leonardo owner must approve the fixture, retention/disable cleanup, and cleanup operator. Automation must not delete. |
| Salesforce destination field/writeback | Salesforce owner must approve the destination and a separate later writeback test. |
| One Development create | Automation owner, separate CSE approver, Leonardo owner, and SecOps must explicitly authorize the single run. |

## Supported-service request to send Leonardo Engineering

Request a documented, Development-scoped interface with:

- versioned `search`, `create`, and `get by UUID` operations;
- stable request/response schemas and explicit error contract;
- scoped non-human identity or another approved machine-authentication model;
- idempotency-key and correlation-ID support;
- tenant/environment binding and production hard separation;
- duplicate/conflict behavior and rate limits;
- audit-event coverage and read-after-write consistency guarantees;
- security-relevant field/default semantics;
- owner, support policy, change notice, and deprecation policy.

Until that exists, private browser endpoints remain implementation details and must not be called directly by Workato or the OPA validator.

## Proposed local package layout

```text
phase2_leonardo/
  README.md
  contracts/
    create-request.schema.json
    create-result.schema.json
  mappings/
    cases-1-6.yaml
    leonardo-fields.yaml
  runner/
    origin-policy.ts
    manifest-validator.ts
    redaction.ts
    idempotency.ts
    selectors.ts
    attended-create.ts
    readonly-verify.ts
  fixtures/
    synthetic-manifests/
    mock-pages/
  tests/
    contract.spec.ts
    mapping.spec.ts
    security.spec.ts
    attended-create.spec.ts
    readonly-verify.spec.ts
```

This package should first use local mock pages. Installing dependencies or connecting it to Leonardo requires separate authorization.

## Sources and related local evidence

- `README.md`
- `docs/03_WORKATO_RECIPE_ARCHITECTURE.md`
- `docs/04_SECURITY_AND_RISK_REVIEW.md`
- `docs/05_TEST_PLAN_AND_ACCEPTANCE.md`
- `docs/08_TECHNICAL_IMPLEMENTATION_GUIDE.md`
- `docs/15_PHASE1_SANITIZATION_AND_WORKATO_TEST_2026-08-21.md`
- `docs/16_WORKATO_QUEUE_SYNTHETIC_HANDOFF_2026-08-22.md`
- `docs/17_INACTIVE_REPEATABLE_QUEUE_TEST_HARNESS_2026-08-23.md`
- `docs/18_LEONARDO_READ_ONLY_DISCOVERY_2026-08-24.md`
- Playwright authentication guidance: `https://playwright.dev/docs/auth`
- Playwright browser-context isolation: `https://playwright.dev/docs/browser-contexts`
- Playwright HAR mocking: `https://playwright.dev/docs/mock`
- OWASP Session Management Cheat Sheet: `https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html`

## New-task continuation prompt

Copy the text below into a new Codex task:

```text
Use $surface-workato-opa for the project at:
C:\Users\Milton Stevenson\Documents\Surface\Workato

Continue Phase 2 from docs/19_PHASE2_LEONARDO_SAFE_EXECUTION_HANDOFF_2026-08-24.md. Also read README.md, docs/08_TECHNICAL_IMPLEMENTATION_GUIDE.md, docs/17_INACTIVE_REPEATABLE_QUEUE_TEST_HARNESS_2026-08-23.md, and docs/18_LEONARDO_READ_ONLY_DISCOVERY_2026-08-24.md.

Leonardo Development is the only Phase 2 target. Keep Salesforce, Workato, OPA, Leonardo, and production read-only unless I explicitly authorize one specific change. Preserve MFA, fail-closed controls, requester/approver separation, idempotency, duplicate checks, redacted logging, and read-after-write verification.

Do not install Playwright/Chromium on workato-opa-01 (172.26.37.20), inspect or persist browser authentication state, replay a HAR, call an undocumented private API, start recipes, create a Leonardo account, or write Salesforce without separate approval.

First, summarize the verified state and blockers. Then implement only the next authorized local-only item from the Phase 2 handoff, with focused tests and a security review. Distinguish verified facts from assumptions.
```
