# VM production console: approval packet (2026-10-19)

**Status: DRAFT FOR THE OWNER TO SEND. Nothing in this packet is approved, built, installed or provisioned.** It asks four approver groups for written decisions. It relaxes no gate: docs/34, 36, 40, 42, 43, 45, 53 and the plan in docs/54 stay in force. Decision numbers D0-D10 below are the ones in `docs/54_VM_PHASE2_PRODUCTION_RUNNER_PLAN_2026-10-19.md` section 7; read that section and sections 3.1-3.5 for the full design.

Conventions: **Verified** = read from the repository docs or code on 2026-10-19. **ASSUMPTION** = not verified; each one is a question to the approver. No password, token, org id, UUID, cookie or customer data appears here or is requested in the answers.

---

## Part 0. One-page cover summary (owner)

**Current outcome.** The guarded production create (one Customer Onboarding, "CE only", docs/53) has only ever been prepared for the owner's Windows desktop. The first live attempt on 2026-10-19 refused `production_session_not_ready` before the form; nothing was created. Both production code constants are `False` again (relocked 2026-10-19). I want the create and its confirmation to run from the VM side instead of only from the desktop.

**Design direction (owner decisions 2026-10-19: D1 = Option P, D2 = Ubuntu console host).** Not a browser on `workato-opa-01` (docs/34, 36, 40 forbid it). Instead a **separate, ephemeral, owner-only "production console" host** that runs the existing dashboard and runner together, reached only through an authenticated remote display. The owner does SSO and MFA by hand in a browser on that host; the session lives in memory and dies with the host. BackOffice has no API or token, and none is requested.

**What is authorized today:** local code and tests only (docs/54 items C1-C5, C7, C8, C9), locked by default. **Nothing external:** no VM, host, SecOps, Salesforce, BackOffice or Workato action has been taken or is authorized.

**What I need, in order.**

| Order | Approver | Decision (docs/54 ref.) | Blocks |
|---|---|---|---|
| 0 | Owner (me) | D0: finish and relock the desktop CO-0798 attempt first (gate 0) | everything below going live |
| 1 | SecOps / VM-Infrastructure | D3 console host; D5 egress; D7 records; D4 display product (with Identity) | provisioning |
| 2 | Identity / OneLogin admin | D4 gating of the remote display; D5 OneLogin hosts and conditional-access behaviour | the display and the sign-in |
| 3 | Salesforce admin | D6 Salesforce path (attended session, org pin, write for `Account_UUID__c` only) | reads and the UUID write |
| 4 | BackOffice owner (product) | D5 part: no WAF/conditional-access block of the console host; awareness of the production create | the create itself |
| 5 | Owner | D8, D9, D10: operators, release/relock policy, one explicit go per live step | each live step |

**What does not change.** The desktop path stays the only production path until these are recorded. Each create stays one CO, one approval, one click, relock afterwards. There is no standing unlock on the console host. Nothing is retried after an uncertain outcome.

**Owner decisions still open for me:** D8 (second approver; default stays owner-only), D9 (who deploys the tagged release), D10 (one go per live step, never batched).

**Known gaps (see Part 6):** the console host name, network segment and display product are unchosen; most egress host names are unknown; Linux behaviour of the code is untested (A1, A3); `sf org login web` inside a remote desktop is unproven (A2).

---

## Part 1. Common facts and controls (identical for all four approvers)

### 1.1 What the console is

| Item | Statement | Basis |
|---|---|---|
| Purpose | Run the existing guarded production CE-only create (one CO) and its read-after-write checks without sitting at the Windows desktop | docs/54 section 0 |
| Shape | One dedicated OS user on one ephemeral Ubuntu host runs the dashboard (bound to `127.0.0.1`) and the runner and Chrome together; the owner reaches it through an authenticated remote display only | docs/54 sections 2, 3.4 (Option P) |
| Not | Not on `workato-opa-01`; not colocated with `workato-agent`/OPA; no shared secrets, files, certificates or environment with them | docs/34, 36, 40 section 6; docs/54 section 3.3 |
| `workato-opa-01` | Unchanged: Phase 1 read-only, no browser, no `sf`, no write, every POST refused in VM mode | docs/54 F2-F4, section 3.4 |
| MFA | Human, in the real browser on the console host, in memory only. No service account, API token, cookie copy or MFA bypass | docs/54 section 3.1 (R1; R3-R5 rejected) |
| What the create does | Re-read the CO, ordered gates, live duplicate check in BackOffice, exactly one `POST /api/v1/backoffice/account/add`, bounded readback, redacted record, then a separate guarded write of `Account_UUID__c` only | docs/53 |
| Scope of one run | One CO, one approval (typed `ISSUE CO-...`, 7 days, consumed by the create), one launch ticket (HMAC, 2 min), one click | docs/53, docs/54 F8-F9 |

