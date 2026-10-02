# Guru rules, gap analysis, and renewal mapping (Cases 4–6) — 2026-10-01

Read-only analysis for the next session. Sources were read in the operator's signed-in Chrome on 2026-10-01; all four cards are **Verified**. No Leonardo, Salesforce, or VM change was made for this document. Customer names and domains visible in card screenshots are deliberately not reproduced.

| Card | State | Last update shown |
| --- | --- | --- |
| Surface & Credential Exposure Onboarding Guide (`idrRoyMT`) | Verified | ~5 months ago |
| Surface Customer Onboarding - New Surface Account Only (`cBop6eXi`) | Verified | (earlier notes: ~4 months) |
| Surface Customer Onboarding - New Credential Exposure only (`cxE5o79i`) | Verified | — |
| Surface Customer Onboarding - Renew existing Surface and/or Credential exposure (`cbqzy5Ai`) | Verified | ~4 months ago |

The guide's two diagrams are the original concept: Slack request → 6-case router → JSON → validate against Salesforce → Surface API → update Salesforce UID/UUID → status message in Slack (and a Slack Workflow / Apps Script / Google Sheet variant with the Surface API "stubbed for now").

---

## 1. Rules extracted from the cards

Status: **✅ implemented**, **🟡 partly / differs by owner decision**, **⬜ not yet**.

### Before any request (guide)

| # | Rule | Status |
| --- | --- | --- |
| G1 | Validate the request against the Salesforce opportunity: customer name, country, domain(s), licence type, expiration, number of subdomains; reject/resubmit on mismatch | 🟡 fixed SOQL reads + fail-closed validation; no "reject/resubmit" action (manual) |
| G2 | Licence must be active; know when it starts and expires | ✅ Active, or Pending ≤ 14 days (`SURFACE_PENDING_START_WINDOW_DAYS`) |
| G3 | Pending approval → move to Approved before creating | 🟡 dashboard requires Approved; approval stays manual in Salesforce |
| G4 | Confirm Core Plus for CE, Surface, or both; CE-only without Core Plus → reject/hold | ✅ CE: Core Plus Commercial required; Case 3: Core Plus required |
| G5 | Salesforce is the source of truth (domain, country, limits, expiration, CSM, TA, primary user) | ✅ fresh reads; TA/CSM not read yet |
| G6 | New account → Add Account; renewal → locate the existing tenant | ✅ Add Account; ⬜ locate/edit for renewals |
| G7 | Existing active tenant: check entitlements, tenant users, logins, scans, scan completed; "ping Ohad/Chen to check UUID linked in Castro"; then finish the flow | 🟡 scan status ✅ (`--scan-status`); users/logins/Castro ⬜ |
| G8 | Renewal may proceed if its licence activates within two weeks | 🟡 same 14-day window exists for new COs; not yet applied to renewals |

### Case summaries (guide)

| Case | Guide rule | Status |
| --- | --- | --- |
| 1 New Surface only | Surface advanced profile; enable Scan now; interval Prime weekly / Go monthly; "Leaked Credentials remain" | ✅ (Scan now absent once a schedule is set — live finding; LC OFF by owner decision) |
| 2 New CE only | All Advanced toggles off; LC ON weekly; scanned domains from Salesforce; Scan now off | ✅ |
| 3 New Surface + CE | Combine Surface and CE rules | ✅ built locally (`case_3_combined_baseline`), not run live |
| 4 Renew Surface + new CE | Tenant exists; turn on LC weekly with Salesforce domains; revalidate Surface profile; edit expiration | ⬜ |
| 5 Renew CE + new Surface | Tenant exists; enable Surface profile, interval from licence, Scan now; keep LC per renewed entitlement; edit expiration | ⬜ |
| 6 Renew both | Update licence type, asset/domain/subdomain limits, expiration, operator if TA changed; revalidate toggles against policy | ⬜ |

### New Surface card

