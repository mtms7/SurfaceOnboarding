# Inactive Repeatable Queue Test Harness

Date: 2026-08-23  
Status: Design only; no Workato asset has been created or tested.  
Scope: Workato Development; no Salesforce or Leonardo dependency.

## Decision

Build an inactive Recipe Function named:

`Surface Onboarding Queue Synthetic Harness (Inactive)`

Use **Recipe function by Workato — New call for function** as the trigger. Workato Test mode allows the operator to provide the trigger parameters manually, so the harness does not need a Salesforce event, a public webhook, an active schedule, or a Leonardo connection.

Do not use Scheduler for the first harness version. A stopped Scheduler recipe can execute immediately when restarted after a missed schedule, while a Recipe Function has no autonomous schedule.

Official references:

- https://docs.workato.com/connectors/recipe-functions/triggers/new-function-call.html
- https://docs.workato.com/en/recipes/testing
- https://docs.workato.com/features/variables.html
- https://docs.workato.com/en/data-tables/connector/upsert-record-action.html

## Safety boundary

The harness must remain inactive except for an explicitly authorized Workato Test. It must not contain connections or actions for:

- Salesforce
- Leonardo
- Donatello
- BackOffice production
- HTTP, OPA, browser automation, or API Platform
- Email, Slack, or external notifications

It must never accept comments, domains, contacts, attachments, credentials, MFA codes, tokens, cookies, or arbitrary JSON payloads.

## Isolation model

Create two Development-only mirror tables instead of writing repeated test records into the operational queue:

- `surface_onboarding_queue_private_test_v1`
- `surface_onboarding_queue_view_test_v1`

The mirror schemas should contain only the fields required for this test. Restrict both tables to the same automation-maintainer role as the private operational table. Do not connect the Workflow App production queue page to either test table.

### Minimum private test schema

| Field | Type | Required test value |
| --- | --- | --- |
| `source_key` | Short text; primary key | `test:workato:surface-onboarding:SYNTH-CO-0701:v1` |
| `sf_record_id` | Short text | `SYNTHETIC-NO-SF-WRITE` |
| `co_number` | Short text | `SYNTH-CO-0701` |
| `account_name` | Short text | `Bormioli Pharma - Synthetic` |
| `approval_status` | Short text | `Approved` |
| `onboarding_stage` | Short text | `Request Approved` |
| `onboarding_product` | Short text | `Surface & Credential Exposure` |
| `onboarding_type` | Short text | `New Product Onboarding` |
| `queue_state` | Short text | `test_only` |
| `sync_status` | Short text | `synthetic_harness_verified` after verification |
| `sensitivity_class` | Short text | `restricted_test_data` |
| `schema_version` | Short text | `queue-synthetic-v1` |
| `test_run_id` | Short text | Operator-supplied unique identifier |
| `eligible_for_automation` | Boolean | `false` |
| `has_attachments` | Boolean | `false` |
| `is_stale` | Boolean | `false` |
| `row_version` | Integer | `1` on create; increment on update |
| `last_synced_at` | DateTime | Test execution time in UTC |
| `error_message_masked` | Long text | Null on success; bounded masked error only |

### Dashboard-safe test schema

Allow only:

- `source_key`
- `co_number`
- `account_name`
- `approval_status`
- `onboarding_stage`
- `onboarding_product`
- `onboarding_type`
- `queue_state`
- `sync_status`
- `schema_version`
- `test_run_id`
- `eligible_for_automation`
- `has_attachments`
- `is_stale`
- `last_synced_at`
- `error_message_masked`

Do not include creator IDs, raw UUIDs, comments, domains, contacts, attachment references, HMAC material, Salesforce URLs, or connector payloads.

## Recipe Function contract

### Parameters schema