### 1.2 Controls the project commits to (all subject to the approvers' own written conditions)

1. **Ephemeral host.** Created per run window, destroyed afterwards, destruction verified. No snapshots or backups of the host while a session may be open. The host is up only for the run window.
2. **Owner-only.** One person; one dedicated OS user for dashboard, runner and browser; no other local accounts; no shared account; single-user allow-list at the display proxy; short idle and absolute timeouts.
3. **No persistent browser state.** The Chrome profile and the Salesforce CLI auth home live on tmpfs (or an encrypted volume deleted at the end). No browser extensions, no second profile. CDP stays on `127.0.0.1` with a random port and is closed by the runner at the end.
4. **No recording, no stored evidence of the display.** No screen recording, screenshots, HARs, traces or page HTML persisted. Runner records stay the existing redacted JSON (field names and pass/fail words only).
5. **Clipboard, file transfer, drive and printer redirection off** in the remote display.
6. **Default-deny egress** with a named allow-list (Part 3); **no public ingress**; inbound only from the display proxy.
7. **Records copied out before teardown.** Masked records (`production-create-records/`, `production-uuid-writes.jsonl`, SHA-256 only for the UUID) are copied to an approved, backed-up location and verified before the host is destroyed. The dashboard should refuse to start when a previous run's records are missing from that location (design intent D7; **not yet built**).
8. **One-CO, one-approval, one-click create.** Every create is a new approval, new dry run (at most 24 h old), fresh Redash clone (under 6 h), new run window. A click whose outcome is uncertain is never retried (`create_outcome_unknown` blocks the CO until the owner checks BackOffice by hand).
9. **Both production code constants are `False` in anything shipped** (`PRODUCTION_RUNNER_SUPPORTED`, `PRODUCTION_DRY_RUN_SUPPORTED`). They are set `True` only by a separate, reviewed, owner-approved commit for one attempt and reverted afterwards. The release is a tagged commit with a recorded hash; no `git pull` on the host.
10. **Fail closed** on ambiguity, mismatch, unsupported route, duplicate, missing authority, auth/WAF failure, inactive OPA, unexpected schema, missing or expired approval, or a host that cannot prove its ticket folder is private.
11. **Nothing secret in the repository, chat or notes**: no VNC/display password, no cookie, token or MFA code, no org id in this repository's docs.

### 1.3 What none of the four approvers is being asked to approve

- Any browser, runner or write path on `workato-opa-01` or any change to its Phase 1 decision.
- A standing or unattended production create, any scheduled run, or any non-owner operator (D8 default: owner only).
- Any BackOffice API, service token, service account or MFA bypass (none exists; none is requested).
- Production BackOffice writes beyond the single guarded `account/add` for one CO per approval; any delete or disable path (none exists).
- Setting either production constant to `True` (a separate owner decision per attempt).
- A time estimate or commitment to build.

### 1.4 Build status (so nobody approves something imagined)

| Item | State |
|---|---|
| docs/54 C1-C5, C7, C8, C9 (console runtime, Linux port, Linux ticket hardening, console login, create gate, result publish, teardown hook, Sessions page) | **Not built.** Authorized as local code and tests only, locked by default |
| Console image, host, display product, firewall rules | **Not built, not chosen** |
| Linux behaviour (Chrome/Chromium launch, `sf org login web`, headless-looking fingerprint) | **Not tested** (A1, A2, A3) |
| Desktop create | **Never completed live.** First attempt refused before the form (`production_session_not_ready`); dry run #7 passed 2026-10-18 |

---

## Part 2. Group 1: SecOps / VM-Infrastructure

**Decisions requested (docs/54 D3, D5, D7, with D4 jointly with Identity):**

