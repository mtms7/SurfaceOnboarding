# Security And Risk Review

## Preferred Connectivity Model

Preferred:

```text
Workato Cloud -> OPA outbound tunnel -> RND Ubuntu VM -> Donatello/BO
```

Why:

- Donatello/BO traffic originates from a trusted internal/VPN path.
- Workato OPA requires outbound-only TCP 443 from the VM to Workato.
- No inbound firewall ports are required for Workato.
- Avoids exposing administrative BackOffice endpoints to shared Workato cloud egress IPs.
- Fits Workato support recommendation.

## Alternative Connectivity Models

### Shared Workato IP Allowlist

Workato US egress IPs:

```text
52.5.142.59
34.226.132.221
52.54.43.157
```

Risk:

- Workato confirmed these are shared multi-tenant IPs.
- Allowlisting them to administrative BackOffice endpoints expands trust to shared infrastructure.

Use only if Ran/Cybersecurity approve it for a scoped dev-only test.

### Service-To-Service API

Preferred long-term if Donatello supports it:

```text
/api/v1/auth/token
```

Requirements:

- Dedicated automation identity.
- Scoped permissions.
- Token rotation.
- Stable API contract.
- Auditability.
- No MFA bypass for human users.

Or noted this official auth method may not work with the internal BackOffice endpoints, but testing is welcome.

## MFA Position

MFA must remain enabled for any user-based Workato/BackOffice account.

Allowed for current dev POC:

- User login through OPA.
- Manual MFA code entered into a Workato task.
- Short task expiration for MFA.
- Token stored only in job memory/variables.

Not allowed:

- Removing MFA from the user.
- Logging MFA codes.
- Storing MFA codes in lookup tables.
- Sending MFA codes through Slack/chat/email.

Future option only after security approval:

- Dedicated automation account with approved service auth.
- Or approved TOTP secret stored in Workato environment properties, if Ran/SecOps explicitly approves.

## Secret Handling

Store in Workato environment properties or secure connection settings:

```text
DONATELLO_BASE_URL
DONATELLO_BACKOFFICE_EMAIL
DONATELLO_BACKOFFICE_PASSWORD
BO_BASE_URL
BO_BACKOFFICE_EMAIL
BO_BACKOFFICE_PASSWORD
```

Do not store:

- passwords on the OPA VM.
- MFA codes in files.
- bearer tokens in lookup tables.
- full request/response payloads in logs.
- screenshots containing secrets.

## Logging Rules

Allowed:

- CO number.
- Salesforce record ID.
- case type.
- Workato job IDs.
- account UUID after creation.
- masked account name if needed.
- domain hash.
- pass/fail status.
- failure category.

Not allowed:

- passwords.
- MFA codes.
- tokens.
- full customer payloads.
- full API responses.
- cookies.
- local credential files.

## Runtime Fail-Closed Conditions

Stop immediately if:

- no CO record found.
- more than one CO record found.
- Onboarding Type/license motion mismatch.
- unsupported case.
- missing required domain/country/license date.
- duplicate account name.
- duplicate primary domain where new account is expected.
- missing BackOffice authority.
- auth fails.
- WAF/CloudFront block.
- unexpected response schema.
- OPA inactive.
- approval denied or timed out.

## VM Controls

Required:

- Ubuntu 22 patched.
- restricted admin access.
- SSH restricted.
- OPA service enabled and monitored.
- outbound-only Workato access.
- no inbound Workato ports.
- TLS inspection bypass only for Workato OPA gateway FQDNs.
- certificate renewal reminder.

Recommended:

- second OPA agent later for HA/load balancing.
- centralized log collection.
- endpoint security/EDR.
- documented change control.

## Production BO Controls

Before production BO writes:

1. Ran/Or approve final connectivity path.
2. Product/CS approve case mappings.
3. One controlled Donatello dev create passes.
4. Duplicate and read-after-write checks pass.
5. Salesforce writeback is verified in sandbox or approved dev path.
6. Logs are reviewed and confirmed masked.
7. Rollback/stop procedure is documented.

