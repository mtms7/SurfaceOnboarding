# Case 3 (Surface + Credential Exposure, New) - what Leonardo Development taught us, for the production mapping of CO-0800 (2026-10-20)

**Status:** read-only analysis. Nothing external was touched and no code was changed. No password, token, cookie, account id, UUID or customer value is recorded; tenants appear only as CO references.

**Purpose:** map CO-0800 (Onboarding_Product__c `Surface & Credential Exposure`, Onboarding_Type__c `New Product Onboarding`, engine `case_3_combined_baseline`) to production BackOffice (`app.pentera.io`). Production CE-only New is already live (CO-0798, 2026-10-19). Case 3 has been created live ONLY in Leonardo Development, once (CO-0757), and has never been run, dry-run or created in production.

**Labels.** VERIFIED = observed live (the evidence is cited). ASSUMED = inferred from code, a design document or a different route and not observed for this route in this environment. "Plan" = `integration/IMPLEMENTATION_PLAN.md`. "Runner" = `tools/attended_ce_only_playwright.py`. Runner line numbers are for the tree at HEAD `88b8cb9`.

**Conflict to read first.** The brief for this analysis says "SpyCloud must be OFF". The repo says the opposite: that rule (2026-10-05) was superseded by the owner on 2026-10-08, "SpyCloud stays enabled (Leonardo's Add Account default)" (Plan section 18, "Handoff 2026-10-08 - SpyCloud is ON by default"). The production CE-only create also sends `spyCloudSettings.enabled = true` (docs/49 s.1). This document follows the repo (ON) and asks the owner to confirm in Question 4.

---

## (a) The Case 3 Dev fill contract, exactly as the runner builds it

Sources: `case3_fill_source` (Runner:1163-1202), `build_case3_fill` (Runner:1223-1232), which starts from `build_surface_only_fill` (Runner:1418-1486), plus the route object `CASE3_ROUTE` (Runner:1578-1590). Owner decisions: 2026-09-12 (docs/33), 2026-09-29/30 (docs/37), 2026-10-01 (decisions 1a, 2a, 3a, 4a; Plan "Progress - 2026-10-01"), 2026-10-04 (approval and timing), 2026-10-08 (SpyCloud).

### a1. Route gate and source reads (fixed SOQL, values stay in memory)
- Route: the CO must be exactly `Surface & Credential Exposure` / `New Product Onboarding`, else `case3_route_mismatch` (Runner:1182-1184). VERIFIED by tests (`Case3RouteTests`, `integration/tests/test_attended_ce_only_runner.py:5046-5129`) and the CO-0757 run.
- CE email domain: `Email_Domains__c` must contain exactly one valid domain (one leading "@" is stripped, nothing else is normalised), else `case3_ce_email_domain_invalid` (`one_email_domain`, Runner:326). A Core Plus row must exist (`case3_core_plus_missing`) and `select_ce_subscription` must find one unambiguous Core Plus term (Runner:436-483; any "Pentera Core Plus ..." baseline row, "Bulk"/"Additional" excluded; Expired/Cancelled/Inactive rows ignored since 2026-10-06).
- Surface entitlement: `select_surface_entitlement` (Runner:788-861). A Surface row counts when Active, or Pending and either starting within 14 days of the run day or the CO is `Onboarding_Approval_Status__c = Approved` (owner 2026-10-04). Exactly one counted baseline is required. A Core Plus row only sets `core_plus_present`.
- Term rule 1a: the Surface and Core Plus expirations (both computed by the one-year rule) must be equal, else `case3_term_mismatch` and the run stops before any browser work (Runner:1205-1220).
- Pre-create gate: a CO whose `Surface_Account_ID__c` or `Account_UUID__c` is already set is refused `salesforce_id_already_present` (Runner:1197, `_run`). VERIFIED (Plan 2026-10-01).
- Timing (Dev: always allowed; production: from start minus 2 days unless a CSM immediate request is recorded): `onboarding_allowed_now` (Runner:710-716), owner 2026-10-04.
- Scope review: the Surface CO needs a revision-bound manual scope review (counts only: `case3_scope_summary`, Runner:1235-1241) before Start (docs/37 "Owner decisions").

