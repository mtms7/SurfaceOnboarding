# Phase 2 CO-0715 decision memo and safe intake

Date: 2026-08-26  
Target: Leonardo Development only (`https://leonardo.dev.app.pentera.io/`)  
Status: **ready for CSE manual routing review; external execution blocked**

## Current outcome and next safe action

CO-0715 now has a successful masked Workato Development router test and a
separate minimum-field, read-only Salesforce/DealHub intake. Exactly one
Customer Onboarding record matched. The source identity/revision, approved
combined onboarding motion, three pending subscriptions, and common dates are
now bound in the restricted local intake. The evidence supports
`case_3_combined_baseline` as a candidate route, but does not approve it.

CO-0715 is now ready for CSE manual routing and commercial-evidence review. It
is not eligible for Leonardo preflight, manifest construction, UI form filling,
account creation, or Salesforce writeback.

Next safe action: the commercial/DealHub owner resolves the two null quantity
fields and confirms whether the Core Plus Commercial/SVA Essentials evidence
authoritatively supplies the Credential Exposure entitlement. The CSE process
owner must then validate the candidate Case 3 route against current verified
Guru guidance and approve every Leonardo field/toggle/license mapping. The
template remains `proceed=false` until those gates pass.

## Workato read-only intake evidence — 2026-08-26

Exactly one authorized Test was run against the inactive Development recipe
`CO Source Review and Onboarding Router` (recipe `73688657`), using version 20
at 12:45:05 PDT. Test job `j-AbMTmLdn-H6zC9z-CD` completed `Successful` in
952 ms.

- The malformed `co_number` condition was not met, so `CO-0715` passed the
  format gate.
- The `Total size does not equal 1` condition was not met, proving the
  Salesforce query returned exactly one Customer Onboarding record.
- The masked DealHub subscription search and masked Ruby classification both
  executed.
- Workato showed the recipe as `Inactive` after the test, with 10 successful
  jobs and zero failed jobs.
- The first delivery attempt returned HTTP 404 while the listener was still
  initializing and Workato explicitly showed that no trigger event had been
  received. One retry was made after the listener showed it was waiting; that
  request returned HTTP 200 and produced the single recorded test job above.
- No Salesforce write, OPA request, Leonardo/Donatello/BackOffice action, or
  production access occurred.

Because job data was masked, this result establishes exact-one existence and
execution only. It does not establish the record ID, source revision, selected
subscription identity/revision, unambiguous product/license/quantity/date
semantics, validator decision, or approved Cases 1-6 route.

## Restricted Salesforce and DealHub normalization — 2026-08-26

A separate minimum-field, read-only Salesforce query established:

- exactly one CO record: `a5KR500000lzBXAMA2`;
- source revision: `2026-08-26T16:41:21.000+0000`;
- onboarding product: Surface and Credential Exposure;
- onboarding type: New Product Onboarding;
- stage: Request Approved; approval status: Approved;
- submission date: `2026-08-20`;
- no existing Surface Account ID or Account UUID.

The related-account DealHub query returned exactly three relevant rows, all on
one opportunity, all `Pending`, all with a 36-month term from `2026-09-01`
through `2029-08-31`:

| Subscription ID | Product code | Normalized role | Quantity state |
| --- | --- | --- | --- |
| `a2ZR5000000Ut8jMAC` | `PSG-500` | Surface Go, 500 subdomains | Missing |
| `a2ZR5000000Ut8kMAC` | `PCP-C-500` | Core Plus Commercial, 500 endpoints; SVA Essentials | Missing |
| `a2ZR5000000Ut8lMAC` | `PCB-500` | Additional 500-endpoint bulk package | `2` |

The sanitized Onboarding Comment date range was processed by the local
`onboarding-comment-dates-v1` policy. It matched the three DealHub terms and
returned `ready_for_cse_review`, while correctly keeping `proceed=false` and
requiring CSE manual validation. The comment itself, customer name, domain,
contact data, and raw payload were not retained in the local intake.

A final read-only re-query confirmed that the CO `SystemModstamp` and all three
subscription `SystemModstamp` values were unchanged after normalization.

### Routing assessment

`case_3_combined_baseline` is the supported candidate because the CO explicitly
requests new Surface and Credential Exposure and the DealHub evidence includes
a Surface product plus Core Plus/SVA products. This remains an inference, not
an approved mapping:

- current Guru mapping could not be retrieved because the read-only Guru
  connector is not configured;
- the local commercial-evidence policy does not establish Credential Exposure
  from Core Plus Commercial/SVA Essentials without owner-validated contract
  evidence;
- two DealHub quantity values are null, so license quantities cannot be mapped
  without inventing values; and
