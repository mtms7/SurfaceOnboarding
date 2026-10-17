# Query 251 coverage vs the manual-create HAR - 2026-10-17

Status: read-only analysis. Key names only, no values. No external call. Sources (V = read directly): newest local snapshot `surface-vm-snapshot-20261016T234043Z.zip` (`inventory_prod_clone_snapshot.json`, 5446 rows), `integration/onboarding/redash_inventory.py` (column lists), `tools/redash_inventory_collector.py`, `integration/onboarding/production_contract.py` (readback), docs/44, 47, 48, 49, and `grupopatio.pentera.io.har` (22 requests, key names). Other HARs (`app`, `hh`) hold no account lifecycle fields: `app` is a 403 on getAllDetailedAccounts; `hh` is inventory/activity/audit only. The grupopatio HAR has NO `accountInfo`/`license`/`userProfile` call; those key names come from docs/48 only.

## 0. What query 251 returns (V)
Columns the collector knows (`redash_inventory.py:32-56`; the live probe reports `missing_columns=[]`, `unexpected_columns=[]`, drift none, so all are present): `_id _created _modified accountName accountUuid accountDomain alternateDomains accountType accountSubtype enabled isDeleted scanningInterval lastScanStatusEnum lastReconScan lastReconScanDurationMilliseconds operatorCount accountLicense.{licenseType,enabled,scanningFrequency,leakedCredentialsAllowed,startDate,expirationDate,assetsNumber,domainsNumber,subDomainsNumber,leakedCredentialsScannedDomainsNumber} leakedCredentialsSettings.spyCloudSettings(.enabled)`. Anything else is reported as `unexpected_columns` and ignored. In the clone these normalized paths are ALWAYS null (not projected): `account_country_code`, `leaked_credentials_scanning_interval`, `campaigns_timeout_hours`, `terms_accepted`, `primary_user.mfa_required`, every `toggles.*`, `counts.user_email_domains/sub_domains/additional_networks/leaked_credentials_scanned_domains`, `license.{allowed_modules,api_access_allowed,notifications_allowed,multiple_users_allowed,phishing_enabled,provisioning_enabled,recon_level,elevated_features_enabled}`. `account_subtype` null in all rows. `primary_user.present` is a bool only. `operator_assigned` is a bool derived from `operatorCount` (count dropped).

## 1. Field table (Q251 = clone has a populated value; Create = in `account/add` body; GADA = in `getAllDetailedAccounts` row; Used = by dashboard/readback)
| field | Q251 | Create | GADA | Used |
|---|---|---|---|---|
| id | yes | no | yes | match, UI |
| accountUuid | yes | no (response has none) | yes | readback (uuid_ok), SF writeback |
| accountName (suffix check) | yes | yes | yes | match, readback |
| accountDomain | yes | yes | yes | match, readback |
| alternateDomains | yes | yes | yes | match, duplicate gate |
| subDomains | no | yes | yes | not compared |
| additionalNetworks | no | yes | yes | no |
| userEmailDomains (internal domain) | NO | yes | yes | readback only (API) |
| accountCountryCode | NO | yes | yes | readback only |
| accountType | yes | yes (lower) | yes (Customer) | readback |
| accountSubtype | no (null) | no | yes (null) | no |
| enabled | yes | no | yes | live-paid, table |
| isDeleted | yes | no | yes | live-paid |
| _created / _modified | yes | no | yes | table |
| scanningInterval | yes | yes | yes | readback |
| leakedCredentialsScanningInterval | NO | yes | yes | readback |
| leakedCredentialsScannedDomains | NO (count only, null) | yes | yes | no |
| accountLicense.licenseType | yes | yes | yes | live-paid, match |
| accountLicense.enabled | yes | yes | yes | table |
| accountLicense.start/expirationDate | yes | yes | yes | match (end) |
| accountLicense.assets/domains/subDomainsNumber | yes (subDomains 39/2000) | yes | yes | readback |
| accountLicense.scanningFrequency | yes | no (resp) | yes | readback |
| accountLicense.leakedCredentialsAllowed | yes | yes | yes | readback |
| accountLicense.leakedCredentialsScannedDomainsNumber | yes (~half) | no (resp) | yes | no |
| accountLicense.reconLevel / provisioningEnabled / notificationsAllowed / multipleUsersAllowed / apiAccessAllowed / phishingEnabled | NO | yes | yes | readback |
| accountLicense.allowedModules / elevatedFeaturesEnabled | NO | no (resp) | yes | no |
| leakedCredentialsSettings.spyCloudSettings.enabled | yes (6 null) | yes | yes | readback, table |
| 8 flags: automatedDiscoveryEnabled (accountSettings.reconSettings), subDomainsReconEnabled, webDictionaryBruteForceEnabled, webDorkingEnabled, staticOutboundIpEnabled, aiEnabled, webAiAttackerEnabled, vodBetaEnabled | NO | yes | yes (webAi/vodBeta null) | readback |
| fullNucleiScanEnabled, authenticatedTestingEnabled | NO | no | yes | no (docs/48 compares them in Dev) |
| campaignsTimeoutInHours | NO | yes | yes | readback |
| campaignExecutionSettings.{domains,subDomains}MultiAttackStackSettings.{enabled,numOfAssetsPerStack} | NO | yes | yes | readback |
| primaryUser.{isMfaRequired, id, firstName, lastName, email, jobTitle, phoneNumber}, primaryUserId | NO (present bool only) | yes (mfaRequired, names, organizationEmail) | yes | no (PII) |
| operatorAccounts[].{accountId, operatorName} | count only | operatorAccountNames | yes | no |
| lastScanStatusEnum | yes (63/2000 set) | no | yes | table |
| lastReconScan, lastReconScanDurationMilliseconds | yes | no | yes | table |
| pendingValidationAssets, lastReconExecutionData.timedOutActions | NO | no | yes | no |
| termsOfUseApproval | NO | no | yes (null) | no |
| accountSettings.{accountLockout,assetTimeToLive,provisioning}Settings, emailSettings, accountNotificationSettings | NO | no | yes | no |
| scanNow | no | yes (false) | no | no |

