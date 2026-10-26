# Production onboarding from the dashboard: PC tab, PowerShell, repo root (2026-10-23)

CE-only and Surface & Credential Exposure (Case 3) New Product Onboarding. One button, no dry run, no typed CO.
Pentera VPN on. Salesforce SSO sign-in if asked.

## 1. Restart the dashboard (once, loads the new page code)

    cd "C:/Users/Milton Stevenson/Documents/Surface/Workato"
    powershell -File tools/start_attended_dashboard.ps1 -Restart -ProductionSessions -PythonPath .venv-renewal-playwright/Scripts/python.exe

## 2. Open http://127.0.0.1:8012/co/CO-xxxx?env=prod (or /onboard/CO-xxxx)

1. CO must be Approved in Salesforce. Case 3 also needs the confirmed tenant name.
2. Click **Onboard in production** ONCE. Sign in with SSO + MFA in the app.pentera.io window.
3. The progress bar runs: create, readback, UUID write, stage to Account Scanning (CE-only). Case 3 stops after the verified create for your review.
4. Anything other than created and verified: do not click again, paste the result to Claude.


# VM to Redash connectivity test, read-only (PC tab, PowerShell; 2026-10-26, IT ticket 16160)

Pentera VPN on. Password prompt = the `workato` VM password, typed in your own window (never paste it to Claude).
Runs on the VM through ssh; sends no key and changes nothing. Do not type into an already open ssh tab: open a new PC tab.

    ssh workato@172.26.37.20 "getent hosts redash.pentera.io; timeout 8 bash -c '</dev/tcp/10.10.24.16/443' && echo TCP_OK || echo TCP_FAIL; curl -sS -m 10 -o /dev/null -w 'HTTP %{http_code}\n' https://redash.pentera.io/ping"

Expect: an IP line for redash.pentera.io (10.10.24.16), TCP_OK, HTTP 200.
TCP_FAIL or a timeout = the firewall path is still closed (reply on ticket 16160 with the result).
Paste only the output lines to Claude.


# Deploy release r37 to the VM (adds "Open this CO on my PC" to the VM CO page), 2026-10-26

Pentera VPN on. The `workato` VM password prompts are typed in your own window; never paste them to Claude.
Archive: surface-onboarding-r37-44d25a2.tar.gz, SHA-256 169E8B45B8D05D668B48F18BE46F3D1387E39741947390163E70A03DBE5D5456 (458 files, LF, checker passed).
The VM runs r36 now. Stage first (changes nothing live), activate only with the owner's separate go.

## PC tab, step B: upload four files to the VM /tmp/r37-upload (asks the password per command)

    ssh workato@172.26.37.20 "mkdir -p /tmp/r37-upload"
    scp "$env:USERPROFILE/.surface-onboarding/releases/surface-onboarding-r37-44d25a2.tar.gz" "$env:USERPROFILE/.surface-onboarding/releases/surface-onboarding-r37-44d25a2.tar.gz.sha256" workato@172.26.37.20:/tmp/r37-upload/
    scp "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step26a_vm_stage_r37.sh" "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step26b_vm_activate_r37.sh" workato@172.26.37.20:/tmp/r37-upload/

Expect "100%" lines.

## PC tab (runs on the VM), step C: stage r37 (checksum, unpack, CR check, full suite and guards; activates nothing)

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r37-upload/_step26a_vm_stage_r37.sh"

Expect: checksum OK, "no absolute or .. paths", "none" for CR bytes, suite OK, both guards pass, "staged only; nothing activated".
Any "stop here" or suite failure: stop and paste the output to Claude.

## PC tab (runs on the VM), step D: activate r37 (ONLY after the owner's go)

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r37-upload/_step26b_vm_activate_r37.sh"

Expect: GET / 200, POST / 403 or 405, direct dashboard without secret 4xx. Rollback to r36:

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r37-upload/_step26b_vm_activate_r37.sh rollback"

## PC, step E: check the button

1. PC dashboard running and signed in (restart command at the top of this file), VPN on, tunnel task running.
2. Open https://localhost:8443/ (the VM), open a production CO page, find the card "Production readiness".
3. Click "Open this CO on my PC": the PC dashboard CO page opens (http://127.0.0.1:8012/onboard/CO-xxxx).
4. A real create is a separate owner go per CO.
