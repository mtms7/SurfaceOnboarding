# Phase 2 CO-0701 diagnostic and Leonardo mapping gate

Date: 2026-08-26  
Target: Leonardo Development only  
Status: **router gate fixed and verified; source ambiguity still blocks mapping and execution**

## Outcome

CO-0701 is an owner-created Customer Onboarding clone and is suitable for
restricted diagnostic work. It is not currently a valid Leonardo mapping
fixture. The read-only source intake found two equally ranked `Active` DealHub
subscriptions on different opportunities. One is an incomplete test row with
no end date. The current Workato router version 20 nevertheless completed its
classification step instead of rejecting the ambiguity.

Workato Development version 21 now applies a conservative intake-level gate
before classification. If more than one subscription shares the best status
rank, the Ruby action stops with the masked category
`ambiguous_subscription_selection`. A focused CO-0701 rerun verified that the
gate rejects this evidence before classification completes.

No Salesforce record, Workato table, OPA service, Leonardo/Donatello account,
BackOffice system, or production environment was changed.

## Verified Salesforce intake

- Exactly one CO: `a5KR500000lYGNVMA4` / `CO-0701`.
- Source revision: `2026-08-26T21:00:12.000+0000`.
- Product: Surface and Credential Exposure.
- Type: New Product Onboarding.
- Stage: Request Approved; approval status: Approved.
- Submission date: `2026-08-13`.
- Surface Account ID and Account UUID are both empty.

The clone is restricted data. Customer name, contact, domain, comments,
attachments, and raw payloads are intentionally omitted.

## DealHub ambiguity

| Subscription | Product | State | Opportunity relationship | Blocking evidence |
| --- | --- | --- | --- | --- |
| `a2ZR5000000VMPFMA4` | Core Plus Commercial / SVA Essentials | Active; 36 months; 2026-04-01 to 2029-03-31 | Opportunity `006R500000PhGJIIA3` | Quantity is null; CE entitlement still needs authoritative commercial mapping. |
| `a2ZR5000000XbK9MAK` | Test product / old pricing | Active; 24 months; starts 2026-07-22 | Different opportunity `006R500000Pm7VTIAZ` | Quantity and end date are null; product semantics are not valid onboarding evidence. |

Both rows receive the current classifier's highest `Active` rank. Version 20
does not reject equal-rank candidates, so any selected row would be ordering
dependent rather than an authoritative onboarding decision.

## Authorized Workato diagnostic

Exactly one masked Test-mode job ran against inactive Development recipe
`CO Source Review and Onboarding Router` (`73688657`), version 20, at 14:08:16
PDT:

- job: `j-AbMX9wte-sLGPcr-CD`;
- outcome: `Successful`;
- CO format gate passed;
- exact-one CO gate passed;
- masked DealHub search and masked Ruby classification executed;
- no validator, OPA, Leonardo, Salesforce-write, or production action existed
  in the path; and
- the recipe was directly verified `Inactive` afterward, with 11 successful
  and zero failed jobs.

`Successful` means the current recipe executed. It does not mean its selected
subscription or route is valid. The diagnostic proves the missing fail-closed
duplicate/equal-rank gate.

## Workato Development hardening and negative retest

The owner authorized the focused correction and CO-0701 retest. Recipe version
21 added only the masked best-rank ambiguity stop to the existing read-only
Ruby classifier; it did not add a connector, write action, approval bypass, or
Leonardo step.

The 14:18:36 PDT retest produced the expected negative result:

- job: `j-AbMXHEz8-3dTWtP-CD`;
- version: 21;
- outcome: failed closed at step 9;
- masked category: `ambiguous_subscription_selection`;
- Salesforce and DealHub searches remained read-only and masked;
- classification did not complete; and
- the recipe was directly verified `Inactive` afterward, with 11 successful
  and one intentionally failed job.

The current rule is deliberately conservative: it rejects any intake-wide tie
at the best status rank. This prevents unsafe selection now, but it is not the
final multi-product selector. Allowing valid combined onboarding evidence will
require an owner-approved opportunity-bound rule that preserves distinct
Surface and Credential Exposure rows while rejecting unrelated or malformed
rows.

## Leonardo mapping decision

The CO fields suggest `case_3_combined_baseline`, but that remains an
unapproved candidate. Do not construct a Leonardo execution manifest from this
record until all of the following are true:

1. The Salesforce/DealHub owner identifies the intended opportunity and
   removes the invalid test row from the fixture's eligible evidence through
   an owner-controlled process, or the router explicitly rejects it.
2. Replace the conservative intake-wide tie rejection with an owner-approved,
   opportunity-bound selector; keep rejection for zero, unrelated, malformed,
   or equal-rank candidates within each mapped product group, and bind every
   selected subscription ID and `SystemModstamp`.
3. Product, Credential Exposure entitlement, quantity, dates, and license type
   are authoritatively mapped without parsing product prose into invented
   values.
4. Current verified Guru guidance and the CSE owner approve every Case 3
   Leonardo field, setting, toggle, module, and license value.
5. Leonardo duplicate/readback rules, separate requester/approver approval,
   idempotency lock, and a one-run Development authorization are recorded.

## Next safe action

The focused negative fixture now passes its safety objective: CO-0701 is
rejected before classification. Next, the Salesforce/DealHub owner must either
clean the clone's eligible DealHub evidence through the responsible process or
approve an opportunity-bound selection design using an isolated fixture whose
only related subscriptions are the intended Case 3 rows. Do not modify the
source merely to trigger a Salesforce event, and do not open or submit a
Leonardo form from the current evidence.