| Parameter | Type | Required | Rule |
| --- | --- | --- | --- |
| `mode` | String | Yes | `validate_only` or `upsert_test_tables` |
| `test_run_id` | String | Yes | `SYNTH-YYYYMMDD-HHMM-<short-id>` |
| `source_key` | String | Yes | Must start `test:workato:surface-onboarding:` |
| `co_number` | String | Yes | Must start `SYNTH-` |
| `account_name` | String | Yes | Must end ` - Synthetic` |
| `schema_version` | String | Yes | Exact `queue-synthetic-v1` |
| `eligible_for_automation` | Boolean | Yes | Must be `false` |

All other business fields must be constants declared inside the recipe, not operator-provided free text.

### Result schema

Return only:

- `decision`: `validated`, `test_tables_upserted`, or `blocked`
- `proceed`: always `false`
- `source_key`
- `test_run_id`
- `schema_version`
- `private_record_created`
- `safe_record_created`
- `idempotency_verified`
- `error_category`
- `error_message_masked`

## Recipe sequence

1. **New call for function** receives the typed parameters.
2. **Create variables** defines the normalized bounded fields and UTC timestamp. Workato variables are job-scoped and do not persist between jobs.
3. **Fail-closed validation** blocks unless every contract condition passes:
   - exact schema version;
   - allowed mode;
   - test-only key prefix;
   - synthetic CO prefix;
   - synthetic account suffix;
   - `eligible_for_automation=false`;
   - no null required fields;
   - all strings below the table limits.
4. If `mode=validate_only`, return `decision=validated`, `proceed=false`, and perform no Data Table action.
5. If `mode=upsert_test_tables`, upsert the private mirror table by exact `source_key`.
6. Upsert the safe mirror table by the same exact `source_key`, using an allowlisted projection only.
7. Search each test table by exact `source_key` and verify exactly one record in each. Zero or more than one is blocked.
8. Compare the returned identity and fail-closed flags to the normalized values.
9. Return a masked result with `proceed=false` and `idempotency_verified`.

Set recipe concurrency to `1`. Enable data masking on the trigger, variables, both upserts, both searches, and the return step. Select **Do not store any data** for recipe retention if the workspace supports it.

## Repeatable test cases

### T1 — validation only

- Mode: `validate_only`
- Expected: successful test job; no Data Table rows created or changed.

### T2 — first isolated upsert

- Mode: `upsert_test_tables`
- Expected: exactly one private test row and one safe test row.
- Required action-time authorization before clicking Test because the job writes Workato data.

### T3 — idempotent repeat

- Use the same `source_key` with a new `test_run_id`.
- Expected: the same two rows update; row count remains one per table.

### T4 — wrong source prefix

- Use a key beginning `salesforce:`.
- Expected: blocked before either upsert.

### T5 — automation eligibility attack

- Supply `eligible_for_automation=true`.
- Expected: blocked before either upsert.

### T6 — projection leakage

- Inspect the safe test row and the step output.
- Expected: no private-only fields, raw payload, or unexpected column.

### T7 — duplicate ambiguity

- If an exact-key search ever returns more than one record, stop and report; do not select one or delete automatically.

## Acceptance gate

The harness is accepted only when:

- the recipe is inactive before and after every test;
- T1–T7 meet their expected outcomes;
- no Salesforce or Leonardo dependency appears in the dependency graph;
- no operational queue row changes;
- `eligible_for_automation` remains false in every persisted test record;
- data masking and retention settings are directly verified;
- the operator records job IDs, row counts, recipe version, and UTC timestamps without raw payloads.

## Approvals required before implementation

- Workato owner: create the Recipe Function and the two test tables.
- Security/Data owner: confirm test-table access, retention, masking, and deletion schedule.
- User: action-time authorization immediately before the first `upsert_test_tables` test.

No Salesforce, Leonardo, OPA, account, or production approval is required for `validate_only`, because it has no dependency or external write. Any later Leonardo discovery remains a separate read-only authorization layer.

## Workato build checkpoint — 2026-08-23

Created in the Workato **Development** environment:

- private isolated table: `surface_onboarding_queue_private_test_v1` (asset ID `139394`);
- dashboard-safe isolated table: `surface_onboarding_queue_view_test_v1` (asset ID `139395`);
- inactive Recipe Function: `Surface Onboarding Queue Synthetic Harness (Inactive)` (recipe ID `75087940`).

Directly verified after saving:

- both isolated tables showed `0` records;
- the Recipe Function showed `Inactive`, `0` successful jobs, and `0` failed jobs;
- its dependency summary showed `No assets used`;
- the saved shell contains only the function trigger and a fail-closed return step;
- `proceed`, `private_record_created`, `safe_record_created`, and `idempotency_verified` default to false;
- no Test or Start action was used.

This checkpoint is intentionally a non-writing shell. The variables, validation branches, isolated-table upserts, exact-key searches, masking, and retention controls in the design above remain to be configured before any test is authorized. Do not treat recipe ID `75087940` as an executable table test yet.

## Guarded build checkpoint — 2026-08-23

Recipe `75087940` was repaired and extended while remaining inactive:

- removed the malformed concatenated return mapping;
- mapped `source_key`, `test_run_id`, and `schema_version` to distinct Step 1 datapills;
- added an AND validation gate requiring:
  - `mode = upsert_test_tables`;
  - the test-only `source_key` prefix;
  - the `SYNTH-` CO prefix;
  - the ` - Synthetic` account suffix;
  - exact schema version `queue-synthetic-v1`;
  - a `SYNTH-` test-run prefix;
  - `eligible_for_automation` not true;
- added guarded upserts to private test table `139394` and safe test table `139395` only;
- configured the dashboard-safe action with an explicit allowlisted projection;
- added a guarded success return and retained the global blocked fallback return;
- kept every return value `proceed=false`;
- enabled visible data masking on the function trigger, validation gate, private upsert, and safe upsert;
- directly verified max concurrency `1`;
- directly verified the recipe remained `Inactive` with `0` successful and `0` failed jobs;
- directly verified both isolated tables still contained `0` records;
- did not click Test or Start.

Still blocked before the first table-writing test:

- require exactly one result per test table using the two configured read-back outputs;
- map the read-back results into `idempotency_verified` rather than leaving it false;
- verify or apply masking to both return steps (the builder did not retain a visible masking badge on them);
- verify recipe job-data retention. The available recipe settings exposed concurrency but no per-recipe `Do not store any data` control;
- recheck the dependency report. The recipe canvas visibly contains only the two Workato Data Tables actions, but the summary still displayed `No assets used`.

Recipe `75087940` is therefore configured for an isolated guarded path but is **not yet authorized or accepted for Test mode**.

### Read-back extension

The inactive guarded branch was subsequently extended with two masked `Search records` actions:

- private test table `139394`, filtered by exact `source_key` from Step 1;
- dashboard-safe test table `139395`, filtered by exact `source_key` from Step 1.

The recipe saved successfully after both searches were added. No test ran and both tables remained empty. The search outputs expose `Records → List size`; the remaining implementation gate is to require `List size = 1` for both outputs before returning `idempotency_verified=true`. Until that gate is present, the guarded success return intentionally leaves `idempotency_verified=false`.

### Safe-view read-back gate checkpoint

The inactive recipe was extended again without using Test or Start:

- added a masked nested IF gate requiring the dashboard-safe search output (`Step 6 → Records → List size`) to equal exactly `1`;
- moved the successful return inside that verified branch;
- mapped the returned `source_key`, `test_run_id`, and `schema_version` from the function input;
- kept `proceed=false`, `private_record_created=false`, and `safe_record_created=false`;
- set `idempotency_verified=true` only inside the safe-view exact-count branch;
- removed the obsolete unconditional success return;
- retained the global fail-closed return for invalid inputs or failed read-back;
- enabled visible data masking on the nested IF and verified-success return;
- directly verified the recipe remained `Inactive` with `0` successful and `0` failed jobs;
- directly verified both isolated test tables still contained `0` records;
- observed that the dependency summary now reports `1 asset` rather than `No assets used`.

