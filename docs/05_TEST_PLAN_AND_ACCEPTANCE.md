# Test Plan And Acceptance Criteria

## Test Layers

1. VM/network.
2. OPA agent.
3. OPA-backed Workato HTTP connection.
4. Router/review logic.
5. Approval gate.
6. Case recipe duplicate check.
7. Case recipe dry-run payload.
8. Controlled Donatello dev create.
9. Post-create verification.
10. Salesforce writeback.
11. Negative and security tests.

## Network Acceptance

From the Ubuntu 22 VM:

```text
sg3.workato.com resolves and TCP 443 succeeds
sg4.workato.com resolves and TCP 443 succeeds
donatello.dev.app.pentera.io returns HTTP 200 for root
donatello.dev.app.pentera.io API endpoints return 401 when unauthenticated
outbound NAT IP is documented
```

Bad results:

```text
DNS failure
timeout
TLS interception error that prevents OPA
CloudFront/WAF 403 for Donatello dev API
```

## OPA Acceptance

In Workato:

- OPA group exists.
- Agent is active.
- `Test agent` succeeds.
- Service is enabled on Ubuntu:

```bash
systemctl is-enabled workato-agent.service
systemctl is-active workato-agent.service
```

## Router Acceptance

Positive test:

```json
{
  "co_number": "CO-0596"
}
```

Expected:

- exactly one Salesforce CO.
- subscriptions returned.
- engine value identified.
- review summary generated.
- no write action before approval.

Negative tests:

- invalid CO.
- duplicated/ambiguous result.
- missing required fields.
- Onboarding Type/license mismatch.
- unsupported case.

## Approval Acceptance

Expected:

- task is created with non-sensitive details.
- approved path calls case recipe.
- rejected path updates Salesforce as rejected/blocked.
- timeout path stops safely.
- approver and timestamp are recorded.

## Case 2 Acceptance

Step 1: OPA login reachability.

Expected:

- no CloudFront/WAF `403`.
- response reaches Donatello auth/MFA layer.

Step 2: MFA.

Expected:

- MFA remains enabled.
- manual task collects code if needed.
- MFA code is not logged.

Step 3: authority check.

Required authorities:

```text
AccessBackoffice
AddAccount
GetAllDetailedAccounts
```

Step 4: duplicate check.

Expected:

- exact account name duplicate blocks create.
- exact primary domain duplicate blocks create for new-account flows.
- result is logged only as pass/fail plus masked summary.

Step 5: dry-run create payload.

Expected:

- payload is built.
- masked preview is reviewed.
- no submit before explicit approval.

Step 6: controlled create.

Expected only after approval:

- create returns success.
- `accountUuid` is present.
- read-after-write confirms settings.
- Workato returns structured success.

## Salesforce Writeback Acceptance

Expected:

- writeback runs only after verified create/update.
- writes `accountUuid` to the approved Salesforce field.
- initial assumption: `Surface_Account_ID__c`.
- confirm whether `Account_UUID__c` also needs update before enabling.

Post-write verification:

- read the CO record again.
- confirm field value matches created account UUID.
- update idempotency table.

## Security Acceptance

Review Workato job logs and lookup tables:

- no password.
- no MFA code.
- no bearer token.
- no full API request body.
- no full API response.
- no raw cookies.
- no raw credential files.

## Demo Readiness

The project is ready for stakeholder testing when:

- OPA is active on RND Ubuntu 22.
- Router source review works.
- approval gate works.
- Case 2 auth reaches Donatello through OPA.
- duplicate check works.
- dry-run create payload is reviewed.
- negative tests stop safely.
- security controls are documented.