### a2. Name, domains, identity
| Item | Rule (source of truth) | Status |
|---|---|---|
| Tenant name | Salesforce `Account_Name__c`, whitespace collapsed, trailing periods and spaces removed, **no suffix** (`surface_names`, Runner:319; owner 2026-09-29). CE-only adds " - CE Only"; Case 3 does not. | VERIFIED live (CO-0757, Dev) |
| Primary domain | `Main_Domain__c` as one valid registrable root (not a subdomain, not a public suffix, not a network), via the shared normalizer (`classify_surface_domains`, Runner:864). Fails `surface_main_domain_invalid`. | VERIFIED live (CO-0757) |
| Alternate Domains field | Roots from `Alternate_Domains__c` (comma, semicolon or whitespace separated; duplicate of main dropped), joined with ", ". Empty = the control is verified blank. | VERIFIED live for the Surface route (CO-0649); ASSUMED identical for CO-0757 |
| SubDomains field | Subdomain entries from `Alternate_Domains__c`, joined with ", ". Empty = verified blank. | as above |
| Networks | Never filled; a CO listing networks fails closed (`surface_networks_not_supported`). Verified blank. | VERIFIED live (Surface route) |
| User email domains | `pentera.io` (internal), not the CE domain and not the customer domain (Runner:1445; Guru S2). Customer domain is added later with the customer user (manual). | VERIFIED live |
| Leaked Credentials scanned domains | Exactly the one `Email_Domains__c` value (decision 2a; Runner:1227). | VERIFIED live (CO-0757 create passed the pre-Confirm re-read) |
| Country | `Account_Country__c` text; option chosen exactly or by one alias (`COUNTRY_ALIASES`, Runner:4708; "United States" = "United States of America", added 2026-10-08). Unknown alias = fail closed. | VERIFIED live |
| Operator account | Left empty (react-select `input[placeholder="Select Operator Accounts"]`, verified empty). Assigned by hand only after the first scan finishes (owner 2026-09-30). | VERIFIED live |
| Primary user | A fixed attended user, never the customer: first name `Milton`, last name `Stevenson`, organization email `<local part>+<alias>@pentera.io`. Alias = account name without spaces and symbols, lower case, when the compact name is 15 characters or fewer; otherwise the lower-case word initials (`_account_display_name_and_alias`, Runner:300-316). Phone number and job title verified blank. The customer user is added later by hand as an extra user. | VERIFIED live |
| MFA | `mfaRequired` ON, never off. | VERIFIED live |
| Account Type | `Customer`. | VERIFIED live |

### a3. Licence
| Item | Rule | Status |
|---|---|---|
| Licence type | `Prepaid annual subscription` (select name `licenseType`). | VERIFIED live |
| Start date | The run day (Dev refuses a start after today; owner 2026-09-29). | VERIFIED live (CO-0757: 2026-10-04) |
| Start-date fallback | If Confirm stays disabled with no field error, the runner re-picks the start ONE day earlier, once (`LICENSE_START_FALLBACK_DAYS = 1`, Runner:4078; `_ensure_confirm_enabled`, Runner:4089), re-reads both dates, and records the date actually entered. Seen live on the Surface route CO-0649 (2026-09-30, run day refused, day before accepted). Not needed for CO-0757. | VERIFIED live (Case 1); not triggered in Case 3 |
| Expiration date | Surface subscription start + 1 year - 1 day, capped at the subscription end (`annual_license_dates`, Runner:496; Case 3 uses the Surface term and requires the Core Plus term to agree). CO-0757: DealHub start 2026-11-03 gave 2027-11-02. | VERIFIED live (CO-0757). **Open for Case 3 in production**: since 2026-10-19 CE-only production uses the Core Plus subscription END instead (`ce_license_dates`, Runner:486; Plan `da6746b`), while "Surface-only and Case 3 keep the one-year rule pending an owner decision". |
| Number of assets | `10000` (fixed; `SURFACE_LICENSE_ASSETS`, Runner:716; owner 2026-09-12/29/10-07, not derived from endpoint quantity). | VERIFIED live |
| Number of domains | = licensed subdomains (owner 2026-09-30). **docs/33's "3" is superseded.** | VERIFIED live |
| Number of subdomains | = licensed subdomains = the number in the Surface baseline product name (Go 500, Prime 1000, a number-less Prime = 1000) plus subdomain add-on rows. Form default is 50000 and is overwritten. A CO whose main + alternate roots exceed it fails `surface_domains_exceed_license`. | VERIFIED live |

### a4. Every select, toggle, switch and checkbox
Selects are resolved by their `name` attribute (`SELECT_NAMES`, Runner:201), checkboxes by `input[type=checkbox][name=...]`, "Scan now" by its aria name "primary checkbox". Fill order (`_fill_add_account_form`, Runner:3888): early selects (Account Type, Type, Scanning interval), absent-control check, expand "Advanced options", every toggle in dict order, Advanced text (max scan duration), late selects (Country, LC interval), texts, blank-control checks, Operator check, the two dates, then a full re-read of everything before Confirm.