Workato's nested-condition data tree exposed only the immediately preceding Step 6 search output. The private-table Step 5 `List size` was therefore not referenced with a guessed formula. Before the first accepted table-writing test, add an explicit private-table exact-count gate using a supported builder structure and require both private and safe counts to equal `1`. Until then, the harness remains inactive and is not accepted for Test mode.

### Exact private-and-safe read-back checkpoint

The private-table read-back gate was completed while the recipe remained inactive:

- an accidental `Skipped` state was found on the private upsert, safe upsert, and first private search; all three were changed back to enabled configuration, without executing the recipe;
- the safe-table search was already enabled;
- a second, enabled private-table search was placed immediately after the safe-table search using the same exact `source_key` filter. This is a builder-compatibility read-back, not another write;
- the nested masked IF now requires both:
  - `Step 6` safe-table `Records → List size = 1`; and
  - `Step 7` private-table `Records → List size = 1`;
- the verified-success return remains inside that two-count branch and returns `proceed=false` and `idempotency_verified=true`;
- direct post-save checks showed `Inactive`, `0` successful jobs, `0` failed jobs, and `0` rows in each isolated test table.

The recipe therefore contains the required exact-count read-back protection, but it is still not accepted for Test mode. Outstanding requirements are the non-writing `validate_only` branch, direct retention-policy confirmation, a review of the blocked-result wording, and explicit action-time approval immediately before the first test-table-writing job. The dependency summary reported `2 assets` at this checkpoint, consistent with the two isolated Data Tables.

### Settings and dependency review

Read-only Workato checks after the exact-count checkpoint found:

- recipe concurrency remains explicitly set to `1`;
- the available per-recipe Settings page exposes concurrency, sharing, usage, and a dependency-graph link, but no per-recipe job-data-retention control or `Do not store any data` choice;
- the dependency summary reports `2 assets`, and the saved recipe canvas shows only the two isolated test Data Tables; it shows no Salesforce, Leonardo, OPA, or production dependency;
- the graph page selected the expected recipe and project but did not render individual graph nodes in the available view. Treat the canvas plus the two-asset count as the directly observed evidence, and retain a Workato-owner confirmation of job-data retention as an acceptance requirement.

### Non-writing preflight route

The inactive function now has a top-level, masked `validate_only` route before the test-table-writing route. It requires all of the following before returning a non-writing result:

- `mode = validate_only`;
- `source_key` starts with `test:workato:surface-onboarding:`;
- `co_number` and `test_run_id` each start with `SYNTH-`;
- `account_name` ends with ` - Synthetic`;
- `schema_version = queue-synthetic-v1`;
- `eligible_for_automation` is not true.

When every condition passes, the route returns `decision=validated`, `proceed=false`, the supplied synthetic identity, and false for both creation flags and `idempotency_verified`. It has no Data Table, Salesforce, Leonardo, OPA, or other external action. Requests that do not meet the preflight conditions proceed to the separate existing `upsert_test_tables` gate, which still requires the same contract with `mode=upsert_test_tables`; its No branch remains fail-closed.

Direct post-save verification: recipe `75087940` remained `Inactive` with `0` successful jobs and `0` failed jobs; isolated test tables `139394` and `139395` remained at `0` records. No Test or Start action was used.

### CSE manual-validation fallback

The global fallback return was revised and saved without executing the recipe:

- `decision=manual_review_required`;
- `proceed=false` and all creation/idempotency flags remain false;
- `error_category=manual_validation_required`;
- `error_message_masked=CSE manual validation required: special CO or source or approval parameters do not match the test contract.`;
- the source key, test-run ID, and schema version remain mapped from the function input;
- visible data masking was applied to the fallback return.

Direct post-save verification showed recipe `75087940` still `Inactive`, with `0` successful jobs, `0` failed jobs, and only the two isolated test-table assets as dependencies. No Test or Start action was used.

### Authorized non-writing preflight tests

