# Phase 2 CO-0717 Leonardo Development mapping and filling decision memo

Date: 2026-08-31  
Target: Leonardo Development only  
Status: **validate-only preparation is available; mapping approval, duplicate preflight, fill, submit, and verification remain blocked**

## Decision

CO-0717 may continue through local validation and inactive Workato recipe
hardening. It may not yet be released to a Leonardo form fill or account
creation. The deployed v5 policy intentionally returns
`proceed=false`, `leonardo_request_allowed=false`, and
`execution_mapping_status=unapproved`.

The preferred production-capable path is an official Leonardo API with a
least-privilege service identity. An attended browser path may be used only as
a temporary Leonardo Development exception, after the exact manifest and
operation are approved, with the operator handling authentication and MFA and
the automation stopping before submission.

## Verified facts

- The v5 Case 3 service is deployed as a loopback-only, validate-only
  preflight. The retained deployment and independent verification establish
  the expected v5 health tuple, listener, active Workato agent, and matching
  allowlisted file hashes.
- The Workato Development recipe remains inactive. Its v2 request guards and
  authorization-and-decision response guard were saved and reload-verified
  without selecting **Test** or **Start recipe**.
- Workato's two lowercase 64-hex hash checks, explicit missing-hash checks,
  six-item unresolved-group count, and all six required-group membership
  checks were saved and reload-verified in the same masked failed-Stop branch.
  The recipe remained inactive and neither **Test** nor **Start recipe** was
  selected.
- The OPA response is a candidate draft, not execution authority. It cannot
  release any operation other than local `validate_only`.
- The Primary User relationship was corrected through an authorized
  single-field Salesforce update. An independent read-only reconciliation
  confirmed exactly one CO, exactly one eligible intended Contact, and an
  exact relationship match. The current revision must still be rebound during
  the single masked caller test.
- `leonardo-dev-create-v2` defines approval-bound operations for validation,
  read-only preflight, `fill_and_pause`, and read-only verification. It does
  not define or authorize a submit/create operation.
- A verified result requires an exact manifest binding, a stable account UUID,
  complete field and setting readback, and a trusted readback method. Every
  non-verified result forbids automatic retry.
- The current repository contains no supported Leonardo API client and no
  browser execution client. The preflight contracts alone do not create either
  capability.
- `phase2_leonardo/attended_phases.py` now models the guarded phase sequence
  using redacted evidence only. Every phase is non-callable, top-level
  `proceed` and `external_action_callable` are always false, current v5 is
  always blocked, and a forged v5 authorization shape is rejected as an
  invariant violation. Seven focused tests and the complete 78-test suite pass.

## Owner-recorded normalization scope

Only these four business rules are recorded in v5's partial mapping authority:

1. Normalize the combined company name to an ASCII-safe form and do not append
   a Credential-Exposure-only suffix to a combined Surface and Credential
   Exposure account.
2. Derive a deterministic customer-specific plus-address alias from the
   normalized combined-account name only when the source organization mailbox
   is an eligible Pentera mailbox; preserve non-Pentera addresses and reject an
   already plus-addressed source.
3. Permit Operator Account to be null; its absence does not block a
   validate-only draft.
4. Derive Number of domains from distinct registrable roots across approved
   domain inputs, counting duplicates once and keeping subdomain licensing
   separate.

MFA remains mandatory for a user-based flow. These rules do not approve any
Leonardo enum, setting, module, entitlement, collision decision, form fill, or
submission.

## Unapproved groups and required decisions

| Contract group | Decision still required |
| --- | --- |
| `account_enums_and_collision_rules` | Confirm account and country enums, authoritative domain sources, normalization edge cases, and collision behavior for names, domains, users, networks, and operator accounts. |
| `settings` | Approve every Leonardo setting value and dependency; do not inherit visible UI defaults. |
| `attack_modules` | Approve Credential Exposure entitlement, enabled modules, interval, entitled email-domain count, and the observed non-editable module behavior. |
| `license_semantics` | Approve license enum, provisioning flags, authoritative quantities, distinct domain/subdomain counts, and subscription-date sources. |
| `addon_taxonomy` | Approve exact supported product codes or names and deterministic Leonardo effects; unknown current products must reject. |
| `duplicate_and_readback_rules` | Define duplicate keys, collision resolution, stable UUID readback, complete field/toggle verification, uncertainty reconciliation, and no-retry behavior. |

Candidate values present in a draft schema, older local guidance, or current UI
defaults are assumptions until the named owners approve a versioned mapping.
They must not be filled into Leonardo by inference.

## Required owners and approvals

