# Query 253: add the CE scan result (owner decision 2026-10-19, option 2)

Owner decision (2026-10-19): extend Redash query **253** ("inventory v2", data source `Prod (Cloned) - Mgmt`) so the
production clone tells the dashboard when a CE-only tenant's first Credential Exposure scan has finished, with no
BackOffice login. Then the dashboard can move the Salesforce stage Account Scanning -> Scan Completed Successfully ->
Onboarding Completed unattended (CE-only skips User Created).

Status: specification only. Nothing has been changed in Redash; nothing was run.

## Why

- The CE scan result is **not** in the account documents. The first live CE scan check of CO-0798 (2026-10-19T20:20Z) and
  all 534 CE-only tenants in the 20:02Z clone have every recon scan field null. Those fields describe Surface recon scans.
- The CE result lives in the tenant's Details > **Duration Per Scan**, i.e. the `campaignExecutions` collection
  ([docs/39](39_REDASH_PROD_CLONE_INVENTORY_2026-10-05.md), used by queries 177, 203, 210 and 237). For CO-0798 it is one
  execution: campaign type `LEAKED_CREDENTIALS_DISCOVERY`, status `DONE`, 2026-10-19 11:45-11:55 local.
- **Clone lag:** the query refreshes hourly, but the cloned database is a daily snapshot. The 20:02Z clone's newest data
  is `2026-10-19T00:01:28Z`, so CO-0798 (created 18:34Z) is not in it yet. A scan finished today is visible after the
  next nightly clone.

## What to add to query 253 (in Redash, by the owner)

Add one `$lookup` stage **before** 253's final `$project`, then four columns to that `$project`. Take the exact field
names of `campaignExecutions` (account link, type, status, start, end) from queries 177 / 203 / 210 / 237; the names
below are the ones the BackOffice reply uses and are the expected ones.

```json
{"$lookup": {
  "from": "campaignExecutions",
  "let": {"acc": "$_id"},
  "pipeline": [
    {"$match": {"$expr": {"$and": [
      {"$eq": ["$accountId", "$$acc"]},
      {"$eq": ["$campaignTypeEnum", "LEAKED_CREDENTIALS_DISCOVERY"]}]}}},
    {"$sort": {"startDate": -1}},
    {"$project": {"_id": 0, "status": "$conversionData.status", "startDate": 1, "endDate": 1}}],
  "as": "lcExecutions"}}
```

New columns in the final `$project` (exact names; the collector checks them strictly):

| Column | Value | Kind |
|---|---|---|
| `lastLeakedCredentialsExecutionStatus` | `{"$arrayElemAt": ["$lcExecutions.status", 0]}` | enum text, e.g. `DONE`, `DONE_WITH_ERRORS`, `PENDING` |
| `lastLeakedCredentialsExecutionStart` | `{"$arrayElemAt": ["$lcExecutions.startDate", 0]}` | date |
| `lastLeakedCredentialsExecutionEnd` | `{"$arrayElemAt": ["$lcExecutions.endDate", 0]}` | date (null while running) |
| `leakedCredentialsExecutionsCount` | `{"$size": "$lcExecutions"}` | non-negative integer |

**Verified 2026-10-19 (Execute-only runs in Redash, nothing saved):** in `campaignExecutions` the account link is `accountId`,
the type is `campaignTypeEnum`, the dates are `startDate` / `endDate`, and the status is **`conversionData.status`** (there is no
top-level `status`; the lookup above projects it as `status`). The corrected query returned 5447 rows, all four columns, status
non-null on 2900 rows (`DONE` 2886, `PENDING` 14), end non-null on 2887, count > 0 on 2900.

Do **not** add the execution id, the campaign id, or any user, operator or customer value. Leave every existing 253
column unchanged.

Notes for the owner:

- If `accountId` is stored as an ObjectId and the account `_id` is too, the match above works as is. If one side is a
  string, wrap it: `{"$eq": [{"$toString": "$accountId"}, {"$toString": "$$acc"}]}`.
- If the query becomes slow on ~5,450 accounts, ask whether `campaignExecutions` has an index on the account link field;
  the result must still finish within Redash's timeout for the hourly refresh.
- Check in Redash before saving: run it, filter the results for `Advanced Technology International - CE Only` after the
  next nightly clone, and expect `lastLeakedCredentialsExecutionStatus = DONE` and `leakedCredentialsExecutionsCount = 1`.

## What I build after the query is saved (local code and tests first, nothing run)

1. `integration/onboarding/redash_inventory.py`: the four columns join query 253's strict v2 list (new kinds: date and
   enum; no free text). An unexpected or missing column still stops the collector (`redash_query_changed`), so a wrong
   edit cannot silently change the clone.
2. The CE scan state from the clone row matched by the verified Account UUID: finished only when the status is `DONE` or
   `DONE_WITH_ERRORS` **and** the execution ended after the create time recorded for that CO (an older execution never
   counts); `PENDING` is running; no execution yet is "not yet".
3. A dashboard background job (no browser, no login) that, for each CO this dashboard created and moved to Account
   Scanning, runs the two guarded stage moves when the clone says finished, the same writes the BackOffice check uses
   (exact from-stage, read back, logged, never retried after an uncertain result). The BackOffice "Check CE scan" button
   stays as the immediate manual fallback.
4. The clone freshness shown on the pages uses the **data** age (`data_latest_modified`), not only the time the query ran.

Owner steps after saving: run `python tools/redash_inventory_collector.py --probe --query-id 253` on the PC and send the
printed column list (names only), so the strict column list matches exactly.