Two authorized synthetic tests were run against recipe `75087940` version 9. Neither input could satisfy the `upsert_test_tables` route.

- The valid `validate_only` input completed successfully through the masked preflight return (`Step 3`).
- An intentionally special/mismatched synthetic input failed both conditional gates and completed successfully through the masked global fallback (`Step 12`).
- Workato masked both return outputs, so no returned values or inputs were exposed during review. The saved Step 12 configuration therefore remains the verified source for the CSE manual-validation wording.
- Direct post-test inspection showed both isolated tables still at `0` records: private table `139394` and dashboard-safe table `139395`.
- The recipe remains `Inactive`; its detail page now reports `2` successful test jobs and `0` failed jobs. No Salesforce, Leonardo, OPA, or production action occurred.

### Authorized isolated table-write test — fail-closed observation

One authorized synthetic `upsert_test_tables` test was run using a new test-only source key. Direct execution evidence showed:

- the request passed the guarded upsert route and executed the private-table upsert, dashboard-safe-table upsert, and all three exact-key searches;
- both isolated tables now show exactly `1` synthetic record for that source key;
- the two-count IF did **not** meet its condition and therefore followed the masked global fallback return rather than the verified-success return;
- this is a safe failure: the recipe did not proceed beyond the test tables, and no Salesforce, Leonardo, OPA, or production action occurred.

The reason the masked read-back outputs did not satisfy the `List size = 1` gate is not visible without weakening data masking, so it must be corrected by reviewing the configured search-result mappings rather than guessed. The recipe remains `Inactive`; Workato reports `3` successful test jobs and `0` failed jobs. Do not accept the table-writing path until the exact-count return succeeds.

### Read-back mapping correction and acceptance test

The dashboard-safe search (`Step 8`) was inspected in the builder. Its `source_key equals` filter had no value, while both private-table searches already mapped that filter to `Step 1 → Source key`. The missing safe-table filter was mapped to the same Step 1 datapill, with data masking and the two exact-count conditions retained. The recipe was saved as version 10.

An authorized new-key synthetic `upsert_test_tables` test then completed through the masked verified-success return (`Step 11`), which is reachable only when both the safe-table and private-table read-backs equal exactly one record for the input key.

- Both isolated tables contain the new synthetic key; each table now shows two retained synthetic rows in total (the earlier diagnostic row plus the corrected test row).
- The exact-key gate passed for the new key; no duplicate row was accepted by the route.
- Recipe `75087940` remains `Inactive` with `5` successful test jobs and `0` failed jobs.
- No Salesforce, Leonardo, OPA, Donatello, BackOffice, or production action occurred.

### Workflow App read-only readiness review

Read-only review of the existing `Surface Onboarding Control Center` Workflow App found:

- the app is `Offline` and is restricted to the `Surface Onboarding Managers - Pilot` group (one user) with the Manager role;
- page `61105`, `Onboarding Queue (Read-only)`, presents only operational/dashboard-safe columns: CO number, account, approval status, onboarding stage, product, type, queue state, last-synced time, Salesforce-record link, Surface Account ID, and Account UUID;
- every displayed column is configured `Read-only`; the page contains no create, update, delete, recipe-run, private-comments, domains, contacts, or attachment control;
- its loader is recipe `75083979`, `Surface Onboarding Dashboard Queue Source (Inactive)`, which has only: `New load event from a table` → search `surface_onboarding_queue_view_v1` → return data to component;
- the loader remains `Inactive`, reports four successful and zero failed prior jobs, and lists one dependency (the dashboard-safe view table).

No Workflow App page, user group, recipe, Salesforce record, or external system was changed during this review. A separate authorization is required before testing or starting the dashboard source recipe.

### Dashboard loader Test-mode check

An authorized Test-mode check was started for loader recipe `75083979`. Its trigger waits for a new Workflow App Table-widget component event. The offline page preview explicitly reports that preview does not run recipes to load table data, so no trigger event can be generated while the app remains offline. The waiting test was stopped without creating a recipe job.