- **D3**: approve a *separate ephemeral production console host*: placement/segment, hardening baseline, EDR and patching, create-and-destroy lifecycle with verified destruction, no colocation with `workato-agent`/OPA, owner-only access.
- **D4 (joint with Identity)**: approve the remote display product, TLS, timeouts, clipboard/file-transfer off, no recording.
- **D5 (egress part)**: approve a default-deny egress allow-list (Part 3), no public ingress, and the firewall rule for the display proxy.
- **D7**: approve where masked records are copied before teardown, their retention, and the teardown-after-copy rule.
- Logging, monitoring and incident response for the console host.

**Why.** docs/34 and docs/36 require a separate, single-user, non-persistent runner host with SecOps-approved streaming, egress and destruction controls before any runner exists; docs/54 turns that into one concrete host design. Without these approvals nothing can be provisioned.

**Not asked:** anything on `workato-opa-01`; an unattended or multi-user host; a persistent host; any change to Phase 1; approval to flip a production constant.

**Controls committed:** Part 1.2 items 1-7, 10; the host is created from a pinned, checksummed image (OS, Python 3.12, Playwright browser, remote display, no other users) delivered as a review artifact with an SBOM before provisioning (docs/54 step 3); after provisioning `ss -ltn` must show no listener except the display (loopback behind the proxy) and an egress test must reach only allow-listed hosts (docs/54 step 4).

**Written answers required:**

1. Placement/network segment and hardening baseline; EDR and patch policy; who creates and destroys the host and how destruction is verified (evidence format).
2. Final **egress allow-list with exact host names and ports** for each row of Part 3, including any CDN/asset/identity hosts the BackOffice SPA loads (to be obtained from a traced load; names only, never HARs with tokens).
3. Remote display product and version, proxy and certificate, TLS settings, how clipboard, file transfer, drive, printer and recording are disabled and verified, idle and absolute session timeouts.
4. How the display is gated to a single owner identity (with Identity) and that there is no public ingress.
5. **Evidence and retention:** what host/proxy/firewall logs are kept, for how long, who can read them, that they contain no BackOffice page content; approved destination, retention and backup for the copied-out masked records; incident-response contact for suspected session exposure.
6. Are snapshots/backups of the host prevented while a session is open? How?
7. Is storing a Salesforce session of the owner (in memory/tmpfs CLI home, destroyed with the host) on the console acceptable? If a dedicated write identity is later chosen (S3), is a key on the ephemeral host acceptable? (S3 is not requested now.)
8. Does the console host's IP/segment trigger any WAF, geo or reputation rule you operate? (ASSUMPTION A5)

**Decision block**

```
SecOps / VM-Infrastructure
D3 console host        [ ] APPROVE   [ ] REJECT   [ ] APPROVE WITH CONDITIONS
D4 remote display      [ ] APPROVE   [ ] REJECT   [ ] APPROVE WITH CONDITIONS   (joint with Identity)
D5 egress allow-list   [ ] APPROVE   [ ] REJECT   [ ] APPROVE WITH CONDITIONS
D7 records/retention   [ ] APPROVE   [ ] REJECT   [ ] APPROVE WITH CONDITIONS
Name: ____________________   Role: ____________________   Date: ____________
Conditions: ______________________________________________________________
Written approval location (outside the repo): _____________________________
```

---

## Part 3. Egress candidates (shared by SecOps, Identity, Salesforce admin, BackOffice owner)

Default deny. Each owner confirms or corrects the row that is theirs. Only the BackOffice origin is verified.

| Purpose | Candidate destination | Basis | Confirmed by (name, date) |
|---|---|---|---|
| BackOffice production | `app.pentera.io` over HTTPS (the single production origin the runner permits; start page `/backoffice/tenantManagement`) | Verified (docs/54 F6) | |
| BackOffice SPA dependencies | Unknown CDN/asset/identity hosts the SPA loads | ASSUMPTION: unknown until a traced load; names only | |
| OneLogin (Pentera SSO) | The tenant's OneLogin sign-in hosts | ASSUMPTION; Identity to name them | |
| Salesforce | Login host and the org's instance host (for the `sf` CLI and the SSO redirect) | ASSUMPTION; Salesforce admin to name them (docs/43 section 6 listed Salesforce login/instance, Redash, OneLogin) | |
| Redash prod clone | None by default: the clone arrives in the signed snapshot (docs/43 section 9, docs/51); no Redash key on the console host | Preference, docs/54 section 3.3 | |
| Package mirrors | Image build time only, not run time | docs/54 section 3.3 | |
| Remote display ingress | TCP from the OneLogin proxy only; no public ingress | docs/34 | |

