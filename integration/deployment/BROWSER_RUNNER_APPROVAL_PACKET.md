# Browser runner approval packet

This is a decision packet only. It authorizes no VM, browser, proxy,
Salesforce, Leonardo, Workato, OPA, or production action.

## Requested scope

Approve one separate, hardened, ephemeral browser-runner host for the
Development-only, attended UI bridge. The operator must manually complete
Salesforce and Leonardo sign-in/MFA in a streamed, per-user browser session.
The runner may report only allowlisted state transitions and must pause before
any create confirmation.

`workato-opa-01` (`172.26.37.20`) is excluded from browser-runner placement.
It remains the OPA/validator host and must not receive browser packages,
profiles, operator sessions, credentials, or session artifacts.

## Required owner decisions

| Owner | Decision/evidence required |
| --- | --- |
| SecOps | Separate host, OS hardening, EDR, patching, no public ingress, restricted administrator access, egress allowlist limited to approved Development destinations, and runner destruction verification. |
| Identity | Corporate SSO/MFA for the control portal, exact pilot group, session/re-authentication duration, streamed-session binding, and emergency access revocation. |
| Data/Operations | Approved KMS/HSM envelope-encryption service, retention period, cryptographic deletion, monitoring, incident response, backup/restore policy, and change/rollback owner. |
| Leonardo owner | Development-only attended UI automation scope, permitted readback, selector-change/support process, and explicit prohibition of production navigation. |
| Application Security | Browser-isolation, CSRF, one-time grant, redacted logging, timeouts, concurrency, disconnect cleanup, and negative-test review. |

## Non-negotiable controls

- One browser context per authenticated operator and approved correlation ID.
- Fresh non-persistent context for every run; destroy it on completion,
  timeout, disconnect, or failure.
- No saved password, MFA code, cookie, bearer token, local/session storage,
  browser profile, HAR, trace, video, screenshot, or page HTML retention.
- The runner receives only an opaque grant and an encrypted manifest reference;
  plaintext is released once after manual login evidence and never logged.
- No shared account, basic authentication, TLS/WAF bypass, CAPTCHA handling,
  or automatic retry of an uncertain create.
- Production hostname is blocked at DNS, egress, browser policy, and
  application origin validation layers.
- The proxy/control portal may never connect to Leonardo directly; only the
  approved runner may do so after a one-time grant.

## Acceptance tests before activation

1. A non-pilot, expired, unauthenticated, forged-header, or replayed grant is
   rejected without creating a browser context.
2. Manual login must occur in the streamed browser; no credential input exists
   in the control portal or runner API.
3. Only Salesforce and Leonardo Development destinations are accepted; a
   production destination is rejected before navigation.
4. Browser context and encrypted manifest reference are destroyed at expiry,
   disconnect, completion, and error.
5. Logs contain only correlation IDs and bounded state categories.
6. Confirm/create remains unavailable to automation and requires the existing
   per-run human confirmation.

## Current local evidence

`phase2_leonardo/browser_runner_control.py` is a pure-local model. It has no
browser, network client, persistence, or cryptographic implementation. Its
`EnvelopeProtector` is an interface that requires an approved KMS/HSM adapter;
the test double is intentionally non-cryptographic and cannot be deployed.
