# Phase 2 CO-0697 execution and API-request plan

Date: 2026-08-24  
Target: Leonardo Development only  
Status: Read-only Workato router validation completed; Leonardo execution is not authorized.

## Current decision

Do not send an HTTP request to an undocumented Leonardo browser endpoint. For
the first separately approved Development test, Workato should produce a
strict, immutable execution manifest. An authorized operator should use that
manifest in an attended Leonardo session, preserve MFA, review every field and
toggle, and perform the one approved `Confirm`. If SecOps and the Leonardo
owner approve Playwright, it may fill the form on a separate hardened runner
and must pause before `Confirm`.

The OPA VM remains the small Workato/validation host. It must not become the
browser runner.

## Verified CO-0697 intake

A scoped read-only Salesforce query on 2026-08-24 established:

| Field | Verified state |
|---|---|
| Match count | Exactly one `Customer_Onboarding__c` record |
| CO | `CO-0697` |
| Salesforce record ID | `a5KR500000lTRcuMAG` |
| Source revision | `2026-08-19T22:33:25.000+0000` |
| Product | Credential Exposure |
| Type | New Product Onboarding |
| Stage | New |
| Approval status | Empty |
| Submission date | Empty |
| Surface Account ID | Empty |
| Account UUID | Empty |

### Owner-attested fixture classification

On 2026-08-24, Milton Stevenson confirmed that he created CO-0697 as a clone
of a real Customer Onboarding record specifically for testing. This resolves
the question of whether CO-0697 is an intentional test record. It is an
owner-attested fact, not a conclusion from the Salesforce field query.

Because the clone derives from a real record, treat its contents as restricted
customer data rather than generated synthetic data. Keep it Development-only;
do not copy its comments, contacts, domains, attachments, or raw payload into
the dashboard-safe view, logs, chat, tickets, screenshots, or dispatch envelope.

No comments, contacts, domains, attachments, credentials, or raw payload were
retrieved or retained in the local fixture.

CO-0697 is still blocked before manifest creation. Its test-clone status does
not authorize a Leonardo action. Missing approval and submission state are
sufficient blockers. DealHub/commercial evidence, the authoritative Case 1-6
mapping, exact requester/approver separation, duplicate status, UUID readback,
clone retention/cleanup, and one-run Development authorization also remain
unresolved. The observed product/type suggests a possible Case 2 route, but
that is an inference and not an approved classification.

## How to deliver the request without a Leonardo API

### First controlled test

Use no machine-to-Leonardo request:

1. Workato reads CO-0697 and DealHub evidence without writing Salesforce.
2. The Phase 1 validator and router fail closed on any mismatch.
3. A CSE approver who is not the requester approves the exact source revision.
4. Workato builds the versioned intent in restricted storage, hashes it, and
   obtains a trusted approval attestation bound to that hash and revision.
5. An authorized operator opens Leonardo Development, completes MFA, and
   manually fills the form from the restricted reviewed manifest.
6. The operator rereads every value and toggle; a separate authorized human
   performs the single `Confirm` only under a one-run approval.
7. A separate read-only lookup verifies the UUID and all saved values.
8. Workato records only the redacted result. Salesforce writeback remains a
   separate future approval.

The manual handoff must use a named restricted Workflow App review task/page,
not the dashboard-safe page. It requires role checks, the exact manifest hash,
expiry, a single-use operator claim, no export/download, masked retention, and
an audited acknowledgement. Do not copy the manifest through email, Slack,
tickets, chat, screenshots, or a downloaded raw payload.

### Browser-assisted bridge, only if separately approved

Workato may dispatch a short-lived opaque reference to an internal control API
on a dedicated, hardened runner. The dispatch envelope contains only the
reference, intent hash, nonce, expiry, correlation ID, and approval revision;
it does not contain the raw manifest. The authenticated runner redeems the
reference once through an owner-approved restricted channel. This API is an
interface we own; it is not a Leonardo API. The preferred path is Workato HTTP
through the approved OPA/private network route, with no public runner ingress.
The transport requires SecOps and Workato confirmation of mTLS or equivalent
short-lived signed authentication, replay-safe acknowledgement, masked
retention, maximum body size, timeout, and network segmentation.