Post-check verification: recipe `75083979` remains `Inactive` with four successful and zero failed historical jobs. No page, group, recipe, Salesforce record, Leonardo, OPA, or production system was changed. A live-pilot publication to the existing restricted group (or another owner-approved mechanism to emit the component event) is required before this loader can be tested end-to-end.

### Authorized live-pilot dashboard load

The owner authorized publication of the Workflow App solely to its existing pilot group and one end-to-end dashboard-load test.

- Before publication, `Manage public links` showed no configured page and only the option to add one. No public link was created or enabled.
- `Manage access` showed exactly one existing group: `Surface Onboarding Managers - Pilot` with one user and the `Manager` role. The group and its membership were not changed.
- The app was published and directly verified as `Online`; Workato reported it accessible through the Apps Portal. The public-links control was rechecked after publication and still showed no enabled page/public link.
- In the live app, the dashboard loader recipe `75083979` was run only in Workato **Test** mode. A single live Table-widget load event completed successfully on 2026-08-23 at 12:50:09 PDT (496 ms) using version 3.
- The successful path was the expected read-only sequence: component event -> search `surface_onboarding_queue_view_v1` -> return data to the component. The live page showed one CO-0701 row and only the configured dashboard-safe columns. No private comments, domains, contacts, attachment data, or mutation control appeared.
- After exiting Test mode, direct recipe inspection showed `Inactive`, `5` successful jobs, and `0` failed jobs. It was not started.

This authorized publication changes only Workflow App availability to the existing one-user pilot group. The test performed no Salesforce, Leonardo, OPA, Donatello, BackOffice, or production write; it read from the dashboard-safe Workato Data Table and returned the result to the page.

### Authorized dashboard-loader activation

After the live app displayed `Failed to load table data`, direct inspection confirmed that its sole loader, recipe `75083979` (`Surface Onboarding Dashboard Queue Source (Inactive)`), was stopped. The prior successful load had been a temporary Test-mode run, so the live component had no active recipe to serve a normal table-load event.

The owner then authorized starting only this loader. Direct post-action checks confirmed the recipe is active (the Workato control now presents `Stop recipe`), and a new live Workflow App page load returned one CO-0701 row through `surface_onboarding_queue_view_v1` without the error.

The active recipe is limited to `New load event from a table` -> search dashboard-safe table -> return data to component. It does not invoke Salesforce, Leonardo, OPA, Donatello, BackOffice, or any write action. The Salesforce synchronization and reconciliation recipes remain outside this authorization and must remain inactive pending separate approval.

### Authorized queue-page layout adjustment

The owner requested a more usable queue view. Page `61105` was saved with a full-width layout and the existing read-only table repositioned to the top of the page. Direct verification of the live Workflow App confirmed that the wider table loads one CO-0701 row and exposes only the existing dashboard-safe columns.

This was a presentation-only change. No data-table schema, row data, app access, loader logic, Salesforce, Leonardo, OPA, Donatello, BackOffice, or production system was changed. Loader recipe `75083979` remains active solely to serve the read-only table-load event.

### Authorized CO-0701 Salesforce-trigger synchronization check

The owner authorized one controlled test-mode check of `Surface Onboarding Queue Sync (Inactive)` (recipe `75083973`). Builder inspection confirmed that it is scoped by the Salesforce trigger to `Customer Onboarding Name equals CO-0701`; its only configured actions are masked upserts into the restricted queue table and the dashboard-safe queue view. It contains no Salesforce update action and no Leonardo, OPA, Donatello, BackOffice, or production action.

Workato found no new or updated CO-0701 trigger event. This is the expected outcome because the source record was not modified for the test. The waiting test was stopped immediately. Direct post-check verification confirmed the recipe is still `Inactive` (the control is `Start recipe`) and its job totals remain `1` successful and `0` failed, showing that no recipe action or table upsert ran during this check.

The next repeatable test mechanism must provide an authorized synthetic event or a separately designed read-only pull/reconciliation test; it must not modify Salesforce merely to satisfy a trigger.

