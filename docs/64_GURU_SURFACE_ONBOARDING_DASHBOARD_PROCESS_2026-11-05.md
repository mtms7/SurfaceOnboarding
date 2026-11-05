# Guru card draft: Surface Customer Onboarding — how it runs with the Onboarding dashboard (2026-11-05)

Draft for the owner to paste into Guru (Collection: Surface / Customer Onboarding). Nothing here was published by the
assistant. Source: `integration/IMPLEMENTATION_PLAN.md` section 18 (through 2026-11-05), docs/45, docs/53, docs/60, docs/61,
docs/62, and the live dashboard pages checked on 2026-11-05.

---

**Card title:** Surface Customer Onboarding — Onboarding dashboard process (CE-only, Surface + CE, Surface renewal)

**Verifier:** Milton Stevenson · **Last reviewed:** 2026-11-05

## Summary

Customer Onboarding (CO) records in Salesforce are onboarded into BackOffice production (https://app.pentera.io) from the
**Onboarding dashboard**, a local tool on the onboarding owner's PC. For the supported types, one click on
**"Onboard in production"** checks for duplicates, creates the tenant, reads it back, writes the IDs to the CO and moves the
Salesforce stage. Every write is read back. The tool never retries a create, never stores passwords or MFA codes, and stops
("fails closed") whenever something is unclear.

## What is automated today

| Onboarding type (Salesforce) | What the dashboard does | Still manual |
|---|---|---|
| **Credential Exposure — New** (CE-only) | Duplicate check → create tenant "*Account* - CE Only" → read back → write Account UUID → stage **Account Scanning** → scan check → **Scan Completed Successfully** → **User Created** | **Onboarding Completed** (after Marcel/Roey link the tenant to the Core licence and tick "Attached to Core?"). No primary user for CE-only. |
| **Surface + Credential Exposure — New** (Case 3) | Duplicate check → create → read back → write Surface Account ID + Account UUID → stage **Account Scanning**; the same run sets Operator = the Technical Advisor's operator and Maximum scan duration = 90 (new, not yet proven on a live CO) | Check Operator and Max scan 90 in BackOffice after the run; scan check; primary user; **User Created** and later stages. Subdomains are refused (do by hand). |
| **Surface — Renewal of Existing Product** | "Renew in production" card: finds the tenant by exact name + primary domain, changes ONLY Scanning interval (Go = Monthly, Prime = Weekly) and Expiration date (term end) → writes Account ID + UUID → stage **User Created** | Nothing else changes (licence counts and domains are shown, never edited). |
| Surface — New only, CE renewals, Surface + CE renewal (Case 6), US / IL tenants | **Not automated.** The queue shows them; onboard by hand per the existing Guru guides. | Everything. |

## Before you start

1. **The CO must be Approved in Salesforce.** Approved means the CO was already reviewed by hand (domains, dates,
   products). The dashboard re-reads the approval live when you open the CO page.
2. **Surface Tenant Region** on the CO: blank or **EU** = https://app.pentera.io (automated). **US** (us.app.pentera.io) or
   **IL** (il.app.pentera.io) = not automated; the dashboard shows "Region US: not checked" and refuses the create. Set the
   field correctly before onboarding a US/IL customer.
3. **Timing:** the create opens 2 days before the licence start. A CSM can ask in writing for an earlier start
   (CE-only only, see Early activation).
4. Pentera VPN on.

## Step by step (onboarding owner's PC)

1. **Start the dashboard** in PowerShell from the project folder:
   `powershell -File tools/start_attended_dashboard.ps1 -Restart -ProductionSessions -PythonPath .venv-renewal-playwright/Scripts/python.exe`
   Wait for "Dashboard is ready at http://127.0.0.1:8012/".
2. **Sign in** at http://127.0.0.1:8012/login. If your Salesforce session is still active, click
   **"Continue as *you*"** (no new SSO). Otherwise **"Sign in with a new Salesforce session"** (Salesforce SSO + MFA).
3. **Queue** (Onboarding page): tabs **All**, **Ready to onboard**, **Blocked**, **Approved**, **Created**. Each row shows
   the Salesforce approval and stage, days to start, and the production check chip (**Clear**, **Duplicate**, **Ambiguous**,
   **Live paid x N**, **Region US: not checked**, **Clone stale**).
4. **Open the CO.** The page header shows e.g. "CE-only · Checks clear · Create ready". The **Production readiness** card
   must show **Ready** on every row: runner unlocked, production session, clone fresh under 6 h, no duplicate in
   production, Salesforce data valid, timing window, one-CO create approval, owner go.
5. **Click "Onboard in production" once.** Your click is the approval for this one CO. A Chrome window opens on
   app.pentera.io: complete **SSO and MFA there yourself**. That sign-in is kept up to 8 h for later runs and scan checks.
6. **Watch the progress bar:** sign-in and duplicate checks (tenant name and every domain, twice) → create (one Confirm) →
   read back → write the Account UUID (and Surface Account ID for Case 3) → stage Account Scanning.
7. **Anything other than "created and verified": do not click again.** Check the tenant in BackOffice by hand and tell the
   onboarding owner. There is no automatic retry and no delete.

**Early activation (CSM exception, CE-only):** when the CSM asks in writing to activate before the licence start, tick
**"Early activation (CSM exception)"** under "Onboard in production". The licence then starts on the run day; if there is
no Active Pentera Core Plus term yet, the one Pending (renewal) term is used.

**Surface renewals:** open the CO from the queue ("Renew in production" card on the CO page) and use that card's button.

## What happens after the create

- **CE-only:** while the BackOffice sign-in is kept, the dashboard checks the scan by itself (first after about 20 min, then
  every 5 min up to 90 min, later once per sign-in). CE scans often take about 10 h, so it can finish on a later check.
  When done: Scan Completed Successfully → User Created. Then Marcel/Roey tick "Attached to Core?" and the CO is moved to
  **Onboarding Completed**.
- **Case 3:** confirm Operator and Maximum scan duration 90 in BackOffice, check the scan, create the primary user, move the
  later stages by hand.

## Values the automation uses

- **Tenant name:** CE-only = "*Account Name* - CE Only"; Case 3 = account name without suffix. Names over 40 characters are
  shortened (first two words + initials of the last word and legal form, e.g. "Träger GmbH" → "TG"); the short name is
  confirmed once on the CO page.
- **Licence dates:** from the DealHub subscription (CE-only: the Active Pentera Core Plus row). Start = run day, end = term
  end. A wrong date range typed in Onboarding Comments does not block; the DealHub dates are used.
- **CE email domains:** the first Email Domains value is the primary domain; all of them are added as Leaked Credentials
  scanned domains.
- **Case 3:** exactly one CE email domain; Go = Monthly (500 subdomains), Prime = Weekly (1000); alternate domains allowed.

## Why a CO is blocked (and what to do)

| Shown | Meaning | Action |
|---|---|---|
| Not approved (Pending) | Salesforce approval is not Approved | Approve in Salesforce; the dashboard picks it up within about a minute (at once when you open the CO page). |
| Duplicate / Ambiguous / Live paid x N | A tenant with the same name or domain already exists in production | Check BackOffice; do not create. Fix the CO or onboard by hand. |
| Region US: not checked | US / IL tenant | Onboard by hand in that region. |
| Clone stale / Clone unusable | The production copy used for the duplicate check is too old (over 6 h since collected, or data over 36 h old) | Connect the VPN; the dashboard re-collects by itself every 45 min (every 15 min after midnight UTC until the day's copy arrives). |
| Account UUID already set | The CO was already onboarded | Nothing to create; check the stage. |
| Timing window not open | More than 2 days before the licence start | Wait, or use Early activation if the CSM asked (CE-only). |

## Good to know

- **Duplicate check source:** a read-only copy of the production tenant list from Redash. It covers **EU only** and the
  copy is loaded once a day (around 00:00 UTC), so a tenant created today may not be in it until tomorrow. The dashboard
  also searches BackOffice live (name and every domain) right before creating.
- **Team view:** a read-only mirror of the queue runs on the onboarding VM. It can ask the owner's PC to start a run
  ("Start onboarding on my PC"); the create still runs on the PC with the owner's SSO and MFA.
- **Security:** the dashboard never sees passwords, MFA codes or cookies; BackOffice has no API, so sign-in stays manual.

## Open items (as of 2026-11-05)

- US tenants: a US Redash data source was added but is still empty (asked Omer Gafni).
- More frequent copy refresh (2-3 times a day) requested from the Redash admin.
- Collection from the VM is blocked by the network (IT ticket 16160).
- "Onboarding Completed" for CE-only waits on "Attached to Core?".

## Contacts

- Onboarding automation owner: **Milton Stevenson**
- Redash admin: **Omer Gafni**
- Attached to Core (Castro link): **Marcel**, **Roey**