The runner must validate the schema/hash, acquire the single-flight lock,
hard-block production, open a fresh non-persistent browser context, let the
operator complete MFA, perform the read-only duplicate check, fill and reread
all controls, and pause. It may return only a signed/verified redacted result.
There is no unattended create operation in the initial contract.

## APIs and service capabilities to request

The names below describe capabilities, not existing or approved Leonardo paths.
Leonardo Engineering must supply the authoritative base URL, methods, paths,
schemas, scopes, and support policy.

### P0 — required for safe account creation

| Capability | Required contract |
|---|---|
| Machine authentication | Development-only workload identity; short-lived tokens; least privilege; approved secret manager; no weakening of human MFA |
| Contract metadata | Versioned schema, enums, dependencies, defaults, feature availability, and compatible contract versions |
| Validate/dry run | Validate the complete onboarding manifest without mutation; return typed field/dependency errors and the canonical payload hash |
| Exact search/collision check | Search by exact account name, primary/alternate/email domains, subdomains, networks, primary user, and operator; return zero/one/many plus typed conflicts |
| Create account | Atomic create with contract version, correlation ID, idempotency key, and payload hash; return UUID or an operation ID |
| Get account by UUID | Authoritative readback of account, primary user, operator binding, every setting/toggle, attack module, and license field |
| Operation status | Resolve asynchronous or uncertain submissions by operation ID or idempotency key without repeating the create |
| Audit lookup | Read audit evidence by correlation ID/idempotency key, including principal, action, target UUID, result, and timestamp without secrets or excess PII |

### P1 — required while mapping Cases 3-6

| Capability | Why it is needed |
|---|---|
| Get current entitlements/license | Distinguish new, renew, and combined motions from authoritative Leonardo state |
| Add product/entitlement | Support combined new-product cases without creating a duplicate account |
| Renew/update license | Support renewal cases with explicit before/after state and independent verification |
| Update account settings/modules | Apply an approved change set atomically and report every changed field |
| Operator-account catalog/binding | Select only an approved operator and detect collisions or unsupported assignments |
| Primary-user create/update/invite | Define whether user provisioning is atomic with account creation and how MFA-required state is verified |
| Development fixture disable/cleanup | Owner-controlled, auditable cleanup; automation must not delete automatically |

### P0 cross-cutting requirements

- Hard Development/production separation by hostname, token audience, tenant,
  credentials, and authorization policy.
- Stable OpenAPI and JSON Schema artifacts with rejection of unknown fields.
- Named scopes such as search, validate, create, read, operation-status, and
  audit-read; no production, delete, or broad administrator scope initially.
- Same idempotency key plus same hash returns the original result; the same key
  with a different hash returns a conflict.
- Typed errors for validation, duplicate, unauthorized, forbidden, conflict,
  throttled, unavailable, and uncertain operation states.
- Documented rate limits, retry rules, idempotency retention, read-after-write
  consistency, maximum convergence time, and signed callbacks if asynchronous.
- Audit coverage, owner/support contacts, SLA, versioning, change notice,
  compatibility, and deprecation policy.

Historical Donatello paths are not Leonardo contracts and must not be copied
into Workato or called until the Leonardo owner explicitly approves them.

## Local implementation added

`phase2_leonardo` now contains strict request/result JSON Schemas and a
dependency-free Python policy for exact-origin enforcement, manifest hashing,
idempotency, approval expiry and separation, duplicate/prior-attempt gates,
MFA and toggle dependencies, redacted evidence, and uncertain-result handling.

The CO-0697 intake file is deliberately blocked and is not a runnable manifest.
The next local step is to complete the owner-approved Cases 1-6 mapping and add
mock form fixtures. The next external step is owner decisions and a read-only
CO-0697/DealHub validation—not a Leonardo create.

## Workato router hardening completed on 2026-08-24

The Development recipe `CO Source Review and Onboarding Router` was saved as
Workato version 18 and directly verified as `Inactive` after reload. No recipe
test, Salesforce write, OPA call, or Leonardo action was performed during this
change.