- the repository has no approved Case 3 field/toggle/license mapping.

## Verified facts

- Leonardo Development is the Phase 2 target; production BackOffice is not an
  approved write target. [Technical implementation guide](08_TECHNICAL_IMPLEMENTATION_GUIDE.md)
- The recorded Leonardo discovery was visible-UI, Development-only, and
  non-mutating. Tenant Management has search and an Add Account form; the blank
  form was cancelled and its Confirm action was not used. The observed UI has
  security-relevant defaults, which must never be inherited implicitly.
  [Discovery](18_LEONARDO_READ_ONLY_DISCOVERY_2026-08-24.md)
- No supported Leonardo API, service identity, browser automation contract, or
  reliable UUID/readback contract has been supplied. Historical Donatello paths
  are not Leonardo contracts. [Phase 2 handoff](19_PHASE2_LEONARDO_SAFE_EXECUTION_HANDOFF_2026-08-24.md)
- The local `phase2_leonardo` policy is deliberately local-only. Its allowed
  operations stop at `fill_and_pause`; it does not authorize an external call
  or unattended create. It requires hash-bound approval, mapping approval,
  duplicate evidence, an idempotency lock, and trusted runner transport before
  any future adapter could proceed.
- `workato-opa-01` remains the minimal OPA/validator host. A browser runtime or
  Playwright installation there is prohibited without a separate SecOps
  architecture approval. [Phase 2 handoff](19_PHASE2_LEONARDO_SAFE_EXECUTION_HANDOFF_2026-08-24.md)

## Assumptions requiring owner confirmation

- CO-0715 may be a Development test candidate; no local evidence establishes
  its test/synthetic status, source revision, data sensitivity, or retention
  plan.
- The observed Tenant Management search is sufficient only for exact company
  name and primary-domain review. Collision semantics for alternate domains,
  subdomains, email domains, networks, and operator accounts remain unknown.
- Leonardo Audits showed no rows in the limited discovery, so it cannot yet be
  used as verification evidence.

## Recommended decision

Adopt an official Leonardo service API/service-account integration as the
production destination. Until Leonardo Engineering supplies a supported,
versioned contract, use a **temporary attended bridge only for a separately
approved Leonardo Development run**. The bridge must pause before `Confirm`;
it is not an unattended automation path.

| Path | Recommendation | Preconditions and hard boundary |
| --- | --- | --- |
| Supported Leonardo API + workload identity | **Preferred and required for production migration** | Leonardo Engineering supplies supported base URL, versioned schemas/OpenAPI, least-privilege Development scopes, short-lived service identity, exact search, validate/dry-run, atomic create with idempotency, operation status, authoritative readback, audit lookup, rate/retry semantics, and support ownership. |
| Attended UI/Playwright bridge | **Temporary Development-only fallback** | SecOps and Leonardo owner approve a dedicated hardened runner; operator completes MFA; Playwright may fill and reread only, then pauses before a separately authorized human selects `Confirm`. |
| Manual attended UI | Safest interim variant of the fallback | Same restricted manifest, MFA, approval, duplicate, idempotency, verification, and evidence controls; an authorized operator completes all form entry manually. |

Do not call an undocumented browser endpoint, replay HAR content, use saved
browser state, or treat historical Donatello endpoints as an integration.

## Workato feasibility and supported components

Workato should orchestrate validation, approval, restricted manifest storage,
idempotency, and redacted result reconciliation. It should call Leonardo only
through the Leonardo-owner-supported API once one exists. The documented
Workato HTTP/OPA connection model is appropriate for an approved private
service endpoint, not for driving a browser UI.

Workato has an official **RPA by Workato** connector for UI automation, but it
requires a separate Robotiq process/account and access token. It is not a
Leonardo-supported connector and does not remove the need for Leonardo-owner
approval, MFA preservation, an isolated runner, or the attended pause before
submission. Do not adopt it for CO-0715 unless SecOps, the Workato owner, and
the Leonardo owner approve its architecture after the API option is rejected
or unavailable.

For the eventual supported-service route, use Workato API clients with narrow
environment/project/role scope and optional allowed-IP restrictions; keep
tokens only in the approved secret manager/secure Workato connection. Do not
store endpoint credentials in environment properties if a secure connection or
secret manager is available. Workato environment properties are configuration
values, not a substitute for a secrets vault.