| Control | Value | Source of truth | Status |
|---|---|---|---|
| `accountType` | Customer | decision 2026-09-12 | VERIFIED live |
| `licenseType` | Prepaid annual subscription | docs/33 | VERIFIED live |
| `scanningInterval` | Weekly for Prime/Enterprise, Monthly for Go/Essentials/Professional (`SURFACE_TIER_SCANNING_INTERVALS`, `phase1_validator/surface_source_readiness.py:~32`) | Guru tier card, owner 2026-09-29 | VERIFIED live (CO-0757 is a Go or Prime tenant; the interval chosen is by tier) |
| Scan now (`primary checkbox`) | **Absent**: the control exists only while Scanning interval is None. With a schedule it is verified absent, not set. | live probe 2026-09-29; Runner:1457-1459 | VERIFIED live (Case 1, CO-0649). Case 3 inherits it. |
| `leakedCredentialsScanningInterval` | Weekly | CE card, decision 2026-09-12 | VERIFIED live |
| `leakedCredentialsAllowed` | ON | decision 2026-09-12 | VERIFIED live |
| `mfaRequired` | ON | non-negotiable | VERIFIED live |
| `automatedDiscoveryEnabled` | OFF (form default ON) | Surface card | VERIFIED live |
| `subDomainsReconEnabled` | ON | owner Case 3 decision | VERIFIED live |
| `webDictionaryBruteForceEnabled` | ON | owner Case 3 decision | VERIFIED live |
| `webDorkingEnabled` | OFF | owner Case 3 decision | VERIFIED live |
| `fullNucleiScanEnabled` | ON (form default OFF) | Surface card | VERIFIED live |
| `authenticatedTestingEnabled` | OFF | owner Case 3 decision | VERIFIED live |
| `staticOutboundIpEnabled` | OFF always (no Salesforce signal is mapped) | Surface card S6 | VERIFIED live |
| `aiEnabled` | OFF | owner Case 3 decision | VERIFIED live |
| `multipleAttackStacksEnabled` | OFF | docs/33 | VERIFIED live |
| MAS for subdomains, Web Agent | Disabled by the form; left untouched | owner 2026-09-12 | VERIFIED (never set) |
| `notificationsAllowed` | ON | owner Case 3 decision | VERIFIED live |
| `multipleUsersAllowed` | ON | owner Case 3 decision | VERIFIED live |
| `apiAccessAllowed` | ON (Case 3 forces ON; Core Plus is required on this route; Runner:1230) | owner Case 3 decision | VERIFIED live |
| `phishingEnabled` | OFF | owner Case 3 decision | VERIFIED live |
| `provisioningEnabled` | ON | both component guides | VERIFIED live |
| `subDomainsNumberAllowed` ("Include subdomains") | ON | both component guides | VERIFIED live |
| Maximum scan Duration (hours) | `90` (a number input under "Advanced options", no name/id/data-am; label is the enclosing `.MuiFormControl-root` label; form default 24) | Surface card | VERIFIED live (Dev). The search row does not carry it (`campaignsTimeoutInHours` null), so it is reported as info, not judged (`validate_row`, Runner:~6040). |
| Operator Account | empty | owner 2026-09-30 | VERIFIED live |
| SpyCloud | **No control exists in Add Account**: Leonardo's front end hard-codes `leakedCredentialsSettings.spyCloudSettings.enabled = true`. Policy since 2026-10-08: stays ON. | Plan 2026-10-04 probe; 2026-10-08 decision | VERIFIED live (CO-0757 row read ON; CO-0757 was turned OFF on 2026-10-05, back ON 2026-10-08) |

Dev form defaults relevant to the production comparison (no-submit probe, docs/37): Automated discovery ON, Recon Subdomains ON, Web dictionary brute force ON, Nuclei OFF, Authenticated Testing OFF, Static outbound IP OFF, AI OFF, Scan now ON while the interval is None, Leaked Credentials OFF with its interval and domains fields disabled until it is switched ON (so the toggle is set before those fields).

### a5. SpyCloud handling, in order of the owner's decisions
1. 2026-09-12 (docs/33): leave untouched.
2. 2026-10-04/05: Add Account hard-codes ON; owner: must be OFF. Edit form checkbox `input[name="spyCloudEnabled"]` after expanding both "Advanced options" sections; Confirm posts `/api/v1/backoffice/account/{id}/edit`; the re-read must show `spyCloudSettings.enabled == false`. VERIFIED live (CO-0679 `spycloud_off_verified`; CO-0757 OFF confirmed 2026-10-05).
3. 2026-10-08: SpyCloud ON by default for every case. `SPYCLOUD_AFTER_CREATE_ENABLED = False` (Runner:6204); the hook `_spycloud_after_create` (Runner:6462) is a no-op. The manual tools `--spycloud-off` and `--spycloud-on` remain, Dev only, dry run by default. All five Dev CE/Case 3 tenants were put back ON (VERIFIED, Plan "Live 2026-10-08").
4. Validation treats ON as ok and OFF as an informational warn (`validate_row`, Runner:5991-6001).