| Owner | Required recorded decision |
| --- | --- |
| Commercial/DealHub owner | Authoritative subscription identities, revisions, quantities, dates, add-on taxonomy, and one-bundle rule. |
| CSE/Surface process owner | Current Guru-backed Case 3 field, setting, module, license, and exception mapping. |
| Leonardo Product/Engineering | Supported API and service identity; form enums and semantics; duplicate and stable readback contract; or explicit approval of the temporary attended-UI exception. |
| SecOps | Least privilege, MFA, secret storage, browser-runner placement, network controls, audit masking, evidence retention, and incident handling. |
| Workato owner | Exact caller assertions, atomic single-flight idempotency, masked audit output, approval enforcement, and stopped-recipe verification. |
| Salesforce owner | Exact Primary User relationship and revision binding; later UUID/status destination fields and separate writeback authorization. |
| Independent operation approver | At-most-60-minute, manifest-hash-bound approval for each read-only preflight or `fill_and_pause`; requester and approver must be separate. |

Approval of a mapping does not approve an external operation. Approval of a
read-only preflight does not approve a fill. Approval of `fill_and_pause` does
not approve submit/create or Salesforce writeback.

## Exact attended phases

1. **Source freeze:** re-read exactly one CO, its exact Primary User Contact,
   the selected subscriptions, and their revisions. Stop on any ambiguity or
   revision change.
2. **Masked Workato validation:** after fresh one-run approval, transmit only
   the single Development test to the loopback v5 preflight. Assert the full v2
   response, including both hash formats and the exact six unapproved groups.
   Stop without retry and return the recipe to inactive.
3. **Mapping decision:** owners resolve all six groups. Build a strict new
   mapping revision, re-run synthetic tests, generate the complete manifest,
   and compute its canonical hash and deterministic idempotency key.
4. **Route decision:** prefer the supported API/service-account route. If none
   is available, Leonardo Product/Engineering and SecOps must explicitly
   approve the temporary attended browser route and its runner controls.
5. **Read-only duplicate preflight:** under its own fresh approval, search the
   approved collision keys without opening or filling Add Account. Any match,
   uncertain response, authorization failure, or UI/schema drift blocks the
   run.
6. **Attended `fill_and_pause`:** bind approval to the exact manifest hash;
   acquire an atomic single-flight lock; let the human operator authenticate
   and complete MFA; fill only approved fields; mask evidence; then pause for
   human comparison. Do not select Confirm or otherwise submit.
7. **Submit/create:** blocked by the current contract. Before this phase can
   exist, add and independently review an explicit submit operation, owner and
   SecOps approvals, a confirmation ceremony, uncertain-submit reconciliation,
   and rollback/incident procedures.
8. **Read-after-write verification:** if a future separately approved submit
   succeeds, re-read through a supported method, bind the stable UUID to the
   exact manifest, verify every field and setting, and emit only the redacted
   result contract. Do not retry an uncertain result.
9. **Salesforce writeback:** separately authorize and verify the exact UUID and
   status destinations. This phase is not implied by Leonardo verification.

## Stop conditions

Stop and preserve the current blocked state for missing, stale, conflicting, or
expired approval; requester self-approval; source or Contact revision drift;
zero or multiple source records or Contacts; an unknown product or add-on;
missing quantity or mismatched term; ambiguous normalization; any duplicate or
collision; an idempotency lock conflict; authentication, MFA, authorization,
WAF, CAPTCHA, or network failure; a production origin; inactive local preflight
or unexpected listener; schema, enum, or UI drift; a missing or malformed hash;
an unexpected unapproved-group set; an uncertain submit; missing UUID; or
incomplete readback. Do not retry an external mutation automatically.

## Safe completion boundary for 2026-08-31

Work may safely complete today on inactive Workato structure, local contracts,
synthetic tests, the versioned mapping decision packet, masked evidence design,
and a non-executing attended fill plan or runner scaffold. The inactive Workato
v2 request and response guard structure and the Primary User correction/readback
are complete. The first approved caller attempt was blocked before job creation
because Step 15 treated the engine literal as a formula; no payload was sent.
The separately authorized literal-mode correction and save/reload verification
are now complete; the recipe remained inactive without Test or Start. The next
safe gate is another immediate source and subscription reread followed by new
one-run approval. Supplemental OPA process,
listener, synthetic-response, agent-state, and deployed-hash verification has
completed cleanly.

Real Leonardo duplicate lookup, field filling, Confirm/create, account
verification, Salesforce writeback, unattended execution, and all production
activity remain outside today's authorized boundary until their preceding
owners and gates are satisfied.
