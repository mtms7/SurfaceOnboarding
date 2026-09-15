# Phase 2 Case 3 v5 validate-only hardening

Date: 2026-08-31  
Status: **v5 deployed and supplementally verified; Primary User correction/readback and Step 15 literal correction complete; caller still inactive**  
Target: Leonardo Development preparation only

## Verified local outcome

- The Case 3 intake request remains `surface-case3-intake-v2`.
- The preflight response is now `surface-case3-preflight-v2`.
- Mapping policy `surface-case3-partial-owner-mapping-2026-08-31-v5` records
  only the four owner-provided normalization rules documented in the approval
  packet.
- `EXECUTION_APPROVED_MAPPING_POLICIES` is empty. Every operation except local
  `validate_only` retains `routing:mapping_not_approved`.
- The response decision is `candidate_mapping_requires_owner_review` and both
  `proceed` and `leonardo_request_allowed` are always false.
- The response reports the unresolved field groups without returning an
  execution approval.
- Customer-derived company/domain examples were removed from executable test
  fixtures and replaced with synthetic RFC-style values.
- The complete local suite passed: 78 tests, including 42 Phase 2 tests after
  adding the pure-local attended-phase safety model and regressions.

## Read-only live checks on 2026-08-31

- The Workato Development recipe remained inactive and displayed **Start
  recipe**.
- All data-bearing source/classifier/OPA actions remained masked.
- The whole Step 9 OPA request object remained bound to the Step 10 raw JSON
  body.
- Non-2xx responses were not configured as success, and no Salesforce,
  DealHub, Leonardo, Donatello, BackOffice, or production write action was
  visible.
- CO-0717 remained in stage `New` with its domain and subscription sections
  present, but the visible Primary User relationship did not match the intended
  owner Contact. The test therefore remained blocked.

No customer values, Contact values, Salesforce/DealHub identifiers, Workato
capability URL, internal address, credential, token, cookie, MFA code, request,
or response is retained here.

## Verified deployment checkpoint on 2026-08-31

- Deployment `20260831-171029` reached the guarded wrapper's successful
  completion path.
- The retained console excerpt directly showed one focused 33-test Phase 2
  run passing, exact v5 health, a `127.0.0.1:8789` listener, and an active
  Workato agent.
- The fail-fast wrapper enforces package checksums and staged/live test gates
  before printing completion. Those earlier lines were not all visible in the
  retained excerpt, so they are recorded as wrapper-enforced rather than
  independently observed.
- The deployment replaced the former restricted derived regression fixture
  with `synthetic-blocked-intake-status.json`, whose exact synthetic markers
  and fail-closed outcomes are covered by the focused policy tests.

The detailed evidence and its limits are recorded in
`docs/27_PHASE2_CASE3_V5_DEPLOYMENT_EVIDENCE_2026-08-31.md`.

## Authorized live structural checkpoint on 2026-08-31

- The inactive Workato Development recipe was edited and saved without using
  **Test** or **Start recipe**.
- Four pre-HTTP invalid-condition guards now cover the v2 request contract,
  exact `CO-0717` test scope, Leonardo Development target, Case 3 engine, and
  `special_requirements=false`.
- The newly added target, engine, and special-requirements IF/Stop pairs are
  masked and use only static failure categories.
- Save/reload verification confirmed the target, engine, and
  special-requirements conditions persisted. No recipe job or external data
  query was initiated by these edits.
- Exit verification showed **Start recipe** and no **Stop recipe** control, so
  the recipe remained inactive after the save.
- Exact source/trigger binding, add-on scope, and the complete post-response
  assertion set are not yet implemented; this checkpoint does not make the
  recipe test-ready.

## Verified authorization-and-decision gate checkpoint on 2026-08-31

- Save/reload verification confirmed exact source/trigger binding and a
  zero-add-on scope guard before the OPA HTTP action.
- The OPA response schema now includes `mapping_authority`,
  `execution_mapping_status`, and `unapproved_field_groups` in addition to the
  previously mapped v2 response fields.
- One masked OR guard rejects a contract-version mismatch, non-Development
  target, decision other than owner review, unexpected required action, v5
  policy mismatch, mapping-authority mismatch, execution status other than
  `unapproved`, either authorization boolean being true, or either
  authorization boolean being absent.
- The mismatch branch ends in a masked failed Stop with the static category
  `phase2_case3_response_contract_guard_failed`; it does not include a customer
  datapill and does not retry.
- The two temporary empty action placeholders were removed. Save/reload
  verification confirmed the source guard, zero-add-on guard, masked OPA HTTP
  action, authorization-and-decision response guard, and final failed Stop all
  persisted.
- **Test** and **Start** were not used, so this checkpoint initiated no recipe
  job or external query.

## Verified response evidence-shape checkpoint on 2026-08-31

- The existing masked response mismatch branch was extended with two lowercase
  64-hex format checks and two explicit missing-value checks for
  `public_suffix_list_sha256` and `evidence_hash`.
- The same branch now rejects an `unapproved_field_groups` count other than six
  and the absence of any required value:
  `account_enums_and_collision_rules`, `settings`, `attack_modules`,
  `license_semantics`, `addon_taxonomy`, or
  `duplicate_and_readback_rules`.
- Count six plus containment of all six required values provides an exact-set
  check independent of order. A missing, extra, or duplicated entry fails the
  branch.
- The failure branch remains masked and ends in the existing static failed Stop
  category. It contains no response or customer value in the error text.
- Save/reload verification confirmed all 22 response conditions persisted.
  **Test** and **Start** were not used, so no recipe job or external query was
  initiated.

## First authorized caller attempt

The immediate read-only source gate passed with exactly one CO, the intended
Primary User relationship, exact active Surface and Core subscriptions, the
expected common term, bound revisions, three Surface domains, one Credential
Exposure email domain, and zero candidate add-ons. The Workato recipe was
inactive and masked. **Repeat job** was clicked once, but Workato refused to
create a job because Step 15's `case_3_combined_baseline` comparison value was
in Formula mode and reported **Formula has errors**. No payload was transmitted
to OPA and the recipe remained inactive. See
`docs/31_PHASE2_CO-0717_CALLER_ATTEMPT_BLOCKED_BY_RECIPE_VALIDATION_2026-08-31.md`.

## Required before a new Workato test

1. Preserve the completed Step 15 correction: value mode Text, exact literal
   `case_3_combined_baseline`, Data field Formula datapill unchanged, and recipe
   inactive. See `docs/32_PHASE2_WORKATO_STEP15_LITERAL_CORRECTION_2026-08-31.md`.
2. Keep every other saved, masked request and response gate unchanged unless a
   separately reviewed contract revision requires an update.
3. Re-read the exact CO, Primary User, and related subscriptions immediately
   before the run and stop on any revision drift or ambiguity.
4. Obtain new action-time confirmation for exactly one masked Development
   transmission to the loopback preflight service.

## Verified Primary User correction and readback

- Exactly one CO-0717 record and exactly one eligible intended organization
  Contact were found.
- Before the authorized correction, the relationship was populated but did
  not point to the intended Contact.
- The authorized update changed only `Primary_User__c`.
- A separate read-only reconciliation found exactly one CO and one eligible
  intended Contact and confirmed the saved relationship matched exactly.
- No identifier, email address, prior Contact value, or raw Salesforce payload
  is retained in this record.

## Remaining boundaries

No Leonardo duplicate lookup, form fill, confirmation, account creation,
read-after-write verification, Salesforce writeback, production action, or
unattended run is authorized. Any failed or uncertain Workato test must stop
without retry and requires new action-time approval before another
transmission.
