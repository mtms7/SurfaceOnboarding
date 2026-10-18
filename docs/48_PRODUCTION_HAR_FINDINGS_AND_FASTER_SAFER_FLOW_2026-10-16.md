# Production HAR findings and a faster, safer flow (2026-10-16)

**Status: analysis and proposal, local document only.** Nothing here authorizes a production BackOffice (BO) read or write, changes a guard, or enables `PRODUCTION_RUNNER_SUPPORTED`. MFA stays with the human in the real login page; our code never handles a password, TOTP code, token or cookie (AGENTS.md).

**Evidence labels.** `HAR` = VERIFIED-in-HAR by the main session, which read the owner's manual production session (host app.pentera.io only, 14 requests) as names, types and status codes only, before the owner deleted the file. This document did not open the HAR and holds no values from it. `CODE` = VERIFIED-in-code (read this session: `integration/onboarding/production_probe.py`, `tools/attended_ce_only_playwright.py`, `integration/onboarding/leonardo_inventory.py`). `ASSUMPTION` = not verified.

**Update 2026-10-18 (live finding, supersedes the cookie-fetch design below).** The assumption flagged in section 1, that a `fetch` with `credentials: "include"` run from the page carries the session, was refuted live: after a human SSO+MFA sign-in both `getAllDetailedAccounts` search calls returned 401, so the page authenticates with an in-page session token header, not cookies. Everything failed closed and nothing was created. The runner no longer fetches anything in the page: it lets the PAGE send its own table request (its own headers and token, never read or logged), rewrites ONLY the `tableServerData` JSON body through a page route (offset 0, 100 rows, the verified `search` filter, `unique_fields` name + domain, an explicit projection) and reads the page's own reply in Python. The request guard sees the rewritten body (`route.fallback`) and passes a `getAllDetailedAccounts` POST only when that body validates as one bounded read-only search. Session state is now the STATUS of that own reply plus the statuses of the page's own start-up `getAuthorities` / `accountInfo` calls when it makes them (401/403 expired; no reply not ready); the authority-name list is no longer read. The demo-domain list is used only if the page fetched it itself (observed passively after a reload); otherwise the check fails closed (`duplicate_check_unavailable`). Not yet verified against production: the real Playwright route/response behaviour and whether the Accounts page issues the demo-domain GET on its own.

## 1. Login and session detection without secrets

| Question | Finding | Label |
|---|---|---|
| Login step | `POST /api/v1/auth/verify` (body key `totpCode`, status 200) is the MFA step. Our code must never call or replay it. | HAR |
| Session-proof calls the page already makes | `GET /api/v1/authenticated/getAuthorities` returns `{authorities:[strings]}`. `GET /api/v1/authenticated/accountInfo` returns account keys (`accountId`, `accountName`, `accountDomain`, `accountSubtypeEnum`, `accountDashboardEnum`, feature flags). Also `GET /api/v1/userProfile` and `GET /api/v1/license`. | HAR |
| Today's detection (production probe) | DOM markers: path under `/backoffice`, no "login" in the path, no "Log In" / "Sign in with SSO" / "Forgot your password?" control visible, and a table, nav or other control visible, on 3 consecutive 1 s polls. | CODE |
| Today's detection (Dev runner) | The table request is captured, and a 401 or 403 raises `LeonardoSessionExpired`. The tab can still look signed in while the API token is expired, which the code comments already note. | CODE |
| Weakness of DOM markers | They can pass on a cached shell while the token is dead, or fail after a UI redesign. They say nothing about permissions. | ASSUMPTION (reasoned from the Dev comment) |

**Proposed check (read-only, no secret handling).** After the human has signed in, the runner runs in the page, with the browser's own cookies and storage: `page.evaluate` of a `fetch` GET to `/api/v1/authenticated/getAuthorities` (same origin, exact path, GET only) and then `/api/v1/authenticated/accountInfo`. The evaluate callback returns only the HTTP status and, for authorities, the list of authority NAMES. It never reads, returns or logs any header, cookie, storage entry or body value other than those names. Whether the page authenticates with cookies or an in-page Authorization header is unknown, so a `fetch` with `credentials: "include"` might get 401 even when signed in. That is fail closed (the run stops) but would defeat the idea, so it must be tested first in the D4-style attended probe (ASSUMPTION). The fallback is the Dev technique: capture the page's own request and read only its status, never its headers.

