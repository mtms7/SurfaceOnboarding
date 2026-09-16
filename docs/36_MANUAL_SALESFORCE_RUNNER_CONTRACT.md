# Manual Salesforce runner contract — draft

**Date:** 2026-09-15  
**Status:** design only. No runner, endpoint, browser, Salesforce CLI profile,
service, proxy, certificate, or authentication flow has been installed or
activated.

## Purpose

The Surface dashboard will ultimately run on `workato-opa-01`
(`172.26.37.20`) as the `surface-onboarding` identity. Salesforce sign-in uses
human SSO and MFA, so it cannot safely occur in that service or on the
Workato OPA host. A separate, single-user runner is required for Milton's
attended pilot.

## Non-negotiable separation

| Component | Placement | Prohibited state |
| --- | --- | --- |
| Dashboard | `172.26.37.20`, `surface-onboarding`, loopback only | Browser profile, Salesforce CLI session, password, MFA code, cookie, token |
| Workato OPA | Existing isolated agent | Any browser or Salesforce user session |
| Manual runner | Separately approved ephemeral host/VM, Milton only | Persistent browser profile, shared/team account, unmasked source logs |

The manual runner must not be co-located with `workato-agent`. The dashboard
is not allowed to launch a browser in VM mode; its current refusal is an
intentional enforcement of this rule.

## Attended flow

1. The dashboard displays **Runner unavailable** until an approved runner is
   provisioned and healthy.
2. Milton opens the runner's browser and completes normal Salesforce SSO/MFA.
   Credentials and MFA are entered only into the Salesforce browser page.
3. The runner proves session availability locally with `sf org display` (or a
   future approved equivalent). It discards stdout/stderr and returns only a
   short-lived readiness result.
4. On a dashboard request, the runner accepts only an allowlisted operation
   for the current user and returns the minimum validated CO fields needed for
   rendering. It never exposes a generic SOQL endpoint or raw CLI output.
5. The runner invalidates readiness on timeout, logout, source error, runner
   restart, or any identity mismatch. The dashboard then returns to **Runner
   unavailable** and shows no cached Salesforce data.

## Minimum runner-to-dashboard protocol

The protocol is intentionally not implemented until the owner approvals below
are recorded. It must have all of these properties:

- Mutual TLS between named runner and dashboard identities; no bearer token,
  cookie forwarding, or shared local filesystem.
- Loopback listener on the dashboard, with any forwarding/proxy separately
  approved. No broad inbound listener or OPA tunnel reuse.
- Fixed operation identifiers, not arbitrary commands, SOQL, URLs, or file
  paths. Initial identifiers are `open_onboardings_read`,
  `co_detail_read`, and `dealhub_term_read`.
- Strict request schema: opaque operator subject, one valid `CO-` reference
  where applicable, request nonce, source revision binding, and short expiry.
- Strict response schema: validated fields only, no CLI output, browser URL,
  token, cookie, password, MFA value, account PDF, or raw Salesforce payload.
- A signed/attested health result with `runner_id`, `operator_subject`,
  `observed_at`, `expires_at` (maximum 15 minutes), and state only:
  `unavailable`, `ready`, or `failed`.
- In-memory data handling by default. Any future masked audit metadata needs
  separate retention, encryption-at-rest, backup, and access approval.

## Encryption and retention

The secure solution is to avoid carrying session material rather than encrypt
and copy it. Browser cookies, CLI authentication files, private keys,
passwords, and MFA values remain in the runner's ephemeral user context and
are deleted at session end. They must never be transferred to the dashboard,
OPA, Git, logs, `var/lib`, or backups.

If a future approved audit store is needed, it may contain only masked,
non-secret operational metadata and must use the organization's managed
encryption-at-rest/key-management platform. Local ad-hoc encryption keys on
the VM are not an approved substitute.

## Required approvals before implementation or activation

1. Identity/SecOps approves the separate runner host, Milton-only access, and
   its lifecycle/ephemeral cleanup design.
2. Salesforce approves the CLI/app registration, exact read scopes, and
   allowlisted query contract.
3. Security approves mutual-TLS identity issuance, proxy/forwarding topology,
   logs, monitoring, retention, and incident response.
4. The automation owner approves the exact first read-only pilot and rollback
   path.

Until all four are recorded, the dashboard remains unable to read Salesforce
from the VM and must fail closed. This contract grants no authority to install
software, authenticate, access Salesforce, change OPA, or start a service.
