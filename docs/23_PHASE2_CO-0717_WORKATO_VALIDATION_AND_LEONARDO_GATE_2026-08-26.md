# Phase 2 CO-0717 Workato validation and Leonardo gate

Date: 2026-08-26  
Updated: 2026-08-27  
Target: Leonardo Development only  
Status: **guarded DealHub selector retest passed; Leonardo mapping remains blocked**

## Outcome

CO-0717 was submitted once to the inactive Workato Development router for the
authorized masked, read-only validation. Recipe version 21 found exactly one CO
and queried the related DealHub subscriptions, then stopped at the conservative
best-rank ambiguity gate. No classification result, OPA call, Leonardo action,
Salesforce write, DealHub write, or production action occurred.

The negative result is correct for the current version, but CO-0717 provides
better evidence for the next selector design than CO-0701: its three active
rows share one opportunity and one term. They appear to form a commercial
bundle rather than unrelated top-rank records. The router must not release the
bundle yet because product-group semantics, quantities, identity binding, and
Case 3 Leonardo mapping are still unresolved.

## Verified Salesforce intake

- Exactly one CO-0717 record exists.
- Current source revision: `2026-08-27T01:41:01.000+0000`.
- Product: Surface and Credential Exposure.
- Type: New Product Onboarding.
- Approval: Approved.
- Stage: New.
- Submission date: 2026-07-08.
- Surface Account ID and Account UUID are both empty.

The initial read showed `Onboarding Completed`. The owner then corrected the
stage to `New`, and a fresh Salesforce read verified the revision above. This
resolves the earlier completed-stage mismatch but does not resolve DealHub
selection or Leonardo mapping.

Customer name, contacts, domains, comments, account ID, opportunity ID, and
raw subscription identifiers are intentionally omitted from this artifact.

## Verified DealHub normalization

The minimum-field account query returned 12 rows: nine expired historical rows
and three active rows. All three active rows are New Pricing, share one
opportunity, and share a 36-month term from 2026-07-01 through 2029-06-30:

| Active product class | Available mapping evidence | Blocking issue |
| --- | --- | --- |
| Remaining usage value | Quantity is present. | It is not an obvious Leonardo license; the exclusion or commercial-treatment rule is not approved. |
| Surface Go, 500 subdomains | Surface product and subdomain tier are explicit. | The dedicated quantity field is null; owner must confirm whether the tier text or another source is authoritative. |
| Core Plus Commercial, 500 endpoints, SVA Essentials | Core Plus and SVA package are explicit. | Quantity is null, and the exact Credential Exposure/Leonardo module interpretation is not approved. |

The shared opportunity and term are strong evidence that the rows belong to one
bundle. They are not sufficient authority to invent product or license values.

## Workato Development test

- Recipe: `CO Source Review and Onboarding Router` (`73688657`).
- Version: 21.
- Job: `j-AbMcKpGX-waeoLn-CD`.
- Started: 18:32:11 PDT.
- CO format and exact-one-record gates passed.
- Masked Salesforce CO and DealHub searches executed.
- Step 9 stopped with masked category
  `ambiguous_subscription_selection` before classification completed.
- Recipe was directly verified Inactive after the test, with 11 successful and
  two intentionally failed jobs.

The Development webhook capability URL was displayed during guarded trigger
inspection and was not recorded here. It was rotated before the retry and
invalidated again immediately afterward.

## Stage-corrected secured retry

After the `New` stage and new Salesforce revision were verified, the owner
confirmed one retry. The Development trigger capability URL was rotated, and
only `CO-0717` was sent to the new Test listener:

- job: `j-AbMcYsXm-8AtzDs-CD`;
- recipe version: 22;
- started: 18:46:28 PDT;
- masked Salesforce and DealHub reads completed;
- step 9 again stopped with
  `ambiguous_subscription_selection` before classification; and
- no source-system, OPA, Leonardo, or production write occurred.

The stage change did not alter the DealHub evidence: the current gate operates
on the three equally ranked active rows. After the retry, the test endpoint was
invalidated by a second rotation, the trigger event name was verified clean,
the final recipe configuration was saved as version 23, no capability URL was
persisted, and the recipe was directly verified Inactive. Job totals were 11
successful and three intentionally failed.

## Verified facts versus interpretation

Verified:

- there is one approved CO;
- the CO stage is now `New`, approval is `Approved`, and both destination IDs
  are empty;
- three active rows share one opportunity and term;
- nine other rows are expired;
- two active license rows have null quantities; and
- version 21 fails closed on the active top-rank tie.

Interpretation requiring owner approval:

