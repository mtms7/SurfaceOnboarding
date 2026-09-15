# VM portability baseline — Phase 2

**Date:** 2026-09-15  
**Status:** local source refactor only; no VM, service, proxy, Salesforce, Leonardo, Workato, or OPA action occurred.

## Goal

Move the complete onboarding application to Ubuntu 22.04 on
`workato-opa-01` (`172.26.37.20`) while preserving the current desktop mode
as a temporary developer test tool. The destination application includes the
queue dashboard, source validators, Salesforce-read contract, manual-review
state, renewal preflight, and masked operational audit.

## Runtime boundary

The dashboard now resolves its Salesforce CLI command by platform:

- Windows desktop pilot: `sf.cmd`.
- Ubuntu 22 service: `sf`.
- An explicit `SURFACE_SF_CLI` setting may select an approved executable.

Ubuntu service mode must set `SURFACE_ONBOARDING_RUNTIME=vm`. In this mode,
the application refuses to launch a local browser, Salesforce SSO/MFA, or
Leonardo/production browser route. This is intentional: a server process
cannot safely use an operator's desktop session or receive credentials.

## Target placement

| Component | Ubuntu placement | Identity / state |
| --- | --- | --- |
| Dashboard and validators | `/opt/surface-onboarding/app` | `surface-onboarding`, separate from `workato-agent` |
| Web listener | `127.0.0.1:8000` | approved TLS/SSO proxy only |
| Runtime state | `/var/lib/surface-onboarding` | `0750`, masked metadata only |
| Salesforce source access | future approved service identity | fixed-query, least privilege, no human CLI profile |
| Manual SSO/MFA browser work | separate approved ephemeral runner | per-run temporary context; no retained browser state |
| Workato OPA | existing agent service | unchanged and isolated |

## Deployment gates that remain blocked

1. VM/SecOps approves the separate service identity and VM colocated-service
   posture.
2. Identity/SecOps approves the TLS/SSO proxy and your single-user pilot
   identity.
3. Salesforce approves the service read identity and exact scopes.
4. SecOps/Application Security approves the separate streamed browser runner
   for manual SSO/MFA. It must not be colocated with `workato-agent`.
5. Operations approves persistence, encryption, backups, monitoring,
   rollback, and polling schedule.

These gates do not prevent local portability work; they prevent VM deployment
or external access activation.
