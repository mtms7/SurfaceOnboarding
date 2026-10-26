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