- the active rows represent the intended Case 3 combined bundle;
- remaining usage value should be excluded from Leonardo license mapping;
- Surface Go tier text authoritatively supplies 500 subdomains;
- Core Plus plus SVA Essentials authoritatively enables Credential Exposure;
  and
- the corrected `New` stage is the approved pre-onboarding state for this
  clone.

## Required next gate

Before another Workato test or any Leonardo form activity:

1. Commercial/DealHub owner approves the one-opportunity bundle rule, the
   remaining-usage-value treatment, and authoritative quantity sources.
2. Workato owner implements an opportunity-bound selector that rejects
   unrelated opportunities, duplicates within each product group, unknown
   products, missing required fields, and changed subscription revisions.
3. CSE/Surface owner approves the Case 3 Leonardo field, module, toggle, date,
   and license mapping.
4. A separate, expiring, hash-bound one-run approval is recorded before any
   attended Leonardo Development form fill or submit.

Until then, CO-0717 remains `manual_review_required`, `proceed=false`, and is
not authorized for Leonardo creation.

## Guarded Pentera product-group selector revision

On 2026-08-26, the owner narrowed the interim DealHub scope to Pentera Surface,
Pentera Core, and explicit add-ons. The inactive Development recipe was updated
accordingly, without running another job:

- Step 8 now returns Product Code, Opportunity ID, SVA Type or Service Package,
  Quantity, and System Modstamp in addition to the previously selected fields.
- Step 9 maps those five values into the Ruby classifier.
- Expired rows and rows explicitly identified as remaining usage value are
  excluded from license selection.
- Current Surface and Core rows are ranked independently. Equal rank across
  Surface and Core is allowed, while equal-best duplicates inside the same
  product group still fail closed.
- A Core row may also qualify as an add-on when its mapped product/SVA evidence
  contains an explicit supported marker such as Credential Exposure, CE module,
  RansomwareReady, Security Validation Advisor, or SVA.
- All relevant current rows must have one shared Opportunity ID. Missing or
  multiple opportunities fail closed.
- Any other current product, unsupported status, or ambiguous same-group/add-on
  selection fails with a masked category.

Workato directly confirmed that the recipe saved successfully, showed a new
latest edit, and remained Inactive after exit. The prior saved configuration
was version 23; the expected next monotonic version is 24, but the recipe page
did not expose the numeric version in the verification view. No webhook was
opened, no CO identifier was transmitted, and no Salesforce, DealHub, OPA,
Leonardo, or production write occurred during this change.

The add-on marker list is an interim fail-closed implementation assumption, not
an authoritative commercial taxonomy. The configured Guru read-only connector
was unavailable in this session, so a Commercial/DealHub owner must approve the
taxonomy before it can authorize Leonardo mapping. Quantity remains captured
for later validation but is not inferred from product-name tier text.

## Secured selector retest — 2026-08-27

After action-time owner confirmation, exactly one payload containing only
`CO-0717` was sent to the inactive Development recipe's Test listener. A
one-time `X-Workato-Dedup` value was included, but neither it nor the webhook
address was persisted.

- Recipe version: `26`.
- Job: `j-AbN4NsKH-oHT4Bh-CD`.
- Started: 13:20:57 PDT.
- Result: `Successful` in 1.06 seconds.
- The malformed-identifier branch was not taken.
- The exact-one-record rejection branch was not taken.
- The masked Salesforce CO search, masked DealHub subscription search, and
  masked Ruby classifier all completed.
- Job totals after the run were 12 successful and three failed.
- No Salesforce or DealHub write, OPA call, Leonardo action, Donatello action,
  BackOffice action, or production action was present or performed.

Before the run, masking was added to the webhook trigger and saved. Workato did
not expose the documented recipe-level **Settings > Data retention** tab to the
current workspace or role, so no retention change is claimed. Workato's Test
mode can retain test-job data; the trigger and all data-bearing steps were
therefore kept masked.

Immediately after the run, the one-run webhook route was invalidated by another
high-entropy event-name rotation. Workato confirmed the save as current recipe
version `27`, the previous test route was no longer shown, and the recipe was
directly verified `Inactive`. No webhook address was recorded.

The successful job verifies that the guarded Pentera Surface, Pentera Core, and
explicit-add-on selector can process the current masked source bundle without
hitting its fail-closed ambiguity gates. It does **not** make the interim add-on
taxonomy authoritative, fill the missing quantity sources, approve Case 3
Leonardo field/toggle mapping, or authorize any Leonardo form activity.

The exact owner decisions and sign-off fields for the next gate are in the
[CO-0717 owner mapping approval packet](24_PHASE2_CO-0717_OWNER_MAPPING_APPROVAL_PACKET_2026-08-27.md).