### a6. Scan settings and what is verified blank
- Interval by tier; no Scan now; max scan duration 90 h; Phishing OFF; LC Weekly on one domain.
- Verified blank (each is located and its value read empty before Confirm): Networks, Phone number, Job title, Operator Account; Alternate Domains and SubDomains when the CO has none.
- Scans: the Surface schedule starts the scan after create (CO-0649 read "No scan started" on create day and "Account Scanning" the next day; first scan 3.1 h, `COMPLETED`). The LC scan is a `LEAKED_CREDENTIALS_DISCOVERY` execution under Details, "Duration Per Scan" (CE-only CO-0798: `DONE`, about 10 minutes).

---

## (b) Playwright, selector, timing, readback and duplicate-check lessons (dated)

### b1. Form and selector gotchas
- **Selects have no `<label>`**; use the `name` attributes (diagnostics 2026-09-29). Native select, set by option label text.
- **Date inputs** carry no label, name, id or placeholder; they are read-only text inputs showing "Sep 29, 2026" and are set through a Material-UI picker dialog (`.MuiPickersModal-dialogRoot`), located only by `data-am="AddEditTenantModal-date-startDate"` and `...-expirationDate` (Runner:56-90, 1693).
- **Advanced options** is collapsed; click the exact text inside `.tenants-add-account`, then wait for the toggles (Runner:3990-4030). Idempotent: never collapse an already open section.
- **Toggle clicks**: the switch track can intercept the pointer; Dev retries with `force=True`, then re-reads the state (`_set_checkbox`, Runner:1640). Click timeout raised 2 s to 6 s on 2026-10-08 (CO-0765, slow Leonardo). Production code uses NO force: scroll into view, hit-test, plain click, verify (dry runs #1-#3, 2026-10-18).
- **Number inputs** such as Max scan Duration have no stable attribute; located by the enclosing form-control label (live probe 2026-09-29). "Number of subdomains" defaults to 50000 and must be overwritten.
- **Label text** `User email domains  (Comma Separated Values)` has two spaces in Dev; the 2026-10-16 production probe saw one (Plan "D4 production probe": "detection artifacts"). The production CE-only dry run passed 32/32 with the Dev label constant (VERIFIED), so the locator tolerates it.
- **Confirm disabled with no field error** when the start date equals the run day (Case 1 CO-0649, 2026-09-30): automatic one-day-earlier fallback (see a3).
- **Hand-built in-page API calls get 401** (the app adds its own authorization header). Redirecting the app's request to another endpoint was blocked by the safety classifier on 2026-10-04. Drive the UI and read only the app's own replies. The same holds in production (cookie-only fetch got 401, 2026-10-18).
- **Row actions**: select the row, open `.rowMenuActionsContainer .list-trigger`, use only `[data-am="Button-Grid_Details"]` and `[data-am="Button-Grid_Edit"]`. Never `_Access`, `_Scan_Now`, `_Stop_Scan`, `_Delete` (`ROW_ACTIONS_ALLOWED`, Runner:~6190).
- **Details tabs** are Details, Pending Requests, Configuration, Duration Per Scan (2026-10-06); the tab name is not exactly "Duration Per Scan", so match any tab containing "duration" (fixed 2026-10-07). There is no tenant user list in BackOffice Details.

### b2. Invisible search, timing and retry
- The search is a reload plus a rewrite of ONLY the body of the app's own `POST /api/v1/backoffice/getAllDetailedAccounts` (`filters {"and":[{"method":"search","value":...}]}`, offset 0, 100 rows); headers are untouched and never read (`_search_tenants`, Runner:4816). Only the reply to the request that was rewritten counts. Fix 2026-10-05: an app refresh after an Edit save had been taken as the reply (SpyCloud save returned 200, the re-read failed; now `spycloud_saved_unverified` after one retry).
- Name search is a substring search: a short name returned 4 rows (2026-10-06), so `_search_one_row` narrows by the expected row's own primary domain and requires exactly one row with the expected id (Runner:6241). Relevant to Case 3 because the tenant name is the bare account name.
- 2026-10-06 Leonardo Dev instability: `Page.reload` hung 30 s with zero events, `ERR_CONNECTION_RESET` on the 5.2 MB `assets/index-*.js`; request interception disables the Chrome HTTP cache so every search reload re-downloads the bundle. Fixes: one retry by navigation after retiring the hung document (`about:blank`), reply wait 45 s (`SEARCH_RESPONSE_TIMEOUT_MS`, Runner:220), wait up to 45 s for the Search toolbar. A frozen tab after SSO: close the automation browser, then Prepare sessions.
- The in-app trigger (header sort click `thead th button`) was never found live (33 `in_app_unavailable`, 0 in-app triggers; Plan 2026-10-18); every search falls back to reload or navigate. In production a read while the Add Account form is open is therefore impossible; production reads run with the form closed (reads-before-form, commit `c59b641`).
- 409 on `POST /api/v1/backoffice/account/add` (CO-0765, 2026-10-08): the form stayed open, nothing created; cause not captured, likely a unique value still held by a previous tenant (such as the primary-user email alias). ASSUMED relevance to Case 3: the alias rule is shared by every route, so a second tenant for the same account name reuses the same alias.
- Click on Confirm is made once; an uncertain submit is never re-clicked; reconciliation is read-only (Runner:5185-5230).

### b3. Duplicate-check behaviour
- Order at every Start: (1) production gate on the Redash clone, query 251/253, usable only when young enough (6 h query age; 36 h data age since 2026-10-19); (2) DEV inventory pre-check; (3) live Leonardo lookups (`_run`, Runner:5098-5130).
- Case 3 runs THREE lookups: tenant name, primary domain, and the CE email domain (decision 4a, `CASE3_ROUTE.extra_lookups`, Runner:1589). Each is classified from the visible table (`_settled_tenant_rows`) and from the server's own rows (`_api_duplicate`, Runner:4944): an exact normalised name match OR an `accountDomain` equal to the lookup's domain = found; `total_count` greater than the rows returned = ambiguous; both stop the run. A search that never settles fails closed.
- The 4a lookup compares `accountDomain` only; it does not look at another tenant's `userEmailDomains` or `leakedCredentialsScannedDomains`. ASSUMED sufficient because a CE-only tenant's primary domain is its email domain (Dev rule 2026-09-21).
- Pre-create gate: a mapped Salesforce ID already present stops the run (decision 2026-10-01).

### b4. Readback quirks
- `_api_readback` (Runner:4964) needs exactly one row whose normalised `accountName` equals the tenant name and whose `accountDomain` equals the primary domain, with a 16-64 character `id` and a 32-hex `accountUuid`. `lastReconScan` set means the tenant has already started a scan: accepted as "Account Scanning" only on routes with `allow_scan_started` (Surface and Case 3 are True; CE-only is False and falls back to the details view).
- After a Case 3 create the runner checks ids and scan state only (`readback_verified`); the field-by-field comparison against the plan is the read-only `--validate` path (`validate_row`, Runner:5891; expiry date, counts, toggles, LC interval, domain lists, SpyCloud, max scan duration reported as info).
- CO-0757 evidence: pre-check `inventory_no_match` (593 tenants), live check clear, `account/add` 200, `readback_verified` in 36 s (VERIFIED, Plan 2026-10-04).
- Production readback: an explicit `projection` made the server drop most license and flag paths (13 false mismatches on 2026-10-18); strict reads now send only the `unique_fields` override and no projection.

### b5. Live failures and fixes in Dev (dated)
| Date | Failure | Fix |
|---|---|---|
| 2026-09-30 | Confirm disabled, no error (CO-0649) | one-day-earlier start fallback |
| 2026-10-01 | CO-0649 duplicated a production tenant | `salesforce_id_already_present` gate |
| 2026-10-05 | SpyCloud save 200 but re-read took an app refresh as its reply | reply bound to the rewritten request; one retry |
| 2026-10-06 | reload hangs, `ERR_CONNECTION_RESET` | retry by navigation, 45 s waits |
| 2026-10-06 | short name matched 4 rows | `_search_one_row` narrowing by domain |
| 2026-10-07 | Duration Per Scan tab not found by exact name | substring "duration" |
| 2026-10-08 | CE-only CO-0765: "@domain" email domain refused; Core Plus Enterprise row not recognised; toggle click too short; country alias; 409 on create | `@` strip; `is_core_plus_baseline_row`; 6 s click; `country_option_label`; local records reset |

### b6. Production failures and fixes already learned (CE-only, relevant to the same code path)
Dry run #1 `toggle_click_failed` (control off-screen, hit test before scroll), #2 `advanced_mismatch` (production defaults automatedDiscovery, subDomainsRecon, webDictionaryBruteForce ON; fill now sets them), #3 and #4 `production_session_not_ready` (no sort header outside a dialog), #5 and #6 `write_request_blocked` (the page's own table refresh counted as a write; aborted but no longer counted), #7 `dry_run_passed` (32/32 fields). First live attempt 2026-10-19T00:11Z refused `production_session_not_ready`; fixed by routing the page-level live guard (`2c724f1`); create `readback_verified` 2026-10-19T18:34:37Z (Plan section 18, handoffs 2026-10-18 and 2026-10-19).

---

## (c) Dev form versus production BackOffice form: observed differences only

| # | Item | Dev (Leonardo Development) | Production (`app.pentera.io`) | Status and citation |
|---|---|---|---|---|
| 1 | Add Account form | One modal `AddEditTenantModal` with named selects, toggles, two data-am date inputs | The same fields exist: 40 controls read by the 2026-10-16 D4 probe; the CE-only dry run #7 passed 32/32 form fields, including Account Type, country, intervals, the licence select, three number fields, both dates, nine toggles and Scan now | VERIFIED (Plan "D4 production probe"; "Step 4 dry run #7") |
| 2 | Surface-only controls | Present: Advanced options with nine switches, Max scan Duration (default 24) | The nine Advanced switches exist and were read and set OFF in the dry run. Production defaults: automatedDiscovery, subDomainsRecon, webDictionaryBruteForce ON; the other six OFF. Identical to the Dev defaults. **Maximum scan Duration (hours) was never read or set in production** (CE-only plans do not carry it) | Switches VERIFIED (dry run #2/#3). Max scan Duration ASSUMED present |
| 3 | Scanning interval options | None/Daily/Weekly/Monthly | `scanningInterval` has 4 options (D4 probe); only None was selected in production dry runs; whether Weekly/Monthly makes Scan now disappear in production is not observed | options VERIFIED; behaviour ASSUMED (plan 2026-10-16, 2026-10-18) |
| 4 | Scan now | Exists only while interval is None | Exists with interval None (dry run set it OFF; a below-the-fold hit-test failure was fixed) | VERIFIED for None; ASSUMED vanishes with a schedule |
| 5 | Licence type options | 5 options, `Prepaid annual subscription` | `licenseType` has 5 options; payload value `prepaid annual subscription` (create) | VERIFIED (docs/49 s.1) |
| 6 | SpyCloud | No control in Add Account; front end sends enabled = true | `spyCloudEnabled` control absent from the production form (dry run #7 "SpyCloud absent"); the manual create payload carried `spyCloudSettings.enabled: true`; readback requires enabled true. The Edit-form path (checkbox `spyCloudEnabled`) was never probed in production, and `set_spycloud_off` refuses a non-Dev environment | VERIFIED absent/true; production Edit UNVERIFIED (Plan "Live 2026-10-18"; docs/49; docs/48 s.4) |
| 7 | Tenant naming | Case 3: bare account name | Production CE-only: account name + " - CE Only" (code enforces the suffix in `production_contract._checked`: `tenant_name_not_ce_only`). No production Surface or Case 3 name has been created or specified | CE-only VERIFIED (CO-0798); Case 3 ASSUMED = Dev rule |
| 8 | Expiration | Case 3: start + 1 y - 1 d capped | CE-only production: Core Plus subscription END since 2026-10-19 (CO-0798 had been entered with the one-year date and was corrected by hand 2027-06-21 to 2029-06-21) | VERIFIED for CE-only (Plan `da6746b`, handoff 2026-10-19 night); Case 3 OPEN |
| 9 | Start date | Run day, one-day fallback | The CE-only create used the run day through the same picker and reached `readback_verified`; the exact start entered and whether the fallback was used are not recorded in the repo | create VERIFIED; date detail not recorded |
| 10 | Create payload | `/backoffice/account/add` 200 (CO-0757) | CE-only body captured from a manual production create: `accountType` lower case `customer`, `accountCountryCode` ISO-2, license `assetsNumber` and `domainsNumber` as STRINGS, `subDomainsNumber` as a number, `reconLevel` null, `scanningInterval` `NO_SCHEDULE`, LC interval `WEEKLY`. No Surface or Case 3 body has been captured | CE-only VERIFIED (docs/49 s.1); Surface/Case 3 body ASSUMED to follow the same types |
| 11 | Duplicate and readback transport | Visible Search box and invisible reload+rewrite | Page's own table request only, with the Add Account form CLOSED; strict reads with no projection; control tenant and demo-domain list checked; reads up to about 180 s old at the last gate | VERIFIED for CE-only (probes A/B 2026-10-18; dry run #7; create 2026-10-19) |
| 12 | Create lock | Always allowed in Dev | Production create is limited by construction to CE-only: `production_approval.ALLOWED_ROUTE = CASE_2` and `SCOPE = "ce_only_single_co"` (`production_approval.py:27-28`), gate step "route must be CE-only new" (`production_create.py:166-167`, refusal `route_not_ce_only_new`), CE-only expected payload and name suffix in `production_contract.py`, CE-only intended values in `ce_production_intended` (Runner:9888). `PRODUCTION_RUNNER_SUPPORTED` and `PRODUCTION_DRY_RUN_SUPPORTED` are False (relocked `d450984`, `session_readiness.py:64,70`) | VERIFIED (code and Plan) |
| 13 | Salesforce writeback | Dashboard only (Dev IDs) | Production CE-only writes `Account_UUID__c` after a verified create (guarded, one field) and moves Request Approved to Account Scanning; both writers refuse any route other than `case_2_new_ce_only` (`salesforce_uuid_writeback.py:19,54`, `salesforce_stage_writeback.py:52`). Case 3 decision 3a (both `Surface_Account_ID__c` and `Account_UUID__c`) exists only for the Dev dashboard panel | VERIFIED (CO-0798; code) |
| 14 | Scan check | Dev reads the executions list and the row | Production CE scan state is read from the Redash clone (query 253 CE columns, docs/56) or the BackOffice Duration Per Scan check; both are built for the CE scan (`LEAKED_CREDENTIALS_DISCOVERY`). Nothing reads the Surface recon scan result for production | VERIFIED CE-only; Surface part not built |
| 15 | Timing rule | Immediate | Production onboards from subscription start minus 2 days unless a CSM immediate request is recorded | VERIFIED rule (owner 2026-10-04); dates of CO-0800 unknown to this analysis |
| 16 | Operator account | Empty | Empty (decision D5); production CE-only readback expects it empty | VERIFIED (docs/48 s.2) |
| 17 | User email domain | `pentera.io` | `pentera.io` by design (Guru S2/R8; handoff 2026-10-19) | VERIFIED |
| 18 | Production readback comparison | `validate_row` (Case 3 plan) | `production_contract.compare_readback` compares the CE-only expected row (all flags false, 1/1/1, `NO_SCHEDULE`); no Surface or Case 3 expected row exists | VERIFIED (code) |

**What a Case 3 production run would need that does not exist yet (derived from the rows above, ASSUMED complete):** a Case 3 variant of the approval route and scope, the gate's route check, the expected create payload and readback row (Surface toggles ON, counts from the licence, tier interval, LC on one domain), the intended-values builder, the two Salesforce writers, and a Surface scan check; each would be a reviewed code change with its own owner go, and a production dry run on CO-0800 would be the first observation of the Surface-specific controls (rows 2-4).

---

## (d) Questions for the owner (ordered by how much each blocks a production dry run of CO-0800)

1. **Route extension or manual create?** Production create, approval, readback and both Salesforce writers are hard-wired to CE-only New (section c, rows 12-13). Do you want (a) a reviewed code change extending the production dry run and create to `case_3_combined_baseline` (new one-CO approval scope, Case 3 expected payload and readback, tests, Opus review, unlock commits), or (b) a manual BackOffice create from the CO page sheet for CO-0800, followed by the guarded writes? Nothing for CO-0800 can run in production before this is decided.
2. **Expiration rule for Case 3 in production.** Since 2026-10-19 CE-only expires at the Core Plus subscription end; Surface and Case 3 still use start + 1 year - 1 day capped (decision pending in Plan, 2026-10-19 night, open decision (b)). Which rule for CO-0800? If DealHub is multi-year the two differ (CO-0798 needed a manual fix). Also, if the rule becomes "subscription end", should the Surface and Core Plus term-agreement check (decision 1a) compare the full term ends?
3. **Production tenant name for Case 3.** Dev uses the bare account name. Production CE-only uses " - CE Only". Confirm the bare account name for the combined tenant, and confirm it should not collide with an existing production CE-only or Surface tenant for the same customer (the duplicate gate will stop on any name, primary-domain, alternate-domain or CE-domain match).
4. **SpyCloud on a combined tenant.** Confirm ON (2026-10-08 decision, docs/49 payload true, no production Add Account control). If OFF is required, there is no verified production path: the Edit-form checkbox exists only in Dev and the tool refuses non-Dev. Do you want that probed (read-only Edit open and cancel) before CO-0800?
5. **CO-0800 source facts to confirm read-only:** Approval status Approved, one valid `Email_Domains__c` value, a Core Plus row present, Surface and Core Plus terms agreeing, tier (Go or Prime), add-on subdomain rows, `Main_Domain__c` is a root, listed roots fit the licence, `Surface_Account_ID__c` and `Account_UUID__c` both empty. May the existing read-only checks (`--domains`, the production duplicate check, the scope summary) be run on CO-0800 now?
6. **Which production form values are unknown for Surface.** The production form has never been filled with a Weekly or Monthly interval or with Maximum scan Duration 90 (section c, rows 2-4). Do you authorise a production dry run on CO-0800 (Confirm never clicked) as the first observation, once Question 1 is resolved? It needs a fresh clone, a one-CO approval for the Case 3 route, an attended SSO + MFA session and a reviewed unlock of only the dry-run constant.
7. **Production Leaked Credentials domain.** Confirm the LC scanned-domain field takes the single `Email_Domains__c` value even when it differs from `Main_Domain__c`, and that `User email domains` stays `pentera.io`. In CE-only production the primary domain and LC domain were the same value; for Case 3 they can differ.
8. **Start date and timing for CO-0800.** What is the DealHub start? Production onboarding is allowed from start minus 2 days unless a CSM immediate request is recorded. Is a CSM request recorded? Also: what start date did the CO-0798 create actually enter (run day or the one-day fallback)?
9. **Salesforce writes after a Case 3 create.** Authorise writing both `Surface_Account_ID__c` and `Account_UUID__c` (decision 3a), empty-or-equal, one verified write each, and the stage move Request Approved to Account Scanning, as CE-only does for the UUID? Which Account ID field maps to the BackOffice row `id`?
10. **Scan completion for Case 3.** Onboarding Completed currently waits for the CE scan only (query 253 CE columns). For Case 3, should it wait for both the Surface recon scan and the LC scan? If so, which Surface scan field does the clone carry, or should the BackOffice Duration Per Scan check be extended to the recon campaign?
11. **Operator Account in production.** Left empty at create and assigned after the first scan finishes (Dev rule, decision D5 for CE-only). For a Surface tenant the Guru card assigns it from TA and CSM: who assigns it in production, and is the TA/CSM mapping still deferred?
12. **Approval and review process for the Case 3 unlock.** The sequence for CO-0798 was: owner approval issuance (7-day expiry, typed phrase), reviewed dry-run unlock, dry run (record valid 24 h), independent review, reviewed create unlock, attended create, guarded writes, relock. Confirm the same sequence and reviewers for CO-0800, and the relock rule (both constants False after the single attempt).

---

## Evidence index
- Runner (`tools/attended_ce_only_playwright.py`): Case 3 constants 1141-1143; `case3_fill_source` 1163; `build_case3_fill` 1223; `build_surface_only_fill` 1418; `RouteContract` 1521; `CASE3_ROUTE` 1578; `SELECT_NAMES` 201; `_fill_add_account_form` 3888; `_ensure_confirm_enabled` 4089; `_search_tenants` 4816; `_api_duplicate` 4944; `_api_readback` 4964; `_run` ~5098; `validate_row` 5891; `SPYCLOUD_*` 6176-6204; `_search_one_row` 6241; `_spycloud_after_create` 6462; `_production_gate` 7925; `_production_expectation` 8037; `_duplicate_check` 8199; `ce_production_intended` 9888; `readback_analyse` 9912.
- Tests: `integration/tests/test_attended_ce_only_runner.py` `Case3RouteTests` 5046-5129; `CE_GOLDEN_PLAN` and `CE_GOLDEN_EVENTS` 3527-3600.
- Docs: docs/33 (draft Case 3 matrix, partly superseded: domains = 3, SpyCloud), docs/37 (Surface Guru notes and Dev form defaults), docs/38 (rules and gaps), docs/48 and docs/49 (production HAR and CE-only create contract), docs/53 (create runbook).
- Plan section 18 entries: Progress 2026-10-01; Handoffs 2026-10-04, 2026-10-05, 2026-10-06, 2026-10-07, 2026-10-08; "Live 2026-10-08 (late morning)" and "(afternoon)" CO-0765; 2026-10-16 D4 probe and manual CO-0797 create; 2026-10-17 to 2026-10-19 production create sequence; "Consolidated status 2026-10-20".

## (e) Owner decisions 2026-10-20 for Case 3 in production (supersede the open items above)

1. **SpyCloud stays ON** for combined Surface + CE tenants (answers question 4; no Edit-to-OFF step after create).
2. **Licence dates = the DealHub Subscriptions term** for "Surface & Credential Exposure" + "New Product Onboarding": start = Subscription Start Date, expiration = Subscription End Date (full term; **not** the one-year cap used in Dev). CO-0800: 2026-10-01 → 2029-09-30 (36 months), which also matches its Onboarding Comments "2026-10-01 - 2029-09-30". Answers question 2. Open: production BackOffice may refuse a start date in the past (Dev refused a start after today; past-start acceptance in production is not observed) — the first dry run shows it.
3. **Source validation (Guru "Surface License Tiers Breakdown")**, already implemented in `phase1_validator/surface_source_readiness.py` (`SURFACE_TIER_SCANNING_INTERVALS`, `classify_surface_product`) and `select_ce_subscription` (owner 2026-10-08):
   - Surface requires a **Pentera Surface Go or Prime** baseline row. Legacy names map: Essentials → Go, Enterprise → Prime (Professional treated as Monthly). Go = **Monthly** scanning, 500 subdomains; Prime = **Weekly**, 1,000 subdomains; subdomain add-ons add to the count.
   - Credential Exposure requires a **Pentera Core Plus** baseline row (any tier); Core Plus includes **one** CE email domain, more only as an add-on.
   - CO-0800 (read-only Salesforce, 2026-10-20): Core Plus Enterprise 4000 EP + Core Plus Bulk 500 EP ×6 + **Surface Go 500 Subdomains**, all Active, 2026-10-01 → 2029-09-30 → tier Go, Monthly, 500 subdomains; CE email domain = the CO's Email Domains value (differs from the main domain; the submitter was asked in Chatter to confirm it). Approval Status still **Pending** → no dry run until Approved.
