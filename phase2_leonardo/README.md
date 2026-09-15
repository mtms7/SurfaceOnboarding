# Phase 2 Leonardo local safety package

This package defines local-only contracts and safety checks for an attended
Leonardo Development bridge. It contains no HTTP client, browser dependency,
credential handling, Salesforce write, Workato action, or Leonardo operation.

The only permitted request operations are `validate_only`,
`read_only_preflight`, `fill_and_pause`, and `read_only_verify`. There is no
unattended `create` operation. `fill_and_pause` means a separately approved
runner may fill the Development form and must stop before `Confirm`; the
authorized human performs the one approved confirmation.

## Hash and idempotency contract

`manifest_sha256` is the reviewed-intent SHA-256 over the contract version,
target, source, routing, account, user, settings, attack modules, and license.
It excludes correlation/idempotency and the complete approval attestation, so
the approval can bind to a stable intent hash without a circular dependency.
The idempotency key is SHA-256 over:

```text
contract_version|target_environment|sf_record_id|source_revision|engine_value|policy_version|approval_revision|operation|manifest_sha256
```

Any reviewed-field change therefore requires a new manifest hash and approval.
The local policy never authorizes an external request by itself; the approved
mapping, Workato approval attestation, preflight attestation, atomic lock, and
authenticated runner transport remain separate trusted gates.

## Local tests

From the repository root, use the bundled Python runtime or any Python 3.12+
runtime:

```powershell
python -m unittest discover -v
```

The blocked-intake regression fixture is wholly synthetic and is not an
execution manifest. It deliberately contains no customer-derived identifier,
domain, contact, comment, attachment, credential, or raw payload.

## Pure-local attended phase planner

`attended_phases.py` is an advisory state planner for the documented sequence:
source re-read, duplicate preflight, `fill_and_pause`, external human
confirmation, readback, and separately approved writeback. It accepts only
redacted policy constants, booleans, counts, and attestation states; it has no
customer-data or authentication fields.

Every returned phase has `local_callable=false`, and the overall plan always
has `proceed=false` and `external_action_callable=false`. The v5 partial
mapping is unapproved by definition and always produces a blocked plan. A
forged v5 state that supplies `proceed=true`,
`leonardo_request_allowed=true`, or zero unapproved groups is rejected as a v5
invariant violation; it cannot make fill, confirmation, readback, or writeback
ready.

The planner does not contain or call a browser, HTTP client, API, create,
Confirm, readback, writeback, filesystem-write, credential, token, cookie, or
MFA capability. A future status describing readiness for a separately
authorized adapter would remain advisory and would not grant authority or make
that adapter callable from this package.

The current tests include seven focused attended-phase regressions, and the
complete local suite passes 78 tests.

## OPA Case 3 preflight service

`server_intake.py` adds a loopback-only service on `127.0.0.1:8789` for two
guarded operations:

- `POST /v1/phase2/prepare-case3` normalizes one Workato-masked Case 3 intake,
  checks the selected Surface/Core IDs, revisions, opportunity, terms,
  quantities, approved product mapping, domain entitlements, and pinned Public
  Suffix List classification, then returns an unapproved draft.
- `POST /v1/phase2/validate-manifest` applies the existing manifest,
  idempotency, duplicate, approval, and single-flight policy. It identifies
  blockers but cannot authorize or perform a Leonardo operation.

Both operations have no persistence, suppress HTTP request logging, return
`Cache-Control: no-store`, reject duplicate JSON keys and unknown fields, and
never return `proceed=true`. Workato must enable data masking for the trigger,
request, and response because the normalized draft contains restricted account
and user values.

Case 3 intake contract v2 derives `license.number_of_domains` from the count of
distinct registrable root domains across primary, alternate, and CE inputs;
subdomains are excluded and CE duplicates are counted once. For a Pentera
organization mailbox, the primary-user email is converted to a customer alias:
one or two normalized company-name words are concatenated, while names with
more than two words use their initials. Operator Account remains an explicit
nullable field; MFA remains mandatory.

The service contains no browser or HTTP client. Playwright, Chromium, browser
profiles, cookies, passwords, MFA codes, and tokens must not be installed or
stored on the OPA host.

## Partial mapping authority (v5)

`surface-case3-partial-owner-mapping-2026-08-31-v5` is a validate-only
hardening revision. It preserves only four owner-recorded normalization rules:
combined-account/ASCII company naming, deterministic Pentera plus-addressing,
nullable Operator Account, and distinct registrable-root domain counting. MFA
is a separate non-negotiable security invariant.

All Leonardo account enums, settings, module behavior, license semantics,
add-on taxonomy, collision/duplicate rules, approval, idempotency, submission,
readback, and writeback mappings remain unapproved. The preflight response is
`surface-case3-preflight-v2`, reports
`candidate_mapping_requires_owner_review`, and always returns both
`proceed=false` and `leonardo_request_allowed=false`. No response from this
service authorizes an external action.
