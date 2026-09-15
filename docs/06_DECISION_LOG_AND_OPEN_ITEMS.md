# Decision Log And Open Items

## Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-06-30 | Router recipe will reproduce `review-co CO-XXX` behavior in Workato | Workato needs native source validation before onboarding |
| 2026-06-30 | Case 2 will use API-first if approved | UI captured browser-backed structured API calls |
| 2026-06-30 | Manual MFA path added for user-based flow | MFA must remain enabled |
| 2026-07-06 | Workato recommended OPA over shared IP allowlist | Shared Workato egress IPs are multi-tenant |
| 2026-07-08 | Previous VM/VPN path was not ideal for Donatello | Donatello dev returned CloudFront/WAF 403 |
| 2026-07-10 | RND Ubuntu lab is best OPA candidate | Donatello dev returned 200/401 app-layer responses |
| 2026-07-10 | Recommended model for planning is 5.6 Terra | Best balance for architecture, security, and execution planning |

## Current Open Questions

For Or/Ran/Donatello/SecOps:

```text
Is OPA from RND NAT IP 199.203.203.177 approved for Donatello dev?
Is shared Workato IP allowlisting acceptable for any dev-only fallback?
Can /api/v1/auth/token support BackOffice tenant-management endpoints?
Which authorities/scopes are required for duplicate check, account creation, license edit, and CE settings?
Is manual MFA via Workato Workflow App approved for dev POC?
What is the approval path before BO production testing?
```

For Workato:

```text
Does our subscription include OPA/on-prem connectivity?
Does the HTTP connector in our workspace support OPA cloud profiles?
If HTTP cloud profile is not available, what is the supported connection-profile pattern?
Can we add a second OPA agent later without changing recipe connections?
```

For Salesforce owner:

```text
Should accountUuid update Surface_Account_ID__c, Account_UUID__c, or both?
What status/stage values should Workato set after successful onboarding?
What exact rejection status and notes fields should be used?
```

## Future Rejection Rules Reminder

Current automatic rejection rule:

```text
Reject if Onboarding Type does not match license/subscription description or inferred motion.
```

Future rules to define:

- missing primary domain.
- invalid email domain.
- missing country.
- invalid or missing subscription dates.
- expired or inactive license.
- domain count exceeds license.
- subdomain count exceeds license.
- duplicate existing BO account.
- Salesforce approval status not approved.
- submitter/account mismatch.
- unsupported CE renewal-only motion.

## Production Readiness Reminder

Production BO is the final target, but current Donatello access is time-limited and intended for validation.

Do not run production BO writes until:

- Cybersecurity approves connectivity/auth model.
- Product/CS approve each case mapping.
- test evidence exists for each case.
- duplicate and post-write verification are implemented.
- Salesforce writeback has been validated.
- rollback/stop procedure exists.

