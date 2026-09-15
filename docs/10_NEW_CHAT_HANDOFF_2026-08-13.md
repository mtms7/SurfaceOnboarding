# Surface Workato OPA - New Chat Handoff

**Prepared:** 2026-08-13  
**Owner:** Milton Stevenson  
**Purpose:** Start a new Codex chat with the accurate technical state, evidence, safeguards, and immediate next steps for this project.

> **Read this file first in the next chat.** The live Workato configuration described below is newer than parts of the original two-week plan. Do not treat proposed names or dates in older documents as current configuration.

## 1. Project Objective

Build a guarded Workato automation for Surface customer onboarding:

1. Read and validate a Salesforce Customer Onboarding (CO) record plus DealHub subscription data.
2. Classify the request into one of six onboarding cases.
3. Stop on incomplete, ambiguous, unsupported, or mismatched source data.
4. Require recorded human approval before any account action.
5. Use Workato On-Prem Agent (OPA) from the RND network path to access Donatello/BackOffice.
6. Check authority and duplicates, construct a masked dry-run payload, and only then perform a separately approved create or update.
7. Verify the result, obtain `accountUuid`, write to the Salesforce field approved by the Salesforce owner, and create a masked idempotency/audit record.

The intended final target is BackOffice production, but **production writes are prohibited** at this stage. Current work is connectivity and dev/presales validation only.

## 2. Non-Negotiable Safety Rules

- Do not remove, bypass, or weaken MFA.
- Do not store passwords, MFA codes, bearer tokens, cookies, or full customer request/response bodies in the VM, project files, recipe comments, Workato lookup tables, chat, email, Slack, or screenshots.
- Do not run `apt upgrade`, `apt-get upgrade`, or `dist-upgrade` on the OPA VM. Pentera requested no manual OS upgrades. A controlled Workato OPA package installation has already been completed.
- Do not use production BackOffice (`https://app.pentera.io`) for writes.
- Do not create/update an account until all of these exist: stakeholder approval, auth/authority validation, duplicate check, approved dry-run payload, and a separately approved controlled-create scope.
- Keep the OPA recipe tests stopped except for a deliberate one-time **Test** run. Do not click **Start recipe** for connectivity-test recipes.
- Fail closed for unknown/multiple COs, source-data mismatch, missing required fields, unsupported cases, missing authority, duplicates, unexpected schema, WAF blocks, failed auth, inactive OPA, or denied/expired approval.

## 3. Live Infrastructure - Verified

### Ubuntu VM

| Item | Live value / result |
| --- | --- |
| Hostname | `workato-opa-01` |
| OS | Ubuntu Server 22.04 LTS |
| VM IP | `172.26.37.20/16` |
| VLAN | VLAN 226 / RND VPN path |
| Default gateway | `172.26.0.1` |
| Capacity | 2 vCPU, approximately 15 GiB RAM, approximately 116 GiB disk |
| Workato agent package | `workato-agent` 32.1 |
| Service | `workato-agent.service` is **enabled** and **active (running)** |
| RND egress NAT previously observed | `199.203.203.177` |

Current systemd proof received on 2026-08-11:

```text
systemctl is-active workato-agent.service  -> active
Loaded: enabled
Active: active (running) since 2026-07-22
```

Safe health commands to run on the VM when needed:

```bash
sudo systemctl is-active workato-agent.service
sudo systemctl status workato-agent.service --no-pager
sudo journalctl -u workato-agent.service -n 50 --no-pager
nc -vz -w 5 sg3.workato.com 443
nc -vz -w 5 sg4.workato.com 443
```

Validated result: both Workato gateways accept outbound TCP/443. The raw OpenSSL test shows Workato's private certificate chain, which is expected. A TLS-decryption exception must remain scoped to `sg3.workato.com` and `sg4.workato.com` only, if inspection exists.

### Workato OPA

**Live names - use these, not older proposed names:**

| Asset | Live name / state |
| --- | --- |
| On-prem group | `surface-onboarding-rnd` |
| OPA host path | `workato-opa-01` / `172.26.37.20` |
| Donatello HTTP connection | `donatello-dev-via-opa-readonly` |
| Presales HTTP connection | `presales-eu-via-opa-readonly` |

Both HTTP connections are in Workato project/location **Surface Onboarding** and use the live on-prem group `surface-onboarding-rnd`.

## 4. Completed Connectivity Evidence

### Donatello dev

The following have already passed through the Workato OPA path:

