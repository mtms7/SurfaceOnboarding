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


# Deploy release r38 to the VM (adds "Open this CO on my PC", the Days to start column and the stale-banner fix), 2026-10-27

Pentera VPN on. The `workato` VM password prompts are typed in your own window; never paste them to Claude.
Archive: surface-onboarding-r38-9b2a30e.tar.gz, SHA-256 ABE4009257DF5AB6B96D902CAD3AED23A9F171965698904CB9D5B1A769A278D9 (460 files, LF, checker passed).
The VM runs r36 now (r37 was built but never sent; it is superseded by r38). Stage first (changes nothing live), activate only with the owner's separate go.

## PC tab, step B: upload four files to the VM /tmp/r38-upload (asks the password per command)

    ssh workato@172.26.37.20 "mkdir -p /tmp/r38-upload"
    scp "$env:USERPROFILE/.surface-onboarding/releases/surface-onboarding-r38-9b2a30e.tar.gz" "$env:USERPROFILE/.surface-onboarding/releases/surface-onboarding-r38-9b2a30e.tar.gz.sha256" workato@172.26.37.20:/tmp/r38-upload/
    scp "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step27a_vm_stage_r38.sh" "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step27b_vm_activate_r38.sh" workato@172.26.37.20:/tmp/r38-upload/

Expect "100%" lines.

## PC tab (runs on the VM), step C: stage r38 (checksum, unpack, CR check, full suite and guards; activates nothing)

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r38-upload/_step27a_vm_stage_r38.sh"

Expect: checksum OK, "no absolute or .. paths", "none" for CR bytes, suite OK, both guards pass, "staged only; nothing activated".
Any "stop here" or suite failure: stop and paste the output to Claude.

## PC tab (runs on the VM), step D: activate r38 (ONLY after the owner's go)

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r38-upload/_step27b_vm_activate_r38.sh"

Expect: GET / 200, POST / 403 or 405, direct dashboard without secret 4xx. Rollback to r36:

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r38-upload/_step27b_vm_activate_r38.sh rollback"

## PC, step E: check the button

