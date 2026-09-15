# Workato Queue Synthetic Handoff

Date: 2026-08-22  
Environment: Workato Development  
Source record: Salesforce `Customer_Onboarding__c` clone `CO-0701`

## Verified outcome

- `Surface Onboarding Queue Sync (Inactive)` remains inactive and all three recipe steps remain data masked.
- The corrected recipe mappings were saved for the exact Salesforce record identifier and record URL.
- A single authorized trigger test was attempted and stopped after Workato did not return a new `CO-0701` event. No recipe action executed during that attempt.
- The earlier malformed rows were removed before the replacement validation.
- One bounded synthetic row now exists in `surface_onboarding_queue_private_v1` and one matching projection exists in `surface_onboarding_queue_view_v1`.
- Salesforce `CO-0701` remained unchanged. Both `LastModifiedDate` and `SystemModstamp` were still `2026-08-20T15:44:24.000Z` after verification.

## Synthetic row identity

| Field | Value |
| --- | --- |
| `source_key` | `salesforce:Customer_Onboarding__c:a5KR500000lYGNVMA4` |
| `sf_record_id` | `a5KR500000lYGNVMA4` |
| `co_number` | `CO-0701` |
| `account_name` | `Bormioli Pharma` |
| `approval_status` | `Approved` |
| `onboarding_stage` | `Request Approved` |
| `onboarding_product` | `Surface & Credential Exposure` |
| `onboarding_type` | `New Product Onboarding` |
| `queue_state` | `open` |
| `sync_status` | `synthetic_verified` |

The private row also records the Salesforce account and creator identifiers, creator name, `sensitivity_class=restricted`, `schema_version=v1`, and `incremental_job_id=synthetic-20260822`. The dashboard-safe row explicitly records `has_attachments=false` and `eligible_for_automation=false`.

## Data deliberately omitted

- Onboarding comments
- Domains and contact details
- Attachment identifiers, filenames, URLs, and content
- Hash fields, because no approved HMAC key or key version has been provisioned
- Surface Account ID and Account UUID, because both are null on `CO-0701`

Unpopulated controls must be treated as fail-closed. In particular, a null `is_stale` value is not authorization to automate. Only an explicit later validation may set eligibility to true.

## Monday continuation

1. Keep the queue recipe inactive.
2. Verify the Workflow App displays only the dashboard-safe projection for `CO-0701`.
3. Decide whether to implement a dedicated inactive synthetic-test recipe or a Workato test case that does not depend on replaying a Salesforce trigger cursor.
4. Complete explicit timestamp, count, and boolean mappings using synthetic values, then test the create/update/idempotency path without Salesforce writes.
5. Confirm Workato job-data retention/masking and table permissions before any real backfill.
6. Continue Presales/Donatello discovery only after access and MFA are restored; no account creation or downstream write is authorized.

## Remaining approvals

- Workato owner: approve the dedicated synthetic test mechanism and later initial backfill.
- Salesforce/process owner: confirm the authoritative actionable timestamp and exact open-record semantics.
- Security/Data owner: approve retention, HMAC key management, and audience for restricted identifiers and customer data.
- Donatello owner: restore approved dev access and MFA before read-only discovery.

