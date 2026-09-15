# Scripts

These scripts are for validation and project setup support only. They are not the production onboarding automation.

Production automation must remain native Workato plus OPA-backed connectors/API calls.

## ubuntu_opa_preflight.sh

Run on an Ubuntu 22 VM candidate before installing OPA:

```bash
chmod +x ubuntu_opa_preflight.sh
./ubuntu_opa_preflight.sh
```

The script checks:

- DNS resolution.
- TCP 443 reachability to Workato OPA gateways.
- HTTPS reachability to Donatello dev, Presale, and BO.
- unauthenticated Donatello API reachability.
- outbound NAT IP.
- TLS connection establishment to Workato OPA gateways.

No secrets are used.

## deploy_phase2_intake.ps1

Packages only the allowlisted Phase 2 intake files, computes SHA-256 checksums,
uploads them to `workato-opa-01`, runs the focused suites in staging and live,
backs up the previous package, and starts the loopback-only service on port
`8789`. The service does not call Leonardo or any other external system.

Run from the repository root in an interactive terminal so the operator can
enter the VM password directly into the SSH/SCP prompts:

```powershell
./scripts/deploy_phase2_intake.ps1
```

## verify_phase2_intake.ps1

Performs a read-only verification of:

- exactly one managed Phase 2 process matching the PID file and service user;
- exactly one TCP listener on IPv4 loopback, owned by that process, with no
  wildcard, IPv6, or UDP listener on the intake port;
- an in-memory synthetic request whose HTTP response exactly matches the
  deployed v2 response shape, v5 fail-closed constants, and local preparation
  result; and
- agent activity and the four deployed-file checksums.

The verifier does not retain or print the synthetic payload or response body.
It emits only bounded status facts and checksums. The embedded Bash/Python
program is UTF-8/Base64 encoded locally and passed as a non-sensitive SSH
command argument so the remote shell cannot strip Python quotes or
backslashes and SSH standard input cannot contaminate the decoder. Remote
`pipefail` turns any decoder error into a failed verification. The encoded
script contains no credential or customer data; OpenSSH still collects the
password interactively:

```powershell
./scripts/verify_phase2_intake.ps1
```

## rollback_phase2_intake.ps1

Restores the backup for one explicit deployment ID and restarts the service:

```powershell
./scripts/rollback_phase2_intake.ps1 -DeploymentId 20260827-123456
```

Never place passwords, MFA codes, tokens, cookies, webhook capability URLs, or
customer payloads in command-line arguments, files, or deployment evidence.
