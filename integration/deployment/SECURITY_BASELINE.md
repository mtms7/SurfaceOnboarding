# Direct integration security baseline

This is a fail-closed deployment baseline for the direct onboarding service.
It does not authorize installation, service activation, or any connection to
Salesforce, Leonardo, Workato, OPA, or BackOffice.

## Network and operator access

- The application must bind only to `127.0.0.1:8000`. It must have no public
  listener and no direct Internet ingress.
- The intended operator network is the internal RND VPN. VPN membership is a
  network boundary only; it does not replace corporate SSO/MFA, the approved
  five-operator allow-list, application authorization, or source-range review.
- A separately approved corporate reverse proxy is the sole future ingress.
  It must terminate enterprise TLS, enforce corporate SSO/MFA, pass only a
  validated authenticated identity, and maintain an approved allow-list for
  the five-operator pilot.
- Static passwords, HTTP Basic authentication, shared browser sessions, MFA
  bypasses, and direct port exposure are prohibited.
- Until an approved egress policy exists, the service has no external adapter
  and must not connect to Salesforce, Leonardo, Workato, OPA, or BackOffice.

## Host and process isolation

- The service runs only as the dedicated non-login `surface-onboarding` user.
  It must not reuse the Workato OPA user, files, certificates, browser state,
  tokens, or network policy.
- The systemd templates use a restrictive umask, empty capability set, no-new-
  privileges, protected system and home paths, private temporary and device
  namespaces, native syscall architecture, and limited address families.
- The only intended writable application path is
  `/var/lib/surface-onboarding`. Runtime state is created with mode `0750`.
- Any future PostgreSQL deployment must use loopback-only connectivity, a
  dedicated least-privilege role, encrypted backups, tested restore evidence,
  retention approval, and operations ownership before the migration may run.

## Secrets, data, and observability

- Secrets must be obtained at runtime from an approved secret provider or
  protected host mechanism. They must not be committed, copied into archives,
  placed in unit files, or printed in logs.
- Audit and operational output must use correlation metadata and masked error
  categories only. Raw Salesforce or Leonardo records, credentials, cookies,
  tokens, and MFA material are prohibited.
- Debug endpoints, generated API documentation, cross-origin access, and any
  operator interface remain disabled until the SSO, identity propagation,
  CSRF/session, logging, retention, and monitoring designs are approved.
- A temporary static-preview process may be used only for an SSH-tunneled
  design review. It must bind to loopback, serve no live data or actions, have
  no proxy/public exposure, and be stopped immediately after the review.

## Evidence required before activation

Activation requires recorded VM/SecOps approval, reverse-proxy/TLS/SSO design
approval, named pilot identities, secret-provider approval, database/backup
approval when persistence is added, monitoring and incident ownership, a
rollback plan, and separately approved Salesforce and Leonardo Development
identities. Each external action remains subject to its own documented gate.
