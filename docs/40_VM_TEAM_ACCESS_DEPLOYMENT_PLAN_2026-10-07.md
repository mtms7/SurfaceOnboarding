# VM team-access deployment plan (dashboard on workato-opa-01)

**Date:** 2026-10-07
**Status:** PROPOSAL ONLY. Nothing here is approved or installed. No VM, proxy, certificate, Salesforce, Leonardo, Workato, or OPA action is authorized by this document. Every gate in `docs/35`, `docs/36`, and `integration/deployment/*` stays in force; this plan adds detail and flags where the owner goal changes them (see "Conflicts").

**Owner goal (2026-10-07):** the owner and teammates open the onboarding dashboard in a browser at `https://172.26.37.20:<port>/` with a login page, like the Pentera appliance at `https://172.26.37.12:8181/login`.

## 1. Target experience

| Item | Proposal |
| --- | --- |
| URL | `https://172.26.37.20:8443/` (8181 is Pentera's on .12; confirm 8443 is free on the VM, and not used by OPA, only after approval) |
| Login | Login page from the SSO layer (OneLogin), then the dashboard. No dashboard-local passwords, no Basic auth (SECURITY_BASELINE) |
| Access | Named allow-list of team emails; roles `viewer` and `operator` (section 2) |
| TLS, preferred | Certificate from the internal CA for a DNS name (or IP SAN) owned by VM/SecOps; browsers trust it silently |
| TLS, pilot fallback | Self-signed, like the Pentera appliance. Trade-off: every teammate sees a browser warning and learns to click through, which weakens phishing resistance, and `Secure` cookies/HSTS are awkward. Acceptable only for a short pilot with SecOps written acceptance; importing the cert into team browsers removes the warning |

## 2. Architecture

```
browser --HTTPS 8443--> nginx (VM IP) --auth_request--> oauth2-proxy (127.0.0.1:4180) --OIDC--> OneLogin
                           |  on success, adds verified identity header
                           +--HTTP--> dashboard 127.0.0.1:8000  (unchanged loopback bind)
```

- **Proxy:** nginx (or Caddy) on the VM under its own non-login user, TLS terminated on the VM IP only. It strips every client-supplied identity, role, and forwarding header, then sets only the approved ones. Upstream is `127.0.0.1:8000` only. HTTP is not bound.
- **Authentication:** OneLogin OIDC via oauth2-proxy (or equivalent), MFA per corporate policy, email allow-list or OneLogin group, deny by default.
- **Identity to the dashboard:** the proxy passes a verified email and role. The dashboard trusts them only when the TCP peer is loopback and exactly one well-formed value is present; anything else fails closed. Header names/format are owner-approved, not guessed (REVERSE_PROXY_APPROVAL_PACKET).
- **Guards kept:** `scripts/run_vm_dashboard.sh` still refuses to start without `SURFACE_ONBOARDING_RUNTIME=vm`, the approved-identity marker, and a `127.0.0.1` bind.
- **Roles (proposed):** `viewer` = queue, CO pages, production readiness, search. `operator` additionally sees Start/confirm actions (disabled on the VM in phase 1 anyway, section 4).

### Dashboard changes required (none implemented yet)

Today the dashboard has a single allowed user (`SURFACE_DASHBOARD_ALLOWED_USERS`, default the owner) and a login built on Salesforce SSO through the operator's local `sf` CLI (`tools/serve_attended_open_onboardings_dashboard.py`; cookie is `HttpOnly; SameSite=Strict`). That cannot work on a server.

| # | Change | Why |
| --- | --- | --- |
| 1 | New VM login mode that trusts the proxy identity header from loopback only; remove the Salesforce-CLI sign-in in VM mode | No human `sf` profile on the server |
| 2 | Role mapping (allow-list, viewer/operator) from approved config outside the repo, enforced on every route, not only in the UI | Per-user authorization |
| 3 | Per-user identity (masked/opaque subject) in audit and run log instead of one AllowedUser | Accountability for several people |
| 4 | `request_origin_problem` allows only `127.0.0.1`/`localhost` Hosts and `http://` Origins. Behind the proxy, Host is `172.26.37.20:8443` and Origin is `https://...`, so every POST would be refused (or the check weakened). Add an HTTPS-aware allowed-origin list; `SURFACE_ONBOARDING_ALLOWED_HOSTS` covers Host only | Keep CSRF protection working |
| 5 | `Secure` session cookie (and `__Host-` prefix if possible); session lifetime aligned with SSO | Cookies currently have no `Secure` flag |
| 6 | Keep CSRF/Origin/Sec-Fetch checks; add tests that forged identity headers from a non-loopback peer are rejected | Header spoofing |
| 7 | Multi-user run lock already exists (a second Leonardo check/run is refused); show who holds it | Concurrency |
| 8 | VM-mode state paths under `/var/lib/surface-onboarding` instead of `integration/` and `%LOCALAPPDATA%` | Section 5 |

Each change needs focused tests and the relevant suite before any deployment.

## 3. Salesforce access on the VM

Proposal: a dedicated non-human Connected App using the OAuth JWT bearer flow; private key in an approved secret provider (never in the repo or unit files); read-only permission set; the existing fixed-query contract. The one Salesforce write path (id writeback) stays disabled on the VM. No human `sf` CLI profile on the server.

Conflict to resolve: `SALESFORCE_READ_APPROVAL_PACKET.md` approves only one record (CO-0717), no queue enumeration, no polling. A team dashboard needs the open-onboardings queue and CO detail reads, so Salesforce admin and SecOps must approve the wider fixed-query scope as a new decision.

## 4. Leonardo and browser work

VM mode refuses browser launches by design.

| Option | Scope | Recommendation |
| --- | --- | --- |
| (a) Phase 1 | VM serves read-only views: queue, production readiness, CO pages, search. Onboarding runs (Leonardo Development) are still started from the owner's Windows desktop | Do first |
| (b) Phase 2 | Separate approved ephemeral browser runner, not colocated with `workato-agent`, per `docs/36` and `docs/34` | Later, after its own approvals |

**Phase 1, teammates can:** log in, view the queue, CO pages and stage tracker, production readiness, Onboarded on Dev, search. **Cannot:** start or confirm an onboarding, run Leonardo checks or SpyCloud/renewal writes, sign in to Salesforce or Leonardo, or change state.

## 5. Data and state

Move to `/var/lib/surface-onboarding` (mode `0750`, owner `surface-onboarding`, masked metadata only):

- `attended_leonardo_readbacks.json`, `attended_ce_only_runner_state.json`, `attended_ce_only_diagnostics.json`, `attended_ce_only_table_diagnostics.json`, `attended_ce_only_run_log.json`, `attended_ce_only_check_state.json`
- `attended_scan_reminders.json`, `attended_scan_status.json`, `attended_user_created_confirmations.json`, `attended_spycloud.json`
- `attended_salesforce_id_writebacks.json`, `attended_surface_validation.json`, `attended_renewal_mirrors.json`, `attended_renewal_outcomes.json`
- Leonardo inventory snapshots (`%LOCALAPPDATA%\SurfaceOnboarding\leonardo-inventory`) and the Redash prod-clone snapshot behind `/prod`

While runs stay on the desktop, the desktop is the writer and the VM only reads. Proposal: one writer per file; the desktop publishes masked read copies to the VM through a one-way sync, so state is never split. Teammates then see desktop run history only after a sync. Needs an owner decision.

**Desktop-only:** automation browser profile, Leonardo session, Salesforce CLI session and `sf` auth files. Never copied to the VM.

**Backup:** nightly encrypted copy of `/var/lib/surface-onboarding` by Operations, with a tested restore.

**Redash collector key:** stored with Windows DPAPI today, which does not exist on Linux. Options: (1) keep the collector on the desktop and push only the snapshot, so no key on the VM (proposed); (2) read from the company secret manager at runtime. Never a key in a file or unit.

## 6. Network and coexistence

- Firewall (ufw/nftables or vSphere rules): allow only TCP 8443 from the approved vSphere/office subnet (SecOps names the range; VPN membership is reachability, not authorization). Default deny otherwise. `127.0.0.1:8000` and `:4180` are never exposed.
- Separate OS user `surface-onboarding` for the dashboard; proxy and oauth2-proxy get their own non-login users. No shared secrets, files, certificates, or environment with `workato-agent`; no control of the OPA service.
- systemd limits per unit (`MemoryMax`, `CPUQuota`, `TasksMax`) so a dashboard fault cannot starve OPA. Use the existing hardened unit template, deployed only after approval.

## 7. Rollout (proposal)

| Step | Action | Approval / owner | Verify | Rollback |
| --- | --- | --- | --- | --- |
| 0 | Local only: build VM identity mode, roles, HTTPS-aware origin checks, Secure cookie, tests; draft proxy config as review artifacts | None (local files) | Suite green; header-spoof tests pass | Revert commits |
| 1 | Approve placement, user, resource limits, port 8443, firewall rule | VM/SecOps | Written approval recorded outside repo | None needed |
| 2 | Register OneLogin app, allow-list/group, MFA, session policy | Identity/OneLogin admin | Allowed user logs in, denied user cannot | Disable the OneLogin app |
| 3 | Certificate (internal CA, or approved self-signed) | VM/SecOps | `openssl s_client` shows expected chain/SAN | Remove cert and listener |
| 4 | Install app tree, Python 3.12 runtime, unit; create `/var/lib/surface-onboarding` `0750`; start with empty data | VM/SecOps + Operations | Service on loopback only; `ss -ltn` shows no other new listener | Stop/disable unit, remove tree |
| 5 | Install proxy + oauth2-proxy, open firewall for the subnet | VM/SecOps, Identity | Allowed host: login page then dashboard; other host blocked; forged header ignored | Disable proxy, close firewall rule |
| 6 | Salesforce service identity, fixed read scope | Salesforce admin + SecOps | Fixed read returns expected shape; any write attempt fails | Revoke Connected App/key |
| 7 | Publish masked state copies; enable backup | Operations | Queue shows; restore test passes | Stop sync, remove copies |
| 8 | Team pilot: viewers first, then named operators | Operations + owner | Audit shows per-user subject | Remove users from allow-list |
| 9 | Phase 2 browser runner | Separate approvals (docs/34, 36) | Per BROWSER_RUNNER_APPROVAL_PACKET | Separate plan |

## 8. Risks and owner decisions

Risks: a self-signed certificate habituates users to warnings; a proxy on the same host as OPA (docs expect a corporate/SecOps-run proxy); desktop/VM state divergence; header spoofing if the trust check is wrong; Salesforce scope wider than the approved single record.

Decisions needed from the owner:

1. **Port:** 8443 (proposed) or other.
2. **Certificate:** internal CA vs self-signed for the pilot.
3. **Allow-list:** which teammate emails (docs say five operators; confirm the number and OneLogin group).
4. **Roles:** viewer/operator split, and who is an operator.
5. **Phase 1 scope:** read-only VM with runs on the desktop (recommended), and the desktop-to-VM state sync direction.
6. **Proxy ownership:** accept a project-run VM-local proxy, or require the corporate proxy in `REVERSE_PROXY_APPROVAL_PACKET`; plus the Redash key approach.

**Owner answers (2026-10-07).** These are owner preferences for the pilot; the SecOps / Identity / Salesforce approvals in §7 are still required and are not implied by them.

1. Port: **8443**.
2. Certificate: **self-signed for now** (pilot); internal CA later.
3. Allow-list: **the owner only for now** (milton.stevenson@pentera.io).
4. Operators: **the owner and john.ostrander@pentera.io**. Allow-list order (confirmed): the owner first; John is added as operator after the owner has checked the pilot.
5. Phase 1: **yes** — read-only VM, runs from the owner's Windows desktop.
6. Proxy: **VM-local (project-run) for now**; the Redash key approach is still open.

## Conflicts with existing documents

- `docs/35` and the deployment README describe a single-user pilot / five operators behind a corporate SecOps proxy with enterprise TLS; this plan proposes a VM-local proxy and optionally a self-signed certificate. Needs explicit SecOps acceptance.
- `docs/36` and `docs/34` assume Milton-only runner access; team operators are deferred to phase 2.
- `SALESFORCE_READ_APPROVAL_PACKET.md` covers CO-0717 only.
- The deployment README supplies no proxy config until hostname, certificate, IdP, and allow-list are approved; Phase 0 drafts remain review artifacts only.
