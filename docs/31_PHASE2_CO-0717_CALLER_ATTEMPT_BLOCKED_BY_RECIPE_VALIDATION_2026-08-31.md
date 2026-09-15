# Phase 2 CO-0717 caller attempt blocked by recipe validation

Date: 2026-08-31  
Target: Workato Development validate-only caller  
Status: **blocked before job creation or transmission; no retry authorized**

## Approved scope

Fresh action-time approval covered exactly one masked Development
validate-only attempt for CO-0717. It explicitly prohibited starting the
recipe, retrying the attempt, and performing any Leonardo action.

## Immediate read-only source gate

The pre-run gate passed after applying the actual source and v2 contract
semantics:

- exactly one controlled CO and a current source revision;
- the expected approved/New Case 3 routing state;
- the intended Primary User relationship, name, organization mailbox, and
  Contact revision;
- phone and job-title keys present; blank strings are allowed by the strict v2
  schema and local intake implementation;
- exactly one active Surface tier and one active Core tier, with matching
  account/opportunity binding, the expected common term, and both selected
  record revisions present;
- three distinct valid Surface domains, one valid Credential Exposure email
  domain, and zero candidate Pentera add-ons.

The DealHub selections are account-related records. The optional direct
subscription lookup fields on the CO are blank and are not the Workato
selector's authority. No raw Contact, domain, Salesforce, DealHub, revision,
or subscription value is retained here.

## Directly observed Workato outcome

- The recipe was in Development and inactive; **Start recipe** remained
  available.
- The trigger, Salesforce query, DealHub query, classifier, and OPA actions
  were masked.
- The documented masked CO-0717 trigger event was selected and **Repeat job**
  was clicked once.
- Workato stopped before creating a job and displayed that the job could not
  be repeated because the recipe had validation errors.
- The specific visible error was Step 15: its comparison value was in Formula
  mode, causing `case_3_combined_baseline` to be parsed as a formula instead of
  a literal Text value.
- Exit verification still showed the recipe as inactive, with **Start recipe**
  available. No CO payload reached OPA and no Salesforce, DealHub, Leonardo,
  Donatello, BackOffice, or production action occurred.

## Next safe gate

The separately authorized structural edit and save/reload verification are
complete; see `32_PHASE2_WORKATO_STEP15_LITERAL_CORRECTION_2026-08-31.md`.
The recipe remained inactive and no Test or Start action occurred.

1. Re-run the masked source/revision gate.
2. Obtain new action-time approval for a new single validate-only attempt. The
   previous approval cannot be reused because it prohibited retries.

Leonardo lookup, fill, confirmation, creation, verification, Salesforce
writeback, unattended execution, and every production action remain blocked.