Inbound: nothing except the display proxy. No route to or from `workato-agent`/OPA.

---

## Part 4. Group 2: Identity / OneLogin admin

**Decisions requested (docs/54 D4 jointly with SecOps; D5 identity part):**

- **D4**: approve gating of the remote display behind OneLogin: registration of the proxy application, MFA, single-user allow-list (owner only), session and idle policy.
- **D5 (identity part)**: confirm that sign-in to BackOffice (`app.pentera.io`) via OneLogin from the console host's source IP and a headless-looking Chrome is permitted, or state the conditions.

**Why.** The owner must do SSO + MFA by hand in a real browser on a host that is not the desktop, and the display that exposes that browser must itself be strongly authenticated. MFA is preserved; no credential is stored.

**Not asked:** any MFA bypass, service account, token, password manager entry or trusted-device exemption for automation; any additional user; any change to the existing OneLogin policy for other users.

**Controls committed:** Part 1.2 items 2, 3, 5, 6, 11; the project handles no VNC password, cookie, token or MFA code; there is no stored credential of any kind on the host.

**Written answers required:**

1. OneLogin sign-in **host names** the console browser must reach (for Part 3).
2. **Conditional-access / risk behaviour** for a sign-in from the console host's IP (new, possibly non-corporate, non-managed device): blocked, step-up, device-trust required, geo or IP allow-list? If a managed-device or IP requirement exists, what must the console host satisfy? (ASSUMPTION A5)
3. Does a headless-looking Chrome fingerprint (Linux, virtual display, no extensions, fresh profile each run) trigger bot or risk detection at OneLogin? If it triggers a challenge, which, and is it acceptable for a human to complete it?
4. Can the display proxy be registered as an application gated to exactly one named user with MFA, and what session/idle/absolute timeouts apply?
5. Are sign-in events from the console host logged and who reviews them?
6. Salesforce sign-in uses SSO through OneLogin: any additional restriction for that flow from a new source IP? (cross-check with Salesforce admin)

**Decision block**

```
Identity / OneLogin admin
D4 display gated by OneLogin, owner only   [ ] APPROVE  [ ] REJECT  [ ] APPROVE WITH CONDITIONS
D5 sign-in from console host permitted     [ ] APPROVE  [ ] REJECT  [ ] APPROVE WITH CONDITIONS
Name: ____________________   Role: ____________________   Date: ____________
Conditions: ______________________________________________________________
Written approval location (outside the repo): _____________________________
```

---

## Part 5. Group 3: Salesforce admin

**Decisions requested (docs/54 D6; D5 Salesforce part):**

- **D6**: choose the Salesforce path for the console host:
  - **S1 (recommended)**: the owner's attended SSO session (`sf org login web` inside the remote desktop, alias `surface-onboarding`, CLI auth home on tmpfs, destroyed with the host). It adds no new credential. Used for the fresh reads, the revision re-check and the UUID write, exactly as on the desktop.
  - **S4 (fallback)**: reads as S1; the UUID write stays manual or desktop-only.
  - **S3 (not requested now)**: a dedicated write identity for `Customer_Onboarding__c.Account_UUID__c` only. Needs its own design and SecOps approval of a stored key; the existing read-only JWT identity (docs/40, 43, 44) is read-only by design and cannot do the write.
- Confirm the **org to pin** (owner types the org id on the console host in `SURFACE_SF_EXPECTED_ORG_ID` or `salesforce-expected-org-id.txt`; never in this repository). The runner refuses (`salesforce_org_not_pinned`) when it is missing, malformed or different.
- **D5 (Salesforce part)**: confirm the Salesforce login and instance host names for the egress list and that a login from a new source IP is not blocked.

