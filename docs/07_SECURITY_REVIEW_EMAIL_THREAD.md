# Security Review Email Thread - Workato OPA / IP Allowlist / Service Auth

Date captured: 2026-07-13
Owner: Milton Stevenson
Project: Surface Customer Onboarding Automation
Scope: Workato automation for Surface administrative onboarding workflows

## Purpose

This document captures the current email thread and decision context around the secure connectivity and authentication architecture for automating Surface BackOffice administrative onboarding workflows through Workato.

The goal is to preserve the full discussion context so the team can continue the security review without repeating previous findings.

## Current Architecture Question

We need to automate Surface administrative onboarding workflows from Workato, but the BackOffice administrative console is accessible only from a trusted network.

The security decision is which architecture should be approved for production:

1. Workato On-Prem Agent (OPA) running from a trusted Pentera network path.
2. IP allowlisting of Workato cloud egress IPs.
3. Supported service-to-service authentication/API path.
4. Temporary dev-only user-authentication flow with MFA preserved.

## Email Thread

### Or's Message

```text
Thanks Milton for the great update.
I want to involve +Ran Kadury for Cybersecurity aspects to determine the best path forward.
I don't want to automatically rule out the IP Whitelist solution, we need a quick security evaluation to determine our preferred way.

Ran - tl;dr - we want to automate few workflows in Surface administrative operations. The existing and important limitation is that a user can connecte to the administrative console (the backoffice) only if it's in a trusted network, and the question now is how we are working with it

Milton - I'm not sure the official authentication method will work with the needed API endpoints for the workflows, but you are welcome to try if you preferred this way without the manual MFA.
Just to make sure - and I'm pretty sure it was not something you thought of - please do not remove the MFA from the user that will be in Workato :) we don't want to compromise on security aspects here.

Let me know if you have any questions.
Perhaps it will be worth to schedule a quick sync with all the relevant people.
```

### Ran's Message

```text
Hi Everyone,

Before we make a recommendation for a production architecture, I would like @Elad Nadler to perform a quick security review of the proposed options, including the Workato On-Prem Agent (OPA), IP allowlisting, and any supported service-to-service authentication mechanisms. We'll then recommend an architecture that balances both security and operational aspects.

@Milton Thomas Machado Stevenson - Please set up a call to sync with all relevant people.

Thanks.
```

## Interpretation

Or and Ran are not blocking the project. They are asking for a structured security review before recommending a production architecture.

Important points:

- IP allowlisting should not be dismissed automatically, but it needs Cybersecurity review.
- Workato OPA remains a strong candidate because it keeps traffic originating from a trusted Pentera network path.
- Service-to-service auth is still worth exploring, but Or is not sure `/api/v1/auth/token` will work with the needed internal BackOffice endpoints.
- MFA must not be removed from any Workato user-based BackOffice account.
- A quick sync should be scheduled with the relevant stakeholders.
- Elad Nadler should review the proposed options from Cybersecurity's side before a final production recommendation.

## Current Technical Evidence

### Direct Workato Cloud HTTP

Result:

```text
Blocked by CloudFront/WAF with HTTP 403 before MFA.
```

Interpretation:

- This was not a credential issue.
- This was not an MFA-code issue.
- Workato cloud egress did not reach the Donatello auth application layer.

### Workato Cloud Egress IPs

Workato confirmed the US DC egress IPs:

```text
52.5.142.59
34.226.132.221
52.54.43.157
```

Workato also confirmed:

- These IPs are stable/published.
- Workato communicates changes when they occur.
- These IPs are shared because Pentera is on multi-tenant Workato.

Security consideration:

- Allowlisting these IPs means allowing shared Workato cloud egress to reach administrative BackOffice endpoints.
- This may still be acceptable for a scoped dev-only or production-reviewed architecture, but it needs Cybersecurity approval.

### Workato OPA

Workato recommended OPA for this scenario.

OPA behavior:

- Runs inside the trusted network path.
- Opens outbound TLS/mTLS connectivity to Workato cloud.
- Does not require inbound ports into Pentera networks.
- Makes Donatello/BO traffic originate from the OPA VM/network path, not from Workato shared cloud IPs.

Validated RND lab candidate:

```text
Internal lab/VM path: 172.26.37.12
Outbound NAT IP: 199.203.203.177
Workato OPA gateways reachable: sg3.workato.com and sg4.workato.com
Donatello dev root: HTTP 200
Donatello unauthenticated API checks: HTTP 401
```

Interpretation:

- `401` on unauthenticated Donatello API checks is good for network validation.
- It proves the request reached the Donatello application/API layer rather than being blocked at WAF.

### Service-To-Service Authentication

Candidate endpoint:

```text
/api/v1/auth/token
```

Open issue:

- Or is not sure this official auth method works with the required internal BackOffice endpoints.

If supported, the preferred long-term model would be:

- dedicated automation identity.
- scoped permissions.
- auditable service authentication.
- no human-user MFA compromise.
- token rotation.
- stable endpoint contract.

If not supported, the dev fallback is:

- user login through OPA.
- MFA preserved.
- manual Workato task for the MFA code.
- dev-only proof-of-concept until a better service-auth path is approved.

## Architecture Options For Review

### Option 1 - Workato OPA From Trusted Network

Description:

```text
Workato Cloud orchestrates the recipe.
OPA runs on Ubuntu 22 VM in the trusted RND VPN/network path.
OPA opens outbound-only TLS/mTLS to Workato.
Donatello/BO requests originate from the VM path.
```