- Added a pre-query fail-closed gate requiring `co_number` to match
  `^CO-[0-9]+$`; malformed values stop the job with an error before Salesforce.
- Reduced the Customer Onboarding SOQL projection to routing and normalized
  validation evidence, omitting comments, rejection logs, personal names, and
  email addresses.
- Added `SystemModstamp` to the query and output schema for the immutable source
  revision.
- Changed the Salesforce search limit from 1 to 2 so the existing
  `Total size does not equal 1` gate can detect both zero and duplicate records.
- Changed exact-one violations to fail the job with an explicit zero-or-many
  reason.
- Preserved the downstream Account ID mapping for the DealHub subscription
  search and removed the Owner ID from its returned fields.
- Enabled Workato job-report data masking on the Customer Onboarding lookup,
  DealHub subscription lookup, and Ruby classification output.

## CO-0697 read-only Workato test completed on 2026-08-24

At 16:29:52 PDT, the inactive Development recipe was run through Workato Test
mode using recipe version 18 and the minimal trigger payload containing only
the test identifier. Workato returned HTTP 200 and reported the job as
`Successful`.

- The `co_number` format condition was not met, so the malformed-input stop
  branch was not taken.
- The exact-one condition (`Total size does not equal 1`) was not met, proving
  that the Salesforce lookup returned exactly one Customer Onboarding record.
- The DealHub subscription search and Ruby classification steps both executed.
- Customer Onboarding, subscription, and classification job data remained
  masked. The current test report therefore does not expose the source record
  or revision; the previously established scoped Salesforce evidence remains
  the source-revision evidence for this run.
- The current router ends after Ruby classification and contains no Phase 1
  validator/OPA call. This successful router job does not establish the
  required `proceed=false` validator decision.
- No Salesforce write, OPA mutation, Leonardo action, Donatello action, or
  BackOffice action was present or performed.

The Workato webhook address was accidentally pasted into the controlling chat
during manual clipboard troubleshooting. The address is intentionally omitted
from this repository and must be treated as exposed and rotated before any
future operational use.

Next gated action: map the router's normalized, masked evidence into the
separate Phase 1 validator path and run a read-only validator test that must
return `proceed=false`. Leonardo creation remains separately blocked and
requires explicit notification and approval immediately before any form fill
or submission.

## Read-only Workato mapping inspection on 2026-08-24

Post-test inspection of version 18 established that the DealHub action searches
by Account, has a limit of 25, and retrieves subscription/product identity,
status, start/end dates, and record ID. It does not retrieve a quantity field
or a subscription revision field such as `SystemModstamp`.

The current Ruby classifier:

- groups candidate subscriptions by Surface, Credential Exposure, and Core;
- ranks `active`, then `pending`, then `trial`, and selects the first candidate;
- does not reject equal-rank duplicates or otherwise establish an unambiguous
  selected subscription;
- emits start, end, status, subscription name, and record ID for each group;
- populates its `license_type` value from the first nonempty product full name,
  product family, or product sub-family; and
- does not emit the validator's required canonical `product` or `quantity`
  fields.

Therefore, version 18 output cannot be mapped directly into the seven-field
Phase 1 validator contract without inventing values or changing semantics. The
router-to-validator connection remains blocked until product and license-type
semantics, quantity source, duplicate selection, subscription revision
binding, and normalized contract evidence are explicitly resolved.

Workato's design-time Recipe Data pane exposed prior Salesforce sample values
while inspecting the masked steps. No such values are reproduced in this
repository. Treat design-time sample data as restricted, clear or replace it
with synthetic samples through an approved Workato procedure, and do not rely
on job-report masking alone.

## Post-test security and contract hardening on 2026-08-24

The webhook address disclosed during clipboard troubleshooting was rotated by
changing the Development trigger to a new high-entropy event URI. Workato
version 20 contains the clean replacement. Version 19 is a superseded
intermediate event-label edit and must not be restored. A guarded probe against
the disclosed old address returned HTTP 404 while the new Test listener was
waiting, proving that the old route no longer delivered to this recipe. The
listener was stopped and the recipe was directly verified `Inactive`.

