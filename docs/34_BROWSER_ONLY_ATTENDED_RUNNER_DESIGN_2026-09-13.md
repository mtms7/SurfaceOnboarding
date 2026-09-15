# Browser-only attended runner design

**Status:** Local control-plane mock only. No browser, runner, proxy, VM
service, Salesforce, Leonardo, or Workato action is enabled by this document.

## Objective

Support an attended, browser-only bridge when Leonardo has no approved service
API. The operator completes Salesforce and Leonardo sign-in/MFA in a browser.
The application must never receive, log, export, or persist passwords, MFA
codes, cookies, tokens, browser storage, HARs, traces, screenshots, or page
HTML.

## Placement

`workato-opa-01` (`172.26.37.20`) remains the OPA/validator host. It must not
run Chromium, Playwright, a reusable browser profile, or an operator browser
session. The browser runner is a separate hardened, ephemeral host. The OPA
host may later run only the SSO-protected control portal after its independent
activation approvals are recorded.

## Flow

1. Corporate proxy authenticates the operator with SSO/MFA and pilot-group
   authorization.
2. The control portal validates a current, separately approved opaque job and
   creates one short-lived runner grant.
3. The runner opens a fresh non-persistent browser context and streams it only
   to that authenticated operator.
4. The operator manually signs in to the approved Salesforce or Leonardo
   Development page and completes MFA in the browser.
5. The runner reports only an allowlisted destination-complete outcome; it
   never returns browser state.
6. The operator-controlled, separately approved attended UI flow may continue.
   Confirm/create remains a human-only action.
7. On completion, timeout, disconnect, or error, the runner destroys the
   browser context and invalidates the grant. It must not retry uncertain
   creation.

## Sensitive data

Browser authentication state is memory-only and never encrypted for later
reuse. If a restricted execution manifest must persist before a one-time
release, it requires authenticated envelope encryption through an approved
KMS/HSM, distinct per-record data keys, short retention, audit-only opaque
correlation IDs, and cryptographic deletion after use. The local code exposes
only an `EnvelopeProtector` interface; its test double is not cryptography and
cannot be deployed.

## Required approvals before any deployment

- SecOps: separate runner host, EDR/patching, egress allowlist, no public
  ingress, session streaming controls, and destruction verification.
- Identity: proxy registration, corporate SSO/MFA, session/reauthentication
  policy, and named pilot group.
- Data/Operations: KMS/HSM, retention, monitoring, incident response, and
  tested rollback.
- Leonardo owner: explicit approval of attended Development browser automation
  and readback behavior.

No approval above authorizes Salesforce writeback, production access, or a
Leonardo create without the existing per-run confirmation gates.