## 2. Missing from Q251 that we need
(i) verify a created tenant: `userEmailDomains`, `accountCountryCode`, `leakedCredentialsScanningInterval`, `leakedCredentialsScannedDomains`, `accountLicense.reconLevel/provisioningEnabled/notificationsAllowed/multipleUsersAllowed/apiAccessAllowed/phishingEnabled`, the 8 flags (+ `fullNucleiScanEnabled`, `authenticatedTestingEnabled`, so 10 with the two off-create Advanced items; owner's "9" is not reproducible from the HAR: it lists 8 create flags), `campaignsTimeoutInHours`, MultiAttackStack settings, MFA (`primaryUser.isMfaRequired`), operator list names, `subDomains` list. Already present: name, domain, accountType, dates, assets/domains counts, scanningInterval, SpyCloud, uuid, id.
(ii) monitor status: Q251 already has `enabled`, `isDeleted`, `accountLicense.enabled`, expiry, `lastScanStatusEnum`, `lastReconScan`, `_created/_modified`, operator count. NOT available anywhere in the HAR GADA row: last login / last activity, user count (only `primaryUser` + `multipleUsersAllowed`), scan status history, `accountInfo`-style health. Last activity would need `POST /api/v1/audit/operational` or `activityLog/*` (seen in `hh` HAR, per-tenant context, not an account-list field) - not Redash-suppliable from the account collection unless the Redash source holds users/audit collections (unverified A).

## 3. Each missing field: in BackOffice API? likely excluded by Redash for PII/secrets?
- All fields in section 2(i) exist in the GADA row (V, HAR). Redash exclusion is by projection, not PII: the nulls are columns never selected (A: the saved query lists columns explicitly; `account_subtype` shows Redash simply omits some). Redash owner can add them.
- `userEmailDomains`: API yes; a domain, not a mailbox - low PII, useful (internal vs customer domain is a create check). Include.
- `primaryUser.email/firstName/lastName/phoneNumber/jobTitle`: API yes; PII - keep OUT; derive only `isMfaRequired` and `primaryUser present`.
- `operatorAccounts[].operatorName`: PII-ish (people); keep count + a boolean "internal operator only" computed in SQL, no names.
- Secrets: no secret-like key appears in the GADA row (V); `textSearchField` in license may echo names - exclude.
- last login / user count / activity: NOT in the GADA row; API only through other endpoints (audit/activity); Redash availability unknown (A).

## 4. Proposal: query 251 v2 (new query, never edit 251; read-only; owner action)
Add columns (flat names, same naming as today): `userEmailDomains`, `accountCountryCode`, `leakedCredentialsScanningInterval`, `leakedCredentialsScannedDomains`, `subDomains`, `additionalNetworks`, `campaignsTimeoutInHours`, `accountLicense.{reconLevel,provisioningEnabled,notificationsAllowed,multipleUsersAllowed,apiAccessAllowed,phishingEnabled,allowedModules,elevatedFeaturesEnabled}`, `automatedDiscoveryEnabled` (= `accountSettings.reconSettings.automatedDiscoveryEnabled`), `subDomainsReconEnabled`, `webDictionaryBruteForceEnabled`, `webDorkingEnabled`, `staticOutboundIpEnabled`, `aiEnabled`, `webAiAttackerEnabled`, `vodBetaEnabled`, `fullNucleiScanEnabled`, `authenticatedTestingEnabled`, `campaignExecutionSettings.domainsMultiAttackStackSettings.enabled` and `.numOfAssetsPerStack` (+ subDomains pair), `primaryUser.isMfaRequired`, `primaryUserPresent` (bool), `internalOperatorOnly` (bool) , `pendingValidationAssets`, `userCount` (count only, if the users collection is in the Redash source: A), `lastLoginAt` max over users (timestamp only, A). Exclude: names, emails, phones, job titles, operator names, `textSearchField`, any token/secret.
Owner must: (1) create a NEW saved query (v2) with these columns on the same `Prod (Cloned) - Mgmt` source; (2) set an hourly scheduled refresh; (3) issue a per-query read-only API key (the existing 251 key cannot POST-refresh, docs/44); (4) give the new id to us. Code side then: add the columns to `redash_inventory.py` KNOWN/PASSTHROUGH lists and `leonardo_inventory` allow-list (not done here; the strict column check means unknown columns are only reported as unexpected).

## 5. Freshness mechanics (V)
- `captured_at` in the snapshot = Redash `query_result.retrieved_at` (collector `_retrieved_at`, `snapshot_payload(..., normalized.retrieved_at)`), i.e. when Redash last RAN the query, NOT when the collector fetched it. `source.collected_at` is the fetch time and is kept separately; `source.data_latest_modified` = newest `_modified` in the rows. Clone age shown by the dashboard = now - `captured_at` (`_clone_info`).
- Collector: `DEFAULT_MAX_AGE` 60 min (older triggers a POST refresh), `HARD_MAX_AGE` 36 h (refused), dashboard/gate trust window 6 h (`PRODUCTION_GATE_MAX_AGE`). The per-query key is refused on POST, so `refreshed_this_run=false` in all three local snapshots: only Redash's own schedule advances `retrieved_at`.
- Observed: `collected_at` is ~:09 minutes past the hour pattern; `retrieved_at - collected_at` gap was 59-60 min in all three snapshots (query run ~1 h before the fetch). Samples: retrieved 2026-10-15T19:09, 10-16T18:09, 10-16T21:09; the schedule interval itself cannot be proven from 3 samples (A: 3 h or less).
- Hourly Redash schedule is enough for the 6 h TRUST gate (worst case about 60 min Redash + 60 min collector + VM ingest lag = about 2-2.5 h). It is NOT enough to confirm a freshly created tenant: it appears in the clone only after the next Redash run plus collect (up to about 2 h), so creation verification must be the API readback (docs/49 s.3, `compare_readback`), and the dashboard must show "in clone: not yet" separately. For monitoring status, hourly is acceptable if labelled "as of captured_at"; `data_latest_modified` was 2026-10-15T23:26Z at the 10-16T21:09Z capture (no row modified in about 22 h), so do not read it as freshness.

## 6. Used by dashboard / readback (V)
Clone feeds `production_match` (name, domains, license type/end, SpyCloud, operator bool, scan interval, live-paid via enabled/isDeleted/licenseType) and `/prod/tenants` (name, id, licence, domain+alternates, quota, scan, interval, SpyCloud, state). The readback (`production_contract.READBACK_PICK_PATHS`, `compare_readback`) uses the GADA row, NOT the clone: userEmailDomains, countries, intervals, license toggles, 8 flags, SpyCloud, uuid shape. So query 251 can never confirm those fields today.

## 7. Query 253 (inventory v2) in the collector - owner steps and field list (added 2026-10-17)

Status: local code and tests only; nothing was run against Redash, the VM or Salesforce. Query 251 stays the default and the fallback; 253 is used only when selected.

**What is selectable.** `--query-id 253` on `tools/redash_inventory_collector.py` (`--store-key`, `--probe`, `--collect`), or `SURFACE_REDASH_QUERY_ID=253`; the argument wins over the variable, the default is 251, any other id is refused (`redash_query_id_unsupported`). Each query has its own strict column list (unexpected or missing columns are reported per query exactly as before; an edited 253 stops with `redash_query_changed` and leaves the old snapshot), its own four exact URL paths, and its own key: PC `%LOCALAPPDATA%\SurfaceOnboarding\redash\query-253.key` (DPAPI, as for 251); Linux `SURFACE_REDASH_KEY_FILE_253` (251 keeps `SURFACE_REDASH_KEY_FILE`). The key is never echoed or logged.

**Owner steps (PC, your own PowerShell; the 253 per-query read key is needed first).**
1. Store the key (hidden prompt, nothing printed): `python tools/redash_inventory_collector.py --store-key --query-id 253`
2. Shape check (counts and column names only): `python tools/redash_inventory_collector.py --probe --query-id 253`. Expect `normalize: ok`, `unexpected_columns: []`, `missing_columns: []`.
3. Switch the hourly task to 253 (same task name, replaced): `powershell -File tools/register_redash_inventory_task.ps1 -WhatIf -QueryId 253`, then without `-WhatIf`. To go back: the same command with `-QueryId 251` (default).
4. After the next run, `collector.log` shows `query_id: 253, row_schema: v2`. The clone push builder and the VM ingest need no change: they accept v1 and v2 (a v1 clone still loads).

**Snapshot format.** Snapshots from 253 carry `row_schema: "v2"` in the payload (absent = v1, as 251 writes today; `schema_version` stays 1 so every existing loader still works) and `source.query_id: 253`. Each tenant keeps all v1 fields and gains `v2`, a flat object of snake_case keys with None omitted (read with `.get`). `accountLicense` extras also fill the v1 `license` fields. `build_clone_push_bundle.py` refuses to sign, and `ingest_clone_push.py` refuses (`row_schema_invalid`), a v2 row with an unknown key, a wrong type, a null, a list over 500 items, an unknown marker, or `v2` rows under a v1 marker. Size, shrink (90% of the largest accepted), first-push floor and freshness checks are unchanged; v2 rows add roughly 0.5 to 1 KB each (a test keeps 6,000 rows below the 32 MB file limit; re-check on the real file after the first 253 run).

**v2 keys** (column -> key; kind): `accountCountryCode` account_country_code (str); `userEmailDomains` user_email_domains (list of str); `enumerationScanningInterval` enumeration_scanning_interval, `leakedCredentialsScanningInterval` leaked_credentials_scanning_interval (str or int); `operatorCount` operator_count, `subDomainsCount` sub_domains_count, `additionalNetworksCount` additional_networks_count, `pendingValidationAssetsCount` pending_validation_assets_count, `domainsMultiAttackStackAssetsPerStack` domains_multi_attack_stack_assets_per_stack, `subDomainsMultiAttackStackAssetsPerStack` sub_domains_multi_attack_stack_assets_per_stack (non-negative int); `leakedCredentialsScannedDomains` leaked_credentials_scanned_domains (list of str); bools: static_outbound_ip_enabled, full_nuclei_scan_enabled, ai_enabled, web_ai_attacker_enabled, vod_beta_enabled, web_dictionary_brute_force_enabled, web_dorking_enabled, sub_domains_recon_enabled, authenticated_testing_enabled, automated_discovery_enabled, domains_multi_attack_stack_enabled, sub_domains_multi_attack_stack_enabled, primary_user_mfa_required, primary_user_present, spy_cloud_enabled. License columns (`reconLevel`, `notificationsAllowed`, `multipleUsersAllowed`, `apiAccessAllowed`, `phishingEnabled`, `provisioningEnabled`, `allowedModules`, `elevatedFeaturesEnabled`) go to the existing `license.*` fields, not to `v2`. Columns already stored in v1 (identity, dates, scan, SpyCloud) are unchanged. SpyCloud: 251 exposes `leakedCredentialsSettings.spyCloudSettings` (object) or `.enabled`; the collector reads those, and 253's `spyCloudEnabled`; all three feed the one stored boolean.

**Still open (not done here).** The dashboard layer reading `v2` (files owned by the parallel change); the VM collector switching to 253 (it needs its own root-owned key file `SURFACE_REDASH_KEY_FILE_253` and `--query-id 253` in the unit; not touched).
