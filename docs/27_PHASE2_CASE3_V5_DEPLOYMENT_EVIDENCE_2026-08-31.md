# Phase 2 Case 3 v5 deployment evidence

Date: 2026-08-31  
Status: **v5 deployed and independently reverified; first Workato caller attempt blocked before job creation by a recipe validation error**  
Target: Leonardo Development validate-only preparation

## Directly observed outcome

The owner-provided deployment console excerpt showed:

- deployment ID `20260831-171029` and a matching successful wrapper completion;
- one focused Phase 2 suite completing 33 tests with `OK`;
- health `status=ok`, capability `phase2_case3_preflight`, and policy
  `surface-case3-partial-owner-mapping-2026-08-31-v5`;
- a listener on `127.0.0.1:8789`; and
- the Workato agent state `active`.

This verifies that v5 replaced the prior health-visible policy and that the
guarded deployment wrapper reached its successful terminal path. It does not
authorize a Workato test, Leonardo action, or production activity.

## Independent read-only verification

The owner then ran `scripts/verify_phase2_intake.ps1`. The command returned to
the local prompt without error after verifying the exact v5 health tuple, the
expected loopback listener, a readable PID whose command contains the managed
Phase 2 server, and an active Workato agent. It also returned SHA-256 values for
`intake.py`, `policy.py`, `server_intake.py`, and the public suffix list. All
four values matched the corresponding local allowlisted files exactly.

The verification did not restart the service, transmit a CO payload, or
change an external system.

## Wrapper-enforced but not fully visible in the retained excerpt

The current deployment script is fail-fast and performs allowlisted SHA-256
verification, a staged 33-test run, controlled replacement with rollback, a
live 33-test run, health/listener/agent checks, and deployed-hash evidence
before printing completion. The retained screenshot did not show every
checksum line or both test summaries. Those checks are therefore treated as
strongly established by wrapper semantics, but not independently observed.

## Synthetic fixture hardening

The package replaced the restricted derived regression fixture with
`synthetic-blocked-intake-status.json`. The fixture declares a fully synthetic
origin, uses synthetic identifiers and values, remains blocked, and sets both
`proceed` and `leonardo_request_allowed` to false. Focused tests validate its
exact shape and ensure it cannot release a resealed manifest.

## Workato state immediately after deployment

The Workato Development recipe remains inactive. No **Test** or **Start**
action occurred. The following masked, fail-closed request guards have been
saved:

- request contract `surface-case3-intake-v2`;
- exact one-record test scope `CO-0717`;
- target environment `leonardo-development`;
- routing engine `case_3_combined_baseline`; and
- `special_requirements=false`.

At this deployment checkpoint, exact source/trigger binding, zero-add-on test
scope, the full post-response assertion set, and the authorized test record's
Primary User correction were pending. A later same-day structural checkpoint,
recorded in `docs/26_PHASE2_CASE3_V5_VALIDATE_ONLY_HARDENING_2026-08-31.md`,
saved and reload-verified exact source/trigger binding, zero-add-on scope, and
the complete 22-condition response guard. At that checkpoint, Primary User
correction and the v5 caller test remained pending; no caller job had
transmitted a CO payload to v5. The later correction and exact readback are
recorded below; at that point the caller test still remained pending. The
subsequent blocked pre-execution attempt is also recorded below.

## Current next safe gate

The later supplemental verifier completed cleanly and directly established one
managed process under the expected owner, one IPv4-loopback-only TCP listener
owned by that process, no UDP listener, an active Workato agent, an exact
synthetic v2 fail-closed response without body retention, and four deployed
file hashes matching the local allowlisted snapshot.

The intended Primary User relationship was later corrected through an
authorized single-field update and independently reread as an exact match.
The separately authorized Step 15 literal-mode correction is also complete:
save/reload showed the exact Text literal, no prior formula error, and an
inactive recipe without Test or Start. Re-read the source and subscription
revisions immediately before execution, then request new action-time approval
for exactly one Development caller test. The first approved attempt created no
job and sent no payload because Workato rejected the former Formula-mode value
before execution.

## Boundaries

No Salesforce or DealHub write, Leonardo lookup or form submission,
Donatello/BackOffice action, production action, credential persistence, or
customer-data evidence capture occurred in this checkpoint.
