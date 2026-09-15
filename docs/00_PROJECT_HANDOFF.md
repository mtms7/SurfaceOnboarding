# Project Handoff

## Executive Summary

We are automating Surface customer onboarding in Workato. The automation starts when a CSM creates or updates a Customer Onboarding record in Salesforce. Workato reviews the CO source data, classifies it into one of six onboarding scenarios, asks for human approval, executes the matching onboarding workflow, verifies the result in BackOffice/Donatello, and updates Salesforce with the created account UUID.

The current implementation work began with Case 2: Credential Exposure only - New CE. The final architecture should support all six cases:

| Case | Scenario | Engine value | Current state |
| --- | --- | --- | --- |
| 1 | Surface only - New Surface | `case_1_new_surface_only` | Logic exists locally; needs Workato mapping |
| 2 | Credential Exposure only - New CE | `case_2_new_ce_only` | Workato recipe scaffold exists; OPA path required |
| 3 | Surface + Credential Exposure - New Surface + New CE | `case_3_combined_baseline` | Needs mapping |
| 4 | Surface + Credential Exposure - Renew Surface + New CE | `case_4_renew_surface_new_ce` | Needs mapping |
| 5 | Surface + Credential Exposure - New Surface + Renew CE | `case_5_renew_ce_new_surface` | Needs mapping |
| 6 | Surface + Credential Exposure - Renew Surface + Renew CE | `case_6_renew_both` | Needs mapping |

## Existing Workato Components

### Router Recipe

Name:

```text
CO Source Review and Onboarding Router
```

URL:

```text
https://app.workato.com/recipes/73688657-co-source-review-and-onboarding-router/edit
```

Known good test:

```text
CO-0596
Account: The Northern Trust Company
Case: case_2_new_ce_only
Salesforce record ID: a5KR500000h3VynMAE
Recipe version tested: 16
Date tested: 2026-06-30
```

What it already does:

- Accepts a CO number.
- Queries Salesforce `Customer_Onboarding__c`.
- Validates exactly one CO record exists.
- Queries related DealHub subscriptions.
- Classifies subscription context.
- Produces a readable review summary and structured outputs.

### Manual Case 2 Recipe

Name:

```text
Manual Case 2 - Donatello API
```

URL:

```text
https://app.workato.com/recipes/73788106-manual-case-2-donatello-api/edit
```

Current state:

- Callable recipe/function trigger exists.
- Ruby normalization action is tested.
- Donatello login HTTP step exists.
- Manual MFA branch exists using Workato Workflow App tasks.
- Donatello credentials are mapped from Workato environment properties.
- Direct Workato cloud HTTP failed with CloudFront/WAF `403` before MFA.

Important interpretation:

- The failed Workato test was a network/WAF edge block, not bad credentials or a bad MFA code.
- No account creation happened through the blocked path.

## What We Tried

1. Local review and onboarding scripts were used to understand the manual process and map fields.
2. Workato Router was built to reproduce the `review-co CO-XXX` logic.
3. Case 2 normalization was implemented in Workato Ruby.
4. A direct Workato HTTP login to Donatello dev was attempted.
5. Direct Workato HTTP was blocked by CloudFront/WAF.
6. Workato support recommended OPA instead of opening WAF to shared Workato egress IPs.
7. VM connectivity was tested from different VPN/lab paths.
8. RND Ubuntu lab was identified as the best OPA candidate path.

## Workato Support Guidance

Workato confirmed:

- US Workato egress IPs are static/published but shared because the tenant is multi-tenant.
- Workato recommends OPA for this scenario.
- With OPA, Donatello/BO traffic originates from the internal VM/network path.
- OPA requires outbound TCP 443 to Workato gateways.
- OPA does not require inbound firewall ports.
- Workato RPA is a separate product and is not currently included.

## Donatello/SecOps Guidance

Or involved Ran Kadury for Cybersecurity review. Or did not automatically rule out IP allowlisting, but asked for security evaluation. Or also confirmed:

- The official `/api/v1/auth/token` path may not work with the internal BackOffice endpoints, but testing is welcome.
- MFA must not be removed from the Workato user.
- A quick sync with stakeholders is recommended.

## Current Recommended Path

Use this order:

1. Build OPA on a new Ubuntu 22 VM in the RND VPN path.
2. Configure Workato HTTP connections through OPA.
3. Test Donatello dev through OPA first.
4. Test `/api/v1/auth/token` only if Donatello/SecOps approves.
5. If official token cannot support BackOffice endpoints, keep user login + manual MFA for dev proof-of-concept.
6. Do not perform BO production writes until approved.

## Important Endpoint Evidence

Observed browser-backed Donatello/BO endpoints:

```text
POST /api/v1/auth/login
POST /api/v1/auth/verify
GET  /api/v1/authenticated/getAuthorities
GET  /api/v1/userProfile/
POST /api/v1/backoffice/getAllDetailedAccounts
POST /api/v1/backoffice/account/add
POST /api/v1/backoffice/getAllDetailedAccounts
```

Required observed authorities:

```text
AccessBackoffice
AddAccount
GetAllDetailedAccounts
ViewLicense
EditLicense
UpdateAdvancedLeakedCredentialsSettings
ViewInventoryLeakedCredentials
```

## Local BO Onboarding Package Context

Existing local package:

```text
C:\Users\Milton Stevenson\Documents\Tickets\BO Onboarding
```

Important local commands:

- `review-co.cmd`: read-only Salesforce CO review.
- `onboard-case.cmd`: guarded dispatcher.
- `onboard-case-2.cmd`: Case 2 workflow.
- `prepare-case2-ce.cmd`: browser automation launcher for Case 2.

Production Workato must not depend on those local scripts. They are reference/discovery material for field mapping, safety gates, and expected behavior.

## Current Known Rejection Rule

For now, the only automatic rejection rule is:

```text
Reject if the CO Onboarding Type does not match the license/subscription description or inferred motion.
```

Examples:

- CO says `Credential Exposure only - New CE` but the account already has CE and the evidence indicates renewal -> reject.
- CO says `New Surface` but Salesforce/BackOffice already has a Surface account ID and the DealHub context indicates renewal -> reject.

Reminder: define additional rejection conditions later.