**Why.** The create re-reads the CO from Salesforce (fresh read at the dashboard POST, source read at runner start, re-read before the approval is consumed) and, only after `created_verified`, writes `Account_UUID__c` with a pre-write re-read, a `write_started` line before the write and read-after-write. The VM has no Salesforce path today (docs/54 F3).

**Not asked:** any broader write, generic SOQL/DML endpoint, new object or field permission, new connected app or integration user (unless S3 is later chosen), or change to the read-only JWT identity decision. Field `Account_UUID__c` only; one go per write.

**Controls committed:** Part 1.2 items 1, 3, 8, 10, 11; pinned alias `surface-onboarding` (an alias override is refused); org-id pin mandatory; the write is one guarded route (`production-uuid-review`, then `production-uuid-confirm`), with the UUID stored only as SHA-256 in the record; an earlier write record (even rejected) blocks another automatic write until reviewed.

**Written answers required:**

1. S1, S4 or (later) S3: which is acceptable?
2. **Is a write path limited to `Customer_Onboarding__c.Account_UUID__c` only acceptable**, and under which identity (owner's own session = S1; any other = S3)? State the permission set the owner's session has for that field today, if known. (ASSUMPTION A7: not verified)
3. **Can a login from a new source IP** (the console host) use `sf org login web` via SSO inside a remote desktop? Are there login-IP ranges, session-security, connected-app IP relaxation or device-activation challenges that would block or complicate it? (ASSUMPTION A2: unproven; docs/43 section 8 recorded device login blocked since 2025-08-28 and `sf org login web` unsupported on a *headless* host)
4. Salesforce **login host and instance host names** for the egress list.
5. The **org to pin**: confirm which org (production) the alias must resolve to; the owner will enter the id privately, do not send it in reply to this packet.
6. Salesforce-side audit: will the writes be visible in field history/event monitoring, and is anything else needed for that?
7. If S3 is ever wanted: scopes, query contract and key-handling in writing (docs/36 item 2). Not required for this approval.

**Decision block**

```
Salesforce admin
D6 path   [ ] S1 attended session   [ ] S4 write stays desktop-only   [ ] S3 (new design needed)   [ ] REJECT
Write limited to Account_UUID__c only acceptable   [ ] YES   [ ] NO   [ ] YES WITH CONDITIONS
D5 login from new source IP not blocked            [ ] CONFIRMED   [ ] BLOCKED   [ ] CONDITIONS
Name: ____________________   Role: ____________________   Date: ____________
Conditions: ______________________________________________________________
Written approval location (outside the repo): _____________________________
```

---

## Part 6. Group 4: BackOffice owner (product side)

**Decisions requested (docs/54 D5 BackOffice part; acknowledgement of the create):**

- Confirm that **BackOffice (`app.pentera.io`) and its WAF / bot / conditional-access layer will not block** an owner-attended session from the console host's IP with a headless-looking Chrome, or state the conditions (ASSUMPTION A5; AGENTS.md: a WAF or authentication failure fails closed).
- Acknowledge the guarded production create: one CO at a time, CE-only, one `POST /api/v1/backoffice/account/add` per attempt, preceded by a live duplicate check and followed by a read-only readback.
- Confirm the full list of host names the BackOffice SPA needs (Part 3).

**Why.** BackOffice has no API or service token. The only way to run the create is the owner signing in with SSO + MFA and the runner driving the page. The product side owns the application and its production tenant data.

**Not asked:** any API, token, service account, MFA exemption, IP allow-listing of automation as trusted, WAF rule weakening, or a delete/disable path (none is known; wrong tenants are corrected by the owner by edit in BackOffice after a recorded decision, docs/53 recovery). No approval to create tenants unattended, in bulk or for any other CO type.

**Controls committed:** Part 1.2 items 4, 8, 10; the runner permits exactly the origin `https://app.pentera.io`, aborts any other write and allows exactly one `account/add`; an uncertain outcome blocks the CO and is never retried; records contain field names and pass/fail words only; the owner keeps the BackOffice window to themselves during a run.

**Written answers required:**

1. **WAF / conditional-access behaviour** for the console host's IP and a headless-looking Chrome (Linux, virtual display): allowed, challenged, rate-limited or blocked? Any request-rate or automation-detection limits that a read-only table sort/reload pattern (the page's own `getAllDetailedAccounts` requests, up to six readback tries three seconds apart) could trip?
2. Are the BackOffice SPA's **extra hosts** (CDN, assets, identity) listed? Anything beyond `app.pentera.io`?
3. Is the **production create by an attended, guarded automation** acceptable to the product owner, including that a created tenant has no delete path and a mismatch is fixed by manual edit? Who is the contact if a run produces `create_outcome_unknown` or `created_mismatch`?
4. Is there any change window, notification or audit expectation for tenants created this way (for example a ticket reference in the record)?
5. Any BackOffice behaviour known to differ between sessions from a different IP/device (session binding, token rebinding) that could cause `production_session_not_ready` or 401s?

