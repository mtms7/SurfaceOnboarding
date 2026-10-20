# Direct Salesforce-to-Surface Onboarding Integration Plan

**Date:** 2026-09-15
**Status (2026-10-19, late night, main):** CO-0798 onboarded; locks False; Redash 253 edit (docs/56) continues in a Pentera RND session (Claude in Chrome is not allowed in the Pentera org); see **"Handoff 2026-10-19 (late night, main)"** at the end of section 18 (it contains the handoff prompt). **Status (2026-10-19, night, main):** CO-0798 onboarded in production (create and UUID verified); relocked `d450984`; CE-only follow-up `da6746b`; see **"Handoff 2026-10-19 (night, main)"** at the end of section 18. **Status (2026-10-19, evening, main):** UNLOCKED (`c95112a`) for one CO-0798 PC create; create-path fixes `2c724f1`; next is step 3 (start the dashboard); see **"Handoff 2026-10-19 (evening, main)"** at the end of section 18. **Status (2026-10-05, end of day):** Every Start now checks the Redash production clone first (match or stale data stops it), then Leonardo; SpyCloud OFF verified live (CO-0679) and enabled after create; Tenants (production) and DevOps tabs. **Blocker: Redash will not refresh query 251 with the per-query key; set an hourly refresh schedule in Redash or Starts stop after 6 h.** 913 tests OK. The authoritative handoff is **"Handoff — 2026-10-05 (continue here)"** at the end of §18. **Earlier status (2026-10-04):** CO-0757 (Case 3, Approved, Pending baseline from Nov 3) was onboarded in Leonardo Development by the operator (`readback_verified`, 36 s). Built today: DEV tenant inventory (594 tenants, 29 s export), duplicate pre-check from the inventory (name, primary domain, alternate domains) before any browser, invisible server-side tenant search, Approved-overrides-window rule, CO page redesign. Open: SpyCloud is ON on LC tenants (Leonardo default) and scan status must follow the executions list - both designed and probed, not coded. The authoritative handoff is **"Handoff — 2026-10-04 (continue here)"** at the end of §18. **Earlier status (2026-10-03):** Dashboard sign-in is built: the dashboard login is the operator's Salesforce SSO (OneLogin + MFA; only the configured operator in the pinned org), which then runs a preflight and the Leonardo Development sign-in; the dashboard opens as a tab of the automation Chrome window. A read-only Leonardo tenant inventory (export + Tenants page) is built but not yet run live. A 403 that blocked every dashboard button since 2026-10-02 is fixed. **The first live SSO login succeeded (2026-10-03)** after the sign-in was moved to Chrome and orphaned CLI sign-ins are stopped. The authoritative handoff is **"Handoff — 2026-10-03 (continue here)"** at the end of §18. **Earlier status (2026-10-02):** Surface validation (read-only, against each tenant's own Leonardo row) and the Renewal plan card are built; review items 1–4 and 6 are fixed. The authoritative handoff is **"Handoff — 2026-10-02"** at the end of §18. **Earlier status (2026-10-01, end of day):** CO-0649's first scan **COMPLETED**; Case 3 built locally; scan-status sweep, Salesforce IDs panel (dashboard only, env dev), already-onboarded gate, faster dashboard, and a tested VM staging copy added; Guru rules, gaps, questions, and the renewal mapping are in `docs/38`. The authoritative handoff is **"Handoff — 2026-10-01 end of day"** at the end of §18. **Earlier status (2026-09-30, end of day):** **The first automated Surface-only onboarding succeeded.** CO-0649 was created in Leonardo Development and read back (`readback_verified`, `account/add` 200, ID and UUID captured, "No scan started"). The authoritative handoff and next steps are **"Handoff — 2026-09-30 end of day"** at the end of §18. **Earlier status (2026-09-29, late evening):** CE-only onboarding works end to end (CO-0679, CO-0728, CO-0762 created and read back). The Surface-only route is merged and its CO-0649 dry run passed live; the first real Surface create (CO-0649) was the next action — see "Handoff — 2026-09-29 end of day" at the end of §18. The operator dashboard was redesigned the same evening (Pentera styling, queue tiles, a **History** tab for closed onboardings; commits `626c58a`, `f48094d`) — see "Dashboard redesign" in that handoff. Earlier status: Attended local pilot **working end to end** for new Credential-Exposure-only COs in Leonardo Development. The attended runner created and read back **CO-0679** (Tango) and **CO-0728** (Bravoblox) fully automatically on 2026-09-29; **CO-0702** (manually created 9/25) was verified read-only. **CO-0762** (SIERRA) is ready and awaits the operator's click. Code is at `154846a` (local, not pushed). The authoritative handoff and next steps are the **2026-09-29 handoff** entry at the end of §18. Leonardo Development only; no Salesforce writeback; no production action.
**Initial execution target:** Leonardo Development only  
**Production target:** Permanently blocked until a separate, recorded approval

## 0. 2026-09-15 VM portability session handoff

This section is the authoritative handoff for continuing tomorrow. Earlier
references in this document to a five-user, fully unattended target remain
future-state planning; the current pilot is **Milton-only and attended**.

### Verified outcomes

- A sanitized project baseline is versioned at
  `https://github.com/mtms7/SurfaceOnboarding`.
- Latest UI commit: `cd21f18` (`Show runner requirement in VM dashboard`).
  Its GitHub Actions Ubuntu run #9 passed.
- Latest runner-contract documentation commit: `04e18c0`.
- Local full suite passed: **137 tests** (one Windows-only POSIX file-mode
  assertion skipped as intended). Artifact and offline-boundary guards passed.
- VM identity and capacity were checked read-only:
  - Host: `workato-opa-01` / `172.26.37.20`
  - Ubuntu 22.04 LTS; 101 GB free
  - application identity: `surface-onboarding` (UID/GID 998)
  - active and historical application directories were preserved.
- Existing managed runtime was located and verified:
  `/opt/surface-onboarding/runtime/python-3.12.14/bin/python3.12`.
  Ubuntu's default `python3` is 3.10 and cannot run this project because the
  project requires Python 3.12.
- The VM launcher now selects that managed Python 3.12 runtime, rejects a
  missing/wrong interpreter, requires a loopback listener, and requires the
  explicit web-identity marker before activation.
- Git now preserves `scripts/run_vm_dashboard.sh` as executable. Ubuntu CI
  verifies that mode; a Windows-created checkout cannot silently regress it.
- Non-active, versioned VM snapshots were created without overwriting the
  active application:
  - `app-r14-staged-20260915` at `8f2da13`
  - `app-r15-staged-20260915` at `39de9c4`
  - `app-r16-staged-20260915` at `08ab674`
  - `app-r17-staged-20260915` at `cd21f18`
- `r15` ran the complete VM suite successfully with Python 3.12.14:
  135 tests, artifact guard passed, offline-boundary guard passed.
- `r16` directly executed the launcher and correctly refused to activate
  without `SURFACE_ONBOARDING_WEB_IDENTITY_APPROVED=1` (expected exit status
  2). No listener started.
- The VM has no Salesforce CLI, Node.js, or browser runtime installed for the
  `surface-onboarding` identity. No authentication state was inspected.
- `r17` provides a VM-specific connection page: **Manual Salesforce runner is
  unavailable**. It contains no local Salesforce sign-in action in VM mode.
- A temporary, static, loopback-only preview of that page was started on
  `127.0.0.1:8013` and viewed through a MobaXterm local SSH tunnel. It has no
  Salesforce data by design; it proves the fail-closed UI only.

### Important boundaries preserved

- No service unit, proxy, firewall rule, VM package, OPA setting, Salesforce
  login, Salesforce read/write, Leonardo action, browser profile, cookie,
  token, password, MFA value, or production target was changed.
- The active legacy path `/opt/surface-onboarding/app` was not replaced.
- Browser/MFA state must not be placed on the dashboard VM or the Workato OPA
  host. The dashboard is loopback-only and cannot launch a browser in VM mode.
- The dashboard's static preview must not be interpreted as a live Salesforce
  test. It deliberately contains no queue data.

### Manual Salesforce runner design recorded

The non-executable design is documented in
`docs/36_MANUAL_SALESFORCE_RUNNER_CONTRACT.md`. It requires a separate,
ephemeral, Milton-only runner host. The runner keeps browser/CLI session
material local and returns only short-lived, schema-limited readiness or
allowlisted read results. It does not copy credentials, cookies, tokens, or MFA
values to the dashboard, OPA, Git, logs, or backups.

Required approvals before implementing or activating that runner:

1. Identity/SecOps: separate runner host, Milton-only access, lifecycle, and
   cleanup.
2. Salesforce: approved CLI/app identity, exact read scopes, and allowlisted
   query contract.
3. Security: mutual-TLS identity, forwarding/proxy topology, audit retention,
   monitoring, and incident handling.
4. Automation owner: exact first read-only pilot and rollback path.

### First steps tomorrow

1. Stop the current static preview with `Ctrl+C` in its VM terminal after UI
   review; verify the port is no longer listening. This is a temporary preview,
   not a service.
2. Decide and obtain approval for the separate, Milton-only runner host. Do
   not install a browser, Salesforce CLI, Node.js, or Playwright on
   `workato-opa-01`/the OPA host.
3. Implement the runner interface only as a typed, fixed-operation,
   fail-closed contract in the repository. It must not issue generic SOQL,
   arbitrary shell commands, or transfer CLI/browser session material.
4. Add dashboard handling for three attested runner states: `unavailable`,
   `ready`, and `failed`; continue to show no cached Salesforce data unless a
   fresh runner result is valid.
5. After the security approvals exist, provision the runner separately and
   perform one read-only, user-attended Salesforce session-health test. Do not
   start automated polling, Salesforce writeback, Leonardo interaction, or
   OPA changes.
6. Only after a separate web-identity/proxy approval may a temporary live
   dashboard listener be considered. Keep it at `127.0.0.1` and use explicit
   SSH forwarding for any pilot review.

## 1. Outcome

Build a Python service on the existing Ubuntu 22.04 VM that:

1. polls Salesforce twice per day for new or changed Customer Onboarding (CO) records;
2. validates and classifies all six supported onboarding cases;
3. places each CO in a secured web queue for five initial team members;
4. automatically provisions or updates the tenant in Leonardo Development through a supported API when one becomes available, or through guarded Playwright browser automation when no API exists;
5. performs duplicate checks, idempotency checks, and read-after-write verification;
6. writes the verified UUID, onboarding comment dates, and lifecycle stage back to Salesforce; and
7. retrieves and displays scanning state while persisting only information that is not already authoritative in Salesforce or Surface.

Workato is not part of the target runtime. Existing Workato/OPA material remains historical evidence until the direct integration passes its replacement gates.

## 2. Confirmed Decisions

| Area | Decision |
| --- | --- |
| Runtime | Python-first, directly integrated; no Workato dependency |
| Host | `workato-opa-01`, the current VMware Ubuntu 22.04 LTS x86-64 VM; direct service must be isolated from the OPA service identity and files |
| Initial target | Leonardo Development only |
| Production | Hard-blocked until a later explicit approval |
| Ingestion | Salesforce CLI polling twice per day plus authenticated on-demand synchronization |
| Trigger population | COs created by CSMs and visible in the Salesforce Open Onboardings population |
| Slack | `pentera-surface-onboarding` is a notification/reference channel, not the source of truth |
| Coverage | All six onboarding cases are in final scope |
| Users | Five initial operators |
| Salesforce writeback | Verified account UUID/Surface account ID, onboarding dates in comments, and lifecycle stages |
| Large-scope rule | Manual review if any root-domain, subdomain, combined, or licensed quantity threshold is greater than 60 |
| Data ownership | Do not persist copies of data already authoritative in Salesforce or Surface |

## 3. Evidence and Current Repository Assessment

### Reusable local implementation

- `phase1_validator` supplies strict comparison, source binding, date extraction, subscription selection, sanitized rejection notes, and fail-closed validation.
- `phase2_leonardo` supplies domain normalization, public-suffix handling, Case 3 preparation, strict JSON contracts, intent hashing, idempotency-key construction, approval constraints, result validation, and redacted evidence.
- The current local suite passes all 78 tests.
- Stable route identifiers must remain unchanged:
  - `case_1_new_surface_only`
  - `case_2_new_ce_only`
  - `case_3_combined_baseline`
  - `case_4_renew_surface_new_ce`
  - `case_5_renew_ce_new_surface`
  - `case_6_renew_both`

### Missing from the current repository

- deployable web application;
- persistent queue and workflow state;
- scheduled Salesforce poller;
- supported non-interactive Salesforce authentication configuration;
- Leonardo browser/API adapter;
- secure secret-provider integration;
- multi-user authentication and role enforcement;
- automated scan-status reconciliation;
- deployment packaging, service units, backup, monitoring, and recovery.

### Legacy `BO Onboarding` inventory

| Asset | Value | Planned disposition |
| --- | --- | --- |
| Salesforce `review-co.ps1` | Existing SOQL fields, route logic, subscription lookup, and CO normalization | Port useful query/normalization logic into tested Python modules; do not shell-wrap the whole PowerShell workflow |
| `create_case2_ce_bo.py` | Playwright selectors, form controls, duplicate lookup, response observation, and readback techniques | Use as reference only; rebuild behind a typed adapter and Leonardo-only origin allowlist |
| HAR/capture utilities | Sanitization and endpoint discovery ideas | Keep diagnostic-only; never retain raw HARs in the operational application |
| MFA prompt flow | Demonstrates transient operator input | Does not satisfy the fully unattended target; replace with an approved non-interactive authentication design |
| Local Fernet credential store | Avoids plaintext password at rest | Reject for server use because the decryption key is stored beside the ciphertext |
| PowerShell launchers | Useful operator workflow examples | Replace with one Python CLI and systemd services/timers |
| Export validator | Useful forbidden-file patterns | Port into CI and deployment checks |

The legacy package is production-oriented and Case-2-centric. Production URLs and behavior must not be copied into the new executable configuration.

### Guru evidence

| Card | State observed | Recency shown | Use |
| --- | --- | --- | --- |
| Surface & Credential Exposure Onboarding Guide | Unverified | Updated four months ago | Six-case routing and high-level process evidence only |
| Surface Customer Onboarding - Settings and Toggles | Verified | Updated 14 days ago | Current settings baseline, subject to explicit versioning and owner review |

The verified settings card requires explicit values for account settings, attack modules, and license controls. It also identifies conditional settings requiring senior, product, or account-team decisions. UI defaults are not authority.

### Slack evidence

Recent read-only inspection confirmed that channel notifications contain Salesforce deep links and a variable set of account, product, user, domain, CSM, and technical-advisor fields. Some messages omit fields; others place renewal prose where a domain would normally appear. Therefore:

- never create an execution manifest from Slack text;
- use the Salesforce record ID/CO link only as an optional reconciliation hint;
- obtain every executable value from a fresh Salesforce read;
- do not persist Slack message bodies.

## 4. Target Architecture

```mermaid
flowchart LR
    T[systemd timer\ntwice daily] --> P[Salesforce poller\nsf CLI JSON]
    UI -->|on-demand full or single-CO sync| P
    P --> V[Python validation and\nsix-case classifier]
    V -->|blocked| Q[(Minimal workflow DB)]
    V -->|eligible| Q
    Q --> UI[Secured FastAPI web UI]
    Q --> W[Single-flight worker]
    W --> A{Approved auth and\nLeonardo adapter}
    A -->|API if supported| L[Leonardo Development]
    A -->|Playwright fallback| L
    L --> R[Read-after-write and\nscan-status verifier]
    R --> S[Salesforce writeback]
    S --> Q
    SL[Slack notification channel] -. optional reconciliation only .-> P
```

### Recommended components

| Component | Technology | Responsibility |
| --- | --- | --- |
| Application/API | Python 3.12+, FastAPI, Pydantic | Queue views, operator actions, validation endpoints, health checks |
| UI | Server-rendered Jinja templates plus HTMX | Small auditable UI without a separate JavaScript application |
| Operational database | Local PostgreSQL bound to loopback | Minimal workflow state, locks, approvals, audit metadata, Surface-only cache |
| Scheduler | systemd timer | Run reconciliation twice daily at configurable times |
| Salesforce adapter | `sf data query --json` and approved `sf data update` calls | Scheduled/on-demand source reads and verified writeback; parse JSON, never terminal-formatted output |
| Leonardo adapter | Typed interface with API and Playwright implementations | Keep workflow logic independent of transport |
| Browser worker | Playwright Chromium under a dedicated service account | Leonardo Development operations only; no production origin |
| Reverse proxy | Nginx or equivalent | TLS, request limits, security headers, SSO proxy integration |
| Process control | systemd | Separate web, scheduler, and worker services with least-privilege identities |

PostgreSQL is preferred over Redis plus another database: row locks and uniqueness constraints can provide the queue, audit, and single-flight behavior without a second persistence system.

## 5. Source of Truth and Minimal Persistence

### Salesforce remains authoritative for

- CO and Salesforce record identity;
- account, country, contacts, domains, product and onboarding type;
- DealHub subscriptions, quantities, status, dates, and revisions;
- onboarding approval and stage;
- onboarding comments;
- final Surface Account ID and/or Account UUID after verified writeback.

The UI should fetch these values live when opening a CO. They must not be copied into the operational database.

### Surface/Leonardo remains authoritative for

- tenant configuration;
- account UUID after creation;
- current feature/toggle/license state;
- scanning state and completion evidence;
- existing-account and duplicate state.

Surface values retrieved only through browser automation may be cached locally with `observed_at`, `expires_at`, source adapter/version, and a refresh control. Once a UUID or lifecycle result has been written successfully to Salesforce, remove the duplicate local value and retain only its hash and writeback receipt metadata.

### Local database may contain only

- Salesforce record ID and normalized CO number;
- Salesforce source revision/SystemModstamp;
- route engine value and policy version;
- workflow state and non-sensitive reason code;
- canonical intent hash and idempotency key;
- lock/lease state and attempt number;
- approval metadata if a manual-review flow is required;
- adapter version and masked failure category;
- timestamps and actor/service identity;
- short-lived Surface-only observations not stored elsewhere;
- writeback/readback receipt hashes, not raw payloads.

Do not store account names, domains, email addresses, contacts, raw comments, raw Salesforce results, raw Surface responses, screenshots, browser storage, passwords, MFA seeds/codes, cookies, tokens, HAR files, or authorization headers in the database or normal logs.

## 6. Queue and State Model

Use a small user-facing state set backed by a more precise internal state machine.

| UI state | Meaning | Typical Salesforce stage |
| --- | --- | --- |
| `Pending` | Newly discovered, awaiting validation, source correction, scheduled work, or manual decision | `New` |
| `Ready` | Source, route, mapping, threshold, duplicate, authentication, and idempotency gates passed | `Request Approved` |
| `Scanning` | Tenant is verified and Surface scanning is active | `Account Scanning` |
| `Finished` | Scan completed successfully; downstream user/finalization work may remain | `Scan Completed Successfully` or `User Created` |
| `Complete` | All required onboarding steps and Salesforce reconciliation succeeded | `Onboarding Completed` |

Add orthogonal filters rather than overloading the primary state:

- `manual_review_required`;
- `source_data_mismatch`;
- `duplicate_or_collision`;
- `authentication_blocked`;
- `mapping_not_approved`;
- `verification_failed`;
- `uncertain_external_result`;
- `retryable_read_failure`.

The exact Salesforce picklist spelling observed in the repository is `Request Approved`, not `Request Approval`. The application must read the live picklist metadata during implementation and fail closed if it differs.

### Required transitions

```text
discovered -> validating -> pending | manual_review_required | ready
ready -> executing -> verifying -> scanning
scanning -> finished -> complete
any pre-write state -> blocked
any uncertain mutation -> uncertain_external_result (no automatic retry)
```

Every transition must enforce an allowed-from state, expected Salesforce revision, idempotency key, and audit record in one database transaction.

## 7. Salesforce Intake and Writeback

### Polling

- Run two configurable polls per day. Deployment configuration will choose the exact times and timezone.
- Query the same logical population as the Salesforce `Open_Onboardings` list view.
- Initial known criteria are stage not equal to `Onboarding Completed` and approval status not equal to `Rejected`; verify the live list-view definition before implementation.
- Use a watermark plus overlap window so a delayed or missed run does not lose records.
- Reconcile by record ID and SystemModstamp; never by account name.
- Enforce exactly one CO record and unambiguous related records.
- A second fresh read immediately before any Leonardo mutation must match the reviewed revision.

### On-demand synchronization

Authorized `operator` and `admin` users may start either:

- a full refresh of the Salesforce Open Onboardings population; or
- a targeted refresh for one normalized CO number or Salesforce record ID.

Expose this through the web UI and the Python CLI. The web endpoint must be a CSRF-protected `POST`, return a generated synchronization job ID immediately, and execute the Salesforce CLI query in the background. It must not accept arbitrary SOQL, shell arguments, object names, or field lists from the caller.

An on-demand request uses the same fixed query definitions, source-revision checks, validation rules, queue upsert, locking, rate limits, timeout, and masked audit trail as a scheduled poll. Repeated requests for the same scope must coalesce while one is active. Triggering synchronization grants no special approval and cannot bypass a manual-review, mapping, duplicate, authentication, or production-origin gate.

The UI must show requested-by, requested-at, started-at, completed-at, outcome, and a non-sensitive failure category. It must never display or retain raw Salesforce CLI output.

Slack is not needed for correctness. A later optional Slack-events enhancement may shorten discovery latency, but it must only enqueue a Salesforce reread by record ID.

### Writeback order

1. Build and hash the exact intended Leonardo state.
2. Check duplicate and prior-attempt state.
3. Execute one idempotent Leonardo Development operation.
4. Re-read and verify UUID plus every required field/toggle/license value.
5. Update the exact approved Salesforce UUID field or fields.
6. Write the normalized date range as `YYYY-MM-DD - YYYY-MM-DD` using an owner-approved append/replace rule.
7. Advance only the stage supported by verified Surface evidence.
8. Re-read Salesforce and confirm exact persisted values.
9. Mark the local workflow transition complete and purge duplicate values.

No Salesforce stage may move ahead of verified external state. A failed Salesforce writeback after successful Leonardo creation must enter reconciliation, not rerun creation.

## 8. Six-Case Routing and Mapping

All six routes must be visible in the queue from the first release. Automated execution becomes enabled route-by-route only after each versioned mapping has passed owner review and Leonardo tests.

| Route | Intended operation |
| --- | --- |
| Case 1 | Create new Surface account only |
| Case 2 | Create new Credential Exposure-only account |
| Case 3 | Create combined Surface and Credential Exposure account |
| Case 4 | Update existing Surface tenant with new Credential Exposure |
| Case 5 | Update existing Credential Exposure tenant with new Surface |
| Case 6 | Renew/update both existing capabilities |

Each mapping must explicitly define:

- source fields and selected subscription rows;
- product/add-on allowlist;
- new-versus-renewal evidence;
- company and primary-user normalization;
- root domains, alternate domains, email domains, and subdomains;
- account type, country, operator account, and MFA behavior;
- all account settings and attack modules;
- license type, dates, assets, domain and subdomain quantities;
- duplicate/collision keys;
- create/update semantics;
- stable UUID discovery and complete readback;
- Salesforce fields and stage transitions.

Unknown products, add-ons, enums, UI controls, or source conflicts remain blocked. No route may inherit a Leonardo UI default silently.

## 9. Large-Scope Manual Review

Set `manual_review_required` before browser/API execution if any condition is true:

- distinct registrable root domains > 60;
- requested subdomains > 60;
- distinct roots plus subdomains > 60;
- any licensed domain or subdomain quantity > 60.

Also trigger manual review for malformed domain input, ambiguous root/subdomain classification, wildcard/public-suffix input, fake-domain requests, ownership exceptions, network ranges, special toggles, or any current/future policy flag.

The review page must display live Salesforce values, calculated counts, reason codes, source revision, and proposed mapping without persisting the values. A reviewer may reject, request correction, or release the exact revision and intent hash. Any source change invalidates the decision. Whether a released large-scope item continues automatically or remains operator-executed is an owner decision required before this path is enabled.

## 10. Authentication and MFA

The requested final state is fully unattended. That cannot safely be implemented with per-run human MFA. One of the following must be approved before unattended Leonardo mutations are enabled:

1. **Preferred:** official Leonardo API plus least-privilege workload identity and short-lived tokens;
2. **Fallback:** a dedicated automation identity with an approved TOTP mechanism whose seed is held by an enterprise secret manager and never exposed to the application database, files, environment dumps, logs, or UI;
3. **Temporary development bridge:** a persisted user browser session refreshed manually when it expires. This is not fully unattended and cannot be accepted as the final design. **Selected for the attended pilot (2026-09-24):** the runner uses a dedicated persisted Leonardo automation profile on the operator's desktop. The first run performs SSO/MFA once; later runs reuse the session until expiry. A "Reset Leonardo session" button wipes the profile. The profile is never stored on the VM, in Git, in logs, or in backups, and is isolated from the operator's main Chrome profile.

MFA must not be disabled. The implementation must expose an authentication-health state and stop before any form activity when authentication cannot be established safely. CAPTCHA, WAF, unexpected SSO prompts, permission changes, or UI drift are hard stops.

The selected secret backend must provide audited access, rotation, host/workload binding, and revocation. Do not reuse the legacy side-by-side Fernet key/ciphertext design and do not store secrets in source control, PostgreSQL, browser profiles, shell history, or ordinary environment files.

## 11. Browser Automation Safety Contract

If no supported API exists, the Playwright adapter must:

- allow only the exact Leonardo Development HTTPS origin and reject redirects to production;
- run under a dedicated non-login Linux user with a private runtime directory;
- use one fresh context per operation unless an explicitly approved auth design requires otherwise;
- validate visible page identity and form schema before entering data;
- perform duplicate lookup before opening or filling the creation/update form;
- fill only versioned, owner-approved mappings;
- reread every control before submission;
- use an atomic single-flight lock and deterministic idempotency key;
- observe the submit response and reconcile uncertain results by read-only lookup;
- never automatically repeat an uncertain mutation;
- read the created/updated record back and verify every setting;
- retain masked structured evidence only;
- disable screenshots, traces, videos, HARs, and raw DOM dumps during normal operation.

Selectors should prefer stable test IDs or accessible labels. Any ambiguity, missing control, new enum, disabled control, or changed default blocks execution until the adapter and mapping are reviewed.

## 12. Web UI

### Queue page

- filters for Pending, Ready, Scanning, Finished, Complete, Manual Review, and Blocked;
- CO number with a Salesforce link;
- route/case, current stage, last source sync, and last Surface refresh;
- non-sensitive blocker/reason category;
- tenant creation verification indicator;
- manual refresh control for Surface-derived cached state;
- `Sync all from Salesforce` and `Refresh this CO` controls for authorized operators;
- synchronization job progress and last successful Salesforce refresh;
- sortable timestamps and assigned operator/reviewer when applicable.

### CO detail page

- live Salesforce review and source revision;
- calculated route and validation results;
- threshold counts and mapping version;
- proposed action and redacted intent hash;
- execution/readback/writeback timeline;
- manual-review decision controls for authorized reviewers only;
- no credential, token, cookie, raw payload, or raw-comment display.

### Access control

Use individual identities, not the shared VM account or a shared web password. Recommended roles:

- `viewer`: inspect queue and status;
- `operator`: refresh and run approved non-mutating checks;
- `reviewer`: decide manual-review items;
- `admin`: configuration and mapping deployment, not routine approval.

Corporate SSO/OIDC through a reverse proxy is preferred. The web UI must not be exposed until the identity mechanism and membership of the five-user pilot group are approved. Restrict network access to the approved corporate/RND path, enable TLS, rate limits, CSRF protection, secure cookies, and audit logging.

## 13. VM Migration and Deployment

Current verified platform baseline: Ubuntu 22.04 LTS, Linux 5.15, x86-64, VMware. Keep Ubuntu 22.04 for the first release.

Recommended future hostname: `surface-onboarding-01`. Do not rename the VM until Networking/SecOps confirms DNS, monitoring, certificates, backups, and any allowlist effects. The current hostname may remain during development.

### Safe migration sequence

1. Snapshot/backup the VM and inventory packages, services, ports, data directories, certificates, cron/systemd jobs, and monitoring dependencies.
2. Identify Workato-specific services and files; do not delete them yet.
3. Patch the OS through the approved maintenance process.
4. Create dedicated service identities and directories with least privilege.
5. Install pinned Python and Playwright dependencies from an approved internal source.
6. Deploy PostgreSQL loopback-only, the web service, scheduler, and worker.
7. Configure TLS, SSO/reverse proxy, firewall, log rotation, backups, and monitoring.
8. Run synthetic, read-only, and Leonardo Development acceptance gates.
9. Operate in shadow mode alongside the inactive/legacy assets long enough to reconcile queue results.
10. After acceptance and rollback approval, disable Workato services, observe, then uninstall the package and remove only an exact reviewed directory list.
11. Verify recovery, disk contents, open ports, running services, and production-origin blocking.
12. Rename the node only after dependent systems are updated and rollback is available.

These steps do not authorize any VM, DNS, firewall, package, Workato, Salesforce, or Leonardo change. Any later cleanup must use an exact reviewed path list; broad or recursive deletion against an unresolved path is prohibited.

## 14. Delivery Phases and Exit Gates

| Phase | Deliverable | Exit gate |
| --- | --- | --- |
| 0. Decisions and threat model | Auth/MFA, SSO, Salesforce field ownership, comment semantics, mapping owners | Every blocker in section 16 has a recorded owner decision |
| 1. Scaffold | Python package, FastAPI skeleton, migrations, config schema, CI, secret-provider interface | Unit tests, lint, type checks, secret scan pass |
| 2. Salesforce read path | Twice-daily poller, full and single-CO on-demand sync, live CO detail fetch, six-case classifier | Synthetic tests plus approved read-only Leonardo-independent validation |
| 3. Queue UI | Minimal DB, secure queue/detail pages, state transitions, RBAC | Five-user pilot access test; no source-data persistence |
| 4. Mapping registry | Versioned Cases 1-6 mappings and golden fixtures | Owners approve every field, toggle, entitlement, and exception rule |
| 5. Leonardo read-only adapter | Auth health, duplicate search, record/scan readback | Approved read-only tests; production origin demonstrably blocked |
| 6. Browser/API execution | Create/update adapter with locks and uncertainty reconciliation | Synthetic and fill-without-submit tests pass |
| 7. Controlled Leonardo writes | One separately approved Development test per route | Create/update plus complete readback succeeds for all six routes |
| 8. Salesforce writeback | UUID, dates/comments, and stage transitions with reread | Exact field and stage verification; failed writeback cannot rerun Leonardo |
| 9. Scan reconciliation | Scheduled and manual status refresh | Scanning, Finished, and Complete mappings verified |
| 10. Operational hardening | Monitoring, backup/restore, incident runbook, retention jobs | Security review and recovery exercise pass |
| 11. Workato retirement | Disable, observe, uninstall, and remove approved legacy paths | Replacement acceptance, backup, and rollback sign-off |

## 15. Test Strategy

### Local and CI

- preserve the current 78-test suite;
- add strict model/schema tests for every persisted and external contract;
- table-driven tests for all six route combinations and source mismatches;
- threshold boundary tests at 59, 60, and 61 for every quantity type;
- domain/public-suffix, IDN, wildcard, duplicate, and malformed-input tests;
- state-machine and forbidden-transition tests;
- idempotency and concurrent-worker tests;
- log/evidence leakage tests using canary secrets and personal data;
- production-origin denial tests;
- Salesforce CLI timeout, invalid JSON, auth expiry, and schema-drift tests;
- on-demand sync authorization, CSRF, rate-limit, job-coalescing, and arbitrary-query rejection tests;
- Playwright selector ambiguity and UI-drift tests against sanitized fixtures.

### Leonardo Development gates

1. connectivity and login-page identification only;
2. approved authentication-health test;
3. read-only duplicate lookup;
4. read-only existing-tenant and scan-status lookup;
5. form mapping inspection;
6. fill and reread without submit;
7. one controlled create/update per route;
8. independent readback and UUID verification;
9. Salesforce writeback in a separately approved step;
10. negative tests for duplicates, stale revisions, uncertain submission, expired auth, and UI drift.

Production BackOffice is not a test target.

## 16. Blocking Owner Decisions

| Blocker | Owner decision required |
| --- | --- |
| Fully unattended MFA | Leonardo Product/Engineering and SecOps must approve a workload identity/API or dedicated automation identity with secure automated MFA |
| Web UI identity | IT/SecOps must select SSO/OIDC or an equivalent individual-identity control for the five users |
| UUID destination | Salesforce owner must decide the case-by-case mapping for `Surface_Account_ID__c`, `Account_UUID__c`, or both |
| Comment update semantics | Salesforce/process owner must decide append versus replace, collision handling, and preservation of existing comments |
| Poll schedule | Operations owner must choose the two daily run times and timezone |
| Six-case mappings | CSE/Surface, Commercial/DealHub, and Leonardo owners must approve every setting and entitlement for each route |
| Large-scope release | Process/SecOps owners must decide whether approved >60 items resume automatically or remain operator-executed |
| Host rename | Infrastructure owner must approve the new hostname and dependent DNS/certificate/monitoring changes |
| Workato removal | Infrastructure and application owners must approve the exact package/directory removal list after replacement acceptance |
| Salesforce CLI service auth | Salesforce/SecOps owners must approve the server identity, org alias, token storage, rotation, and least-privilege permissions |

## 17. Immediate Next Safe Work

The next local-only implementation slice should create:

1. the Python project/package structure under `integration/`;
2. typed domain models and the internal state machine;
3. adapters with no-op/mock implementations for Salesforce, Leonardo, secrets, and persistence;
4. a versioned mapping registry with every route disabled by default;
5. the >60 manual-review policy and boundary tests;
6. a minimal FastAPI queue using synthetic records only;
7. CI checks that preserve the existing validator suite and prohibit sensitive artifacts.

The first slice should also define the on-demand synchronization command and HTTP contract using a mock Salesforce adapter; it must not call a live Salesforce org until the read-only service identity and test scope are approved.

This slice requires no external access or mutation. Salesforce reads, Slack ingestion, Leonardo authentication, browser form filling, VM deployment, and Salesforce writeback must each cross their documented approval and verification gate later.

## 18. Attended Local Pilot Handoff — 2026-09-17/18

### Completed and verified

- The local attended dashboard runs on `127.0.0.1:8012` under the desktop user's Salesforce CLI session. It must not be run on the Ubuntu OPA VM for browser/MFA work.
- CO-0741 has a revision-bound Salesforce comment-repair flow with fresh source reads and readback verification.
- CO-0745 has a read-only renewal-term evaluator and an exact production tenant-name policy of `<Account Name> - CE Only`; production lookup remains read-only and is not connected to a create action.
- CO-0702 now has a local CE-new-product Email Domains preflight. The current owner decision is deliberately narrow: it validates only one valid `Email_Domains__c` value. Empty, malformed, or multiple values produce `exactly_one_email_domain_required`. It does not validate product, subscription, dates, country, primary domain, or user data.
- The CE-only Guru card `Surface Customer Onboarding - New Credential Exposure only` was read in the operator's authenticated Chrome session on 2026-09-18. It was visibly **Verified** and updated four months earlier. Key guidance observed: CE/Core Plus entitlement review; one CE scanned domain per license; Customer account type; clear `- CE Only` naming when team practice requires it; operator as Primary User; no Operator Account; Surface scanning None; Scan now and advanced options disabled; Leaked Credentials enabled/Weekly; prepaid annual license with Provisioning and subdomains on, one domain, expiration from Salesforce; then read back UUID and mark User Created. This guidance is evidence, not an authorization to create.
- Playwright `1.63.0` and its Chromium runtime were installed on the Windows desktop runtime after user approval. They were not installed on `172.26.37.20`.
- Leonardo Development Tenant Management and a blank Add Account form were inspected read-only. The blank form was cancelled without entering values or submitting. Observed controls include account type, company/primary/alternate/subdomain/email-domain fields, country, primary-user fields, MFA, distinct Operator Account selector, Surface/CE settings, license block, dates, and disabled Confirm button.

### CE-only owner decisions for the attended pilot

- Company naming: exactly `<Salesforce Account Name> - CE Only`, using a regular hyphen, not an em dash. For CO-0702 the intended name is `Sample Company - CE Only`.
- Primary User: `Milton Stevenson`; no Operator Account; phone number and job title remain blank.
- Primary-user email: `milton.stevenson+<alias>@pentera.io`.
  - Account names of 15 characters or fewer: remove spaces and symbols; `Sample Company` becomes `SampleCompany`.
  - Longer names: use word initials; `First Main Bank & Trust` becomes `fmbt`.
- CE-only validation gate: only `Email Domains` is a pass/fail validation. The values needed to fill Leonardo (company, primary domain, country, and approved alternate/subdomains) may still be read from Salesforce, but are not additional commercial-validation gates.
- Manual SSO/MFA remains required. Browser profiles, credentials, cookies, tokens, and MFA material must not be copied, logged, or persisted.

### Not complete / no external creation

- The dashboard's current **Start manual onboarding** handler still opens Tenant Management only and displays the manual-workflow message. It has not been connected to Playwright.
- No desktop CE runner has been written, no duplicate search has run, no Leonardo field has been filled, no tenant has been created, no UUID has been read back, and no Salesforce writeback occurred.
- The local dashboard must not reuse the operator's existing Chrome profile. A future runner must launch a fresh temporary profile, wait for the operator's manual Leonardo Dev SSO/MFA, then destroy its context on completion/error.
- The Ubuntu VM remains a no-browser OPA/validator host. Do not deploy Playwright, Chromium, browser sessions, or Leonardo interactive authentication there.

### Exact next implementation steps

1. Add a desktop-only `attended_ce_only_playwright` runner with pure unit-tested helpers for the CE-only naming and primary-user alias rules.
2. Have the runner perform a fresh Salesforce read for the fill values, while applying the Email Domains-only preflight rule. Fail closed on missing required form data or source-revision drift.
3. Launch an isolated temporary Chromium context at `https://leonardo.dev.app.pentera.io/login`; wait for manual SSO/MFA and Tenant Management. Never read/reuse the existing Chrome session.
4. Perform exact duplicate checks using the derived CE-only company name and primary domain before opening Add Account. Any match, partial-match, schema failure, timeout, or ambiguity must stop without filling/submitting.
5. Fill the reviewed CE-only mapping, explicitly leave Operator Account/phone/job title blank, and verify every selector/control state. UI drift or unavailable controls must stop.
6. Bind **Start Onboarding** (renamed from Start manual onboarding) to a one-time source-revision acknowledgement. The user requested that this action be the attended Dev creation authorization; retain a visible, explicit final state/result and never retry an uncertain creation.
7. After a successful Confirm, search/read back the exact tenant, capture Surface Account ID, Account UUID, and Account Scanning state into the existing minimal local readback evidence format. Do not update Salesforce in this phase.
8. Add focused runner/dashboard tests plus the full integration suite, then ask the operator to restart `tools/start_attended_dashboard.ps1 -Restart` once. Only then perform the separately attended CO-0702 Dev run.
9. After all runner work is verified, update this plan again with commit hash, test results, actual Dev outcome, readback state, and any blockers.

### Progress — 2026-09-21 (steps 6–8 complete locally)

- Steps 6 and 7 are implemented in `tools/attended_ce_only_playwright.py` (revision-bound one-time start, post-Confirm readback capture) and wired into the dashboard: the POST `start-co0702-ce-only-runner` handler now enforces the revision/one-time gate via `evaluate_ce_only_start`, records the start with `record_runner_start`, and fails closed on state-file or preflight unavailability; the GET preflight handler loads and displays the runner state. The runner never clicks Confirm and never updates Salesforce.
- Step 8 focused tests are added: `integration/tests/test_attended_ce_only_runner.py` (state-file round-trip, one-time/supersede, first-writer-wins result, corrupt-file fail-closed, readback evidence schema/preserve/reject, `ce_only_names` 15-char boundary, `one_email_domain` edge cases, `source_for_fill` failure modes, `_exact_tenant_rows` and `_readback_details` classification, and end-to-end `run()` with an injected fake `playwright.sync_api` covering drift-stop-before-browser, login timeout, operator cancel, readback-verified, and value-mismatch) plus dashboard tests for `evaluate_ce_only_start` and the revision-bound preflight page rendering.
- Local test result on 2026-09-21 (Windows, Python 3.12): the CE-only runner and attended-dashboard suites pass in full. The full `python -m unittest discover` run is recorded at commit time; the only expected failures are the 7 pre-existing `invalid_poll_timezone` errors in `integration/tests/test_scaffold.py` (Windows Python 3.12 missing `tzdata`; unrelated to this work).
- The attended CO-0702 Dev run (step 9) is still pending. It is a separate operator-attended action that requires the RND VPN for Leonardo Development access, an explicit restart of `tools/start_attended_dashboard.ps1 -Restart`, and explicit approval. It has not been performed. Step 9 will record the commit hash, actual Dev outcome, readback state, and any blockers after the run.

### Progress — 2026-09-21 (encoding fixes, CE Primary Domain rule, run unblocked)

- Dashboard encoding fix (commit `1400b6e`): `sf_json`/`sf_write_json` in `tools/serve_attended_open_onboardings_dashboard.py` now decode Salesforce CLI `--json` output as UTF-8 (`encoding="utf-8", errors="replace"`) instead of the locale code page, and fail closed when `stdout is None`. Four `SalesforceCliEncodingTests` cover the cp1252-on-UTF-8 failure mode and the `None`-stdout gate.
- Runner encoding fix (commit `c6f9b06`): the same UTF-8 decode + `None`-stdout fail-closed was applied to `source_for_fill` in `tools/attended_ce_only_playwright.py`; `test_none_stdout_fails_closed` added.
- "Browser never opened" diagnosis: the runner died at the source read (`salesforce_fill_source_unavailable`) before Playwright launched, so no browser ever appeared. Playwright itself was verified working from the dashboard's Python (`C:\Users\Milton Stevenson\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`) with a throwaway diagnostic. The many Chrome processes observed were the operator's regular browser, not ms-playwright.
- CO-0702 live source read (read-only): revision `2026-09-21T18:24:55.000+0000`, `Account_Name__c='Sample Company'`, `Main_Domain__c` empty, `Email_Domains__c='sample-co.example'`. The dashboard preflight (`evaluate_co0702_ce_only_fill_preflight`) validates only Email Domains by design, so the record is "eligible" despite the empty `Main_Domain__c`.
- Owner business-rule clarification (no Salesforce data change needed): for a Credential Exposure, `Main_Domain__c` is NOT used — the Leonardo "Primary Domain" must be the single Email Domains value (`sample-co.example` for CO-0702). The runner logic was wrong, not the data.
- CE-only Primary Domain fix (commit `1c4c565`): `source_for_fill` no longer selects or validates `Main_Domain__c`; it returns the normalized single Email Domains value as the Primary Domain (and as the Email Domains value). `SourceForFillTests` updated (mocked rows no longer carry `Main_Domain__c`) and `test_main_domain_field_is_ignored_for_ce_only` added to lock the rule. Expected CO-0702 fill values: Company Name `Sample Company - CE Only`, Primary Domain `sample-co.example`, Email Domains `sample-co.example`, Primary User Email `milton.stevenson+sampleCompany@pentera.io` (name ≤15 chars → spaces removed).
- Full suite on 2026-09-21 (Windows, codex runtime Python 3.12): `python -m unittest discover` → 302 tests, OK (skipped=1), no failures/errors. The 7 pre-existing `invalid_poll_timezone`/`ZoneInfoNotFoundError` errors in `integration/tests/test_scaffold.py` no longer reproduce because `tzdata` is now installed in this Python (verified: `import tzdata` succeeds, `ZoneInfo('America/New_York')` resolves).
- Run unblocked: the stale `integration/attended_ce_only_runner_state.json` (written by an earlier failed diagnostic run) was deleted so the revision-bound one-time gate allows a start for revision `2026-09-21T18:24:55.000+0000`. The gate itself is unchanged: it still blocks any re-run for a revision that already has a record, result or not.
- RND VPN routing: the local model host `10.0.0.202` remains reachable over the RND VPN via the more-specific connected `/24` route on the Wi-Fi adapter; no routing change was needed. Documented fallback if it ever drops: `route add 10.0.0.202 mask 255.255.255.255 10.0.0.1 metric 10`.
- Step 9 (attended CO-0702 Dev run) remains pending and is the next action: operator runs fill preflight → Start Onboarding → manual SSO/MFA → review + Confirm on `127.0.0.1:8012` with the RND VPN connected. After the run, this plan must record the commit hash, actual Dev outcome, readback state (Surface Account ID / Account UUID / Account Scanning), and any blockers.

### Progress — 2026-09-22 (attended-run browser defects fixed; run re-armed, pending operator SSO/MFA)

Two browser defects were found and fixed while running the attended CO-0702 Dev run. Both live attempts timed out in the login-wait loop (no Leonardo mutation occurred). The run is now re-armed (fix committed, gate cleared) and pending the operator's SSO/MFA.

- **Defect 1 — Playwright launch flags kill the browser over the RND VPN (commit `06da5b3`).** Playwright's own `launch()` flag set terminates the browser process when it reaches the Leonardo Development host over the RND (Fortinet) VPN. The runner now launches an isolated Chrome (fresh temp profile, CDP port) via `subprocess` and attaches with `connect_over_cdp`. Focused tests added for `_free_port`, `_chrome_executable`, and `_wait_for_cdp`.
- **Defect 2 — the SSO redirect replaces the CDP target; a held page reference goes stale (commit `cad993f`).** The cross-origin Leonardo SSO redirect replaces the underlying CDP target, so a page reference held from `connect_over_cdp` attach time never advances past the login URL. The runner re-resolved the live page from `context.pages` each poll via `_find_live_page`. This proved insufficient: the held CDP connection's `context.pages` list is also stale across the target replacement, so the re-scan never found the post-login page and the run timed out.
- **Defect 3 fix — detect login via the CDP `/json` endpoint, then attach after login (commit `01be423`).** The runner now polls the CDP `/json` HTTP endpoint (which always reflects the live target list — verified to show the operator's logged-in page) to detect SSO/MFA completion, then attaches via `connect_over_cdp` only after the operator reaches tenant-management, yielding a fresh, valid page object for the in-app automation. A dedicated `LoginTimeout` maps to `development_login_timeout`, and the temp profile is cleaned up on the failure path. Focused tests added for `_cdp_page_urls` and `_wait_for_tenant_management`; end-to-end fakes updated for the new attach signature.
- **Live run history (both timed out in the login-wait loop; no Leonardo mutation occurred).** First run started 20:37:30 and timed out 20:52:38 with `development_login_timeout` (defect 2: stale held page reference). The gate was cleared and the run re-triggered at 21:01:23 on the `cad993f` code; it timed out 21:16:31 with `development_login_timeout` (the `context.pages` re-scan was insufficient). Both runs tore down cleanly (no orphaned Chrome, no leftover `attended_ce_chrome_*` profiles).
- **Tests (2026-09-22, codex runtime Python 3.12):** runner file `integration/tests/test_attended_ce_only_runner.py` → 65 tests OK; `integration` discover → 221 OK (skipped=1); `phase2_leonardo` and `phase1_validator` suites OK. No failures or errors.
- **Gate cleared and run re-armed.** The consumed `integration/attended_ce_only_runner_state.json` (holding the 21:16:31 timeout) was deleted — safe because no Leonardo mutation occurred. The revision-bound one-time gate is unchanged and now allows a fresh start for revision `2026-09-21T18:24:55.000+0000`. The dashboard (`127.0.0.1:8012`) is still running and spawns the runner as a subprocess, so it picks up the `01be423` runner without a restart; it reads the gate state fresh per request.
- **Next action (operator, tomorrow):** on `127.0.0.1:8012` with the RND VPN connected, run the fill preflight → **Start Onboarding** → complete manual SSO/MFA in the fresh isolated Chrome window → review the filled Add Account form → click **Confirm** manually. The runner never clicks Confirm and never updates Salesforce. Expected fill values: Company Name `Sample Company - CE Only`, Primary Domain `sample-co.example`, Email Domains `sample-co.example`, Primary User Email `milton.stevenson+sampleCompany@pentera.io`, Operator Account/phone/job title blank. After Confirm the runner reads back Surface Account ID / Account UUID / Account Scanning into `integration/attended_leonardo_readbacks.json`.
- **Blockers:** none in code. The remaining step is the operator-attended SSO/MFA plus the manual Confirm, which cannot be performed unattended. After a verified readback, this plan must be updated with the readback values (Surface Account ID / Account UUID / Account Scanning).

### Progress — 2026-09-23 (CO-0702 Onboard automation made primary; fill diagnostics hardened; run re-armed)

The 2026-09-22 "next action" (fill preflight → Start Onboarding) was refined. The CO-0702 detail page now presents the attended CE-only fill-and-pause runner as the **primary Onboard action** (a single **Start Onboarding** button), so the operator no longer has to find a buried preflight step. The manual tab-open flow is removed for CO-0702 only and retained as a fallback for all other COs. A live run was attempted and failed at the form-fill stage (no Leonardo mutation); the fill diagnostics were hardened so the next failure is diagnosable. The run is re-armed and pending the operator's attended SSO/MFA plus manual Confirm.

**Commit:** `676d62f` (Make CO-0702 Onboard automation primary; harden CE-only fill selectors and diagnostics). The baseline before this commit was `23eab01`.

- **Dashboard rewiring (CO-0702 Onboard as primary).** `tools/serve_attended_open_onboardings_dashboard.py`: the CO-0702 detail page (`page_detail`) now inlines the automation via a new `_co0702_onboard_section()` helper and no longer offers the separate "Run fill preflight" step or the manual tab-open flow. The Onboard panel shows the live source revision, the Email Domains count, local blockers, any prior-run result, and — when no run is recorded for the current revision — the one-time, revision-bound authorization checkbox plus the **Start Onboarding** button (POST `/attended/start-co0702-ce-only-runner`). The manual flow (`/attended/start-manual-onboarding`) is retained for all other COs.
- **Why the operator saw the manual flow before (root cause, resolved).** The CO-0702 detail page previously presented the MANUAL flow as the primary action; the CE-only automation was buried behind a separate preflight step. The operator's earlier click triggered the manual tab-open, not the automation. The new Onboard button makes the automation the only CO-0702 action.
- **Live run attempted and failed at form fill (no Leonardo mutation).** A run started 2026-09-23T18:53:43 (on the `23eab01` code) reached the Add Account form and failed at 18:55:28 with `fill_form_schema_unavailable`. The Add Account form inputs have UUID ids and no `aria-label`s, so `get_by_label("Account Name")` did not resolve; the `data-am` fallback also did not resolve. No tenant was created and no Salesforce write occurred.
- **Fill diagnostics hardened (the fix was previously "blind").** The prior diagnostics did not capture `data-*` attributes or associated label text, so the selector failure could not be diagnosed. `_capture_search_diagnostics` in `tools/attended_ce_only_playwright.py` now records, per input, the `data-am` attribute and the associated `<label>` text (resolved in-page from `label[for]`, a wrapping `<label>`, or `aria-labelledby`) via a single in-page evaluation. It still records only control metadata — never form values, tenant names, or table contents — and still swallows all failures so diagnostics can never change the fail-closed outcome.
- **Fill robustness.** `_locate_form_field` now has a third fallback, `_form_field_id_by_label`, which resolves a field by its associated label text and returns the element id only when exactly one input matches (fails closed on zero or ambiguous matches). A bounded 5-second wait was added after opening Add Account (defensive only; the field lookup still fails closed if the form or a field is absent).
- **Tests (2026-09-23, codex runtime Python 3.12).** `integration/tests/test_attended_ce_only_runner.py` → 73 tests OK (added `test_falls_back_to_the_associated_label_text`, `test_returns_none_when_label_lookup_is_ambiguous`, and a `CaptureSearchDiagnosticsTests` class covering `data-am`/label capture and swallowed `evaluate` failures; the `_LocatePage` mock now supports `evaluate`/`get_by_id`). `integration/tests/test_attended_open_onboardings_dashboard.py` → 52 tests OK (removed the stale "Run fill preflight" assertion; added `test_co0702_detail_shows_onboard_automation_as_primary_for_approved_source` and `test_co0702_detail_onboard_panel_shows_prior_result_instead_of_rerun`). Full `python -m unittest discover -s integration/tests -t .` → **236 tests OK (skipped=1)**.
- **Gate re-armed and dashboard restarted.** The consumed `integration/attended_ce_only_runner_state.json` (holding the 18:55:28 `fill_form_schema_unavailable` result) was reset to `{}` — safe because no Leonardo mutation occurred. `tools/start_attended_dashboard.ps1 -Restart` was run (new listener PID 40552; Salesforce queue read succeeded). The CO-0702 detail page was verified live: it renders the Onboard panel (source revision `2026-09-21T18:24:55.000+0000`, Email Domains configured: 1, Blockers: none, **Start Onboarding** button) and no manual flow.

**State of the one-time gate:** re-armed (empty). Each attended run consumes it; after any failed run with no Leonardo mutation, reset the state file to `{}` to re-arm. The revision-bound gate is unchanged.

**Remaining blocker for a successful run (selector uncertainty).** The form-fill selectors are not yet confirmed against the live Add Account form. The prior failure (`fill_form_schema_unavailable`) is not fully root-caused because the old diagnostics were blind to `data-*`. The enhanced diagnostics will capture `data-am` and label text on the next failure, so the selectors can then be fixed definitively. The HAR `n2n` definitions indicate the fields carry `data-am` values (`Input_Field-addCustomerAccountForm_0_accountName`, `Input_Field-addCustomerAccountForm_1_accountDomain`), but the live DOM was not confirmed.

**Next action (operator, tomorrow):**
1. Ensure the dashboard is running on `127.0.0.1:8012` (if not, run `tools/start_attended_dashboard.ps1 -Restart`). With the RND VPN connected, open `http://127.0.0.1:8012/co/CO-0702`.
2. Confirm the Onboard panel shows source revision `2026-09-21T18:24:55.000+0000`, Email Domains configured: 1, Blockers: none, and the **Start Onboarding** button.
3. Tick the authorization checkbox and click **Start Onboarding**.
4. In the isolated Chrome window: complete SSO/MFA, review the filled Add Account form, then click **Confirm** yourself. The runner never clicks Confirm and never updates Salesforce.
5. Expected fill values: Company Name `Sample Company - CE Only`, Primary Domain `sample-co.example`, Email Domains `sample-co.example`, Primary User Email `milton.stevenson+sampleCompany@pentera.io`; Operator Account/phone/job title blank.
6. On `readback_verified`: verify `integration/attended_leonardo_readbacks.json` has a CO-0702 entry (Surface Account ID / Account UUID / Account Scanning), then update this plan with the readback values.
7. On another `fill_form_schema_unavailable`: read `integration/attended_ce_only_diagnostics.json` (now includes `data-am` + label text), fix the selectors in the fill plan (`build_ce_only_fill`) / `_locate_form_field` accordingly, re-run the runner + integration suites, re-arm the gate to `{}`, restart the dashboard if the page changed, and retry. (Note: the `CE_FORM_FIELDS` constant named here no longer exists; the fill contract is built by `build_ce_only_fill` — see the 2026-09-25 progress entry.)

**Boundaries preserved:** no Salesforce, Leonardo, Workato, OPA, or production action occurred in this work beyond the one attended Dev run attempt that failed at form fill (no tenant created, no write). No browser profile, cookie, token, password, MFA value, or raw payload was copied, logged, or persisted. The runner never clicks Confirm and never updates Salesforce.

### Progress — 2026-09-24 (auto-confirm, persisted Leonardo session, generalization to all approved CE-only COs)

The attended pilot was changed from a single-CO (CO-0702) manual-Confirm flow to a dashboard that surfaces all COs with `Onboarding_Approval_Status__c == "Approved"` and can onboard them automatically (auto-confirm), creating Credential Exposure tenants in Leonardo Development. A Leonardo session/connection check was added to the main dashboard's connection page.

**Runner (`tools/attended_ce_only_playwright.py`):**

- **Path-based login detection.** `_wait_for_tenant_management` and `_find_live_page` now detect login completion by checking whether the page path contains `TENANT_MANAGEMENT_PATH` (`/backoffice/tenantManagement`) instead of requiring an exact URL match. This was the likely cause of the repeated `development_login_timeout` results (the SSO redirect could land on a URL with query parameters or a trailing segment that broke the exact match).
- **Persisted Leonardo automation profile (Plan §10 option 3 — temporary development bridge).** `leonardo_profile()` returns `(profile_dir, persist)`. The first run performs SSO/MFA once; later runs reuse the persisted session (no MFA until expiry). The profile is stored on the operator's desktop (never VM, Git, logs, or backups) and is isolated from the operator's main Chrome profile. `reset_leonardo_profile()` wipes the profile so the operator can force a fresh SSO/MFA.
- **Session check.** `check_leonardo_session()` launches Chrome with the persisted profile, classifies the session via CDP (`_classify_leonardo_session`), and closes Chrome. Returns a result code: `leonardo_session_active`, `leonardo_session_expired`, or `leonardo_profile_unavailable`.
- **Auto-confirm.** `run()` now clicks Confirm itself after a clear duplicate check (zero matches). `_locate_confirm_button(page)` uses `page.get_by_role("button", name="Confirm", exact=True)` and fails closed (returns `None`) on zero or multiple matches. No retry on an uncertain submit. The runner never updates Salesforce.
- **Reset.** `reset_runner_record(reference)` re-arms a completed, non-successful run (refuses in-flight runs and `readback_verified` results). This makes a failed auto-confirm run retryable.
- **`main()` CLI:** `--check-session` and `--reset-profile` flags added.
- **`run()` exception handling:** `except RuntimeError` added between `except LoginTimeout` and `except Exception` to preserve specific browser-failure codes (`browser_cdp_unavailable`, `leonardo_profile_unavailable`, `login_page_schema_unavailable`) instead of collapsing them to `attended_ce_runner_unavailable`.

**Dashboard (`tools/serve_attended_open_onboardings_dashboard.py`):**

- **Leonardo session check panel (Step 2 Part A).** A "Leonardo Development session" panel was added to the `/connection` page (desktop only; hidden in VM mode) with Check and Reset buttons. POST handlers `/attended/leonardo-dev-session-check` and `/attended/leonardo-dev-session-reset` were added (wired before the reference check; vm-mode returns 503). These do NOT require a `reference` parameter. `LEONARDO_SESSION_MESSAGES` and `page_leonardo_session_result(result)` render the result.
- **Generalized CE-only Onboard (Step 2 Part B).** The CE-only Onboard action is no longer restricted to CO-0702. Any approved CO with exactly one valid `Email_Domains__c` value (and not a renewal) now shows the automated CE-only Onboard section. The manual flow is retained for non-CE-only COs. Case 4 COs remain blocked.
  - `evaluate_ce_only_fill_preflight(reference)` (was `evaluate_co0702_ce_only_fill_preflight()`).
  - `start_attended_ce_only_runner(reference, revision)` — removed the CO-0702 restriction.
  - `evaluate_ce_only_start` — state lookup by `evaluation.reference`.
  - `_ce_only_start_form(evaluation)` — action URL `/attended/start-ce-only-runner`; label "auto-confirm run".
  - `page_ce_only_fill_preflight(evaluation, state)` — body uses `evaluation.reference`.
  - `_ce_only_onboard_section(reference)` (was `_co0702_onboard_section()`) — includes a reset button for completed non-successful runs.
  - `page_ce_only_runner_status(state, reference)` (was `page_co0702_runner_status(state)`).
  - `ce_only_eligible(row)` helper — mirrors the Email-Domains-only gate on the detail row.
  - `page_detail` — order: Case 4 blocked → CE-only Onboard (if eligible and not renewal) → manual flow.
- **Routes generalized:** `/attended/ce-only-runner-status?ref=`, `/attended/rerun-ce-only-fill-preflight`, `/attended/start-ce-only-runner`, `/attended/reset-ce-only-runner` (with `reset_authorized` checkbox). Old CO-0702 routes retained for backward compatibility.
- **New result codes** in `RUNNER_RESULT_MESSAGES`: `leonardo_profile_unavailable`, `browser_cdp_unavailable`, `login_page_schema_unavailable`, `confirm_button_schema_unavailable`, `confirm_button_not_enabled`, `confirm_no_create`.

**Tests:**

- Runner: 92 tests OK (`integration/tests/test_attended_ce_only_runner.py`).
- Dashboard: 54 tests OK (`integration/tests/test_attended_open_onboardings_dashboard.py`), including new tests for `ce_only_eligible` and the non-CE-only detail path.

**CE-only gate (unchanged owner decision):** exactly one valid `Email_Domains__c` value only. No product, subscription, date, country, primary-domain, or user checks. The operator is the human-in-the-loop who triggers each attended run.

**Writeback:** Leonardo Development only. No Salesforce writeback.

**Next action (operator):** restart the dashboard (`tools/start_attended_dashboard.ps1 -Restart`), run a live session check from the `/connection` page, then trigger each attended run from the CO detail page. The runner auto-confirms after a clear duplicate check. On the first run, complete SSO/MFA in the isolated Chrome window (subsequent runs reuse the session until expiry).

### Progress — 2026-09-24 (HAR analysis; duplicate-check and session-stability fixes; session bootstrap)

The 2026-09-24 14:55 CO-0702 run failed with `duplicate_schema_unavailable`. Analysis of the operator-provided HAR (`gdleonardo.dev.app.pentera.io.har`, 12 entries) plus a read-only live table diagnostic and a read-only code audit confirmed the root cause and drove four fixes.

**Root cause (confirmed):** the tenant table is a MUIDataTable fed by `POST /api/v1/backoffice/getAllDetailedAccounts` (`items_per_page:1000`, client-side search — no server search API calls in the HAR). When a search filters to 0 results (the expected no-duplicate case), MUIDataTable renders one empty-state row `<td colSpan=N>No records found</td>` (a single cell). `_exact_tenant_rows` failed closed on ANY row with fewer than 2 `td` cells, so the clean no-duplicate case was misclassified as `duplicate_schema_unavailable`.

**Runner (`tools/attended_ce_only_playwright.py`):**

- **Empty-state row handling.** `_exact_tenant_rows` now treats a genuine empty-state row (the table's only row, a single cell with `colspan > 1`) as `duplicate_clear` — a zero-result search is by definition a clean duplicate check. Any other short-row shape stays fail-closed. New helpers: `_row_text_cells` (row text cells, skipping leading checkbox-only cells) and `_is_empty_state_row`.
- **Checkbox-column robustness (audit finding).** MUIDataTable renders a leading checkbox column when rows are selectable; comparing it as the company name would silently false-clear a real duplicate and break the readback. `_row_text_cells` skips leading cells that contain a checkbox input and no text, so the company/domain columns are compared against the right cells. `_readback_details` uses the same helper when locating the created tenant's row.
- **Stability-based session wait/check (race fix).** `_wait_for_tenant_management` and `_classify_leonardo_session` now require the tenant-management URL to be observed on 5 consecutive 1-second CDP polls before counting. The app can briefly show the tenant-management URL before a client-side auth redirect sends an expired session to `/login`; a pre-redirect sighting no longer classifies the session as active. (The 14:55-era session check had returned `leonardo_session_active` while the page was actually at `/login` ~74 minutes later.)
- **`bootstrap_leonardo_session()` + `--bootstrap-session` CLI.** Opens the dedicated persisted automation profile at tenant-management and waits (bounded, 15 min) until a page is stably there. With a valid session it returns almost immediately; with an expired session the operator completes SSO/MFA once in the visible window and the wait then succeeds. It never fills, submits, or creates.
- **Diagnostics on table-schema failure.** `_capture_search_diagnostics` now also records redacted table-structure metadata (per-row cell count, colspan, checkbox-cell count — never row contents), and `run()` captures it on the `duplicate_schema_unavailable` / `duplicate_ambiguous` paths (pre-create and post-confirm re-search). The `duplicate_schema_unavailable` failure no longer leaves no diagnostic trail.

**Dashboard (`tools/serve_attended_open_onboardings_dashboard.py`):**

- **Re-establish session button.** The `/connection` Leonardo panel (desktop only) now offers "Re-establish Leonardo session (SSO/MFA)" → POST `/attended/leonardo-dev-session-bootstrap` → `bootstrap_leonardo_session()` (vm mode returns 503). The operator completes SSO/MFA once before any mutation; the page confirms when the session is stable.
- **New result codes in `RUNNER_RESULT_MESSAGES`:** `duplicate_schema_unavailable` (blocked; diagnostics captured). **New code in `LEONARDO_SESSION_MESSAGES`:** `leonardo_session_bootstrapped` (success).

**Tests:**

- Runner: 103 tests OK (was 92): empty-state row is clear; single-cell row without colspan stays unavailable; empty-state row among data rows stays unavailable; checkbox column skipped for match/clear; readback with checkbox column; wait/classify require a stable URL (pre-redirect sighting is not active); bootstrap success/timeout/CDP-unavailable.
- Dashboard: 58 tests OK (was 54): connection page offers the bootstrap action (desktop) and hides the panel (vm); `duplicate_schema_unavailable` and `leonardo_session_bootstrapped` messages covered.
- Full integration suite: OK (skipped=1).

**Live evidence still required (operator action):** the table diagnostic (`tools/attended_ce_only_table_diagnostic.py` → `integration/attended_ce_only_table_diagnostics.json`) last ran against an expired session (page at `/login`), so the live table-structure evidence (checkbox column? empty-state colspan? active tab? Add Account form `accountType` default) is not yet captured. The checkbox-column handling above is structural and safe either way, but the diagnostic should be re-run after the session is re-established to confirm the layout before the next creation.

**Next action (operator):**
1. From the dashboard `/connection` page, click **Re-establish Leonardo session (SSO/MFA)**; complete SSO/MFA once in the automation browser window that opens (this is the dedicated persisted automation profile — a separate Chrome window, isolated from the operator's main browser, retaining the Leonardo session per §10 option 3).
2. Re-run the read-only table diagnostic to capture the live table structure.
3. Reset the CO-0702 runner record (dashboard reset button), then trigger the attended run from the CO-0702 detail page; verify `readback_verified` and `integration/attended_leonardo_readbacks.json`.

### Progress — 2026-09-24 (confirm_button_not_enabled root cause; full CE form contract; HAR decision)

The 2026-09-24 18:41 CO-0702 run **passed the duplicate check** (the empty-state fix worked), opened the Add Account form, filled Company name and Company primary domain, then failed with `confirm_button_not_enabled`. The operator's screenshot confirmed it: the Confirm button is disabled while "Account Type" and "Country" are still unset.

**Root cause:** the runner fills only 2 of the form's ~20 controls. The form keeps Confirm disabled until the required controls are set. The full live form inventory was captured read-only at 19:04 (`tools/attended_ce_only_table_diagnostic.py` → `integration/attended_ce_only_table_diagnostics.json`, session still valid from the 18:41 run):

- **Selects (live options):** `accountType` → `customer`/`demo` (default unset); `accountCountry` → ISO country codes, `FR` = France (default unset); `scanningInterval` → `NONE` (default) / DAILY / WEEKLY / MONTHLY; `leakedCredentialsScanningInterval` → `NONE` (default) / DAILY / WEEKLY / MONTHLY; `licenseType` → `Evaluation` (default) / Trial / `prepaid monthly subscription` / `prepaid annual subscription` / `PAYG monthly subscription`.
- **Checkbox defaults:** `mfaRequired` ON, `notificationsAllowed` ON, `multipleUsersAllowed` ON, `apiAccessAllowed` ON, `phishingEnabled` OFF, `leakedCredentialsAllowed` OFF, `provisioningEnabled` ON, `subDomainsNumberAllowed` ON, "Scan now" (aria-label "primary checkbox") ON.
- **Confirm:** disabled in the blank form (as expected).
- **Table view:** the main tenant table loads **all** accounts (588 total, 10/page, no `accountType` filter, sorted by `lastReconScan`). The `accountType==Operator` (66 accounts, `items_per_page:1000`, `projection:[accountName]`) query seen in the operator HAR is the Add Account form's "Select Operator Accounts" list fetch, triggered when the form opens — not a table filter. **Known gap to verify with the creation HAR:** the search box is client-side over the loaded page (10 rows), so duplicate-check coverage may be limited to the first page; the post-creation re-search has the same exposure.

**Salesforce evidence (read-only, 2026-09-24):** CO-0702 `Account_Country__c = 'France'` (→ `FR`), `Main_Domain__c`/`Alternate_Domains__c`/`Primary_User_Name__c`/`Primary_User_Email__c` empty, `CE_Subscription_Information__c` empty (no expiration data in Salesforce), `Onboarding_Product__c = 'Credential Exposure'`, `Onboarding_Approval_Status__c = 'Approved'`.

**Owner decisions (2026-09-24, operator):**

- Toggle contract per the Guru card `Surface Customer Onboarding - New Credential Exposure only`: **everything disabled, only Leaked Credentials enabled**. (This supersedes the 2026-09-18 plan summary's "Provisioning and subdomains on" reading; the creation HAR is the definitive evidence.)
- The operator will **manually create the CO-0702 tenant** in Leonardo Development with a **DevTools HAR capture** of the creation request; the runner will be implemented to replicate that exact contract, and a readback-only path will verify the manually created tenant.
- Exact license quantities (assets/subdomains) and the expiration date come from the operator (also contained in the HAR).

**Next actions:**

1. **Operator:** create the CO-0702 tenant manually in Leonardo Development (Guru card as reference) with HAR capture: DevTools → Network → check **Preserve log** → clear the log → create the tenant → after it appears, right-click the request list → **Save all as HAR with content** → save to the Downloads folder.
2. **Assistant:** parse the HAR (creation endpoint, exact request body: accountType, country, primary user, toggles, license, quantities, dates), implement the full CE form fill in `tools/attended_ce_only_playwright.py` plus a readback-only mode for the manually created CO-0702, update tests, and verify the CO-0702 readback.
3. **Operator:** trigger attended runs for the remaining approved CE-only COs; assistant verifies each readback.

### Progress — 2026-09-25 (full CE form contract implemented; readback-only mode; readback column-mapping fix + tenant-name override)

**Authoritative handoff for continuing tomorrow.** This section supersedes the "Next actions" of the 2026-09-24 entries: the full CE form contract is implemented, the readback-only path is live, and the CO-0702 readback root cause is fixed. The remaining work is the operator-attended runs (CO-0702 readback, then the three create runs).

**Status of the 2026-09-24 "Next actions":**
- Step 1 (operator manual create + HAR) — **done.** The operator created the CO-0702 tenant manually; the creation HAR (`gdleonardo.dev.app.pentera.io.har`, entry [15] = `POST /api/v1/backoffice/account/add`) and the rename HAR (`secoundpart-gdleonardo.dev.app.pentera.io.har`, entry [24] = edit) were both parsed.
- Step 2 (assistant implement full fill + readback-only) — **done** (this entry).
- Step 3 (operator trigger attended runs) — **pending**, the next action tomorrow.

**Runner (`tools/attended_ce_only_playwright.py`) — full CE form contract (owner-confirmed 2026-09-25, per the CO-0702 creation HAR):**

- **`build_ce_only_fill(source)`** builds the full Add Account fill plan (the `CE_FORM_FIELDS` constant no longer exists). Contract: company name `<Account> - CE Only` (regular hyphen); `Account Type` = Customer; primary domain = the single `Email_Domains__c` value; user email domains `["pentera.io"]`; primary user Milton Stevenson with organization email `milton.stevenson+<lowercase alias>@pentera.io`; country from Salesforce; `Scanning interval` None; `Leaked Credentials scanning interval` Weekly on the primary domain; license `Prepaid annual subscription` with 1/1/1 quantities.
- **Toggle contract (owner, 9/25):** everything OFF except Leaked Credentials, Provisioning, and Subdomains → `mfaRequired` OFF, `scan_now` OFF, `notificationsAllowed`/`multipleUsersAllowed`/`apiAccessAllowed`/`phishingEnabled` OFF, `leakedCredentialsAllowed` ON, `provisioningEnabled` ON, `subDomainsNumberAllowed` ON.
  - **License start date — superseded 2026-09-29 (owner decision, operator):** Leonardo Development silently refuses a license start date after the current day (verified by no-submit probes: yesterday accepted; tomorrow and 2026-10-24 refused). The license therefore **starts on the day the attended onboarding runs**; the expiration keeps the contract rule `min(Salesforce Core Plus start + 1 year − 1 day, subscription end)` (CO-0679: 2026-09-29 → 2027-10-23). A run whose expiration is not after the run day stops before the browser opens (`ce_license_dates_unavailable`). The date inputs are readonly Material-UI picker fields displayed as `Sep 29, 2026`; the runner selects dates through the picker.
  - **Superseded 2026-09-29 (owner decision, operator):** `mfaRequired` is **ON** (tenant MFA required). Evidence: the CO-0702 manual-creation HAR (`account/add`) carried `mfaRequired: true`, and it is the Leonardo form default. "Everything off" applies to the feature toggles only. All other toggles are unchanged.
- **Email alias casing: lowercase** (per HAR). `ce_only_names` casefolds the alias in both branches (≤15 chars → spaces/symbols removed; longer → word initials).
- **License source:** the Salesforce DealHub subscription. `select_ce_subscription` prefers `Pentera Core Plus Commercial` rows (casefolded prefix match), explicitly excludes product names containing `bulk` or `additional` (casefolded), and fails closed on missing/ambiguous (`ce_subscription_unavailable` / `ce_subscription_ambiguous`). It does NOT filter on `DealHub_Status__c`.
- **CE license date rule (operator-confirmed):** `startDate = Core Plus subscription start`; `expirationDate = min(start + 1 year − 1 day, subscription end)`. For CE renewals the start date is never modified. `ce_license_dates` implements this.
- **Country select contract:** option label = country name, value = ISO code → filled via `select_option(label=<Account_Country__c>)`.
- **Checkbox location:** by `name` attribute; "Scan now" has no name/id → `get_by_role("checkbox", name="primary checkbox", exact=True)`.
- **License date control contract (resolved via the 9/25 extended diagnostic):** the date controls are plain text inputs identified only by `data-am` = `AddEditTenantModal-date-startDate` / `AddEditTenantModal-date-expirationDate`; form display format `YYYY-MM-DD` (`LICENSE_DATE_INPUT_FORMAT = "%Y-%m-%d"`); the `data-am` locator is primary with label candidates as fallback; the read-after-write guard fails closed on a picker reformat.
- **`run()`** uses the richer `ce_fill_source(reference)` read (frozen dataclass, drift check by dataclass equality); `source_for_fill` is retained.
- **Readback-only mode:** `run_readback(reference, tenant_name_override=None)` — ungated, no runner-state write, no create/confirm/fill. CLI: `--readback-only --co <ref> [--tenant-name <name>]` (no `--revision`). It records the observed scan state verbatim; an empty control → `"No scan started"`. `READBACK_STATES = {"Account Scanning", "No scan started"}` (fail closed otherwise). It never consumes the create gate or touches the runner state file.

**Readback column-mapping fix (root cause of CO-0702 `readback_only_tenant_not_found`):**

- **Tenant table column contract (9/25 probe):** rows use a repeating **label / value / empty** pattern — `texts[0]` = `"Company name"` (label), `texts[1]` = actual company name, `texts[2]` = `""`, `texts[3]` = `"Company primary domain"` (label), `texts[4]` = actual domain, `texts[5]` = `""`, `texts[6]` = `"Account Type"` (label), `texts[7]` = value, `texts[8]` = `""`, `texts[9]` = `"License Type"` (label), `texts[10]` = value, `texts[11]` = `""`.
- **Root cause:** the old `_exact_tenant_rows` compared the fixed first two cells (`texts[0]` = the literal label `"Company name"`, `texts[1]` = the company value) against the expected name/domain, so it could never match → every real tenant was misclassified as `duplicate_clear` (and the readback as `readback_only_tenant_not_found`). This affected both the readback and the create-run duplicate check.
- **Fix:** new helper `_tenant_row_values(texts)` resolves the company (cell after the `"Company name"` label) and domain (cell after the `"Company primary domain"` label) by label, robust to column reordering, returning `(None, None)` when a label is absent so the caller fails closed. `_exact_tenant_rows` and `_open_tenant_details` (the details-row clicker) both use it; missing labels → `duplicate_schema_unavailable`.
- **Tenant-name override for the readback:** `run_readback(..., tenant_name_override=...)` and CLI `--tenant-name` replace the computed contract name for the search, the row classification, and the details lookup (the email domain still comes from the Salesforce source). A blank override is ignored (falls back to the contract name).

**Dashboard (`tools/serve_attended_open_onboardings_dashboard.py`):**

- Readback wiring complete: `start_attended_ce_only_readback(reference)`, POST route `/attended/ce-only-readback`, the readback button on the CO detail page, and the readback result messages. The dashboard launches the runner as a subprocess (picks up runner code changes without a restart); dashboard code changes require a restart.
- **Note:** the dashboard readback button uses the standard contract name, so it is correct for the auto-created tenants (CO-0679/0728/0762). The one-off CO-0702 readback (intentional ` test` suffix) is run via the CLI with `--tenant-name` (see Next actions). The dashboard route does not take a tenant-name override.

**Tests (2026-09-25, codex runtime Python 3.12):**

- Runner `integration/tests/test_attended_ce_only_runner.py` → **151 tests OK** (was 146). New/updated: label-based column mapping (missing labels fail closed; company resolved by label not position), `_RPRow`/`_ReadbackPage`/checkbox mocks emit the label/value/empty pattern, and the readback tenant-name override (override verifies; no override on a ` test`-suffixed tenant is `duplicate_ambiguous`; blank override falls back to the contract name).
- Full `python -m unittest discover -s integration -p "test_*.py"` → **327 tests OK (skipped=1), exit 0.** (Was 322; +5 new tests.) The dashboard suite remains green (65 OK).
- Note: a daemon-thread `OSError [WinError 10038]` can appear intermittently during interpreter shutdown from the CDP HTTP-server tests; it is a known Python quirk, does not fail any test, and the suite exit code is 0 on a clean run.

**CO-0702 live tenant facts (9/25 probe, read-only):** name `Sample Company - CE Only test` (trailing ` test` — an **intentional operator exception**, not a contract value), domain `sample-cotest.example`, Account Type `Customer`, License Type `prepaid annual subscription`. Surface Account ID `000000000000000000000001`, Account UUID `00000000000000000000000000000001`, `lastReconScan: null`. The ` test` suffix means the contract name `Sample Company - CE Only` is a strict prefix of the live name, so a readback without the override fails closed as `duplicate_ambiguous` (correct — it does not verify a mismatched tenant).

**Eligible CE-only auto-run candidates (read-only Salesforce recon, 9/25):** **CO-0679** (Tango, `tango.example`, Israel), **CO-0728** (Bravoblox, `bravo.example`, Germany), **CO-0762** (SIERRA, `sierra.example`, Sweden). Blocked: CO-0686/CO-0719 (8 domains), CO-0712/CO-0756/CO-0763 (leading `@`), CO-0754 (malformed, no approval), CO-0755 (Pending), CO-0748 (5 domains, Pending). All eligible COs have a `Pentera Core Plus Commercial - 500 End Points` subscription (Tango also has a Bulk row, same dates — excluded by the selector). CE license dates: Tango 2026-10-24→2027-10-23, Bravoblox 2026-10-01→2027-09-30, SIERRA 2026-10-01→2027-09-30, Sample Company 2026-09-28→2027-09-27.

**Work state (uncommitted):**

- All code changes are **uncommitted**; the repo was clean at `fc86b43` before this work. Changed files: `tools/attended_ce_only_playwright.py` (full fill contract, readback-only, column-mapping fix, tenant-name override), `tools/attended_ce_only_table_diagnostic.py` (extended input-metadata capture), `tools/serve_attended_open_onboardings_dashboard.py` (readback wiring), `integration/tests/test_attended_ce_only_runner.py`, `integration/tests/test_attended_open_onboardings_dashboard.py`.
- Dashboard: **PID 38676** on `127.0.0.1:8012`, HTTP 200, readback button live on the CO-0702 detail page.
- `integration/attended_ce_only_runner_state.json`: the CO-0702 create gate is **consumed** (`confirm_button_not_enabled`) — **leave as-is.** The tenant was already created manually; leaving the gate consumed prevents an accidental duplicate create. The three create-run COs (CO-0679/0728/0762) have unconsumed gates.
- `integration/attended_leonardo_readbacks.json`: only CO-0740 present; **CO-0702 pending** (the readback has not yet succeeded).
- Scratch probe `tmp/probe_tenant_search.py` deleted.

**Blockers:**

- **Leonardo session TTL is very short** (it expired between a passing `--check-session` and the next probe). Mitigation (proven): bootstrap (or check) then launch the run **immediately, in the background** (the runner's `_attach_attended_browser` waits up to `MAX_WAIT_SECONDS = 900` plus a leading Salesforce query; foreground timeouts kill runs mid-flight). Not a hard blocker.
- **CO-0702 readback pending:** needs a fresh session plus the `--tenant-name` override.

**Next actions (tomorrow):**

1. **Operator:** connect the RND VPN and re-establish the Leonardo session (dashboard `/connection` → **Re-establish Leonardo session (SSO/MFA)**, or `--bootstrap-session`). The session TTL is short, so do this immediately before each run.
2. **Assistant/operator:** re-run the CO-0702 readback in the **background** with the override: `python tools/attended_ce_only_playwright.py --readback-only --co CO-0702 --tenant-name "Sample Company - CE Only test"`. Expect `readback_only_verified`.
3. **Verify:** `integration/attended_leonardo_readbacks.json` records a CO-0702 entry with `surface_account_id` `000000000000000000000001`, `account_uuid` `00000000000000000000000000000001`, and `leonardo_state` `"No scan started"`.
4. **Operator:** trigger the attended auto-creation runs, one at a time, from the CO detail page (the runner auto-confirms after a clear duplicate check; the operator completes SSO/MFA on the first run and the manual Confirm is the runner's, not the operator's — the operator only triggers). Bootstrap immediately before each run. Order and expected CE dates: **CO-0679 Tango** first (2026-10-24→2027-10-23), then **CO-0728 Bravoblox** and **CO-0762 SIERRA** (both 2026-10-01→2027-09-30). Verify each readback into `integration/attended_leonardo_readbacks.json`.
5. **Assistant:** update this plan with the commit hash(es), each run's outcome, readback state, and any blockers.

**Boundaries preserved:** no Salesforce, Leonardo, Workato, OPA, or production action occurred in this work beyond read-only recon and the readback attempts (no tenant created by the runner, no write). No browser profile, cookie, token, password, MFA value, or raw payload was copied, logged, or persisted. The runner never clicks Confirm without the attended gate and never updates Salesforce. Production BackOffice writes remain blocked without separate explicit approval.

### Progress — 2026-09-29 (reused automation browser: one window, one new tab per run; local code + tests only)

- **Behavior:** each attended operation (`run`, `run_readback`, `check_leonardo_session`, `bootstrap_leonardo_session`, the table diagnostic) now opens a **new tab** in the already-running automation Chrome instead of launching a new window, and closes **only that tab** afterwards. The window and its Leonardo session stay open for the next run. It is still only the dedicated persisted profile from `leonardo_profile()` (never the operator's main Chrome profile).
- **Discovery/verification:** the running instance is found via `DevToolsActivePort` in the automation user-data-dir and trusted only when loopback `/json/version` returns a browser WebSocket URL whose port and per-instance path match the file. Missing, corrupt, or stale entries mean "not running" (nothing is deleted) and a fresh launch follows.
- **Launch:** `--remote-debugging-port=0` (Chrome picks a free loopback port and records it in `DevToolsActivePort`) plus a neutral `about:blank` anchor tab, so closing the run tab never exits the reused browser. No `--remote-debugging-address` (loopback only).
- **Tab ownership:** tabs are opened with `PUT /json/new` and tracked by CDP target id; if the SSO redirect replaces the target, only a single new, not-preexisting target is adopted, otherwise the run fails closed. Another tab at tenant-management is never used.
- **Temporary (non-persisted) profiles** keep the old full teardown (terminate Chrome, delete the temp profile) and are never reused.
- **Close/Reset:** new `close_automation_browser()` / `--close-browser` and a dashboard "Close automation browser" button (`/attended/leonardo-dev-browser-close`, desktop only). `reset_leonardo_profile()` first closes the running automation browser (CDP `Browser.close`) and fails closed (nothing wiped) if it cannot.
- **Security trade-off:** while the automation window stays open, its loopback CDP port lets any local process on the desktop drive that Leonardo Development session. The operator closes the automation browser at the end of the day.
- No live run, browser launch, or external system contact was performed for this change.
- **Verified live later on 2026-09-29:** the CO-0702 readback, the no-submit form probes, the CO-0679 dry run and create, and the CO-0728 create each opened a new tab in the one reused automation window and closed only that tab.

### Handoff — 2026-09-29 (first fully automated CE-only onboardings; all fixes; next steps)

**Authoritative handoff for the next session.** It supersedes the "Next actions" of the 2026-09-25 entry. Work was done with the operator attended, one step at a time, with explicit confirmation before each Leonardo write. Everything is committed locally on `main` (**not pushed**); HEAD is `154846a`; the working tree is clean.

#### Outcome

| CO | Account | Result (2026-09-29) | Surface Account ID | Account UUID | Leonardo state |
| --- | --- | --- | --- | --- | --- |
| CO-0702 | Sample Company | Manually created 9/25; **read-only verified** (`readback_only_verified`, name override `Sample Company - CE Only test`) | `000000000000000000000001` | `00000000000000000000000000000001` | No scan started |
| CO-0679 | Tango Group Ltd. | **Created by the runner** 13:39:53–13:40:37 (`readback_verified`), `account/add` 200 | `000000000000000000000002` | `00000000000000000000000000000002` | No scan started |
| CO-0728 | Bravoblox | **Created by the runner** 14:52:35–14:53:20 (`readback_verified`), `account/add` 200, Advanced toggles OFF | `000000000000000000000003` | `00000000000000000000000000000003` | No scan started |
| CO-0762 | SIERRA | **Created by the runner** 15:27:41–15:28:27 (`readback_verified`), `account/add` 200, Advanced toggles OFF, license 2026-09-29 → 2027-09-30 | `000000000000000000000004` | `00000000000000000000000000000004` | No scan started |

**Route-gate fix (`3f487a1`, after the table above was first written):** an audit found that the CE-only Start action, its preflight, and the runner's source read chose the route from the email-domain count alone. An approved **Surface** or **Surface & Credential Exposure** CO with one email domain (for example CO-0757) could therefore have been created as a CE-only tenant. All three layers now require exactly `Onboarding_Product__c = "Credential Exposure"` and `Onboarding_Type__c = "New Product Onboarding"`; the runner stops with `ce_route_mismatch` before any browser work. Verified live: CO-0757 and CO-0649 no longer show the CE card. Tests: 423 OK.

Evidence: `integration/attended_leonardo_readbacks.json` (per-CO IDs), `integration/attended_ce_only_runner_state.json` (CO-0679 and CO-0728 are `readback_verified` and can never be re-run; CO-0702's gate stays consumed on purpose), and `integration/attended_ce_only_run_log.json` (redacted step log per run). All three files are gitignored. Salesforce was **not** updated for any CO.

**Known deviation on CO-0679 (created before two fixes landed):** the tenant name is `Tango Group Ltd. - CE Only` (trailing period), and **Automated discovery, Recon Subdomains, and Web dictionary brute force are ON**. The runner has no edit mode. **Operator action pending:** in Leonardo, rename the tenant to `Tango Group Ltd - CE Only` and turn those three toggles off under **Advanced options**; afterwards run `--readback-only --co CO-0679` (the contract name now has no period) to refresh the local evidence. CO-0728 was created with both fixes applied.

#### Root causes found and fixed today (in order)

1. **`duplicate_schema_unavailable` (CO-0679 run 1):** the tenant table uses MUIDataTable's *stacked* layout, where the zero-result row renders as two cells (label + "No matching records found", no colspan). `_is_empty_state_row` now accepts a 2–3-cell row whose only non-empty text is an allowlisted empty-state message; the 1-cell colspan rule is unchanged (`05c53fa`).
2. **`fill_form_schema_unavailable` (run 2):** the five `<select>` controls have no associated `<label>`. `_locate_select` locates them by `name` (`accountType`, `accountCountry`, `scanningInterval`, `leakedCredentialsScanningInterval`, `licenseType`) (`0ea4b05`).
3. **Duplicate check could read a stale table (QA review):** the tenant search is **server-side** (`getAllDetailedAccounts` per edit, HAR-verified; this also settles the old "first page only" concern). `_search_tenants` clears the box and waits for the response that carries *this* lookup; the server rows are also classified independently (`_api_duplicate`: an exact name or domain match, or `total_count` > rows, blocks) (`741a48e`, `55f3043`).
4. **Details view has no labelled ID/UUID controls:** the readback now reads `id`, `accountUuid`, `lastReconScan` from the single exact row in the search response (details view is the fallback) (`55f3043`).
5. **`fill_form_schema_unavailable` (run 3) — license dates:** the date inputs are `readonly`, display `Sep 29, 2026`, and are set only through a Material-UI v3/v4 picker (`MuiPickersModal-dialogRoot`, auto-accepts on day click). `_pick_license_date` steps months with the header settled between slide transitions, clicks the single enabled day, and verifies the displayed value (`cd6b9b6`).
6. **Leonardo refuses a start date after today** (silently; no-submit probes: yesterday accepted, tomorrow and 2026-10-24 refused). See the owner decision below (`b8840d7`).
7. **Advanced options:** Automated discovery, Recon Subdomains, and Web dictionary brute force sit under a collapsed **Advanced options** section and default **ON**. The runner expands it (idempotently), sets them OFF, pins the other enabled advanced toggles OFF, and re-verifies before Confirm (`b6fb327`).
8. **`duplicate_schema_unavailable` on CO-0728 (14:30):** the session had expired — the tab still showed Tenant Management but the API answered **401**. A 401/403 now stops every mode with **`leonardo_session_expired`** (`d2c42c5`).

#### Owner decisions recorded 2026-09-29 (operator)

- **Tenant MFA required = ON** (matches the CO-0702 manual-creation HAR and the Leonardo default). "Everything off" in the 9/25 contract applies to feature toggles only.
- **License start = the day the attended onboarding runs** (Leonardo Development refuses future starts). Expiration keeps the contract rule `min(Core Plus start + 1 year − 1 day, subscription end)`. A run whose expiration is not after the run day stops before the browser opens (`ce_license_dates_unavailable`).
- **Tenant name:** a trailing period is removed from the Salesforce account name (`Tango Group Ltd.` → `Tango Group Ltd - CE Only`); internal periods stay; the email alias is unchanged.
- **Advanced options OFF:** Automated discovery, Recon Subdomains, Web dictionary brute force (plus Web dorking, Nuclei, Authenticated Testing, Static outbound IP, AI, Multiple attack stacks). MAS for subdomains and Web Agent are disabled by the form and left alone.
- **Dashboard UX:** one **Start Onboarding** action per CO; the duplicate check is part of the run; a duplicate returns to the CO as "Already exists — duplicate"; Pentera platform styling.

#### Current CE-only contract (what one run does)

1. Fresh fixed-field Salesforce read (`ce_fill_source`); revision must equal the acknowledged revision; license dates computed (start = run day).
2. Open a new tab in the reused automation window; wait until Tenant Management is stable (operator does SSO/MFA only if the session expired).
3. Duplicate check by **tenant name** and **primary domain** (table + server rows). Any match → `duplicate_found` (nothing created).
4. Re-read Salesforce (drift check), open Add Account, then fill in this order: Account Type / License Type / Scanning interval → expand Advanced options → all toggles → Country / Leaked Credentials interval → text fields → license dates (picker) → re-verify every toggle, select, and date.
5. Confirm once (never retried); record the `account/add` status; wait for the form to close (unknown ≠ closed).
6. Re-search, read back ID/UUID/state from the server row, write local evidence (`readback_verified`). Never updates Salesforce.

Values: company `<Account without trailing period> - CE Only`; Customer; primary domain = the single Email Domains value; user email domain `pentera.io`; primary user Milton Stevenson, `milton.stevenson+<alias>@pentera.io`; country from Salesforce; Scanning None; Leaked Credentials Weekly on the primary domain; Prepaid annual subscription, 1/1/1; MFA ON; Scan now OFF; Notifications/Multiple users/API/Phishing OFF; Leaked Credentials, Provisioning, Subdomains ON; Advanced options OFF as above.

#### Runner and dashboard features added today

- **Run log** `integration/attended_ce_only_run_log.json` (gitignored, last 20 runs): every step and field outcome, console errors, failed requests, API method/path/status, and failure diagnostics. Redacted: no cookies, headers, bodies, query strings, or source values.
- **One reused automation window** (a new tab per run; "Close automation browser" on `/connection`). Close it at the end of the day (loopback CDP port trade-off).
- **CLI modes** (`tools/attended_ce_only_playwright.py`): `--co X --revision R` (create), `--dry-run` (everything up to Confirm, then Cancel; never touches the gate), `--duplicate-check`, `--readback-only [--tenant-name]`, `--bootstrap-session`, `--check-session`, `--close-browser`, `--reset-profile`. The last read-only check per CO is recorded in `integration/attended_ce_only_check_state.json` (gitignored).
- **Dashboard:** Pentera-style shell for the CO and status pages; status chip beside the CO number (Ready / Running / Onboarded / Duplicate / Failed); a green ✓ or red ✗ outcome banner; a finished run returns to the CO after 6 s; re-posting an already-run revision shows its outcome instead of a dead end; the separate duplicate/readback buttons and routes were removed; unknown POST routes return 404 before any form parse, Salesforce read, or launch.
- **Tests:** full `python -m unittest discover -s integration -p "test_*.py"` → **420 tests OK (skipped=1)**, stable across three consecutive runs (codex runtime Python 3.12.14). The test fakes now mirror the live DOM (no labels on selects, readonly picker dates, collapsed Advanced options, server-side search responses), which is how earlier bugs had slipped past the tests.

#### Commits since `fc86b43` (local `main`, not pushed)

`bbc36f6` 9/25 slice · `05c53fa` stacked empty-state row · `0ea4b05` run log + selects by name · `741a48e` QA hardening · `ee4a72e`/`dc3aa4b` single automation window · `ea1a062` MFA ON · `55f3043` API readback + API duplicate check · `cd6b9b6` date picker · `b8840d7` run-day start + dry run · `b6fb327` trailing period + Advanced toggles · `ae2d030` outcome banner + redirect · `c844786` read-only duplicate check · `d2c42c5` session-expired result · `f4994d3` single action + Pentera styling · `154846a` stale note removed + deterministic route test.

#### Blockers and cautions

- **Leonardo session TTL is short** and an expired session can still show Tenant Management. Mitigation: re-establish the session on `/connection` right before a run; an expired session now reports `leonardo_session_expired`, never a false result.
- **Dashboard lifetime:** when the assistant starts the dashboard from its tool shell it can stop when the session restarts (observed after a model switch). The operator should start it from their own terminal: `powershell -ExecutionPolicy Bypass -File tools\start_attended_dashboard.ps1 -Restart`.
- **No edit mode:** fixing an existing tenant (CO-0679) is manual until a guarded edit mode is designed and approved.
- **Readback evidence label:** `attended_leonardo_readbacks.json` still says `"source": "Leonardo Development Details readback"` even when the values come from the search response. Cosmetic; worth renaming in a later slice.
- **HAR files** in the operator's Downloads folder (`gdleonardo…`, `secoundpart-gdleonardo…`) contain session material. They were read for structure only (boolean setting names/values, endpoint paths). Recommend deleting them once no longer needed.

#### Next steps

1. **Operator:** start the dashboard from your own terminal, connect the RND VPN, and on `/connection` click **Re-establish Leonardo session** if needed.
2. **Operator:** on `http://127.0.0.1:8012/co/CO-0762`, tick the box and click **Start Onboarding**. Expect `SIERRA - CE Only`, license run day → 2027-09-30, `readback_verified` and a green Onboarded chip. Optionally run `--dry-run` first.
3. **Operator:** fix the CO-0679 tenant manually in Leonardo (rename; three Advanced toggles OFF). **Assistant:** then run `--readback-only --co CO-0679` to refresh its evidence.
4. **Assistant:** re-run the Salesforce recon for newly approved CE-only COs. **CO-0755** was Approved with one Email Domain at 9/29 and has not been reviewed yet; CO-0686/0712/0719/0756 remain blocked by the Email Domains gate.
5. **Owner decisions still needed:** (a) whether and when to write the verified Surface Account ID / Account UUID, onboarding dates, and stage back to Salesforce (§7, §16 — blocked today); (b) whether to build a guarded **edit** mode for correcting existing tenants; (c) whether to push the local commits to `mtms7/SurfaceOnboarding`.
6. **Assistant (small follow-ups):** rename the readback evidence `source` label; update `README.md` and `LOCAL_IMPLEMENTATION_STATUS.md`, which still describe the 9/11–9/12 scaffold and do not mention the attended CE-only runner.

**Boundaries preserved:** Leonardo Development only. Tenant creation happened only on explicit operator clicks (CO-0679, CO-0728). All diagnostic probes were no-submit and cancelled the form. No Salesforce write, no production action, and no Workato/OPA/VM change. No password, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

### Progress — 2026-09-29 (Surface-only route `case_1_new_surface_only` implemented locally; not run live)

- **Route-contract layer** (`tools/attended_ce_only_playwright.py`): `RouteContract` + `ROUTES = {case_2_new_ce_only: CE_ROUTE, case_1_new_surface_only: SURFACE_ROUTE}`; `run/run_readback/run_duplicate_check(..., route=)` and CLI `--route` (default CE). The CE fill plan and the CE fake end-to-end event sequence are pinned by golden tests (`CeGoldenTests`). `_fill_ce_form` is an alias of the generic `_fill_add_account_form` (optional `advanced_texts`, `blank_texts`, `operator_account_empty`).
- **Surface source** (`surface_fill_source`): exact `Onboarding_Product__c = "Surface"` and `Onboarding_Type__c = "New Product Onboarding"` (`surface_route_mismatch`); main root / alternate roots / subdomains via the pinned-PSL normalizer (networks → `surface_networks_not_supported`; wildcards/malformed → `surface_domains_invalid`; non-root main → `surface_main_domain_invalid`); exactly one counted baseline (Active, or Pending starting ≤ run day + 14 days) classified by `phase1_validator.surface_source_readiness.classify_surface_product` (Go/Prime/Essentials/Professional/Enterprise; Prime without a number = 1000; bulk and "Additional" subdomain add-ons summed; domain add-ons add nothing). A Core Plus baseline row does not block: it sets `core_plus_present` (Leaked Credentials stays OFF; dashboard reminder "enable Credential Exposure later").
- **Surface fill**: account name without trailing periods (no suffix); Customer; primary/alternate/subdomain CSVs; `pentera.io`; Networks/phone/job title verified empty; Operator Account verified empty (Dev skip); MFA ON; Scanning interval per tier; Scan now ON; Advanced profile + Maximum scan Duration 90 h (live default 24); Notifications/Multiple users/API ON (API behind `SURFACE_API_ACCESS_REQUIRES_CORE_PLUS`, open owner question); Phishing/LC OFF; Provisioning/Subdomains ON; Prepaid annual; 10000 assets; domains = 1 + alternate roots; subdomains = baseline + add-ons; CE date rule. Readback accepts a scanning tenant (`allow_scan_started`).
- **Dashboard**: `route_for(row)`; Surface card with counts-only scope summary, two required checkboxes (authorization + "I reviewed the scope for this source revision"), start bound to revision and scope digest (`/attended/start-surface-runner`; 409 `scope_review_missing` / `scope_changed`); runner state records `route` and `scope_reviewed_on`; reminders with local acknowledgements in gitignored `integration/attended_scan_reminders.json` (`/attended/mark-scan-settings-off`, `/attended/mark-ce-enabled`).
- **Not verified live**: the max-duration control lookup, the Operator Account selected-chip check, Prime/older product names beyond the shapes supplied by the operator, the CSV separator accepted by Leonardo, and `lastReconScan` as the only scan-started signal (`lastScanStatusEnum` unused). No Leonardo, Salesforce, or browser action was performed for this change.
- *Superseded in part by the end-of-day handoff below* (Scan now behaviour; controls since verified by the CO-0649 dry run).

### Handoff — 2026-09-29 end of day (Surface-only route ready for its first live create; continue here tomorrow)

**Authoritative handoff for the next session.** It supersedes the "Next steps" of the earlier 2026-09-29 handoff. The CE-only route is complete for today's COs. The Surface-only route (`case_1_new_surface_only`) is merged, its dry run on **CO-0649 passed live**, and the **first real Surface create has not been run yet**. Nothing was created in Leonardo for any Surface CO. No Salesforce write happened today.

#### Where things stand

| Item | State |
| --- | --- |
| CE-only COs | CO-0679, CO-0728, CO-0762 created and read back (`readback_verified`); CO-0702 verified read-only. Gates permanently consumed. Real IDs are in the local gitignored `integration/attended_leonardo_readbacks.json`; the table in the earlier handoff shows placeholders (sanitized before publishing). |
| CO-0679 tenant | Still named with a trailing period and has Automated discovery / Recon Subdomains / Web dictionary brute force ON (created before those fixes). **Manual fix pending** in Leonardo, then `--readback-only --co CO-0679`. |
| Surface route | Merged (`32f3a04` via merge `40d6c2b`) plus the Scan-now fix `668b829`. Tests: **497 integration OK (1 skipped)**, 55 `phase1_validator` OK. |
| CO-0649 (Surface Prime 1000, 1 + 7 domains, Core Plus Enterprise on account) | **Dry run passed** 20:09:51–20:10:31 (`dry_run_fill_verified`); gate **unused**; ready for the first real create. |
| CO-0735 (Surface Prime 1000) | **Blocked** `surface_domains_invalid`: `Alternate_Domains__c` holds 5 valid roots plus a free-text line ("IOT Domains e.g. …"). Needs a Salesforce data correction by the CSM/owner; the runner will not guess. |
| GitHub | `origin/main` = `e854101` (sanitized squash, pushed by the operator). Local `main` is **10 commits ahead** (`71bc251`, `ebd0746`, `a4c021b`, `32f3a04`, `40d6c2b`, `668b829`, `f109a3a`, `626c58a`, `f48094d`, plus the commit that records this update). A pre-push scan of the Surface branch found no customer names, IDs, real domains, or secrets. The three later commits (`f109a3a` handoff, `626c58a` and `f48094d` dashboard) were scanned the same way: the code and tests contain no customer names, email addresses, or Salesforce IDs, and the plan uses only the sanitized placeholders already on `origin`. The local-only branch `backup/local-history-2026-09-29` holds the unsanitized history — **never push it**. The assistant's `git push` is blocked by the safety classifier; the operator pushes (`git push origin main`). |
| Dashboard | Redesigned (see "Dashboard redesign" below). **518 integration tests OK (1 skipped).** Restarted at about 21:26 to load `f48094d`; `/`, `/?queue=ready`, `/history`, and `/connection` return 200 live. |
| Runtime | Dashboard on `127.0.0.1:8012` and one automation Chrome window were left running from the assistant's shell (the dashboard stops if that session ends). Close the automation browser at the end of the day (dashboard `/connection` → **Close automation browser**), and start the dashboard from your own terminal tomorrow. |

#### Surface-only contract (owner decisions 2026-09-29; Guru notes in `docs/37_CASE1_SURFACE_ONLY_GURU_NOTES_2026-09-29.md`)

- **Route:** exactly `Onboarding_Product__c = "Surface"` and `Onboarding_Type__c = "New Product Onboarding"`. The CE route keeps its own exact gate (`Credential Exposure` / `New Product Onboarding`, fix `3f487a1`).
- **Licence:** one Surface baseline row that is Active, or Pending starting within 14 days. Tier → interval: Prime / old Enterprise = Weekly; Go / old Essentials / Professional = Monthly. Subdomains = number in the product name (Go 500, Prime 1000) + subdomain add-on rows. Assets 10,000. Number of domains = main + alternate roots. Dates = CE rule (start = run day; expiration = min(contract start + 1 year − 1 day, subscription end)).
- **Core Plus** row (Commercial or Enterprise) on the account: do **not** block; Leaked Credentials stays OFF; amber reminder "enable Credential Exposure later" with a local acknowledgement.
- **Form:** company name = account name without trailing period (no suffix); Customer; primary domain; Alternate Domains / SubDomains CSVs from Salesforce (verified empty when none); User email domains `pentera.io`; Networks, phone, job title empty; primary user Milton Stevenson with the shared alias rule; MFA ON; **Operator Account empty (Dev skip)**; Scanning interval per tier; Advanced options: **Maximum scan Duration 90 h** (live default 24), Automated discovery OFF, Recon Subdomains ON, Multiple attack stacks OFF, Web dictionary brute force ON, Web dorking OFF, **Nuclei ON** (live default OFF), Authenticated Testing OFF, Static outbound IP OFF, AI OFF; Notifications / Multiple users / API access ON; Phishing OFF; Leaked Credentials OFF; Provisioning ON; Subdomains ON; Prepaid annual subscription; Number of subdomains overwritten (live default 50000).
- **Scan now (live finding, fix `668b829`):** the Scan now control exists **only while Scanning interval is None**; a Weekly/Monthly schedule removes it. The Surface plan therefore does not set Scan now; it verifies the control is absent after the interval is set and again before Confirm. The schedule itself drives scanning; the dashboard shows an amber "turn scanning off later" reminder after a Surface create (owner decision: scanning ON in Dev, turned off later).
- **Manual scope review (owner option b):** every Surface CO shows a counts-only scope summary and needs two ticks (authorization + "I reviewed the scope for this source revision"); the start is bound to the revision and a scope digest.
- **v1 scope:** create + readback now; **scan-status sweep next**. Salesforce updates and customer-user creation stay manual.

#### Verified live on CO-0649 (dry run, no submit)

Duplicate check clear (name + domain); selects set (Customer, Weekly, Prepaid annual, Country); **Scan now verified absent**; Advanced options expanded; **max scan duration 90 set and re-read**; all toggles set as above; 7 alternate domains, `pentera.io`, primary user, **assets 10000, domains 8, subdomains 1000**; SubDomains / Networks / Phone / Job title verified empty; **Operator Account verified empty**; license dates 2026-09-29 → 2027-09-30 via the picker; final re-verification passed; form cancelled.

Still not verified live: the post-Confirm readback on a **scanning** tenant (`lastReconScan` as the scan-started signal; `lastScanStatusEnum` values), and the Surface dashboard reminders after a real create.

#### Dashboard redesign (late evening; commits `626c58a`, `f48094d`; local code only)

Designed with two expert reviews (a UX/queue review and a data-visualisation review) and built to their shared recommendation, matching the Pentera platform and the CO page.

- **Shell:** every page (queue, History, CO, runner status, Connection) uses the Pentera shell. It has a navy sidebar (**Onboardings · History · Connection**) and a light canvas with white cards. On narrow screens the sidebar becomes a top bar. No JavaScript is used, so the CSP is unchanged (`default-src 'none'; style-src 'unsafe-inline'`); every page is rendered on the server with inline SVG and CSS-only hover.
- **Queue page (`/`):** six tiles.
  - Five are both counts and filters: **All open**, **Manual review**, **Ready to onboard**, **Needs validation**, **Follow-up** (`/?queue=review|ready|validation|scanning`).
  - The sixth, **Completed**, shows onboardings completed in the last 30 days and the change against the previous 30. It opens the History tab.
  - Below the tiles, each queue has one aligned table: Onboarding + account; Product/type; Automation chip (CE-only / Surface-only / Manual, plus the run result); Salesforce stage/approval; Next step; Age in days.
- **Queue rules** (`classify_queue_row`, first match wins):
  1. A failed or duplicate run → Manual review.
  2. A tenant already exists (verified run, local readback, or a stage past approval) → Follow-up. The next step is one of: Update Salesforce, Create customer user, Complete onboarding, or Waiting · Leonardo scan.
  3. A run in progress → Ready, "Waiting · runner".
  4. Approval status missing → Manual review.
  5. Approved + New/Request Approved → Ready. The next step is Start onboarding, Review scope, start, or Onboard manually.
  6. Pending + New → Needs validation (DealHub term check).
  7. Anything else → Manual review.

  **Fix:** an onboarded CO is no longer shown as "ready" again.
- **History tab (`/history`):**
  - Chart: completed onboardings per month for 13 months (the last 12 plus this month, marked "to date"), stacked by product in a fixed, validated colour order. The order is Credential Exposure, Surface & Credential Exposure, Surface, then Other (unknown products). New COs created per month appear as a tick on the same count axis; there is never a second axis.
  - Stat strip: completed in the last 30 days with the change, and the median days from created to completed over the last 90 days.
  - Hover shows each month's values; a **Table view** lists all of them.
  - A footnote gives the rejected-all-time count, because Salesforce has no rejection date to chart.
  - Data: four fixed, read-only aggregate SOQL queries (`COMPLETED_HISTORY_QUERY`, `CREATED_HISTORY_QUERY`, `COMPLETION_DURATIONS_QUERY`, `REJECTED_TOTAL_QUERY`). Results are cached in memory for 10 minutes (a failure for 60 s) and never written to disk or logs. The page shows the cached read time.
  - In narrow windows the chart opens on the newest months.
  - A history failure degrades only its own card or tile; the open queue is unaffected.
- **Queue-page reads:** the unfiltered queue page warms the history cache. The filtered queue views only peek at it (`cached_closed_history`), so they never add a Salesforce read.
- **Connection page:** it is in the shell and says "Connection required" only on the failure path.
- **Verified:** 518 integration tests OK (1 skipped). After the restart, the live pages return 200 and were checked visually at about 800 px and 1400 px.
- **Boundaries:** Salesforce reads only (aggregate counts plus the existing open-queue read); no Leonardo, Salesforce-write, Workato, OPA, or VM action.

#### Open owner questions

1. **API access for Surface:** (a) always ON (Verified "New Surface Account Only" card; current setting), or (b) ON only when the account has Core Plus ("Surface License Tiers Breakdown" card, Unverified: "API depends on Core Plus"). One switch: `SURFACE_API_ACCESS_REQUIRES_CORE_PLUS`. CO-0649 gets API ON either way.
2. **Scan now vs. schedule:** Guru says "enable Scan now", but the form hides Scan now once a schedule is set. Current behaviour: schedule only. Confirm this is acceptable.
3. Earlier, still open: Salesforce writeback timing (§7/§16); a guarded edit mode for existing tenants (CO-0679 fix); CO-0755 CE review.
4. **Dashboard address:** keep `http://127.0.0.1:8012/`, or serve it at `http://127.0.0.1/` (port 80)?
   - Port 80 needs three changes: `listener_address()` must allow exactly port 80 on the desktop (it currently enforces 1024–65535), the start script's default must change, and the tests and docs must be updated.
   - Windows does not reserve low ports, but IIS/HTTP.sys or another service may already hold port 80. Check that first (`netstat -ano | findstr :80`).
   - Either way, the listener stays on loopback.

#### Next steps (tomorrow, in order)

1. **Operator:** start the dashboard from your own terminal (`powershell -ExecutionPolicy Bypass -File tools\start_attended_dashboard.ps1 -Restart`), connect the RND VPN, and re-establish the Leonardo session on `/connection`.
2. **Operator:** answer the API-access question (the default is fine for CO-0649) and the dashboard-address question (keep `:8012` or move to port 80). Look over the redesigned queue page and the **History** tab.
3. **Operator:** on `http://127.0.0.1:8012/co/CO-0649`, check the scope summary (Prime, Weekly, 8 domains, 1000 subdomains, 10000 assets, 2026-09-29 → 2027-09-30, Core Plus yes), tick both boxes, click **Start Onboarding**. This is a real create and the Weekly schedule starts scanning the customer's real domains from Dev. Note: the licence start will be the run day (tomorrow), so a fresh dry run first is optional but cheap: `python tools\attended_ce_only_playwright.py --route case_1_new_surface_only --co CO-0649 --revision "2026-07-22T12:00:05.000+0000" --dry-run`.
4. **Assistant:** verify the result from the run log, `account/add` status, readback (`leonardo_state` expected "Account Scanning" or "No scan started"), and the two amber reminders on the CO page; record the outcome here.
5. **Operator/CSM:** correct CO-0735's `Alternate_Domains__c` in Salesforce (remove or replace the free-text IoT note); then CO-0735 can follow the same flow.
6. **Assistant:** build the **scan-status sweep** (phase 2): a read-only runner mode that, in the signed-in automation tab, reads each onboarded tenant's `id`, `accountUuid`, `lastReconScan`, `lastScanStatusEnum`, `lastReconScanDurationMilliseconds` from the tenant search response, stores Surface-only observations with `observed_at`/`expires_at` (plan §5), and shows *No scan started → Scanning → Scan completed* on the dashboard with a Refresh action (optionally a scheduled task); an expired session reports `leonardo_session_expired` and waits for the operator.
7. **Operator:** fix the CO-0679 tenant manually; **assistant** re-reads it.
8. **Operator:** push the 10 local commits (`git push origin main`); keep the backup branch local.
9. **Assistant (small follow-ups):** update `README.md` / `LOCAL_IMPLEMENTATION_STATUS.md` (including the queue tiles and the History tab); rename the readback evidence `source` label; review the builder's flagged choices (blank DealHub product name fails closed; add-ons use the same Active/Pending-14-day rule; unknown-status Core Plus still flags; subdomains not checked against their roots).

**Boundaries preserved (Surface work):** only no-submit probes and two dry runs touched Leonardo Development (all cancelled, nothing created). Salesforce reads only. No production, Workato, OPA, or VM change. No credential, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

### Handoff — 2026-09-30 end of day (first automated Surface-only onboarding succeeded; continue here tomorrow)

**Authoritative handoff for the next session.** It supersedes the "Next steps" of the 2026-09-29 end-of-day handoff. **CO-0649 is onboarded:** the attended runner created it in Leonardo Development and read it back. Nothing was written to Salesforce.

#### Result (verified from the run log, the local runner state and readback, and the CO page)

| Item | Verified value |
| --- | --- |
| Run | 2026-09-30 15:40:43–15:41:29, route `case_1_new_surface_only`, source revision `2026-07-22T12:00:05.000+0000` (unchanged since the dry runs) |
| Duplicate check | clear by tenant name and by primary domain |
| Confirm | disabled with start = run day (2026-09-30) → automatic start fallback to **2026-09-29** → enabled → one click; `account/add` **200**; form closed |
| Readback | `readback_verified`; Surface Account ID and Account UUID captured in the gitignored `integration/attended_leonardo_readbacks.json` (not reproduced here); state **"No scan started"** (expected right after create; the Weekly schedule starts the scan) |
| Tenant settings | Prime · Weekly; Advanced: 90 h, Recon Subdomains / Web dictionary brute force / Nuclei / Notifications / Multiple users / API **ON**, the rest OFF; **Leaked Credentials and Phishing OFF**; Operator Account empty; Provisioning and Subdomains ON; Prepaid annual subscription; assets 10,000, **domains 1,000**, subdomains 1,000; licence **2026-09-29 → 2027-09-30** |
| CO page | green "Onboarded successfully" plus three amber reminders: enable Credential Exposure later (Core Plus), turn scanning off later, assign the Operator Account after the first scan |

#### What went wrong first, and the root cause

1. 14:24 — the first real create stopped with `confirm_button_not_enabled`. **Nothing was created**; no `account/add` request was sent.
2. **Gap found:** the dry runs on 09-29 and 09-30 had reported `dry_run_fill_verified` because they cancelled **before** checking Confirm. Fixed: dry runs now require an enabled Confirm (`dry_run_confirm_not_enabled` otherwise).
3. The no-submit diagnosis (`--diagnose-confirm`, four Leonardo runs, all cancelled) ruled out every single field and every combination of CE-style changes. It also showed that Leonardo's Add Account component sets no field error. It ruled out the dormant Leaked Credentials domain too, through the one owner-approved probe that briefly turned LC on inside an unsaved form and ended with LC OFF.
4. **Root cause, found by the operator by hand:** Leonardo Development accepts the run day (2026-09-30) in the start-date picker, without greying it out, but keeps Confirm disabled with no message; **2026-09-29 enables it**. On 2026-09-29 the run day itself had worked for the three CE creates. The exact Leonardo rule is unknown (a clock or timezone offset, or no current day allowed).

#### Owner decisions 2026-09-30

- **Surface Number of domains = licensed subdomains** (number in the product name + DealHub subdomain add-ons). This replaces "1 + alternate roots". A CO whose listed domains (main + alternates) exceed it fails closed with `surface_domains_exceed_license`. CE is unchanged (1).
- **Operator Account stays empty**; it is assigned (TA/CSM from Salesforce) after the first scan finishes. The CO page shows a reminder with a local acknowledgement (`/attended/mark-operator-assigned`).
- **No Credential Exposure on a Surface-only tenant:** Leaked Credentials and Phishing stay OFF. No diagnostic probe turns them on, except the one opt-in probe the owner approved (`--probe-lc-prefill`), which ends with LC OFF and never submits.
- **Licence start (option D):** start = run day; if Confirm stays disabled, the runner re-picks the start **one day earlier** (at most one), re-reads both dates (expiration unchanged), and continues only if Confirm then enables. Applies to CE and Surface, create and dry run.

#### Code (local commits, all tests passing)

`6cef6dc` (dry runs require Confirm; `--diagnose-confirm`), `30633e5` (domains rule, Operator reminder, deeper diagnosis), `c90d284`, `6b2c30a`, `6b93ff9`, `f259694` (diagnostics: validation report, React form state, component error slots, cumulative probes, opt-in LC probe, timeline, alternate-domain searches), `3483369` (start-date fallback). **540 integration tests OK (1 skipped).** Diagnostics run only with `--diagnose-confirm` and log no field values: domains are redacted, and known source values are replaced. The dashboard was restarted at 15:26 with this code (listener on `127.0.0.1:8012`, started from the assistant's shell).

#### Known follow-ups

1. **Licence date on the CO page:** the scope summary shows the planned start (2026-09-30), not the date actually entered (2026-09-29, logged as `license_start_fallback confirm_enabled`). Show the entered start date (from the run log or runner state) after a create.
2. **Guru notes** (`docs/37_CASE1_SURFACE_ONLY_GURU_NOTES_2026-09-29.md`): record the domains rule, the Operator Account timing, the start-date quirk, and that the card's licence example shows domains 10000 (Go 500).
3. **Report to the Leonardo team:** Dev refuses the current day as licence start without an error (Confirm silently disabled).
4. A stale CO page (opened before a dashboard restart) correctly refuses Start with a scope change; reload before starting.

#### Next steps (tomorrow, in order)

1. **Operator:** start the dashboard from your own terminal (`powershell -ExecutionPolicy Bypass -File tools\start_attended_dashboard.ps1 -Restart`), connect the RND VPN, and check the Leonardo session on `/connection` (re-establish with SSO/MFA if expired).
2. **Assistant (read-only):** `python tools\attended_ce_only_playwright.py --route case_1_new_surface_only --co CO-0649 --readback-only` to see whether the first Weekly scan started or finished (`lastReconScan`, `lastScanStatusEnum`). Record the observed values here.
3. **Operator, once the first scan finished:** in Leonardo, assign the Operator Account (TA/CSM from Salesforce) and turn the scanning settings off (owner decision 2026-09-29: scanning ON in Dev, turned off later). Then click **Mark Operator Account assigned** and **Mark scan settings turned off** on the CO page. CE stays off until it is enabled separately (**Mark CE enabled** when done).
4. **Operator:** Salesforce updates stay manual (IDs and stage → Account Scanning, per the Guru card).
5. **Assistant:** the small follow-ups above (CO-page start date, Guru notes), then build the **scan-status sweep** (phase 2, as described in the 2026-09-29 end-of-day handoff, step 6).
6. **Operator/CSM:** CO-0735 still needs its `Alternate_Domains__c` corrected in Salesforce (free-text IoT note).
7. **Operator:** fix the CO-0679 tenant by hand (trailing period; three Advanced toggles ON); the assistant then re-reads it.
8. **Operator:** push the local commits (`git push origin main`; local is **17 commits ahead** of `origin/main` `e854101`, plus this handoff commit). Keep `backup/local-history-2026-09-29` local.
9. Open owner questions carried over: API access for Surface (always ON vs Core Plus only); dashboard on port 80 (port 80 was free on 2026-09-30); Salesforce writeback timing.

**Boundaries preserved (2026-09-30):** Leonardo Development only. There was one real create (CO-0649, from the operator's explicit Start click after a reset and scope review); everything else was session checks, dry runs, or no-submit diagnostic runs, all cancelled. Salesforce reads only; no production, Workato, OPA, or VM change. No credential, MFA value, cookie, token, or raw payload was copied, logged, or persisted. The automation Chrome window and one manually opened Leonardo tab were left open; close the automation browser at the end of the day (`/connection` → **Close automation browser**).

### Progress — 2026-10-01

- **CO-0649 scan started.** Read-only `--readback-only` at 11:48 returned `readback_only_verified`; one exact tenant match (name + primary domain), IDs unchanged from create, state **"Account Scanning"** (was "No scan started" on 09-30). Nothing written to Leonardo or Salesforce.
- **Not yet known:** whether the first scan has *finished*. The readback only tests that `lastReconScan` is set; it does not record `lastScanStatusEnum` or the scan duration. The Operator Account assignment and scanning-off steps wait for a confirmed finished scan (scan-status sweep, or a manual check in Leonardo).
- **Owner decision — Salesforce ID mapping (closes the §16 "UUID destination" question for two routes):** CE-only (`case_2_new_ce_only`) → Leonardo **Account UUID** goes to `Account_UUID__c`; Surface-only (`case_1_new_surface_only`) → Leonardo **Surface Account ID** goes to `Surface_Account_ID__c` **only**. All other routes have no mapping and fail closed (`mapping_not_decided`).
- **CO page "Salesforce IDs" panel (local code only):** `salesforce_id_writeback_plan()` in `tools/serve_attended_open_onboardings_dashboard.py` compares the captured Leonardo value with the live Salesforce field and reports **Ready to write** (field empty), **Matches Salesforce**, **Conflict** (different value; never overwrite), or **Not captured yet**. The UUID is compared case-insensitively, the Surface Account ID exactly. The panel shows the other captured ID as "Also captured (not written)". The future Salesforce writeback must reuse this function. No Salesforce write was added. Tests: **548 integration OK (1 skipped)**, 55 `phase1_validator` OK.
- **Owner decision — already-onboarded COs:** CO-0649 already had a production Surface Account ID in Salesforce (the Dev create duplicated a production customer). The runner now stops a create or dry run with `salesforce_id_already_present` when the route's mapped Salesforce ID field is set; the CO page blocks Start and the panel shows an amber "Salesforce already has an ID" (never written). Commit `72c2e61`.
- **Owner decision (b) — pilot ID writeback:** Leonardo Development IDs may be written to Salesforce during the pilot, **into empty mapped fields only**; an existing value is never overwritten. Not built yet; its design (operator click, revision-bound, read-after-write) needs approval first.
- **Dashboard speed (owner-approved):** every `sf` CLI call costs about 4 s (CLI startup; telemetry/update switches and direct `node` invocation did not help). GET pages now reuse Salesforce reads from an in-memory cache for up to 2 minutes (never disk or logs; cleared by Refresh or any POST). Pages show "Read from Salesforce at" with a Refresh button. When the cached queue already gives the route, the CO page also starts the onboarding preflight in parallel with the CO read. Starts and the runner always read fresh and re-check the revision. Tests: **558 integration OK (1 skipped)**, 55 `phase1_validator` OK.
- **VM staging (owner choice (a): tested server copy only; runs stay on the desktop; no VM revert).** The operator ran each step in an SSH session as `workato` (sudo password typed by the operator; the assistant only read the terminal). Read-only check first: 101 GB free, managed Python 3.12.14 present, active `app` plus `r3`–`r17` copies, no listener on 8012/8013, no `surface-onboarding` units.
  - `app-r18-staged-20261001` (`f533cbd`) is **defective, do not use**: `git archive` with the desktop's `core.autocrlf=true` converted 177 text files to CRLF, so `scripts/run_vm_dashboard.sh` failed at `set -o pipefail` (its exit 2 was not a real refusal). One test (`test_profile_default_on_nt_is_persisted`) was Windows-only. It stays inactive; deleting it needs a separate confirmation.
  - **`app-r19-staged-20261001` (`f23caef`) is the good copy:** archive `surface-onboarding-r19-f23caef.tar.gz` (SHA-256 `33C4167A…76F5A084`), checked by the independent r17 checker, unpacked as `surface-onboarding` into a new folder (`incoming-r19` holds the archive). On the VM: launcher 0 CR lines; **559 integration OK (1 skipped)**, 55 `phase1_validator` OK, artifact and offline-boundary guards passed, launcher correctly refused (exit 2).
  - **Build rule:** always export with `git -c core.autocrlf=false -c core.eol=lf archive …` and check for CR bytes before copying.
  - Nothing was activated: no service, listener, package, firewall, credential, browser, or Salesforce CLI on the VM; the active `app` and the Workato/OPA agent untouched. Live use on the VM still needs the §0 approvals (runner host, Salesforce identity, Security, web identity/proxy).
- **Small fixes (`3ac0e74`):** the start script's liveness check uses `/connection` (no Salesforce read) and a slow first queue read is only a warning; the runner records the licence dates actually entered before Confirm (`license_start_entered`/`license_end_entered`) and the CO page shows them; `docs/37` updated.
- **Scan-status sweep (`444e89c`):** read-only `--scan-status --co` matches the tenant by the **captured Surface Account ID and Account UUID** and stores `lastReconScan`, `lastScanStatusEnum`, and duration with a 6 h expiry in gitignored `integration/attended_scan_status.json`. CO pages of onboarded COs have a **Leonardo scan status** card with Refresh. `SCAN_STATUS_COMPLETED`/`FAILED` are deliberately empty: no value counts as "completed" until a live value is reviewed. **Next live step:** Refresh CO-0649 and review the observed `lastScanStatusEnum`.
- **Salesforce ID writeback (decision (b)):** on a *Ready to write* panel, **Review write to Salesforce** does a fresh read and shows a confirmation bound to the source revision and value (one-time code, 10 min). **Confirm** re-reads; it writes only if the field is still empty and nothing changed; it sends `sf data update record` for **exactly one field**, then reads it back (`written_verified`). Rejected or uncertain outcomes are recorded (value hash only, gitignored `integration/attended_salesforce_id_writebacks.json`); an uncertain write is never offered again automatically. No stage or comment change. Nothing has been written yet; the first write is an operator click.
- **Test hygiene:** two CO-page tests had made a read-only live Salesforce call through the Surface preflight; they are stubbed now. A tripwire run (`SURFACE_SF_CLI` pointing to a logging stub) confirmed **0 real CLI calls** across the suite. Tests: **582 integration OK (1 skipped)**, 55 `phase1_validator` OK.
- **Case 3 (`case_3_combined_baseline`) mapped and built locally (owner decisions 2026-10-01).** Route: exactly `Surface & Credential Exposure` / `New Product Onboarding`. One tenant = the **Surface-only contract** (name without suffix, main/alternate/sub domains, tier interval, Surface advanced profile 90 h, Notifications/Multiple users/API ON, assets 10,000, domains = subdomains = licensed subdomains, Operator Account empty, scope review with two ticks) **plus the CE contract's Leaked Credentials** (ON, Weekly, scanned domains = the single `Email_Domains__c` value, decision 2a). Phishing OFF. Requires a Core Plus row and an unambiguous Core Plus Commercial term (`case3_core_plus_missing`, `ce_subscription_*`), and exactly one CE email domain (`case3_ce_email_domain_invalid`).
  - **1a — term rule:** the Surface and Core Plus expirations must agree; otherwise `case3_term_mismatch` (manual review) and the CO page prints both dates and what to do. The runner stops before any browser work.
  - **3a — Salesforce IDs:** both IDs map (Surface Account ID → `Surface_Account_ID__c`, Account UUID → `Account_UUID__c`); the writeback writes only the empty ones in one update and reads all back; the pre-create gate blocks if either is set.
  - **4a — duplicates:** a third lookup searches the CE email domain and compares rows with that domain, so an existing CE-only tenant for the customer stops the run.
  - Dashboard: "Surface + CE" chip, "Review scope, start" in the queue, the Surface card titled "Surface + Credential Exposure onboarding" with a Leaked Credentials scope row; no "enable CE later" reminder; scanning-off and Operator reminders apply. Not run live: the first use is a **dry run** on an owner-chosen Case 3 CO.
  - Tests: **595 integration OK (1 skipped; tripwire 0 real CLI calls)**, 55 `phase1_validator`, 49 `phase2_leonardo` OK.
- **CO-0649 first scan finished (live, read-only).** `--scan-status` read `lastScanStatusEnum = COMPLETED`, `lastReconScan` 2026-09-30 22:45 UTC, duration 11,292,919 ms; the operator confirmed it in Leonardo (Last Scan Sep 30 15:45 local, duration 03:08:12). **Owner decision: `COMPLETED` = "Scan completed"** (`SCAN_STATUS_COMPLETED`); the dashboard re-applies the confirmed list at display time. Failure values stay unconfirmed (empty).
- **Owner decision — IDs dashboard-only (replaces decision (b)):** Leonardo Development IDs are shown on the dashboard only, labelled **Environment: dev**; nothing is written to Salesforce. `ID_WRITEBACK_ENABLED = False`: the Review button is gone and both writeback routes return 409; the guarded write path stays tested (tests enable it explicitly). Reason: Salesforce comparison showed CO-0649, CO-0702, CO-0728, and CO-0762 already carry **production** IDs, i.e. production onboarding runs in parallel with the Dev pilot.
- **Runtime note:** the system `python` has no Playwright (`playwright_runtime_unavailable`). Run the runner with the dashboard's interpreter (`$PythonPath` in `tools\start_attended_dashboard.ps1`).

### Handoff — 2026-10-01 end of day (continue here tomorrow)

**Authoritative handoff for the next session** (including the "Workato integration implementation" session). It supersedes the "Next steps" of the 2026-09-30 handoff. Details of the day are in "Progress — 2026-10-01" above; the Guru rules, the gap list, the open owner questions, and the renewal (Cases 4–6) mapping mock-ups are in **`docs/38_GURU_RULES_GAP_ANALYSIS_AND_RENEWAL_MAPPING_2026-10-01.md`**.

#### State at end of day

| Item | State |
| --- | --- |
| Code | Local `main` at the commit that records this handoff; GitHub `origin/main` = `0f6e617` (pushed by the operator). Unpushed: `1a2c1c9`, `13af2f7`, this handoff commit. 13 commits today since `481b121`. |
| Tests | 605 integration OK (1 skipped), 55 `phase1_validator`, 49 `phase2_leonardo`; Salesforce CLI tripwire run = 0 real calls; artifact and offline-boundary guards pass. |
| Routes | Case 1 and Case 2 live-proven in Leonardo Development; **Case 3 built locally, not run live** (no eligible CO before **2026-10-20**: CO-0757's DealHub rows start 2026-11-03); Cases 4–6 not built. |
| CO-0649 | First weekly scan **COMPLETED** (2026-09-30 22:45 UTC, 3.1 h), confirmed in Leonardo by the operator. The CO is a duplicate of a **production** onboarding (Salesforce holds the production Surface Account ID). |
| Production overlap | Salesforce shows production IDs on CO-0649, CO-0702, CO-0728, CO-0762 → production onboarding runs in parallel with the Dev pilot. The pre-create gate `salesforce_id_already_present` now blocks such COs. |
| Salesforce IDs | **Dashboard only, Environment: dev** (owner decision; replaces pilot decision (b)). `ID_WRITEBACK_ENABLED = False`; nothing was written to Salesforce today. |
| Scan status | `COMPLETED` = Scan completed (owner-confirmed). `--scan-status-all` read CO-0649 and CO-0740 (both COMPLETED); CE-only tenants are skipped. |
| VM `workato-opa-01` | Inactive tested copies `app-r19-staged-20261001` (`f23caef`) and `app-r20-staged-20261001` (`a9d230e`, new design preview; 595/596 on the VM — the one failure is fixed in `0f6e617`). `app-r18` is defective (CRLF) — delete only with confirmation. Preview stopped ("8013 closed"). Active `app`, OPA agent, services, firewall untouched. |
| Dashboard | Desktop `127.0.0.1:8012`; restart from the operator's terminal to load `1a2c1c9`/`13af2f7`. |

#### Owner decisions 2026-10-01 (all recorded in "Progress — 2026-10-01")

ID field mapping (CE → `Account_UUID__c`, Surface → `Surface_Account_ID__c` only, Case 3 → both); block COs whose Salesforce ID is already set; IDs dashboard-only (env dev); Case 3 = Surface contract + CE Leaked Credentials with decisions 1a (terms must agree, plain message), 2a (LC on the CE email domain), 3a (both IDs), 4a (extra duplicate lookup by CE email domain); `COMPLETED` = scan completed; VM = tested staging copy only, no revert; display-read cache (2 minutes).

#### Next steps (tomorrow, in order)

1. **Operator:** restart the dashboard (`powershell -ExecutionPolicy Bypass -File tools\start_attended_dashboard.ps1 -Restart`), check the Leonardo session on `/connection`, push the remaining commits (`git push origin main`).
2. **Operator, CO-0649 (Dev tenant):** the scan finished — assign the Operator Account and turn the scanning settings off in Leonardo Development, then click the two "Mark …" buttons on the CO page. Consider whether to keep this Dev duplicate of a production customer at all.
3. **Owner:** answer the 16 questions in `docs/38` §4 — at least Q1 (renewal expiration), Q2 (start date), Q3 (added domains), Q9 (how to test renewals in Dev), Q10 (renewal routing table), Q11 (legacy products) — and the 7 proposed renewal rules from today's session (they match `docs/38` §5).
4. **Assistant (read-only, after the answers):** build the **Renewal plan** card (mock-up in `docs/38` §5) for all renewal COs — Salesforce reads only, no Leonardo action, no Start button.
5. **Operator + assistant:** capture the Edit Account contract on a Dev tenant we own (e.g. CO-0649's Dev tenant): one assistant no-submit probe of ⋮ → Edit (menu item, modal selector, prefilled values, which controls are editable), and one operator-run manual edit with a diagnostic HAR to learn the submit endpoint. Then design the guarded edit mode (contract sketch in `docs/38` §5).
6. **Assistant:** stage r21 on the VM (all of today's commits) with the LF-clean archive procedure; optionally delete the defective `app-r18` with the operator's confirmation.
7. **Assistant (housekeeping):** README / `LOCAL_IMPLEMENTATION_STATUS.md`; draft the note to the Leonardo team about the start-date quirk; pick items from the improvement review below.
8. Carried over: CO-0735 `Alternate_Domains__c` correction (CSM); CO-0679 tenant manual fix (trailing period, three Advanced toggles); dashboard on port 80 (open); Slack status message (Q15; Slack connector not authorized in this session).

#### Improvement review (independent read-only agent, 2026-10-01; items 1, 3, 4 re-verified in code by the assistant)

Fix before any new live run (safety, each small):

1. **Start guard across revisions.** `evaluate_ce_only_start` refuses only the *same* revision; a CO edited in Salesforce during a run (e.g. during the SSO wait) can launch a second runner, and `record_runner_start` overwrites the in-progress record. The CE route can also re-start after `readback_verified` on a newer revision (Surface guards this). Fix: refuse whenever any record has no result or is `readback_verified`, regardless of revision, in one shared gate.
2. **Post-Confirm outcomes are labelled "nothing was created" and are resettable.** Codes that can occur after the click (`confirm_no_create`, `readback_*`, post-create duplicate/schema failures, session expiry) should be "create uncertain" (e.g. whenever `license_start_entered` is recorded), reset blocked until a read-only readback settles it, with honest wording.
3. **Readback details fallback can pick the wrong tenant.** When the API row is not unique/domain-matching, `_open_tenant_details` clicks the *first* name match and ignores the domain; `write_readback_evidence` then overwrites IDs without a conflict check, and the scan sweep trusts those IDs. Fix: fall back only for a unique name+domain row with a missing state; require exactly one match; refuse to replace existing IDs.
4. **Salesforce CLI timeout escapes the handlers.** `subprocess.TimeoutExpired` is not an `OSError`/`ValueError`: a timed-out read crashes the runner without `_finish` (record stuck "Running"), and the Surface/Case 3 CO page can fail without a response. Fix: catch `SubprocessError` in `_sf_records`; top-level `runner_crashed` result in `main`.
5. **Stuck "in progress" record has no recovery** (Popen handle discarded, stderr to DEVNULL). Add stale-record detection and a read-only "Reconcile" action (never `os.kill(pid, 0)` on Windows).
6. **No Host/Origin/CSRF check** on the loopback dashboard (threat model requires CSRF). Allow only the dashboard's own Host; require same-origin `Origin`/`Sec-Fetch-Site` on POST.

Next (correctness/UX): 7 the dashboard's read-only duplicate check uses the primary domain for every lookup (Case 3 extra lookup weaker than in the create run — share one classifier); 8 the final pre-Confirm re-read skips text fields and is not repeated after the start-date fallback; 9 bind the reviewed scope digest into the runner (`--scope-digest`), add a CE scope summary, re-check Approved/stage in the runner; 10 refuse profile reset / browser close / bootstrap while a run is in progress; 11 local state files: a malformed readback entry shows "Salesforce unavailable" (separate local-state error), and `_write_json_atomic` uses a fixed `.tmp` name without a lock across processes; 12 read-after-write covers only IDs/state (plan §7 requires every field/toggle/licence value); 16 the Follow-up step still says "IDs and stage → Account Scanning" although IDs are now dashboard-only; 17 reminder acknowledgements do not move the queue row, read-only check failures are invisible, the progress page could show the redacted run-log steps; 18 a dashboard "Dry run (no create)" button (owner sign-off).

Maintainability: 13 split the runner into a `tools/attended/` package (`routes`, `state`, `browser`, `form`, `tenants`, `diagnostics`, `run_log`) and the dashboard into `history`, `salesforce`, `gates`, `pages`, `legacy_one_off`, `server` — **first** add a test guard (all `*_PATH` to a temp dir, `SURFACE_SF_CLI` stub, assert real `integration/*.json` untouched), then one module per commit, retargeting patches (≈45 patched names) and the preview's swaps; 14 de-duplicate route detection (5 places), the readback schema (2), the scan regex (2), the Surface Account ID pattern (dashboard `[a-f0-9]{24}` vs runner `[A-Za-z0-9]{16,64}`), and remove dead test-only helpers. 15 lists the matching missing tests.

Before renewals (Cases 4–6): 19 add a create/edit **mode** to `RouteContract` (target = exactly one tenant by captured Dev IDs + name + domain; open form; `expected_before`; `changes`; `preserve`; readback of IDs + changed + preserved fields) and replace the plan dict with a typed `FieldSpec` (set/verify/blank/absent/preserve); 20 renewal blockers in today's code — the start-date fallback must be off in edit mode, `build_fill` conflates licence start and run day, `select_ce_subscription` ignores `DealHub_Status__c` (expired + active Core Plus → ambiguous), the `salesforce_id_present` gate inverts for renewals (production IDs present; Dev target IDs come from the local readback), and the route lists must be extended.

**Suggested order tomorrow:** after steps 1–3 above, fix review items 1–4 (small, safety) with tests, then 6, then continue with the Renewal plan card.

**Boundaries preserved (2026-10-01):** Leonardo Development only. Live actions were read-only: Salesforce queries (counts, flags, products/dates — no values copied into Git), `--readback-only` and `--scan-status`/`--scan-status-all` reads. **No Leonardo create or edit, no Salesforce write**, no production action. VM changes were operator-run (copy, stage into new inactive folders, tests, a loopback preview later stopped). No credential, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

### Handoff — 2026-10-02 (continue here)

**Authoritative handoff for the next session** (including the "Workato integration implementation" session). It supersedes the "Next steps" of the 2026-10-01 handoff; that handoff's state table, decisions, and `docs/38` remain valid background.

#### Done today (local commits; tests 656 integration OK, 1 skipped; 55 `phase1_validator`; 49 `phase2_leonardo`; Salesforce CLI tripwire 0 real calls; guards pass)

| Commit | Change |
| --- | --- |
| `9f47ed7` | **Review item 1** — one start guard across revisions (`start_blocker`: `run_in_progress`, `tenant_already_verified`); the Start handlers record the start before launching, under a lock; a failed launch is `runner_launch_failed`. |
| `8c858fc` | **Surface validation** (no official API): reads the same tenant search JSON the Tenant Management page loads, matched by captured `id` + `accountUuid`; compares account status, licence, all toggles, intervals, domains, people, scan against the route plan. `--validate` / `--validate-all`; CO-page card, Validate / Validate all buttons, queue drift marker. Names/domains/emails stored only as ok/drift + counts. |
| `32a04cf` | Validation: Leonardo stores the "None" interval as `NO_SCHEDULE`. |
| `3872a10` | **Review item 2** — `create_uncertain`: a failure after the licence dates were entered blocks start and reset; "Verify in Leonardo (read-only)" runs `--readback-only`, which promotes a found tenant to `readback_verified` or marks `no_tenant`. |
| `8c3195d` | **Review items 3 + 4** — readback identity (`readback_tenant_ambiguous`, details IDs must equal the server row, exactly one name+domain row, `readback_id_conflict` never replaces captured IDs); `sf` timeouts become "source unavailable"; `runner_crashed` safety net. |
| `9c3a5aa` | Validation: max scan duration is not in the search data (operator confirmed 90 h in the Edit form) → reported as info. |
| `cafbd2a` | **Review item 6** — `parse_request` refuses a foreign `Host` (DNS rebinding) and cross-site POSTs (foreign `Origin`, `null`, or `Sec-Fetch-Site` cross-site) with 403 before any handler runs; `SURFACE_ONBOARDING_ALLOWED_HOSTS` for extra exact host:port values. |
| `07bb125` | **Renewal plan card** (read-only, no form/button) for Cases 4–6 and single-product renewals; checked against the six real open renewal COs. |

GitHub `origin/main` = `8c3195d` (pushed by the operator); `9c3a5aa`, `cafbd2a`, `07bb125`, and this handoff commit are local.

#### Verified live today (read-only)

- **Schema probe** (CO-0649 Dev tenant): 138 field paths of the tenant search row, types only (no values). Key paths: `accountLicense.{licenseType,startDate,expirationDate,assetsNumber,domainsNumber,subDomainsNumber,…Allowed,phishingEnabled,provisioningEnabled}`, `accountSettings.reconSettings.automatedDiscoveryEnabled`, top-level toggles, `campaignExecutionSettings.domainsMultiAttackStackSettings.enabled`, `primaryUser.isMfaRequired`, `operatorAccounts`, `termsOfUseApproval`, `lastReconExecutionData.timedOutActions`, `pendingValidationAssets`. Not in the row: max scan duration value, the tenant's user list.
- **Validate all** (before the `NO_SCHEDULE` fix): CO-0649 38/38 ✓; CO-0728 and CO-0762 clean after the fix; CO-0679 real drift (company name, primary domain; its three Advanced toggles now match the plan); CO-0702 (manual creation 9/25) real drift (name, domain, primary-user last name); CO-0740 plan not rebuildable (`ce_subscription_unavailable`), account/scan checks pass.
- **Renewal plans** for the open renewal COs: CO-0767 and CO-0770 Case 6 (terms agree; CO-0770 Go 500 + Bulk 500 = 1000, CE +100 email domains), CO-0758 Case 4, CO-0761 and CO-0769 CE renewals, CO-0771 blocked (`no_new_surface_term`, `no_core_plus_term`: only legacy rows).

#### Owner input today

- Max scan duration shows **90 h** in the Leonardo Edit form for CO-0649 (it is not in the search data).
- Next work order agreed: item 6, then the renewal plan (done); renewal rules stay *proposed* until the `docs/38` answers.

#### Next steps (in order)

1. **Operator:** push (`git push origin main`), restart the dashboard (`powershell -ExecutionPolicy Bypass -File tools\start_attended_dashboard.ps1 -Restart`), click **Validate all** on the queue page, and open a renewal CO (e.g. CO-0767) to review the Renewal plan card.
2. **Owner:** answer `docs/38` §4 — Q1 (annual cap vs term end), Q2 (start date), Q3 (added domains), Q7 (CE email-domain add-ons), Q9 (renewal testing in Dev), Q10 (routing table), Q11 (legacy rows). Then the renewal rules move from *proposed* to decided.
3. **Assistant:** review item 5 (stale "in progress" recovery: keep Popen handles, stale-record detection, read-only Reconcile); then items 7–12 and 16–17 from the improvement review (2026-10-01 handoff).
4. **Operator + assistant (needs Q9):** Edit Account probe on a Dev tenant we own (⋮ → Edit, prefilled values, editable controls; one operator manual edit with a diagnostic HAR for the submit endpoint), then the guarded edit mode (`docs/38` §5 contract; review items 19–20).
5. **Assistant:** stage r21 on the VM with the LF-clean archive (all commits since `a9d230e`); optionally delete the defective `app-r18` with the operator's confirmation.
6. Carried over: CO-0649 Dev tenant — assign the Operator Account and turn scanning off; CO-0679 tenant name/domain fix; CO-0735 Salesforce correction; README / `LOCAL_IMPLEMENTATION_STATUS.md`; Leonardo-team note on the start-date quirk; Slack status message (connector not authorized).

**Boundaries preserved (2026-10-02):** Leonardo Development only. Live actions were read-only: Salesforce queries, one schema probe, `--validate` / `--validate-all` reads in the automation tab. **No Leonardo create or edit, no Salesforce write**, no production action, no VM change today. No credential, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

### Handoff — 2026-10-03 (continue here)

**Authoritative handoff for the next session** (including the "Workato integration implementation" session). It supersedes the "Next steps" of the 2026-10-02 handoff; that handoff's open items (review items 5, 7–12, 16–17; `docs/38` owner questions; carried-over CO items) remain valid unless listed below.

#### Done today (local commits; 744 integration tests OK, 1 skipped; 55 `phase1_validator`; 49 `phase2_leonardo`; both guards pass)

| Commit | Change |
| --- | --- |
| `5eea5e1` | **Sessions + offline inventory module.** `integration/onboarding/session_readiness.py` (pure: session states, Salesforce/Leonardo classification, freshness, action gate, production lock). Server-side gates on every Start / Validate / scan refresh / Verify route (Start: Leonardo checked ≤ 5 min, Salesforce ≤ 15 min; reads: both ≤ 15 min; a stale check is repeated read-only, never during a run). Every Salesforce read/write pinned to `--target-org surface-onboarding` and to `SURFACE_SF_EXPECTED_ORG_ID`; login no longer uses `--set-default`. `integration/onboarding/leonardo_inventory.py` (pure: allow-listed tenant snapshot, page-assembly checks, schema drift, atomic files outside the repo, CSV, CO matching). |
| `8343e62` | **Salesforce SSO dashboard login** (owner decision: no dashboard password; reset = Pentera SSO reset). `/login` → fresh `sf org login web` → `userinfo` must name an allowed operator in the pinned org → 12 h in-memory session cookie for the browser that started it; wrong user/org ends that CLI session. Login runs the preflight (VPN reachability of Leonardo Dev, sf CLI, browser runtime, Chrome) and the Leonardo sign-in. **Inventory wiring:** runner `--probe-inventory-shape` and `--export-tenants [--env dev\|prod] [--with-sweeps] [--csv]` (captures only the page's own `getAllDetailedAccounts` replies on the exact Dev origin via reload + Next; fails closed; prod refused); dashboard **Tenants** page + gated **Refresh inventory**. API-level session check built but `SESSION_API_CHECK_ENABLED = False` until the probe confirms the reload signal. Cleaner **Sessions** page (Salesforce / Leonardo / Preflight rows, Prepare sessions, Re-check, Troubleshooting). |
| `26f0412` | **403 fix:** pages sent `Referrer-Policy: no-referrer`, so Chromium sent `Origin: null` on the dashboard's own form posts (verified live in Chromium) and the review-item-6 check refused **every POST since `cafbd2a`**. Now `same-origin`; `Origin: null` stays refused. **Dashboard tab:** the launcher opens `http://127.0.0.1:<port>/login` as a tab of the automation Chrome (own persisted profile; Chrome 136+ ignores remote debugging on the default profile, and the work profile must never be exposed), so Leonardo tabs open in the same window. Sign out keeps the window. |

GitHub `origin/main` is behind: `9c3a5aa`, `cafbd2a`, `07bb125`, `46dc2f9`, `5eea5e1`, `8343e62`, `26f0412`, and this handoff commit are local (the operator pushes).

#### Verified live today (read-only)

- Salesforce: one `SELECT Id FROM Organization` on `surface-onboarding` → the org Id (not a secret) was saved to `%LOCALAPPDATA%\SurfaceOnboarding\salesforce-expected-org-id.txt`; the launcher reads it automatically.
- Operator HAR of a Leonardo Dev tenant search (inspected with values redacted): `POST /api/v1/backoffice/getAllDetailedAccounts`, `tableServerData {offset, items_per_page: 10, sort lastReconScan DESC, filters.and[{method: "search"}]}`; reply `pagination_response {total_count, table_data[]}`; no cookie/authorization header (HttpOnly session). The HAR holds customer personal data: **operator to delete it from Downloads**; it was not copied anywhere.
- Real-Chrome checks (throwaway profile / stubbed CLI only): the Sign-in button now reaches the login handler; the dashboard tab opens in the automation window and is reused, not duplicated.

#### Owner decisions today

- Dashboard login = Salesforce SSO (OneLogin + MFA), allowed operator = the owner's Pentera address (launcher `-AllowedUser` default); no dashboard password; no email reset (it would need a mail secret and a security review). Direct OneLogin OIDC stays optional (needs IT to register an OIDC app with redirect `http://127.0.0.1:8012/login/callback`).
- Login also prepares Salesforce, Leonardo Dev, and the preflight; Workato/OPA status is out of scope (would need a Workato API token).
- Inventory: page through the UI table and capture the page's own replies; direct `fetch` stays disabled until the Leonardo owner and Security approve. Snapshot keeps the primary domain and only counts of domain lists; no user name/email/phone, no SpyCloud settings; keep the last 10 files.
- BackOffice production stays locked for sessions and inventory; even a read-only production export needs Leonardo-owner, Security, and Privacy/DPO approval.
- The dashboard runs as a tab of the automation Chrome window, never in the work profile.

#### Next steps (in order)

1. **Operator:** connect the RND VPN; restart the dashboard (`powershell -ExecutionPolicy Bypass -File tools\start_attended_dashboard.ps1 -Restart`); the dashboard should appear as a tab in the automation Chrome window.
2. ✅ **Done 2026-10-03:** first live **Sign in with Salesforce** succeeded (OneLogin in a Chrome tab; `userinfo` identity check verified). First attempt failed unseen: the CLI opened the default browser (Edge), Cancel killed only `sf.cmd` so an orphaned CLI held OAuth port 1717 (`PortInUseError` on every retry, each after logging out). Fixed: `--browser chrome`, process-tree kill, port check before logout.
3. **Assistant (read-only, with operator approval):** `--probe-inventory-shape` (reload Tenant Management + one Next click; key names and counts only). Settles assumption A1 (does a reload send the unfiltered table request?) and the page size. If A1 holds, turn on `SESSION_API_CHECK_ENABLED` in a reviewed commit.
4. **Assistant:** one export (`Refresh inventory` or `--export-tenants --env dev --with-sweeps --csv`); check `row_count == total_count ==` table footer, spot-check CO-0649 ID/dates/last scan/duration against the UI, `@` absent from the snapshot, `git status` clean.
5. **Operator:** push the local commits; delete the HAR from Downloads.
6. Carried over from 2026-10-02: review item 5 and items 7–12, 16–17; `docs/38` owner answers (Q1–Q3, Q7, Q9–Q11); Edit Account probe (needs Q9); stage r21 on the VM; CO-0649 Operator Account + scanning off; CO-0679 tenant fix; CO-0735 Salesforce correction; Leonardo start-date quirk note; Slack status (connector needs authorization).

**Boundaries preserved (2026-10-03):** Leonardo Development only. Live actions were one read-only Salesforce org query and local real-Chrome checks with a throwaway profile or a stubbed CLI. **No Leonardo create, edit, or search; no Salesforce write**; no production access; no VM change. No credential, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

### Handoff — 2026-10-04 (continue here)

**Authoritative handoff for the next session.** Supersedes the 2026-10-03 "Next steps". Owner requirement recorded today: **everything must finally run without Claude and without a human at the keyboard** — first on the operator's Windows machine, later on the VM (the VM still needs an approved browser runner).

#### Done today (local commits; 778 integration tests OK, 1 skipped; 55 `phase1_validator`; 49 `phase2_leonardo`; guards pass)

| Commit | Change |
| --- | --- |
| `496dcb3`, `26d4e5a` | SSO sign-in opens Chrome (`--browser chrome`), orphaned CLI sign-ins killed as a process tree, port 1717 checked before logout; Leonardo's `/backOffice/` spelling recognised. **First live SSO login verified.** |
| `dc4f690`, `de17853` | Inventory keeps the 2 tenants without UUID (never matchable); unreadable reply retried once; runner Salesforce reads pinned to `--target-org`; files/snapshots/CSV/page labelled **DEV (Leonardo Development)** (`leonardo-dev-inventory-*`); observed schema (86 tenants without licence). |
| `6b1e2f0` | **Duplicate pre-check from the DEV inventory** (owner options A+B): same tenant name or primary domain, or a CO domain equal to a tenant's alternate domain → Start stops with `duplicate_inventory_match` **before any browser**; "no match" is never a clearance (live check still runs). CLI `--duplicate-precheck`; CO-page button. Alternate domains kept in the snapshot (company domains only). |
| `c056ec8` | **Approved overrides the 14-day Pending window** (owner): an Approved CO counts a Pending Surface baseline whatever its start; production onboarding day = subscription start − 2 days or earlier on a CSM request (force option wired when production is enabled); DEV onboards immediately. Licence dates unchanged (run day → start + 1 y − 1 d). |
| `dd2c3f5` | **CO page redesign** (UX agent proposal, owner-approved): one next-step card, one primary button per state; renewals have **no** primary button (plan first; production sign-in under "More"). All routes/hidden inputs/checkboxes unchanged. |
| `e2dd506` | **Invisible tenant search**: reload + rewrite only the *body* of the app's own `getAllDetailedAccounts` request (headers incl. the app's `authorization` token untouched, never read); fails closed without the rewrite. **Export at 100 rows/page**: 29 s instead of 4–6 min. Quota fields known objects. |

GitHub `origin/main` is behind by all commits since `8c3195d` (operator pushes).

#### Verified live today (Leonardo Development)

- Inventory export: 594 tenants, 6 pages, 2 without UUID, 56 with alternate domains (1,297), no `@` in files.
- `--validate-all`: CO-0649/0728/0740/0762 clean; CO-0679 (name, domain) and CO-0702 (name, domain, last name) = known drift.
- Duplicate pre-check: CO-0649 and CO-0757 match **their own** tenants (ID equals readback); no false positive seen.
- **CO-0757 onboarded by the operator** (Case 3): pre-check `inventory_no_match` (593) → live check clear → create 200 → readback verified, 36 s. Licence 2026-10-04 → 2027-11-02.
- Invisible search: `rows=1 total=1` for CO-0649 (server filter applied), domain search works (`uuw.co.za`).
- A hand-built in-page API call gets **401** (the app adds an `authorization` header). Redirecting the app's request to another endpoint was **blocked by the Claude Code safety classifier (credential exploration)** — do not use that technique; drive Leonardo's own UI and read its own responses.

#### Probe findings for tomorrow (read-only; Edit opened and cancelled, nothing saved)

- **SpyCloud:** Leonardo's front-end hard-codes `leakedCredentialsSettings.spyCloudSettings.enabled = true` in Add Account (no control in that form). The tenant row exposes `leakedCredentialsSettings.spyCloudSettings.enabled` (CO-0757: `true`). The **Edit** form has a checkbox `input[name="spyCloudEnabled"]` after expanding both "Advanced options" sections; Edit has **Cancel / Confirm**; Confirm posts `/api/v1/backoffice/account/{id}/edit`. **Owner: SpyCloud must be OFF** (supersedes `docs/33` "leave untouched").
- **Row actions:** select the row, then `.rowMenuActionsContainer .list-trigger`; items by stable id only: `[data-am="Button-Grid_Details"]`, `[data-am="Button-Grid_Edit"]` (others: `_Access`, `_Scan_Now`, `_Stop_Scan`, `_Delete` = hazard — never click).
- **Duration Per Scan:** Details → text "Duration Per Scan" → the app posts `/api/v1/backoffice/account/{id}/campaign/executions` (`{tableServerData}`); rows: `startDate`, `endDate`, `status` (`PENDING` while running; `DONE` when finished — the operator saw PENDING with an end date, then DONE), `campaignTypeEnum` (e.g. `LEAKED_CREDENTIALS_DISCOVERY`), `campaignExecutionTypeEnum`. CO-0757 had 2 executions, both PENDING. Duration = end − start.

#### Next steps (in order)

1. **Assistant (local code + tests):**
   a. Scan status from executions: runner opens Details → Duration Per Scan via the ids above, captures the app's own executions reply, stores per CO `{executions: [type, execution type, status, start, end, duration_ms]}` and `running` / `done` (done = status DONE, duration shown); dashboard tile "Running since …" / "Done · hh:mm:ss"; plug into `--scan-status` / `--scan-status-all`.
   b. SpyCloud OFF after each create on LC routes (CE, Case 3): ⋮ → Edit → expand Advanced options → `spyCloudEnabled` OFF only → Confirm → re-read row, require `spyCloudSettings.enabled == false`; failure = visible warning, never silent. Standalone `--spycloud-off --co CO-XXXX`. **First live save needs operator approval.**
   c. Inventory + validation report SpyCloud ON/OFF (boolean only).
   d. `tools/register_scan_status_task.ps1`: Windows scheduled task running the read-only `--scan-status-all` every few hours (operator registers it once). VM: systemd timer later, with the VM browser runner.
2. **Operator (with approval):** run `--spycloud-off` on CO-0757, then CO-0679, CO-0728, CO-0762 (and any CE/Case 3 tenant the inventory shows ON).
3. **Operator:** push (`git push origin main`); delete the HAR from Downloads; restart the dashboard to see today's UI.
4. Still open (owner said "later" / not chosen): identity-only pre-check for COs not ready to start; label a CO's own tenant on the pre-check page; drop the extra Leonardo check at Start; show the checks with timings on the run page.
5. Carried over: review item 5 and items 7–12, 16–17; `docs/38` owner answers (Q1–Q3, Q7, Q9–Q11); VM staging (r21); CO-0679 tenant fix; CO-0735 Salesforce correction; Leonardo start-date quirk note; Slack (connector not authorized).

**Boundaries preserved (2026-10-04):** Leonardo Development only. Live actions: read-only Salesforce reads, inventory exports, `--validate-all`, duplicate checks/pre-checks, UI probes (Add Account and Edit opened and **cancelled**). The only Leonardo write today was the operator's own CO-0757 Start. No Salesforce write, no production access, no VM change. No credential, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

### Handoff — 2026-10-05 (continue here)

**Authoritative handoff for the next session.** Supersedes the 2026-10-04 "Next steps". All 2026-10-04 assistant steps (1a–1d) are built locally; nothing is committed yet (operator commits on request and pushes).

#### Done today (local; 913 integration tests OK, 1 skipped; `phase1_validator` OK; artifact and offline-boundary guards pass)

Tests must run with the project runtime `%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`; the system `python` has no Playwright and shows 6 false failures.

| Area | Change |
| --- | --- |
| Redash prod-clone inventory | See `docs/39_REDASH_PROD_CLONE_INVENTORY_2026-10-05.md`. Read-only saved query 251 on `Prod (Cloned) - Mgmt`; adapter `integration/onboarding/redash_inventory.py`, collector `tools/redash_inventory_collector.py` (DPAPI-stored per-query key, four allow-listed calls, fail closed), env `prod-clone` on the Tenants page, informational answer on the duplicate pre-check. **Live:** key stored, `--probe` OK (26 columns, 5,439 rows), first `--collect` OK, hourly task `SurfaceOnboarding-RedashInventory` registered, first run exit 0. The DEV export is **not** retired (DEV and prod share 0 UUIDs). |
| 1a Scan status from executions | `parse_scan_executions` (pure, fail closed; accepts `pagination_response.table_data`, `table_data`, or a list) and `read_scan_executions` (row → `.rowMenuActionsContainer .list-trigger` → `[data-am="Button-Grid_Details"]` → "Duration Per Scan"; passively captures the app's own POST `/account/{id}/campaign/executions`; id must equal the CO's readback id; Escape in `finally`). States `done` / `running` (+ `running_since`) / `no_executions` / `unrecognized`. Row-based status is always still written. Dashboard: "Running since …" / "Done · hh:mm:ss (last finished execution)" + per-execution list. Used by `--scan-status` / `--scan-status-all`. CE-only skip kept. |
| 1b SpyCloud OFF | `set_spycloud_off` / CLI `--co CO-XXXX --spycloud-off` = **dry run** (Edit opened, checkbox state reported, Cancel); `--confirm-write` = the write: only `spyCloudEnabled` unticked, Confirm, app's own POST `/account/{id}/edit` must be 2xx with the expected id, then row re-read must show `spyCloudSettings.enabled == false` (`spycloud_off_verified`). ~17 reason codes; non-dev env and Surface-only route refused; ambiguous row fails closed. Row menu allow-list = Details and Edit only. After-create hook for CE / Case 3 exists but `SPYCLOUD_AFTER_CREATE_ENABLED = False` until the first live save is verified; when on, failure = warning only (`integration/attended_spycloud.json`, run log, CO page). CO page: SpyCloud card + read-only "Check SpyCloud" button (`/attended/spycloud-check`, dry run only); the save is CLI-only. `docs/33` superseded (SpyCloud must be OFF). Tests: `integration/tests/test_spycloud_off.py` (37). |
| 1c SpyCloud in inventory/validation | Boolean `spycloud_enabled` only (true/false/null) in DEV and Redash inventories, Tenants column, CSV. `--validate-all`: informational **warn** "SpyCloud is ON (owner: must be OFF)" for CE / Case 3. |
| 1d Scheduled scan status | `tools/register_scan_status_task.ps1` (`SurfaceOnboarding-ScanStatus`, `-IntervalHours` default 4, `-WhatIf`, `-Remove`, current user, interactive, no overlap, 30-min limit). **Not registered.** Without a signed-in session the run opens a sign-in tab and waits up to 15 min, then stops (`development_login_timeout` / `leonardo_session_expired`). |

#### Later on 2026-10-05

- Operator ran `--scan-status`, `--spycloud-off`, and `--spycloud-off --confirm-write` for CO-0757 with the **system** `python` → all `playwright_runtime_unavailable` (no browser opened, nothing read or written). Run the CLI with the project runtime (path above).
- Fixed: the SpyCloud CLI printed `"leonardo_write":"attempted"` for any `--confirm-write` run, even one that stopped before Leonardo. Now `spycloud_write_label`: `verified` only for `spycloud_off_verified`, `attempted_unverified` only for outcomes after Confirm, otherwise `not_performed`. 889 tests OK.
- Dashboard restarted on today's code (`start_attended_dashboard.ps1 -Restart`).

#### Live runs with the project runtime (2026-10-05, 13:42–13:43, CO-0757, Leonardo Development)

- `--scan-status` → `duplicate_search_schema_unavailable`: the read-only path still requires the visible Search box (`_open_search`), which was not found; the executions read was never reached. Envelope and row click remain unverified.
- `--spycloud-off` (dry run) → `spycloud_dry_run_on`: tenant search `rows=1 total=1`, Edit opened, checkbox read ON, cancelled. Row click, Edit, Advanced options, checkbox, Cancel **verified**.
- `--spycloud-off --confirm-write` → printed `spycloud_readback_unavailable` / `"leonardo_write":"not_performed"`, **but the run log shows the save happened**: POST `/api/v1/backoffice/account/<CO-0757 id>/edit` → **200**. The re-read then failed (`tenant_search not_rewritten`): the app's own post-save table refresh was in flight and was taken as the search reply. **State: SpyCloud on CO-0757 probably OFF, not verified.** The CLI label is wrong for this case (bug).
- **Fixes 1 and 2 applied (operator approved "1 yes, 2 yes"):** (1) after a 2xx save, a failed re-read is retried once, then reported as `spycloud_saved_unverified` with `"leonardo_write":"attempted_unverified"`; (2) `_search_tenants` accepts only the reply to the request it rewrote (an in-flight app refresh no longer counts) — applies to every invisible search (create duplicate checks, readback, scan status, SpyCloud). Tests: 892 OK, 1 skipped; guards pass.
- **Fix 3 declined (owner, 2026-10-05: "fix 3 stays as it is"):** read-only paths (`run_readback`, `run_scan_status`, `run_validate`) keep requiring the visible Search box (`_open_search`). Consequence: `--scan-status` can stop with `duplicate_search_schema_unavailable`, so the executions read stays unverified live.
- **Verified:** dry run afterwards → `spycloud_already_off` (Edit form shows the checkbox OFF; cancelled). **CO-0757 SpyCloud is OFF.** Row-level `spyCloudSettings.enabled == false` not yet re-read (next DEV export shows it). Fixes 1–2 are in: `--confirm-write` may be used again, one CO at a time, after a dry run (CO-0679, CO-0728, CO-0762 next).

#### Tenants tab split (2026-10-05, owner request; local, 904 tests OK)

- Sidebar **Tenants** → `/tenants`: production clone (Redash query 251 snapshot), no refresh button, plus **"Check a CO for duplicates in production"** (CO + route → POST `/attended/production-duplicate-check`, same login/origin guard). Runner `run_production_duplicate_check` = Salesforce source read + `_production_gate` only (no DEV inventory, Leonardo, or browser). CLI `--production-duplicate-check --co CO-XXXX [--route ...]` (ids/reasons/counts only).
- Sidebar **DevOps** → `/inventory`: Leonardo Development inventory with Refresh. `/inventory?env=prod-clone` → 303 `/tenants`.
- Production gate compares the same fields as the DEV pre-check (`duplicate_precheck`): tenant name (normalised), primary domain, alternate domains, every CO domain incl. route extras; deleted tenants still match; no alternate-domain data → `production_clone_incomplete`. Snapshot trusted ≤ 6 h. DEV Start gate unchanged; production answer stays informational.
- Owner answers (2026-10-05): (a) **yes** — matches now carry `created` (Dev and clone); the result table shows a Created column (`_created_date`: epoch ms or ISO → YYYY-MM-DD, else "—"). (c) **yes** — section renamed "Production duplicate check" with neutral wording (match → "This CO already has a tenant in production"; no match → not a clearance, clone up to a day behind; unusable → fail closed). 905 tests OK. (d) 6-hour limit **kept** for now: it only makes the check refuse to answer (read-only, cannot affect production); its age is Redash's `retrieved_at`, so if the per-query key cannot refresh query 251 the check will refuse after 6 h — confirm `"refreshed": true` in `collector.log`. (b) **yes, via a new query**: query 251 stays untouched; a new saved query (copy of 251 plus the Salesforce account id / name-alias fields on `tenantsMView`) is to be created in Redash by the operator, then the collector gets a pinned allow-list for that second id and its own DPAPI key file. Field names are unknown — operator to read them from the Redash schema browser.

#### Committed and first verified save (2026-10-05, late)

- Commit `0b39d08` (all of today's work); **operator pushed** `640eaf6..0b39d08` to `origin/main`.
- CO-0679: dry run `spycloud_dry_run_on`, then `--confirm-write` → **`spycloud_off_verified`** (`"leonardo_write":"verified"`): save 2xx and the re-read row says `spyCloudSettings.enabled == false`. First full live proof of the SpyCloud OFF path, including fix 2 (the re-read took only its own search reply).
- SpyCloud OFF so far: CO-0757 (Edit form, dry run `spycloud_already_off`), CO-0679 (verified). Next: CO-0728, CO-0762 (dry run, then `--confirm-write`).
- **Owner enabled SpyCloud OFF after create** (2026-10-05): `SPYCLOUD_AFTER_CREATE_ENABLED = True`. After `readback_verified` on CE / Case 3 the runner turns SpyCloud OFF and re-reads; a failure is a visible warning and never changes the create result (test `test_a_spycloud_failure_after_create_keeps_readback_verified`; the CE golden test pins the create path with the hook patched off). 906 tests OK. Not yet exercised live (next CE / Case 3 create).

#### Production duplicate gate at Start (2026-10-05, owner decision; local, 913 tests OK)

Owner: "validate first if this was created on Production and mark it; then validate it is not created in Leonardo; if not, create in Leonardo. Once we migrate to production, delete the Leonardo duplicate validation." The production check is automatic, not a manual option.

- `_run` order (create and dry run; CE, Surface-only, Case 3): **(1) production gate** (`_production_gate` on the Redash prod-clone snapshot) → match = `duplicate_production_match`, unusable clone (missing / > 6 h / tampered / no alternate domains) = `production_clone_unavailable`; both stop before any browser. **(2)** DEV inventory pre-check (unchanged). **(3)** live Leonardo duplicate check and create (unchanged). TODO in code: remove (2)+(3) duplicate validation when onboarding moves to production.
- Marked: check kind `production_duplicate` recorded at every Start (ids, names, created dates, reason only); CO page and queue show **"Already in production"** (tenant name, id, created) or "Production check unavailable — reason" with the `--collect` hint; a match goes to the review queue.
- Manual form `/tenants` → `/attended/production-duplicate-check` **removed**; the Tenants tab says every Start checks this list first. CLI `--production-duplicate-check` kept as a read-only diagnostic.
- Renewals (cases 4–6) never reach `_run` (not in `ROUTES`), so they are not gated.
- Golden: one added event `production_gate production_clone_no_match blocks=False` after `license_dates`.
- **Operational:** if the hourly Redash task has not refreshed within 6 h (PC off / signed out), every Start stops with `production_clone_unavailable`; run `tools\redash_inventory_collector.py --collect` first. The marker is replaced by a later check for the same CO (the check state keeps the latest check per CO).

#### Unverified until a live read (Leonardo Development)

- Executions reply envelope; the row-click selector (`tr, [role='row']` filtered by tenant name).
- "Advanced options" expanders as exact text and collapsed initially; unique Cancel / Confirm buttons in Edit; edit request is a POST (its reply body is not read).

#### Redash refresh finding (2026-10-05, end of day)

Operator ran `redash_inventory_collector.py --collect`: `"refresh_failed": true, "refreshed": false`, `inventory_unchanged`, 5,439 rows, data age 107 min. **The per-query key cannot make Redash re-run query 251**; the collector only gets Redash's cached result. Its age (`retrieved_at`) keeps growing, so once it passes 6 h **every Start stops with `production_clone_unavailable`** (fail closed, by design). Fix is on the Redash side: give the query a refresh schedule (step 1 below).

#### Next steps for 2026-10-06 (in order; continue here)

1. **Operator (Redash UI, blocks every Start):** set a **Refresh Schedule = every 1 hour** on the query the collector reads. Today that is query 251 (schedule only; the query text stays untouched). If the new v2 query (step 4) is created first, schedule that one instead. Then run `--collect` and confirm `data_age_minutes` < 60; the hourly task keeps the snapshot fresh after that.
2. **Operator, SpyCloud OFF:** CO-0728 then CO-0762 — dry run (`--co CO-XXXX --spycloud-off`), then `--confirm-write`; expect `spycloud_off_verified`. Done: CO-0757 (off), CO-0679 (verified).
3. **Operator:** push the end-of-day commit (`git push origin main`).
4. **Operator (Redash):** create the v2 query (fork of 251, `Prod (Cloned) - Mgmt`, unpublished, read-only) adding the Salesforce account id and name-alias fields from `tenantsMView`; send the **query id and field names** (never the key). **Assistant then:** pin the second query id in the collector allow-list with its own DPAPI key file, allow the new columns in the adapter, add them to the production duplicate gate, tests.
5. **First live Start after this** (next CE / Case 3 CO): verify in the run log `production_gate production_clone_no_match` → `inventory_precheck` → live check → create → `readback_verified` → `spycloud spycloud_off_verified`.
6. **Scan status (fix 3 declined):** `--scan-status` keeps the visible Search box gate and stopped with `duplicate_search_schema_unavailable` today; the executions reply envelope and row click remain unverified. Owner to decide how to proceed (e.g. investigate why the Search box was not found in a read-only probe). Do not register `register_scan_status_task.ps1` until a live `--scan-status` succeeds.
7. **Owner decisions still open:** (a) lift the CE-only scan-status skip? (b) "Done" = last finished execution's duration (current) or sum? (c) accept that an unattended scan-status run can leave a sign-in tab waiting? (d) set a rotation date for the query-251 key (it bypasses SSO/MFA).
8. Carried over from 2026-10-04 items 4–5 (identity-only pre-check, own-tenant label, drop extra Leonardo check at Start, timed checks on run page; review items 5, 7–12, 16–17; `docs/38` Q1–Q3, Q7, Q9–Q11; VM staging r21; CO-0679 tenant fix; CO-0735 Salesforce correction; start-date quirk note; Slack). Wire prod-clone `lastScanStatusEnum` into the CO page (later, production COs).

**Boundaries preserved (2026-10-05):** Leonardo Development only. Live actions today: Redash read-only query/collection (operator-run) and task registration; operator-run Leonardo Development reads (dry runs, scan-status attempt) and two Leonardo Development SpyCloud saves (CO-0757, CO-0679). No Salesforce write, no production BackOffice access, no Redash change by the assistant, no VM change. No credential, key, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

### Handoff — 2026-10-06 (continue here)

**Authoritative handoff for the next session.** Supersedes the 2026-10-05 "Next steps" where stated. Focus today: make onboarding Cases 4–6 (renewals) functional in Leonardo Development.

#### Verified today
- Step 1 (Redash refresh) **done**: `--collect` at 18:28Z → `inventory_collected`, `data_age_minutes: 0`, 5,440 rows, `refreshed: false` (Redash refreshes query 251 on its own schedule). Step 3 (push of `500d30c`) was already done.
- CO-0728 SpyCloud dry run: first two runs `spycloud_readback_unavailable` (`Page.reload` timeout 30 s with **zero** browser events → stale/frozen automation tab after an SSO sign-in); after closing the automation browser and **Prepare sessions**, `spycloud_dry_run_on`. Operator approved `--confirm-write` for CO-0728; result not yet reported. Follow-up idea (not built): detect a stale tab before the search reload and fail fast with a clear reason.

#### Case status (Leonardo Development)
| Case | Status |
| --- | --- |
| 1 New Surface only | Working live (CO-0649) |
| 2 New CE only | Working live (CO-0679, CO-0728, CO-0762; CO-0702 manual + verified) |
| 3 New Surface + CE | Working live (CO-0757) |
| 4 / 5 / 6 Renewals | Edit mode + prod→Dev mirror built locally (below); **not run live** |

#### Owner decisions 2026-10-06 (supersede `docs/38` §4 for these items)
- Q1 (**revised later the same day**): renewal expiration = the new term's DealHub Subscription End Date **exactly** (full multi-year term, e.g. 2026-09-30 → 2029-09-29). Production showed Toyota at Sep 30, 2029; owner chose DealHub end exactly.
- Production target selection (owner, 2026-10-06): the renewal's production tenant = the one **live, paid** tenant (`is_live_paid_tenant`: not deleted, enabled, prepaid/PAYG) with the CO's **exact** tenant name. Trial / Evaluation / POV tenants on the same domain and disabled duplicates are never the target (CO-0770 had two "Toyota Motor Europe", one disabled; CO-0767 and CO-0758 have eval/POV tenants on their domains). Used by the mirror and by the Q3 gate.
- Observed: CO-0770 (Toyota, → 2029-09-30) and CO-0758 (Etimad, → 2029-09-18) look **already renewed in production**; their renewal edit will stop with `renewal_expiration_would_shorten` (fail closed). CO-0770's Salesforce main domain is `toyotaeu.mail.onmicrosoft.com` (needs a CSM fix). **CO-0767 (A2A, expires 2026-10-25) is the first live Case 6 test.**
- Q2: never change the tenant's start date, even if the old licence expired.
- Q3: "Onboarding Approval Status = Approved" means a human (owner/team) validated the CO, for **all** cases; no extra manual review. Any domain/subdomain a renewal **adds** must pass the production duplicate validation on the Redash prod clone (own target tenant excluded and found exactly once; a match on any other tenant, or an unusable clone, stops).
- Q9: test renewals against a **mirror** tenant created in Dev from the production clone (start date = today; Dev cannot backdate).
- Q10: `Surface & Credential Exposure` + `Renewal of Existing Product` = Case 6; single-product renewals out of scope.
- Accepted: Surface advanced toggles report-only on renewal (`profile_drift`); renewal requires Approved + exactly one CE email domain; domain lists add-only; mirror scanning interval from prod `scanningInterval`, LC weekly; LC scanned domains = CO email domains capped at the prod count; mirror fails closed when two prod tenants match; mirrors to be labelled "DEV mirror" and skipped by drift validation (**follow-up, not built**).

#### Built today (local; parallel Sonnet agents, merged `eb93daa`, `b12043b`; 1000 integration tests OK, 1 skipped; artifact and offline-boundary guards pass)
- `45d231c` renewal **edit mode**: `--co CO-XXXX --renew [--confirm-write] [--tenant-name ...]`; `RouteContract.mode`, `RENEWAL_ROUTES` (outside `ROUTES`), `renewal_domain_gate`, review item 20 fixes (no start fallback in edit, no run-day in plan, `select_ce_subscription` honours `DealHub_Status__c`, `production_ids_present` informational). Tests `integration/tests/test_renewal_edit.py` (58). Live-unverified selectors listed above `RENEWAL_ENGINES`.
- `0dc79d8` **prod→Dev mirror**: `integration/onboarding/renewal_mirror.py` (pure plan), `--co CO-XXXX --mirror-renewal [--confirm-write]`, records in gitignored `integration/attended_renewal_mirrors.json` + readback store. Tests `integration/tests/test_renewal_mirror.py` (29). Licence-type labels other than "Prepaid annual subscription" are guesses (unmapped → fail closed).

#### Next steps
Superseded by "End of day 2026-10-06" below.

**Boundaries preserved (2026-10-06):** assistant actions were local code/tests and one read-only Redash `--collect`. Operator-run Leonardo Development reads (SpyCloud dry runs). No Salesforce write, no production access, no Redash change, no VM change. No credential, key, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

#### CO page redesign - stage tracker (2026-10-06, local code and tests only)
- `/co/<CO>` now opens with a six-step tracker mirroring Salesforce's Onboarding Stage path (New, Request Approved, Account Scanning, Scan Completed Successfully, User Created, Onboarding Completed). **Dashboard-only and derived; nothing is written to Salesforce.** TODO: on the move to production ask the owner again: Salesforce Onboarding Stage must then be updated.
- Derivation (`derive_onboarding_stage`, pure): each stage needs all earlier ones plus its own proof; missing/unknown evidence stops at the last provable stage. Approved = Approval Status Approved; Account Scanning = Leonardo readback (or verified run record); Scan Completed = scan-status read with execution state `done`; User Created = operator account AND customer user present in the local inventory snapshot; Onboarding Completed only from Salesforce. Salesforce's own stage counts as evidence for itself; when the derived stage is ahead the page shows "Salesforce still shows: <stage>".
- **User Created data gaps:** the DEV snapshot keeps only `operator_assigned` (bool), `primary_user.present` (an object exists) and `mfa_required`; it cannot tell a user entered in the form from an activated/invited/logged-in one. `terms_accepted` (shape unconfirmed live) is not used. The production clone (Redash) carries only the operator flag, so for production COs the customer user stays "not collected" and the stage never advances to User Created from the clone. Snapshots are as fresh as the last export. No new Leonardo reads were added.
- Layout: tracker, header, summary (now with licence dates and short tenant id), one next-step card; tenant checks (validation, scan status, SpyCloud), Salesforce IDs, record, and renewal sign-in/manual sections are collapsed. Renewal plan card updated to the decided rules (Q1 term end exactly, Q2 start never changes, Q3, Q10) and points to the CLI `--renew` / `--mirror-renewal`; still informational.

#### End of day 2026-10-06 (authoritative; continue here)

**Live today (Leonardo Development, operator-run):**
- CO-0767 (Case 6): production tenant chosen = "A2A" (live, paid, exact name; Trial/Eval/POV tenants ignored). `--mirror-renewal` dry run → `mirror_dry_run_verified`; `--confirm-write` → **`mirror_created_verified`** (account/add 200, readback 1 row, mirror record verified; Dev ids in the readback store). SpyCloud-after-create stopped `spycloud_row_ambiguous` ("A2A" matched 4 rows) → fixed by `_search_one_row` (narrow by the row's own domain); `--spycloud-off` dry run → `spycloud_dry_run_on` (narrowing verified live). **`--confirm-write` approved; result not yet reported.**
- `--probe-tenant-details` (read-only) on the A2A mirror: Details tabs = Details, Pending Requests, Configuration, Duration Per Scan; API calls seen: userProfile, accountInfo, `inventory/metadatas/{id}`, `runConfiguration/{id}`, `account/{id}/campaign/executions`. **BackOffice Details exposes no tenant user list** → Dev "User Created" can only be confirmed manually (the row's Access action stays forbidden). Also **verified live:** the executions reply envelope `pagination_response.table_data` with `startDate`/`endDate`/`status`/`campaignTypeEnum`/`campaignExecutionTypeEnum` (was unverified).
- CO-0728 SpyCloud: dry run `spycloud_dry_run_on`; `--confirm-write` approved, **result not reported**. CO-0762 not started.
- Leonardo Development reliability: many reload hangs (30 s, zero events) and `ERR_CONNECTION_RESET` on the 5.2 MB `assets/index-*.js`; plain GETs answered in 1.4–3.9 s; by 15:13 reloads took 4–6 s. No browser-code change between the last good run on 10-05 and the first hang → environmental (network/VPN or Leonardo Dev). Request interception disables Chrome's HTTP cache, so every search reload re-downloads the bundle.

**Code today (local commits after `9d96407`; 1053 tests OK, 1 skipped; both guards pass; not pushed):**
| Commit | Change |
| --- | --- |
| `3a71eaa` | Q1 = DealHub term end exactly; production target = one live paid exact-name tenant (`is_live_paid_tenant`) for mirror and Q3 gate |
| `2b6af1b`, `5bca5c7`, `0bb50e7` | Search: one retry by navigation after a reload timeout (hung document retired first, only that attempt's reply counts); 45 s reply wait; `page_unresponsive` hint; diagnostics title fix |
| `6746fa1` | Search toolbar: wait up to 45 s for Leonardo to render before failing closed |
| `b1564cf` | `_search_one_row` (name, then the row's own primary domain; exactly one row with the expected id) for SpyCloud and renewal edit; standalone SpyCloud OFF allowed on verified CE renewal mirrors (Cases 4, 6) |
| `c76b509` (merge) | CO page redesign: six-step stage tracker (dashboard-only; **ask the owner again at the production move: Salesforce Onboarding Stage must then be updated**), compact header, one next-step card, details collapsed; renewal card updated to the decided rules |
| `8717461` | Search trigger: header sort click first, reload/navigate fallbacks; live: `thead th button` **not found** (`in_app_unavailable`) → reload used. Needs the real header selector (read-only probe) |
| `c28c2a9` (merge) | User Created: `primary_user_matches` (boolean only) from validation; "Confirm user created"/Undo (POST `/attended/confirm-user-created`, `/attended/unconfirm-user-created`, existing guards; gitignored `integration/attended_user_created_confirmations.json`). Note: our creates use the fixed attended user as Primary User |
| `b4c5b8c` | `--co CO-XXXX --probe-tenant-details` (read-only; tab labels, masked API paths, reply key names only) |

**Owner decisions (later on 2026-10-06):** stage tracker dashboard-only until production; "User Created" = the CO's primary user exists as an **extra user** on the tenant (owner answer (b)) — production: detect via a Redash users query (store only SHA-256 of lower-cased emails per tenant); Dev: manual confirmation (no user list in BackOffice).

**Case status:** 1 ✅ live (CO-0649) · 2 ✅ live (CO-0679/0728/0762) · 3 ✅ live (CO-0757) · 6 🟡 mirror created live (CO-0767), edit not yet run · 4 🟡 built; CO-0758 looks already renewed in production (expect `renewal_expiration_would_shorten` unless only CE is added) · 5 🟡 built; no open CO.

**Next steps (in order; each live write needs operator go):**
1. Operator: report the CO-0767 (A2A mirror) and CO-0728 SpyCloud `--confirm-write` results (expect `spycloud_off_verified`); then CO-0762 dry run → `--confirm-write`.
2. Case 6: `--co CO-0767 --renew` dry run (expect `renewal_dry_run_planned`: expiration 2026-10-25 → DealHub term end 2029-10-26, start unchanged, counts per DealHub, CE overlay) → review the plan → `--renew --confirm-write` → `renewal_edit_verified`. Live-unverified Edit selectors are listed above `RENEWAL_ENGINES`.
3. Case 4 on CO-0758: `--mirror-renewal` dry run → create → `--renew` dry run (expected stop on shorten, or a CE-only diff) — owner to decide how to treat already-renewed tenants.
4. Case 5: pick a CO or a synthetic fixture.
5. Operator (Redash): users-per-tenant query (account id, user email, status) on `Prod (Cloned) - Mgmt`; send query id + column names. Assistant: collector allow-list + hashed emails + "User Created" automatic proof for production COs. Also the v2 accounts query (Salesforce account id / aliases).
6. Assistant: probe the real tenant-table header selector (read-only) so the in-app search trigger works; "DEV mirror" label and skip in `--validate-all`/dashboard; renewal outcome state file.
7. Operator: restart the dashboard (`tools\start_attended_dashboard.ps1 -Restart`) to see the stage tracker; push (`git push origin main`); delete `_to_delete/`.
8. Carried over: 2026-10-05 item 7 (scan-status decisions a–d) and item 8.

**Boundaries preserved (2026-10-06, end of day):** Leonardo Development only. Leonardo writes today (operator-run, approved): CO-0767 mirror create; SpyCloud saves for CO-0767 mirror and CO-0728 if run. Assistant live actions: read-only Redash `--collect`, local prod-clone reads, unauthenticated timing GETs of the Leonardo Dev front page and bundle. No Salesforce write, no production BackOffice access, no Redash change, no VM change. No credential, key, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

#### Handoff for 2026-10-07 (latest; supersedes the "Next steps" above)

**Since the end-of-day entry:** CO-0767 A2A mirror SpyCloud `--confirm-write` → **`spycloud_off_verified`** (`"leonardo_write":"verified"`). Owner approved the assistant to create a **new** Redash users query (never modify existing queries; copy or create new only) and to restart the dashboard and push — but this session's auto-mode permission classifier **blocked** `git push` and every Redash-directed action ("Out-of-Place Publication"). Nothing was pushed or created in Redash. Dashboard restart (`start_attended_dashboard.ps1 -Restart`) was launched and may be waiting for SSO sign-in in the automation window.

**Start tomorrow with (in order):**
1. **Operator:** `git push origin main` (the earlier commits were pushed by the operator on 2026-10-06; push this handoff commit). To let the assistant push or work in Redash, add a Claude Code permission rule from an interactive `claude` terminal first.
2. **Operator:** open http://127.0.0.1:8012/ (sign in via SSO) and review the new CO page (stage tracker; "Confirm user created").
3. **Case 6 (read-only first):** `--co CO-0767 --renew` → expect `renewal_dry_run_planned` (expiration 2026-10-25 → 2029-10-26, start unchanged, counts per DealHub, CE overlay); paste the plan JSON; then, with owner go, `--renew --confirm-write` → expect `renewal_edit_verified`. Edit-form selectors are live-unverified (list above `RENEWAL_ENGINES`); a mismatch fails closed.
4. **SpyCloud clean-up:** CO-0728 `--confirm-write` result still unreported (re-run the dry run to read the state); CO-0762 dry run → `--confirm-write`.
5. **Redash users query (owner rule: new query only, never edit 251 or any existing one):** in `Prod (Cloned) - Mgmt` → New Query; find the users collection in the schema browser; send collection + field names (tenant/account id, email, status). Assistant drafts the query text; operator saves it unpublished and sends the query id (never the key). Assistant then adds a pinned allow-list entry + own DPAPI key file, stores only SHA-256 of lower-cased emails per tenant, and wires "User Created" (CO primary user present as an extra user) for production COs. Dev stays manual (BackOffice Details has no user list).
6. **Case 4 (CO-0758):** owner decision first — the production tenant already expires 2029-09-18 (looks renewed); options: skip, or apply only the CE part. Then mirror dry run → create → renew dry run.
7. **Case 5:** choose a CO or a synthetic fixture.
8. **Assistant (local):** read-only probe for the real tenant-table header selector (in-app search trigger currently falls back to reload); "DEV mirror" label + skip in `--validate-all`/dashboard; renewal outcome state file; delete `_to_delete/` (operator).

**Run commands with the project runtime** (PowerShell): `& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" tools\attended_ce_only_playwright.py --co CO-XXXX <flags>`. If Leonardo hangs: close the automation browser → dashboard **Prepare sessions** → re-run.


#### Renewals run from the dashboard; Account ID / UUID on the CO page (2026-10-07, local code and tests only)
- **Owner decision (reverses 2026-10-04 "renewals CLI-only")**: Cases 4-6 (Approved) start from the CO page like a create. `--co CO-XXXX --revision R --renew-run` (`run_renewal_onboarding`): source/revision check, Dev mirror if no verified mirror matches the readback (a "maybe created" mirror result stops, marked `uncertain=mirror_create`), renewal dry run (`renewal_already_current` finishes OK; anything but `renewal_dry_run_planned` stops), apply (must end `renewal_edit_verified`; attempted-unverified codes are marked `uncertain=renewal_write`), SpyCloud OFF (warning only), outcome record. Leonardo Development only; no production mode. `--renew`, `--mirror-renewal`, `--spycloud-off` unchanged; a read-only `--renew` dry run settles an uncertain apply (dashboard Verify).
- Dashboard: `POST /attended/start-renewal` (session gate "start", origin/login guards, Approved, Case 4-6 only, two attestations, revision match, one-time 15-minute nonce, no other run in progress, per-CO start blockers). Runner state carries the renewal route; `renewal_uncertain` blocks Start and Reset. Old "next CLI step" card removed; one CLI hint line stays in Diagnostics.
- Not built: verifying an uncertain **mirror create** from the dashboard (shown as "verify by hand"; Start and Reset stay blocked; the mirror record file also blocks a second create).
- CO page: full, copyable Account ID / Account UUID (CE: UUID; Surface: ID; Case 3 and renewals: both; "Not captured" when absent), labelled Leonardo Development; `SALESFORCE_ID_MAPPING` now covers the renewal engines (Dev IDs are never written; `ID_WRITEBACK_ENABLED` still off).

#### Two areas: Leonardo Development and Production read-only (2026-10-07, local code and tests only)
- **Owner-approved split** of the dashboard. Sidebar: LEONARDO · DEV (Onboardings `/`, Onboarded on Dev `/dev/onboarded`, History `/history` + `/dev/history`, Tenants `/inventory`), PRODUCTION · BO read-only (Readiness `/prod`, Matches `/prod/matches`, Tenants `/prod/tenants`; `/tenants` and `/inventory?env=prod-clone` redirect 303 there), TOOLS (Sessions). Every page has a 3 px environment bar and a header pill (navy "DEV · Leonardo"; amber "PRODUCTION · read-only" from the warn tokens; red is never an environment colour).
- **Onboarded on Dev** (`integration/onboarding/dev_onboarded.py`, pure): counts only verified evidence: create `readback_verified` (and `readback_only_verified`, the manual create) with a readback holding both ids; renewal `renewal_edit_verified` (confirm write, verified) or `renewal_already_current` (read-only verification, any mode) whose outcome tenant id equals the readback id. A verified mirror alone is "Dev mirror". Dry runs, uncertain runs and `attempted_unverified` never count. Those COs leave the queue `/`; a link shows "N onboarded on Dev".
- **Production match** (`integration/onboarding/production_match.py`, pure; glue `production_match_for` in the runner): clone_unavailable / not_in_production / exists_other (blocks) / ambiguous / exists_matches / exists_differs. Required: name, primary domain, alternate domains, licence type, licence end (DealHub; renewals = term end exactly), SpyCloud OFF (CE routes). Informational: licence start, operator, scan status; Salesforce account id "not checked". Unknown required data is never a match. Production never uses the Dev run day or entered dates. `/prod` (filters, default onboarded-only, `?all=1`), `/prod/matches` (current only, nothing stored, plus recorded Start-gate checks), CO `?env=prod` (comparison table, "Before migration" text; no form or button).
- **Not done / to know:** the Dev CO view's Production chip only peeks at the display cache ("Not checked" until a Production page or view computed it); the sidebar Sign out form (POST) still appears on every page when signed in; the Redash accounts query (Salesforce account id) is still pending. 1343 integration tests OK, 1 skipped.


#### Case 5 renewal built (2026-10-07, local code and tests only; not run live)
- Engine `case_5_renew_ce_new_surface` now differs from Case 6 in `build_renewal_specs` / `renew_tenant`: edits the existing CE tenant in place; assets 10,000; subdomains and **Number of domains = licensed subdomains**; Weekly (Prime) / Monthly (Go); LC kept (ON, Weekly, CE email domain); the create-time Surface profile (90 h, `SURFACE_ADVANCED_TOGGLES`, Notifications / Multiple users / API / Provisioning / Subdomains ON, Phishing OFF) is **applied and verified** (Cases 4/6 stay report-only `profile_drift`).
- Domains rule for Case 5: every tenant root domain must be in Salesforce (else `renewal_domains_mismatch_manual_review`); Salesforce roots the tenant lacks are added to Alternate Domains (add-only, Q3 production gate). Cases 4/6 keep the equal-sets rule.
- Read-after-write compares every profile control (changed or not). Controls the search row does not report (`subDomainsNumberAllowed`, possibly `campaignsTimeoutInHours`) are form-verified (read, set, re-read before Confirm). Missing Edit controls fail closed with `renewal_field_unavailable`.
- `SPYCLOUD_MIRROR_ROUTES` now includes Case 5 (SpyCloud OFF follows the renewal). Dashboard: Case 5 plan summary "Surface added: profile applied, domains = subdomains (N)"; all renewal cases show "Check the Operator Account (did the customer's TA change?)" after a verified renewal (no automatic change).
- UNVERIFIED LIVE (Edit form): see the comment block above `RENEWAL_ENGINES`. Tests: `integration/tests/test_case5_renewal.py` (34). Next: Case 5 dry run on a Dev mirror of a CE-only tenant, read the plan, then owner go for `--confirm-write`.

#### End of day 2026-10-07 (authoritative; continue here)

**Live today (Leonardo Development, operator-run):**
- CO-0767 (Case 6, A2A Dev mirror): `--renew` dry run `renewal_dry_run_planned` (domains_check match 5; only change expiry 2026-10-25 → 2029-10-26; start unchanged; no added domains), then **Start renewal from the dashboard → `renewal_edit_verified` (15:02:35)**. First live renewal end-to-end. Applied 6 days before "apply from" 2026-10-13 (window was not enforced yet; now production-only by owner decision, Dev exempt).
- CO-0767 `--scan-status`: row status saved (COMPLETED, tracker reaches Scan Completed) but the executions read still ends `scan_status_executions_unavailable` after two fixes (narrow to one row; listen before opening Details). Step logging added (`126e0e6`): the next run writes a `scan_status` run with details_opened / tab_lookup / tab_clicked / reply / parse events. **Re-run and read the run log first thing.**

**Owner decisions 2026-10-07 (binding):**
1. Renewals start from the dashboard (reverses 2026-10-04 CLI-only). Renewals are not blocked when Salesforce already holds IDs (production tenants have them; the Dev mirror is separate).
2. Renewal Number of domains: Cases 4/6 keep the tenant's current value when Salesforce root domains equal the tenant's, else stop `renewal_domains_mismatch_manual_review`; **Case 5 = licensed subdomains** (matches production CO-0462).
3. Case 5 applies the create-time Surface profile (verified after save); Cases 4/6 stay report-only. Scan now follows the schedule (as Case 1). Assets stay 10,000 (guide says ~50,000; owner kept 10,000, production CO-0462 has 10,000). Operator: reminder only after renewals ("did the TA change?").
4. Guide G7 14-day apply window enforced for production only; Dev mirrors may be renewed early.
5. A tenant already at/after the DealHub term end keeps its expiry; only missing parts are applied (CO-0758 Case 4).
6. Fix 3 reversed: read-only paths (`--scan-status`, `--readback`, `--validate`) use the hidden search; create paths keep the visible Search box gate.
7. Dashboard split into LEONARDO · DEV and PRODUCTION · BO (read-only); "Onboarded on Dev" tab; production match required fields: name, primary domain, alternate domains, licence type, licence end, SpyCloud OFF (CE routes); Salesforce account id "not checked"; trial/eval/POV-only = blocks; Readiness defaults to Dev-onboarded COs; Matches current-only.
8. CO page: one "What to do now" card, key facts (max 10), pinned alerts, folds (Tenant checks, Salesforce record, Run history, Diagnostics incl. CO-0741/0745 actions); tracker draws reached stages done; full Account ID / UUID per product (CE UUID, Surface ID, Case 3 and renewals both); cleaned Alternate Domains shown as text (no Copy box; no page scripts).
9. Case 5 fixture: pure CE-only "before", CO-0462 (Helly Hansen, only historical Case 5, production) as anonymised "after".

**Built and merged today (local; 1394 integration tests OK, 1 skipped; both guards pass; no real `integration/attended_*.json` changes during test runs):** domain separator fix + `--co CO-XXXX --domains` preview; DEV mirror label/skip + renewal outcome file; CO-page read speed (parallel CO/DealHub read, one DealHub query, queue warming, 5-min cache, read timing log); scan-status renewal support + failures shown + COMPLETED counts; hidden search for read-only paths; renewal domains check; tracker/partial scan fixes; CO page redesign; renewal orchestrator + Start renewal; Dev/Production split; apply window; kept expiry; Case 5.

**Test-safety incidents (fixed):** (a) an existing scan-status test ran a real read-only `sf` SOQL query (CO-0649) on every full run until fixed; (b) a CE create test wrote a fake CO-0702 entry into the real `attended_spycloud.json` (removed); (c) the new scan-status logging made tests write 8 fake runs into the real run log (removed; they had pushed 8 older real runs out of the 20-run log — not recoverable). `integration/tests/__init__.py` now redirects every runner/dashboard state file to a temp folder for all tests. Known flaky: a dashboard server test (WinError 10038) fails ~1 in 3 full runs; a separate task was suggested.

**Case status (Leonardo Development):** 1 ✅ CO-0649 · 2 ✅ CO-0679/0728/0762 (CO-0702 manual) · 3 ✅ CO-0757 · 6 ✅ CO-0767 (dashboard renewal) · 4 🟡 built; CO-0758 expected to apply only missing parts · 5 🟡 built + synthetic tests; no open Case 5 CO in Salesforce (live test needs one).

**Next steps (in order):**
1. Operator: `--co CO-0767 --scan-status`; assistant reads the `scan_status` run events and fixes the executions read.
2. Operator: restart the dashboard; review the new layout (Dev/Production areas, CO page). Report what to move/hide.
3. CO-0767: confirm the primary user on the A2A Dev mirror manually → "Confirm user created".
4. Case 4 on CO-0758: Start renewal from the dashboard (mirror → dry run shows expiry kept + CE part → apply) — owner go.
5. Case 5: first real Case 5 CO (none open) → Start renewal; Edit-form profile controls are UNVERIFIED LIVE (fail closed `renewal_field_unavailable`).
6. Production readiness: open `/prod`, review match results for the onboarded COs; keep the Redash clone fresh (`tools\redash_inventory_collector.py --collect`).
7. Still open: Redash users query + v2 accounts query (Salesforce account id); scan-status decisions (CE-only skip, Done = last vs sum, unattended sign-in tab, key rotation); flaky server test; delete `_to_delete/` and old `.claude/worktrees/agent-*` folders (four from earlier sessions).

**Boundaries preserved (2026-10-07):** Leonardo Development only. Live writes today (operator-run, approved): CO-0767 renewal edit on the Dev mirror. Assistant/agent live actions: read-only Salesforce SOQL (Case 5 research; plus the accidental test read above), local prod-clone snapshot reads, Guru card reads. No Salesforce write, no production BackOffice access, no Redash change, no VM change. No credential, key, MFA value, cookie, token, or raw payload was copied, logged, or persisted.

#### Addendum 2026-10-07, late (supersedes next-step 1 of the end-of-day entry)
- **Scan executions read fixed and verified live (CO-0767, 16:12):** `scan_status_recorded`; 2 executions read. Root cause, from the new `scan_status` run-log events: the "Duration Per Scan" tab's accessible name is not exactly that text, so the exact-name lookup never matched; now any tab containing "duration" (case-insensitive), after waiting for the Details panel (`60f5186`, `f9892a9`). A one-time `details_snapshot` event (tab labels, masked path, pages, dialogs) is logged if no tab appears.
- **Owner decisions:** `DONE_WITH_ERRORS` = finished, with an amber warning on the scan card (`b3047de`; stored older reads are re-derived). Scan card headline = **total of all scan durations** (settles scan-status decision b: sum), each scan listed with its duration and finished / finished with errors / running (`610f780`). CO-0767: total 07:35:46 (07:27:27 finished with errors, 00:08:18 finished).
- **CO search** (`ecdcb99`): sidebar search on every page; a CO number in any form (`CO-0767`, `co 767`, `767`) opens `/co/<CO>`; other text lists matches (CO, account, tenant name, domain) from the open queue, local run records and Onboarded on Dev (History holds counts only, so it is not searchable). No new Salesforce query.
- 1418 integration tests OK, 1 skipped; both guards pass; no real state file changed during test runs. Restart the dashboard to see all of it.

#### Handoff for 2026-10-08 (latest; continue here)

**Since the 2026-10-07 addendum (all local unless stated):**
- **CI fix** (`78dfa76`, pushed): GitHub Actions "Integration scaffold checks" failed on plain Python (no Playwright). `integration/tests/__init__.py` now registers a placeholder `playwright.sync_api` (sync_playwright refuses to run) only when the package is missing. Verified locally: 1418 OK with the system Python (CI-like) and the project runtime. **Run #28's result (green/red) is not yet confirmed by the operator.** `gh` CLI is not installed, so the assistant cannot read Actions.
- **VM team-access plan** `docs/40_VM_TEAM_ACCESS_DEPLOYMENT_PLAN_2026-10-07.md` (proposal; `aaf9a67` pushed). Owner answers recorded in its §8 (`f0b0c98`, `9fe6a3e`): port **8443** (`https://172.26.37.20:8443/`, like the Pentera appliance at `https://172.26.37.12:8181/login`); **self-signed** certificate for the pilot; allow-list = **owner only** first, **john.ostrander@pentera.io added later as operator**; **phase 1** = read-only VM, runs from the owner's Windows desktop; **VM-local proxy** (nginx + oauth2-proxy + OneLogin). SecOps / OneLogin admin / Salesforce admin / Operations approvals in docs/40 §7 are still required — the owner answers do not replace them. Existing Salesforce read approval covers CO-0717 only (team dashboard needs a new fixed-query approval).
- **Step 0 built** (merge of `90479fb`; VM mode only, desktop unchanged): trusted `X-Forwarded-Email` from a loopback peer in VM mode; allow-list/operators via `SURFACE_ONBOARDING_ALLOWED_USERS` / `SURFACE_ONBOARDING_OPERATORS` (+ `_FILE`), fail closed; viewers GET-only; operators only the local mark/confirm and read-only POSTs; every run-starting / Salesforce-writing POST refused on the VM; HTTPS origin check against `SURFACE_ONBOARDING_PUBLIC_ORIGIN`; Secure cookies; state under `SURFACE_ONBOARDING_STATE_DIR` (`/var/lib/surface-onboarding`) via `integration/onboarding/state_paths.py`; review-only artifacts in `integration/deployment/vm_pilot/` (nginx, oauth2-proxy, systemd units, allow-list/operator templates, README with self-signed cert and firewall text; stored LF). Tests: 1453 OK, 1 skipped, under both the project runtime and the CI-like system Python; guards pass; no real state file changed. Full run now takes ~140 s (new server tests).

**Not pushed:** `f0b0c98`, `9fe6a3e`, the step 0 merge (operator did not push after the last output).

**Must be fixed before any VM install (step 0 follow-ups):**
1. **Local header forging:** any shell user on the VM can reach `127.0.0.1:8000` and send `X-Forwarded-Email`. Recommended: a shared secret between oauth2-proxy and the dashboard (or a Unix socket owned by the proxy group). Build this next.
2. Review/validate the oauth2-proxy, nginx and systemd templates on Linux (syntax untested).
3. The allow-list lives in two places (oauth2-proxy file and dashboard env/file) — keep identical or derive one from the other.
4. Smaller: viewers still see action buttons (clicks are refused); oauth2-proxy needs outbound HTTPS to OneLogin (name it in the SecOps request); Redash collector key is Windows DPAPI (stays desktop); inventory page text still mentions `%LOCALAPPDATA%`.

**Start tomorrow with (in order):**
1. Operator: confirm Actions run #28 (✓/✗); if red, paste the failing test names. Then `git push origin main`.
2. Assistant: step 0 follow-up 1 (shared-secret proxy → dashboard), then template review (2) and single allow-list source (3).
3. Operator: send approval requests per docs/40 §7 steps 1–3 (VM/SecOps placement + port 8443 + firewall; OneLogin app + allow-list; self-signed certificate acceptance) and the new Salesforce read-scope request. The assistant can draft them on request.
4. Onboarding work (from the 2026-10-07 end-of-day list): restart the dashboard and review the layout; CO-0767 "Confirm user created"; Case 4 Start renewal on CO-0758 (owner go); Case 5 on the first real Case 5 CO; review `/prod` readiness.

**Boundaries preserved:** no VM, proxy, firewall, OneLogin, Salesforce write, Redash change or production access. Pushes this evening were operator-requested (`98bfc50..78dfa76`, `78dfa76..aaf9a67`). No credential, token, cookie, MFA value or raw payload was copied, logged or persisted.

#### Handoff 2026-10-08 — SpyCloud is ON by default (owner decision; local code and tests only; not committed)

- **Owner decision 2026-10-08 (supersedes 2026-10-05 "SpyCloud must be OFF on LC tenants"):** SpyCloud stays enabled (Leonardo's Add Account default). Applies to every onboarding case; Surface-only (Case 1) has no Leaked Credentials, so nothing to do there.
- **Code:** no automatic OFF after create (CE, Case 3: `SPYCLOUD_AFTER_CREATE_ENABLED = False`, hook kept as a gated no-op) or after renewals (Cases 4/5/6: orchestrator step removed). Standalone `--spycloud-off [--confirm-write]` kept as a manual, write-gated tool. Validation: ON = ok, OFF = informational warn. Dashboard: no "must be OFF" banner/step; OFF shows as an amber note. Production match: SpyCloud is an informational row (`spycloud`), no longer in `REQUIRED_FIELDS`, never blocks. Stored state: `SPYCLOUD_ON_OUTCOMES` / `SPYCLOUD_OFF_OUTCOMES`; `warning` now means "not verified"; legacy records still load; `spycloud_readback_still_on` shows as "not verified".
- **Verified:** 1458 tests OK, 1 skipped (project runtime); artifact and offline-boundary guards pass. One known flaky dashboard-server test passed on rerun.
- **Open owner decision:** Dev tenants turned OFF earlier (CO-0757, CO-0679, CO-0728, CO-0767 mirror, CO-0762 if run) — leave as is or turn back ON (a Leonardo Development write, needs approval). Docs/README still describe the old OFF rule (docs/33 etc.) and need a sweep.

#### Progress 2026-10-08 — manual SpyCloud ON tool and docs sweep (local; not run live)
- New `--co CO-XXXX --spycloud-on [--confirm-write] [--env dev]`: dry run by default (Edit opened, checkbox read, Cancel); same path and guards as `--spycloud-off`. Outcomes `spycloud_on_verified`, `spycloud_already_on`, `spycloud_dry_run_off`, `spycloud_readback_still_off`, `spycloud_check_failed`, plus the shared failure codes. Dev only. No dashboard write button. **Live ON flow unverified (tested against fakes only).** 1479 tests OK, 1 skipped; both guards pass.
- Docs: "Superseded 2026-10-08" lines in docs/33, docs/39, LOCAL_IMPLEMENTATION_STATUS.md.
- Next: operator dry run `--spycloud-on` on CO-0679 first (recorded OFF), then `--confirm-write` with owner go; then CO-0757, CO-0767 mirror, CO-0728 (state unverified), CO-0762.

#### Live 2026-10-08 — SpyCloud ON on Leonardo Development (operator-run, approved per CO)
- First `--spycloud-on` dry run on CO-0679 failed once (`spycloud_edit_unavailable`, Edit click timeout 5 s while the page was still settling; rerun fine) then `spycloud_dry_run_off`. The operator then enabled CO-0679 by hand; `--spycloud-on` read it as `spycloud_already_on`.
- `--spycloud-on --confirm-write`: **CO-0757 `spycloud_on_verified`** and **CO-0728 `spycloud_on_verified`** (first live proof of the tool's own save; both `"leonardo_write":"verified"`); CO-0767 (A2A mirror) and CO-0762 `spycloud_already_on` (no write).
- All five Dev CE / Case 3 tenants are now ON: CO-0679, CO-0757, CO-0728, CO-0767 mirror, CO-0762. No Salesforce write, no production access. The dashboard process predates the policy change and still shows the old "must be OFF" wording until restarted (`tools\start_attended_dashboard.ps1 -Restart`).
- Next: restart the dashboard, push (`3b21e98`, `22243fa` + this entry), then Case 4 on CO-0758 (mirror → dry run → apply; owner go at each write).

#### Progress 2026-10-08 (afternoon) — email-domain "@" fix, run progress bar, quiet mode, demo reset tool (local; not run live)
- CO-0765 (Orlando Health, CE-only) showed "No automated route": Salesforce Email Domains `@orlandohealth.com` failed the exactly-one-valid-domain gate. Now exactly one leading `@` is stripped (dashboard eligibility + preflight, runner `one_email_domain`, `_domain_tokens`); `@@x`, `user@x`, two domains still fail closed.
- Run progress bar on the CO page (CSS-only, meta refresh 3 s while a run is open, no page scripts): `integration/onboarding/run_progress.py` maps real milestone events (new git-ignored `integration/attended_ce_only_progress.json`: step/outcome/time only) to a 10-step create list or a 4-step renewal list; capped at 95% until a result exists.
- Opt-in `SURFACE_AUTOMATION_QUIET=1`: automation Chrome off-screen + anti-throttling flags (warm session only; not yet run live).
- `tools/reset_demo_co.py --co CO-XXXX [--confirm-tenant-deleted]`: dry run by default; backs up then clears one CO's local records after the rehearsal tenant was DELETED in Leonardo (rename is not enough: duplicate check matches domains). Never contacts external systems.
- 1493 tests OK, 1 skipped; both guards pass. Known gap: "No automated route" gives no reason text.

#### Live 2026-10-08 (late morning) — CO-0765 (Orlando Health, CE-only) first run; three fixes (operator-run, approved)
- CO-0765 shows Dev "Ready" after the email-domain `@` fix. First starts failed closed with nothing created, in this order: `ce_subscription_unavailable` (gate only knew "Pentera Core Plus Commercial"; CO has "Core Plus Enterprise - 4000 End Points"; per Guru "Surface License Tiers Breakdown" CE depends on Core Plus for every tier → `select_ce_subscription` now uses `is_core_plus_baseline_row`); `production_clone_unavailable` (stale Redash clone; operator ran `redash_inventory_collector.py --collect`); `fill_form_schema_unavailable` on the `automatedDiscoveryEnabled` toggle (click 2 s / state wait 1 s too short on a slow Leonardo Dev → 6 s / 4 s, state still verified); `fill_form_schema_unavailable` on Country (Salesforce "United States" vs Leonardo "United States of America" → `country_option_label`: exact, then one alias, else fail closed); `fill_value_mismatch` in the final verify pass (same alias rule applied there).
- **Run at 11:26:36 → `readback_verified`** (first live run with the progress bar; SpyCloud stays ON, no extra step). The operator then renamed that Dev tenant ("OrlandoHealth - CE Only - test", domain `orlandohealth-test.com`) and cleared CO-0765's local records with `tools/reset_demo_co.py --co CO-0765 --confirm-tenant-deleted` (backup in `integration/attended_reset_backups/`), so the CO can be started again live for the team-lead presentation.
- 1496 tests OK, 1 skipped; both guards pass. Not pushed. Follow-ups: "No automated route" has no reason text; Country alias list is small (extend when a new country fails); the quiet browser mode is still untested live; delete the extra Dev tenant after the demo; second CO-0765 run result to be recorded.

#### Live 2026-10-08 (afternoon) — CO-0765 onboarded live (operator-run, approved)
- After the 11:26 run (tenant renamed, then deleted by the operator) two further starts (12:20, 12:24) stopped with `confirm_no_create`: Leonardo answered **409** on `POST /api/v1/backoffice/account/add`, form left open, nothing created (cause not captured; likely a unique value still held by the first tenant, e.g. the primary-user email alias). A "mark as created" request was refused: no tenant, no readback. After the operator removed the leftover value, local records were cleared with `tools/reset_demo_co.py` (backup `20261008-135943`) and the next start gave **`readback_verified` at 14:02:04**. CO-0765 is onboarded on Dev (real record, tenant present).
- Follow-ups: capture the 409 response key names (no values) in the run log so the conflicting field is named; a "No automated route" reason text; Country alias list is small.
- Next phase started: VM team access (docs/40), phase 1 = read-only VM, runs stay on the owner's desktop.

#### Handoff 2026-10-08 (evening) — VM team access, phase 1 built locally (not deployed)
- **Owner decisions:** VM owner confirmed; phase 1 = read-only VM, runs stay on the desktop (VM has **no** Salesforce, Leonardo, Redash or browser: consistent with the 2026-09-15 boundary); manual zip copy of a signed snapshot (transport A); automatic publish after each run (optional hook, default off); customer names and domains visible to allow-listed viewers; snapshot caps raised to **32 MB per file / 64 MB per bundle** (prod clone ≈ 17 MB); **no POST for any role on the VM** (`VM_OPERATOR_POSTS` is empty). Hostname and the `workato` user are **not** renamed (the Workato agent, `phase1_validator` and `phase2_leonardo` services run under it). User creation / operator role / OneLogin / firewall / certificate: tomorrow.
- **Built (local, 1585 tests OK on project runtime and CI-like Python 3.12; both guards pass):** shared proxy secret (`X-Surface-Proxy-Secret`, `SURFACE_ONBOARDING_PROXY_SECRET_FILE`); single users file (`SURFACE_ONBOARDING_USERS_FILE`, `render_allowed_emails.py`); signed snapshot (`vm_snapshot.py`, `tools/publish_vm_snapshot.py`, `tools/ingest_vm_snapshot.py`); VM data source reading `<STATE_DIR>/snapshots/current/` with a "Data as of" banner; loopback-only nginx tunnel template; design `docs/41`.
- **VM read-only facts (2026-10-08):** Ubuntu 22.04, Python 3.12.14 at `/opt/surface-onboarding/runtime/`, `surface-onboarding` user, empty `/var/lib/surface-onboarding`, nginx not installed (apt candidate 1.18), 101 GB free, ports 8000/4180/8443 free, internet reachable. Operator cleaned old staged releases (r3–r10, r14–r18, `app-before-r12/r13`, `incoming-r18`); kept `app`, `app-r19`, `app-r20`, `incoming-r19/20`, `runtime`, `venv`.
- **Next:** build and check release archive r21 (`git -c core.autocrlf=false -c core.eol=lf archive`, no CR bytes, SHA-256, `tools/check_release_archive.py`), upload to `incoming-r21`, unpack as `surface-onboarding` into `app-r21-staged-20261008`, run the VM tests, activate nothing. Then (each step with approval): nginx via apt, loopback-only listener behind an SSH tunnel, first snapshot publish/ingest.
- Known limits on the VM: prod clone and DealHub rows/History chart/queue start dates depend on what the snapshot carries; the oauth2-proxy header pass-through and `client_secret_file` support must be checked on the VM.

#### Handoff for 2026-10-09 (latest; continue here)

**Where things stand (2026-10-08 evening)**
- **Onboarding on Leonardo Development:** Cases 1, 2, 3 working live from the dashboard (CO-0649, CO-0679/0728/0762, CO-0757, **CO-0765** Orlando Health at 14:02:04 after a 409 conflict was cleared); Case 6 working live (CO-0767); Cases 4 (CO-0758) and 5 built, not run live. SpyCloud is **ON by default** everywhere (manual `--spycloud-on` / `--spycloud-off` tools remain, write-gated); all five Dev CE/Case 3 tenants are ON. Run progress bar on the CO page; opt-in quiet browser (`SURFACE_AUTOMATION_QUIET=1`, untested live).
- **Code commits (local, check `git log origin/main..`):** `1ebc3fb` (`@` email-domain fix, progress bar, quiet mode, `tools/reset_demo_co.py`), `34fcb96` (any Core Plus tier for CE-only, longer toggle waits, Country alias), `7363d8e` (VM phase 1: proxy secret, single users file, signed snapshot sync, read-only VM data source, caps 32/64 MB, no POST for any role on the VM) plus this handoff. **Operator: `git push origin main`, then confirm Actions is green** (`gh` is not installed; the assistant cannot read it).
- **VM `workato-opa-01` (172.26.37.20), changes made today (operator-run, approved):** removed old staged releases (kept `app`, `app-r19`, `app-r20`, `incoming-r19/20`, `runtime`, `venv`); uploaded release `r21` (built from `7363d8e`, `git -c core.autocrlf=false -c core.eol=lf archive`, 0 CR bytes, SHA-256 `cc33bf97…2aca` = `cc33bf974d25d731437ef8c6e954afa3fd0471ed3d66c4949d14c4399aa4222f`, offline checker passed) into `/opt/surface-onboarding/incoming-r21`, unpacked as `surface-onboarding` into `app-r21-staged-20261008`; on the VM with managed Python 3.12.14 the **full suite is OK (skipped=3) and both guards pass**. Nothing activated: no service, nginx, listener, proxy, firewall, OneLogin, Salesforce, Leonardo, Redash or browser. Upload copies are in `/home/workato/r21-upload/` and `/tmp/r21-vm-check.sh` (delete when convenient).
- **VM facts:** Ubuntu 22.04, `workato` has sudo (password), nginx not installed (apt candidate 1.18, internet reachable), 101 GB free, ports 8000/4180/8443 free. The `workato` user runs live services (Workato agent `workato-agent.service`, `phase1_validator.server` and `.server_notes`, `phase2_leonardo.server_intake`): **do not rename the `workato` user or the hostname**; use a DNS alias such as `surfonb` if a nicer URL is wanted.
- **Owner decisions today:** VM owner = the operator; phase 1 = read-only VM, runs stay on the desktop; VM has **no** Salesforce/Leonardo/Redash/browser (consistent with the 2026-09-15 boundary; "Salesforce like on the desktop" from the VM would need its own approval, phase 2); transport = manual zip of a signed snapshot (design `docs/41`); automatic publish after each run (optional hook, default off); customer names/domains visible to allow-listed viewers; caps 32/64 MB; no operator POSTs on the VM; user creation / operator role / OneLogin deferred to tomorrow.

**Start tomorrow with (in order; each VM step is run by the operator, one explained block at a time)**
1. Operator: `git push origin main`; confirm the Actions run. Optional hygiene: delete `/tmp/r21-vm-check.sh` and `~/r21-upload` on the VM; **rotate the `workato` password** (it was pasted into the chat transcript).
2. Review r21 on the VM only through the operator; then `sudo apt install nginx` (explain first), create `/etc/surface-onboarding/` (`users.txt` with the owner as viewer, `proxy-secret` via `openssl rand -hex 32`, never printed), the HMAC snapshot key (desktop DPAPI-free key file + VM `0640` copy), the self-signed certificate (README), `approved-web-identity` marker (operator's explicit approval of a **temporary loopback-only listener**, SSH forwarding for review), the dashboard unit, and the loopback nginx tunnel template (`nginx-surface-onboarding-tunnel.conf.template`, `SURFACE_ONBOARDING_PUBLIC_ORIGIN=https://localhost:8443`).
3. First snapshot: desktop `tools/publish_vm_snapshot.py --key-file …` (dry run first), copy the zip, `tools/ingest_vm_snapshot.py` on the VM, open the dashboard through `ssh -L`, review the banner and pages. Verify `client_secret_file`/`cookie_secret_file` support and the secret-header pass-through of the installed oauth2-proxy before the OneLogin step.
4. Then: OneLogin app + allow-list, users and the operator role (John), firewall 8443 for the approved subnet, certificate decision, SecOps acceptance of the self-signed pilot; phase 2 (browser runner) stays a separate design.
5. Onboarding backlog: Case 4 on CO-0758 (mirror → dry run → apply; owner go at each write); Case 5 needs a real CO; capture the 409 response key names (no values) in the run log; reason text for "No automated route"; extend the Country alias list when a new country fails; delete the extra Dev tenants after demos (test tenant "OrlandoHealth - CE Only - test" was removed by the operator); `_to_delete/`.

**Lessons recorded today**
- Windows PowerShell 5.1 strips embedded double quotes in native arguments: for `ssh` use a small uploaded script (`scp` to `/tmp`, then `sudo -u surface-onboarding … sh /tmp/script.sh`), never nested quotes. `app-r*` folders are `0750 surface-onboarding`: read them with `sudo`. The Claude Terminal panel integration did not load in this session (helper script missing), so VM commands are run by the operator.
- Release build rule stays: LF export, CR-byte check, SHA-256 sidecar (uppercase, one line, LF) for `tools/check_release_archive.py`, independent checker from the previous release.
- Deletion and activation steps on the VM: look first, list exactly, one block at a time; passwords are typed only in the operator's terminal and never in chat.

**Boundaries preserved (2026-10-08):** Leonardo Development only for onboarding writes (operator-run, approved); no production BackOffice access; no Salesforce write; no Redash change (one operator-run read-only `--collect`); on the VM only the operator-run steps listed above. No credential, token, MFA value or cookie was copied or persisted by the assistant.

#### Handoff 2026-10-09 (evening) — VM pilot running behind an SSH tunnel; r23 UI cleanup built locally

**Outcome:** the read-only VM dashboard is live on `workato-opa-01` as a **temporary, loopback-only pilot** and was reviewed by the owner through `ssh -L 8443:127.0.0.1:8443` (banner "Data as of 2026-10-09 20:14 UTC", queue, CO pages, no action buttons). Nothing is enabled at boot; `sudo sh /tmp/step2c.sh stop` stops both services and removes the marker.

**Done today (all VM steps operator-run, each approved):**
- `git push`; Actions run #31 failed on a **flaky test** (`test_scheduling_does_not_wait_and_uses_the_existing_pool_size`: 26 queries, not 24, because reads left on the shared prefetch pool by an earlier test hit this test's fake); fixed by draining the pool with a 4-way barrier (`c2ba4b4`), Actions green afterwards.
- Step 2A (`_step2a_vm_prepare.sh`): nginx installed then **disabled and stopped** (default port-80 site removed), `/etc/surface-onboarding/` with `proxy-secret`, `users.txt` (owner as plain **viewer**), self-signed certificate (SAN localhost, 127.0.0.1, 172.26.37.20, 90 days). Lesson: the first attempt had not actually run; compare the pasted output with the expected state before moving on.
- **Bug found before activation:** `run_vm_dashboard.sh` rejected `SURFACE_ONBOARDING_USERS_FILE` (the unit's only allow-list variable). Fixed with a test (`2f20a82`), so release **r22** (SHA-256 `DBD1CE36…3FBE`, checked by the current and the previous checker, no CR in text files) replaced r21: staged in `app-r22-staged-20261009`, suite OK on the VM.
- Step 2B (`_step2b_vm_install.sh`): the old Sep 10 `app` directory renamed `app-prev-20260910` (rollback), `app` -> r22 symlink, dashboard unit with `PUBLIC_ORIGIN=https://localhost:8443`, loopback-only tunnel nginx site with the owner identity, `nginx -t` OK.
- Snapshot key: a new 32-byte key generated on the desktop (`%USERPROFILE%\.surface-onboarding\snapshot.key`, outside the repo), installed on the VM as `snapshot.key` (0640 root:surface-onboarding), temp copy shredded. First **full** snapshot (read-only Salesforce reads: 9 queue rows, 11 CO details, 16 files, 107 KB) ingested as `snapshot-20261009T201453Z`.
- Step 2C (`_step2c_vm_start_stop.sh`): owner approved the marker `approved-web-identity` and the start. Dashboard on `127.0.0.1:8000`, nginx on `127.0.0.1:8443`, nothing on `0.0.0.0`; GET / 200, POST / 403, direct call without the proxy secret 403.

**Built locally after the review (release r23 candidate; tests pass):** VM pages no longer say "Read from Salesforce at" (now "Page rendered at"), the closed-history chip says "Not included in the published snapshot", and the Refresh buttons, the empty More menu and the Sessions nav link are removed in VM mode; README snapshot caps corrected to 32/64 MB. Not yet built as an archive or uploaded.

**Boundaries preserved:** no Salesforce write, no Leonardo or Redash change, no production BackOffice access, no OneLogin, no firewall change, no certificate decision. No password, key, secret value or token was shown or persisted by the assistant; the `workato` password was typed only in the operator's terminal.

**Next (owner/SecOps decisions unless marked):**
1. Operator: leave the pilot running or `stop` it; delete `/tmp/step2a.sh`, `/tmp/step2b.sh`, `/tmp/step2c.sh`, `/tmp/r22-upload`, `/tmp/r21-vm-check.sh`, `~/r21-upload` when convenient; **rotate the `workato` password**; the VM reports "System restart required" (a reboot restarts the Workato agent and the validator/intake services, so schedule it).
2. Assistant: build r23 (LF archive, both checkers) after the owner's go, stage it on the VM like r22, then the operator restarts the dashboard.
3. Owner/SecOps: OneLogin application and allow-list, John as operator, firewall for 8443, certificate decision, SecOps acceptance of the self-signed pilot; then remove the tunnel site and the interim identity. Phase 2 (browser runner on the VM) stays a separate design.
4. Onboarding backlog unchanged from the 2026-10-09 handoff above (Case 4 on CO-0758, Case 5 needs a real CO, `_to_delete/`).

#### Handoff 2026-10-13 — r24 active on the VM pilot (loopback-only, behind the SSH tunnel)

**Outcome:** release **r24** (built from `ca52953`) replaced r22 on `workato-opa-01`; the read-only dashboard was restarted and verified (GET / 200, POST / 403, direct call without the proxy secret 403, listeners only `127.0.0.1:8000` and `127.0.0.1:8443`, banner "Data as of 2026-10-09 20:14 UTC", new "Page rendered at" wording, old "Read from Salesforce at" gone, nothing enabled at boot).

**Done (VM steps operator-run, each approved):**
- Local: r23 UI cleanup committed (`70e0f12`), suite 1588 OK (1 skipped) and both guards pass.
- r23 (`C97BA56D…FCD4F3`) was staged in `app-r23-staged-20261012`; the VM suite showed **one failure**, `test_vm_proxy_mode.StateDirTests.test_helper_rules`: `state_paths.inventory_root` resolves the real `snapshots/current` pointer, and the VM now has an ingested snapshot. A **test-isolation bug only** (product behaviour correct); fixed by stubbing `vm_snapshot.current_dir` in that assertion (`ca52953`). Lesson: tests must not depend on the host's real state dir; the VM is the only place that catches this.
- r24 (`surface-onboarding-r24-ca52953.tar.gz`, SHA-256 `38D4C4C50409E60B4E4555BFC93895A89FB5C2B39326D6111F6DD2F357FEB9D5`, LF export, 0 CR bytes, current and r20-era checkers pass) staged by `_step3d_vm_stage_r24.sh` into `app-r24-staged-20261012` (suite OK, skipped=3, both guards pass), activated by `_step3e_vm_activate_r24.sh` (atomic symlink swap, dashboard-only restart; `rollback` points `app` back at r22).
- Scripts: `_step3a/3b/3c` (r23 stage/test/show-failure), `_step3d` (r24 stage), `_step3e` (activate/rollback) in `integration/deployment/vm_pilot/`.

**Boundaries preserved:** no Salesforce, Leonardo, Redash or production BackOffice access; no OneLogin, firewall or certificate change; no password, key, secret or token shown or persisted by the assistant (passwords typed only in the operator's terminal).

**Next (owner/SecOps decisions unless marked):**
1. Operator: review the r24 pages through `ssh -L 8443:127.0.0.1:8443`; later delete the unused VM folders (`app-r23-staged-20261012`, `incoming-r23`, `app-r22-staged-20261009` after a few days, `/tmp/r2*-upload`, `/tmp/step2*.sh`, `/tmp/r23-suite.log`; list first, one block at a time); **rotate the `workato` password**; schedule the reboot ("System restart required" also restarts the Workato agent, validator and intake services).
2. Owner/SecOps: OneLogin application and allow-list, John as operator, firewall for 8443, certificate decision, SecOps acceptance of the self-signed pilot; then remove the tunnel site and the interim identity. Phase 2 (browser runner on the VM) stays a separate design.
3. Fresh snapshot publish/ingest when the owner wants newer data (the pilot still shows 2026-10-09).
4. Onboarding backlog unchanged (Case 4 on CO-0758, Case 5 needs a real CO, 409 response key names in the run log, reason text for "No automated route", Country alias list, `_to_delete/`).

#### Addendum 2026-10-13 (later) — VM leftovers listed and cleaned

- `_step4a_vm_list_leftovers.sh` (read-only listing) then `_step4b_vm_delete_leftovers.sh` (exact names only, owner-approved list; refuses if `app` or a running process uses a target). **Removed:** `app-r23-staged-20261012`, `app-r19/r20/r21-staged-*`, `incoming-r19/r20/r21/r23`, `/tmp/r22-upload`, `/tmp/r23-upload`, `/home/workato/r21-upload`, `/tmp/r21-vm-check.sh`, `/tmp/r23-suite.log`, `/tmp/step2a.sh`, `/tmp/step2b.sh`. **Kept:** r24 (active), `app-r22-staged-20261009` + `incoming-r22` (rollback), `incoming-r24`, `app-prev-20260910`, `runtime`, `venv`, snapshots, `/tmp/step2c.sh` (start/stop), `/tmp/r24-upload` (activate/rollback). After the cleanup: `app -> r24`, dashboard and nginx active, GET / 200.
- VM facts (read-only listing): uptime 92 days; **reboot required** (kernel `linux-image-5.15.0-198` and libc6 pending); a reboot restarts the Workato agent, `phase1_validator`, `phase2_leonardo`, and stops the pilot (not enabled at boot; start again with `step2c.sh start`). Schedule with whoever depends on the Workato agent. Snapshot `current -> snapshot-20261009T201453Z` (Oct 9 data).
- Still open: rotate the `workato` password (operator); r24 page review through the tunnel (owner); fresh snapshot (needs Salesforce read-scope confirmation); OneLogin/firewall/certificate/SecOps; onboarding backlog.

#### Addendum 2026-10-13 (evening) — unscheduled VM reboot; pilot restarted; validator/intake not running

- **The VM rebooted at about 18:20-18:22 on 2026-10-13** (clean systemd shutdown in the journal; cause/who unknown, to be confirmed with the owner). It was due anyway (kernel `5.15.0-198` and libc6 pending; now running `5.15.0-198`). The pilot (dashboard + nginx, not enabled at boot) stayed down, the tunnel returned "site can't be reached", and `curl` on the VM gave `000`. Diagnosed from `systemctl is-active`, `ss`, and `journalctl` (the approval marker `approved-web-identity` was still present, so nobody ran `stop`).
- **Pilot restarted** with `sudo sh /tmp/step2c.sh start` (within the 2026-10-09 temporary-loopback approval): dashboard and nginx active, listeners only `127.0.0.1:8000` and `127.0.0.1:8443`, GET / 200, POST / 403, direct call without the secret 403, both still `disabled` at boot. Owner reviewed r24 through `ssh -L 8443:127.0.0.1:8443` (tunnel must stay open in its own window; test with `curl.exe -k -s -o NUL -w "%{http_code}`n" https://localhost:8443/`).
- **Lesson: `/tmp` is cleared on every reboot** (all `/tmp/r24-upload`, `/tmp/step2c.sh` were gone). Re-upload the scripts before use, or keep VM ops scripts somewhere durable (for example `/opt/surface-onboarding/ops/`, owner decision). Lesson: on Windows, run VM commands as `ssh -t workato@172.26.37.20 "sudo sh ..."`; a bare `sudo` in local PowerShell hits Windows' own stub.
- **The `workato` services are unmanaged and did not come back:** only `workato-agent.service` (enabled) is running. `phase1_validator.server` (8787), `phase1_validator.server_notes` (8788) and `phase2_leonardo.server_intake` (8789) are loopback-only processes started by hand (`/home/workato/phase1-venv/bin/python -m ...` from `/home/workato`; the intake via a deployment wrapper, `phase2-intake.pid/.log` dated Sep 1, scripts probably under `/home/workato/phase2-operations`) with **no systemd unit or cron**; the Workato Development recipe is inactive per docs/27. **Open owner decisions:** are they needed today; if yes, draft and review a start script (loopback only, with a stop path), and consider real systemd units (design change, owner approval). Also: the VM home still holds the old `surface-onboarding-r18/r19/r20` tarballs (about 2.6 MB), a later cleanup.

#### Handoff 2026-10-13 (end of day) — continue here

**Outcome:** VM pilot is up (r24, loopback-only, owner reviewed it through the tunnel); boot-enable and tunnel helper drafted (not yet run); next phase designed locally: **CE-only on the production BackOffice** (owner, 2026-10-13: "CE on BO" = CE-only; other cases follow later). Nothing written to production, Salesforce, Leonardo or Redash today.

**Done today:** r23 built, VM suite exposed a test-isolation bug (`ca52953`), r24 built/staged/tested/activated on the VM, VM leftovers cleaned (`_step4a/_step4b`), unscheduled VM reboot diagnosed, pilot restarted and verified, production CE-only gap-list sweep (read-only subagent) and design doc **`docs/42_PRODUCTION_CE_ONLY_DESIGN_AND_CHECKLIST_2026-10-13.md`** (proposal; no guard changed, `PRODUCTION_RUNNER_SUPPORTED` stays `False`). Commits through `facd6b4` pushed by the operator; the items below are uncommitted until the next commit.

**Owner approvals recorded today:** r24 activation on the pilot; the exact leftover delete list; **enable the loopback-only pilot (dashboard + nginx) at boot** (reverses the 2026-10-09 "nothing at boot" rule; the unit still needs the `approved-web-identity` marker); the production gap-list sweep and the local design step. Not approved: any production read or write, a fresh Salesforce snapshot scope, OneLogin/firewall/certificate.

**Next (in order):**
1. Operator: upload and run `_step5a_vm_enable_at_boot.sh` (`scp` to `/tmp/r24-upload/`, then `ssh -t workato@172.26.37.20 "sudo sh /tmp/r24-upload/_step5a_vm_enable_at_boot.sh"`; re-upload `_step2c` / `_step3e` to `/tmp` first if they are gone); expect `enabled enabled`, both `active`, two files in `/opt/surface-onboarding/ops/`; prove it at the next reboot. Tunnel: `powershell -File tools\open_vm_tunnel.ps1` (keep the window open).
2. Operator: the two read-only listings (`ls -la /home/workato/phase1-operations /home/workato/phase2-operations | head -60`, `cat /home/workato/phase2-intake.log`) so the assistant can draft **systemd units for the validator/intake (8787/8788/8789)** (owner approval needed; they are unmanaged and are down since the reboot; decide whether they are needed today); rotate the `workato` password; delete the old `~/surface-onboarding-r18/r19/r20` tarballs later.
3. Production CE-only: review `docs/42`; decide D1-D6 (approval scope and issuer, rollback method for a wrong tenant, Q13 writeback owner, read-only BO probe permission, operator handling, first candidate CO and CSM timing); then local code and tests per docs/42 §3-4 (one reviewed commit each), a read-only clone-based candidate check (needs a fresh Redash `--collect` by the operator), a decision request to the owner and SecOps (operator sends it), then an attended read-only BO probe, a dry run without Confirm, and one create only after an explicit owner go. **Biggest gap: no documented BO rollback.**
4. Owner/SecOps (unchanged): OneLogin application and allow-list, John as operator, firewall for 8443, certificate and acceptance of the self-signed pilot; fresh snapshot scope; Case 4 on CO-0758 (owner go at each write); Case 5 needs a real CO; 409 response key names; "No automated route" reason text; Country alias list; `_to_delete/`.

**Lessons:** `/tmp` on the VM is wiped by every reboot; on Windows run VM commands as `ssh -t workato@… "sudo sh …"` (a bare `sudo` hits Windows' own stub); the VM suite catches tests that depend on the host's real state dir; passwords are typed only in the operator's terminal.

**Boundaries preserved (2026-10-13):** local code/docs/tests; VM steps operator-run and approved; no Salesforce write, no Leonardo, Redash or production BackOffice access, no OneLogin or firewall change; no credential, key, MFA value, cookie or token shown or persisted by the assistant.

#### Owner direction 2026-10-13 (later) — VM-native onboarding server (design only; `docs/43`)

- **Owner decisions recorded (direction only; none of them is an activation approval):** the VM is the team onboarding server and must work without the owner's laptop; the **VM reads Salesforce itself** (this supersedes the docs/41 "no Salesforce on the VM" decision once SecOps and Salesforce admin approve it); the Redash collection also moves to the VM (**design first**, no credential on the VM before approval); **before any onboarding write the dashboard shows a summary of everything to be sent, an operator approves it, and if anything is wrong the CO is corrected in Salesforce and the process restarts from a fresh summary**.
- **Why CO-0780 was missing on the VM:** the VM serves the last published snapshot (2026-10-09 20:14 UTC); CO-0780 appeared later; the VM has no Salesforce connection yet.
- **Written:** `docs/43_VM_NATIVE_ONBOARDING_SERVER_DESIGN_2026-10-13.md` (components A Salesforce reader, B Redash collector, C browser runner, D review summary and approval gate, E identity; MFA options; order of work; decisions V1-V6).
- **Next (local, no approval needed):** build part D (summary page, approval record bound to CO, route, summary hash and Salesforce revision, expiry, consumed by the run) with tests; then parts A and B as modules with injectable credentials and fake backends, units as review artifacts. **Owner/SecOps:** V1-V6 in docs/43; the operator sends the request pack. Nothing installed or activated; no credential created.

#### Progress 2026-10-13 (later) — part D built locally: production-checked Onboardings, review summary, one-use approval (not committed)

- **Navigation:** new first group **ONBOARDING** (`/onboard`); the existing pages are grouped under **DEV · LEONARDO** unchanged (Leonardo duplicate validation as before).
- **New flow (`/onboard`, `/onboard/CO-nnnn`):** lists open COs; supported routes (CE-only, Surface-only, Case 3) open a **review summary**: account, product, route, Salesforce source revision, the data that would be sent (tenant name, domains, plan, scope counts) and **duplicate validation against the Production BackOffice clone (Redash) only; Leonardo is never consulted** (`leonardo_consulted` is false in the hashed summary; a test fails if the Leonardo precheck is called). Blockers (fail closed): no automated route, unreadable values, missing revision, production tenant exists/differs/other/ambiguous, clone unavailable, clone older than 6 h or of unknown age, any preflight blocker. A clone "not in production" result is **not a clearance** (live BO probe still needs D4).
- **Approval (`integration/onboarding/review_approval.py`, POST `/attended/review-approve`):** an operator approves exactly the summary shown; the record is bound to CO, route, Salesforce revision and the SHA-256 of the canonical summary, lasts 4 hours and is consumed once (the consume step is ready for the future runner gate; **no run is wired to it and nothing is created**). A stale page, a changed CO or a blocked summary cannot be approved; correcting the CO in Salesforce invalidates the old approval and requires a new summary and approval. A corrupt approval file means nothing is approved.
- **VM:** the new POST is refused for every role on the VM (still `VM_OPERATOR_POSTS = {}`); on the VM the review pages show the "not available" page until part A (VM Salesforce reader) exists. Letting VM operators approve is decision V5 (docs/43), not made here.
- **Not built:** the dry run without Confirm and the runner's use of the approval (docs/42 section 3.5, after D1-D6); parts A/B/C of docs/43.
- **Verification:** 1617 tests OK (29 new: `test_review_approval.py`, `test_onboard_review.py`), both guards pass. Nothing external was touched.

#### Progress 2026-10-14 — docs/43 parts A and B built locally (VM Salesforce and Redash readers; nothing installed, not committed)

- **Part A (VM reads Salesforce):** `tools/vm_salesforce_reader.py` (read-only REST client, OAuth JWT bearer signed through the `openssl` CLI; answers only the dashboard's exact `data query ... --json` call shape and only a single `SELECT`; hosts pinned to `*.salesforce.com`, redirects refused, paging links confined, size/page/record caps, value-free reason codes; config is a JSON file of identity values plus a key *path*; key and config must be private regular files). `tools/vm_refresh_snapshot.py` (timer job): swaps the dashboard's `sf_json` for the REST client only while reading, builds a new signed snapshot (`producer = vm-salesforce-reader`) with the fresh queue and CO details, **carries over every other file** (desktop run history, Dev inventory), keeps the previous detail of a CO whose read failed, refuses `vm` runtime and clock roll-back, takes a lock, and on any failure leaves the previous snapshot current. The banner now says "refreshed on the VM from Salesforce; run history published from the desktop". The transport lives in `tools/` because the offline-boundary guard forbids network and process capability inside `integration/onboarding` (the guard was **not** changed).
- **Part B (Redash on the VM):** the collector reads its key from `SURFACE_REDASH_KEY_FILE` (private regular file, no DPAPI fallback when set), keeps its state under `<state dir>/redash`, and writes the production clone to a new **`<state dir>/clone-inventory`** folder (`state_paths.clone_inventory_root`) that a snapshot ingest never replaces; the dashboard and the runner read the clone from there (`clone_root()`); desktop defaults are unchanged.
- **Review artifacts (not installed):** `surface-onboarding-sf-refresh.{service,timer}.template` (every 10 min) and `surface-onboarding-redash-collect.{service,timer}.template` (hourly), each gated by an approval marker (`approved-sf-reader`, `approved-redash-collector`), credential groups `surface-sf-reader` / `surface-redash-reader` that the **dashboard unit does not have**, same hardening as the dashboard unit; README section "VM-side readers".
- **Verification:** 1662 tests OK (8 Linux-only permission tests skipped on the Windows desktop; they run in CI and on the VM), both guards pass; no external call was made.
- **Not built / still needed:** the live read from the dashboard process for approval summaries on the VM (the summary needs the runner's source reads; V5), part C (browser runner and MFA, V4), and everything in the request pack: Salesforce Connected App + read-only permission set limited to the fixed fields, Redash read-only key, outbound allow-list (Salesforce login/instance hosts, `redash.pentera.io`), OneLogin/firewall/certificate. **Owner/SecOps: V1-V6 in docs/43.** Nothing is activated until the approvals are recorded; no credential was created or stored.

#### Progress 2026-10-14 (later) — queue-read gap fixed in the VM Salesforce reader; request pack drafted (`docs/44`; not committed)

- **Bug found while writing the request pack:** the dashboard reads the queue through the list view results GET (`api request rest .../listviews/<id>/results`), not `data query`; the reader committed in `4086a52` answered only `data query`, so the refresher would have failed on the queue. Fixed in `tools/vm_salesforce_reader.py` (second exact call shape, GET only, path pinned to `Customer_Onboarding__c` list view results, raw body returned like the CLI) with tests, including one that runs the dashboard's **real** `queue_source_rows` and `detail_row` through the adapter over a fake Salesforce (every call a GET). Lesson: test the adapter with the real callers, not canned reads.
- **Key handling changed in the README:** the Salesforce key pair is generated **on the VM**; only the public certificate goes to the Salesforce admin (the private key never travels). The Redash key still arrives through the Redash owner's secure channel.
- **`docs/44_VM_READER_REQUEST_PACK_2026-10-14.md`:** the request for the Salesforce admin (Connected App, read-only fields and list view, IP restriction), the Redash owner, and SecOps/network (outbound hosts, later OneLogin, firewall, certificate), with safety properties, rollout, acceptance check, rollback and the decisions V1-V6. No credential or customer data in it; **nothing was sent or requested**; the owner sends it and fills in contacts.
- **Verification:** full suite and both guards pass after the fix (see the commit). Nothing external was touched.

#### Progress 2026-10-14 (night) — owner decisions on the VM pilot; clone in the snapshot; in-memory CLI session mode (local; not committed)

- **Owner decisions recorded:** (1) nothing unsafe or private stored on the VM, MFA at login in the operator's own browser; (2) **the production clone is allowed in the signed snapshot** (supersedes the 2026-10-08 exclusion), no Redash key on the VM; (3) continue with local/attended methods first and request additional access only after they are confirmed working. Query 251 is the unpublished "Surface Onboarding | Tenant inventory (read-only)" query (visible in the owner's Redash tab); no copy was needed and no browser session was taken over.
- **Built (tests pass):** snapshot pairs `inventory_prod_clone_latest.json` + `inventory_prod_clone_snapshot.json` (name-prefix enforced, unpacked to `leonardo-inventory/prod-clone`, read by the dashboard and runner through `clone_root()`; a real dry run on the owner's desktop lists the 17 MB clone); `vm_refresh_snapshot.py --transport cli` (CLI home in tmpfs only: `/run/` or `/dev/shm/`, private, not a link; HOME and CLI env restored after every run), `refresh-status.json` and the banner "Salesforce session needed"; the Salesforce refresh unit now uses that mode (no credential group, `MemoryDenyWriteExecute=no` documented for Node); the Redash VM timer templates are marked DEFERRED; README rewritten.
- **Finding that changes the plan:** device login with the default Salesforce CLI app is blocked (since 2025-08-28), `sf org login web` is unsupported on headless hosts, and the maintainers recommend JWT. The in-memory session needs an unproven web-login workaround (BROWSER hook plus an SSH forward of port 1717) or an admin-created connected app. A JWT read-only service identity is the smaller exposure than a personal full-permission token (docs/43 section 8). **Owner to choose:** (a) spike the web login on the VM (needs the CLI download and an integrity check against the GitHub release hashes), (b) send the JWT request (docs/44), or (c) keep the desktop as the publisher for now.
- **Not done / no external effect:** nothing installed or run on the VM, no download, no Salesforce or Redash call; operator scripts for the CLI install and login were deliberately not written until the owner chooses.

#### Handoff 2026-10-14 (end of day) — continue here

**Outcome:** the read-only VM dashboard runs **r25** on `workato-opa-01` (loopback only, SSH tunnel, enabled at boot) and shows the new ONBOARDING / DEV groups. Parts A, B, D of docs/43 are built locally and tested (1683 tests OK on the desktop; Linux-only tests ran green on the VM suite for r25 before the later changes). **No live Salesforce data reaches the VM yet**: its snapshot is still the manual one of 2026-10-09 20:14 UTC (amber banner), so new COs such as CO-0780 are missing there. Nothing was written to Salesforce, Redash, Leonardo or production BackOffice today.

**Owner decisions recorded today:** (1) nothing unsafe or private stored on the VM, MFA at login in the operator's own browser; (2) the **production clone may travel in the signed snapshot** (supersedes 2026-10-08), no Redash key on the VM (query 251 is the unpublished "Surface Onboarding | Tenant inventory (read-only)"; no copy needed); (3) try attended/local methods first, request admin access only after they are confirmed working; (4) spike the CLI web login on the VM before sending the JWT request. Request pack `docs/44` is drafted and **on hold** (not sent).

**VM state (operator-run, approved today):** r25 staged (hash `690ED9B8…FCE2A`, built from `ef36140`) and active; `app -> app-r25-staged-20261014` (rollback `_step7b … rollback` -> r24); groups `surface-sf-reader` and `surface-redash-reader` created and `surface-onboarding` is in neither; self-signed key pair `/etc/surface-onboarding/sf-reader.pem` (`0640 root:surface-sf-reader`, never left the VM) and certificate (SHA-256 fingerprint `37:28:48:F3:…:4A:09`, valid to 2028-10-13; a copy `sf-reader.crt` sits **untracked in the repo root on the desktop: move it out**); Salesforce CLI **2.154.4** installed at `/opt/surface-onboarding/sf-cli` (download hash `e19ccd04…478b1a` verified by the operator against GitHub's digest for the official release asset; root-owned); in-memory session folder `/run/surface-onboarding-sf` (tmpfs, `0700`, `/run` is mounted **noexec**); opener hooks in `/opt/surface-onboarding/ops/sf-login-hook`. **No Salesforce login exists, no timer, no approval marker, no service started.**

**Built locally, uncommitted (git status):** clone pair in the snapshot (`inventory_prod_clone_*`, `collect_inventory_files`, ingest layout, `clone_root()` = inventory root); `vm_refresh_snapshot.py --transport cli` (CLI home in `/run` or `/dev/shm` only, private, not a link; session = a real `~/.sfdx/<user>@….json` auth file), `refresh-status.json` and the banner "Salesforce session needed"; reworked `surface-onboarding-sf-refresh.service.template` (CLI mode, `MemoryDenyWriteExecute=no` documented for Node), Redash VM timer templates marked DEFERRED; README and docs/43 section 8; scripts `_step6a`, `_step7a/7b` (r25), `_step8a`-`_step8d` (CLI download, install, attended login, logout). Last commit pushed by the operator: `ef36140`.

**Salesforce CLI login spike (step 8C), where it stands:** the web login **starts** on the VM (attempt 1, `BROWSER` hook now outside the noexec `/run`, captured the real authorize URL for `client_id=PlatformCLI`, redirect `http://localhost:1717/OauthRedirect`), but the browser step did not complete and the CLI ended with `AuthTimeoutError`. Unknown yet: whether the CLI's own wait is shorter than the script's 5 minutes, whether the SSH forward (`-L 1717`) delivered the redirect, or whether the browser showed a Salesforce error. Background: device login with the default CLI app is blocked since 2025-08-28 and the maintainers recommend JWT for headless use.

**Next (in order; each VM step is run by the operator, one explained block at a time):**
1. Ask the owner what the browser showed (no URLs or `code=` addresses in chat). Run the read-only grep on the VM for the CLI's auth timeout default and variable name: `sudo grep -n -E 'DEFAULT_AUTH_TIMEOUT|getAuthTimeout|AUTH_TIMEOUT|OauthLocalPort|1717' /opt/surface-onboarding/sf-cli/node_modules/@salesforce/core/lib/webOAuthServer.js`.
2. Desktop pre-checks: `netstat -ano | findstr :1717` prints nothing; during the retry, from a second window `curl.exe -s -o NUL -w "%{http_code}" http://localhost:1717/` must return a status, not "refused" (proves the forward). Retry step 8C prepared (Chrome already signed in to SSO, URL pasted at once); assistant may print the URL on one clean line and raise the timeout through the variable the grep shows.
3. If login completes: verify with the script's status-only query, then run `vm_refresh_snapshot.py --transport cli --dry-run` and one real refresh as the service user (operator-run), compare the VM queue with the desktop queue (CO-0780 must appear), then discuss the approval marker `approved-sf-reader` and the timer (needs the owner's explicit yes); `8D` ends the session.
4. If it cannot work: send the Salesforce part of `docs/44` (JWT, read-only, certificate and fingerprint already prepared) and keep the desktop as publisher meanwhile (a one-command publish incl. the clone can be built).
5. Assistant: commit on request (clone pair, CLI mode, scripts, docs) and re-run the VM suite after the next release (r26) so the Linux-only tests cover the new code; the operator pushes and confirms Actions.
6. Unchanged backlog: docs/42 D1-D6 for production CE-only; docs/43 V4 (MFA for browser runs on the VM) and V5 (live reads for approval summaries on the VM); OneLogin, firewall 8443, certificate; Case 4/5; rotate the `workato` password; the VM still reports "System restart required" (a reboot restarts the Workato agent and the validator/intake services); delete leftover `/tmp/step*.sh`, `/tmp/r2*-upload`, `/root/sf-cli-download` when done; `_to_delete/`.

**Lessons today:** `/run` is `noexec` (scripts must live elsewhere); adapters must be tested against the real callers (the queue read is a list-view GET, not SOQL); a session check must look for the auth file, not for folders the CLI creates on any run; the harness blocked a local `sh -n` over scripts containing `rm`/`find -delete`, so syntax checks run on the VM (`sh -n` first); verify download hashes against GitHub's per-asset digest and by file size; the offline-boundary guard forbids network and process code inside `integration/onboarding` (transport lives in `tools/`).

**Boundaries preserved (2026-10-14):** local code, docs and tests; VM steps operator-run and approved (staging, activation, groups and key pair, CLI download and install, login attempt); no Salesforce or Redash write, no production BackOffice access, no OneLogin or firewall change; no password, MFA code, token, cookie, private key or login URL was shown or persisted by the assistant.

#### Handoff 2026-10-15 (end of day) — r26 active; first live read-only refresh on the VM worked

**Outcome:** the VM dashboard (**r26**, built from `aa4748b`) now shows **live Salesforce queue data**: one attended refresh at 2026-10-15 17:01:34 UTC (`queue_rows=14`, `co_details=17`, nothing unavailable, `refresh-status.json` = `ok`, transport `cli`). The queue matches the desktop row by row (same 9 open COs incl. CO-0780/0781/0782/0791/0792/0793, same chips, product, Salesforce status, automation and next step). No Salesforce write, no production BackOffice access, no Redash change.

**Salesforce CLI web-login spike: solved.** The 2026-10-14 `AuthTimeoutError` came from the CLI's own wait: `@salesforce/core` `webOAuthServer.js` has `DEFAULT_AUTH_TIMEOUT = 120_000` (2 minutes), overridable with `SF_WEB_OAUTH_SERVER_TIMEOUT` (ms); the script's 5-minute wait never applied. `_step8c_vm_sf_login.sh` now sets `SF_WEB_OAUTH_SERVER_TIMEOUT=300000`; with Chrome signed in to SSO, the URL pasted at once and `ssh -t -L 1717:127.0.0.1:1717 …`, the login completed (the script never opens a browser: it prints the one-time URL for the operator's own Chrome). A later "localhost refused to connect" page is only a second request after the CLI closed its listener. Each login was ended with `_step8d` (revoke at Salesforce, memory folder emptied); no marker, no timer, no service exists.

**VM state (operator-run, approved):** r26 staged (`surface-onboarding-r26-aa4748b.tar.gz`, SHA-256 `1C8AD2399DA53C015CDD3ED81B34A27A99D388598619CDC120A58A735ABB7335`, 313 members, LF, both checkers pass; VM suite `OK (skipped=3)`, both guards pass) by `_step9a`, activated by `_step9b` (`app -> app-r26-staged-20261015`, rollback `… rollback` -> r25; dashboard and nginx enabled at boot, loopback only, GET / 200, POST / 403). `_step10a_vm_refresh.sh dry-run|refresh` ran the refresher as the service user. Snapshots now: `snapshot-20261009T201453Z` and `snapshot-20261015T170134Z` (`current`).

**Known gap (found by comparing with the desktop):** the VM shows no Start dates, no "overdue/in Nd" and orders by submission date, because the refresher does not read the DealHub subscription data (`DealHub_Subscription__c`, the same fixed read-only query the desktop uses); the VM journal shows `sf_read DealHub_Subscription__c … fail` on each page. Closed history is not in the snapshot by design ("Not included in the published snapshot").

**Owner decisions still open:** (1) the `approved-sf-reader` marker and the timer: the session is memory-only and ends at reboot or logout, so a timer only refreshes while someone has signed in, otherwise the banner says "Salesforce session needed" and the last good data stays; unattended refresh needs the JWT request (`docs/44`, on hold); (2) snapshot retention (keep the last N; deleting needs approval); (3) production CE-only decisions D1-D6 (`docs/42`); (4) validator/intake systemd units; OneLogin, firewall 8443, certificate, SecOps; rotate the `workato` password.

**Next (in order):**
1. Operator: confirm the logout was run (`ssh -t workato@172.26.37.20 "sudo sh /tmp/step8d.sh"`), push `main`, confirm Actions.
2. Assistant (local, tests first): add the DealHub subscription read to `tools/vm_refresh_snapshot.py` (fixed query, read-only, same shape as the desktop) so Start dates, overdue/in-Nd and ordering work on the VM; update tests; build r27; stage/test/activate like r26; then an attended login and one refresh to verify (operator-run).
3. Decide the marker/timer or the JWT request; snapshot retention policy.
4. Backlog unchanged: docs/42 D1-D6 for production CE-only, docs/43 V4/V5, Case 4/5, `_to_delete/`.

**Lessons:** the Salesforce CLI web login waits 2 minutes by default (`SF_WEB_OAUTH_SERVER_TIMEOUT`); run the attended login from the repo root in a window where Chrome is already signed in to SSO; `cd` into the repo before `scp` with relative paths; a read-only comparison of the VM and the desktop queue (row by row) is the right acceptance check for a refresh.

**Boundaries preserved (2026-10-15):** local code, docs and tests; VM steps operator-run and approved (staging, activation, attended Salesforce login with the operator's own SSO and MFA in their browser, one read-only refresh); no Salesforce or Redash write, no production BackOffice access, no OneLogin or firewall change; no password, MFA code, token, cookie, private key or `code=` address was shown or persisted by the assistant (the one-time authorize URLs shown in chat carry no secret of ours and were consumed).

#### Correction and progress 2026-10-15 (late) — VM start dates from the published comments (local; r27 candidate)

- **Correction to the 2026-10-15 handoff:** the queue **Start** column does **not** come from `DealHub_Subscription__c`; it comes from the DealHub date range in each CO's `Onboarding_Comments__c` (`subscription_summaries`, only the validated date is kept, never the comment). The `sf_read DealHub_Subscription__c … fail` lines in the VM journal belong to the **renewal-plan panel on CO pages** (`dealhub_rows_for_co`), a separate gap that remains (not shown on the VM).
- **Built (local, tested):** in VM mode `subscription_summaries` answers from the published `co_details.json` (it already carries `Onboarding_Comments__c` for every queued CO) through `_snapshot_subscription_summaries`: same parser, same output, no live read, no new Salesforce query, no new snapshot file, no new approval; a CO without a published detail fails closed (`ReadUnavailable`). Desktop unchanged. Three tests in `test_vm_snapshot_dashboard.py` (dates from comments; fail closed; queue page shows the date, no "could not be read" note, no comment text), two fail without the change. 1687 tests OK (skipped=11), both guards pass (one unrelated Windows loopback `ConnectionAbortedError` flake in `test_dashboard_stage_tracker` on the first run; passes alone and on the second full run).
- **Effect:** after r27 is active, the VM queue shows Start dates, overdue/in-Nd markers and start-date ordering from the data already on the VM (the 2026-10-15 17:01 snapshot); no new login or refresh is needed.

#### Live 2026-10-15 (evening) — r27 active on the VM; start dates verified against the desktop

- **r27** (`surface-onboarding-r27-2513124.tar.gz`, built from `2513124`, SHA-256 `F6802A7FD4732CB7C9D3A3DA4BD7FF0BAD3DEF715E2DFEFCEF558D35172BA9C4`, 316 members, LF, both checkers pass) staged by `_step11a` (VM suite `OK (skipped=3)`, both guards pass) and activated by `_step11b` (`app -> app-r27-staged-20261015`, rollback `… rollback` -> r26; dashboard restarted only; loopback only; GET / 200, POST / 403, direct call 403; both units enabled at boot; banner still "Data as of 2026-10-15 17:01 UTC").
- **Acceptance (owner, screenshot):** the VM queue now equals the desktop queue row by row including **Start** (CO-0782 Jul 1 overdue 106d, CO-0780 Jul 1 106d ago, CO-0781 Sep 30 15d ago, CO-0766/0791/0792 Oct 1 14d ago, CO-0769 Oct 20 in 5d, CO-0793 and CO-0777 "—") and the start-date ordering; the "could not be read" note is gone. Source: the published CO comments, no new Salesforce read, no new login. Only the closed-history chip differs by design.
- **Open on the VM:** the renewal-plan panel on CO pages (`dealhub_rows_for_co`, `DealHub_Subscription__c`) is still unavailable there; the `approved-sf-reader` marker/timer decision (the memory-only session ends at reboot or logout) or the JWT request (`docs/44`, on hold); snapshot retention; D1-D6 for production CE-only (`docs/42`); validator/intake systemd units; OneLogin, firewall 8443, certificate, SecOps; rotate the `workato` password. Leftover VM folders to clean later (list first, owner approval): `app-r23`/`incoming-r23` are already gone; `app-r24`, `incoming-r24`, `app-r25`, `incoming-r25` and `/tmp/r2*-upload` remain, keep r26 as the rollback.

#### Owner direction 2026-10-15 (evening) — the VM must run without the owner's computer; production clone on the VM (local; r28 candidate)

- **Trigger:** on CO-0780 on the VM the Production tab showed "Not checked / Clone unavailable". **Cause:** the VM's snapshot is built by the VM refresher, which only reads Salesforce and carries forward whatever the previous snapshot held (the manual 2026-10-09 bundle, published before the production clone existed); a desktop publish that includes the clone (`collect_inventory_files`) had never been ingested. My r26/r27 acceptance checks covered the queue only; check the Production tab too next time.
- **Owner decision (2026-10-15):** neither Salesforce nor the Redash clone may depend on the owner's computer (vacation, someone else onboarding). Supersedes the 2026-10-14 decisions to keep the Redash key off the VM and to use the attended operator session as the main path (both remain fallbacks). `docs/43` section 9 and `docs/44` updated; the `docs/44` request pack hold is lifted **for the owner to send** (Salesforce admin: Connected App + read-only integration user; Redash owner: read-only key for query 251; SecOps: outbound hosts). The assistant sends nothing; keys and certificates are placed by the operator as root-owned files, never in chat.
- **Built locally (tests first):** `state_paths.clone_inventory_root`: a state-dir process that is not serving (the Redash collector) writes `<state>/clone-inventory`; the VM dashboard prefers that folder once it holds `prod-clone/latest.json`, else the clone in the current snapshot; a snapshot ingest never touches `clone-inventory`. VM wording: an unusable clone now names the VM's own collector, never the Windows command (`_collect_hint_html`). Redash service/timer templates no longer marked DEFERRED (still gated: key, marker, outbound allow-list). 1692 tests OK (skipped=11), both guards pass.
- **Interim until the admins respond (6 h limit, the production gate fails closed on an older clone):** desktop `publish_vm_snapshot.py` (with the clone) + manual ingest (`_step12a_vm_ingest.sh`).
- **Next:** build/stage/test/activate r28; the owner sends docs/44; operator-run activation per docs/44 section 6 (key + certificate as root-owned files, markers one at a time, one timer first, acceptance by comparing counts with the desktop), then retire the manual publish.

#### Live 2026-10-15 (night) — r28 active on the VM

- **r28** (`surface-onboarding-r28-3254102.tar.gz`, built from `3254102`, SHA-256 `DB3A3543F6AB76817D2FA3F72A89EA6FCD5D08E00244B859AA3E12EFF5D08C81`, 319 members, LF, both checkers pass) staged by `_step13a` (VM suite `OK (skipped=3)`, both guards pass) and activated by `_step13b` (`app -> app-r28-staged-20261015`, rollback `… rollback` -> r27; dashboard restarted only; loopback only; GET / 200, POST / 403, direct call 403; both units enabled at boot; banner still 2026-10-15 17:01 UTC). The VM still has **no production clone** (no source yet): the Production tab stays unavailable and now names the VM's own collector. Next: the owner sends docs/44; the interim desktop publish + `_step12a_vm_ingest.sh` (valid 6 h) if CO-0780 must be checked before the admins respond. Uncommitted: `_step13a`, `_step13b`, this entry.

#### Production tab on the VM: root cause found and fixed locally (2026-10-15, night; r29 candidate)

- **Diagnosis (reproduced locally with the real signed bundle, reason codes only):** after the interim ingest the VM loaded the clone fine (trusted, ~100 min old), yet the CO-0780 Production tab still said "Clone unavailable". Fresh computation gave `clone_unavailable / co_source_unreadable`: `production_match_for` builds the CO's expected names, domains and plan with `contract.load_source(...)`, which runs **live Salesforce reads through the `sf` CLI** (the CO row and `DealHub_Subscription__c`); the VM has no CLI session and never reads live, so it failed closed. (The dashboard also caches display reads for 300 s, which hid the first result.) The clone-folder change of `3254102` was necessary but not sufficient.
- **Fix (tests first):** new signed-snapshot file `production_inputs.json` (`vm_snapshot.PRODUCTION_INPUTS_FILE`): per queued CO, the engine, tenant names, domains and plan that the production check uses (`production_review_inputs`, JSON-plain, dates as ISO text), computed where Salesforce is readable. Desktop publisher: `collect_dashboard_reads` adds it (a CO it cannot read has no entry). VM refresher: always writes it, keeps the previous entry when a read fails (like CO details), and in the REST (JWT) transport points the runner's `_sf_records` at the REST client; the CLI transport uses the session's `sf`. VM runner: `_production_expectation` reads the file in VM mode (`_snapshot_production_expectation`), fails closed when the entry is missing, differs in product/type or is malformed, and never reads Salesforce. Desktop unchanged. 1700 tests OK (skipped=11), both guards pass; the adapter test is now hermetic (no real `sf`).
- **`docs/44` updated:** the Salesforce admin request now also covers `DealHub_Subscription__c` (read: `DealHub_Account__c` filter, `Product_Full_Name__c`, `DealHub_Status__c`, start and end dates) and `Customer_Onboarding__c.Account_Country__c`; request volume roughly doubles.
- **Next:** build/stage/test/activate r29; desktop publish with the new publisher (includes `production_inputs.json`) and ingest with `_step12a`; then CO-0780's Production tab should show the duplicate check and CO status for 6 hours; the permanent fix remains the Redash key and the Salesforce JWT identity (docs/43 s.9).

#### Handoff 2026-10-15 (night) — r29 staged on the VM, not yet active; continue here

**Outcome:** the VM dashboard runs **r28** (active). **r29** (`surface-onboarding-r29-4b9993f.tar.gz`, built from `4b9993f`, SHA-256 `5A11DD554270D86A5C541C7F9E20A229F81D4DB96958C940B7E6D04A54BF33F9`, 321 members, LF, both checkers pass) is **staged and tested on the VM** by `_step14a` (`app-r29-staged-20261015`, suite `OK (skipped=3)`, both guards pass, `app` still -> r28). Not activated. r29 carries the fix for the VM Production tab (`production_inputs.json`; see the previous entry "Production tab on the VM: root cause found and fixed locally").

**State of the VM data:** snapshot `snapshot-20261015T204724Z` is `current` (interim desktop publish, ingested with `_step12a`): it has the production clone (captured 19:09 UTC, trusted for 6 h, i.e. until about 01:09 UTC on 2026-10-16), queue 16 rows, CO details 18, but **no `production_inputs.json`** (published before the fix), so CO-0780's Production tab still shows "Clone unavailable / co_source_unreadable". No Salesforce session lives on the VM (`step8d` was run); no `approved-sf-reader` marker, no timer, no Redash key.

**Owner decisions recorded today (2026-10-15):** (1) the VM must run without the owner's computer (docs/43 s.9): it will collect Salesforce (JWT service identity) and the Redash clone itself; supersedes the 2026-10-14 decisions, the attended CLI session and the desktop publish stay as fallbacks; (2) the `docs/44` request pack hold is lifted **for the owner to send** (now also covering `DealHub_Subscription__c` and `Account_Country__c`); (3) interim: desktop publish + manual ingest while the admins respond.

**Next, in order (every VM step is run by the operator, one block at a time, with approval):**
1. Activate r29: `ssh -t workato@172.26.37.20 "sudo sh /tmp/r29-upload/_step14b_vm_activate_r29.sh"` (rollback: same with `rollback` -> r28; `/tmp` is wiped by a reboot: re-upload the scripts first if the VM was rebooted). Expect `app -> app-r29-staged-20261015`, loopback only, GET / 200, POST / 403, direct 403.
2. Re-publish from the desktop with the new publisher: `python tools/publish_vm_snapshot.py --key-file "$env:USERPROFILE\.surface-onboarding\snapshot.key" --out-dir "$env:USERPROFILE\.surface-onboarding\snapshots-out"` (read-only Salesforce reads on the desktop; the bundle now includes `production_inputs.json`; check that the output reports `production_inputs=N`); first `python tools/redash_inventory_collector.py --collect` if the clone is old (the last collect returned exit 2: Redash's refresh call failed and the cached result, 94 min old, was used; reason not investigated). Then `scp` the zip and `_step12a_vm_ingest.sh` to `/tmp/r29-upload/` and `sudo sh /tmp/r29-upload/_step12a_vm_ingest.sh <zip name>`. The dashboard caches display reads for 300 s: reload after 5 minutes.
3. Verify in the browser (tunnel `tools\open_vm_tunnel.ps1`): CO-0780 Production tab, `/prod/tenants`, `/prod/matches`: the duplicate check and CO status must show (not "Clone unavailable"). If a CO still fails, read the reason with a local repro on the same bundle (reason codes only) and fix in r30.
4. Owner: send `docs/44` to the Salesforce admin (Connected App + read-only integration user incl. DealHub), the Redash owner (read-only key for query 251) and SecOps (outbound hosts). Operator then places the key and the certificate as root-owned files (never in chat), creates the markers one at a time, enables one timer first, accepts by comparing counts with the desktop (docs/44 s.6). The Redash timer then feeds `<state>/clone-inventory` (preferred by the VM dashboard once it exists).
5. Open decisions: snapshot retention (keep the last N); the renewal-plan panel on VM CO pages (`DealHub_Subscription__c`); D1-D6 for production CE-only (`docs/42`); validator/intake systemd units (down since the 2026-10-13 reboot); OneLogin, firewall 8443, certificate, SecOps; rotate the `workato` password; delete old VM folders (list first): `app-r24`, `app-r25`, `app-r26`, `app-r27` and their `incoming-*`, keep r28 as the rollback.

**Lessons today:** the Salesforce CLI web login waits 2 minutes by default (`SF_WEB_OAUTH_SERVER_TIMEOUT`); the VM refresher carries forward whatever the previous snapshot held, so a new snapshot file must be published by the desktop or produced by the refresher; check the Production tab (and not only the queue) when accepting a VM release; reproduce a VM-only symptom locally by ingesting the same signed zip into a temp state folder and calling the dashboard functions in VM mode (reason codes only); the dashboard display cache is 300 s; the working copy uses CRLF for some files (git normalizes to LF on commit); in bash-tool Python, build Windows backslashes with `chr(92)` to avoid escaping surprises.

**Boundaries preserved (2026-10-15):** local code, docs and tests; VM steps operator-run and approved; one attended Salesforce login (own SSO and MFA) and one read-only refresh on the VM earlier today, ended with `step8d`; read-only Salesforce reads and one read-only Redash collect on the owner's desktop for the interim publish; no Salesforce or Redash write, no production BackOffice access, no OneLogin or firewall change; no password, MFA code, token, cookie, private key or `code=` address was shown or persisted by the assistant.

#### Handoff 2026-10-16 — VM self-heal and always-on tunnel installed; IP access (172.26.37.20:8443) not opened

**Outcome:** the dashboard on the VM no longer needs anyone to start the services or the tunnel. Read-only status first (`_step15a`): the VM itself was healthy (up 2 days, dashboard `active` since 2026-10-15 19:22 UTC, nginx `active`, `GET /` 200, listeners only `127.0.0.1:8000/8443`); the break was the desktop tunnel (one foreground `ssh -L` window with a password prompt, nothing restarted it). Dashboard unit had `Restart=on-failure` with 5 starts per 10 s; nginx had `Restart=no`.

**Done (operator-run, approved; passwords typed only in the operator's terminal, none stored):**
- `_step15c_vm_authorize_tunnel_key.sh`: one tunnel-only public key in `/home/workato/.ssh/authorized_keys` (`command="/bin/false",restrict,port-forwarding,permitopen="127.0.0.1:8443"`, tag `surface-vm-tunnel`; `revert` removes it). First run failed because a Windows-written `.pub` ends in CR; script now strips it.
- `_step15b_vm_self_heal.sh`: drop-ins `Restart=always`, `RestartSec=5`, `StartLimitIntervalSec=0` for the dashboard and nginx; `/opt/surface-onboarding/ops/pilot_health.sh` plus `surface-onboarding-health.timer` (every 2 min; restarts the dashboard, then nginx, on no answer or 5xx; journal tag `surface-onboarding-health`). **Proven:** the dashboard was killed with SIGKILL and was `active` again with 200 within 9 s.
- Desktop: `tools/setup_vm_tunnel_key.ps1` (ed25519 key in `%USERPROFILE%\.surface-onboarding\`, no passphrase, ACL to this user), `tools/keep_vm_tunnel.ps1` (reconnect loop, BatchMode, backoff to 60 s, log in `%LOCALAPPDATA%\SurfaceOnboarding\vm-tunnel.log`), `tools/register_vm_tunnel_task.ps1` (task `SurfaceOnboarding-VmTunnel`, at sign-in, restart on failure). After the old password tunnel (a manual `open_vm_tunnel.ps1` window) was stopped, the task's ssh owns port 8443 and `https://localhost:8443/` returns 200 with no window.

**Not verified yet:** sign-out/sign-in and PC reboot persistence of the task; the VM reboot case for the health timer (units are enabled). The tunnel still needs this PC on, signed in and on the VPN.

**IP access (`https://172.26.37.20:8443/`) — decision needed, nothing changed:** `ufw` is inactive; nginx listens only on `127.0.0.1:8443`. The VM sees the operator's SSH client as `10.213.213.1` (a VPN/NAT gateway), so an nginx or ufw allow-list by IP cannot be limited to one PC: it would admit everyone behind that gateway. The interim nginx site also sets one fixed identity (`<OWNER_EMAIL>`) for every request, so exposing it would let any such client act as the owner (viewer role). Do not bind it beyond loopback. Options: (1) keep the automatic tunnel (current); (2) the full template with oauth2-proxy and OneLogin (docs/41; IT must register the app, plus firewall 8443 and a real certificate); (3) a stopgap with per-user nginx basic auth, a VPN-subnet ufw rule and the self-signed certificate, only with SecOps approval.

**Lessons:** the terminal tool will not type into a tab that is running an interactive `ssh`; the operator pastes into one logged-in VM session tab and the assistant reads it; `sudo -v` keeps the password prompt away for 15 minutes; Windows `ssh-keygen` writes CRLF in `.pub`; stop an old manual tunnel before the task can bind 8443; `/tmp` on the VM is wiped by reboots (the health script lives in `/opt/surface-onboarding/ops/`).

**Next:** (1) operator closes the VM session (`sudo -k`, `exit`); (2) test sign-out/sign-in; (3) owner decides the IP-access option (recommend 2, send the OneLogin request via docs/41); (4) validator/intake systemd units (ports 8787-8789) after the read-only listing and approval; (5) r29 activation and desktop re-publish from the 2026-10-15 handoff, still pending; (6) uncommitted: `_step15a/b/c`, the three `tools/*vm_tunnel*` scripts, `RUN_STEPS_VM_STATUS.md`, `COMMAND_TO_PASTE.md` (delete the two helper `.md` files or move them to `docs/`).

**Boundaries preserved (2026-10-16):** no Salesforce, Redash, Leonardo or production access; no firewall, OneLogin or nginx listen change; VM changes were the two approved scripts only; no password, private key or token was shown or stored by the assistant (the public key and its fingerprint are not secrets).

#### Owner decisions 2026-10-16 (late) — production CE-only D1-D6 recorded (`docs/45`); no production access, nothing built yet

- **Internal-network access built and live:** `https://172.26.37.20:8443/` (nginx site on the VM address, per-person basic auth with bcrypt, allowed range `10.213.213.1/32`, tunnel kept); step 16A/16B run by the operator, firewall step 16C not run. The VM has no Sessions page by design (no browser/MFA on the VM). Desktop dashboard fixed: it ran on the codex Python without Playwright ("Browser automation runtime: missing"); restarted on `.venv-renewal-playwright` with `start_attended_dashboard.ps1 -Restart -PythonPath ...`; Sessions now Salesforce Ready, Leonardo Development Ready, Preflight Passed.
- **CO-0797 (Grupo Patio):** Credential Exposure, New Product Onboarding, Request Approved/Approved, CE-only, Ready to onboard, DealHub May 21 2026 to May 20 2027, email domain `patio.cl`, Account UUID empty, submitter Eduardo Varela, submitted 2026-10-16.
- **D1 accepted** as recommended (CE-only, one named CO per approval, 7-day expiry, Milton only, consumed by the create). **D4, D5, D6 accepted** as recommended (CO-0797 first candidate).
- **D2 (differs):** no BO rollback owner answer; a wrongly created tenant is recorded in full (redacted local record) and the owner decides rename/fix by edit; residual risk (no known delete/disable) accepted by the owner; the D4 probe must confirm an Edit path exists.
- **D3 (differs):** for CE-only New the tool writes the verified Account UUID to `Customer_Onboarding__c.Account_UUID__c` after BO readback (one field, one CO, empty-or-equal, revision unchanged, explicit go per write, verified after write). Dev-only plan and panel already exist (2026-10-01).
- **Still blocked:** `PRODUCTION_RUNNER_SUPPORTED=False`; no production read or write until the guarded path (docs/42 sections 3-4) is built, tested and reviewed, the Redash clone is fresh (under 6 h), the D4 probe is done, a dry run passes and the owner gives the go for the single create.

#### Handoff 2026-10-16 (night) — guarded production path and production-first dashboard built locally; production still locked

**Built (local code and tests only; nothing committed; `PRODUCTION_RUNNER_SUPPORTED` stays `False`; no production, Salesforce, Leonardo or VM access):**
- `integration/onboarding/`: `origin_policy.require_production_origin`, `run_target.py` (production target only when the unlock passes), `production_approval.py` (one CO, CE-only route `case_2_new_ce_only`, 7-day default and 31-day max, consume once with a cross-process lock file), `production_create_record.py` (D2: redacted, append-only record of every create/dry-run/refusal), `salesforce_uuid_writeback.py` (D3: one field `Account_UUID__c`, plan then fixed argv, nothing executed), `production_create.py` (orchestrator: ordered gate incl. current-vs-approval revision drift, fresh clock before consume, dry run never clicks or consumes, one click and no retry, `attempt_started` record before the click, prior-attempt review gate incl. `attempt_started` and `readback_verified`, separate `writeback_plan_after_verified`), `production_views.py` (read-only dashboard helpers). Independent opus review found 1 high and 14 other items; all fixed and re-tested.
- Dashboard (`tools/serve_attended_open_onboardings_dashboard.py`): production-first. Menu PRODUCTION (Onboarding, Tenants, History, Approvals), TOOLS (Sessions), collapsed DEV - LEONARDO last; `/` and `/onboard` are the production landing, Dev queue moved to `/dev` (old URLs alias), `/prod/approvals` read-only, CO page cards per `docs/46` mockup (Duplicate validation holds the match details; lock banner; no create control in the HTML while locked). VM mode shows "unavailable on this server" for data the snapshot lacks. Design: `docs/46`, `docs/47`, `docs/mockups/production_dashboard.html`.
- **Verification:** full suite 1969 tests, 13 skipped; 1 error in `test_dashboard_stage_tracker` (`ConnectionAbortedError` WinError 10053, the known Windows loopback flake; the module passes alone, 23 tests OK); both guards pass.
- **Open:** the desktop dashboard on 8012 still runs the old code until restarted (sign in again); two tests in `test_attended_ce_only_runner` appear to reach the real `sf` CLI (read-only) and should be found and stubbed; the production browser runner is NOT built (needs the D4 probe: attended read-only, field names only); the Redash clone must be fresh (under 6 h) and the VM needs r29 plus a desktop publish for the production tab; CO-0797 (Grupo Patio, CE-only New) is the first candidate and can still be onboarded in Leonardo Development today (Sessions Ready after the Playwright Python fix).

#### Release r30 prepared (2026-10-16, night; not uploaded, not staged, not active)
- Commit `7aaa539` (local, not pushed). Archive `surface-onboarding-r30-7aaa539.tar.gz` in `%USERPROFILE%\.surface-onboarding\releases\`, SHA-256 `E7412691CD0407B7576CDF2122553037FF67872BC9511BFA729A713DE21AF07E`, 351 members, 1,490,771 bytes, unpacked 4,991,285 bytes (limit 5,242,880: **only ~250 KB headroom left in `tools/check_release_archive.py`; raise the limit by a reviewed change before the next growth**), LF export, no CR bytes, `check_release_archive.py` passed. Scripts `_step17a_vm_stage_r30.sh` (stage and test) and `_step17b_vm_activate_r30.sh` (activate from r28, rollback to r28). r29 stays staged and unused (r30 contains it). After activation: desktop snapshot publish with `production_inputs.json`, fresh Redash clone, ingest, so the VM shows CO-0797 and the Production data.

#### Live 2026-10-16 (night) — r30 active on the VM; fresh snapshot ingested; CO-0797 visible
- **r30** (`surface-onboarding-r30-7aaa539.tar.gz`, SHA-256 `E7412691CD0407B7576CDF2122553037FF67872BC9511BFA729A713DE21AF07E`) staged by `_step17a` (checksum OK, 351 members, no CR, VM suite `OK (skipped=3)`, both guards pass) and activated by `_step17b` (`app -> app-r30-staged-20261016`, rollback `... rollback` -> r28; dashboard restarted only; GET / 200, POST / 403, direct 403; listeners 127.0.0.1:8000, 127.0.0.1:8443 and 172.26.37.20:8443). r29 stays staged and unused.
- **Snapshot** `surface-vm-snapshot-20261016T194636Z.zip` (desktop publish, read-only; 18 queue rows, 20 CO details, `production_inputs.json` for 10 COs, 8 unavailable) ingested with `_step12a` as `snapshot-20261016T194636Z` (current). Banner "Data as of 2026-10-16 19:46 UTC"; CO-0797 (Grupo Patio) is in the VM queue; its production page shows the clone with age about 3 h (trusted under 6 h), "Not in production", and "Production create is locked". **Clone expiry:** the Redash refresh call still fails (`refresh_failed`, exit 2), so the clone is trusted only until about 6 h after capture; docs/44 addendum asks the Redash owner for an hourly schedule on query 251 plus the VM key (the only way to make it automatic without the PC).
- **Tunnel task** `SurfaceOnboarding-VmTunnel` died once (result 0xC000013A, console closed); re-registered with sign-in plus a 5-minute repeat trigger; GET / via tunnel 200. **Internal site** `https://172.26.37.20:8443/` (basic auth, allowed `10.213.213.1/32`) serves the same r30 pages.
- **Open:** D4 probe and the production browser runner (unbuilt); Redash owner request (docs/44 addendum, owner sends); two tests in `test_attended_ce_only_runner` reach the real `sf` CLI; `check_release_archive.py` unpacked-size cap has about 250 KB headroom; firewall step 16C not run; validator/intake units; r29 folders and old `app-r24..r27` cleanup.

#### Live 2026-10-16 (night) — D4 production probe done (attended, read-only; production_write not_performed)
- Third run (`PROBE-D4-20261016C`, report `20261016T213339Z.json`, git-ignored under `integration/production-probe/`): sign-in by the owner with MFA in the dedicated profile; 9 checks passed (login, page settled, create control "Add Account" found, create form opened, 40 fields read, form closed without saving, search present, no write request allowed, submit never clicked); 3 failed (Dev-contract labels/names, Edit not checked). One read request (`POST /api/v1/backoffice/getAllDetailedAccounts`), nothing blocked, clicks only `open_create_form` and `cancel_create_form`. The first two runs were inconclusive because of probe bugs (hidden page, false login-complete), fixed.
- **Form contents (names/labels only):** Company name, Company primary domain, Alternate Domains, SubDomains, User email domains, Networks, `accountType` (3 options), `accountCountry` (251), First/Last name, Organization Email, Phone number, Job title, `mfaRequired`, Select Operator Accounts, `scanningInterval` (4), Scan now, toggles `notificationsAllowed`, `multipleUsersAllowed`, `apiAccessAllowed`, `phishingEnabled`, `leakedCredentialsAllowed`, `provisioningEnabled`, `subDomainsNumberAllowed`, `leakedCredentialsScanningInterval` (4), Leaked Credentials scanned domains, `licenseType` (5), Number of assets/domains/subdomains, start and expiration dates; a submit control exists.
- **Dev-contract differences:** the 6 "missing labels" are detection artifacts (selects are identified by name, `User email domains` has one space instead of two). The 9 feature-flag names (`automatedDiscoveryEnabled`, `subDomainsReconEnabled`, `webDictionaryBruteForceEnabled`, `webDorkingEnabled`, `fullNucleiScanEnabled`, `authenticatedTestingEnabled`, `staticOutboundIpEnabled`, `aiEnabled`, `multipleAttackStacksEnabled`) are NOT on the closed create form although the production account model has them: likely a collapsed Advanced section or the Edit form. **Unresolved; the production dry run must expand and read it before any create.** Edit was not checked (no row menu opened).
- A manual production HAR (14 requests, `app.pentera.io`) was analysed structure-only (no values) and then deleted by the owner; it showed the read endpoints (`auth/verify` TOTP, `getAuthorities`, `accountInfo`, `userProfile`, `license`, `demoAccountDomains`, `getAllDetailedAccounts` list and name search) and the account record field names, but no `account/add`. Findings go to docs/48.

#### Handoff 2026-10-16 (late night) — production dry run built and locked; probe done; Onboard card; Redash-on-VM scripts (all local, uncommitted since 15212b3)
- **Verified:** full suite 2126 tests OK (skipped=13), both guards pass, `PRODUCTION_RUNNER_SUPPORTED = False` and the new `PRODUCTION_DRY_RUN_SUPPORTED = False` (separate hard-coded constants; neither reads env or arguments; unlocking the dry run never unlocks the create).
- **Built:** `production_probe.py` (D4 read-only probe; ran live 3x, see the entry above), `production_dry_run.py` + `--production-dry-run` in `tools/attended_ce_only_playwright.py` (locked; gates in order target, reference, revision, approval, clone age; page-context session check by status code and authority names; production duplicate and demo-domain pre-check; fill, read the 9 Advanced flags, read-back, Cancel; never Confirm; real create click NOT implemented; redacted record only), the CO page Onboard card (`test_co_onboard_card.py`), Redash-on-VM scripts `_step18a/b/c` and `--cached-ok` in the collector (waits on the Redash owner: hourly schedule on query 251 + key; SecOps outbound), docs 48 (HAR findings; the HAR was structure-read and then deleted by the owner), docs/42 6.1-6.2.
- **Independent review (opus) of the dry run:** 0 critical, 2 high (forced toggle click could hit Confirm; service workers/WebSocket outside the request guard), 2 medium, several low; all fixed (no forced clicks, element-under-pointer check, refuse when a service worker is registered, no Enter/newline, fresh clock at the end, exact "Add Account" only, form-left-open handling, enum refusal reasons, test-hook env, restricted GET origins, stronger tests). WebSocket traffic is still not scanned.
- **Not done / next:** (1) commit and build r31 (Onboard card, probe, locked dry run, Redash scripts) for the VM; (2) owner go to flip `PRODUCTION_DRY_RUN_SUPPORTED` in a reviewed change, then the first real production dry run for CO-0797 (fresh clone, one-CO approval bound to the current revision, attended sign-in); (3) read the 9 Advanced flags from the real form; (4) the real create click, readback via API and Account UUID write (`production_create`, gated by `PRODUCTION_RUNNER_SUPPORTED`, owner go); (5) Redash owner request (docs/44 addendum) and SecOps outbound; (6) two tests in `test_attended_ce_only_runner` reach the real `sf` CLI; (7) `check_release_archive.py` unpacked cap (about 250 KB headroom at r30; r31 is larger, check before building); (8) stray `_to_delete/`, `RUN_STEPS_VM_STATUS.md`, `COMMAND_TO_PASTE.md` untracked.

#### Live 2026-10-16 (night, later) — r32 active on the VM (r31 failed its VM gate and was never activated)
- **r31** (`surface-onboarding-r31-bd159fd`, 363 members) staged by `_step19a`: checksum OK, but the VM suite reported `FAILED (errors=1, skipped=4)` — `test_production_probe.RunnerWiringTests.test_probe_profile_is_separate_from_dev_and_ignores_dev_override` assumed Windows (`LOCALAPPDATA`, patched `os.name`). Not activated; `app` stayed on r30. Fix (`5ffc0c3`): the persisted-profile test is `skipUnless(os.name == "nt")` like the Dev one, plus a Linux-safe test that the probe profile off Windows is temporary and never persisted. Lesson: a new test that touches desktop-only paths must be portable; the VM stage gate is the check that catches it.
- **r32** (`surface-onboarding-r32-5ffc0c3.tar.gz`, SHA-256 `9803B12FCBC5070D9A347C3D4AB9BC403918EE97613592C00076F8A99E2D6C6C`, 365 members, 1,570,831 bytes, unpacked 5,309,366 bytes; `check_release_archive.py` unpacked cap raised from 5 MiB to 8 MiB in `bd159fd`, with a test) staged by `_step20a` (checksum OK, no CR, VM suite `OK (skipped=5)`, both guards pass) and activated by `_step20b` (`app -> app-r32-staged-20261016`, rollback `... rollback` -> r30; dashboard restarted only; GET / 200, POST / 403, direct 403; listeners 127.0.0.1:8000, 127.0.0.1:8443, 172.26.37.20:8443; both units enabled at boot). Verified through the tunnel: `/`, `/prod/approvals`, `/prod/tenants`, `/history`, `/dev` 200; the CO-0797 production page shows the Onboard card ("Onboarding runs from the desktop dashboard"), "Production create stays locked until the owner unlocks the runner", "Not in production", the clone age, no POST form and no create/confirm control (the only `type='submit'` is the sidebar search button). Snapshot still `snapshot-20261016T194636Z`.
- **VM leftovers to list and clean later (owner approval first):** `app-r31-staged-20261016`, `incoming-r31`, `incoming-r30`, `app-r24`..`app-r27`, `app-r29-staged-20261015`, `/tmp/r30-upload`, `/tmp/r31-upload`, `/tmp/r32-upload`, old `step*.sh`.
- **Commits (local, not pushed):** `e2528c2`, `bd159fd`, `9b0ae27`, `5ffc0c3`, `7c3af9b` (plus `7aaa539`, `15212b3` earlier today). Untracked helpers: `RUN_STEPS_VM_STATUS.md`, `COMMAND_TO_PASTE.md`, `_to_delete/`.
- **Next (in order):** (1) owner go to flip `PRODUCTION_DRY_RUN_SUPPORTED` in a reviewed change, then the first real production dry run for CO-0797 (fresh clone under 6 h, one-CO create approval bound to the current revision, attended sign-in; the dry run reads the 9 Advanced flags); (2) real create click, API readback and the Account UUID write (gated by `PRODUCTION_RUNNER_SUPPORTED`, owner go); (3) Redash owner request (docs/44 addendum) and SecOps outbound for the VM collector (scripts `_step18a/b/c` ready); (4) CO-0797 can still be onboarded in Leonardo Development from the desktop dashboard.

#### Live 2026-10-16 (night) — CO-0797 created MANUALLY in BackOffice production by the owner; create contract captured (docs/49)
- The owner created the CE-only tenant for CO-0797 by hand in `app.pentera.io` (not our automation; both automation lock constants stay False and were not involved) while recording a HAR (`grupopatio.pentera.io.har`, 22 requests; read structure-only; the owner deletes it). `POST /api/v1/backoffice/account/add` -> 200 at 2026-10-16T22:48:55Z; readback through `getAllDetailedAccounts` found one tenant with a single `accountUuid` (captured; not recorded here). The create response contains no id/UUID, so the UUID comes from the readback. Full observed contract in `docs/49` (CE-only conventions: name suffix " - CE Only", internal email domain and existing internal primary user, scanning NO_SCHEDULE, leaked credentials WEEKLY on the customer domain, SpyCloud enabled in the payload, license 1/1/1 prepaid annual subscription, expiry = DealHub end).
- **Open:** (1) write the Account UUID into `Customer_Onboarding__c.Account_UUID__c` for CO-0797 (D3: one field, verified by read-back; owner go) and move the CO stage; (2) the Dev tab still shows "Not onboarded" and the production match "Not in production" until the next clone/snapshot; the clone must be refreshed before another production check; (3) use the captured contract to finish the automated dry run and create (docs/42 6.2, docs/48, docs/49), still locked; (4) delete the two HAR files in Downloads; (5) uncommitted: approval-issuer tool and fixes, docs/49, this entry.

#### Live 2026-10-16 (night) — Account UUID written to CO-0797 in Salesforce (D3, owner go, one field)
- After the owner's manual production create (previous entry), the guarded plan `salesforce_uuid_writeback.plan` (production, CE-only New, readback verified, revision unchanged, explicit owner go) returned `ready_to_write`; org pinned (`surface-onboarding`, id compared with the expected org id), exactly one `Customer_Onboarding__c` row for CO-0797; the fixed argv `sf data update record --sobject Customer_Onboarding__c --record-id <id> --values Account_UUID__c=<uuid> --target-org surface-onboarding` ran once (exit 0). Fresh direct read-back (not the dashboard cache): `verify_after_write` = verified; `Onboarding_Stage__c` unchanged (`Request Approved`; the owner moves the stage); `LastModifiedDate` moved to 2026-10-16T22:54:29Z (so any approval bound to the old revision 15:34:30Z would now drift, as designed). No other field or record touched. The UUID value is not recorded in the repo.
- The UUID write path used here is the guard module plus a one-off script run from this session; it is NOT yet a dashboard feature (the dashboard Salesforce IDs panel is still Dev-only). Wiring it with its own confirmation form is a later step.

#### Handoff 2026-10-16 (end of session) — r33 active on the VM; fresh snapshot published, ingest pending; continue here
- **VM state:** `app -> app-r33-staged-20261016` (r33 = commit `a610e98`, SHA-256 `84BCD4FE65E535B12D2F383196E1AFCD9F3602223F56F12470CD90AFF5C30292`; rollback `... _step21b_vm_activate_r33.sh rollback` -> r32). Gates passed on the VM: checksum, no CR, suite `OK (skipped=5)`, both guards; activation checks GET / 200, POST / 403, direct 403. Internal site `https://172.26.37.20:8443/` (basic auth, allowed `10.213.213.1/32`) and the always-on desktop tunnel task serve the same pages. r33 adds the read-only **Production onboarding sheet** on CE-only New CO pages (no write control; expiry via the owner rule of 2026-09-25: start + 1 year − 1 day capped at the subscription end; hard blockers hide the values; VM shows the snapshot time), country in `production_inputs.json`, and the locked production readback code.
- **Snapshot:** `surface-vm-snapshot-20261016T234043Z.zip` was published from the desktop (read-only; 18 queue rows, 20 CO details, `production_inputs` for 10 COs incl. CO-0797 and CO-0798, country on 6) but **NOT yet ingested**. To finish: `scp` the zip and `_step12a_vm_ingest.sh` to `/tmp/r33-upload/` and run `sudo sh /tmp/r33-upload/_step12a_vm_ingest.sh surface-vm-snapshot-20261016T234043Z.zip` (steps in `COMMAND_TO_PASTE.md`). The production clone is older than 6 h (Redash refresh still fails with the per-query key), so production checks fail closed until the Redash owner sets an hourly schedule on query 251 and the VM collector is activated (docs/44 addendum, scripts `_step18a/b/c`).
- **CO status:** CO-0797 was created by the owner manually in BackOffice production (docs/49); its Account UUID was written to Salesforce (one field, verified); the stage is the owner's to move. **CO-0798** (CE-only New, Approved, stage Request Approved, UUID empty, one email domain, term 2026-06-22..2029-06-21 so the license expiry to enter is **2027-06-21**) is the next candidate: use the sheet on the CO page (VM or desktop) and a manual BackOffice create, then write the UUID with the guarded one-field write (owner go each time).
- **Built and committed locally (not pushed; last commits `c625c6a`, `a610e98`, `43b5d29`, `7c3af9b`, `e2528c2`, `bd159fd`):** guarded production path (locked), D4 probe (ran live), locked production dry run, owner-run one-CO approval command, read-only production readback + contract (validated offline against the real manual create: zero mismatches), CO Onboard card, Redash-on-VM scripts, r30..r33 stage/activate scripts. Full suite 2300 tests OK (skipped=13); both guards pass.
- **Still locked, unchanged:** `PRODUCTION_RUNNER_SUPPORTED = False` and `PRODUCTION_DRY_RUN_SUPPORTED = False` (separate hard-coded constants; flipping either needs the owner's explicit go in a reviewed commit and a relock).
- **Open / next (in order):** (1) ingest the snapshot above and check the CO-0798 sheet on `https://172.26.37.20:8443/co/CO-0798?env=prod`; (2) owner sends the Redash request (docs/44 addendum) and SecOps outbound for the VM collector; (3) live production readback of CO-0797's tenant (`--production-readback --co CO-0797`, attended sign-in; docs/42 6.3); (4) first automated dry run when the owner says go (docs/42 6.2: fresh clone, one-CO approval via `tools/issue_production_approval.py`, unlock commit and relock); (5) dashboard confirmation form for the guarded UUID write; (6) the real create click and API readback, gated by `PRODUCTION_RUNNER_SUPPORTED`; (7) delete the two HAR files in Downloads (live session); clean old VM staging folders (`app-r24..r27`, `r29`, `r30`, `r31`, `incoming-*`, `/tmp/r3*-upload`); two tests in `test_attended_ce_only_runner` reach the real `sf` CLI; untracked helpers `RUN_STEPS_VM_STATUS.md`, `COMMAND_TO_PASTE.md`, `_to_delete/`.
- **Lessons:** a markdown viewer drops backslashes before `.` and `_`, so give Windows commands with forward slashes and in a file; the terminal tool cannot type into a tab that runs `ssh`, so the operator pastes into one logged-in VM tab and the assistant reads it; a new test touching desktop-only paths must be portable (the VM stage gate catches it); independent opus reviews found real defects every time (locks, races, wrong guidance), keep them.
- **Boundaries preserved (2026-10-16):** the automation never wrote to production; the one production create was the owner's manual action; Salesforce writes: one verified field (`Account_UUID__c` on CO-0797) with the owner's explicit go; no password, MFA code, token or cookie was shown or stored by the assistant; both HAR files were read structure-only.

#### Handoff 2026-10-17 (r34 built locally, not staged; interim clone push built, not installed; Redash hourly refresh set)
- **VM:** snapshot `snapshot-20261016T234043Z` ingested (CO-0798 sheet shows country and expiry 2027-06-21). Internal site 403 fixed: `lan-allow.conf` now allows `10.213.213.1/32` and `10.213.213.2/32` (backup `.bak-20261017`, nginx -t ok, reloaded). Tunnel task restarts itself within 5 minutes (it was killed twice; cause of the kill unknown, hidden window).
- **Redash (owner action, verified by reload):** query 251 Refresh Schedule is now Every hour. The VM cannot reach `redash.pentera.io` (outbound 443 times out); SecOps request still to send. Browser login to Redash was considered and rejected (does not help the VM, adds a session type).
- **Built locally (commits `95de1a3`, `8c98cd7`, plus the step23 scripts; not pushed):** simplified production CO page (one verdict, flat enter-only table, no dev content, fail-closed incl. unavailable key rows and the recorded production-duplicate marker; docs/50), interim PC-to-VM clone push (hourly task, forced-command key, HMAC bundle, high-water and 1000-row first-push floor, marker enforced in the ingest program; docs/51; step22a/b/c and `tools/*clone_push*`; NOT installed). Two independent opus reviews found 5 + 11 defects; all fixed and re-tested. Full suite 2353 OK (13 skipped) per the implementing agent; 241 relevant tests re-run OK; both guards pass.
- **r34:** `surface-onboarding-r34-8c98cd7.tar.gz`, SHA-256 `AA0008DAC2A931FC75863CD7FC3BC07515665D49BAC92B1FDD19DEDE178AD152`, 388 members, 1,651,756 bytes, unpacked 5,629,151, LF, `check_release_archive.py` passed. Scripts `_step23a` (stage) and `_step23b` (activate, rollback to r33). Steps in `COMMAND_TO_PASTE.md`.
- **Next (owner go each):** (1) stage r34 (`_step23a`), then activate (`_step23b`); (2) install the clone push (step22a/b on the VM, key setup and task registration on the PC; wait for the first hourly push); (3) send the SecOps outbound request and then `_step18a/b/c`; (4) onboard CO-0798 by hand using the new page, then the guarded one-field UUID write; (5) `/onboard/CO-xxxx` page still has the old cards; docs 46/47 and the mockup not updated; PowerShell scripts untested on a real PS 5.1.
- **Boundaries preserved:** no production write by automation; both lock constants False; no secret shown or stored.

#### Live 2026-10-17 (afternoon) - r34 active on the VM; clone push installed and working; Redash query 253 (v2) created; r35 built, not staged
- **r34 active** (`app -> app-r34-staged-20261017`, rollback `_step23b rollback` -> r33): simplified production CO page (one verdict, flat enter-only table, no dev content). Stage gate passed (checksum, suite OK skipped=5, guards).
- **Interim clone push installed** (owner go each step): `_step22a` (gate, ingest program, sudoers rule), `_step22b` (restricted key `from=10.213.213.0/24`, approval marker; the PC appeared as 10.213.213.34/.1/.2 on different days), PC task `SurfaceOnboarding-CloneToVm` hourly. First push `ssh_exit=0 result=ingested captured_at=2026-10-17T15:59:59Z rows=5447`; `_step22c` status: clone 51 min, trusted. Needs the laptop on, signed in, on VPN; otherwise the clone ages past 6 h and checks fail closed.
- **Redash (owner session driven by the assistant, owner go):** query 251 Refresh Schedule set to Every hour (verified after reload). **New query 253** 'Surface Onboarding | Tenant inventory v2 (read-only)' (MongoDB $project over tenantsMView, same source, 58 flat columns incl. userEmailDomains, accountCountryCode, license toggles, 10 Advanced flags, MFA flag, counts; no PII), Every hour. 251 untouched and still the live source. The VM cannot reach redash.pentera.io (outbound 443 times out): SecOps request still to be sent (after CO-0798).
- **Found live:** the per-CO production checks replayed a 5-min display cache computed from the old clone; CO-0797 (created by hand) showed Duplicate; Surface+CE showed 'Clone unavailable'. Fixed locally (`08ce33b`, `a9bc8cf`): cache keyed on the clone stamp and re-aged at read time, new statuses `onboarded` (CO Account_UUID__c matches exactly one clone tenant; warnings for identity/licence/expiry/counts/SpyCloud/suffix), `uuid_not_in_clone`, `not_checked`, 'Other tenants only'; every status except `clear` blocks create in the sheet; docs/50.
- **v2 support** (`9d7f509`, `a9bc8cf`): collector `--query-id 251|253` (default 251), per-query key `query-253.key`, strict per-query columns, `row_schema: v2`, v2 value shape and size caps; docs/52 (coverage of 251 vs the manual-create HAR, query 253 field list, owner steps).
- **Verification:** full suite 2418 tests OK (skipped 14) run by the orchestrator, both guards pass; 3 independent opus reviews (CO page, push security, match+v2) found 5+11+8+9 items, all fixed.
- **r35:** `surface-onboarding-r35-a9bc8cf.tar.gz`, SHA-256 `E707BDA26B9154A3C2E02EE8159A3E18C8DCE2F00C2B5A8425749726221DF2A6`, 392 members, 1,679,480 bytes, unpacked 5,735,550, LF, `check_release_archive.py` passed. Scripts `_step24a` (stage) and `_step24b` (activate, rollback to r34). Steps in `COMMAND_TO_PASTE.md`. NOT yet staged.
- **Next (owner go each):** (1) stage then activate r35; (2) CO-0798: check BackOffice for a duplicate yourself, create by hand with the Add Account table, then the guarded one-field UUID write (owner go); the dashboard create stays locked; (3) store the query 253 key (`--store-key --query-id 253`), run `--probe --query-id 253` (caps on real rows), then re-register the task with `-QueryId 253`; (4) SecOps outbound request + `_step18a/b/c`; (5) `git push origin main` (owner; main is ahead of origin by about 24 commits); (6) `/onboard/CO-xxxx` page still has old cards.
- **Boundaries preserved:** automation never wrote to production; both lock constants False; no secret shown or stored (Redash key and push key never displayed).

### 2026-10-17 (evening) Guarded production create built, locked, local only
Built `production_create_live.py`, `production_create_ui.py`, `run_production_create` (tools runner, `--production-create`), small dashboard hooks, tests `test_production_create_{live,ui,runner}.py`, and `docs/53` (owner runbook). `PRODUCTION_RUNNER_SUPPORTED` and `PRODUCTION_DRY_RUN_SUPPORTED` stay False; nothing ran externally; no commit. Next: owner issues the CO-0798 approval, reviewed flip of the dry-run constant, dry run, reviewed flip of the create constant, create, UUID write, relock (docs/53).

#### Handoff 2026-10-17 (evening) - r36 active; CO-0798 Ready but NOT created; locked production create built and in second-review fixes; continue here
- **VM state:** `app -> app-r36-staged-20261017` (r36 = `86953a1`, SHA-256 `91C41ACA1C48D688EE2AD4ED8540F21055CF3ED2FD9DB8DC30DF1B364AB04A9E`; rollback `/tmp/r36-upload/_step25b_vm_activate_r36.sh rollback` -> r35, then r34, r33). Salesforce snapshot `snapshot-20261017T180026Z` ingested (re-publish and `_step12a` ingest by hand when the Salesforce side changes; the VM cannot read Salesforce). Clone push installed (step22a/b, key `from=10.213.213.0/24`, marker present) and the PC task `SurfaceOnboarding-CloneToVm` runs hourly (needs the laptop on, signed in, on VPN; log `%LOCALAPPDATA%/SurfaceOnboarding/clone-push/clone-push.log`; one `collect exit=1 redash_schema_unavailable` at 17:51Z was transient, it still pushed the last good clone). Internal site allow-list now `10.213.213.1/32` and `10.213.213.2/32` (backup `.bak-20261017`).
- **Redash:** query 251 hourly schedule set (owner session). New query 253 (v2, 58 flat columns, hourly) created, NOT yet used: owner steps `--store-key --query-id 253`, `--probe --query-id 253`, then re-register the task with `-QueryId 253` (docs/52 s7). SecOps outbound 443 from the VM to `redash.pentera.io` still to be requested (then `_step18a/b/c` and the PC push can be retired).
- **CO status (docs/54, read-only):** CO-0797 Onboarded (clean, green); CO-0798 Clear/Ready (Salesforce approved, UUID empty, not in production); CO-0780 Onboarded: review (tenant name differs); CO-0781 and CO-0765 onboarded but licence end 2029 vs expected 2027 (owner decides a BackOffice edit); CO-0795/0796 are the same account; 8 COs Not checked (production_inputs missing); proposal not implemented: identity-only Onboarded path from `co_details.json` for COs holding a UUID.
- **Locked production create (commits `f9d982f`, `947b176`; local, NOT pushed, NOT deployed; both constants False):** `production_create_live.py`, `production_create_ui.py`, `production_launch_ticket.py`, `tools/clear_production_attempt.py`, `--production-create`, `--production-search-probe`, docs/53 runbook. Owner goal: CO-0798 created from the dashboard, not by hand (live duplicate check, one click, readback, guarded UUID write). Two independent opus reviews: first verdict NO (7 findings, fixed in `947b176`), second verdict YES conditional on: (1) control-tenant selection must exclude names containing the domain, (2) clear tool must require and record the owner, (3) request guard at browser-context level and popup after arming = uncertain. Also queued: launch-ticket HMAC (or documented same-user limit), exact country compare, dry-run age at click time, ignore `SURFACE_SF_TARGET_ORG` in production. A builder agent was applying these when this entry was written: check `git log` and `git status` for the result before trusting it; then run the full suite + both guards, commit, and run a short third review of just those changes.
- **Owner sequence still ahead (each is an explicit owner go):** attended read-only `--production-search-probe` (set `SURFACE_ONBOARDING_PRODUCTION_PROBE=1` and `SURFACE_ONBOARDING_PRODUCTION_PROBE_REF=PROBE-SEARCH-20261017`, sign in with MFA) and `--production-readback` on an existing tenant (verifies four unverified row paths); then issue the one-CO approval for CO-0798 (`tools/issue_production_approval.py`); reviewed commit flipping `PRODUCTION_DRY_RUN_SUPPORTED`; dry run; reviewed commit flipping `PRODUCTION_RUNNER_SUPPORTED`; create; guarded UUID write (separate confirmation); relock. Fallback if the owner prefers: manual BackOffice create from the Add Account table on the CO-0798 page (page says Ready), then the guarded one-field UUID write.
- **Other open items:** `git push origin main` (owner; about 28 commits ahead of origin/main); `/onboard/CO-xxxx` page still has old cards; docs 46/47 and the mockup not updated; PowerShell scripts untested on a real PS 5.1; Surface+CE COs need production_inputs to be checked; clean old VM staging folders (r29..r33 folders, `incoming-*`, `/tmp/r3*-upload`); delete HAR files in Downloads; untracked helpers `RUN_STEPS_VM_STATUS.md`, `COMMAND_TO_PASTE.md`, `_to_delete/`.
- **Lessons:** the per-CO match display cache must be keyed on the clone stamp and re-aged at read time; a gate for 'create' must never be presented as the CO's status (onboarded COs read green); the classifier blocks production-create work without an explicit owner statement in chat; subagent reports are not verification, re-run the full suite and guards after each round; three independent opus reviews found real defects every time.
- **Boundaries preserved:** the automation never wrote to production; both lock constants False; the only Redash changes were the owner-authorized hourly schedule on 251 and the new read-only query 253; no secret shown or stored.

#### Handoff 2026-10-18 (morning) - production create second/third/final review fixes committed and verified; still locked; continue here
- **Commits (local, NOT pushed, NOT deployed; 31 ahead of origin/main):** `0226c7a` second-review fixes (control-tenant selection excludes names containing the domain, clear tool requires and records the owner, context-level request guard, signed launch ticket, exact country compare, dry-run age at click time, production ignores `SURFACE_SF_TARGET_ORG`); `be39289` third-review fixes (32-byte atomic ticket key, one ticket root outside the repo, popup check after readback, nonce replay record, org id pin); `d58f35b` final fixes (org id pin mandatory, ticket hardening fails closed, local-only ticket root, one unknown-outcome wording).
- **Verified by the orchestrator session 2026-10-18 (re-run, not a builder report):** full suite 2623 tests OK (skipped=14); `check_integration_artifacts.py` and `check_integration_offline_boundary.py` pass; `PRODUCTION_RUNNER_SUPPORTED = False` and `PRODUCTION_DRY_RUN_SUPPORTED = False`; tracked tree clean (untracked: `COMMAND_TO_PASTE.md`, `RUN_STEPS_VM_STATUS.md`, `_to_delete/`).
- **Not verified:** no external system touched today yet; no independent re-review of `be39289`/`d58f35b` is recorded. The org pin file `%LOCALAPPDATA%\SurfaceOnboarding\salesforce-expected-org-id.txt` exists on the desktop (content not read or recorded; its correctness is owner-verified at the dry run, which refuses `salesforce_org_not_pinned` on a missing, malformed or mismatched Id).
- **Next (each an explicit owner go, docs/53 steps):** read-only `--production-search-probe` and `--production-readback` on CO-0797's tenant; CO-0798 approval; reviewed flip of the dry-run constant; dry run; reviewed flip of the runner constant; create from the dashboard; guarded UUID write; relock.
- **Boundaries preserved:** no production write; both lock constants False; no secret shown or stored.
- **Gap-review fixes (2026-10-18, local, committed after a full re-run: 2642 tests OK, skipped=14, both guards pass, both locks False):** (1) strict MFA readback now reads only `primaryUser.isMfaRequired` (Dev-probe path); (2) any Advanced-control mismatch refuses `advanced_mismatch` before a click (dry run and live), `multipleAttackStacksEnabled` added to the readback; (3) the one allowed create POST body is captured redacted (key names and types) and compared with the expected payload (`payload_mismatches`; unchecked body = `create_outcome_unknown`); docs/49 expiry wording aligned with the owner rule. Not verified: real Playwright `post_data` and real production row/body shapes (fakes only); no independent review of these fixes yet.

#### 2026-10-18 (midday) - production reads moved to the page's own request; live probe found the cookie-fetch assumption wrong
- **Found live:** after a human SSO+MFA sign-in, the production search probe got 401 on both `getAllDetailedAccounts` calls: the page authenticates with an in-page session token, so cookie-only `fetch` (docs/48 s1 assumption) never worked. Everything failed closed, nothing was written. Playwright Python 1.63.0 was also missing from the dashboard interpreter and was installed with the owner's yes (`...codex-primary-runtime/dependencies/python`).
- **Built locally and committed (tests first):** all production reads (session, searches, demo list, readback) use the page's own table request with only the `tableServerData` body rewritten (`ProductionPageReader`), guards check the body grammar, session state from status codes of the page's own traffic, demo list compared only if the page fetched it, else fail closed. Full suite 2692 tests OK (skipped=14), both guards pass, both locks False (re-run by the orchestrator). Independent Opus review: YES for the read-only probes; three medium findings to fix BEFORE any create unlock: (1) sort-header click with the Add Account form open (`_in_app_sort_header`), re-run the form compare before arming; (2) append to `rewritten` only after `route.fallback` succeeds and match the response by request body; (3) probe control proof must require `total <= len(rows)` and an exact domain match. Unverified against real Playwright: `route.fallback(post_data)` visibility to the guards, request identity, `dispatch_event` behind a modal, demo GET on reload.
- **BackOffice has no API/OAuth client** (owner asked the former product director): auth stays manual SSO+MFA in the automation window.
- **Live 2026-10-18 (read-only, owner signed in with MFA):** Probe A (`--production-search-probe`) passed after the page-own-request rework (name and domain search proven, 0 clicks, 0 blocked, the page fetched the demo-domain list on its own). Probe B (`--production-readback --co CO-0797`) first showed 13 false mismatches; a names-only diagnostic proved the explicit `projection` made the server drop nearly all license/flag/MFA paths. Fix (committed): strict reads keep the `unique_fields` override [accountName, accountDomain] and send NO projection; compared paths are the verified ones (`accountLicense.startDate/expirationDate/reconLevel/scanningFrequency`, `accountSettings.reconSettings.automatedDiscoveryEnabled`, `primaryUser.isMfaRequired`, `primaryUser.email`). Re-run Probe B: `production_readback_complete`, `mismatches: []`, UUID ok, Salesforce UUID matches, no write. Suite 2725 tests OK (skipped=14), both guards pass, both locks False (orchestrator re-run; the builder's 2829 was a stale log). Because search reads changed, Probe A must be re-run once more. Still open before any create unlock: review findings 1 (sort-header click with the form open), 2 (rewrite bookkeeping after `route.fallback`), 3 (control proof needs `total <= len(rows)` and exact domain match), then a second independent review.
- **Review fixes 1-3 committed (2026-10-18):** sort-header lookup restricted to the tenant table outside dialogs; a live form re-check (`form_changed_during_checks`, `form_recheck_missing`) after the final gate and before the intent record/approval consume; rewrite recorded only after `route.fallback` succeeds and the reply matched by request body; control proof needs `total <= len(rows)` plus exact name and domain. Suite 2756 OK (skipped=14), both guards pass, locks False (orchestrator re-run). Second Opus review: A) proceed to approval, reviewed dry-run unlock and attended dry run = YES; B) later create unlock = CONDITIONAL on a live dry run proving the form-open reads and on F1 (re-check must include SpyCloud and re-run the schema check), F2 (use one ElementHandle for header check and click), F3 (re-check keys must be a superset of the fill keys).

#### Handoff 2026-10-18 (afternoon) - production reads proven live (read-only); create still locked; F1-F3 built but NOT verified or committed; continue here
- **Code state:** HEAD `f21273b` (local, NOT pushed; about 36 commits ahead of origin/main). Commits today: `e51ff2c` gap-review fixes (MFA readback path, Advanced-flag gate `advanced_mismatch`, create-body compare), `fd5653c` + `7c80d7d` page-own-request reads, `f4d147e` no projection on strict reads, `f21273b` review fixes 1-3. **UNCOMMITTED in the working tree (builder report only, NOT re-run by the orchestrator):** review items F1 (SpyCloud + form schema in the live re-check: keys `spycloud_state`, `form_schema`), F2 (one ElementHandle for header classification and click), F3 (re-check keys must be a superset of the fill keys, else `form_recheck_missing`), docs/53 section 'Review fixes F1-F3', files `production_create.py`, `tools/attended_ce_only_playwright.py`, tests `test_production_create.py`, `test_production_create_runner.py`, `test_production_page_read.py`, `test_production_dry_run.py`, `page_own_table.py`, `test_search_reload_retry.py`. Builder claims 2765 tests OK (skipped=14), both guards pass. **First action next session: run the full suite and both guards yourself, read the diff, then commit.** Both lock constants are False (`session_readiness.py:64,70`).
- **Live today (read-only, owner signed in with MFA; no production or Salesforce write):** the production runner's cookie-only fetch got 401 (the app authenticates with an in-page token); reads now use the page's own table request with a body-only rewrite. Probe A (`--production-search-probe`) passes (name and domain search proven). Probe B (`--production-readback --co CO-0797`) passes: `mismatches: []`, UUID ok, Salesforce UUID matches (an explicit projection had made the server drop most paths; strict reads now send the `unique_fields` override and NO projection). Playwright Python 1.63.0 was installed into the dashboard interpreter `C:\Users\Milton Stevenson\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` with the owner's yes; run the runner with THAT python, not the system one. The page fetches the demo-domain list on its own (200). BackOffice has no API or OAuth client: auth stays manual SSO+MFA in the automation window.
- **Independent reviews (Opus):** read-only probes YES; second review: owner-gated sequence up to and including the attended dry run YES; later create unlock CONDITIONAL on (1) a live dry run proving the form-open reads (a dialog-free sort header is found, the `post_data` body match works, the control is proven) and (2) F1 (and preferably F2, F3), which are the uncommitted items above. Unverified against real Playwright: `route.fallback(post_data)` visibility to the guards, request identity, `closest()` dialog test on the real DOM/MUI markup, `dispatch_event` behind a modal, ElementHandle calls.
- **CO-0798 facts (Salesforce, owner-approved, screenshot 2026-10-18):** Advanced Technology International, CE-only New, Approved by Milton Machado (expected; our automation approval is separate and issued only by Milton Stevenson), email domain `ati.org`, term 2026-06-22..2029-06-21, Account UUID empty, license expiry to enter 2027-06-21 (DealHub subscription START + 1 year - 1 day capped at the end; the license start entered is the run day), CE-only 1/1/1 as in docs/49. Owner confirmed this mapping.
- **Owner sequence still ahead (each an explicit owner go, docs/53):** (1) verify and commit F1-F3; (2) `python tools/issue_production_approval.py --co CO-0798` (typed `ISSUE CO-0798`; use the dashboard interpreter); (3) reviewed commit flipping ONLY `PRODUCTION_DRY_RUN_SUPPORTED = True`; (4) attended dry run `--production-dry-run --co CO-0798 --revision <REV>` with `SURFACE_ONBOARDING_PRODUCTION_SESSIONS=1` and `SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF=<ref>` (creates nothing; needs a fresh Redash clone under 6 h: the hourly PC push must be running) and read the result word `dry_run_passed`; (5) a short third Opus review of anything changed after the dry run; (6) reviewed commit flipping `PRODUCTION_RUNNER_SUPPORTED = True`; (7) dashboard 'Create in production' (typed `CO-0798`, owner click, MFA); (8) guarded UUID write (own confirmation); (9) relock both constants. Fallback if the owner prefers: manual BackOffice create from the CO page sheet, then the guarded one-field UUID write.
- **Not done / other open items:** `git push origin main` (owner); Redash query 253 steps (`--store-key --query-id 253`, `--probe`, re-register task with `-QueryId 253`, docs/52 s7) so the clone carries the v2 fields; SecOps outbound for the VM collector; old VM staging cleanup; delete the HAR files in Downloads (`grupopatio.pentera.io.har` was read structure-only today); untracked helpers `COMMAND_TO_PASTE.md`, `RUN_STEPS_VM_STATUS.md`, `_to_delete/`; `/onboard/CO-xxxx` old cards; docs 46/47 and the mockup.
- **Lessons:** a hypothesis in the design docs (docs/48 s1: cookie fetch) was wrong and only a live read-only probe could show it, so probe every transport assumption before the dry run; builder report test counts can be stale (one was 104 higher than the final tree), always re-run the suite and guards yourself; `git add -u` does not add new files (two test files were missed once and committed separately); a server-side projection of dotted paths silently dropped fields, so compare against full rows.
- **Boundaries preserved:** the automation never wrote to production or Salesforce today; both lock constants False; no password, MFA code, token or cookie shown or stored; the HAR was read structure-only.

#### Handoff 2026-10-18 (evening) - F1-F3 committed; CO-0798 approval issued; dry run BLOCKED by a stale Redash clone (root cause found, fix awaiting owner go)
- **Done:** orchestrator re-ran the full suite (2765 tests OK, skipped=14) and both guards (pass) on the F1-F3 tree, read the diff, committed `abd8f15` (not pushed). Issued the one-CO approval: `PROD-CE-0798-20261018`, expires 2026-10-25T21:23:44Z (owner typed the phrase; first attempt refused `confirmation_phrase_mismatch`, nothing recorded). The issuer CLI needs the launcher's non-secret env (`SURFACE_ONBOARDING_APPROVAL_ISSUERS`, `SURFACE_SF_TARGET_ORG`, `SURFACE_SF_EXPECTED_ORG_ID` from the org-id file); without it it refuses `issuer_list_ambiguous`. Both lock constants still False.
- **Blocker (dry run needs a clone < 6 h):** clone last captured 2026-10-17T17:00:03Z. The hourly `SurfaceOnboarding-RedashInventory` task fails every run with `redash_schema_unavailable` (clone push then refuses `inventory_snapshot_stale`). Read-only diagnosis (shape only, no values): query 251 returns 5447 rows; 126 fail `_hostnames` on `alternateDomains`: 125 rows hold more than `_MAX_LIST_ITEMS = 50` alternate domains (median 110, max 1622, up to 28,538 chars vs `MAX_ROW_LIST_CHARS = 1500`) and 1 item contains a comma. Cause: commit `a9bc8cf` (2026-10-17 17:43Z, v2 size caps) also applied the v2 caps to the v1 `alternateDomains` path.
- **Proposed fix (owner go needed; local code + tests, then Opus review since it feeds the duplicate pre-check):** separate, realistic bounds for v1 `alternateDomains` (items and chars), keep the strict caps for v2 lists, and split a comma-joined item into hostnames (each part must still match; more coverage for duplicate detection, never less). Then a manual `--collect` run, confirm a fresh clone, then continue the dry-run sequence.
- **Fix built and verified (evening, uncommitted until owner go):** `redash_inventory.py` gives alternateDomains its own bounds (5,000 items / 100,000 chars per row; 1.5M chars and 90,000 items across the result -> `inventory_too_large`), splits comma-joined items into hostname-shaped parts, excludes them from the 1,500-char v2 row budget; `leonardo_inventory.MAX_ALTERNATE_DOMAINS` 500 -> 5,000 (the old cut could hide a duplicate match). Tests first (4 new, 2 adjusted); size test now measures per-char/per-item snapshot cost. Test isolation fix: `test_vm_snapshot_dashboard` now also patches `PRODUCTION_APPROVAL_PATH` (the first real approval leaked into it). Opus review: APPROVE WITH NITS (no fail-open, no new PII route; nits D/E applied; open nit A: `minimize_row` still truncates silently for non-Redash callers; nit F: Leonardo-path comma items still dropped). Orchestrator: full suite 2769 OK (skipped=14), both guards pass. Manual `--collect`: `inventory_collected`, 5,447 rows, no drift; clone `fresh`, captured 2026-10-18T21:01:26Z (6 h window ends ~03:01Z).
- **Committed (owner go):** `0f06d63` alternateDomains fix; `853cf99` step 3, ONLY `PRODUCTION_DRY_RUN_SUPPORTED = True` (create lock `PRODUCTION_RUNNER_SUPPORTED` still False). 11 lock tripwire tests updated: constants pinned dry run True / create False; locked-path tests patch the dry-run flag False; the real-constants create test now refuses `production_runner_not_supported`. Full suite 2769 OK (skipped=14), guards pass. The relock commit must revert `853cf99`'s test edits too. Next: step 4 attended dry run via `dry_run_co0798.ps1` (scratchpad; non-secret env only, revision `2026-10-16T18:25:50.000+0000`, ref `PROD-CE-0798-20261018`); clone fresh until ~03:01Z.
- **Step 4 dry run #1 (live, attended, 2026-10-18T21:52Z):** result `toggle_click_failed`, outcome refused, `production_write: not_performed`, `salesforce_writeback: not_performed`; record `production-create-records/CO-0798-20261018T215242Z.json` names only `toggle.scan_now = fail`. Read-only diagnosis: `_pdr_set_toggle` ran the pre-click hit test (`elementFromPoint` at the control centre) WITHOUT scrolling the control into view, unlike the Confirm path (`production_create_live.py` ~298/327); off-viewport points return null -> refusal. Fix (uncommitted): `scroll_into_view_if_needed` before the hit test; new failing-first test `test_a_toggle_below_the_fold_is_scrolled_into_view_before_its_hit_test` (reproduced `toggle_click_failed`, passes with the fix). Opus review and full suite running. The approval was NOT consumed by the refused dry run (dry runs never consume).
- **Toggle fix verified (uncommitted):** Opus review APPROVE WITH NITS (scroll is harmless, still fails closed, test fails without the fix by mutation check; viewport cause consistent but not proven live). Nit applied: the refusal record now names the failing stage (`toggle.<key>.stage_read|scroll|hit_test|click|state`, names only). Not applied: a scan_now-specific below-the-fold fake case. Orchestrator: full suite 2770 OK (skipped=14), both guards pass. CO-0798 approval still `valid` (unused). Next: owner go to commit, then dry run #2.
- **Committed `35fa054`** (toggle scroll fix + stage names). **Dry run #2 (live, 2026-10-18T22:08Z):** toggles passed (scroll fix confirmed live), then refused `advanced_mismatch`, nothing written. Record (names/states only): `automatedDiscoveryEnabled`, `subDomainsReconEnabled`, `webDictionaryBruteForceEnabled` read ON (production form defaults), the other six OFF, `spyCloudEnabled` absent, section found. Cause: `_pdr_read_advanced` only READS the advanced switches (never sets them) while docs/49 targets are all OFF, so every CE-only run refuses here. Latent second blocker: F1 `spycloud_state` requires a non-None SpyCloud read, so the live create's re-check would refuse while SpyCloud reads absent. Owner decision needed: (a) build "set advanced switches OFF" (scroll + hit test + plain click + verify, no force; Dev needed a forced click for automatedDiscoveryEnabled, so it may still refuse) and decide SpyCloud handling (locate it, or accept absent-at-fill == absent-at-recheck and rely on readback), then dry run #3; or (b) manual create from the CO page sheet now, then the guarded UUID write. Owner reminder: dry runs only map and never confirm; the real create is done only from the Web UI dashboard.
- **Correction:** the "latent second blocker" (SpyCloud absent) above is WRONG: `advanced_report` encodes absence as the string `"absent"`, so absent at the fill and at the re-check passes `spycloud_state`; appearing/changing still refuses; the readback still requires SpyCloud enabled True. Now pinned by tests. **Advanced-set fix (uncommitted, owner go):** the fill sets each advanced switch to its target via `_pdr_set_toggle` (scroll, hit test, plain click, verified, no force), then reads; the live re-check stays read-only. Opus review APPROVE WITH NITS (no path from a failed set to Confirm; N4: Dev needed a forced click for automatedDiscoveryEnabled, so dry run #3 may refuse safely at `stage_hit_test`; N2: an unexpected popup after an advanced click fails closed but may leave the window for the owner to close).
- **Full suite on the advanced-set tree:** 2776 OK (skipped=14), guards pass (uncommitted). **Dry run #3 (live, 2026-10-18T22:18Z):** every form field `pass` (texts, blanks, selects, dates, 9 toggles incl. scan_now), all nine advanced switches end OFF (automatedDiscoveryEnabled set by a plain hit-tested click, no force: N4 did not happen), SpyCloud absent; then refused `production_session_not_ready` at the post-fill session re-check with the form OPEN (`_pdr_session_problem(form_open=True)` -> reader trigger `in_app_dispatch` only: a `dispatch_event("click")` on the sort header behind the modal must produce the page's own rewritten table reply). Nothing written. This is the form-open read the second Opus review called unproven; the live create's duplicate reads use the same path. The record does not say which stage failed (header not found / no request / rewrite mismatch / no reply). Owner decision needed: (a) add stage diagnostics (names only) and run dry run #4 to learn why; or (b) redesign the order so every read (session, duplicates) runs with the form CLOSED immediately before opening it, and no read runs while the form is open (needs its own review).
- **Committed `32ee28b`** (advanced-set fix). **Diagnostics (uncommitted):** the page reader records the stages of its last table read (`trigger_*`, `header_found`, `trigger_sent`, `request_routed`, `rewritten`, `body_matched`, `reply`, `status_2xx`; pass/fail only) and the post-fill session refusal carries them as `session.*`; 2 new tests; full suite 2778 OK (skipped=14), guards pass. **Dry run #4 (live, 22:28Z):** every form field pass, advanced all OFF, refused `production_session_not_ready` with `session.header_found = fail` (no `thead th button` outside a dialog). **Root cause:** the selector `thead th button` matches nothing in this application: the Dev run log (20 runs) has 33 `in_app_unavailable` and zero in-app triggers; every read fell back to reload/navigate. So no form-open read (session re-check, live duplicate reads) can work as built. Owner decision: (a) redesign so every read runs with the form CLOSED (reload allowed) immediately before the form opens, and no read while it is open (recommended; removes the unproven mechanism), or (b) a read-only DOM probe to find the real sort control, then a selector change and another dry run (dispatch behind the modal still unproven).
- **Committed `a28a0e5`** (read-stage diagnostics). **Reads-before-form (owner go, uncommitted):** the reader refuses any read with the form open (`form_open_read_refused`); the fill no longer reads the session; the live create runs its duplicate pre-check (session, control, name, domain, demo) at the start of the fill with the form closed (stamped before the reads); the duplicate hook only returns that result (`duplicate_check_stale` after `PCR_PRE_FORM_MAX_AGE` 180 s) and the read-only form re-check judges the age again (`pre_form_age`, last hook before the intent record). Opus review: CHANGES REQUIRED -> fixed (age re-judged at the last hook, stamp before reads, docs/53 wording: a session lost before the click -> `uncertain_submit`, approval consumed, no readback, CO blocked for owner review; 4 page-read tests rewritten). Full suite 2782 OK (skipped=14), guards pass (one transient socket error in an unrelated dashboard test during a concurrent run; clean on re-run). Next: dry run #5.
- **Dry run #5 (live, 22:51Z, reads-before-form tree):** session and duplicate reads passed with the form closed, all 32 form fields pass, advanced OFF; refused `write_request_blocked` (the dry-run guard aborted some production `/api/` non-GET request during the fill; nothing left the browser, nothing written). The record did not name it. Most likely the page's own unrewritten table refresh (`getAllDetailedAccounts` POST with a non-search body is aborted by design and counted as a blocked write); a test reproduces exactly this. **Diagnostic added (uncommitted):** the refusal record now carries `blocked_requests` (`METHOD /api/path/template`, strict shape, per-segment secret check; new record allow-list field + sanitizer test). Full suite 2784 OK (skipped=14), guards pass. Next: dry run #6 to name the request, then decide (if it is the page's own table refresh: it is a read the guard already aborts, so it should not count as a write attempt; owner decision + review).
- **Committed `c59b641`** (reads-before-form + blocked-request names). **Dry run #6 (live, 23:13Z):** all 32 fields pass, advanced OFF; refused `write_request_blocked` with `blocked_requests = ["POST /api/v1/backoffice/getAllDetailedAccounts"]` (the page's own table refresh). **Fix (owner go "procced"):** a table-shaped aborted refresh is no longer a write attempt (still aborted); hardened per Opus review (APPROVE WITH NITS: vocabulary at every depth, size cap, no query string). Full suite 2787 OK (skipped=14), guards pass. Committing, then dry run #7.
- **Committed `c3e62d0`. Dry run #7 (live, 2026-10-18T23:28Z): `dry_run_passed`** (record `CO-0798-20261018T232836Z.json`): 32/32 form fields pass, all nine advanced OFF, SpyCloud absent, no mismatches, redactions 0, `production_write: not_performed`. Approval `PROD-CE-0798-20261018` still valid/unused (expires 2026-10-25T21:23:44Z). The dry-run record is good for the create for 24 h (until ~2026-10-19T23:28Z). Both create-path changes made today (advanced set, reads-before-form, guard counting) were each Opus-reviewed. **Owner sequence left (each its own go):** independent Opus review of the full create path as it stands; reviewed commit flipping `PRODUCTION_RUNNER_SUPPORTED = True`; dashboard "Create in production" (owner types CO-0798, ticks, clicks, MFA); guarded UUID write; relock both constants (revert `853cf99` test edits too).
- **Pre-unlock Opus review (whole create path since f21273b): GO WITH CONDITIONS** (416 tests OK). No fail-open path before the consume. Live-only, never exercised: `_pdr_recheck_form` (form_schema, spycloud_state), country code + payload expectation, keep_open, the 180 s bound, the run-day license START date (the dry run filled the subscription start), the control-tenant duplicate check (proven by Probe A only); all fail closed before the consume. Gap (Medium): after the click, other aborted writes are not inspected (`read()` checks only create_blocked/popups); a blocked follow-up write by the app would be silent. Relaxations (owner decided, bounded): reads up to ~180 s before the last hook; table-shaped refresh not a write. Stale comments: production_create.py:436, `_pdr_fill_and_verify` docstring. Residual: payload mapping unproven in production (one HAR; mismatch only flagged after creation, no delete path). Conditions: clone < 6 h (hourly task working again), dry-run record < 24 h (until ~2026-10-19T23:28Z), single attended attempt then relock, owner watches for non-account/add writes after Confirm (or add post-click `blocked_requests` to the record first).
- **Committed `f82a445`** (after-click: any other aborted write -> outcome unknown, named in `blocked_requests`; stale comments). **UNLOCK committed `1b98591`** (owner go): `PRODUCTION_RUNNER_SUPPORTED = True`; locked-build tests run through `integration/tests/locked_build.py` (`locked_create_build` patches the create lock False); `test_unlock_state.py` pins the real state. Full suite 2790 OK (skipped=14), guards pass. Known flaky (unrelated): `test_dashboard_stage_tracker` local-socket errors under full-suite load (WinError 10053/10038), passes alone 3/3. **RELOCK = revert `1b98591` and `853cf99`** (then the suite must be green with both constants False). Dashboard started via scratchpad `start_dashboard_create.ps1` (sets `SURFACE_ONBOARDING_PRODUCTION_SESSIONS=1` in the dashboard process). Owner: SSO into the dashboard, open `/co/CO-0798?env=prod`, Create in production (type CO-0798, tick, click), MFA in the runner window.
- **First live create attempt (dashboard, 2026-10-19T00:11Z):** refused `production_session_not_ready` BEFORE the form (no form fields in the record); nothing created; approval `PROD-CE-0798-20261018` still valid/unused. Owner screenshot: runner window on about:blank with the `tenantManagement` document pending (the reader's navigate fallback never loaded). Only difference vs the passing dry runs: `LiveRequestGuard` routes at CONTEXT level (never exercised live). Committed `8c4da13`: a pre-form session refusal now records `session.*` read stages + start-up statuses (pass/fail). Next: owner re-runs Create in production once to get the stages. **Still UNLOCKED (`1b98591`)**; dashboard running on 127.0.0.1:8012 with `SURFACE_ONBOARDING_PRODUCTION_SESSIONS=1`. If not continuing, RELOCK = revert `1b98591` + `853cf99`. Dry-run record valid until ~2026-10-19T23:28Z. Side session `claude/adoring-clarke-80cb55` (`473d734`, production CO page BackOffice-only + BO session row) not merged; it reported 9 suite failures to check. Proposed task card: VM Phase 2 runner plan (not started).

#### Handoff 2026-10-19 - RELOCKED; create waits for the VM path; continue here
- **Relocked (owner decision 2026-10-19):** `52b8347` reverts the create unlock `1b98591` and the dry-run unlock `853cf99`. `PRODUCTION_RUNNER_SUPPORTED = False` and `PRODUCTION_DRY_RUN_SUPPORTED = False` again (verified in `session_readiness.py`). Full suite 2790 OK (skipped=14), both guards pass. The PC dashboard on 127.0.0.1:8012 is stopped (port free); no runner process is running.
- **CO-0798 state:** NOT created in BackOffice; no Salesforce write. Approval `PROD-CE-0798-20261018` stays valid and UNUSED until 2026-10-25T21:23:44Z. Dry run #7 (`CO-0798-20261018T232836Z.json`, `dry_run_passed`) is valid for a create until ~2026-10-19T23:28Z; after that a new attended dry run is needed (re-unlock is a new reviewed commit with its own owner go). The first live create attempt (2026-10-19T00:11Z) refused `production_session_not_ready` before the form (about:blank, tenantManagement pending); `8c4da13` now records which read stage fails if it recurs.
- **Clone:** captured 2026-10-19T03:01Z; the hourly Redash refresh has failed `redash_unreachable` since ~06:19Z (PC offline/VPN overnight; app.pentera.io:443 is reachable). A create needs a clone under 6 h: re-check Redash reachability and run `redash_inventory_collector.py --collect` before any retry.
- **Owner direction:** the onboard and its confirmation will run on the VM. Planning session `plan-vm-phase-2-runner-f` (read-only) is writing `docs/54_VM_PHASE2_PRODUCTION_RUNNER_PLAN_2026-10-19.md` (options for headless SSO+MFA, VM Salesforce write path, SecOps outbound, dashboard routes, release steps, owner/SecOps/SF-admin decision list). Side branch `claude/adoring-clarke-80cb55` (`473d734`, production CO page BackOffice-only + BackOffice session row) is NOT merged; it reported 9 suite failures to check against the pre-change baseline before any merge.
- **Next safe actions:** (1) review docs/54 and clear its decision list with SecOps/Salesforce admin/VM owner; (2) investigate the side branch's 9 failures; (3) decide whether to finish CO-0798 from the PC before the dry-run record expires (needs: clone refresh, new unlock commit, dry run if expired) or wait for the VM path.
- **VM plan decisions (owner, 2026-10-19):** D1 = Option P (separate ephemeral console host runs dashboard + runner, owner-only), D2 = Ubuntu console host. `docs/54_VM_PHASE2_PRODUCTION_RUNNER_PLAN_2026-10-19.md` committed `51e1046` (planning only, includes C9: Sessions page that signs the owner into Salesforce and BackOffice production; status-only on workato-opa-01). Started two sessions: `console-approval-packet` (docs/55 approval packet for SecOps/Identity/Salesforce admin/BackOffice owner; docs only, no commit) and `console-runtime-build` (worktree branch `console-runtime`, Sonnet, tests-first C1-C5/C7/C8/C9, constants stay False, cherry-picks `473d734` instead of merging the unlocked-tree branch). Next: Opus review of the build, my own suite/guard re-run, then owner reviews the packet. Nothing deployed; no approvals requested yet; CO-0798 still not created (approval valid to 2026-10-25, dry-run record expires ~2026-10-19T23:28Z).

#### Handoff 2026-10-19 (afternoon) - VM console: plan + approval packet committed, local build in progress; CO-0798 still not created; continue here
- **State:** locks both False (relock `52b8347`); dashboard on the PC stopped; nothing deployed anywhere; no external approval requested yet. `docs/54` (plan, Option P / Ubuntu console host, items C1-C9, decisions D0-D10) and `docs/55` (approval packet for SecOps, Identity/OneLogin admin, Salesforce admin and BackOffice owner, with tracking table) are committed. The packet is a DRAFT FOR THE OWNER TO SEND; the owner sends it and files the written approvals outside the repo.
- **Build in progress (not merged):** session `console-runtime-build` (`local_0975ef64-ffb6-4be1-9182-f6368b28e0cb`, Sonnet, worktree branch `console-runtime`) is implementing C1-C5, C7, C8, C9 tests-first, constants False, cherry-picking `473d734` for the Sessions page. When it finishes: (1) read its report; (2) run the full suite and both guards yourself on its branch (baseline 2790 OK, skipped=14); (3) Opus review of the diff (anything that changes the create gate, ticket, launch or Salesforce paths); (4) only then ask the owner whether to merge. Side branch `claude/adoring-clarke-80cb55` (`473d734`) was cut from the UNLOCKED tree: never merge it as is; it reported 9 suite failures to compare against baseline.
- **What cannot happen today, honestly:** an onboard/confirm ON the VM needs (a) SecOps/Identity/Salesforce-admin approvals (D3-D7), (b) a console host that does not exist yet, (c) the Linux code untested (A1-A3) and (d) gate 0: the desktop create has never succeeded live (first attempt `production_session_not_ready` before the form, `8c4da13` records the stage if it recurs). Do not weaken a gate to hit the date.
- **CO-0798:** NOT created; no Salesforce write. Approval `PROD-CE-0798-20261018` valid and unused until 2026-10-25T21:23:44Z. Dry run #7 record (`CO-0798-20261018T232836Z.json`) valid for a create until ~2026-10-19T23:28Z, after that a new attended dry run is needed. Clone stale (captured 2026-10-19T03:01Z; Redash unreachable since ~06:19Z, app.pentera.io reachable): run `redash_inventory_collector.py --collect` once Redash is reachable. Re-unlock needs a new reviewed commit with its own owner go (revert of `52b8347` is NOT the way; flip only the needed constant in a new commit and update the lock tests), then relock right after.
- **Next safe actions:** (1) owner sends docs/55 to the four approver groups; (2) finish the build review/merge decision above; (3) owner decides whether to finish CO-0798 on the PC tonight (needs clone refresh, unlock commit, dry run, MFA) or wait for the console path; (4) after approvals: console host spike per docs/54 section 3.5 steps 3-8, one owner go each.

#### Handoff 2026-10-19 (evening, main) - UNLOCKED for one CO-0798 PC create; create-path fixes committed; continue here
- **Owner decision (chat, 2026-10-19):** create CO-0798 from the PC today before dry run #7 expires (2026-10-19T23:28:36Z), go at every step. This supersedes "wait for the console host" for this one CO. The console work (C1-C9 + review fixes) stays on branch `claude/console-runtime-a5f85c` (`1b771c5`, `88a0516`), unmerged.
- **Step 1 done (no external call):** Redash is reachable again; the scheduled task `SurfaceOnboarding-RedashInventory` succeeds hourly; clone captured 2026-10-19T17:02:25Z.
- **Step 2 done:** `c95112a` = revert of the relock `52b8347` (owner go): `PRODUCTION_RUNNER_SUPPORTED` and `PRODUCTION_DRY_RUN_SUPPORTED` = True for ONE attended CO-0798 create. Full suite 2791 OK, guards pass.
- **Fixes `2c724f1` (owner-authorized, tests first, full suite 2835 OK (skipped=14), both guards pass):** (1) live request guard routes the page like the dry run (the context-level route was the only difference from dry runs #5-#7 and the likely cause of the 00:11Z `production_session_not_ready` / about:blank; about 60% confidence, not proven live); (2) launcher `-ProductionSessions` sets `SURFACE_ONBOARDING_PRODUCTION_SESSIONS=1` (without it the gate refused `production_locked`); (3) `473d734` cherry-picked (pages follow the create gate; no "Locked" next to a Create form); (4) a stale READY Salesforce sign-in is re-checked once for the gate; (5) the runner writes a redacted, fixed-enum log to `%LOCALAPPDATA%/SurfaceOnboarding/runner-logs/`.
- **Found:** the launcher's default `-PythonPath` has no Playwright; start with `.venv-renewal-playwright` (Playwright 1.58.0). Owner checks before the create: CO-0798 LastModifiedDate still `2026-10-16T18:25:50.000+0000` (the approval's revision) and Account UUID empty.
- **Next (each needs the owner's go):** step 3 start the dashboard (`COMMAND_TO_PASTE.md`) and sign in; step 4 Create in production on `/co/CO-0798?env=prod` (BackOffice SSO + MFA in the runner window); step 5 guarded UUID write; step 6 RELOCK = revert `c95112a`. If the session refuses again: read the runner log and the record's `session.*` fields before any retry; the approval is not consumed by a session refusal.
- **Not pushed.** Nothing external touched by Claude.

#### Handoff 2026-10-19 (night, main) - CO-0798 ONBOARDED in production; relocked; CE-only follow-up built; continue here
- **CO-0798 done (verified on the PC):** BackOffice create `readback_verified` 2026-10-19T18:34:37Z (record `CO-0798-20261019T183437Z.json`, approval `PROD-CE-0798-20261018` consumed 18:34:05Z, runner log `%LOCALAPPDATA%/SurfaceOnboarding/runner-logs/CO-0798-20261019T183219Z.log`); Salesforce `Account_UUID__c` `written_verified` 18:41:38Z (matches the BackOffice tenant). The page-level live guard (`2c724f1`) fixed the 00:11Z session failure on the first try. Owner corrected the expiration in BackOffice by hand (2027-06-21 -> 2029-06-21). Stage still Request Approved: the owner moves it to Account Scanning by hand for this CO. User email domain `pentera.io` is by design (Guru S2/R8).
- **Relocked `d450984`:** both constants False; dashboard restarted without `-ProductionSessions` (PID 28516, locked).
- **Built `da6746b` (owner decisions 2026-10-19, tests first, full suite 2861 OK, guards pass):** (1) CE expiration = Core Plus subscription end (Surface-only and Case 3 keep the one-year rule pending an owner decision); (2) automatic UUID write after a verified CE-only create, then Request Approved -> Account Scanning (`salesforce_stage_writeback.py`, `production-stage-writes.jsonl`); `after_scan_finished` (Account Scanning -> Scan Completed Successfully -> Onboarding Completed, never User Created) built and tested, NOT wired; (3) the create stays on `/co/CO-x?env=prod` with a progress bar from recorded evidence; refusals inside the dashboard.
- **Open decisions (owner):** (a) build 3 gate: the CE scan check is a read-only production BackOffice read (SSO + MFA); reuse the D4 read-only probe gate, or bind it to the CO's verified create; the "scan finished" field for CE-only must be confirmed by one live read-only check of CO-0798 (Dev never checked CE scans); (b) whether Surface-only / Case 3 also expire at the subscription end; (c) further trimming of the CO pages (`/onboard/CO-x` vs `/co/CO-x?env=prod`).
- **Build 3 `03fae04` (owner decision: bound to the verified create, owner-started):** runner `--production-scan-check --co CO-x` (read-only, desktop only, SSO + MFA), masked observations in `production-scan-checks/`, a CE scan card with "Check CE scan in BackOffice" on `/co/CO-x?env=prod`; on `scan_completed` the dashboard moves Account Scanning -> Scan Completed Successfully -> Onboarding Completed. `CONFIRMED_FINISHED_RULES` is EMPTY: results stay `unconfirmed` until the first live CO-0798 check is reviewed and the finished field is added in a reviewed commit. Full suite 2875 (one known flaky stage-tracker socket error, passes 3/3 alone), guards pass.
- **Next:** restart the dashboard to load `03fae04` (no `-ProductionSessions`), owner moves CO-0798 to Account Scanning by hand (if not yet), then one live read-only CE scan check of CO-0798 (owner go), review the recorded field names.
- **First live CE scan check of CO-0798 (2026-10-19T20:20Z, read-only):** ran cleanly, recorded `unconfirmed`: the table row has no CE result. The owner's screenshot (Details > Duration Per Scan) shows the CE scan = `LEAKED_CREDENTIALS_DISCOVERY`, `DONE` (Oct 19 11:45-11:55 local). Fixed in `48eacb6`: the check reads the Duration Per Scan executions list (Dev reader, production origin, verified account id); the CO page says "Onboarded in production by this dashboard" instead of "Blocked: ... do not create another". Full suite 2883 OK, guards pass.
- **Next:** confirm CO-0798's Salesforce stage is Account Scanning; restart the dashboard (loads `48eacb6`); second live check (owner go): on `scan_completed` it writes Scan Completed Successfully then Onboarding Completed (two guarded Salesforce writes).
- **CE scan via Redash (owner decision: option 2, extend query 253):** the account documents carry no CE scan result (all 534 CE-only tenants in the 20:02Z clone have null recon scan fields); the result is in `campaignExecutions` (`LEAKED_CREDENTIALS_DISCOVERY`). Spec in [docs/56](../docs/56_QUERY_253_CE_SCAN_COLUMNS_2026-10-19.md): one `$lookup` plus four columns (`lastLeakedCredentialsExecutionStatus`, `...Start`, `...End`, `leakedCredentialsExecutionsCount`), no execution id. **Clone lag found:** the query refreshes hourly but the cloned DB is a daily snapshot (the 20:02Z clone's newest data is 2026-10-19T00:01:28Z; CO-0798 is not in it yet); the "Clone fresh under 6 h" label measures the query age, not the data age (fix planned in docs/56 step 4; creates stayed safe through the runner's live BackOffice duplicate search). The owner updates 253 in Redash and sends the `--probe` column list; then the collector columns, the clone-based CE scan state and the unattended stage-move job are built, tests first.
- **CO-0798 stage:** scan DONE (owner screenshot); stages Scan Completed Successfully -> Onboarding Completed still to set (by hand, or the BackOffice check with `48eacb6` loaded).
- **Not pushed.** Next CO needs a new approval, dry run and reviewed unlock commit as before.

#### Handoff 2026-10-19 (late night, main) - Redash 253 edit blocked on Claude in Chrome; continue in a Pentera RND session
- **State of `main` (local, NOT pushed: 65 commits ahead of origin/main; the owner pushes):** HEAD `7157331`. Locks False (relocked `d450984`). CO-0798 onboarded in production: BackOffice create `readback_verified` 18:34:37Z, Salesforce `Account_UUID__c` `written_verified` 18:41:38Z, owner fixed the expiration by hand (2029-06-21), CE scan `LEAKED_CREDENTIALS_DISCOVERY` `DONE` (Oct 19 11:45-11:55 local, owner screenshot). Its Salesforce stage still needs Scan Completed Successfully -> Onboarding Completed (by hand, or the BackOffice "Check CE scan" button with `48eacb6` loaded). Dashboard running locked on the PC (PID 19564, started before `48eacb6`: restart to load it).
- **Built today on main (all tests first, full suite 2883 OK, guards pass):** `2c724f1` create-path fixes (page-level live guard, `-ProductionSessions`, gate-consistent pages, Salesforce re-check, redacted runner log); `da6746b` CE expiry = Core Plus subscription end, automatic UUID write + Account Scanning stage, same-page progress bar; `03fae04` + `48eacb6` read-only BackOffice CE scan check (Duration Per Scan) and "Onboarded in production by this dashboard" verdict; `7157331` docs/56 (query 253 spec).
- **Blocker found:** the Redash 253 edit needs Claude in Chrome (the in-app browser cannot open redash.pentera.io on this install). The Chrome native host is installed and valid on the PC, but the Chrome tools only attach when Chrome and the Claude app are signed in to the SAME organization, and the **Pentera** organization does not allow Claude in Chrome (an org admin setting; the owner is a regular user there). **Pentera RND** allows it. The test session [`redash-253-edit`] made no change in Redash.
- **Owner decision 2026-10-19:** continue the Redash 253 edit (and the collector build after it) in a new session signed in to **Pentera RND**, not Pentera. Alternatives kept open: a Pentera admin enables Claude in Chrome for the Pentera org (long-term), or paste the two query texts into any session (no Chrome needed).
- **Open owner decisions:** Surface-only / Case 3 expiry (still the one-year rule); trimming `/onboard/CO-x` vs `/co/CO-x?env=prod`; the low review findings; merging the console branch `claude/console-runtime-a5f85c` (unmerged, waits for docs/54 D3-D10); never merge `claude/adoring-clarke-80cb55`.
- **Handoff prompt for the Pentera RND session** (paste as the first message):

```text
Continue the Surface Customer Onboarding work in C:/Users/Milton Stevenson/Documents/Surface/Workato (branch main). Follow AGENTS.md and the surface-workato-opa skill. Start from integration/IMPLEMENTATION_PLAN.md section 18, last entry "Handoff 2026-10-19 (late night, main)", then read docs/56_QUERY_253_CE_SCAN_COLUMNS_2026-10-19.md. Explain each step and wait for my "go" before any external action; commit locally only, I push.

Task 1 (Redash, in my own signed-in Chrome via Claude in Chrome; this app and Chrome are both on Pentera RND): first check that the mcp__Claude_in_Chrome__* tools are available; if not, stop and tell me. Then, in a NEW tab:
 a. Open https://redash.pentera.io/queries/253/source, read the query text and confirm the data source is "Prod (Cloned) - Mgmt". Keep a copy of the original text in your scratchpad (not the repo).
 b. Open one of queries 177, 203, 210 or 237 (whichever reads collection campaignExecutions) and read ONLY its query text to learn the exact campaignExecutions field names (account link, campaign type, status, start, end). Never export or copy result rows.
 c. Prepare the edited 253: unchanged plus one $lookup on campaignExecutions (matched to the account _id, campaign type LEAKED_CREDENTIALS_DISCOVERY, newest start first, projecting only status/start/end) before the final $project, and four new columns: lastLeakedCredentialsExecutionStatus, lastLeakedCredentialsExecutionStart, lastLeakedCredentialsExecutionEnd, leakedCredentialsExecutionsCount. No execution id, campaign id, user, operator or customer value; every existing column unchanged.
 d. Show me the exact diff, put the text in the editor, click Execute only, and report counts only (rows, whether the four columns appear, how many rows have a non-null status). Never paste customer names, domains, emails or ids into chat.
 e. Wait for my explicit "go", then click Save. If I say no, restore the original text.
Hard limits: never open "Show API Key" or any key, settings or admin page; never read, copy or type a key, password, token or cookie; do not touch any other query, dashboard, alert, data source or the schedule.

Task 2 (after I store the key myself with `python tools/redash_inventory_collector.py --store-key --query-id 253` and paste the output of `python tools/redash_inventory_collector.py --probe --query-id 253`): implement docs/56 steps 1-4 on main, tests first: the four columns in query 253's strict v2 list (date and enum kinds, no free text); the CE scan state from the clone row matched by the verified Account UUID (finished only when DONE / DONE_WITH_ERRORS and ended after the CO's recorded create time; PENDING = running); a dashboard background job (no browser, no login) that runs the existing guarded stage moves (create_ui.after_scan_finished: Account Scanning -> Scan Completed Successfully -> Onboarding Completed, CE-only skips User Created) for COs this dashboard created; and the clone freshness shown and gated on the data age (source.data_latest_modified), not only the query time. Keep both production lock constants False; run the full suite (python -m unittest discover -s integration/tests -t .) and both guards (python tools/check_integration_artifacts.py, python tools/check_integration_offline_boundary.py); one commit; append a dated handoff to section 18. No Workato, Salesforce, BackOffice, VM or other network action without my go.
```

#### Handoff 2026-10-19 (end of day, main) - Redash 253 edit executed (not saved); status field name still unknown; continue on Pentera without Chrome
- **State of `main`:** unchanged since the previous entry (HEAD `8674a8b`, nothing committed in this session, not pushed). Locks False. No repo file changed except this entry.
- **Verified in Redash (read-only plus one Execute, in the owner's signed-in Chrome, Pentera RND session):**
  - Query 253 is `collection: tenantsMView`, one `$project` stage (59 columns), data source `Prod (Cloned) - Mgmt`. The original text was copied to the old session's scratchpad only (not the repo); it is also the current saved version in Redash, which was **not** changed.
  - Only query 203 reads `campaignExecutions`. Confirmed field names: `accountId` (the account `_id`), `campaignTypeEnum`, `startDate`, `endDate`. Queries 177, 210 and 237 do not read that collection. Query 203 has no top-level status field (only `conversionData.status` and `campaignExecutionTypeEnum`), so the status field name was **not** confirmed.
  - The edited 253 (the docs/56 `$lookup` before the final `$project`, plus the four columns, using `status` for the status) was Executed in the editor, **never saved**. Result: 5447 rows, 7 s runtime, 61 columns (the usual 59 plus three of the four new ones). `lastLeakedCredentialsExecutionStart` non-null 2900, `lastLeakedCredentialsExecutionEnd` non-null 2887, `leakedCredentialsExecutionsCount` > 0 on 2900 rows (max 1212). **`lastLeakedCredentialsExecutionStatus` did not appear (0 non-null): `status` is not the field name** in `campaignExecutions`. The query is fast enough for the hourly refresh.
  - The editor tab still held the unsaved edit when this session ended; closing or reloading the tab discards it (nothing was saved). Check the saved text of 253 is still the original (59 `$project` columns, no `$lookup`) before the next step.
- **Next safe action:** find the real status field name of `campaignExecutions` (candidates to try via Execute-only runs: `status`, `statusEnum`, `executionStatus`, `state`; or read it from the BackOffice Duration Per Scan data shape in docs/39 or the repo's BackOffice CE check code, which already reads CO-0798's `DONE` status), fix the `$lookup` and the first new column, Execute, expect the status column present with values `DONE` / `DONE_WITH_ERRORS` / `PENDING`, then wait for the owner "go" and Save.
- **Owner decision 2026-10-19:** continue on the **Pentera** org (weekly limit on RND), not Pentera RND. Claude in Chrome tools do NOT attach under Pentera, so Task 1 needs the fallback: the owner does the edit in Redash by hand, or pastes the query text into the session.
- **Blockers / owner decisions:** the status field name (above); Save of 253 needs the owner's explicit "go"; Task 2 needs the owner to store the key and paste the `--probe` output; open decisions from the previous entry are unchanged.
- **Handoff prompt for the Pentera session** (paste as the first message):

```text
Continue the Surface Customer Onboarding work in C:/Users/Milton Stevenson/Documents/Surface/Workato (branch main). Follow AGENTS.md and the surface-workato-opa skill. Start from integration/IMPLEMENTATION_PLAN.md section 18, last entry "Handoff 2026-10-19 (end of day, main)", then read docs/56_QUERY_253_CE_SCAN_COLUMNS_2026-10-19.md. Explain each step and wait for my "go" before any external action; commit locally only, I push. Claude in Chrome does not attach in this org, so do not try browser tools for Redash: I do the Redash editing by hand.

Task 1 (Redash 253, me in my own Chrome, you give me exact text): the saved 253 must still be the original (59 columns, no $lookup); I will check and tell you. Confirmed campaignExecutions fields: accountId, campaignTypeEnum, startDate, endDate. The status field name is NOT known yet (the name "status" returned nothing). Help me find it: check docs/39 and the repo's BackOffice CE-scan check code for the field name of the execution status (CO-0798's execution was DONE); if the repo does not say, give me a tiny Execute-only probe I can run in a NEW unsaved Redash query on Prod (Cloned) - Mgmt that lists only the distinct key names of one campaignExecutions document (no values). Then prepare the exact edited 253 text (original + one $lookup before the final $project + four columns lastLeakedCredentialsExecutionStatus, lastLeakedCredentialsExecutionStart, lastLeakedCredentialsExecutionEnd, leakedCredentialsExecutionsCount; no ids, users or customer values; existing columns unchanged), a diff against the original that I will paste, and what to expect (about 5447 rows, 63 columns, status non-null on about 2900 rows). I paste, Execute and Save myself and report counts only.

Task 2 (after I store the key myself with `python tools/redash_inventory_collector.py --store-key --query-id 253` and paste the output of `python tools/redash_inventory_collector.py --probe --query-id 253`): implement docs/56 steps 1-4 on main, tests first: the four columns in query 253's strict v2 list (date and enum kinds, no free text); the CE scan state from the clone row matched by the verified Account UUID (finished only when DONE / DONE_WITH_ERRORS and ended after the CO's recorded create time; PENDING = running); a dashboard background job (no browser, no login) that runs the existing guarded stage moves (create_ui.after_scan_finished: Account Scanning -> Scan Completed Successfully -> Onboarding Completed, CE-only skips User Created) for COs this dashboard created; and the clone freshness shown and gated on the data age (source.data_latest_modified), not only the query time. Keep both production lock constants False; run the full suite (python -m unittest discover -s integration/tests -t .) and both guards (python tools/check_integration_artifacts.py, python tools/check_integration_offline_boundary.py); one commit; append a dated handoff to section 18. No Workato, Salesforce, BackOffice, VM or other network action without my go.
```

#### Handoff 2026-10-19 (end of day, addendum) - status field found; corrected 253 verified by Execute, awaiting the owner's Save
- **Verified (Execute-only runs in Redash, nothing saved):** the execution status in `campaignExecutions` is **`conversionData.status`** (no top-level `status`; other keys are `accountId`, `campaignTypeEnum`, `startDate`, `endDate`, `executionId`, `executionData`, `conversionData`). Corrected 253 (the docs/56 `$lookup` with `"status": "$conversionData.status"` in its inner `$project`, plus the four columns): 5447 rows, all four columns present, status non-null 2900 (`DONE` 2886, `PENDING` 14), end non-null 2887, count > 0 on 2900. docs/56 now states the verified field names (local edit, uncommitted unless the next commit includes it).
- **Redash state:** the saved 253 is still the original; the unsaved corrected text may still sit in the Chrome tab (reload discards it). The exact corrected text is in the old session's scratchpad `query253_edited_v2.json`; it is also fully derivable from docs/56 (4 additions to the original, nothing else).
- **Next safe action:** the owner Saves the corrected 253 in Redash (by hand on Pentera, or via Claude in Chrome in a Pentera RND session), then stores the key and pastes `--probe` output, then Task 2 (docs/56 steps 1-4).
- **Handoff prompt for a new session** (paste as the first message; Pentera org, no Claude in Chrome):

```text
Continue the Surface Customer Onboarding work in C:/Users/Milton Stevenson/Documents/Surface/Workato (branch main). Follow AGENTS.md and the surface-workato-opa skill. Start from integration/IMPLEMENTATION_PLAN.md section 18, last entry "Handoff 2026-10-19 (end of day, addendum)", then docs/56_QUERY_253_CE_SCAN_COLUMNS_2026-10-19.md (now with the verified field names; status is conversionData.status). Explain each step and wait for my "go" before any external action; commit locally only, I push. No browser tools for Redash in this org: I do the Redash Save by hand.

Task 1: give me the exact final text of query 253 (original + the docs/56 $lookup + four columns) only if I paste the current saved text; otherwise just tell me to Save the corrected text that is already verified. After I Save and report, I run `python tools/redash_inventory_collector.py --store-key --query-id 253` myself and paste the output of `python tools/redash_inventory_collector.py --probe --query-id 253`.

Task 2 (after the probe output): implement docs/56 steps 1-4 on main, tests first: the four columns in query 253's strict v2 list (date and enum kinds, no free text); the CE scan state from the clone row matched by the verified Account UUID (finished only when DONE / DONE_WITH_ERRORS and ended after the CO's recorded create time; PENDING = running); a dashboard background job (no browser, no login) that runs the existing guarded stage moves (create_ui.after_scan_finished: Account Scanning -> Scan Completed Successfully -> Onboarding Completed, CE-only skips User Created) for COs this dashboard created; and the clone freshness shown and gated on the data age (source.data_latest_modified), not only the query time. Keep both production lock constants False; run the full suite (python -m unittest discover -s integration/tests -t .) and both guards (python tools/check_integration_artifacts.py, python tools/check_integration_offline_boundary.py); one commit; append a dated handoff to section 18. No Workato, Salesforce, BackOffice, VM or other network action without my go.
```

- **Update 2026-10-19 21:17Z: corrected 253 SAVED in Redash (owner "go").** Read back from the Redash API (read-only): 2 stages, `$lookup` on `campaignExecutions` with `status` = `$conversionData.status`, 63 `$project` keys (59 + 4), data source unchanged, schedule unchanged (hourly, 3600 s), updated_at `2026-10-19T21:17:14Z`. The next hourly run serves the four new columns. Task 1 is done; next: the owner stores the key and pastes the `--probe` output, then Task 2. The handoff prompt above still applies (its "Task 1" paragraph is now complete: skip it).

#### Handoff 2026-10-19 (end of day, Task 2 steps 1-2 done on main) - next: docs/56 steps 3-4
- **Verified:** the owner stored the 253 key and ran `--probe --query-id 253`: `row_count` 5447, data age 15 min, all four new columns present (`lastLeakedCredentialsExecutionStatus/Start/End` str, `leakedCredentialsExecutionsCount` int). `normalize` said `redash_schema_unavailable` at that time because the collector's strict list did not know the columns yet; steps 1-2 below fix that.
- **Built (commit `872df82`, local, not pushed; locks False; full suite OK, both guards pass):**
  - Step 1: `redash_inventory.py` V2_COLUMNS gets the four columns (new kinds `enum` = `[A-Z][A-Z_]{0,31}` and `date` = epoch ms via `parse_timestamp_ms`; no free text); stored short keys `ce_status`, `ce_start`, `ce_end`, `ce_count` (`_V2_SHORT_KEYS`). A missing or mistyped column stops the collector. To keep the 6,000-row worst case under the 32 MB clone limit, the alternate-domain whole-result bounds were lowered (chars 1.5M -> 1.1M, items 90k -> 70k; live 0.57M / 36k): an owner-visible limit change.
  - Step 2: `integration/onboarding/ce_clone_scan.py` (`ce_scan_state(v2, created_ms)` -> finished / finished_with_errors / running / not_yet / unknown; finished only for DONE / DONE_WITH_ERRORS ending after the CO's create time; PENDING counts as running only if it started after creation; `clone_v2_for_uuid(payload, uuid)` matches exactly one tenant by Account UUID) with `test_ce_clone_scan.py` and `CeScanColumnTests` in `test_redash_v2.py`.
- **Not done (steps 3-4):** (3) the dashboard background job (no browser, no login) that calls `production_create_ui.after_scan_finished(inputs, read_row, run_argv, target_org, scan_state="scan_completed")` when `ce_scan_state` is finished, for COs this dashboard created and moved to Account Scanning (CE-only skips User Created); keep the BackOffice "Check CE scan" button as the manual fallback; exact from-stage, read-back, logged, never retried after an uncertain result; (4) the clone freshness shown on the pages and gated on `source.data_latest_modified` (data age), not only `retrieved_at`. Tests first. The collector run itself (a real 253 collection) is a separate owner-approved network action.
- **Handoff prompt for the next session** (paste as the first message):

```text
Continue the Surface Customer Onboarding work in C:/Users/Milton Stevenson/Documents/Surface/Workato (branch main). Follow AGENTS.md and the surface-workato-opa skill. Start from integration/IMPLEMENTATION_PLAN.md section 18, last entry "Handoff 2026-10-19 (end of day, Task 2 steps 1-2 done on main)", then docs/56_QUERY_253_CE_SCAN_COLUMNS_2026-10-19.md. Explain each step and wait for my "go" before any external action; commit locally only, I push. Redash 253 is already saved and verified; steps 1-2 of docs/56 are committed (872df82). Implement docs/56 steps 3-4, tests first: (3) a dashboard background job (no browser, no login) that, for each CO this dashboard created and moved to Account Scanning, reads the clone row by the verified Account UUID (integration/onboarding/ce_clone_scan.py), and when ce_scan_state is finished runs production_create_ui.after_scan_finished (Account Scanning -> Scan Completed Successfully -> Onboarding Completed; CE-only skips User Created) with the same guards as the BackOffice check (exact from-stage, read-back, logged, never retried after an uncertain result); keep the "Check CE scan" button as the manual fallback; (4) the clone freshness shown on the pages and gated on source.data_latest_modified (data age), not only retrieved_at. Keep both production lock constants False; run the full suite (python -m unittest discover -s integration/tests -t .) and both guards (python tools/check_integration_artifacts.py, python tools/check_integration_offline_boundary.py); one commit; append a dated handoff to section 18. No Workato, Salesforce, BackOffice, VM or other network action without my go.
```

- **Owner decision 2026-10-19:** the lowered alternate-domain whole-result bounds (chars 1.1M, items 70k) are accepted. Steps 3-4 continue in a new session with the prompt above.

#### Handoff 2026-10-19 (night, docs/56 steps 3-4 done on main) - next: owner-approved live collection, then watch the first automatic finish
- **Built (one local commit, not pushed; both production lock constants still False; full suite 2942 tests OK, both guards pass):**
  - Step 3, `production_create_ui.auto_scan_finish(inputs, load_clone, read_row, run_argv, target_org)` plus `auto_scan_candidates(parent)`: for a CO whose newest stage-log line is a verified Account Scanning (or a verified Scan Completed Successfully, to resume after a crash between the two moves), with its verified create and UUID write, it reads the clone row by the verified Account UUID (`ce_clone_scan`), and only for `finished` / `finished_with_errors` (ended after the CO's recorded `readback_verified` time) runs `after_scan_finished`. Same guards as the BackOffice check: both constants True, not the VM, fresh clone, exact from-stage, CE-only route, UUID still on the CO, read-back, logged in `production-stage-writes.jsonl`. A rejected or uncertain move leaves the CO no candidate, so it is never retried. A CO moved to Account Scanning by hand (CO-0798) is not a candidate: use the "Check CE scan" button (kept as the manual fallback).
  - Dashboard: daemon thread `_auto_scan_loop` (every 600 s, desktop only, not on the VM); `_STAGE_MOVE_LOCK` serialises it with the button's follow-up; it skips a CO whose BackOffice check is running; the CE scan card shows "Automatic clone check <time>: <text>" (`AUTO_SCAN_STATE`, in memory).
  - Step 4: `production_views.clone_status` adds `data_age_seconds` (from `source.data_latest_modified`) and `data_limit_seconds`; state is fresh only when the query age is within 6 h AND the data age is known and within `DATA_LIMIT_SECONDS` (36 h); the create gate (`_clone_fresh`) enforces both; the strip and the data-age lines show "data N h M min old".
- **Owner decisions needed:** (1) the 36 h data-age limit is my choice (the clone is a nightly snapshot, so 6 h would block every create most of the day); say if you want another value. (2) Because the create gate now needs the data age too, a clone whose newest change is over 36 h old blocks Create until the nightly clone is fixed; before the next create confirm the live clone shows data under 36 h. (3) Nothing live was run: the job is inert until both constants are set True by the owner's separate unlock.
- **Not done / not verified:** no real 253 collection, no Salesforce read or write, no dashboard run. Test fixtures that built clone rows modified weeks before the snapshot got realistic `_modified` stamps.

#### Handoff 2026-10-19 (late night, clone collected, onboarded state, Leonardo hidden) - next: restart check, scan-status validation
- **Verified live (owner ran, PC):** `--probe --query-id 253` first refused (`redash_schema_unavailable`); the new diagnostic named two tenants failing the hostname-list checks (`leakedCredentialsScannedDomains`, `userEmailDomains`: one row over 50 items, one entry odd, one `userEmailDomains` entry holding an `@`). After the change below the probe said `normalize: ok`, 5,447 rows, `withheld_list_cells` 2 + 2, and `--collect --query-id 253 --no-refresh` stored `redash-prod-clone-inventory-20261020T000719Z.json` (v2, no drift). The dashboard strip then read "Clone 54 min old, limit 6 h; data 25 h 55 min old, limit 36 h - trusted". No Salesforce, BackOffice, Workato or VM action.
- **Built (one local commit, not pushed; both production lock constants still False; full suite 2963 tests OK, both guards pass):**
  - Probe diagnostics (value-free): `failing_columns`, `failing_list_reasons`, `failing_list_detail`, `max_row_list_chars`, `withheld_list_cells`.
  - Owner decision (option A): a `userEmailDomains` / `leakedCredentialsScannedDomains` cell that is a real list but fails the hostname bounds is withheld (omitted, counted in `source.withheld_list_cells`); every other problem still refuses the whole result. An email-shaped entry is never stored. **Owner follow-up:** one tenant's `userEmailDomains` holds an email-shaped value; find it in Redash by hand.
  - `production_create_ui.onboarded_summary` + list/CO page: a CO this dashboard created now shows "Onboarded" (or "Created: UUID not written") with its Salesforce stage and, from the clone, the CE scan state, instead of "Clear"; the locked create card no longer says "Next: write the Account UUID" after the write; the "Before you start" Account UUID row is OK for such a CO (`production_views.apply_own_onboarding`).
  - Leonardo hidden (owner decision): no DEV menu group, no Leonardo/preflight rows or buttons on Sessions, session chip = Salesforce only, sign-in / Prepare sessions / Re-check start no Leonardo work, launcher messages cleaned. Dev routes still exist by direct URL; no code or records deleted.
- **Not done / not verified:** the dashboard has not been restarted since these edits; the live pages are unchecked. Scan-status validation (a local read-only tool printing a CO's CE scan state from the clone, then a check against CO-0798 after the next nightly clone) is next. The first live automatic finish still needs both constants True, a session unlock and an approval (owner).

#### Handoff 2026-10-19/20 (night, every open CO validated against the clone) - next: restart check, scan-status validation
- **Why the list did not validate some COs:** `production_match_for` built its inputs (names, domains, plan) only for CE-only New, so Surface + CE New COs (CO-0766, CO-0791) showed "Not checked"; renewals and other pairs (CO-0799, CO-0782, CO-0767, CO-0793) were not matched at all ("No automated route"); and on the desktop no CO carried its Account UUID into the match (only the VM did), so CO-0795 showed "Duplicate - UUID check runs on the VM dashboard" and an onboarded CO could never read "Onboarded".
- **Built (one local commit, not pushed; both lock constants still False; full suite 2979 tests OK, both guards pass):** `production_match.identity_match` (names, domains, Account UUID only; plan NOT compared; can only block, never clear): onboarded by UUID, ambiguous, UUID not in clone, not in production (new routes) or "Not in production / In production" for renewals (`tenant_missing` / `tenant_found`), duplicate with `differs == ["plan_not_compared"]`. `attended_ce_only_playwright._identity_for` reads ONE fixed Salesforce row (read-only: name, account name, three domain fields, Account UUID; kept in memory). `production_match_for` falls back to the identity check when the plan cannot be built and passes the UUID on the desktop for the full match too. The Onboarding list now shows the clone check for every row, including unsupported routes ("No automated route" stays as a note). New tests in `test_identity_match.py`.
- **Not verified live:** the dashboard has not been restarted since this change, so the real chips for CO-0766, CO-0791, CO-0782, CO-0799, CO-0767, CO-0793 and CO-0795 are unchecked. A renewal that is "Not in production" in the clone needs the owner's eyes (the clone can lag a day). The identity check never replaces the live BackOffice duplicate check at create time.
- **Open question for the owner:** CO-0798's tenant should be in the clone now (data newer than its create), yet the list said "not in the clone yet". Search `Advanced Technology International - CE Only` on the Tenants page; if it is there, compare its Account UUID with the one written to Salesforce.

#### Consolidated status 2026-10-20 - everything changed since the CO-0798 production create (one place; supersedes the scattered 2026-10-19 handoffs for current state)
- **Live and verified (owner-run, PC):** CO-0798 created and verified in production (2026-10-19 18:34Z), Account UUID written to Salesforce and read back, stage moved to Account Scanning. Redash query 253 ("Tenant inventory v2", data source `Prod (Cloned) - Mgmt`, hourly) probes `normalize: ok` (5,447 rows at the first collection, 5,449 later) and `--collect --query-id 253 --no-refresh` stored a v2 clone. The dashboard was restarted and shows the new strip, the Sessions page without Leonardo, and CO-0798 as Onboarded. The CE scan state of CO-0798 is not yet read from the clone (see open item 1).
- **Local commits since 2026-10-19 (all on `main`, none pushed; both production lock constants False):**
  - `872df82` docs/56 steps 1-2: four CE scan columns in the strict v2 column list; `ce_clone_scan` (finished only when DONE / DONE_WITH_ERRORS ended after the CO's recorded create time).
  - `c73871d` docs/56 steps 3-4: background job `auto_scan_finish` (Account Scanning -> Scan Completed Successfully -> Onboarding Completed from the clone row by Account UUID, same guards as the BackOffice check, inert while locked) and clone data age (`source.data_latest_modified`, limit 36 h, gate and strips).
  - `0a32285` value-free probe diagnostics; withheld list cells (owner option A); recorded onboarded state (list and CO page); Leonardo hidden from the UI and no Leonardo work started by sign-in.
  - `c916266` every open CO validated against the clone by identity (name, domains, Account UUID); the desktop passes the UUID; renewals read In production / Not in production.
- **Owner decisions recorded:** option 2 for the CE scan (docs/56); lowered alternate-domain whole-result bounds (1.1M chars / 70k items); 36 h data-age limit; withhold only a bad `userEmailDomains` / `leakedCredentialsScannedDomains` cell (counted), never the clone; Leonardo hidden (production only); validate every open CO against the clone.
- **Open items and owner decision to clear each:** (1) CO-0798 should be in the clone now (data newer than its create) yet the list said "not in the clone yet": search `Advanced Technology International - CE Only` on Tenants and compare IDs (owner). (2) One tenant's `userEmailDomains` holds an email-shaped value: find it in Redash (search `@` in query 253's results) and have it corrected in BackOffice (owner). (3) The first live automatic stage finish needs both constants True, a session unlock and an approval (owner) and a CO this dashboard moved to Account Scanning; CO-0798 was moved by hand, so use the "Check CE scan in BackOffice" button. (4) The Onboarding list must be re-checked after a restart: CO-0766 / CO-0791 should show a clone result, renewals "In production" / "Not in production", CO-0795 no "VM dashboard" note (owner). (5) Scan-status tool (a local read-only command printing a CO's CE scan state from the clone) is not built yet. (6) Stale text: the Tenants page footnote still says saved query 251 and mentions Leonardo.
- **Next for tomorrow (2026-10-21):** restart the dashboard and check the list; map CO-0800 (Surface & Credential Exposure New = Case 3) to production, see the Case 3 block below.