| # | Rule | Status |
| --- | --- | --- |
| S1 | Primary domain valid and not a subdomain | ✅ `classify_surface_domains` |
| S2 | Company name, Customer, primary/alternate/sub domains, networks if provided, user email domains `pentera.io`, country | ✅ (networks: verified empty; a CO with networks fails closed) |
| S3 | MFA always ON; primary user = operator with `first.last+company@pentera.io` | ✅ |
| S4 | Operator Account from TA and CSM | 🟡 owner: empty at create, assign after first scan (reminder) |
| S5 | Interval Prime/Enterprise weekly, Go/Essentials/Professional monthly | ✅ |
| S6 | Advanced: automated discovery OFF, Nuclei ON, **static outbound IP ON only if requested by Salesforce/contract**, max scan 90 h | ✅ static IP always OFF (no Salesforce signal mapped) |
| S7 | Licence: Provisioning ON, subdomains ON, Prepaid annual; assets default unless licence says otherwise; domains/subdomains/expiration from Salesforce | ✅ (assets 10,000; domains = subdomains = licensed subdomains, owner 9/30) |
| S8 | Save, search, open details, put UID + UUID in Salesforce, stage Account Scanning | 🟡 readback ✅; **Salesforce update deliberately off** (owner 10/01: dashboard only, env dev) |
| S9 | Scan ≥ 3 h; check next day that it completed with valid results | 🟡 scan status ✅ (`COMPLETED` confirmed); "valid results" (subdomains, vulns, data) ⬜ |
| S10 | Then create the customer admin user: add customer email domain, create user; stage User Created | ⬜ |

### New CE card

| # | Rule | Status |
| --- | --- | --- |
| C1 | CE/Core Plus included; country, domain(s), licence type, expiration, domain count | ✅ |
| C2 | One scanned domain per CE licence; more needs Product approval or a dedicated licence | ✅ exactly one CE email domain; add-ons not mapped |
| C3 | "- CE only" naming when team practice requires | ✅ `<Account> - CE Only` |
| C4 | Primary domain from Salesforce; only alternate/sub domains Salesforce provides | 🟡 owner rule: CE primary domain = the single `Email_Domains__c` value (09-21) |
| C5 | Primary user = yourself (customer needs no access) | ✅ |
| C6 | Interval None, Scan now off, all Advanced toggles off | ✅ |
| C7 | LC ON, always Weekly | ✅ |
| C8 | Licence: Provisioning ON, subdomains ON, Prepaid annual, domains 1, expiration from Salesforce | ✅ (1/1/1) |
| C9 | Copy the **UUID** (CE only needs UUID) to Salesforce; stage User Created | 🟡 captured; Salesforce update off by owner decision |

### Renew card (the basis for Cases 4–6)

| # | Rule | Status |
| --- | --- | --- |
| R1 | Request matches the opportunity | ⬜ (reuse G1) |
| R2 | Additional domains on renewal: confirm approved ("evaluation was running on them")/licensed before adding; same domains → proceed | ⬜ needs current-tenant domain read |
| R3 | Open the existing tenant in Back Office and **Edit** it | ⬜ Edit flow not mapped |
| R4 | Check expiration, **never change the start date**, subdomains, domains, asset limit, licence type against Salesforce | ⬜ |
| R5 | Renew/update features per entitlement: Surface settings (same profile as new: 90 h, discovery OFF, recon ON, MAS off, brute force ON, dorking OFF, Nuclei ON, auth testing OFF, static IP OFF, AI OFF, Notifications/Multiple users/API ON; interval per tier) | ⬜ (reuse `SURFACE_ADVANCED_TOGGLES`) |
| R6 | CE: LC ON, Weekly, scanned domains match Salesforce; Phishing OFF; Provisioning/subdomains ON; Prepaid annual | ⬜ (reuse CE overlay) |
| R7 | Update operator if the TA changed | ⬜ |
| R8 | User email domains must include the primary user's domain; add it if needed, then add the user | ⬜ |
| R9 | Save; add/update UUID and/or UID in Salesforce; stage User Created | ⬜ (Salesforce writes off) |

