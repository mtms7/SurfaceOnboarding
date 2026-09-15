# CO-0696 Rejection-Note Writeback Test

Date: 2026-08-17  
Scope: one controlled Salesforce test-clone update.

## Authorized field update

After the CO-0696 shadow validation returned `manual_review_required`, the user approved an append-style test of the Salesforce **Onboarding Rejection Notes** field.

Target:

- Object: `Customer_Onboarding__c`
- Record: `CO-0696` (test clone)
- Field changed: `Onboarding_Rejection_Notes__c` only

## Safety checks completed

1. Queried Salesforce by exact CO number and confirmed exactly one record.
2. Read the field before update; it was empty.
3. Wrote one sanitized, timestamped Phase 1 note only.
4. Read the same record back and confirmed Salesforce saved the expected note.

The saved note records:

- license scope mismatch: 4,000 versus 10,000 End Points;
- quantity mismatch: 4,000 versus 10,000;
- approval status pending;
- OCR evidence requiring visual confirmation;
- the required CSM reconciliation action; and
- an explicit statement that no provisioning is authorized.

## Boundaries preserved

- No other Salesforce field or record was changed.
- No Workato recipe was started or edited.
- No tenant, Surface, Credential Exposure, Slack, or browser action occurred.
- The note contains no raw contract text, contract filename, contact data, or document download URL.

## Reusable production pattern

For a future approved CO, the automation must:

1. read the CO and current `Onboarding_Rejection_Notes__c` value;
2. deterministically generate a bounded sanitized note from mismatch/gate data;
3. append a timestamped entry rather than overwrite history;
4. update only `Onboarding_Rejection_Notes__c`; and
5. read the exact record back and fail closed if verification does not match.