- Root endpoint returned `200` with HTML content.
- A protected endpoint (`/api/v1/authenticated/userProfile`) returned `401` when unauthenticated.
- The Workato job output contained `x_workato_onprem_agent`.

Interpretation: networking and OPA routing work; the `401` is the expected application-layer response for an unauthenticated protected endpoint. This was **not** a login, MFA, authority, duplicate, or write test.

### Presales EU

VM test passed:

```bash
curl -sS -D - -o /dev/null --connect-timeout 10 \
  https://presale.app.pentera.io/backoffice
```

Result: `HTTP/2 200`, `Content-Type: text/html`; the page is an Amazon S3/CloudFront-hosted single-page app shell.

Workato guided sample request also passed:

| Item | Value |
| --- | --- |
| Base URL | `https://presale.app.pentera.io` |
| Path | `/backoffice` |
| Method | `GET` |
| Authentication | None |
| Result | `200 - OK` |
| Response type | HTML, not JSON |
| OPA proof | `X-Workato-Onprem-Agent` response header present |
| Gateway proof | `X-Gateway-Job-Complete: true` |

`X-Cache: Error from cloudfront` is **not a failure** in this result because the actual HTTP status is `200`.

This proves only that Workato can reach the **Presales frontend** from the OPA. It does not establish a supported Presales API endpoint, user authentication, authority, or permission to change Presales data.

## 5. Exact Current Workato Screen / Immediate Task

### Current recipe

| Item | Value |
| --- | --- |
| Recipe | `OPA Connectivity Test - Presales EU` |
| Recipe URL pattern | `https://app.workato.com/recipes/74686219-opa-connectivity-test-presales-eu/edit` |
| Trigger | Trigger on a specified schedule |
| State | Keep stopped/inactive; use only **Test** for one-off runs |
| Action | HTTP -> Send request via HTTP |
| Selected connection | `presales-eu-via-opa-readonly` |

### What just happened

The user used Workato's **guided setup** and the sample `GET /backoffice` succeeded with `200 - OK`. When the user clicked **Apply configuration**, Workato showed:

```text
Error generating schema for response: Invalid json document.
Please specify proper response type on the previous step.
```

This is expected. The response is HTML (`<!DOCTYPE html>...`), so Workato cannot generate a JSON schema.

### Resume instructions - manual setup only

Give beginner-friendly click-by-click instructions. The immediate goal is only to save the safe connectivity action, not to access data.

1. In the **Setup HTTP from sample request** window, click the **X** at the upper-right to close guided setup.
2. Back on the recipe canvas, click the action box **Send request via HTTP**.
3. In the panel on the right, choose **setup manually**. Do **not** click **Start guided setup** again.
4. Enter or confirm:

   | Field | Exact value |
   | --- | --- |
   | Request name | `GET Presales EU Backoffice via OPA` |
   | Method | `GET` |
   | Request URL | `/backoffice` |
   | Request headers | Leave empty |
   | Response content type | `Text` |
   | Encoding | `UTF-8` (default) |
   | HTTP response headers/schema | Leave empty; do not use JSON |

5. Click **Save** in the upper-right of the recipe builder.
6. Click **Test** in the upper-right one time.
7. In the resulting job, expect:

   ```text
   Successful
   status_code: 200
   x_workato_onprem_agent: <value>
   x_gateway_job_complete: true
   ```

8. Do **not** click **Start recipe**. Send the status and headers to the next chat before any further test.

## 6. Workato Guidance for a Beginner

The next assistant should give every Workato instruction in this format:

1. State the exact Workato page/asset where the user should be.
2. Identify the exact button or menu item to click.
3. Provide the literal text/value to enter.
4. State what screen/result is expected.
5. Say clearly whether the action is read-only or can change data.
6. Stop at the first meaningful checkpoint and ask for a screenshot or sanitized output.

Never tell the user to put an HTTP request inside the **Connections** asset page. A connection stores endpoint/authentication configuration; the request action is created inside a **recipe**.

Use the **HTTP** app, not **HTTP webhook**, for outbound HTTP requests. HTTP webhook is for receiving events into Workato.

## 7. Workato Recipes and Current Functional State

### Router

| Item | Details |
| --- | --- |
| Recipe | `CO Source Review and Onboarding Router` |
| Historical URL | `https://app.workato.com/recipes/73688657-co-source-review-and-onboarding-router/edit` |
| Trigger | Real-time HTTP webhook expecting `co_number` |
| Current steps | Salesforce CO lookup -> exact-one check -> DealHub subscription lookup -> Ruby classification |
| Proven behavior | It successfully processed a test webhook for `CO-0596` and performed only read-only Salesforce/DealHub/Ruby steps. |