1. PC dashboard running and signed in (restart command at the top of this file), VPN on, tunnel task running.
2. Open https://localhost:8443/ (the VM), open a production CO page, find the card "Production readiness".
3. Click "Open this CO on my PC": the PC dashboard CO page opens (http://127.0.0.1:8012/onboard/CO-xxxx).
4. A real create is a separate owner go per CO.


# VM refreshes its own Salesforce data from YOUR attended sign-in (owner decision 2026-10-27), PC tab

Pentera VPN on. Passwords (workato, then sudo) are typed in your own window, never pasted to Claude. No new release is needed: the
refresher, the unit templates and the Salesforce CLI are already on the VM (r38 active). Port 1717 on the PC must be free while you sign in.

## Step 1 (PC): upload the installer (asks the password per command)

    scp "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step28a_vm_install_sf_login_and_refresh.sh" workato@172.26.37.20:/tmp/

## Step 2 (runs on the VM): install the login program, the logout program, the refresh timer and the approval marker

    ssh -t workato@172.26.37.20 "sudo sh /tmp/_step28a_vm_install_sf_login_and_refresh.sh install https://pentera.my.salesforce.com"

Expect: "ok", the five listed files, "enabled" and "active" for the timer. Undo everything: same script with `revert`.

## Step 3: sign in (do this after every VM reboot; the session lives only in the VM's memory)

    ssh -t -L 1717:127.0.0.1:1717 workato@172.26.37.20 "sudo /usr/local/sbin/surface-sf-login"

1. Keep this window open. It prints a long https://... URL.
2. Open that URL in the Chrome where you are signed in to SSO; complete SSO and MFA there.
3. The window then prints a status 0 line and starts one refresh. Browser message "localhost refused to connect" afterwards is normal.

## Step 4: check

    ssh -t workato@172.26.37.20 "sudo sh /tmp/_step28a_vm_install_sf_login_and_refresh.sh status"

Then open https://localhost:8443/ : the banner "Data as of ..." should show today's time and the queue the current COs.
End the session on purpose: `ssh -t workato@172.26.37.20 "sudo /usr/local/sbin/surface-sf-logout"`.


# VM Salesforce refresh cadence: every 30 minutes instead of 10 (owner decision 2026-10-27), PC tab

Each refresh costs about 3 minutes of CPU, so 10 minutes keeps the VM busy a third of the time. Only the timer interval changes (a systemd drop-in).

    scp "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step28b_vm_sf_refresh_cadence.sh" workato@172.26.37.20:/tmp/
    ssh -t workato@172.26.37.20 "sudo sh /tmp/_step28b_vm_sf_refresh_cadence.sh 30"

Expect: "drop-in: OnUnitActiveSec=30min", timer enabled and active, next run listed. Undo: same script with `reset`. Look: `show`.


# Deploy release r39 to the VM (VM page parity: identity check, create records, no dead Sign out), 2026-10-27

Pentera VPN on. The `workato` and `sudo` passwords are typed in your own window, never pasted to Claude.
Archive: surface-onboarding-r39-a910fb4.tar.gz, SHA-256 1DF9D05CA727F61DAA664876E0EA6BBF09B63D01A1D0D2E3F7D783F37F6495DF (466 files, LF, checker passed).
The VM runs r38 now. Stage first (activates nothing), activate only with the owner's separate go. Rollback goes back to r38.

## Step B (PC tab): upload four files

    ssh workato@172.26.37.20 "mkdir -p /tmp/r39-upload"
    scp "C:/Users/Milton Stevenson/.surface-onboarding/releases/surface-onboarding-r39-a910fb4.tar.gz" "C:/Users/Milton Stevenson/.surface-onboarding/releases/surface-onboarding-r39-a910fb4.tar.gz.sha256" "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step29a_vm_stage_r39.sh" "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step29b_vm_activate_r39.sh" workato@172.26.37.20:/tmp/r39-upload/

## Step C (runs on the VM): stage (checksum, unpack, CR check, full suite, guards; activates nothing)

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r39-upload/_step29a_vm_stage_r39.sh"

## Step D (ONLY after the owner's go): activate

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r39-upload/_step29b_vm_activate_r39.sh"

Expect GET / 200, POST / 403 or 405, direct dashboard 4xx. Rollback:

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r39-upload/_step29b_vm_activate_r39.sh rollback"

## Step E (PC tab): push the create records now instead of waiting for the hourly task, then compare

    powershell -Command "Start-ScheduledTask -TaskName SurfaceOnboarding-CloneToVm"

Check %LOCALAPPDATA%/SurfaceOnboarding/clone-push/clone-push.log: expect `records_push ssh_exit=0 result=ingested-records snapshot-...`.
The next VM Salesforce refresh (every 10 minutes until the 30-minute cadence is applied) publishes the identity inputs.
Then compare https://localhost:8443/ with http://127.0.0.1:8012/: CO-0813, CO-0806, CO-0757 get the same Clear / blocked chips, Created counts match, the Create column shows Verified. The Approval column stays "Unavailable on this server" on the VM by design.


# Deploy release r40 to the VM (VM queue parity part 2: Approval column, UUID and stage logs), 2026-10-27

Pentera VPN on. The `workato` and `sudo` passwords are typed in your own window, never pasted to Claude.
Archive: surface-onboarding-r40-b73f302.tar.gz, SHA-256 43825C27BF6EF9D3C522C4116923D8906AEB6C32ECF63BDE8DC05104D196A812 (468 files, LF, checker passed).
The VM runs r39 now. Stage first (activates nothing), activate only with the owner's separate go. Rollback goes back to r39.

## Step B (PC tab): upload four files

    ssh workato@172.26.37.20 "mkdir -p /tmp/r40-upload"
    scp "C:/Users/Milton Stevenson/.surface-onboarding/releases/surface-onboarding-r40-b73f302.tar.gz" "C:/Users/Milton Stevenson/.surface-onboarding/releases/surface-onboarding-r40-b73f302.tar.gz.sha256" "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step30a_vm_stage_r40.sh" "C:/Users/Milton Stevenson/Documents/Surface/Workato/integration/deployment/vm_pilot/_step30b_vm_activate_r40.sh" workato@172.26.37.20:/tmp/r40-upload/

## Step C (runs on the VM): stage

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r40-upload/_step30a_vm_stage_r40.sh"

## Step D (ONLY after the owner's go): activate

    ssh -t workato@172.26.37.20 "sudo sh /tmp/r40-upload/_step30b_vm_activate_r40.sh"

Rollback: same script with `rollback`.

## Step E (PC tab): push records, approvals copy and logs now, then reload both pages (Ctrl+F5 on each)

    powershell -Command "Start-ScheduledTask -TaskName SurfaceOnboarding-CloneToVm"

Log: %LOCALAPPDATA%/SurfaceOnboarding/clone-push/clone-push.log, expect `records_push ssh_exit=0 result=ingested-records snapshot-...`.
The VM Approval column should now say Used / None / Valid to MM-DD instead of "Unavailable on this server", and the onboarded line should read "Account UUID written and read back". CO-0813 and the other "Not checked" COs need one VM Salesforce refresh after r39 (every 10 minutes).
