# VM Phase 2: guarded production CE-only create from the VM side (plan, 2026-10-19)

**Status: PLANNING ONLY.** Nothing here is approved, built or installed. No VM, Workato, Salesforce, OPA, Donatello or BackOffice action was taken to write it (read-only review of the repository). It relaxes no gate: docs/34, 36, 40, 42, 43, 45, 53 and the `integration/deployment/*` packets stay in force. (Number note: `docs/54_CO_PRODUCTION_STATUS_REPORT_2026-10-17.md` already exists; this file keeps the 54 prefix as requested.)

**Owner decisions recorded 2026-10-19:** D1 = Option P (console host runs the dashboard and the runner together, owner-only). D2 = Ubuntu console host (Linux port of the Windows-only parts, items C2 and C3). Local build C1-C5, C7, C8 and C9 is authorized as local code and tests only, locked by default (both production constants stay False in everything shipped); it authorizes no VM, host, SecOps, Salesforce, BackOffice or Workato action. D3-D10 below still need their approvals.

**Goal.** Let the owner run the guarded, one-CO, CE-only production create (docs/53) without sitting at the Windows desktop dashboard (`127.0.0.1:8012`), using the Ubuntu side of the project. MFA stays human; BackOffice has no API or token; no credential, cookie or token is ever stored.

## 0. Bottom line

1. **Do not put a browser on `workato-opa-01`.** docs/34, 36 and 40 forbid it and the code refuses it. Phase 2 = a **separate, approved, ephemeral "production console" host** that is not colocated with `workato-agent`.
2. **Recommended shape (Option P): the console host runs the existing dashboard + runner together, as a second `desktop`-style runtime on Linux, and the owner drives it through an authenticated remote display.** This reuses the file-based approval, launch ticket, dry-run and record machinery unchanged (they assume dashboard and runner share one state folder and one OS user). Splitting dashboard (VM) from runner (another host) is possible (Option Q) but needs a new mutual-TLS protocol and an approval/ticket exchange that does not exist; it is the larger build.
3. **MFA/SSO** is done by the owner inside the remote-display browser, in memory only; the session dies with the host/context. No VNC password, cookie or token is handled by this project.
4. **Salesforce** on that host needs a human SSO session (as on the desktop) for the fresh reads, the revision re-check and the UUID write. The non-human read-only JWT identity discussed in docs/40, 43 and 44 is **read-only by design and cannot do the `Account_UUID__c` write**; the write needs either the owner's attended session or a separate, tightly scoped write identity (a Salesforce admin decision).
5. This needs decisions from SecOps, Salesforce admin, Identity and the owner (section 7). **The desktop path stays the only production path until they are recorded.** Nothing in this plan should delay or replace the current attended desktop attempt for CO-0798.

## 1. Verified facts vs assumptions

### Verified from the repository (read 2026-10-19)