### Inactive callable CO-0701 read-only test wrapper

The owner authorized creation of `Surface Onboarding CO Read-only Test (Inactive)` (recipe `75088697`) as a repeatable alternative to the event-driven synchronization test. It is a real-time recipe function, constrained to the existing CO-0701 Salesforce record ID. Its sole action is `Get details of specific Customer Onboarding in Salesforce`; it has no Data Table action, Salesforce update, Leonardo, OPA, Donatello, BackOffice, or production action.

The Salesforce action and return step are both configured with Workato data masking. The return contract is intentionally limited to the static test status `read_only_validation_complete`, `salesforce_read_only=true`, and `co_number=CO-0701`; it returns no raw Salesforce fields. Direct post-save verification confirmed the wrapper is `Inactive` (`Start recipe` is shown), has `0` successful and `0` failed jobs, one Salesforce-connection dependency, and a concurrency limit of `1`.

No test execution was run as part of creation. A separate owner authorization is required before invoking this new Salesforce read, even though it has no write action.

### Authorized callable CO-0701 Salesforce read test

The owner subsequently authorized exactly one Workato **Test-mode** invocation of recipe `75088697` on 2026-08-23. The function declares no input parameters, so the test was run with the default empty function-call context. It completed successfully through the existing masked Salesforce `Get details of specific Customer Onboarding` step and masked return step.

Direct post-test verification confirmed the recipe is still `Inactive` (`Start recipe` is shown), with `1` successful job and `0` failed jobs. The saved workflow remains exactly `Function call` -> masked Salesforce Get -> masked Return and declares one dependency. No recipe was started; no Salesforce record was created, updated, or deleted; and neither Workato queue table, Leonardo, OPA, Donatello, BackOffice, nor a production system was accessed or changed.

### Read-only Salesforce reconciliation-field discovery

Read-only Salesforce Object Manager review confirmed the Customer Onboarding object API name is `Customer_Onboarding__c`. The dashboard's existing `Onboardings - Open` list definition is restricted to all Customer Onboarding owners and matches **all** of these filters:

- `Onboarding_Stage__c` is not `Onboarding Completed`;
- `Onboarding_Approval_Status__c` is not `Rejected`.

The two field API names and their active picklist values were directly verified. Stage values are `New`, `Request Approved`, `Account Scanning`, `Scan Completed Successfully`, `User Created`, and `Onboarding Completed`. Approval values are `Pending`, `Approved`, and `Rejected`. The field catalog also directly confirmed the planned operational fields, including `Name`, `Account__c`, `Account_Name__c`, `Onboarding_Product__c`, `Onboarding_Type__c`, `Submission_Date__c`, `Surface_Account_ID__c`, and `Account_UUID__c`.

No SOQL query was run, no recipe was edited or started, and no Salesforce data was changed. Before configuring the inactive reconciliation recipe, confirm SOQL null/blank behavior against the list-view semantics and validate the Salesforce connection's field-level visibility with an owner-approved read-only test.

Security observation: the existing Salesforce list view exposes raw `Onboarding Comments` and a credential-like plaintext value was visible during the metadata/list-definition review. The value was not copied or retained. This blocks any bulk synchronization of raw comments until the source owner remediates the exposure and the Security/Data owner approves the raw-comment retention, masking, and access controls.

### Local Onboarding Comment DealHub-date parser

The automation owner clarified the business rule: one unambiguous valid ISO date range in `Onboarding Comments` is evidence that the internal DealHub subscription-date validation is complete. A local-only parser and focused test suite were added as `phase1_validator/onboarding_comment_dates.py` and `phase1_validator/test_onboarding_comment_dates.py`.

The parser returns only normalised dates and safe operational state. One valid range returns `ready_for_cse_review`; it still returns `proceed=false`, `eligible_for_automation=false`, and requires `cse_manual_validation`. Missing, multiple, malformed, or reversed ranges return a masked fail-closed `blocked` state. The source comment is never returned, logged, or persisted by this parser.