**Decision block**

```
BackOffice owner (product)
No WAF / conditional-access block of the console host   [ ] CONFIRMED  [ ] BLOCKED  [ ] CONDITIONS
Acknowledge guarded one-CO production create            [ ] ACKNOWLEDGED  [ ] REJECT  [ ] CONDITIONS
Name: ____________________   Role: ____________________   Date: ____________
Conditions: ______________________________________________________________
Written approval location (outside the repo): _____________________________
```

---

## Part 7. Assumptions and gaps (read before approving)

| # | Item | Status |
|---|---|---|
| A1 | Chromium with a virtual display runs the BackOffice SPA and the request-guard/CDP logic as on Windows | ASSUMPTION; untested |
| A2 | `sf org login web` works inside a remote desktop on the console host | ASSUMPTION; unproven; fallback S4 or an admin-chosen client |
| A3 | The Python code path is Linux-clean for a `console` runtime (it has Windows-only branches) | ASSUMPTION; suite never run on Linux for this purpose |
| A4 | A remote display product can be approved and gated behind OneLogin | ASSUMPTION; product not chosen |
| A5 | BackOffice, OneLogin and Salesforce do not block the console host or a headless-looking Chrome | ASSUMPTION; the questions above settle it |
| A6 | The cause of the first live attempt's `production_session_not_ready` is fixed on the desktop first (gate 0 / D0) | OPEN; nothing in this packet should go live before it is understood |
| A7 | Salesforce admin accepts a write for `Account_UUID__c` only | ASSUMPTION; Salesforce admin question |
| G1 | Console host name, segment, image and display product | Not chosen |
| G2 | Egress host names other than `app.pentera.io` | Unknown |
| G3 | Approved destination and retention for copied-out records | Not chosen |
| G4 | "Dashboard refuses to start when prior records are missing" (D7) | Design intent, not built |
| G5 | Linux hardening hook for the ticket folder (docs/54 C3); today the hardening is Windows-only and fails closed | Not built |
| G6 | Console-state "pointer" on VM pages (C6) | Optional, not built |

---

## Part 8. Tracking table

Written approvals live **outside the repository**. Fill the last column with a ticket, mailbox or document reference, never a copy of any secret.

| Decision (docs/54) | Approver | Status | Date | Written approval location (outside repo) |
|---|---|---|---|---|
| D0 Finish and relock desktop CO-0798 attempt; record `session_not_ready` cause | Owner | Open | | |
| D1 Option P | Owner | Recorded 2026-10-19 (docs/54) | 2026-10-19 | docs/54 header |
| D2 Ubuntu console host | Owner (+ SecOps picks image) | Owner recorded 2026-10-19; SecOps image approval open | 2026-10-19 | docs/54 header |
| D3 Separate ephemeral console host | SecOps / VM-Infrastructure | Not sent | | |
| D4 Remote display product, OneLogin gating, owner-only | SecOps + Identity / OneLogin admin | Not sent | | |
| D5 Egress allow-list and no block by BackOffice/OneLogin/Salesforce | SecOps; BackOffice owner; Identity; Salesforce admin | Not sent | | |
| D6 Salesforce path (S1/S4/S3), `Account_UUID__c`-only write, org pin | Salesforce admin (+ SecOps; owner for org-id file) | Not sent | | |
| D7 Record copy-out, retention, teardown rule | Owner + Data/Operations + SecOps | Not sent | | |
| D8 Second approver / other operators (default owner-only) | Owner | Open | | |
| D9 Release and relock policy | Owner (+ Operations) | Open | | |
| D10 One explicit go per live step (rehearsal, dry run, create, UUID write) | Owner | Open; per step | | |