| # | Fact | Source |
|---|---|---|
| F1 | Both locks are code constants. `PRODUCTION_RUNNER_SUPPORTED = True` since commit `1b98591` (owner go, one attended CO-0798 create; relock = revert `1b98591` + `853cf99`). First live attempt (2026-10-19T00:11Z) refused `production_session_not_ready` before the form; nothing created; approval `PROD-CE-0798-20261018` unused. Dry run #7 passed 2026-10-18T23:28Z (valid ~24 h) | plan section 18 last entries, docs/53 |
| F2 | `local_browser_launch_allowed()` returns true only when `os.name == "nt"` AND `SURFACE_ONBOARDING_RUNTIME == "desktop"`. `_launch_production_create` (the dashboard's start of the runner) returns `None` otherwise. VM mode therefore can never start the runner | `tools/serve_attended_open_onboardings_dashboard.py:319, 7675` |
| F3 | In VM mode the runner's `sf_command()` raises `SalesforceUnavailableInVmMode` (no Salesforce CLI); the dashboard's CLI helper is switched the same way. The VM reads only a signed, published snapshot (docs/41) | `tools/attended_ce_only_playwright.py:339-346`; dashboard comment block at 7768 |
| F4 | `VM_OPERATOR_POSTS` is an empty frozenset: in VM mode every POST is refused for every role (owner decision 2026-10-08). `production_create_ui.gate` also refuses first with `vm_read_only` ("VM dashboard is read-only guidance; create runs from the owner's Windows desktop") and the create card shows no control on the VM | `serve_...py:7756`, `integration/onboarding/production_create_ui.py:76, 323, 416` |
| F5 | The create is **one runner process**, started by the dashboard, that does: launch-ticket consume, Salesforce fixed reads via `sf` CLI (alias pinned `surface-onboarding`; env override refused), Redash clone facts from local state, browser page, ordered gate, create, readback, record. It is not a thin browser client | `run_production_create` (`attended_ce_only_playwright.py:10389`) |
| F6 | The browser is a dedicated automation Chrome launched with `--user-data-dir` and `--remote-debugging-port=0` (loopback CDP), Playwright attaches over CDP; `_chrome_executable()` knows only Windows paths and otherwise falls back to the bundled build; the profile folder is under `%LOCALAPPDATA%` or the temp dir; the runner closes CDP at the end. The production origin is exactly `https://app.pentera.io` (start `/backoffice/tenantManagement`) | lines 2733-2743, 3078-3085, 3146, 9635; `production_probe.py:30-31` |
| F7 | The human signs in with SSO + MFA in that window (up to 15 min wait); the runner never reads credentials. All reads go through the page's own `getAllDetailedAccounts` request (the cookie-only fetch returned 401). Exactly one `POST /api/v1/backoffice/account/add` is allowed by a context-level request guard | docs/53 |
| F8 | Launch ticket: HMAC-signed JSON, 2 min TTL, bound to CO + revision + approval ref + operator, atomic-rename consume, nonce log, key `.ticket.key` (32 random bytes, 0600). Both sides must use `production_launch_ticket.ticket_root()`; on Linux that is `~/.local/state/surface-onboarding` unless `SURFACE_ONBOARDING_TICKET_ROOT` is set; UNC/network drives and repository/OneDrive/Documents folders are refused; Windows hardening via `icacls` fails closed (no Linux hardening hook exists yet) | `integration/onboarding/production_launch_ticket.py:71-101`, docs/53 |
| F9 | Approval file: `production_approval.STATE_FILE_NAME` under `integration/` via `state_file()` (so `STATE_DIR/...` when a state dir is set); issued only by `tools/issue_production_approval.py` (typed `ISSUE CO-0798`, one CO, 7 days, consumed by the create). The gate requires operator == approval issuer (the owner only). Records land in `production-create-records/` (append-only), UUID writes in `production-uuid-writes.jsonl` (SHA-256 only) | docs/53; `_pdr_approval_path`, `_pdr_records_parent` |
| F10 | The gate also needs the Redash prod clone < 6 h (VM already receives it via snapshot/`clone-inventory`, docs/43 section 9, docs/51), a passing dry run < 24 h for the same approval, and a pinned Salesforce org Id (`SURFACE_SF_EXPECTED_ORG_ID` or `salesforce-expected-org-id.txt`) | docs/53 |
| F11 | The Salesforce UUID write is a dashboard route using `sf_write_json` through the `sf` CLI, alias `surface-onboarding`, field `Account_UUID__c` only, with a pre-write re-read, `write_started` intent line and read-after-write | docs/53 steps 9; `serve_...py:1858, 7689` |
| F12 | Dashboard login on the desktop is Salesforce SSO through the owner's local `sf` CLI (`sf org login web`, OAuth callback on loopback port 1717). VM login mode is oauth2-proxy + OneLogin with a loopback-only shared secret header | docs/40; `serve_...py:287, 7730` |
| F13 | VM standing decisions: Phase 1 read-only; nothing in the VM holds browser state; Salesforce on the VM only through the (unapproved, wider-scope) JWT read identity or the attended in-memory CLI session; docs/43 section 8 found device login blocked since 2025-08-28 and `sf org login web` unsupported on a *headless* host | docs/40, 43 |
| F14 | docs/36 requires, for any runner: a separate host, owner-only (single user), no persistent browser profile, mutual-TLS runner-to-dashboard protocol with fixed operation ids, 15-minute signed health result, in-memory data only, 4 named approvals (Identity/SecOps, Salesforce, Security, automation owner) | docs/36 |
| F15 | docs/34: the runner streams the browser context only to the authenticated operator, uses a fresh non-persistent context, destroys it on completion/timeout/disconnect, never retries an uncertain create; approvals needed from SecOps (host, EDR, egress allow-list, no public ingress, streaming controls, destruction check), Identity, Data/Ops, Leonardo owner | docs/34 |

### Assumptions (not verified; each is a spike or a decision)

| # | Assumption | How to settle |
|---|---|---|
| A1 | Chromium (bundled Playwright build or Google Chrome) with a virtual display runs the BackOffice SPA and the guard/route/CDP logic exactly as on Windows | Local Linux spike (no production): run the read-only `--production-search-probe`-style flow only after approvals; before that run the unit suite on Linux |
| A2 | `sf org login web` works inside a remote **desktop** session on the console host (the browser and the callback port are on the same machine), unlike the headless case in docs/43 | Spike on the console host after approval; fallback is a custom Connected App `--client-id` or a Salesforce admin decision (section 3) |
| A3 | The Python code path is Linux-clean for a "console" runtime (it has several `os.name == "nt"` branches: Chrome path, process-group flags, `icacls` hardening, `taskkill`) | Run the suite on Linux; list failures; none of this has been run |
| A4 | A remote-display product (noVNC/Guacamole/RDP/other) can be approved by SecOps and gated behind OneLogin | SecOps decision |
| A5 | BackOffice/Salesforce/OneLogin do not block logins from the console host's IP or from a headless-looking Chrome fingerprint (WAF, conditional access, geo/IP allow-lists) | Ask the BackOffice and OneLogin owners; AGENTS.md: a WAF/auth failure fails closed |
| A6 | The page's own table request and the first-live-attempt `session_not_ready` cause (context-level `LiveRequestGuard`, never live-tested) are fixed on the desktop first | Desktop attempt must succeed before porting anything (section 5, gate 0) |
| A7 | Salesforce admin will allow a write-capable path for exactly `Customer_Onboarding__c.Account_UUID__c` | Salesforce admin decision D5 |

## 2. Architecture options

```
Option P (recommended)                         Option Q (larger build)
owner browser --SSO proxy--> remote display    owner browser --SSO/proxy--> dashboard on workato-opa-01 (VM)
       |                                              |  mutual-TLS, fixed ops, signed tickets
       v                                              v
  CONSOLE HOST (ephemeral, owner-only)          CONSOLE/RUNNER HOST (ephemeral browser only)
   dashboard on 127.0.0.1 + runner + Chrome      runner + Chrome (+ Salesforce session)
   state: tmpfs / encrypted folder               approvals/records on VM, ticket over mTLS
```

| | **P: console host = dashboard + runner** | **Q: VM dashboard + remote runner** |
|---|---|---|
| Reuses today's code | Almost all (new runtime value, Linux fixes) | Dashboard route, ticket exchange and record transport must be built |
| Approval/ticket/record files | Local folder on the console host (same-user trust limit as the desktop) | Must cross hosts: needs the docs/36 mTLS protocol, signed grants, and a store the runner can write |
| Owner experience | One remote desktop window: dashboard tab + BackOffice window, as on the desktop | Dashboard stays at the VM URL; a second remote window for BackOffice MFA |
| New attack surface | Remote display ingress; a console host with a signed-in BackOffice session | Same, plus a runner-to-VM channel and its identity issuance |
| Team use | Owner-only (fits docs/36 "Milton only") | Could later serve named operators (docs/40 Phase 2) |
| Verdict | **Recommended first.** Smallest delta from a design that has run live 7 dry runs | Only if the team (not just the owner) must run creates |

Why not a plain "desktop on a VM": a Windows VM would avoid A3 entirely (Chrome path, icacls, process groups already work) but is a different OS image/patch/EDR story. Treat **Windows ephemeral console VM** as the lowest-risk code path and **Ubuntu console host** as the lowest-risk platform path; SecOps picks (decision D2). The rest of this plan applies to either.

## 3. The five design questions

### 3.1 BackOffice SSO + MFA for a browser on a headless VM

Constraints: BackOffice has **no API or service token** (memory/owner decision 2026-10-16); MFA must be preserved; never store a password, MFA code, cookie or token; the session must live only in the browser's memory and be destroyed afterwards (docs/34, 36).

| Option | How MFA happens | Trade-offs | Verdict |
|---|---|---|---|
| **R1. Ephemeral console host + authenticated remote display** (Xvfb/Xorg + a window manager + VNC/noVNC/Guacamole/RDP, reached only through the OneLogin proxy) | Owner opens the remote display, signs in to `app.pentera.io` with SSO + MFA in the real Chrome on the host | Needs a remote-display product and SecOps streaming controls (docs/34); clipboard and file transfer must be disabled; screen sharing could expose BackOffice data, so TLS + owner-only + idle timeout | **Recommended** |
| R2. Same, but the host is a Windows VM with RDP | Same | Reuses the Windows-tested code and Chrome path; heavier OS to approve and patch | Acceptable alternative (D2) |
| R3. Run Chrome on the owner's desktop and drive it from the VM over a tunnel (reverse CDP) | Owner does MFA locally | CDP over a network is remote code execution on a signed-in browser; the signed-in production session would be reachable from the VM | **Reject** |
| R4. Copy the signed-in cookies/profile from the desktop to the VM | none (bypasses MFA at the VM) | Violates "never store/transfer cookies" and MFA intent | **Reject** |
| R5. Service account / API token / MFA bypass | none | BackOffice has none; a stored credential is forbidden by AGENTS.md | **Reject** |
| R6. Push-based unattended runs | n/a | Needs a human at MFA anyway | Not applicable |

Required hardening for R1/R2 (all subject to SecOps approval):

- **Ephemeral:** the host (or at least the OS user, profile and display) is created per run window and destroyed afterwards; the Chrome profile lives on tmpfs (or an encrypted volume deleted at end); no snapshots/backups of the host while a session may be open; destruction verified (docs/34).
- **Owner-only access:** OneLogin proxy to the remote display with MFA and a single-user allow-list; no shared account; short idle and absolute timeouts; remote display not reachable from anywhere else; no public ingress.
- **Display controls:** clipboard, file transfer, drive and printer redirection off; no recording or screenshots persisted; no VNC password stored in the repo or notes.
- **Session in memory:** keep the existing runner behaviour of closing the CDP endpoint at the end; on Linux CDP stays bound to `127.0.0.1` with a random port; no other local users on the console host; the browser, runner and dashboard run as one dedicated OS user.
- **Window ownership:** as today, nobody touches the BackOffice window while a run is active (docs/53: WebSocket traffic and extensions are invisible to the request guard). The console host must have **no browser extensions** and no second profile.
- **Timing:** the console host is up only for the run window; the dry run (24 h limit), the clone (6 h) and the approval (7 days) timings in docs/53 are unchanged.

### 3.2 Salesforce fresh reads and the guarded UUID write from the VM side

Today (F3, F11) the VM has no Salesforce path, and the create needs: a fresh fixed read at the dashboard POST, a source read at the runner start, a re-read right before the approval is consumed, and (after `created_verified`) the pinned-alias `Account_UUID__c` write with read-after-write.

| Option | Reads | UUID write | Trade-offs |
|---|---|---|---|
| **S1. Owner's attended SSO session on the console host** (alias `surface-onboarding`, `sf org login web` inside the remote desktop) | yes, as on the desktop | yes, as on the desktop (same code, same gates) | Smallest delta, no new Salesforce identity; session is owner-scoped and in memory (the CLI auth home on tmpfs); depends on A2 and on the org allowing that login from the console host; the owner's token has the owner's full Salesforce permissions (docs/43 section 8 comparison) |
| S2. Read-only JWT service identity (docs/40 section 3, docs/44) for reads, owner's session only for the write | yes | S1 for the write only | Needs the wider-scope read approval anyway for the team queue; but two Salesforce paths on one host, more code branching |
| S3. Dedicated write identity (JWT, `Account_UUID__c` only, one object) for the UUID write | yes | yes | Narrowest write credential; but it is a stored key on a server, which conflicts with "VM holds no private data" (docs/43 section 8) unless kept on the ephemeral host and approved by SecOps; needs Salesforce admin work and a code branch replacing the `sf` CLI call |
| S4. UUID write stays manual or desktop-only | reads as S1/S2 | not from the VM | Safest; the create's `created_verified` result is still produced on the console host and the write is a separate attended step on the desktop |

Recommendation: **S1 for the first VM-side attempt** (it adds no new credential and keeps every existing gate, org pin and alias pin), and **S4 as the fallback** if A2 fails. S3 is only worth designing after the owner and Salesforce admin ask for unattended writes, which the owner has not requested. In every variant:

- the pinned alias (`surface-onboarding`) and the mandatory org-id pin (`SURFACE_SF_EXPECTED_ORG_ID`, file `salesforce-expected-org-id.txt`) must exist on the console host, set by the owner, never by this code;
- the write path stays the single guarded route (`production-uuid-review` then `production-uuid-confirm`), one go per write (D3), field `Account_UUID__c` only, pre-write re-read, `write_started` before the write, read-after-write; no generic SOQL or DML endpoint is exposed;
- VM Phase 1 behaviour on `workato-opa-01` (no `sf`, no write) does not change; only the console runtime enables these paths.

### 3.3 Outbound network and SecOps approvals

The console host needs a **default-deny egress allow-list**; SecOps names the exact hosts and ports. Candidates to request (to be confirmed by the owners of each service; not verified from the repository except where marked):

| Purpose | Destination | Basis |
|---|---|---|
| BackOffice production | `app.pentera.io` over HTTPS (the single production origin the runner permits) | verified (F6); plus whatever CDN/asset/identity hosts the SPA loads (unknown until a traced load; use the browser's own list, names only, never HARs with tokens) |
| Pentera SSO (OneLogin) | the tenant's OneLogin sign-in hosts | assumption; Identity to name them |
| Salesforce | login host and the org's instance host (for `sf` CLI and the SSO redirect) | assumption; Salesforce admin to name them (docs/43 section 6 listed "Salesforce login and instance hosts, Redash host, OneLogin") |
| Redash prod clone | only if the console host collects the clone itself; otherwise none (the clone arrives in the signed snapshot, docs/43 section 9, docs/51) | prefer the snapshot: no Redash key on the console host |
| Package mirrors | only during image build, not at run time | deploy step |
| Remote display ingress | TCP from the OneLogin proxy only (no public ingress) | docs/34 |

Inbound: nothing except the remote-display proxy. No route to or from `workato-agent`/OPA; no shared secrets, files, certificates or environment with it (docs/40 section 6).

SecOps/Identity approvals to request (union of docs/34, 36, 40):

1. A separate console host: placement, hardening baseline, EDR/patching, lifecycle (ephemeral creation and destruction, verified), owner-only access.
2. Egress allow-list above; no public ingress; firewall rule for the display proxy.
3. Remote display product, TLS, OneLogin registration, MFA, session/idle policy, no clipboard/file transfer, no recording.
4. Logging and monitoring: runner records stay the existing redacted JSON (names/pass-fail only); no screenshots, HARs, traces or page HTML; retention and incident response.
5. Salesforce: acceptance of the attended session from a new source IP (S1) and, if S3 is chosen, the write identity.
6. Whether BackOffice's WAF/conditional access permits the console host (A5).

### 3.4 Dashboard changes (VM pages today have no create/approval routes)

Today the VM dashboard is a read-only view of a snapshot with no create/approval/UUID routes (F4); the create card shows `vm_read_only`. **In Option P the console host does not use VM mode at all**: it runs a new third runtime, here called `console`, so the VM pages stay read-only and unchanged. Required changes (all local code + tests first, then reviewed, none implemented here):

| # | Change | Where | Notes |
|---|---|---|---|
| C1 | New runtime value (e.g. `SURFACE_ONBOARDING_RUNTIME=console`) distinct from `desktop` and `vm`; `local_browser_launch_allowed()` allows `console` only on Linux/Windows with an explicit approved-host marker; `vm_mode()`, `sf_command()` and the VM data-source switches remain false for `console` | dashboard, runner, `state_paths`, `session_readiness` | Fail closed on any other value; the VM unit still refuses browser launches; test that `vm` never launches |
| C2 | Linux-capable browser launch: Chrome/Chromium discovery for Linux, no `CREATE_NEW_PROCESS_GROUP`, process-group handling, display env, tmpfs profile root, `taskkill` replacement | runner | A3 |
| C3 | Linux hardening hook for the ticket folder (mode 0700 + ownership check) in place of `icacls`; `ticket_root()` must resolve inside the console user's private tmpfs/encrypted directory | `production_launch_ticket.py`, dashboard | `HARDEN` is currently Windows-only (fails closed) |
| C4 | Console login for the dashboard: keep the Salesforce-SSO login (works inside the remote desktop, A2) or use OneLogin identity; the dashboard binds `127.0.0.1` only and is reached from the remote desktop's own browser, so no inbound dashboard listener | dashboard | Preserve operator == approval issuer (owner only) |
| C5 | Create card/gate: replace the hard `inputs.vm` refusal with a runtime gate: console runtime + approved-host marker + owner-only; `vm` still refuses with `vm_read_only` | `production_create_ui.py` | The 12 gate conditions in docs/53 are unchanged |
| C6 | VM page wording: add a read-only "Create runs on the production console (owner only)" pointer with the console state (down/up, dry-run age) taken from a snapshot field, no link that starts anything | VM CO page | Optional; avoids owners hunting for the control |
| C7 | Publish the **result words only** from the console back to the VM snapshot (same one-way, signed, allow-listed sync as docs/41): `created_verified/...` plus record name, never the UUID | snapshot publisher | One writer per file (console host), VM only reads |
| C8 | Preserve the single-instance guard (no run already launched) and the 15-minute MFA wait; add console-host teardown hook that runs after a result | runner | |
| C9 | **Production Sessions page on the console dashboard (owner request 2026-10-19).** Once the owner is logged in, `/connection` shows Salesforce and **BackOffice production** (not Leonardo Development) with a Prepare sessions / Re-check action for each: Salesforce opens the real SSO window (the `sf org login web` flow of the desktop page, alias pinned), BackOffice opens `https://app.pentera.io/backoffice/tenantManagement` in the dedicated automation profile so the owner does SSO + MFA there; the page shows status only (Ready / sign-in needed / expired, age) and never receives credentials, MFA codes, cookies or tokens. Leonardo stays a Dev-only entry under `/dev`. On `workato-opa-01` (vm runtime) the same page is **status-only** (no sign-in action; a browser cannot run there): it shows the console's last published session state from the signed snapshot | dashboard `/connection`, `session_readiness.py`, a console-only BackOffice sign-in launcher (reuses `_pdr_browser_page`), snapshot field for the VM view | Builds on side branch `claude/adoring-clarke-80cb55` (`473d734`: read-only BackOffice row on the Sessions page). **That branch was cut from the UNLOCKED tree (it contains `1b98591` and `test_unlock_state.py`): rebase or cherry-pick onto the relocked HEAD (`52b8347`) and keep both constants False; never merge it as is.** BackOffice "Ready" must come from the runner's own results (a signed-in page that answers its own table request), as the dry runs do, not from DOM markers alone |

Launch ticket and approval locations in Option P (all inside one private, owner-only, ephemeral folder on the console host, `SURFACE_ONBOARDING_STATE_DIR` and `SURFACE_ONBOARDING_TICKET_ROOT`; never in the repository, a network share, or `workato-opa-01`):

- `…/production-launch-tickets/<CO>.json`, `.ticket.key`, `.used-nonces` (HMAC-signed, 2 minute TTL; key lost with the host = outstanding tickets invalid);
- `…/<approval file>` (issued on the console host by `tools/issue_production_approval.py`, typed `ISSUE CO-…`, by the owner);
- `…/production-create-records/` and `production-uuid-writes.jsonl` (append-only).

Because the host is ephemeral, **records must be copied out before teardown** to an approved, backed-up location (masked JSON only: this is the only evidence that an attempt exists, and an attempt-started record without its follow-up blocks the CO). Losing the records of an earlier attempt would let a new approval proceed without the "no earlier attempt waiting for review" check, so teardown must not happen until the record is verified copied (decision D7, and the dashboard should refuse to start when the previous run's records are missing from the sync target). The honest same-user trust limit from docs/53 carries over: anyone who can run as the console user can mint a ticket; hence one dedicated user, no other accounts, owner-only access.

In Option Q the same data would live on the VM and would cross hosts: a new protocol (docs/36: mTLS, fixed operations `create_start`/`status`, signed grants with source-revision binding, 15-minute health) and a hardened ticket handoff would be needed. That is the reason it is deferred.

### 3.5 Deploy and release steps

Unchanged rule: **local edits do not authorize external mutations; every step below needs its own owner go, SecOps approval where noted, and is operator-run one block at a time.** Format follows the project's owner-command convention (text plus `COMMAND_TO_PASTE.md`, labelled PC vs VM).

| Step | Action | Needs | Verify | Rollback |
|---|---|---|---|---|
| 0 | **Gate 0:** the desktop CO-0798 attempt is finished, relocked (`1b98591` + `853cf99` reverted) and its `session_not_ready` cause understood; the first VM-side attempt must not be the first live use of the context-level `LiveRequestGuard` | owner | Desktop `created_verified` (or understood refusal) recorded in plan section 18 | n/a |
| 1 | Local code: C1-C5, C7, C8, C9 with focused tests and the suite on Linux; locked by default; independent review; an Opus-style review of every path that touches the create | none (local) | Suite green on Windows and Linux; `vm` runtime still refuses launches; console runtime refuses without the host marker | Revert commits |
| 2 | Decisions D1-D6 (section 7) recorded in plan section 18 | owner, SecOps, Salesforce admin, Identity | Written approvals stored outside the repo | none |
| 3 | Build the console image (OS, Python 3.12, Playwright browser, remote display, no other users) as a **review artifact**; pin versions and checksums | SecOps approval of baseline | SBOM/checksums recorded; no secrets in the image | Delete the image |
| 4 | Provision one console host in the approved segment; apply egress allow-list and display proxy rule | SecOps, Identity | `ss -ltn` shows no listener except the display (loopback behind proxy); egress test reaches only allow-listed hosts | Destroy the host, remove rules |
| 5 | Deploy a **tagged release** of the repo to the console host (same procedure as the VM release, exact commit hash recorded; no `git pull` on the host; locks False in the shipped tree) | owner go | Hash on the host equals the reviewed commit; tests pass on the host | Re-deploy previous tag / destroy host |
| 6 | Set owner-only config (pinned Salesforce alias, `SURFACE_SF_EXPECTED_ORG_ID`, sessions flag in the dashboard process only, clone source) typed by the owner, never in chat | owner | `--production-readback` style read-only checks later | Destroy host |
| 7 | **Read-only rehearsal:** Salesforce SSO (S1), BackOffice SSO+MFA, then only the read-only probes (`--production-search-probe`, `--production-readback` on an existing tenant); no create | owner go; BackOffice owner awareness | Redacted records only; A2, A5 answered | Destroy host |
| 8 | **Dry run** from the console (same command and 24 h rule as docs/53 step 4) | owner go (D1 approval for that CO) | `dry_run_passed`, no mismatches | Destroy host |
| 9 | **One attended create** for one CO on the console (new approval, typed CO, one click, MFA in the remote window) | explicit owner go for that click | docs/53 result words; records copied out and verified before teardown | Docs/53 recovery (no delete path; owner decision) |
| 10 | UUID write (S1) only after `created_verified`, with its own go; then teardown; then relock (set both constants back to `False` in the release that follows) | owner go per write | read-after-write; records archived | docs/53 |
| 11 | Retire or repeat: each later CO is a new approval, new dry run, new run window; the host stays ephemeral | owner | — | — |

Each production create keeps docs/53's one-CO, one-approval, one-click, relock-afterwards discipline; this plan does **not** add a standing unlock on the console host.

## 4. Failure behaviour (fail closed)

Unchanged from docs/53 and AGENTS.md: ambiguity, mismatched source, unsupported route, duplicate, missing authority, auth/WAF failure, inactive OPA, unexpected schema, missing or expired approval, or a console host that cannot prove the launch-ticket folder is private all refuse before the consume. Console-specific: display disconnect during the MFA wait ends the run as a refusal; a disconnect **after** the click is `create_outcome_unknown` (CO blocked, never retried); loss of the console host or its records after an `attempt_started` line blocks the CO until the owner checks BackOffice by hand.

## 5. Risks

| Risk | Mitigation / owner |
|---|---|
| A signed-in BackOffice production session on a shared network host | Ephemeral, owner-only, tmpfs profile, no recording, egress allow-list (SecOps) |
| Remote display exposes tenant data and can be hijacked | OneLogin proxy, TLS, timeouts, no clipboard/transfer; no public ingress |
| Ticket/approval files readable by another local user | One dedicated OS user; private folder; Linux hardening hook (C3) |
| Records lost with the ephemeral host, hiding an unreviewed attempt | Copy-out before teardown; dashboard refuses start when records are missing (D7) |
| Owner's Salesforce token on the console host | In-memory CLI home on tmpfs, org + alias pin, host destroyed after use; S4 fallback |
| Linux behaviour untested (A1, A3) | Local Linux suite first; read-only rehearsal before any create |
| Desktop and console both able to create | Both locks False by default; one attempt per approval; the approval is consumed by the first create wherever it runs; keep the approval file on exactly one machine |
| First live create has not happened yet (`session_not_ready` cause open) | Gate 0 |

## 6. What this plan does not do

It does not edit code, flip any constant, mint an approval, start a browser, read Salesforce or BackOffice, touch the VM, or change `docs/40`'s Phase 1 decision for `workato-opa-01`. It does not estimate effort; the build is C1-C8 plus a Linux suite pass, then the SecOps-gated provisioning.

## 7. Ordered decision list (owner / SecOps / Salesforce admin)

Order matters: each item unblocks the next. "Clears" names who can lift the blocker.

| # | Decision / blocker | Clears | Notes |
|---|---|---|---|
| D0 | Finish and relock the desktop CO-0798 attempt first (gate 0); record the outcome and the `session_not_ready` root cause in plan section 18 | **Owner** | Nothing below should start before this proves the guarded flow end to end |
| D1 | Approve Option P (console host runs dashboard + runner, owner-only) vs Option Q (VM dashboard + remote runner, needs mTLS protocol) vs "stay on the desktop" | **Owner** | Recommendation: P |
| D2 | Console host OS: Ubuntu (A3 Linux port) or Windows ephemeral VM (existing code path) | **Owner + SecOps** | Pick one image to approve |
| D3 | Approve a separate ephemeral console host: placement, hardening, EDR/patching, lifecycle and verified destruction, no colocation with `workato-agent` | **SecOps (VM/Infrastructure)** | docs/34, 36 |
| D4 | Approve the remote display product, OneLogin gating, owner-only access, timeouts, clipboard/file-transfer off, no recording; name the proxy and certificate | **SecOps + Identity/OneLogin admin** | Replaces any "VNC password" |
| D5 | Approve the egress allow-list (BackOffice production host(s), OneLogin, Salesforce login and instance hosts; Redash only if collected there) and confirm BackOffice/OneLogin/Salesforce do not block the console host (A5) | **SecOps; BackOffice owner; Identity; Salesforce admin** | Names every host in writing |
| D6 | Salesforce path: S1 attended owner session (recommended), S4 write stays desktop-only (fallback), or S3 dedicated write identity for `Account_UUID__c` only; and confirm the org Id to pin | **Salesforce admin + SecOps; owner for the org-id file** | docs/36 item 2: exact scopes and query contract in writing; the read-only JWT identity of docs/40/44 does not cover the write |
| D7 | Record handling on an ephemeral host: where masked records are copied (approved, backed-up location), retention, and whether the dashboard refuses to start when prior records are missing | **Owner + Data/Operations + SecOps** | Protects the "no earlier attempt waiting" gate |
| D8 | Whether a second person must approve production creates and who else (if anyone) may operate the console; default stays owner-only | **Owner** | docs/43 V5, docs/36 "Milton only" |
| D9 | Release and relock policy: who deploys the tag, that the shipped tree is locked, and that every create is a one-CO, one-approval window | **Owner (+ Operations for deployment)** | docs/53 |
| D10 | Go for each live step on the console in order: read-only rehearsal, dry run, one create, UUID write | **Owner, one explicit go each** | Never batched |

Security/Identity/Salesforce approvals above are separate written approvals; none is implied by this document or by the owner's earlier Phase 1 answers (docs/40 section 8).
