# Redash production-clone inventory (2026-10-05)

Owner decision (2026-10-05): **read-only, automated use** of one saved Redash query on the cloned production
database, so tenant data and scan status can be collected on a schedule with no browser, no MFA, no Claude.

## Verified facts (read-only review of redash.pentera.io, 2026-10-05)

| Fact | Evidence |
| --- | --- |
| Redash data sources include `Dev - Mgmt`, `Staging - Mgmt`, `Prod (Cloned) - Mgmt`, `3rd-Party - *`, `Hybrid - Prod`, `Dev - Attack`, `Prod (Cloned) - Attack`. | New-query data source list. |
| **No Redash source is the Leonardo Development database.** `Dev - Mgmt`: 230 tenants, newest created 2026-08-11. `Staging - Mgmt`: 1,905, newest created 2026-08-30. Leonardo Development: 594 tenants, newest created 2026-10-04 (CO-0757). | Same read on each source, sorted by `_created`. |
| `Prod (Cloned) - Mgmt`: 5,439 tenants; newest change 2026-10-04 16:49 when read on 2026-10-05, so the clone is at most about a day behind. | Same read. Exact clone lag is not measured. |
| The tenant view `tenantsMView` carries the Leonardo row fields; collection `campaignExecutions` carries per-scan start, end, type and status (Leonardo's "Duration Per Scan" data). | Existing queries 177, 203, 210, 237. |
| `lastScanStatusEnum` and `alternateDomains` are present in the clone's results. | Query 251 result columns. |

## What was created in Redash

One saved query, **id 251**, "Surface Onboarding | Tenant inventory (read-only)", data source `Prod (Cloned) - Mgmt`,
unpublished. It is an aggregation with `$project` only: id, UUID, name, domains, alternate domains, account type,
enabled/deleted, created/modified, scan interval/last scan/duration/status, operator **count**, licence type/dates/
quotas, SpyCloud object (read only for its `enabled` boolean). **No** user names, emails, phones, or operator ids.
No other query, dashboard, alert, or data source was changed.

## What was built (local only)

| File | Role |
| --- | --- |
| `integration/onboarding/redash_inventory.py` | Pure adapter (no transport; the offline-boundary guard stays green): result shape check, column allow-list, strict date/number parsing, Leonardo-shaped rows. |
| `integration/onboarding/leonardo_inventory.py` | New environment `prod-clone` (label "PROD (cloned copy via Redash)", file prefix `redash-prod-clone`), `assemble_rows`, shared row validation, `snapshot_payload(..., source=, acquisition=, expected_paths=)`. BackOffice `prod` stays refused. |
| `tools/redash_inventory_collector.py` | Transport + CLI: `--store-key`, `--probe`, `--collect`. |
| `tools/register_redash_inventory_task.ps1` | Registers the hourly Windows task (operator runs it; `-WhatIf` previews). |
| Dashboard `/tenants` (sidebar "Tenants"; `/inventory?env=prod-clone` redirects here) | Production clone tab: data age, source newest change, no CO links, no refresh button, plus "Check a CO for duplicates in production" (POST `/attended/production-duplicate-check`; CLI `--production-duplicate-check --co CO-XXXX`). The Leonardo Development inventory is the "DevOps" tab at `/inventory`. |
| `integration/tests/test_redash_inventory.py` | Adapter, transport safety, key store, dashboard view. |

## Safety properties

* Host pinned to `redash.pentera.io` over HTTPS; only query 251's results plus the job/result endpoints a refresh needs; redirects refused; key sent only in the `Authorization` header.
* The **per-query** API key (not the user key) is stored as a Windows DPAPI blob (current user) in `%LOCALAPPDATA%\SurfaceOnboarding\redash\query-251.key`, entered with a hidden prompt, never printed/logged/in argv.
* Output is reason codes and counts only. Failures fail closed and leave the previous snapshot untouched. Results older than 36 h, or dated in the future, are refused.
* Only four exact (method, path) pairs are ever called (cached results GET, refresh POST, one job GET, one result GET); anything else is refused before a request is built.
* A changed query (unexpected column), a result more than 10% smaller than the last snapshot (override: `--accept-shrink`), more than 10% of tenants without a UUID, or a missing `accountDomain` / `alternateDomains` / `lastScanStatusEnum` column stops the run (nothing written).
* A refresh that did not happen is not hidden: the run still writes the older data with its true age but exits **2** (Task Scheduler shows a failed run).
* `--store-key` refuses a non-console or echoing prompt. DPAPI buffers are wiped after use. The registration script runs `--probe` first and refuses to register a task that cannot work.
* Independent security review done 2026-10-05 (no key-leak path or Redash write path found); its findings 1-7, 9 and 10 are fixed and tested. Remaining by design: item 8 (operational), below.
* The snapshot is informational: it can block a duplicate, it never clears one; Start and the read-after-write check still use Leonardo live.

## Operator steps (in your own terminal; never paste the key into chat)

```
python tools\redash_inventory_collector.py --store-key     # paste the query-251 API key at the hidden prompt
python tools\redash_inventory_collector.py --probe         # shape only; confirms the result format
python tools\redash_inventory_collector.py --collect       # writes the snapshot
powershell -File tools\register_redash_inventory_task.ps1 -WhatIf
powershell -File tools\register_redash_inventory_task.ps1
```

## Deployed and verified live (2026-10-05, operator desktop)

* Key stored by the operator (DPAPI); `--probe`: 26 columns as expected, `normalize: ok`, 5,439 rows, data 23 min old.
* First `--collect`: 5,439 tenants, 0 deleted, 0 without UUID, 0 schema drift (16.1 MB snapshot + 1.8 MB CSV).
* Scheduled task `SurfaceOnboarding-RedashInventory` registered (hourly, current user, only while signed in, 10-min limit, no overlap). First unattended run exit 0 (`inventory_unchanged`: Redash's cached result had not moved). The first run that asks Redash for a refresh (cached result older than 60 min) is the one that proves the per-query key may re-run the query; check `collector.log` for `"refreshed": true` or `"refresh_failed": true` (exit 2).
* The duplicate pre-check page and CLI (`--duplicate-precheck`) now also show the production clone's answer. It is **informational**: it never changes the DEV result and a Start run neither reads nor logs it (the golden run log is unchanged).

## Why the Dev collection is not retired yet

Measured on 2026-10-05 against the real snapshots: the 594 Dev tenants share **1 id and 0 UUIDs** with the 5,439 production tenants, and **none** of the 7 CO readbacks on record exist in the clone. The clone is production; the onboarding target is Leonardo Development. The attended Dev export still feeds the Start duplicate gate, `--validate-all` / scan-status sweeps for onboarded Dev tenants, and the CO to tenant links. Removing it would remove gates (AGENTS.md: do not relax a gate). It can be retired the moment a Redash data source for the Leonardo Development database exists (same query, same collector), or when onboarding moves to production.

## Open items

* **Operational (review item 8):** the collector cannot see which key type it holds or whether the data source user is read-only. Use the **query-251 key** (not your user key), keep edit rights on query 251 limited, and set a rotation date for the key (it bypasses SSO/MFA). An edited projection is caught by the unexpected-column check.
* The default Python path used by the task lives under `.cache\codex-runtimes`; if that runtime moves, re-register with `-PythonPath`.
* Leonardo Development tenants still need the attended UI export (needs the operator's sign-in). A read-only Redash source for the Leonardo Development database would remove that; it must be added by whoever administers Redash.
* The first live `--probe` confirms how Redash serialises dates; the adapter fails closed (`redash_date_unparseable`) rather than guessing.
* Whether Redash allows the per-query key to re-run the query (the collector's POST refresh) is unverified. If it is refused, the collector uses the cached result; set a refresh schedule on query 251 in Redash instead.
* Duplicate pre-check still reads the DEV inventory only (production onboarding is not enabled). Switching it to the clone for production COs is a later, separate change.
* SpyCloud ON/OFF is now stored as one boolean, `spycloud_enabled` (true / false / null when absent or not a boolean), derived from `leakedCredentialsSettings.spyCloudSettings.enabled` in the query-251 object (or its flat `.enabled` column). Nothing else from that object is kept (the allow-list test asserts it). It is shown in the Tenants page and CSV; `--validate-all` warns "SpyCloud is ON (owner: must be OFF)" for Credential Exposure routes. Open: the first live SpyCloud-OFF save in Leonardo Development needs the operator's approval.
* Scan status for production tenants can come from the same hourly pull (`lastScanStatusEnum`); wiring it into the CO page is not done yet.