The router currently does **not** call Donatello/Presales and does not create/update records. It should remain that way until hardened.

Known active steps shown in Workato:

1. `CO source review request via HTTP webhook`
2. `Search for records using SOQL query in Salesforce`
3. Conditional: stop if result count is not exactly one
4. `For each` returned record
5. `Search for DealHub Subscriptions in Salesforce`
6. Ruby: `Classify DealHub subscriptions`

### Case 2 recipe

| Item | Details |
| --- | --- |
| Recipe | `Manual Case 2 - Donatello API` |
| Historical URL | `https://app.workato.com/recipes/73788106-manual-case-2-donatello-api/edit` |
| Status | Scaffold exists; do not enable authenticated calls, duplicate checks, or writes without security scope approval. |

Earlier build state: callable trigger, Ruby normalization, login action, and manual-MFA concept existed. Direct Workato-cloud HTTP hit WAF `403`; the OPA route removed this connectivity issue. That does **not** authorize login or create tests.

## 8. CO-0596 Test Facts

`CO-0596` was used only for read-only router testing.

| Field | Result |
| --- | --- |
| Salesforce CO ID | `a5KR500000h3VynMAE` |
| Account | The Northern Trust Company |
| Product | Credential Exposure |
| Onboarding type | New Product Onboarding |
| Stage | New |
| Approval status | Pending |
| Surface Account ID | Empty |
| Account UUID | Empty |
| Email domain | `@ntrs.com` |
| Main/alternate domain | Empty |
| Primary user | Empty |

The DealHub/Ruby output found 11 subscriptions and reported:

- Surface subscription: expired on 2026-04-27.
- Credential Exposure subscription: expired on 2026-04-27.
- Core subscription: active from 2026-04-28 through 2027-04-27.

Important: The CO record has `Onboarding_Approval_Status__c = Pending` and lacks several fields. It must **not** be automatically routed to an onboarding create. Treat it as `awaiting_approval` and/or `manual_review_required` until source-data requirements and the case mapping are confirmed.

## 9. Next Engineering Work - Safe Sequence

### A. Finish Presales connectivity recipe (current task)

Complete the manual `Text` response configuration described in section 5 and record the one-time successful test result. This remains a frontend reachability test only.

### B. Obtain an approved Presales API test contract

Before sending any Presales API request, obtain from the Presales/BackOffice owner:

- the exact read-only API endpoint and HTTP method;
- supported authentication method for the assigned dev/presales user or service identity;
- expected response format and sample sanitized response;
- confirmation that the test is permitted through OPA;
- required authorities and confirmation of the user role;
- a non-production test account/tenant and an expiry/availability window.

Do **not** assume Donatello endpoints such as `/api/v1/authenticated/getAuthorities` are identical or permitted in Presales. Do not try login paths or brute-force discovery.

### C. Harden the Router before any case execution

Edit the Ruby action only after saving/versioning the recipe. Add/router-map these outputs:

```text
co_number
sf_record_id
account_name
account_country
main_domain
email_domains
alternate_domains
onboarding_product
onboarding_type
surface_account_id
account_uuid
surface_subscription
credential_exposure_subscription
core_subscription
engine_value
decision
review_summary
rejection_reasons
warnings
```

The Ruby input screen in the last screenshot showed mappings for subscription fields. That screen is **only input mapping**. Do not add output text there. Scroll down inside the Ruby action to find the code and obtain/export the existing code before changing it. The existing code must be reviewed before an exact replacement is supplied.

Required router outcome categories:

```text
approved_for_review
awaiting_approval
manual_review_required
rejected
case_not_mapped_yet
error
```

### D. Build approval gate

Use a Workato Workflow App approval task after valid source review and before a case recipe. Display only non-sensitive review data. Track approver and timestamp. Denied/timeout must stop safely.

### E. Case 2 authenticated path - blocked pending approval

Only after Or/Ran/Elad/Surface approve the scoped dev test:

1. Use the OPA-backed Donatello connection.
2. Authenticate through the approved method; preserve MFA.
3. Validate authorities.
4. Run a duplicate check.
5. Build a masked dry-run payload.
6. Stop before create until a separate create approval exists.

Known historical Donatello endpoints (not authorization to call them):

```text
POST /api/v1/auth/login
POST /api/v1/auth/verify
GET  /api/v1/authenticated/getAuthorities
GET  /api/v1/userProfile/
POST /api/v1/backoffice/getAllDetailedAccounts
POST /api/v1/backoffice/account/add
```