Official references: [RPA by Workato](https://docs.workato.com/connectors/rpa-by-workato.html), [on-prem connectivity](https://docs.workato.com/en/on-prem), [API clients](https://docs.workato.com/workato-api/api-clients), [environment roles](https://docs.workato.com/en/user-accounts-and-teams/role-based-access/new-model/system-environment-roles.html), and [environment properties](https://docs.workato.com/features/account-properties).

## CO-0715 execution gates

All gates must pass in order. A failed or unknown gate is a blocked state, not
a retry condition.

1. **Restricted intake:** exact CO number, Salesforce record ID, timezone-bearing
   source revision, selected subscription IDs/revisions, product/license
   semantics, quantity, dates, and source match are captured read-only. Do not
   copy contacts, domains, comments, attachments, credentials, or raw payloads
   into the safe view, chat, tickets, logs, or this memo.
2. **Mapping:** CSE/process owner approves exactly one Cases 1-6 engine value
   and maps every Leonardo field, license value, module, setting, toggle, and
   dependency to authoritative evidence. Every visible default is explicitly
   set or rejected; none is inherited.
3. **Approval:** requester and approver differ; a CSE approver in the approved
   group binds a one-run, Development-only, at-most-60-minute approval to the
   source revision and canonical manifest hash.
4. **Duplicate/idempotency preflight:** owner-approved exact collision checks
   cover company, primary/alternate/email domains, subdomains, networks,
   primary user, and operator. Acquire one atomic lock on the deterministic
   idempotency key. Any zero-or-many ambiguity, prior submitted/verified/pending
   attempt, or uncertain result blocks the run.
5. **Executor:** hard allowlist Leonardo Development origin; hard-block all
   production hosts; require interactive MFA for any user session; set a short
   timeout and one active run. No persistent profile, cookies, storage state,
   password/passkey, token, trace, HAR, video, HTML, or screenshot retention.
6. **Submission and verification:** recompare all values/toggles to the reviewed
   manifest, pause before `Confirm`, then use an independently authorized
   read-only lookup to obtain the UUID and verify every field/toggle. Record
   only the redacted signed result.
7. **Writeback:** Salesforce UUID/status writeback is a separately approved,
   read-after-write action and is not coupled to the Leonardo submission.

## Stop, recovery, and production migration rules

Stop immediately on unknown or mismatched source evidence, missing mapping or
authority, denied/expired approval, incorrect origin, MFA/auth/WAF/CAPTCHA
failure, unauthorized/forbidden response, rate-limit or unexpected schema,
duplicate/collision, selector/default drift, lock failure, missing UUID, or
failed readback. A timeout, navigation loss, partial page transition, or any
uncertain submit becomes `uncertain`: do not retry automatically. Preserve the
lock, perform only a safe read-only reconciliation, and require an owner
decision before another attempt. Do not automatically delete/clean up an
account.

Production migration requires a fresh security design review and recorded
approval after Development acceptance. At minimum: a distinct production
hostname and account/tenant, separate least-privilege workload identity and
secret path, network allowlists, production RBAC, environment-scoped Workato
projects/API clients, audited promotion/versioning, dry-run and negative
tests, idempotency retention and operation-status behavior, read-after-write
consistency/SLO evidence, rollback/kill-switch runbook, masking/retention
review, and an explicitly authorized first production change. No Development
credential, approval, runner, or manifest can promote a production action.

## Required owners and approvals

| Decision | Accountable owner(s) | Required before |
| --- | --- | --- |
| CO-0715 read-only source and DealHub evidence; UUID destination field | Salesforce owner; commercial/DealHub owner | Manifest creation |
| Case mapping and all explicit field/toggle/license defaults | CSE process owner; Surface product owner | Approval request |
| Leonardo supported API/service identity, schemas, duplicate/readback/audit contract | Leonardo Engineering/product owner | API implementation |
| UI/Playwright method, form semantics, Development fixture and cleanup policy | Leonardo owner | Any UI fill |
| Isolated runner, MFA/session/network/logging controls; production architecture | SecOps | Any browser-assisted run; production migration |
| Restricted storage, hash attestation, lock, Workato roles/connections and audit masking | Workato owner | Dispatch/preflight |
| One-run Development authorization | Automation owner, separate CSE approver, Leonardo owner, SecOps | Immediately before fill/Confirm |
| Salesforce writeback and any production action | Salesforce owner plus recorded production approvers | Each separate write |

## Acceptance evidence for an approved Development pilot

Use a synthetic fixture approved by the Leonardo owner. The first pilot must
prove, without retaining restricted data: wrong environment is blocked;
self-approval/expired approval is blocked; invalid mapping/default dependency
is blocked; collision is blocked; duplicate and uncertain submission do not
retry; MFA remains interactive; explicit fields/toggles are correctly read
back; result binds to the manifest hash and idempotency key; and Salesforce
remains unchanged unless a separate writeback approval is exercised.
