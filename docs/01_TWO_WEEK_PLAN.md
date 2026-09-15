# Two-Week Implementation Plan

Schedule assumption: two-hour sessions, Monday through Friday, starting Monday 2026-07-13 and ending Friday 2026-07-24. If blockers are cleared quickly, this can be compressed into one week by combining adjacent sessions.

## Week 1

### Day 1 - Monday 2026-07-13: VM And Security Readiness

Goal: create the new Ubuntu 22 RND VM and confirm it is safe to host OPA.

Tasks:

1. Create Ubuntu 22 VM on the RND VPN path.
2. Assign stable hostname, for example:

```text
workato-opa-rnd-01
```

3. Confirm the VM has a stable internal IP or reservation.
4. Confirm outbound NAT IP; expected RND path is:

```text
199.203.203.177
```

5. Apply OS updates.
6. Enable time sync.
7. Install minimal tools:

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates gnupg wget dnsutils netcat-openbsd openssl jq
```

8. Run `scripts/ubuntu_opa_preflight.sh`.
9. Save the output in the project evidence folder.
10. Send the NAT IP and validation result to Or/Ran if they need the Cybersecurity review.

Exit criteria:

- VM exists on RND VPN.
- `sg3.workato.com` and `sg4.workato.com` reachable on TCP 443.
- Donatello dev returns `200` for root and `401` for unauthenticated API checks.
- No TLS interception blocks Workato gateway connectivity.

### Day 2 - Tuesday 2026-07-14: Workato OPA Group And Agent Install

Goal: install and activate Workato OPA on the RND VM.

Tasks:

1. Confirm Workato workspace has OPA/on-prem connectivity entitlement.
2. In Workato, create an on-prem group:

```text
surface-onboarding-rnd-opa
```

3. Add a Linux DEB agent:

```text
surface-rnd-opa-01
```

4. Follow Workato Linux DEB install steps.
5. Activate using the one-hour activation command from Workato.
6. Start and enable the service:

```bash
sudo systemctl start workato-agent.service
sudo systemctl enable workato-agent.service
sudo systemctl status workato-agent.service
```

7. Click `Test agent` in Workato.
8. Confirm the agent is active.
9. Record certificate expiration date and set a renewal reminder 45 days before expiry.

Exit criteria:

- OPA agent is active in Workato.
- Agent service auto-start is enabled.
- Certificate/key handling is documented.

### Day 3 - Wednesday 2026-07-15: OPA-Backed HTTP Connection

Goal: create the Workato connection that sends Donatello traffic from the OPA VM.

Tasks:

1. Create Workato HTTP connection:

```text
Donatello Dev HTTP via RND OPA
```

2. Base URL:

```text
https://donatello.dev.app.pentera.io
```

3. Authentication type:

```text
None
```

4. Select OPA/on-prem group:

```text
surface-onboarding-rnd-opa
```

5. Confirm the HTTP connector supports OPA cloud profile in this workspace.
6. If not visible, ask Workato to confirm the correct connector/profile setup.
7. Create optional second connection for final BO testing, but do not use it for writes yet:

```text
BO Prod HTTP via RND OPA
https://app.pentera.io
```

8. Keep Donatello/BO credentials in environment properties, not the connection.

Exit criteria:

- Donatello dev OPA-backed HTTP connection exists.
- It points only to the approved base URL.
- No secrets are stored in connection comments or VM files.

### Day 4 - Thursday 2026-07-16: Router Hardening And Review Output

Goal: make the Router the single source-review gate for all six cases.

Tasks:

1. Clone or version the existing Router recipe before edits.
2. Confirm trigger strategy:
   - Phase 1: manual/API trigger for testing.
   - Phase 2: Salesforce new/updated CO trigger when ready.
3. Extend Router output to include:
   - `co_number`
   - `sf_record_id`
   - `account_name`
   - `account_country`
   - `main_domain`
   - `email_domains`
   - `alternate_domains`
   - `onboarding_product`
   - `onboarding_type`
   - `surface_account_id`
   - `account_uuid`
   - `surface_subscription`
   - `credential_exposure_subscription`
   - `core_subscription`
   - `engine_value`
   - `review_summary`
   - `rejection_reasons`
4. Implement current auto-rejection rule:

```text
Reject if Onboarding Type does not match inferred license/subscription motion.
```

5. Add a future TODO/reminder in recipe notes:

```text
Define additional rejection rules before production rollout.
```

6. Add a clear response structure for approved, rejected, blocked/manual-review, and error states.

Exit criteria:

- Router can classify all six target cases as engine values or known blocked/manual states.
- Router can reject mismatch cases without calling onboarding recipes.
- Output is readable enough for human approval.

### Day 5 - Friday 2026-07-17: Human Approval Gate

Goal: add a Workato-native approval checkpoint after review and before onboarding.

Tasks:

1. Choose approval mechanism:
   - Preferred: Workato Workflow Apps task.
   - Alternative: Slack approval only if approved and auditable.
2. Create approval app/task:

```text
Surface Onboarding Approval
```

3. Show non-sensitive review details:
   - CO number
   - account name
   - case type
   - product motion
   - license summary
   - rejection/block status
   - masked domain summary if needed
4. Do not show full secrets, tokens, passwords, or raw API responses.
5. Branch:
   - Approved -> call case recipe.
   - Rejected by reviewer -> update Salesforce with rejection notes.
   - Expired/no response -> stop as `approval_timeout`.
6. Keep task expiration minimal but realistic, for example 1 business day for review and 5 minutes only for MFA tasks.

Exit criteria:

- Workato asks whether to proceed after source review.
- Onboarding cannot run without approval.
- Audit trail shows who approved and when.

## Week 2

### Day 6 - Monday 2026-07-20: Case 2 Through OPA

Goal: unblock and complete Case 2 auth + duplicate-check path through OPA.

Tasks:

1. Update `Manual Case 2 - Donatello API` Step 3 to use:

```text
Donatello Dev HTTP via RND OPA
```

2. Retest login.
3. Expected next result:
   - MFA-required response, or
   - Donatello auth error, or
   - token response if already authenticated.
4. Confirm CloudFront `403` is gone.
5. Preserve MFA.
6. Test `/api/v1/auth/token` only if Or/Ran approve.
7. Add authority check:

```text
GET /api/v1/authenticated/getAuthorities
```

8. Add duplicate check using:

```text
POST /api/v1/backoffice/getAllDetailedAccounts
```

9. Stop before create until duplicate-check behavior is validated.

Exit criteria:

- Case 2 reaches Donatello app/API layer through OPA.
- MFA is preserved.
- Authority check and duplicate check are built or ready for first validation.

### Day 7 - Tuesday 2026-07-21: Case 1 Workato Mapping

Goal: map existing Case 1 local logic into Workato recipe structure.

Tasks:

1. Review local Case 1 logic from the existing package.
2. Create callable recipe:

```text
Manual Case 1 - Surface New Account
```

3. Define input contract from Router.
4. Add normalization:
   - company name
   - domain lists
   - country
   - primary user
   - license dates
   - license limits
5. Add duplicate check against BackOffice/Donatello dev equivalent.
6. Add dry-run payload builder.
7. Do not enable create until endpoint/payload contract is validated.

Exit criteria:

- Case 1 has a callable Workato recipe scaffold.
- Input/output contract matches Router.
- Duplicate and dry-run gates are defined.

### Day 8 - Wednesday 2026-07-22: Cases 3-6 Design And Gating

Goal: define safe contracts for combined and renewal cases before building writers.

Tasks:

1. Create recipe stubs for:
   - Case 3 New Surface + New CE
   - Case 4 Renew Surface + New CE
   - Case 5 New Surface + Renew CE
   - Case 6 Renew Surface + Renew CE
2. For each case, document:
   - required Salesforce fields
   - required existing IDs
   - required subscriptions
   - duplicate/renewal detection
   - target BackOffice actions
   - expected output UUID
3. For cases 4-6, require existing Surface or CE IDs where renewals are expected.
4. Block all write actions until HAR/API/UI mapping exists.
5. Add Workato Router branches that stop with `case_not_mapped_yet` for unbuilt cases.

Exit criteria:

- Router can identify all six cases.
- Cases 3-6 are safely blocked until mapped.
- No unsupported case can accidentally create or update an account.

### Day 9 - Thursday 2026-07-23: Salesforce Writeback And Audit

Goal: update Salesforce only after verified BackOffice/Donatello success.

Tasks:

1. Confirm exact Salesforce field with SF owner:
   - user request says update `Surface Account ID`.
   - local review logic includes `Surface_Account_ID__c` and `Account_UUID__c`.
2. Implement writeback only after post-create verification.
3. Store created BackOffice UUID from response field:

```text
accountUuid
```

4. Update Salesforce:

```text
Surface_Account_ID__c = accountUuid
```

unless SF owner confirms another target field.
5. Optionally update onboarding stage/status after approval:

```text
User Created
```

6. Add lookup table:

```text
surface_onboarding_idempotency
```

7. Add masked audit result:
   - CO number
   - case type
   - Salesforce ID
   - Workato job IDs
   - created account UUID
   - domain hash
   - status
   - failure category
8. Never log passwords, MFA codes, bearer tokens, full request bodies, or full customer payloads.

Exit criteria:

- Salesforce update path exists but runs only after verification.
- Idempotency prevents duplicate creates.
- Audit data is useful and sanitized.

### Day 10 - Friday 2026-07-24: End-To-End Test Readiness

Goal: prepare for stakeholder testing/demo.

Tasks:

1. Run end-to-end dry run for CO-0596 in Donatello dev:
   - review
   - approval
   - Case 2 duplicate check
   - dry-run create payload
   - no submit unless approved
2. Run negative tests:
   - missing CO
   - duplicate CO result
   - mismatched onboarding type/license motion
   - unsupported case
   - duplicate account
   - approval denied
   - MFA timeout
3. Validate logs are masked.
4. Validate OPA service status and restart behavior.
5. Review security pack with Ran/Or.
6. Decide whether to perform one controlled Donatello dev create.
7. Prepare short demo script and rollback plan.

Exit criteria:

- Ready for controlled Donatello dev test.
- Production BO remains blocked until explicit approval.
- Stakeholders have a clear security and operations story.

## Compressing The Plan

If OPA installation and approval are fast, combine:

- Day 1 + Day 2 into one longer infrastructure session.
- Day 4 + Day 5 into one Router/approval session.
- Day 7 + Day 8 into one design session for Case expansion.

The shortest safe path to first Donatello dev test is:

1. RND OPA VM.
2. OPA-backed HTTP connection.
3. Case 2 auth through OPA.
4. Duplicate check.
5. Dry-run payload.
6. Controlled create only after approval.