---

## 2. What we already have (as of `13af2f7`)

- **Routes live-proven in Leonardo Development:** Case 2 (CO-0679, CO-0728, CO-0762 created; CO-0702 verified) and Case 1 (CO-0649 created, scanned, `COMPLETED`).
- **Case 3** built locally (combined contract, term rule 1a with plain message, CE email-domain duplicate lookup, both IDs mapped).
- **Runner safety:** fixed SOQL only; route gates; revision drift checks; duplicate check by name, primary domain (and CE email domain for Case 3); `salesforce_id_already_present` pre-create gate; dry runs require an enabled Confirm; licence-start one-day fallback; uncertain submit never re-clicked; read-after-write readback; persisted Dev automation profile on the desktop only.
- **Read-only checks:** `--readback-only`, `--duplicate-check`, `--scan-status`, `--scan-status-all` (Surface/Case 3 only), session check/bootstrap.
- **Dashboard (desktop, loopback):** queue tiles and rules, History tab, CO pages with scope review, Start, outcome banners, reminders (CE later, scanning off, Operator), entered licence dates, Salesforce IDs panel (**dashboard only, Environment: dev**), Leonardo scan status card, 2-minute display cache, parallel preflight.
- **VM:** inactive tested copies r19/r20 under `/opt/surface-onboarding/`; synthetic design preview (`tools/serve_loopback_preview.py`, SSH tunnel only).
- **Tests:** 605 integration (1 skipped), 55 `phase1_validator`, 49 `phase2_leonardo`; Salesforce CLI tripwire = 0 real calls.

## 3. What is needed to finish

| Area | Gap | Blocked by |
| --- | --- | --- |
| Renewals (Cases 4–6) | Edit Account flow: locate tenant, open Edit, read current values, compute diff, set only allowed fields, never touch start date, Confirm, readback | Edit-form probe + submit endpoint (§5); owner answers Q1–Q8 |
| Renewals in Dev | Production tenants do not exist in Dev | Q9: test fixture strategy |
| Routing | Real renewal COs use `Renewal of Existing Product` far more than the Case 4/5 types; CE-only and Surface-only renewals are not among the six cases | Q10 |
| Legacy → new model | Renewal COs migrate from Core Software / CE Module / Surface Software Professional/Enterprise to Core Plus / Go / Prime; old domain add-ons ("bulks of 10 Domains") | Q11 |
| Customer admin user | Add customer email domain + create admin user (S10, R8) | Q12 (in scope?) |
| Salesforce updates | Guru requires UID/UUID + stages; owner decision 10/01: dashboard only (Dev IDs) | Q13 (production target) |
| Operator Account | TA/CSM → Operator Account mapping; "TA changed" detection | Q4 |
| Scan quality | "Valid results" check after a scan (subdomains, vulnerabilities, data) | Q14 |
| Slack | Status message to the channel (diagram); Slack connector not authorized in this session | Q15 |
| VM / multi-user | Web identity, TLS, proxy, Salesforce identity, runner host approvals (plan §0, §12) | Security/Identity |
| Production | Hard-blocked until a separate approval | Owner |

---

## 4. Questions for the owner (unclear or conflicting rules)