Rules, all fail closed:
1. `signed_in` = status 200 on both calls, `authorities` is a non-empty list of strings, and the DOM markers agree. Any disagreement, redirect, non-JSON, unexpected key set or status other than 200 means `production_session_not_ready`.
2. 401 or 403 at any later moment means `production_session_expired`. Nothing is retried, and the run stops before any further step. An uncertain submit is still never re-clicked.
3. Required authorities: the exact backoffice authority names are NOT known (the HAR lists names, but the main session did not pass them on). The doc cannot state them. The probe should record the names only (names are not secrets) and the owner should pin the minimum set as a constant. Until then the rule is "the list must contain at least the backoffice-access authority the owner names; unknown means stop" (ASSUMPTION).
4. `accountInfo` identifies the signed-in tenant context. The runner should assert that its `accountName` and `accountDomain` match the expected production values, so a user in the wrong context is stopped. Only equality results are recorded, not the values.
5. The check repeats immediately before Confirm, so a session that expired during the dry run fails closed.

## 2. Faster create through the page's own authenticated fetch

**Request grammar for `POST /api/v1/backoffice/getAllDetailedAccounts` (HAR, 2 calls, both 200).** Body is `{tableServerData:{offset, items_per_page, sort:{direction,key}, filters, unique_fields:[...], projection:[...] (optional)}}`. `filters` is `{}` or `{and:[{or:[...]}]}`. The first call (table load) sorts by `lastReconScan` with `unique_fields` of account type, license type and first name. The second call (a name search) sorts by `accountName`, with `unique_fields:[accountName]`, `projection:[accountName]` and an `and`/`or` filter. Response: `{pagination_response:{unique_fields, total_count, table_data:[rows]}}`.

