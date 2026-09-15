# Phase 2 OPA read-only verifier transport fix

Date: 2026-08-31  
Status: **transport fixed; clean live read-only verification passed**

## Verified failure

- Interactive SSH authentication succeeded and the verifier reached the
  approved OPA VM.
- The remote Python block failed before any service checks because the
  verifier passed the whole Bash program as an SSH command-line argument.
- OpenSSH joins command arguments for the remote shell. That shell consumed
  Python quotes and backslashes, changing a valid source line into invalid
  Python.
- The failure was in the verifier transport. It is not evidence of an OPA
  process, listener, service, schema, or password failure.

## Implemented fix

- `scripts/verify_phase2_intake.ps1` now builds the same remote Bash/Python
  program locally and encodes its exact UTF-8 bytes as Base64. The first fix
  sent the Base64 text through SSH standard input; the service checks passed,
  but the decoder reported trailing invalid input.
- The hardened transport now supplies the non-sensitive Base64 as a command
  argument, keeps SSH standard input separate, and runs the decoder under
  Bash `pipefail`. Any decoder error therefore fails the verifier rather than
  accepting a partially decoded program.
- Base64 prevents the intermediate remote shell from interpreting Python
  quotes, backslashes, heredoc markers, or line endings.
- The remote program and verification behavior are otherwise unchanged and
  remain read-only.

## Local verification

- PowerShell parser: pass.
- Extracted embedded Python syntax compilation: pass.
- Base64 command-argument character and length checks: pass.
- UTF-8/Base64 byte-for-byte round trip: pass.

## Clean live result

- Exactly one managed Phase 2 process matched the PID file and expected
  service owner.
- Exactly one TCP listener existed on IPv4 loopback, it belonged to the
  managed process, and no UDP listener existed on the intake port.
- The fully synthetic probe passed the exact v2 response shape and v5
  fail-closed decision: both authorization booleans remained false.
- The response body was not retained.
- The Workato agent reported active.
- All four deployed file hashes matched the local allowlisted snapshot.
- The run completed without a Base64, Python, SSH, service, or schema warning.

## Security and evidence boundary

No password, MFA code, token, cookie, customer payload, request body, response
body, or browser state is encoded, logged, or persisted. The VM password is
still entered only at the interactive OpenSSH prompt. This verification was
read-only and does not authorize a Workato caller test, Leonardo lookup or
fill, account creation, Salesforce writeback, or production action.
