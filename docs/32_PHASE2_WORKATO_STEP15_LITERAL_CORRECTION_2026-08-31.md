# Phase 2 Workato Step 15 literal correction

Date: 2026-08-31  
Target: Workato Development validate-only caller  
Status: **authorized structural edit completed; recipe verified inactive**

## Authorized scope

The action-time authorization covered one structural edit only: change Step
15's comparison value from Formula mode to the Text literal
`case_3_combined_baseline`, save, reload, and verify that the recipe remained
inactive. It explicitly prohibited **Test** and **Start recipe**.

## Directly verified outcome

- The recipe was in the Development environment and inactive before the edit.
- Only Step 15's comparison **value** mode was changed from Formula to Text.
- The Data field side of the comparison remained in Formula mode and retained
  its existing Step 9 datapill.
- The value displayed exactly `case_3_combined_baseline` as Text.
- **Save** was selected once and Workato reported that the recipe was saved.
- After reload, Step 15 displayed the exact literal and the prior formula error
  was absent.
- After exiting the editor, the recipe page still showed **Inactive** and
  **Start recipe** remained available.
- The visible job totals were unchanged. No Test, Start, Repeat job, OPA
  transmission, Leonardo action, Salesforce/DealHub write, or production
  action occurred.

No password, token, cookie, MFA value, customer payload, or raw source value is
retained in this record.

## Next safe gate

The structural-edit authorization is fully consumed and does not authorize a
caller run. Before any new validate-only attempt:

1. Perform a fresh masked, read-only source and revision gate for exactly one
   controlled CO-0717 record and its selected source relationships.
2. Stop on drift, ambiguity, duplicates, unsupported add-ons, source mismatch,
   or any recipe validation error.
3. Obtain a new action-time approval for exactly one masked Workato
   Development validate-only attempt. Do not reuse the earlier no-retry
   approval.

Leonardo lookup, fill, confirmation, creation, verification, Salesforce
writeback, unattended execution, and every production action remain blocked.