## Core entitlement mapping correction and retest — 2026-08-27

The owner confirmed that the DealHub Subscriptions related list displayed on
the Customer Onboarding record is the commercial-validation source for this
test, and approved these exact interpretations:

- `Pentera Surface Go - 500 Subdomains` supplies the 500-subdomain Surface
  entitlement.
- `Pentera Core Plus Commercial - 500 End Points` supplies the 500-endpoint
  Core entitlement and Credential Exposure entitlement evidence.
- `Current Product - Remaining Usage Value` is administrative and must not be
  selected as a license.
- The active commercial term is `2026-07-01` through `2029-06-30`.

Development recipe `73688657` was saved as version `28` with a minimal,
contract-preserving classifier correction: an explicit supported add-on is
still preferred when present; otherwise the selected Pentera Core subscription
populates the existing `credential_exposure_subscription` output. No output
schema or downstream action was added.

The previously masked `CO-0717` event was repeated against the latest recipe
version, avoiding disclosure or reuse of a webhook capability address:

- Job: `j-AbN8h3EN-pNRABL-CD`.
- Started: 15:45:54 PDT.
- Result: `Successful` in 1.31 seconds.
- The masked Salesforce lookup, DealHub lookup, and Ruby classifier completed.
- The recipe performed no Salesforce or DealHub write and contained no OPA or
  Leonardo action.
- The recipe was directly verified `Inactive` after the run.

The Salesforce `Onboarding Comments` field was then replaced with exactly
`2026-07-01 - 2029-06-30`, and the rendered value was verified. This is a
temporary date-only rule. Surface Subscription Information and CE Subscription
Information are intentionally not written to that field yet; they require a
later approved design. The prior raw value was not persisted in the repository.

This clears the DealHub commercial-bundle ambiguity for review: commercial
validation is `approved_for_review`. It does not authorize Leonardo execution.
CO-0717 remains `manual_review_required`, `proceed=false`, and
`leonardo_request_allowed=false` until subscription identity/revision binding,
the Case 3 Leonardo mapping, duplicate/read-after-write controls, and a
separate hash-bound one-run approval are complete.

## Temporary date-only Onboarding Comments rule — 2026-08-27

The owner narrowed the temporary Salesforce `Onboarding Comments` contract to
one ISO range only:

`YYYY-MM-DD - YYYY-MM-DD`

For CO-0717 the saved and rendered value is
`2026-07-01 - 2029-06-30`. Surface Subscription Information and CE
Subscription Information remain separate future fields and are not included in
the comment.

The inactive Workato Development router was updated to preserve that future
contract without adding a Salesforce write:

- Step 9 now emits a top-level string output named `onboarding_comments`.
- The value is generated only from the selected subscription term.
- Missing dates stop with `missing_subscription_term`.
- Different selected Surface and Credential Exposure terms stop with
  `subscription_term_mismatch`.
- Surface, Core, and Credential Exposure subscription objects remain separate
  structured outputs.

The first retest on version `29`, job `j-AbN8rfnn-ETzrP9-CD`, failed closed
with `missing_subscription_term`. Inspection verified that the custom Ruby
input schema names the date fields `start_date` and `end_date`, not `start` and
`end`. The code was corrected without relaxing the missing-term gate.

Version `30`, job `j-AbN8tKRe-zxrGaH-CD`, then completed successfully in 1.14
seconds using the same masked CO-0717 event. Workato kept the trigger and
data-bearing steps masked, no Salesforce write or OPA/Leonardo action was
added, and the recipe was directly verified `Inactive` after the test.

## Selected subscription identity and revision binding — 2026-08-27

The inactive Workato Development router now binds every non-null selected
Surface, Credential Exposure, and Core subscription output to the source
DealHub `record_id` and `system_modstamp`. Step 9 stops with
`missing_subscription_identity_or_revision` when either value is absent. The
fields remain inside masked Workato output; their runtime values were not
copied into this repository.

Recipe version `31`, job `j-AbN99CCz-mQc384-CD`, completed successfully in
1.13 seconds by repeating the previously masked CO-0717 event. The masked
Salesforce and DealHub query steps and the classifier completed. No Salesforce
or DealHub write and no OPA or Leonardo action was present or performed. The
recipe remained inactive, as verified by the available **Start recipe** action.

This clears `selected_subscription_ids_and_revisions_not_bound`. CO-0717 still
fails closed for Leonardo execution because the Case 3 Leonardo mapping is not
approved, duplicate and read-after-write evidence is absent, and there is no
separate hash-bound one-run approval.