1. **Renewal expiration (R4, Cases 4–6):** "edit the expiration based on the new licence". For a multi-year DealHub term (e.g. 2026-10-27 → 2029-10-26), is the new expiration the **annual** cap (start + 1 year − 1 day, our CE rule) or the **term end**?
2. **"Never change the start date" (R4):** confirm the tenant's existing start date stays even when the old licence has already expired (e.g. CO-0770, old term expired 2026-09-18, new term Active from 2026-09-30).
3. **Additional domains on renewal (R2):** we cannot verify "evaluation was running on them". Proposal: if the renewal adds any domain not already on the tenant → manual review. OK?
4. **Operator Account (S4, R7):** which Salesforce field holds the TA/CSM, and how do names map to Leonardo Operator Accounts? For renewals, "update if the TA changed" — compare against what?
5. **Scan now (Cases 1 and 5):** the guide says enable Scan now, but the live form removes Scan now when a schedule is set. Keep "schedule only" for Case 5 too?
6. **Static outbound IP (S6):** which Salesforce field or contract signal means "requested"? Today it is always OFF.
7. **CE scanned domains (C2, R6):** DealHub row "Credential Exposure - Additional 100 Email Domain" exists (CO-0770). Is the allowed count 1 + add-on quantity, and which domains go in the LC field?
8. **"Leaked Credentials remain" for Case 1:** we read it as "stay OFF" (owner decision 09-29). Confirm the meaning.
9. **Testing renewals in Dev:** proposal — create a Dev "existing tenant" with our Case 1/2 automation for a synthetic or chosen CO, then run the renewal edit against that Dev tenant. Acceptable?
10. **Routing table for renewals:** `Surface & Credential Exposure` + `Renewal of Existing Product` = Case 6? `Credential Exposure` + renewal and `Surface` + renewal = CE-only / Surface-only renewal (not in the six cases) — add them as Case 6 variants?
11. **Legacy products:** for migrations (Professional/Enterprise/Essentials, Core Software, CE Module), confirm the new tenant values come only from the **new-model** rows, and old domain add-ons are ignored.
12. **Customer admin user (S10, R8):** in scope for automation, or stays manual?
13. **Salesforce updates long-term:** dashboard-only now (Dev IDs). In production, who writes UID/UUID and stages — this tool (guarded, read-after-write) or the CSM?
14. **"Valid results" after a scan (S9):** which signals count (subdomains found > 0, vulnerabilities, collected data)? Are they in the tenant search response?
15. **Slack status message:** wanted in the pilot? It needs the Slack connector authorized and a channel decision.
16. **"Ping Ohad/Chen — UUID linked in Castro" (G7):** what is Castro, and is it a required step for existing tenants?

---

## 5. Playwright learnings for an Edit mode

From the working Add Account runner (`tools/attended_ce_only_playwright.py`):

- The licence date controls carry `data-am="AddEditTenantModal-date-startDate"` / `...-expirationDate`: **Add and Edit use the same `AddEditTenantModal` component.** The Renew card's "EDIT ACCOUNT" screenshot shows the same Account Details fields. Expect the label-based text controls, `SELECT_NAMES` (`accountType`, `accountCountry`, `scanningInterval`, `leakedCredentialsScanningInterval`, `licenseType`), toggle keys, and the Material-UI date picker helpers to be reusable.
- The tenant table rows (MUIDataTable, server search `getAllDetailedAccounts`) end with a per-row **⋮ menu** — the expected entry point for Edit.
- Differences to probe (no submit): the menu item name; the modal's selector (Add uses `.tenants-add-account`); prefilled values (must be **read first**); whether Scan now appears; whether the start date is editable; the Confirm button label; the submit endpoint (Add posts `/backoffice/account/add`; Edit is unknown — likely an update endpoint, to be captured from one operator-run manual edit HAR on a Dev tenant we own).
- Existing helpers that carry over unchanged: `_open_search`, `_search_tenants` (session-expiry detection), `_set_checkbox`, `_fill_select`, `_fill_text_control`, `_fill_license_date`, `_expand_advanced_options`, `_ensure_confirm_enabled`, the redacted `RunLog`.

### Proposed edit-mode contract (mock-up)