Initial required authorities for a future Case 2 path: `AccessBackoffice`, `AddAccount`, and `GetAllDetailedAccounts`.

### F. Other cases / production

- Case 1: map into a callable Workato scaffold after reviewing local reference logic.
- Cases 3-6: create only safe stubs that return `case_not_mapped_yet` until their payload/renewal rules are documented.
- Salesforce writeback: obtain Salesforce-owner confirmation whether `accountUuid` belongs in `Surface_Account_ID__c`, `Account_UUID__c`, or both. Do not write either field beforehand.
- Add `surface_onboarding_idempotency` only when writeback/case workflow design is ready. Store masked audit data only.

## 10. Security / Stakeholder Gate

The security discussion requires a sync involving:

- Milton Stevenson - automation owner
- Or - Donatello/Surface stakeholder
- Ran Kadury - Cybersecurity
- Elad Nadler - Cybersecurity review
- Surface/BackOffice owner
- Salesforce owner/admin
- Workato contact as needed

Their decision should scope the next allowed Donatello-dev action:

> Permit an OPA-based authentication, authority, duplicate-check, and dry-run test from `172.26.37.20`; prohibit account creation and all production BackOffice access.

Separate external context noted in the prior chat: Or said the previous Donatello environment was allocated to another use and Amit might provide another environment; a support-team method to create dev users may need to be exposed through Jenkins/RBAC. Treat Donatello environment availability, user provisioning, and MFA re-enrollment as open until confirmed directly by Or/Amit. Do not rely on stale Donatello credentials.

## 11. Key Evidence and Reference Files

| File | Use |
| --- | --- |
| [README.md](../README.md) | Project index and global rules. |
| [00_PROJECT_HANDOFF.md](00_PROJECT_HANDOFF.md) | Original implementation context; some names/status are historical. |
| [01_TWO_WEEK_PLAN.md](01_TWO_WEEK_PLAN.md) | Sequencing reference; dates are historical. |
| [02_OPA_UBUNTU22_VM_REQUIREMENTS.md](02_OPA_UBUNTU22_VM_REQUIREMENTS.md) | VM/network/install reference. |
| [03_WORKATO_RECIPE_ARCHITECTURE.md](03_WORKATO_RECIPE_ARCHITECTURE.md) | Target recipe design and contracts. |
| [04_SECURITY_AND_RISK_REVIEW.md](04_SECURITY_AND_RISK_REVIEW.md) | Security requirements. |
| [05_TEST_PLAN_AND_ACCEPTANCE.md](05_TEST_PLAN_AND_ACCEPTANCE.md) | Test gates and acceptance criteria. |
| [06_DECISION_LOG_AND_OPEN_ITEMS.md](06_DECISION_LOG_AND_OPEN_ITEMS.md) | Decisions and ownership questions. |
| [07_SECURITY_REVIEW_EMAIL_THREAD.md](07_SECURITY_REVIEW_EMAIL_THREAD.md) | Or/Ran security context. |
| [08_TECHNICAL_IMPLEMENTATION_GUIDE.md](08_TECHNICAL_IMPLEMENTATION_GUIDE.md) | Concise end-to-end technical reference. |
| [09_ARCHITECTURE_RECONCILIATION.md](09_ARCHITECTURE_RECONCILIATION.md) | Reconciles legacy Slack/GAS model with Workato target. |
| [day 1 VM evidence](../outputs/day%201%20VM-%20Workado.out) | Original VM preflight output. |
| [OPA gateway test evidence](../outputs/sg3%20and%20sg4%20test.txt) | Gateway/TLS evidence. |
| [Workato installation evidence](../outputs/workato%20installation.txt) | Package version and installation transcript. |
| [Current workflow source](../diagrams/surface_workato_opa_workflow_current.mmd) | Mermaid source for target architecture. |

## 12. First Prompt for the Next Chat

Copy this into the new chat after attaching or linking this file:

```text
Read docs/10_NEW_CHAT_HANDOFF_2026-08-13.md first. I am a Workato beginner, so give every Workato instruction click-by-click: where to go, what to click, exact values, expected result, whether it is read-only, and when to stop. We are currently finishing the recipe `OPA Connectivity Test - Presales EU`. The guided HTTP setup failed only because `/backoffice` returned HTML and Workato tried to create a JSON schema. Help me finish it using manual setup with response content type Text, then validate one test job. Do not start the scheduled recipe and do not perform authentication or writes.
```

