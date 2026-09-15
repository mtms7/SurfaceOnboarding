# Ubuntu deployment preparation — not an installation runbook

These files describe the future direct-integration placement on
`workato-opa-01` (Ubuntu 22.04). They are intentionally non-deploying: no
package installation, user creation, service enablement, timer enablement,
database migration, firewall change, OPA change, Salesforce call, or Leonardo
operation is authorized by their presence.

## Coexistence boundary

`workato-opa-01` is also the planned/current Workato OPA host. The direct
onboarding service must use a separate OS identity, directory tree, log policy,
network policy, and future secret-provider identity. It must not reuse the OPA
activation command, certificate, files, environment, service account, or
browser state. No direct-integration component may control `workato-agent`.

## Intended topology

The future web process binds only to `127.0.0.1:8000`. A separately approved
TLS/SSO reverse proxy is the only component that may expose it to the five
operators. There is no approved public hostname, certificate, identity
provider, allowed operator list, or exact RND-VPN source-range rule yet;
therefore an Nginx configuration is deliberately not supplied. The RND VPN is
the intended internal operator network, but its membership is not an identity
or authorization control.

`REVERSE_PROXY_APPROVAL_PACKET.md` is the security-review checklist for that
future component. It intentionally specifies evidence and acceptance criteria,
not an installable proxy configuration or an identity-header value.

`THREAT_MODEL.md` records assets, trust boundaries, abuse cases, current
controls, and activation evidence for the internal RND-VPN design. It treats
VPN presence as network reachability rather than operator authorization.

The systemd templates are guarded in two ways:

1. They require an approval marker outside this repository.
2. The Python application factory independently rejects listener creation until
   web identity approval is represented in approved runtime configuration.

The poll timer template is intentionally disabled and includes no schedule.
Deployment owners must choose the two polling times, timezone, overlap window,
service identity, database provisioning, backup/restore plan, monitoring,
secret provider, Salesforce non-interactive read identity, and SSO/CSRF/TLS
controls before it may be enabled.

## Required future approval gates

- VM owner and SecOps approve collocation and least-privilege OS identities.
- Identity owner approves SSO, the five operators, proxy headers, and CSRF/TLS.
- Salesforce owner and SecOps approve a non-interactive read identity and its
  fixed-query scope; the currently used human MFA session is not a service
  credential.
- Data/operations owners approve PostgreSQL, encryption, backup, retention,
  monitoring, incident response, and the two polling times.
- Mapping owners approve each route. Leonardo remains Development-only and
  blocked until its separate authentication and operation approvals exist.

Do not copy these templates to the VM until the preceding gates are recorded.