Local verification completed successfully on 2026-08-23:

```text
Ran 15 tests
OK
```

This is a local implementation and test result only. No Workato recipe, Salesforce record, Data Table, Leonardo, OPA, Donatello, BackOffice, or production system was changed. Mapping the parser into the restricted Workato private queue remains subject to the raw-comment security/retention decision and separate explicit authorization.

### Manual CSE approval gate before any Leonardo creation

The automation owner authorized creation of a Workato-only, deny-by-default control before any future Leonardo request. No Leonardo connection, request, authentication, or creation action was added.

- A blank approval table, `surface_onboarding_cse_approval_gate_v1` (table `139401`), was created with `0` records. It contains only approval-control metadata: request/source/CO keys, approval and CSE-validation status, approver and policy metadata, an allow flag, expiry/approval timestamps, a masked reason, and a revision number. It contains no raw onboarding comments, contact details, domains, attachments, credentials, or other source payload.
- Workflow App tab `CSE Approval Gate` uses that table. Its initial request page (`61114`) was narrowed to four inputs only: approval-request key, queue-source key, CO number, and a masked CSE handoff note. Requesters cannot set approval status, approver identity, expiry, revision, policy version, or the Leonardo allow flag from that page. The page also warns that credentials, attachments, contacts, domains, and raw onboarding comments must not be entered.
- A separate `CSE Approval Decision` page (`61115`) was saved in the workflow's `In progress` stage using Workato's approval-form template. It displays request metadata read-only and provides only Workato's built-in `Approve` / `Reject` decision controls. No request was submitted, approved, or rejected while building the page.
- Recipe `75088933`, `Surface Onboarding Leonardo Approval Gate (Inactive)`, was created as an inactive, masked recipe function with no dependencies and `0` successful / `0` failed jobs. Its current saved return is deliberately static and fail-closed: `decision=blocked_pending_manual_approval`, `proceed=false`, and `action_required=cse_manual_validation`. It has no app action beyond its function-return step and therefore cannot contact Salesforce, Leonardo, OPA, Donatello, BackOffice, or production.

This is a protective scaffold, not a Leonardo integration. Before any later Leonardo preflight or creation recipe is designed, a named CSE approver group and a complete decision-recorder flow must be configured. The gatekeeper must then read exactly one approval record and verify the matching source key, CO number, approved CSE decision, policy version, approval time, and unexpired approval window. Until that separate work is authorized, the saved gatekeeper always blocks.

### Staged CSE approver pilot

On 2026-08-24, the automation owner supplied a four-person future CSE-approver roster. Direct Workato access review confirmed that the existing `Surface Onboarding Managers - Pilot` group currently contains exactly one user: the automation owner. The group, its membership, and its `Manager` role were not changed; no public link exists.

The named roster is therefore an authorization plan for the later test phase, not current access. The single-user pilot cannot provide separation of duties: it must never authorize a Leonardo action, even if that user completes a Workflow App approval task. Before any Leonardo preflight or test, create and verify separate requester and CSE-approver access groups, then obtain a new explicit approval immediately before adding group members.

### Inactive CSE decision-recorder scaffold

Recipe `75430449`, `Surface Onboarding CSE Decision Recorder (Inactive)`, was created on 2026-08-24. It has one real-time `Workflow apps by Workato` trigger, `New/updated request`, scoped only to `Surface Onboarding Control Center`. Direct post-save verification showed `Inactive`, `0` successful jobs, `0` failed jobs, and one Workflow App dependency.

The recipe intentionally has no action steps. It does not create or update a Data Table row and cannot contact Salesforce, OPA, Leonardo, Donatello, BackOffice, or production. Its next permitted configuration step is a separately authorized, Workato-only synthetic approval test that establishes the exact request-completion fields before mapping a masked approval decision to the gate table. Any absent, duplicate, denied, expired, or mismatched decision must leave `leonardo_request_allowed` false.