Pros:

- Aligns with Workato recommendation.
- Avoids opening BackOffice to shared Workato egress IPs.
- No inbound firewall ports required.
- Keeps BackOffice trusted-network requirement intact.
- RND path already validated against Donatello dev.

Cons:

- Requires VM lifecycle ownership.
- Requires OPA entitlement/configuration.
- Requires monitoring, patching, certificate renewal, and service health checks.

### Option 2 - Workato Cloud IP Allowlisting

Description:

```text
Allow Workato US DC egress IPs through Donatello/BO WAF for required endpoints.
```

Pros:

- Simpler infrastructure.
- No VM/OPA operations.
- Native Workato cloud HTTP path.

Cons:

- Workato IPs are shared multi-tenant.
- Expands trust boundary to shared cloud egress.
- Requires WAF allowlist maintenance.
- Direct Workato test is currently blocked.

### Option 3 - Supported Service-To-Service Auth/API

Description:

```text
Use official service authentication, ideally /api/v1/auth/token, if it can access the required BackOffice operations.
```

Pros:

- Best long-term identity model if supported.
- Avoids user-password and manual MFA flow.
- Easier to scope, rotate, and audit.

Cons:

- Endpoint may not support the internal BackOffice API calls.
- Requires Donatello/Product/SecOps support.
- May require new API contract work.

### Option 4 - Dev-Only User Login With Manual MFA

Description:

```text
Use BackOffice user login through OPA, preserve MFA, and collect the MFA code through a Workato Workflow App task.
```

Pros:

- Works with the existing BackOffice user model.
- Preserves MFA.
- Good enough for controlled Donatello dev proof-of-concept.

Cons:

- Not ideal for production.
- Human MFA step prevents full automation.
- User-account dependency needs careful controls.

## Recommended Position For The Sync

Recommended technical position:

```text
Use OPA as the preferred near-term connectivity path for Donatello dev testing, while Cybersecurity reviews whether production should use OPA, IP allowlisting, or service-to-service authentication.
```

Recommended security position:

```text
Do not remove MFA from any user account. If user-based auth is used for dev, preserve MFA and use a manual Workato MFA task. Prefer service-to-service auth if Donatello confirms it supports the needed BackOffice endpoints and scopes.
```

Recommended production position:

```text
No production BO writes until Ran/Elad/Or approve the connectivity and authentication model, duplicate checks are implemented, read-after-write verification is proven, and Salesforce writeback is validated.
```

## Stakeholders For Sync

Required:

- Milton Stevenson - automation owner / CSE
- Or - Donatello/Surface stakeholder
- Ran Kadury - Cybersecurity
- Elad Nadler - Cybersecurity review
- Workato contact / AE or technical contact if needed

Recommended:

- Salesforce owner/admin for CO writeback fields
- Surface Product/BackOffice owner
- CS Operations representative

## Suggested Meeting Title

```text
Security Review: Workato Automation Connectivity for Surface BackOffice Onboarding
```

## Suggested Meeting Agenda

1. Confirm project scope:
   - automate Surface onboarding workflows.
   - Donatello dev first.
   - final BO production only after approval.
2. Review trusted-network requirement for BackOffice.
3. Review three production architecture options:
   - Workato OPA.
   - Workato IP allowlisting.
   - service-to-service auth/API.
4. Review current evidence:
   - direct Workato cloud HTTP blocked by WAF.
   - RND OPA candidate path reaches Donatello app/API layer.
5. Confirm MFA posture:
   - MFA remains enabled for user-based flow.
6. Decide next allowed test:
   - OPA install on RND Ubuntu VM.
   - `/api/v1/auth/token` test.
   - no create action until duplicate/read-after-write gates are in place.
7. Confirm Salesforce writeback fields:
   - `Surface_Account_ID__c`
   - `Account_UUID__c`
8. Assign owners and due dates.

## Suggested Reply To Thread

```text
Hi Ran, Or,

Thanks, that makes sense.

I'll set up a short sync with the relevant people and include Elad for the security review.

For the discussion, I'll prepare the options we have tested/considered so far:

1. Workato OPA from a trusted RND network path.
2. Workato cloud IP allowlisting.
3. Supported service-to-service authentication, if /api/v1/auth/token can support the required BackOffice endpoints.
4. Dev-only user login with MFA preserved as a fallback for controlled testing.

To be clear, I will not remove MFA from the Workato/BackOffice user. If we use a user-based flow for the dev POC, MFA will stay enabled and the code will be handled through a manual Workato task.

Current evidence:
- Direct Workato cloud HTTP was blocked by CloudFront/WAF before MFA.
- Workato confirmed their US egress IPs are static but shared because we are on multi-tenant Workato.
- Workato recommended OPA for this use case.
- The RND Ubuntu path has been validated: Workato OPA gateways are reachable, Donatello dev returns HTTP 200, and unauthenticated Donatello API checks return HTTP 401, which means the traffic reaches the application/API layer instead of being blocked by WAF.

I'll send a calendar invite with a short agenda so we can decide the approved path for the next test and the production recommendation.

Thanks,
Milton
```

## Immediate Next Actions

1. Milton schedules sync with Or, Ran, Elad, and relevant stakeholders.
2. Prepare security option comparison using this document.
3. Do not remove MFA.
4. Do not run production BO writes.
5. Continue with OPA setup only after stakeholders are aligned on next test scope.
6. Test `/api/v1/auth/token` only as an approved validation path.
7. Keep Donatello dev testing separate from final BO production rollout.
