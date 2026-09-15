# Reverse-proxy security approval packet

This packet is a review artifact, not a proxy configuration or activation
runbook. No reverse proxy may be installed, enabled, or exposed from this
repository. The direct onboarding application remains loopback-only at
`127.0.0.1:8000` until every applicable approval is recorded.

## Objective and non-negotiable boundary

The proxy is the only future ingress to the five-operator onboarding queue.
It must be operated as a corporate security component separate from the
application and the Workato OPA. It may forward requests only to the local
loopback listener after enterprise TLS and SSO/MFA enforcement.

The intended operator network is the internal RND VPN. Its Israeli location
does not itself authorize a request: SecOps must still approve the exact VPN
source ranges, and the proxy and application must enforce the individual
identity and authorization controls below.

It must not expose the Python service directly, terminate unauthenticated
traffic to the application, use a shared password, bypass MFA, or reuse OPA
files, certificates, service identities, browser state, or network policy.

## Required architecture decisions

The Identity and SecOps owners must record these values outside the repository
before a configuration is produced:

| Decision | Required recorded evidence |
| --- | --- |
| Public/internal hostname | Approved DNS name, ownership, and network zone |
| TLS | Corporate certificate owner, issuance method, renewal monitoring, minimum TLS policy, and approved cipher policy |
| SSO/MFA | Identity provider, application registration, MFA policy, session lifetime, reauthentication policy, and break-glass owner |
| Operator authorization | Exact group or allow-list for the five pilot operators, joiner/mover/leaver process, and review cadence |
| Identity propagation | A documented, integrity-protected user/role/correlation contract; header names and validation rules are owner-approved, not guessed |
| Network policy | Approved internal RND-VPN source ranges, proxy-to-loopback path, and default-deny egress policy |
| Operations | Patch owner, monitoring, incident owner, log retention, backup/restore owner, change/rollback process |

## Mandatory proxy controls

1. Terminate corporate TLS at the approved proxy. Redirect HTTP to HTTPS only
   when the approved network zone requires HTTP at all; otherwise do not bind
   HTTP.
2. Require corporate SSO with MFA before any application route, including
   queue views, synchronization requests, health information, and static
   assets. Do not use Basic authentication or an application-local password.
3. Remove every client-supplied identity, role, forwarding, and correlation
   header before forwarding. Add only the documented proxy-authenticated
   identity contract. The application must reject absent, duplicate, malformed,
   unsigned, expired, or unauthorized identity context.
4. Limit access to the approved pilot group and deny by default. A group match
   is not sufficient until the application has a role mapping and audit policy
   approved for that exact identity contract.
5. Permit the proxy upstream only to `127.0.0.1:8000`; never to a wildcard
   address, an Internet address, OPA, Workato, Salesforce, Leonardo, or
   BackOffice.
6. Enforce request-size, header-size, connection, read, and idle time limits;
   rate-limit authentication and state-changing requests; and use an approved
   WAF policy where required by SecOps.
7. Set security headers compatible with the final UI: HSTS after hostname/TLS
   approval, `X-Content-Type-Options: nosniff`, restrictive framing policy,
   referrer minimization, and an owner-reviewed Content Security Policy.
   Header values must be tested with the real SSO flow; do not copy generic
   snippets into production.
8. Disable proxy caching for authenticated and state-changing routes. Avoid
   access-log fields that include authorization, cookies, query secrets, or raw
   customer values. Use a generated correlation ID and masked failure category.
9. Do not proxy generated documentation, debug routes, metrics, or health
   detail publicly. Their exact visibility and authentication policy require
   separate review.
10. Preserve an emergency disable control at the proxy. Disabling ingress must
    not stop, alter, or reconfigure `workato-agent`.

## Application dependencies that remain blocked

The current scaffold deliberately cannot validate a proxy identity or start a
web listener. Before activation, implement and independently review:

- strict authenticated-identity and role schema validation;
- a complete, time-bounded `WebActivationAttestation` with opaque approval
  references for host/SecOps, TLS/SSO/MFA, the five-user pilot, secret
  handling, operations, and the Development-only scope;
- server-side session and CSRF controls for every state-changing route;
- authorization enforcement independent of UI visibility;
- redacted audit logging and correlation propagation;
- request-body validation, rate limits, and safe error responses;
- availability, alerting, incident response, and tested rollback procedures.

No proxy approval grants a Salesforce read, Salesforce writeback, Leonardo
authentication, Leonardo action, database migration, or production action.

## Security acceptance evidence

SecOps should retain test evidence demonstrating all of the following before
activation:

- direct connections to port 8000 are impossible from every non-local network;
- unauthenticated, non-MFA, expired-session, non-pilot, and forged-header
  requests are denied without reaching application authorization;
- a permitted pilot identity is propagated exactly once and maps to the
  approved least-privilege application role;
- client-supplied forwarding and identity headers are stripped or replaced;
- TLS certificate chain, hostname validation, renewal monitoring, and security
  headers meet the recorded corporate standard;
- request and connection limits behave as designed without leaking sensitive
  data in logs or error pages;
- proxy logs, application logs, and monitoring contain no credentials, cookies,
  MFA material, raw Salesforce data, or raw Leonardo data;
- disabling ingress is documented, tested in a non-production environment, and
  leaves the OPA service unaffected.

## Required approvals

| Owner | Approval required |
| --- | --- |
| VM owner and SecOps | Colocation, host hardening, proxy package/source, network controls, and monitoring |
| Identity owner | SSO registration, MFA and session policy, exact pilot group, and identity-propagation contract |
| Application security | CSRF/session/authorization design, logging policy, threat model, and security test evidence |
| Operations | Change window, alerting, incident process, backup/restore, rollback, and on-call ownership |
| Product/Surface/Salesforce owners | Separate approvals for external identities, route mappings, and any later workflow actions |

Until these approvals and evidence exist, retain the current state: no proxy,
no public hostname, no service activation, and no listener beyond loopback.
