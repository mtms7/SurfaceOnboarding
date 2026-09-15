# Direct onboarding threat model

**Status:** local review artifact; no activation authority.  
**Scope:** the direct onboarding service on `workato-opa-01`, accessed only by
the internal team through the RND VPN, with a future corporate TLS/SSO proxy.

RND VPN connectivity is a network boundary, not proof of an operator's
identity, role, authorization, or approval. The direct service is currently
inert: it has no running listener, database, external adapter, or proxy.

## Protected assets

- The ability to create, alter, or verify a Leonardo Development tenant.
- Salesforce and Leonardo identifiers, revisions, approval state, and masked
  audit evidence.
- Authentication sessions, secret-provider access, and deployment artifacts.
- The separate Workato OPA service, identity, certificate, and connectivity.
- Availability of the internal onboarding queue and integrity of its workflow
  state, idempotency, and approval boundaries.

Raw Salesforce/Leonardo records, passwords, MFA material, browser storage,
cookies, bearer tokens, HAR files, and full request/response bodies are never
valid application assets to persist or log.

## Trust boundaries

| Boundary | Required protection | Current state |
| --- | --- | --- |
| RND VPN client to corporate proxy | Approved VPN source ranges plus TLS, SSO/MFA, five-user group, rate limits | Blocked pending SecOps/Identity approval |
| Proxy to `127.0.0.1:8000` | Proxy-only loopback upstream; strip client identity headers; authenticated identity contract | Blocked; no proxy or listener |
| Web application to workflow persistence | Least-privilege database role, transaction/lock integrity, encryption, backup/restore | Blocked; no database |
| Application to Salesforce | Non-interactive read identity, fixed queries, masked outcomes, revision binding | Blocked; no adapter |
| Application to Leonardo Development | Approved secret provider/auth, duplicate check, single-flight execution, read-after-write | Blocked; no adapter |
| Direct service to Workato OPA | No trust or shared state; separate users, paths, credentials, and controls | Enforced by deployment boundary; no direct integration control exists |
| Release artifact to VM | Detached checksum, offline archive validation, service-account test before atomic swap | Enforced for staged releases |

## Threats and required mitigations

| Threat | Primary mitigations | Required evidence before activation |
| --- | --- | --- |
| VPN user impersonation or stolen session | Corporate SSO/MFA, short sessions, reauthentication, exact five-user group, secure cookies | Identity-owner approval and negative session tests |
| Forged proxy/forwarded identity headers | Proxy strips client headers; integrity-protected identity contract; duplicate/malformed headers rejected | Header-propagation design and forged-header test evidence |
| CSRF or confused-deputy state change | Server-side sessions, per-request CSRF protection, POST-only mutations, role checks independent of UI | Application-security review and negative endpoint tests |
| Unauthorized RND/VPN source | SecOps-approved source ranges, default deny, proxy-only ingress | Firewall/proxy rule review and network test evidence |
| Brute force, resource exhaustion, or queue flooding | Proxy/application rate limits, bounded bodies/timeouts, job coalescing, single-flight locks, monitoring | Load/limit test evidence and alert ownership |
| Salesforce/Leonardo data or secret disclosure | Approved secret provider, no raw payload persistence, masked logs, least-privilege identities | Secret-provider and logging review; redaction tests |
| Replay, source drift, or duplicate tenant action | Source revision match, canonical intent hash, idempotency, human approval, duplicate/readback checks | Integration tests and controlled Development evidence |
| Supply-chain or archive tampering | SHA-256 sidecar, offline tar validation, reject links/path traversal/oversize members, test candidate before swap | Release verifier result and service-account test log |
| VM-to-OPA lateral movement | Separate OS identity, directories, secrets, certificate, browser profile, and network policy | SecOps host review; no OPA control from direct service |
| Database compromise or audit tampering | Loopback-only DB, dedicated role, immutable audit design, encryption, backups, restore tests | Data/Operations approval and recovery exercise |
| Egress abuse, SSRF, or production-target use | No external adapter until approved; Development-only origin policy; explicit egress allow-list | SecOps egress review and adapter contract tests |
| Unsafe deployment/rollback | Atomic staging swap, preserved prior release, inactive-service precondition, change record | Deployment rehearsal and rollback evidence |

## Explicitly unacceptable shortcuts

- Treating RND VPN membership or an Israeli network location as operator
  authorization.
- Basic authentication, shared accounts, static passwords, local users, shared
  browser sessions, or disabling MFA.
- Trusting any inbound identity header without the approved proxy contract.
- Exposing port 8000, binding to a wildcard address, or bypassing the proxy.
- Reusing Workato OPA credentials, files, certificates, sessions, or service
  identity.
- Enabling a real adapter, database migration, or web listener because a local
  test or staging test passes.

## Exit conditions for the threat-model phase

1. SecOps records the RND-VPN source-range and host/network decisions.
2. Identity records the SSO/MFA, group, session, reauthentication, and identity
   propagation design for the five pilot operators.
3. Application Security approves the session, CSRF, authorization, logging,
   rate-limit, and error-handling design and corresponding negative tests.
4. Operations approves monitoring, incident response, backup/restore,
   retention, deployment, and rollback ownership.
5. Salesforce and Leonardo approvals remain separate and are not implied by
   completion of this threat model.