The local Phase 1 validator now also exposes a backward-compatible strict
endpoint at `POST /v1/validate-bound`. It requires the CO number, Salesforce
record ID, timezone-bearing source revision, canonical request UUID, policy
version, and exactly seven normalized fields under each evidence source. It:

- rejects unknown fields, duplicate JSON keys, malformed dates, invalid
  quantities, invalid identifiers, and policy-version mismatches;
- binds the result to the source identity and canonical evidence with SHA-256
  `evidence_hash` and `result_hash` values;
- returns only mismatch field names and types, not the underlying values; and
- always keeps `proceed=false`.

The existing `/v1/validate` behavior remains available for backward
compatibility. The complete local validator suite passes 25 tests, including
localhost HTTP-route coverage and `Cache-Control: no-store` verification. The
deployment and Workato validation completed later on 2026-08-24 are recorded
below.

Read-only inspection of the DealHub Salesforce object confirmed that candidate
fields exist for `Quantity`, `System Modstamp`, `Product Code`, `Pricing Type`,
and `SVA Type or Service Package`. No fields were selected or changed during
that inspection. The next Workato version should add only owner-approved fields
and must, at minimum:

1. carry the selected subscription ID and `System Modstamp`;
2. map the authoritative quantity field without conversion from product text;
3. resolve product and license-type semantics before mapping either field;
4. reject zero, multiple, or equal-rank subscription candidates;
5. emit all seven validator fields plus source-control metadata from a masked
   normalization step;
6. call only the fixed `/v1/validate-bound` path through the dedicated OPA
   connection after deployment approval; and
7. re-read the CO and selected subscription revisions before accepting the
   terminal `proceed=false` result.

## OPA deployment and one strict Workato test on 2026-08-24

A fresh vSphere rollback snapshot named
`workato-opa-01 phase2-bound-validator 08-24-26` was created before deployment.
The pre-deployment VM baseline established that the Workato agent and notes
validator on loopback port 8788 were healthy, while the canonical validator on
loopback port 8787 was not running. The existing live validator files matched
the documented 2026-08-21 hashes.

A mode-0700 operation area was created at
`/home/workato/phase1-operations/20260824-phase2-bound-validator`. The previous
`server.py` and `test_server.py` were copied into its `backup` directory, and a
complete package copy was created under `staging`. Only the following staged
files were installed into the live package:

| File | Deployed SHA-256 |
|---|---|
| `server.py` | `ff3b152339b34f66403c504b83211483e7444d612a59829a47066906dcd909a2` |
| `test_server.py` | `53195d070ceac20771adca2baff8fc35484a7b65cafc907fb303ad16fa6d59a6` |

The complete staged suite passed 25 tests. The applicable live package suite
passed 20 tests after installation. The port 8787 validator was then started
from `/home/workato` using `/home/workato/phase1-venv/bin/python -m
phase1_validator.server`; port 8788 was not restarted. Verification showed:

- ports 8787 and 8788 listening only on `127.0.0.1`;
- both health endpoints returning policy `phase1-shadow-v1`;
- the 8788 capability remaining `rejection_note`; and
- `workato-agent.service` remaining `active`.

A direct synthetic localhost request to `/v1/validate-bound` returned a
redacted product mismatch, `manual_review_required`, `proceed=false`, and both
binding hashes. It did not reflect submitted source values in the mismatch.

Exactly one authorized Test was then run in the inactive Workato Development
recipe `OPA Connectivity Test - Phase 1 Validator` (recipe `74743605`), using
recipe version 4 at 17:13:06 PDT. The recipe was updated to call only
`/v1/validate-bound` with synthetic evidence. The job completed `Successful`
with HTTP 200 and returned `manual_review_required`, `proceed=false`, one
redacted product mismatch, `evidence_hash`, and `result_hash`. The recipe was
directly verified `Inactive` afterward with four successful jobs and zero
failed jobs.

This strict Workato test did not use CO-0697 or other customer-derived values.
The main CO router still ends after classification and is not yet mapped to the
strict validator. No Leonardo, Donatello, BackOffice, Salesforce-write, or
production action was performed or authorized.