**Inner condition grammar: UNVERIFIED in the HAR.** It is verified in the Dev code (CODE): the Dev runner sends `filters = {"and":[{"method":"search","value":<text>}]}` with offset 0 and a page size of 100, and treats `total_count` greater than the rows returned as ambiguous. The production `{and:[{or:[...]}]}` form may use the same `method`/`value` leaf conditions, or a different one. How to learn it safely: run the owner's dry run on the real production form with the request guard recording method, path template and body KEY names only (never values), then type nothing but the search the owner already does by hand. Alternatively the owner repeats one manual name search while the guard records key names. Until then, reuse the Dev technique: rewrite only the body of the page's own table request (the page's headers go out unchanged and are never read). That is already production-compatible in shape because the endpoint, `tableServerData` and response envelope match the Dev contract (HAR vs CODE).

**What the runner can do instead of UI scraping (all through the page's own request, read-only):**
- Duplicate pre-check: search by exact account name and by domain; compare client-side on normalized `accountName` and `accountDomain` (the Dev `_api_duplicate` rule). A search that returns fewer rows than `total_count` is ambiguous and stops. Add a `projection` of `accountName`, `accountDomain` to shrink the response (projection key names for domain are ASSUMPTION; the HAR shows `projection` only with `accountName`).
- Post-create readback by name with a projection: require exactly one exact-name row, then compare fields as below. Dev readback also requires a 32-hex `accountUuid` and a well-formed `id`; keep both. Because a projection may omit fields, the readback should use no projection or an explicit list of every field compared.
- `GET /api/v1/backoffice/systemSettings/demoAccountDomains` returns `{demoDomains:[strings]}` (HAR). Use: refuse a create whose primary or email domain is in that list (domains reserved for demo accounts). Whether a demo domain is allowed for a real customer is the owner's rule (ASSUMPTION; fail closed means stop and ask). The list itself is data to compare against, not to persist.

**Production row field names for the readback comparison (HAR for names; mapping to Dev checks is CODE):**

| Intended value | Production row field | Notes |
|---|---|---|
| Tenant name | `accountName` | Compare normalized (case, whitespace). |
| Primary domain | `accountDomain` | Compare lowercased, trailing dot removed. |
| Alternate domains | `alternateDomains` (list) | Dev compares as a set. |
| Email domains | `userEmailDomains` (list) | Dev uses "includes the plan". |
| Country | `accountCountryCode` | Form select is `accountCountry`; code versus label mapping is ASSUMPTION. |
| License type | `accountLicense.licenseType` | Enum text, normalized (`_norm_enum`). |
| License dates | `accountLicense.startDate`, `accountLicense.expirationDate` | Epoch ms in Dev; compare as UTC and local ISO dates. |
| Recon level | `accountLicense.reconLevel` | Not compared in Dev today (new check). |
| Scanning frequency | `scanningInterval`; `accountLicense.scanningFrequency` | The first is compared in Dev; the second is new. |
| Leaked credentials | `accountLicense.leakedCredentialsAllowed`; `leakedCredentialsScanningInterval`; `leakedCredentialsScannedDomains` (list); `accountLicense.leakedCredentialsScannedDomainsNumber` | Domains number is new. |
| SpyCloud flag | `leakedCredentialsSettings.spyCloudSettings.enabled` (bool) | Dev reads only this boolean (`spycloud_enabled`); ON is the Dev default. |
| Account type | `accountType` (also `accountSubtype`) | |
| Primary user | `primaryUser.firstName`, `lastName`, `email`, `isMfaRequired`, `jobTitle`, `phoneNumber`; `primaryUserId` | Phone and job title expected empty (Dev). |
| Counts and toggles | `accountLicense.assetsNumber`, `domainsNumber`, `subDomainsNumber`, `notificationsAllowed`, `multipleUsersAllowed`, `apiAccessAllowed`, `phishingEnabled`, `provisioningEnabled`; row-level `subDomainsReconEnabled`, `webDictionaryBruteForceEnabled`, `webDorkingEnabled`, `fullNucleiScanEnabled`, `authenticatedTestingEnabled`, `staticOutboundIpEnabled`, `aiEnabled` | All already compared in Dev. |
| Operator | `operatorAccounts` | Expected empty (D5). |
| Identity of the create | `id`, `accountUuid` | Dev requires both well formed. |

## 3. Diff: HAR data model versus Dev runner and docs/38 contract

| Item | Result | Label |
|---|---|---|
| Endpoint, `tableServerData`, `pagination_response.table_data`, `total_count` | Same as Dev. | HAR vs CODE |
| Row keys the Dev code reads (`accountName`, `accountDomain`, `accountUuid`, `id`, `lastReconScan`, `accountType`, `scanningInterval`, `leakedCredentialsScanningInterval`, `userEmailDomains`, `alternateDomains`, `subDomains`, `additionalNetworks`, `leakedCredentialsScannedDomains`, `primaryUser.*`, `accountLicense.*`, `campaignsTimeoutInHours`, `operatorAccounts`, `leakedCredentialsSettings.spyCloudSettings`, toggles) | All present in the production row. | HAR vs CODE |
| `accountSettings.reconSettings.automatedDiscoveryEnabled`, `campaignExecutionSettings.domainsMultiAttackStackSettings.enabled` | The parents exist (`accountSettings.reconSettings`, `campaignExecutionSettings.domainsMultiAttackStackSettings`); the leaf keys were not listed. | HAR partial, leaves ASSUMPTION |
| New in production versus the Dev checks | `accountLicense.reconLevel`, `accountLicense.scanningFrequency`, `accountLicense.enabled`, `accountLicense.allowedModules`, `accountLicense.elevatedFeaturesEnabled`, `accountLicense.leakedCredentialsScannedDomainsNumber`, `termsOfUseApproval`, `enabled`, `isDeleted`, `webAiAttackerEnabled`, `vodBetaEnabled`, `accountSubtype`, `emailSettings`, `lastScanStatusEnum`, `pendingValidationAssets`, `lastReconExecutionData`. | HAR |
| Missing from production versus Dev | None found among keys the Dev code reads. | HAR vs CODE |
| Session and settings calls (`getAuthorities`, `accountInfo`, `userProfile`, `license`, `demoAccountDomains`) | Not used by the Dev runner or docs/38 today. | HAR vs CODE |
| `isDeleted` | New safety input: the duplicate and readback checks should treat a soft-deleted row as a stop and ask, not as clear. | ASSUMPTION |

## 4. What the HAR does not show

Not in the HAR: any `account/add` create request, any Add Account form, any edit call, the inner search condition grammar, the authority names, and the `account/add` response shape. Remaining production ASSUMPTIONS: the Add Account form labels, select option texts and required flags match Dev (the form is the same modal in Dev, CODE docs/38, but production is unverified); the `account/add` body and response (the Dev contract is CODE, taken from the CO-0702 creation HAR); the create status code and uncertain-submit behavior; operator field not required (D5); the delete or edit path in production (D2 condition); and that a page-context `fetch` carries the session.

Safest way to learn the rest: the D4 probe (docs/42 6.1) already reads form control names, labels and required flags with Confirm unreachable and records request method, path template and status only. Extend it to record JSON key names of the page's own requests (never values), plus a dry run on the real form with Confirm never clicked, and compare the control names with the Dev `account/add` contract. The `account/add` response shape is visible only at a real create, so it stays unverified until the first gated create (the run then records key names only).

## 5. Recommended implementation order

| Step | Change | File | Proving test |
|---|---|---|---|
| 1 | Pure helper that takes a status plus an authority-name list and returns ready or a stop reason (fail closed on odd shapes). | `integration/onboarding/production_probe.py` | New tests in `integration/tests/test_production_probe.py`: 200 with names, 401, 403, empty list, wrong types. |
| 2 | Probe records authority names and `accountInfo` key names via a page-context GET (read only); the request guard keeps blocking non-GET. | `production_probe.py`, `tools/attended_ce_only_playwright.py` | Guard test: only the two exact GET paths pass; a POST to any other path is blocked. |
| 3 | Probe records request/response JSON key names for the page's own table request, including the search filter keys. | same | Report writer test: refuses any value-shaped text; key names only. |
| 4 | Pure duplicate and readback comparison table using the section 2 field names (reuse the Dev functions, add `reconLevel`, `scanningFrequency`, `isDeleted`). | `integration/onboarding/` (new module beside `production_create`) | Table-driven tests for exact name, domain, ambiguity, demo domain, soft-deleted row. |
| 5 | Production dry run: fill the form, Confirm never clicked, redacted result. | `tools/attended_ce_only_playwright.py` | Fake page object asserts no Confirm click (docs/42 section 4). |
| 6 | Session re-check immediately before Confirm, then the one gated create (D1-D6 approval consumed) and the readback. | same | Gate tests: expired session, drift, consumed approval, uncertain submit never re-clicked. |

`PRODUCTION_RUNNER_SUPPORTED` stays `False` through steps 1-5; opening it is its own reviewed change after the owner's explicit go.

## 6. Risks and what to keep or delete

- The HAR contained a live session token, cookies and a TOTP request: it has been deleted by the owner. Never recreate or share it; if the token or TOTP request may have been exposed to anyone, the owner should sign out and treat that session as ended. The main session handed over names only.
- Do not store `accountInfo`, authority lists or rows beyond names, key names and pass/fail. Account names, UUIDs and emails stay out of reports and docs.
- A page-context fetch run by our code uses the page's own session but must stay GET-only and same-origin; it must never read the token, cookie or storage.
- Never call or script `/api/v1/auth/verify`; no TOTP secret anywhere.
- Demo-domain list is a safety input only; the rule for it needs an owner decision.
- Unknowns that block the first create: the `account/add` shape, the authority names, and D2's residual risk (no known delete or disable path).
