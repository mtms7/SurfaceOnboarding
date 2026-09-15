# Workato Recipe Architecture

## Target End-To-End Flow

1. CSM creates or updates a CO in Salesforce.
2. Workato runs `Review CO-XXX`.
3. Workato validates source data and classifies the case.
4. If rejected, Workato updates Salesforce with rejection status/notes.
5. If valid, Workato asks a human whether to proceed.
6. If approved, Workato calls the matching case recipe.
7. Case recipe authenticates to Donatello/BO through OPA.
8. Case recipe performs duplicate check.
9. Case recipe creates or updates the account only if safe.
10. Case recipe performs read-after-write verification.
11. Workato retrieves `accountUuid`.
12. Workato updates Salesforce `Surface Account ID` with the UUID after verification.
13. Workato writes masked audit data.

## Component Map

| Component | Type | Purpose |
| --- | --- | --- |
| Salesforce CO trigger | Workato Salesforce trigger | Start automation when CO is ready |
| Router/Review recipe | Workato recipe | Source review, classification, rejection gates |
| Approval task | Workato Workflow App | Human approval before write actions |
| OPA VM | Ubuntu 22 + Workato OPA | Trusted network path from Workato to Donatello/BO |
| HTTP via OPA connection | Workato HTTP connector | Send BackOffice calls from VM path |
| Case recipes | Callable Workato recipes | Execute case-specific onboarding |
| Lookup table | Workato lookup table | Idempotency and masked audit |
| Salesforce writeback | Workato Salesforce action | Update CO after verified success |

## Recipe 1: CO Intake And Review Router

Name:

```text
CO Source Review and Onboarding Router
```

Recommended trigger phases:

| Phase | Trigger | Use |
| --- | --- | --- |
| Phase 1 | Manual/API trigger with `co_number` | Development and controlled tests |
| Phase 2 | Salesforce new/updated `Customer_Onboarding__c` | Production-ready automation |

Core steps:

1. Normalize CO number.
2. Query Salesforce `Customer_Onboarding__c`.
3. Validate exactly one record.
4. Query DealHub subscriptions.
5. Normalize subscriptions.
6. Infer case engine value.
7. Apply rejection gates.
8. Build review summary.
9. If rejected, update Salesforce and stop.
10. If valid, create approval task.
11. On approval, call matching case recipe.
12. Receive case result.
13. If success, update Salesforce with UUID.
14. Write masked audit result.

## Required Router Output Contract

```json
{
  "status": "approved_for_onboarding",
  "co_number": "CO-XXXX",
  "sf_record_id": "<salesforce-id>",
  "account_name": "<salesforce-account-name>",
  "account_country": "<country>",
  "main_domain": "<domain>",
  "email_domains": ["example.com"],
  "alternate_domains": [],
  "onboarding_product": "<salesforce-product>",
  "onboarding_type": "<salesforce-onboarding-type>",
  "surface_account_id": "<existing-or-empty>",
  "account_uuid": "<existing-or-empty>",
  "engine_value": "case_2_new_ce_only",
  "review_summary": "<human-readable-summary>",
  "rejection_reasons": [],
  "router_job_id": "<workato-job-id>"
}
```

## Case Routing

| Engine value | Callable recipe | Initial behavior |
| --- | --- | --- |
| `case_1_new_surface_only` | `Manual Case 1 - Surface New Account` | Build from local Case 1 logic |
| `case_2_new_ce_only` | `Manual Case 2 - Donatello API` | Continue current build through OPA |
| `case_3_combined_baseline` | `Manual Case 3 - New Surface New CE` | Stub and block until mapped |
| `case_4_renew_surface_new_ce` | `Manual Case 4 - Renew Surface New CE` | Stub and block until mapped |
| `case_5_renew_ce_new_surface` | `Manual Case 5 - New Surface Renew CE` | Stub and block until mapped |
| `case_6_renew_both` | `Manual Case 6 - Renew Surface Renew CE` | Stub and block until mapped |

Unsupported or uncertain routes must stop with:

```text
manual_review_required
case_not_mapped_yet
source_data_mismatch
```

## Current Rejection Gate

Rule:

```text
Reject if Onboarding Type does not match the license/subscription description or inferred motion.
```

Implementation hints:

- Compare Salesforce `Onboarding_Product__c` and `Onboarding_Type__c` to inferred subscription motion.
- For `New` motions, block if Salesforce already has the relevant existing account ID/UUID or BackOffice duplicate exists.
- For `Renew` motions, block if the relevant existing account ID/UUID is missing.
- For combined Surface + CE renewals, block until the Surface and CE motions are individually identified.

Rejection output:

```json
{
  "status": "rejected",
  "failure_category": "source_data_mismatch",
  "rejection_reasons": [
    "Onboarding Type says New CE but license/account evidence indicates CE renewal."
  ]
}
```

## Approval Gate

Use Workato Workflow Apps for a durable audit trail.

Approval task should show:

- CO number.
- account name.
- case type.
- onboarding product/type.
- subscription summary.
- duplicate-check status if already performed.
- reason for block/rejection if applicable.

Approval task must not show:

- passwords.
- MFA codes.
- bearer tokens.
- full request bodies.
- full API responses.
- sensitive raw payloads beyond what the reviewer needs.

## Case 2 Recipe Through OPA

Existing recipe:

```text
Manual Case 2 - Donatello API
```

Required next steps:

1. Change HTTP connection to:

```text
Donatello Dev HTTP via RND OPA
```

2. Login:

```text
POST /api/v1/auth/login
```

3. Preserve MFA:

```text
POST /api/v1/auth/verify
```

4. Authority check:

```text
GET /api/v1/authenticated/getAuthorities
```

5. Duplicate check:

```text
POST /api/v1/backoffice/getAllDetailedAccounts
```

6. Create only after duplicate check and approval:

```text
POST /api/v1/backoffice/account/add
```

7. Verify:

```text
POST /api/v1/backoffice/getAllDetailedAccounts
```

8. Return:

```json
{
  "status": "success",
  "account_uuid": "<accountUuid>",
  "created_account_name": "<name>",
  "verified": true
}
```

## Salesforce Writeback

User requirement:

```text
Retrieve the Account UUID from the provisioned account and update Surface Account ID in Salesforce.
```

Working field assumption:

```text
Surface_Account_ID__c = accountUuid
```

Important: local review logic also references `Account_UUID__c`. Confirm with Salesforce owner whether CE-only should update `Surface_Account_ID__c`, `Account_UUID__c`, or both. Until confirmed, update only the approved target field.

Writeback must happen only after:

- create/update response is successful.
- read-after-write verification passed.
- duplicate/idempotency table is updated.

## Idempotency Table

Create Workato lookup table:

```text
surface_onboarding_idempotency
```

Columns:

```text
idempotency_key
co_number
sf_record_id
case_type
target_environment
status
workato_router_job_id
workato_case_job_id
created_account_uuid
created_account_name
created_account_domain_hash
approved_by
approved_at
created_at
updated_at
failure_category
failure_message_masked
```

Do not store raw passwords, MFA codes, tokens, or full API payloads.