```text
RenewalContract(engine, load_source, target_tenant, plan_changes, verify_unchanged)
  load_source      -> fresh CO + DealHub read (new-term rows only), route gate, term rule (1a)
  target_tenant    -> exactly one tenant: by Salesforce ID if set, else exact name + primary domain
  read_current     -> open ⋮ > Edit, read every control into a value map (no typing)
  plan_changes     -> {field: (current, target)} for allowed fields only
  verify_unchanged -> start date, company name, primary domain, user email domains (except R8 add)
  apply            -> set only changed fields; re-read all; Confirm once; never re-click
  readback         -> search + details: licence/expiration/toggles equal the plan
```

Field map for the Edit form (mock-up; ✱ = needs an owner answer above):

| Edit field | Case 4 renew Surface + new CE | Case 5 renew CE + new Surface | Case 6 renew both | Source / rule |
| --- | --- | --- | --- | --- |
| Company name, primary domain, country | keep (verify unchanged) | keep | keep | tenant |
| Alternate domains / SubDomains | add only if licensed ✱Q3 | add Surface domains ✱Q3 | update ✱Q3 | Salesforce |
| User email domains | keep `pentera.io` (+ customer domain only with the user step ✱Q12) | same | same | R8 |
| Scanning interval | per tier | per tier (Scan now ✱Q5) | per tier | new Surface baseline |
| Advanced options (90 h + profile) | revalidate = Surface profile | set Surface profile | revalidate | `SURFACE_ADVANCED_TOGGLES` |
| Notifications / Multiple users / API | ON | ON | ON | Surface card |
| Leaked Credentials + interval + domains | **ON**, Weekly, CE email domain | keep per entitlement, Weekly | keep, Weekly | CE overlay ✱Q7 |
| Phishing | OFF | OFF | OFF | cards |
| Operator Account | if TA changed ✱Q4 | ✱Q4 | if TA changed ✱Q4 | Salesforce TA |
| Licence type | Prepaid annual | Prepaid annual | Salesforce | R4 |
| Assets / domains / subdomains | Surface rule (10,000 / licensed / licensed) | Surface rule | Salesforce | 9/30 rule |
| Start date | **never change** | **never change** | **never change** | R4 |
| Expiration date | new term ✱Q1 | new term ✱Q1 | new term ✱Q1 | DealHub new rows |

### Dashboard mock-up: "Renewal plan" card (read-only, first deliverable)

```text
┌ Renewal plan · Case 6 (renew Surface + CE) ─────────────── Manual in production ┐
│ Existing tenant   Salesforce Surface Account ID: (empty) → find by name + domain │
│ New term          Pentera Surface Prime - 1000 Subdomains   Pending 2026-10-27   │
│                   Pentera Core Plus Commercial - 500        Pending 2026-10-27   │
│ Term check        ✓ Surface and Core Plus end on the same day                    │
│ Apply from        2026-10-13 (14 days before the new term starts)                │
│ Change            current → target                                               │
│   Expiration      (read in Leonardo) → 2027-10-26 ✱Q1                            │
│   Start date      unchanged (never edit)                                         │
│   Tier/interval   → Prime · Weekly                                               │
│   Subdomains      → 1000 · Domains → 1000 · Assets → 10000                       │
│   Leaked Creds    → ON · Weekly · 1 CE email domain                              │
│ Checklist  ☐ open tenant > Edit  ☐ apply changes  ☐ save  ☐ user email domain   │
│            ☐ operator (TA) check  ☐ Salesforce: stays manual                     │
└──────────────────────────────────────────────────────────────────────────────────┘
```

Real open renewal COs seen read-only on 2026-10-01 (products/status/dates only): CO-0758 (Case 4 type, done), CO-0767 (renew both, new term Pending 2026-10-27), CO-0770 (renew both, new term Active 2026-09-30, Go 500 + Go Bulk 500), CO-0771 (only legacy rows, no renewal term yet), CO-0769 (CE renewal Pending 2026-10-20), CO-0761 (CE renewal Active 2026-10-01).

## 6. Improvement review

See the "Improvement review" subsection of the 2026-10-01 handoff in `integration/IMPLEMENTATION_PLAN.md`.
