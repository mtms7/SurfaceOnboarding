# Production dashboard: data contract - 2026-10-16

Status: analysis only (read-only). No existing file was changed. `PRODUCTION_RUNNER_SUPPORTED` stays `False`; nothing here authorizes a production write, a Salesforce write or any external call. Companion to `docs/46_PRODUCTION_DASHBOARD_REDESIGN_2026-10-16.md` (the look) and `docs/45_PRODUCTION_DECISIONS_D1_D6_2026-10-16.md` (decisions).

Legend. **V** = verified by reading the code at the cited `file:line`. **A** = assumption or inference, not verified. Paths: `DASH` = `tools/serve_attended_open_onboardings_dashboard.py`, `PW` = `tools/attended_ce_only_playwright.py`, `INV` = `integration/onboarding/leonardo_inventory.py`, `PM` = `integration/onboarding/production_match.py`, `ON` = `integration/onboarding/`.

The `new modules` (`production_approval`, `production_create_record`, `production_create`, `salesforce_uuid_writeback`, `run_target`) were being fixed while this was written: only their public API was read (V), and they may change. Line numbers for them are from the working tree on 2026-10-16.

## 0. Headline findings

1. **V - The new modules are not wired into the dashboard at all.** `grep` for `production_approval|production_create|salesforce_uuid_writeback|run_target` in `tools/*.py` finds only two unrelated error-text strings (`DASH:530-531`). Nothing in the dashboard reads `production_create_approvals.json`, the redacted D2 records, or the create result. Every "Approval" and "Create" chip in docs/46 needs new read functions (section 3).
2. **V - There are two different approvals.** The 4-hour review-summary approval (`ON/review_approval.py:22-23`, file `onboarding_review_approvals.json`, written by `DASH:7686 _post_review_approve`) and the D1 7-day one-use create approval (`ON/production_approval.py:27,30`, file `production_create_approvals.json`, issued only through `production_approval.issue`). The dashboard shows only the first (`DASH:6404`, `6460-6472`). The design must label them differently ("Summary reviewed" vs "Create approved").
3. **V - The per-CO production match can only be computed from a clone at most 6 h old.** `production_match.load_snapshot` defaults to `PRODUCTION_GATE_MAX_AGE` (`PM:35`, `INV:302`) and a stale snapshot returns only a reason string, so the result carries `captured_at=None, age_seconds=None` (`PM:163-165,173-174`). Only the page-level `_clone_info()` loads any age (`DASH:6212-6222`, `INVENTORY_DISPLAY_MAX_AGE` = 3650 days, `DASH:5765`). So "clone age" for a stale clone is available only from `_clone_info`, never from a per-CO result.
4. **V - `/prod` and `/prod/matches` default to "only COs onboarded on Dev"** (`DASH:6252-6263`, toggle `?all=1`). A production-first queue must list every open CO by default; this default must be inverted.
5. **V - The VM cannot show approvals, D2 records or the create outcome today.** The signed-snapshot allow-list `STATE_FILE_NAMES` (`ON/vm_snapshot.py:72-78`) contains neither approval file, and bundle members are flat names (`vm_snapshot.py` docstring "flat file names, no folders"), so the `production-create-records/` folder cannot travel. On the VM, `state_file()` points into `snapshots/current/` (`ON/state_paths.py:62-71`) and `review_approval.load` returns `{}` for a missing file (`ON/review_approval.py:58-62`), so a VM page would say "Not approved yet" when the truth is "not published to this copy". That wording must not be used on the VM (fail-closed wording below).

## 1. Inventory: what supplies production data today

### 1.1 Clone (Redash production clone) loading and trust

| Item | Where | What it does | Verified |
|---|---|---|---|
| `clone_root()` | `DASH:5778` -> `ON/state_paths.py:78 clone_inventory_root` | Folder of the `prod-clone` snapshot. Desktop without state dir: next to the Dev inventory. State dir set: `<state>/clone-inventory`. VM: own collected clone once `prod-clone/latest.json` exists there, else the clone from the signed snapshot (`<current>/leonardo-inventory`). | V |
| `inventory.load_latest(root, "prod-clone", max_age, now)` | `INV:708-740` | Re-verifies schema version, environment, origin, endpoint, row hash, name stamp, future skew. Reasons: `inventory_snapshot_missing / tampered / schema_version / wrong_environment / stale`. Returns payload plus `age_seconds`. | V |
| `PRODUCTION_GATE_MAX_AGE` (6 h) | `INV:302` | The only trust window for any gate or match. Collector refreshes hourly (comment, `INV:302`). | V |
| `_clone_info()` | `DASH:6212-6222` | Display read: loads any age; returns `{captured_at, age_seconds, trusted (age <= 6 h), label}` or `{reason}`. | V |
| `_clone_line(info)` | `DASH:6230-6236` | Strip text: "captured ... data age N h M min", "older than 6 h: not trusted", or "unusable: <reason>". | V |
| `production_duplicate_gate` | `INV:305-328` | Start gate for runner: results `duplicate_production_clone_match`, `production_clone_unavailable`, `production_clone_incomplete` (no alternate-domain data), `production_clone_no_match` (never a clearance). Called by `PW:7859 _production_gate`. | V |
| `is_live_paid_tenant` | `INV:337-343` | not deleted, `enabled is True`, licence type in prepaid annual/monthly or PAYG monthly. | V |
| `render_inventory(env="prod-clone")` | `DASH:5933-6087`, route `/prod/tenants` `DASH:7566-7573` | Production Tenants table (name, id, licence, domain + alternate count, quota, scan, interval, SpyCloud, state), banner with capture time, age, tenant counts, schema drift. No CO links on prod. | V |

### 1.2 Per-CO production match

| Item | Where | Notes | Verified |
|---|---|---|---|
| `production_match_for(ref, product, type)` | `PW:8019-8038` | Loads clone (6 h rule), then CO expectation, then pure match. Any failure -> `clone_unavailable` with a reason. Sets `result["route"]`. | V |
| `_production_expectation` | `PW:7971-8007` | Desktop: live read-only Salesforce reads (names, domains, plan: licence type/start/end). VM: delegates to the snapshot (below). Dates come from the DealHub term, never Dev run day. | V |
| `_snapshot_production_expectation` | `PW:~7943-7968` | VM: `production_inputs.json` of the signed snapshot (`ON/vm_snapshot.py:59`); entry must match the page's product and type; fails closed `production_inputs_unavailable`. | V |
| `production_review_inputs` | `PW:8010-8016` | `{engine, co_source{tenant_names, domains}, plan}`; raises on any problem. | V |
| `production_match.production_match(co_source, plan, snapshot)` | `PM:168-202` | Pure. Statuses `clone_unavailable / not_in_production / exists_other / ambiguous / exists_matches / exists_differs` (`PM:29`). Result keys: `status, reason, fields[], differs[], candidates[], target, captured_at, age_seconds, tenants_checked` (+ `route`). Required fields `PM:30`. | V |
| `production_match_display` / `_match_of` | `DASH:6156-6171` | Cached display read; any exception -> `_unreadable_match()` (`clone_unavailable`, reason `co_source_unreadable`). | V |
| `MATCH_CHIPS`, `MATCH_FILTERS`, `FIELD_RESULT_CHIPS`, `BEFORE_MIGRATION` | `DASH:6094-6118` | Labels and filters. | V |
| `_prod_scope(all_open)` | `DASH:6252-6263` | Default scope = Dev-onboarded COs only; `all_open` = whole open queue (queue read failure -> note and Dev-only list). | V |

### 1.3 Pages

| Page / route | Function | Data it uses | Verified |
|---|---|---|---|
| `/prod` Readiness | `render_prod_readiness` `DASH:6282-6321` | `_prod_scope`, `_match_of`, `_clone_info`; columns CO, Account, Route, Dev status, Production match, Differing fields, Action needed. | V |
| `/prod/matches` | `render_prod_matches` `DASH:6509-6549` | Same plus recorded Start-gate checks `load_check_state()` kind `production_duplicate` (`DASH:6526`). Reasons unreadable -> note. | V |
| `/co/CO-x?env=prod` | `page_detail_production` `DASH:6765-6788`, route `DASH:7613-7619` | `detail_row`, `_match_of`, `_field_table`, `_candidates_table`, `_production_marker_for`, `_clone_line`, `BEFORE_MIGRATION`. | V |
| `/onboard` | `render_onboard_queue` `DASH:6488-6506` | `queue_rows()` only; **no** production check, no clone age; only "Automated route" yes/no and link. | V |
| `/onboard/CO-x` | `build_review_summary` `DASH:6352-6401` + `page_onboard_review` `DASH:6424-6485` | Preflight (`evaluate_ce_only_fill_preflight` `DASH:1966` / `evaluate_surface_fill_preflight`), `production_review_inputs`, `_match_of`, `review_approval` state. Blockers `REVIEW_BLOCKER_TEXT` `DASH:6327-6338`. Hash covers `summary`, not `extras` (clone time/age). | V |
| `/prod/tenants` | `render_inventory` (above) | Clone payload. | V |
| `/inventory?env=prod-clone`, `/tenants` | redirect to `/prod/tenants` `DASH:7549-7551` | | V |
| `_clone_precheck_section` | `DASH:5835-5859` (used on the Dev duplicate pre-check page `DASH:5888`) | Clone gate answer for one CO (Dev page, secondary). | V |

### 1.4 VM snapshot mode (read-only copy)

| Item | Where | Verified |
|---|---|---|
| `vm_mode()` | `DASH:6867` (env `SURFACE_ONBOARDING_RUNTIME=vm`) | V |
| Queue and CO facts | `queue_source_rows` `DASH:2159-2161` -> `snapshot_queue_rows` `DASH:6908`; `detail_row` `DASH:2410-2413` -> `snapshot_detail_row` `DASH:6923`. Fields fixed: `QUEUE_FIELDS` `DASH:115`, `DETAIL_FIELDS` `DASH:116`. A CO not published -> `ReadUnavailable` -> `page_vm_unavailable` (HTTP 503) `DASH:7625-7627`. | V |
| `production_inputs.json` | produced by `tools/publish_vm_snapshot.py:129-143` (per queued CO: engine, co_source, plan, product, type; failures just have no entry), merged by `tools/vm_refresh_snapshot.py:217-223` | V |
| Clone on VM | VM's own collector (`<state>/clone-inventory`) when present, else `inventory_prod_clone_*` from the snapshot (`vm_snapshot.py:64-67`, `state_paths.py:78-95`) | V |
| `snapshot_banner()` | `DASH:6963-6985`: "Data as of ... UTC", amber after `snapshot_stale_hours()` (default 6 h, `DASH:6935-6940`), plus refresh-status line (`refresh_status` `DASH:6947`) | V |
| `vm_strip_actions` | `DASH:6994-7002`: removes every POST form, GET refresh forms and the empty More menu | V |
| POSTs | `VM_OPERATOR_POSTS = frozenset()` `DASH:6864`; every POST refused on the VM for every role `DASH:7711-7725` | V |
| Local state on VM | `state_paths.state_file()` returns `snapshots/current/<name>` `ON/state_paths.py:62-71` | V |
| Which state files travel | `STATE_FILE_NAMES` `ON/vm_snapshot.py:72-78` (readbacks, runner state/log/progress, check state, scan, spycloud, id writebacks, validation, renewal mirrors/outcomes). **Not** included: `onboarding_review_approvals.json`, `production_create_approvals.json`, `production-create-records/`. | V |

### 1.5 New guarded production path (public API only)

| Module | Public API (V) | Read-only? | Used by dashboard today |
|---|---|---|---|
| `ON/production_approval.py` | `STATE_FILE_NAME="production_create_approvals.json"` (:27), `ALLOWED_ROUTE` = CE-only new (:28), `SCOPE` (:29), `DEFAULT_LIFETIME` 7 d (:30), `issue(...)` (:119), `check(path, approval_ref, co, route, source_revision, now) -> reason or None` (:180), `consume(...)` (:208), `unlock_environ(record)` (:236). Reason codes seen: `approval_missing, approval_consumed, approval_expired, approval_co_mismatch, approval_route_mismatch, approval_revision_drift, approval_unreadable, approval_locked`. **No list/describe function** (the file loader `_load` :91 is private). | `check` read-only; `issue/consume` write | No |
| `ON/production_create_record.py` | `RECORDS_SUBDIR="production-create-records"` (:23), `OUTCOMES` (:29) = `dry_run_passed, refused, created_unverified, readback_verified, uncertain_submit, attempt_started`, `record(...)` (:164, writes), `load(path)` (:213), `list_records(state_dir, co=None)` (:222; a corrupt record raises), `editable_parameters(rec)` (:235). Stored fields: `co_reference, outcome, recorded_at, redactions, form_fields_checked` + `route, approval_ref, refusal_reason, tenant_name, account_id, account_uuid, plan_summary, salesforce_revision, domains, response_shape` (:33-36). Redacts secret-like strings (:41-43, :98-122). | `load`/`list_records` read-only | No |
| `ON/production_create.py` | `CreateInputs` (:41), `evaluate_gate(inputs) -> first refusal reason or None` (:102, pure), `run(...)` (:223, orchestrator with injected callables), `CLONE_MAX_AGE` 6 h (:27). Gate order: production lock -> approval -> route -> single valid SF email domain -> source consistent -> prior UUID empty -> clone fresh and matching -> no duplicate -> at most one live paid tenant -> timing window or CSM request -> sessions ready -> schema ok. Refusal codes include `production_locked:<reason>`, `owner_go_missing`, `form_verification_failed`, `uncertain_submit`. | `evaluate_gate` pure and safe to call for display (A: only with inputs gathered read-only) | No |
| `ON/salesforce_uuid_writeback.py` | `plan(environment, route, co_row, readback_uuid, readback_verified, rev_now, rev_at_approval, explicit_go) -> WritebackPlan(status, reason, uuid)` (:48); status `ready_to_write / already_present / blocked / conflict`; `update_argv` (:80, builds an `sf` argv only; never executed here); `verify_after_write` (:99). | pure | No |
| `ON/run_target.py` | `production_target(environ, now)` raises `ProductionLocked(reason)` unless `production_unlock_problem` is None (:37-44). | pure | No |
| `ON/session_readiness.py` | `PRODUCTION_RUNNER_SUPPORTED = False` (:64); `production_unlock_problem` (:67): `production_sessions_flag_missing`, `production_approval_missing`, `production_approval_expired`, `production_runner_not_supported` (:87). | pure | Reason text only `DASH:530-531` |

Reading note (V): `production_create.CreateInputs` needs `clone_collected_at`, `clone_matches`, `duplicate_found`, `live_paid_tenant_count`, `timing_allowed`, `sessions_ready`, `schema_ok`, `sf_email_domains`, `sf_source_consistent`, `prior_account_uuid`. The dashboard already computes the equivalents for most (match status, candidates, `Email_Domains__c`, `Account_UUID__c`, `onboarding_allowed_now` `PW:681`, `ONBOARD_DAYS_BEFORE_START=2` `PW:674`); `sessions_ready` and `schema_ok` (D4 probe) have **no read-only source** yet.

## 2. Field table

Availability: **Desktop**, **VM** (read-only signed snapshot, no live Salesforce/Redash/browser), **Both**. Staleness: how old the value can be. "Fail-closed wording" is the exact text style to show when the value is unavailable; it must never read as OK/clear/none.

Common rule (V basis `PM:1-17`, `DASH:6165`): an absent or unreadable value is shown as "Not available: <reason code>" with a warn chip, never blank, never "Clear", never "None". "No match" is always followed by "not a clearance".

### 2.1 Production queue (landing), one row per open CO

| Field | Source function / file | Avail. | Staleness | Unavailable wording |
|---|---|---|---|---|
| CO ref, Account, Product, Type | `queue_source_rows()` `DASH:2159` (desktop live list view, display-cached; VM `queue.json`) | Both | Desktop: seconds to minutes (display cache). VM: snapshot age | Page: "The open queue could not be read from Salesforce" (existing note `DASH:6259`); VM: banner "No data published yet" `DASH:6968` |
| Start date (and "2-day window open/closed") | `queue_rows`/subscription summaries `DASH:2105-2131` (desktop: DealHub read; VM: `_snapshot_subscription_summaries` from published comments); window rule `onboarding_allowed_now` `PW:681` | Both (A: window needs "today" at render time; CSM-immediate flag source is not in the dashboard, see gap G6) | As the queue | "Start date: Not verified" (existing literal `DASH:2113`) and window "Not determined" |
| Route chip (CE-only, Surface, Case 3, renewals, none) | `route_for(row)` + `ENGINE_LABELS` `DASH:6129-6130`, `_route_label` `DASH:6243` | Both | As the queue | "No automated route" (exists `DASH:6499`) |
| Production check chip (Clear / Duplicate / Live paid x N / Clone stale / unavailable) | `_match_of` -> `production_match_for` `PW:8019`; chip from `MATCH_CHIPS` `DASH:6094` | Both (VM: needs a `production_inputs.json` entry for that CO) | Bound by the clone, at most 6 h for a usable answer (`PM:35`); queue-read part per row above | Chip "Clone unavailable" (`MATCH_CHIPS`) plus reason code (e.g. `inventory_snapshot_stale`, `co_source_unreadable`, `production_inputs_unavailable`). Never "Clear". |
| Live paid tenant count | `len([c for c in match["candidates"] if c["live_paid"]])` from `PM:_summary` (`PM:98-103`); gate semantic "at most 1 exact name" is `production_create.py:102` | Both | Same as match | "Live paid: not available" |
| Clone age strip ("Clone 2 h 10 min old, limit 6 h") | `_clone_info()` `DASH:6212` + `_clone_line` `DASH:6230` | Both | Live at render; age counts from collector capture time | "Production clone unusable: <reason>. Refresh with <collect hint>" (V text `DASH:6232`); VM hint is `VM_COLLECT_HINT` `DASH:5193` |
| Summary approval chip (None / Valid until / Expired / Used / Changed) | `review_approval.load` + `problem` via `_approval_state` `DASH:6404`; needs a summary hash per CO, which means running `build_review_summary` for every row (heavy) | Desktop only (VM: file not published, gap G3) | Real time (local file) | Desktop: "Approval record unreadable: nothing is treated as approved" (`DASH:6470`). VM: "Not published to this copy" |
| Create approval chip (D1) | **Gap G1** (`production_approval` has no list API) | Desktop only until G3 | Real time | "Approval record unreadable (approval_unreadable): nothing is treated as approved"; VM "Not published to this copy" |
| Create state chip (Locked / Dry run ready / Dry run passed / Created / Verified / Uncertain) | Lock: `session_readiness.PRODUCTION_RUNNER_SUPPORTED` `session_readiness.py:64`. Outcome: **Gap G2** (latest record per CO from `production_create_record.list_records`/`load`) | Lock: Both (static code constant). Outcome: Desktop only until G3 | Records are written at run time; a record is append-only | Lock chip text while False: "Locked: production runner not supported". Outcome unreadable: "Create records unreadable: do not retry" |
| Next step | Derived (pure) from the above; **Gap G7** `next_step()` | Both | Derived | "Resolve: <first blocker>" (owner decision named) |

### 2.2 Production CO detail page

| Card / field | Source function / file | Avail. | Staleness | Unavailable wording |
|---|---|---|---|---|
| Header: CO, account, route | `detail_row` `DASH:2410` (VM `snapshot_detail_row` `DASH:6923`), `route_for` | Both | Desktop live (display-cached); VM snapshot age | `ReadUnavailable` -> `page_salesforce_unavailable` (desktop) / `page_vm_unavailable` 503 (VM) (`DASH:7625-7629`) |
| Lock banner | `PRODUCTION_RUNNER_SUPPORTED` + `production_unlock_problem` reason text (`DASH:530-531` pattern) | Both | Static | Banner always shown while False: "Production create is locked. Review and dry run only." |
| Salesforce facts: account, product, type, source revision | `build_review_summary` -> `summary` `DASH:6352-6401`; revision = `evaluation.source_revision` (CE: from `evaluate_ce_only_fill_preflight` `DASH:1966`), token logic `_source_revision_token` `DASH:1723` (sha256 of ref + `LastModifiedDate`) | Desktop. VM: **A** only if the preflight can run from the snapshot's `co_details.json` (not verified; `detail_row` is snapshot-backed on the VM but `evaluate_*_fill_preflight` was not traced) | Desktop: as read (display-cached) | "Source revision could not be determined" (`REVIEW_BLOCKER_TEXT["source_revision_missing"]` `DASH:6330`) |
| Tenant name, domains, plan ("what would be sent") | `production_review_inputs` `PW:8010` via `build_review_summary` -> `summary["values"]` `DASH:6386-6387` (VM: `production_inputs.json`) | Both | Desktop live; VM snapshot age | "The values to be sent could not be read from Salesforce" (`DASH:6329`) |
| Production checks: clone captured/age vs 6 h | `extras` from `build_review_summary` `DASH:6394` (`match["captured_at"]`, `["age_seconds"]`) and `_clone_info` | Both | Per CO: only populated when clone <= 6 h (V, see 0.3) | "Clone age: unknown" + blocker `production_clone_age_unknown` (`DASH:6331`). Use `_clone_info` for the stale case. |
| Duplicate by name / by domain | `PM:186-188` (candidates: same `_name_key`, or primary/alternate domain via `duplicate_precheck`); per-candidate reason is not in the match result (only `_summary` fields) | Both | Same | "Duplicate check unavailable (<reason>). Treated as blocked." |
| Candidate tenants (name, id, domain, licence, created, deleted/disabled/live-paid) | `match["candidates"]` -> `_candidates_table` `DASH:6746-6762` | Both | Same | "No candidates listed" never shown when status is unavailable; show the reason instead |
| Required field comparison (name, primary, alternates, licence type, licence end; info: SpyCloud, start, operator, scan, SF account id "not checked") | `match["fields"]` -> `_field_table` `DASH:6735-6743` (`PM:106-160`) | Both, only for `exists_matches/differs` | Same | Not rendered for other statuses; "Unknown" chip for missing data (`FIELD_RESULT_CHIPS`) |
| Timing window (start minus 2 days or CSM request) | `onboarding_allowed_now` `PW:681`, `ONBOARD_DAYS_BEFORE_START` `PW:674` | Both for the date rule; CSM request: **gap G6** | Date: computed at render | "Timing: not determined (start date not verified)" |
| One valid email domain; Account UUID empty | `Email_Domains__c`, `Account_UUID__c` in `DETAIL_FIELDS` `DASH:116`; CE-only email-domain rule `evaluate_ce_only_fill_preflight` `DASH:1966` (exactly one valid `Email_Domains__c`) | Both (VM: snapshot value) | As the CO read | "Not verified: <blocker code>" |
| Summary approval (docs/43, 4 h) | `_approval_state` `DASH:6404`; text blocks `DASH:6460-6472` | Desktop only (VM gap G3) | Real time | Existing texts; VM: "Approval records are not published to this copy" |
| Create approval (D1: one CO, route, revision, issuer, expiry, consumed, ref) | **Gap G1** `describe_approval()` | Desktop only until G3 | Real time | "Approval record unreadable: nothing is treated as approved" |
| Gate result "why create would be refused" | `production_create.evaluate_gate` (pure) fed by **Gap G4** `build_create_inputs_readonly()` | Desktop (A for VM) | Computed at render | First refusal code mapped to text; any exception -> "Gate could not be evaluated: refused" |
| Create steps (dry run / owner go / submit / readback) and outcome | **Gap G2** `latest_create_record(co)` over `production_create_record` | Desktop only until G3 | Real time | "Create records unreadable" |
| D2 record (tenant name, account id, UUID, domains, plan summary, editable parameters) | `production_create_record.load` + `editable_parameters` `:213,:235` (already redacted at write) | Desktop only until G3 | Real time | "No create record" only when the folder was read and is empty; unreadable -> "Create records unreadable (record_unreadable)" |
| UUID writeback card | `salesforce_uuid_writeback.plan` `:48` for display of status/reason; execution is out of scope | Desktop | Real time | "Locked until a verified create" while no `readback_verified` record |

## 3. Gaps and smallest implementation

Each is new code; none writes to an external system. Names are proposals. All are read-only and wrap errors into a reason string (fail closed).

| ID | Gap | Smallest implementation | Inputs -> outputs |
|---|---|---|---|
| G1 | List / describe D1 approvals (none exists; `_load` private) | `ON/production_approval.py`: `describe(path, co_reference, now) -> dict` (public wrapper over `_load` and `_problem`). Returns `{"state": "none"/"valid"/"expired"/"consumed"/"unreadable", "approval_ref", "issued_by", "expires_at", "consumed_at", "route", "scope"}` for the newest record of that CO; plus `list_all(path, now) -> list[dict]` (same shape, all COs). No source revision or secrets. | `(path, co, now)` -> dict; unreadable -> `{"state":"unreadable","reason":"approval_unreadable"}` |
| G2 | List D2 records and latest create outcome per CO | `DASH` helper `latest_create_records() -> dict[co, {outcome, recorded_at, refusal_reason, tenant_name, account_id, account_uuid, editable}]`, built on `production_create_record.list_records` + `load` + `editable_parameters`; catches `ProductionCreateRecordError` and returns `{"error":"record_unreadable"}`. Resolve the folder through a new `state_paths.production_records_dir(default)` (desktop has no state dir today, `state_dir()` is None, `state_paths.py:26-39`, so a desktop default folder must be chosen: A). | `()` -> `{co: summary}` or error marker |
| G3 | VM cannot see approvals/records (not in `STATE_FILE_NAMES`, flat names only) | Publish one aggregated, already-redacted file `production_create_state.json` containing `{"approvals": G1 list, "records": G2 summaries}` through `tools/publish_vm_snapshot.py` and add it to `EXPORT_FILE_NAMES` (`vm_snapshot.py:79`); dashboard reads it with `_snapshot_json`. Until then the VM shows "Not published to this copy". | desktop state -> one JSON member |
| G4 | Read-only `CreateInputs`/gate display | `DASH` (or new `ON/production_gate_view.py`): `create_gate_view(reference, now) -> {"first_refusal": code|None, "steps": [{code,label,state}]}` calling `production_create.evaluate_gate` on inputs from `build_review_summary` extras plus `onboarding_allowed_now`. `sessions_ready` and `schema_ok` have no read-only source: pass `False`, so the gate **always** refuses with `sessions_not_ready`/`schema_unexpected` (honest) until the D4 probe exists. Must not call `production_target` with a patched flag. | `(co, now)` -> view model |
| G5 | Clone age on every page and per row, including a stale clone | `DASH`: `clone_status() -> {"state": "fresh"/"stale"/"unusable", "captured_at", "age_seconds", "limit_seconds": 21600, "reason"}` wrapping `_clone_info` (`DASH:6212`); render one strip via `_app_shell` for every production page. Per-CO rows keep using `match` but fall back to `clone_status()` for age. | `()` -> dict |
| G6 | CSM-request flag (timing exception) | Source not found in the dashboard (V by grep: `csm_immediate` exists only as an argument of `onboarding_allowed_now`, `PW:682`). Smallest: treat as `False` and show "CSM request: not recorded" until the owner names a source; do **not** invent a Salesforce field. | n/a |
| G7 | Row "Next step" and a single production state | Pure function `production_row_state(match, approval, create_record, lock) -> {"chips": [...], "next_step": str, "owner_decision": str|None}` using `REVIEW_BLOCKER_TEXT` codes; table-driven, unit-testable. | dicts -> dict |
| G8 | Queue rows with per-row production state without N Salesforce reads | `/onboard`'s `render_onboard_queue` (`DASH:6488`) only reads `queue_rows()`. Reuse `prefetch_display_read(production_match_display, ...)` as `_prod_results` does (`DASH:6266-6271`) for the match; do **not** call `build_review_summary` per row (it runs the full preflight). The summary-approval chip needs the hash, so for the queue show "Review to see" and compute the summary-approval state only on the detail page, or store the hash with the approval and compare the stored `source_revision` (cheap, A). | list -> rows |
| G9 | Scope default | Add parameter to `_prod_scope` (or a new `_prod_scope_all()`): default all open COs; keep `?dev=1` for the Dev-onboarded subset (V: `DASH:6262`). | |
| G10 | Review page stale blocker is effectively dead | V: `build_review_summary` adds `production_clone_stale` only if the match status is `not_in_production` and age > 6 h (`DASH:6397-6400`), but a clone older than 6 h already yields `clone_unavailable` (`PM:35`). Harmless, but the queue must treat `inventory_snapshot_stale` as the stale case and show the clone age from `clone_status()` (G5). | |
| G11 | Per-candidate "why matched" (name vs domain) | `PM._summary` does not carry reasons (`PM:98-103`), while `leonardo_inventory.duplicate_precheck` does (`PRECHECK_REASON_TEXT` `DASH:5803`). Optional: add `reasons` to the candidate summary. Not required to ship. | |

## 4. Test plan

### 4.1 Existing tests that pin the production pages (must be re-read and updated with the build)

`integration/tests/test_dev_prod_areas.py` (V names):
- `NavigationTests`: `test_groups_labels_and_order`, `test_each_page_has_exactly_one_active_link_and_the_right_environment`, `test_production_uses_the_warn_tokens_and_red_is_never_an_environment_colour`, `test_the_pill_stays_visible_on_mobile`, `test_real_pages_carry_their_environment`, `test_co_page_dev_and_production_views_have_their_pill_bar_and_segmented_control`.
- `RedirectAndRouteTests`: `test_old_tenants_url_is_a_303_to_production_tenants_keeping_filters`, `test_production_pages_render_and_the_clone_tenants_page_moved`, `test_production_routes_accept_get_only`, `test_co_page_env_parameter`, `test_dev_pages_and_history_alias_render`.
- `OnboardedOnDevPageTests` (Dev page, links to the prod view): `test_evidence_chips_and_production_chip_link_to_the_production_view`.
- `ProductionReadinessTests`: `test_default_scope_is_only_cos_onboarded_on_dev_needs_action_first` (**will break**: scope default changes, G9), `test_all_toggle_lists_every_open_co`, `test_columns_chips_actions_and_differing_fields`, `test_the_summary_strip_counts_act_as_filters`, `test_clone_unavailable_count_shows_the_clone_age`, `test_no_forms_no_scripts_no_refresh_button`, `test_a_failed_source_read_shows_as_unavailable_never_as_clear`, `test_matches_page_is_current_only_sorted_by_status_with_the_recorded_gate_checks`, `test_matches_page_has_no_persistence`.
- `ProductionCoViewTests`: `test_it_has_no_form_and_no_button_and_no_script`, `test_it_shows_the_status_the_comparison_and_the_matched_tenants`, `test_it_has_the_before_migration_checklist_as_text`, `test_unavailable_and_blocked_states_explain_themselves`, `test_the_recorded_start_gate_marker_is_reused`, `test_dev_chip_in_the_segment_uses_local_evidence_and_the_prod_chip_the_match`, `test_dev_view_peeks_only_and_never_reads_production`.
- `QueueSplitTests`: `test_cos_onboarded_on_dev_leave_the_queue_and_a_link_counts_them`, `test_a_dev_mirror_or_unverified_run_stays_in_the_queue`, `test_an_unreadable_evidence_store_never_hides_a_co` (Dev queue; changes if `/` stops being the Dev queue).
- `SafetyTests`: `test_no_production_page_has_a_post_form_script_or_write_route` (pages list must include the new ones; note it forbids any POST form on production pages, which conflicts with a future "Approve this summary" form on `/onboard/CO-x`, since that page is not in its list: keep it that way or update deliberately), `test_new_code_never_touches_a_production_session_or_writes` (scans the source slice between `# ---- Two areas:` and `# Dashboard login`; moving code out of that slice silently removes protection), `test_dev_and_production_rows_never_share_a_table`, `test_the_match_module_persists_nothing_and_reads_no_secret`.

`integration/tests/test_onboard_review.py`: `SummaryTests` (`test_summary_carries_what_will_be_sent_and_never_consults_leonardo`, `test_clone_age_and_capture_time_do_not_change_the_hash`, `test_every_uncertainty_is_a_blocker`, `test_unreadable_values_block`, `test_salesforce_read_failure_propagates`), `PageTests` (`test_queue_page_lists_cos_and_links_supported_routes_only` **breaks** with new columns, `test_review_page_shows_data_production_check_and_an_enabled_approve_button`, `test_blocked_page_disables_approval_and_lists_the_reason`, `test_unreadable_approval_file_is_never_an_approval`), `ApproveTests` (`test_approve_records_the_summary_bound_approval_and_starts_nothing`, `test_a_stale_page_cannot_approve_a_changed_co`, `test_a_changed_revision_cannot_be_approved_from_an_old_page`, `test_blocked_summaries_cannot_be_approved_even_with_a_forged_post`, `test_fixing_the_co_invalidates_the_old_approval_and_a_new_one_is_needed`, `test_an_expired_approval_does_not_apply`, `test_salesforce_read_failure_records_nothing`), `RouteWiringTests` (`test_route_is_a_post_route_and_refused_on_the_vm_for_everyone`, `test_the_review_code_never_reaches_leonardo_or_a_browser`).

`integration/tests/test_production_match.py`: `StateMachineTests` (all, incl. `test_clone_unavailable_never_clears`, `test_no_candidate_is_not_in_production_and_says_it_is_not_a_clearance`, `test_only_trial_pov_disabled_or_deleted_tenants_is_exists_other_and_blocks`, `test_two_live_paid_tenants_with_the_exact_name_is_ambiguous`, `test_unknown_required_data_is_never_a_match`), `SnapshotRuleTests` (`test_missing_stale_and_tampered_clones_fail_closed`, `test_a_real_snapshot_runs_through_the_state_machine`), `RunnerGlueTests` (`test_an_unusable_clone_needs_no_salesforce_read`, `test_an_unreadable_co_source_is_clone_unavailable_never_a_clearance`, `test_the_expectation_comes_from_the_co_and_passes_to_the_match`, `test_production_dates_never_use_the_dev_entered_or_run_day_dates`, `test_surface_only_does_not_show_spycloud_and_compares_main_and_alternate_roots`, `test_renewals_expect_the_dealhub_term_end_exactly_both_names_and_show_spycloud`).

`integration/tests/test_vm_snapshot_dashboard.py`: `VmPagesFromSnapshotTests` (`test_every_page_renders_with_the_banner_and_without_calling_out`, `test_no_action_forms_or_buttons_on_any_page`, `test_no_live_read_wording_refresh_buttons_or_sessions_link`, `test_server_side_refusals_remain`, `test_a_co_that_was_not_published_is_a_clear_unavailable_page`), `StaleBannerTests`, `EmptyStateTests`, `DesktopUnchangedTests`, `VmCloneWordingTests` (`test_unusable_clone_line_and_action_name_the_vm_collector`, `test_the_prod_tenants_page_has_no_windows_command_when_the_clone_is_missing`), `VmProductionMatchTests` (`test_the_expectation_comes_from_the_published_inputs`, `test_a_missing_different_or_malformed_entry_fails_closed`, `test_without_a_published_file_the_expectation_is_unavailable`, `test_the_match_uses_the_clone_and_the_published_inputs_without_a_salesforce_read`, `test_the_page_no_longer_says_the_co_source_is_unreadable`).

`integration/tests/test_redash_inventory.py` (clone and gate): `test_an_unusable_clone_blocks_instead_of_waving_the_onboarding_through`, `test_a_clone_without_alternate_domains_is_incomplete_and_blocks`, `test_missing_clone_snapshot_says_how_it_is_collected`, `test_clone_page_shows_the_data_age_without_co_links_or_a_refresh_button`, `test_precheck_page_shows_the_production_gate_without_touching_the_dev_answer`, `test_sidebar_has_a_dev_tenants_page_and_a_production_tenants_page`, `test_tenants_tab_has_the_gate_note_and_no_manual_check_or_refresh_while_devops_keeps_refresh`, `test_the_old_clone_url_redirects_keeping_the_filters`, `test_snapshot_is_labelled_prod_clone_and_round_trips`, `test_environments_keep_backoffice_production_blocked`.

`integration/tests/test_dashboard_search.py`: `test_form_is_on_the_queue_the_history_and_the_prod_pages`, `test_form_is_in_the_shell_for_every_page_family`, `test_no_post_route_or_form_was_added` (search form must stay on every shell).

`integration/tests/test_vm_collectors.py`: `test_on_the_vm_the_own_collected_clone_wins_once_it_exists`, `test_on_the_vm_the_clone_is_read_from_the_current_snapshot` (clone location logic; G3 changes the publisher and must keep these green).

New-module suites (guards, keep green): `test_production_approval.py` (`test_state_file_name_is_separate_from_review_approvals`, `test_unlock_environ_feeds_session_readiness`, `ProductionApprovalTests`), `test_production_create_record.py` (`test_no_secret_lands_on_disk`, redaction tests, `test_list_fails_closed_on_corrupt_record`), `test_production_create.py` (`PRODUCTION_RUNNER_SUPPORTED` stays False, scans for forbidden imports/`PRODUCTION_RUNNER_SUPPORTED\s*=`, line ~506-511), `test_production_origin_and_target.py` (`test_runner_support_flag_stays_false`, `test_valid_environ_succeeds_only_with_runner_support_patched`), `test_salesforce_uuid_writeback.py`.

Unverified (A): `test_co_page_layout.py`, `test_dashboard_stage_tracker.py`, `test_attended_open_onboardings_dashboard.py` pin the Dev CO and queue markup per docs/46; I did not open them.

### 4.2 New tests needed

Put these in a new `integration/tests/test_production_dashboard_data.py` (data layer, G1-G9) and `test_production_dashboard_pages.py` (pages). Use the existing `areas()`/fixtures pattern from `test_dev_prod_areas.py`.

Locked-state and no-create-button (hard requirements):
1. `test_no_production_page_has_a_create_button_while_runner_unsupported`: with `session_readiness.PRODUCTION_RUNNER_SUPPORTED` False (assert it first), render every production page for every state fixture (clone fresh/stale/missing; approval none/valid/expired/consumed/unreadable; record outcomes `dry_run_passed`, `readback_verified`, `uncertain_submit`, none) and assert the HTML has no `<button`/`<input type='submit'` whose label or form action matches `create|submit|confirm|go`, and no form with `action` under `/attended/` that starts a create. Absent, not `disabled` (docs/46 line 33). Also assert that a valid approval and a `readback_verified` record still produce no create button.
2. `test_create_button_is_never_rendered_when_the_flag_is_patched_true_without_a_route`: patch the flag True; page must still show no create control because no create route exists (guard against a page that reads the flag only). Expected to be removed or rewritten when the runner is deliberately supported.
3. `test_there_is_no_post_route_for_production_create`: `Handler.do_POST` path table contains no route matching `create|production`, except the existing `/attended/review-approve`; on the VM `VM_OPERATOR_POSTS` stays empty (extends `test_route_is_a_post_route_and_refused_on_the_vm_for_everyone`).
4. `test_lock_banner_text_present_on_every_production_page_while_locked`.

No secrets / no raw payloads:
5. `test_no_production_page_shows_secrets_or_raw_payloads`: seed approvals, records and snapshots with marker strings (`Bearer abc...`, a JWT-shaped string, `password=hunter2`, a 60-char blob, a cookie header, an OPA activation command, `SURFACE_ONBOARDING_PROXY_SECRET`, `response_shape` with a raw JSON body) and assert none appears in any production page, in `/prod/tenants`, nor in the VM variant. Reuse the redaction regexes from `production_create_record` (`_KEYWORD_RE`, `_JWT_RE`, `_BLOB_RE`, `:41-43`) as the oracle on the whole HTML.
6. `test_record_card_shows_only_allowlisted_fields`: the D2 card renders only keys in `production_create_record._ALLOWED` (`:36`); a record file containing an unknown key (hand-edited) makes the card show "Create records unreadable" (since `load` raises, `:187-212`), never the raw JSON.
7. `test_page_never_reveals_proxy_secret_cookies_or_tokens_in_headers_or_body` (VM mode request through `Handler`).
8. `test_data_layer_does_not_import_forbidden_modules`: new helper code must not import `subprocess`, `webbrowser`, `playwright`, call `sf_write_json`, `consume`, `issue`, `run`, `record`, `update_argv` (extend the `test_new_code_never_touches...` pattern, and extend its source slice so the new code is inside it).

Data-contract tests:
9. G1 `describe`: none / valid / expired / consumed / co mismatch / corrupt file (`approval_unreadable`) / missing file; exact output keys; no `source_revision` leakage.
10. G2 `latest_create_records`: ordering by `recorded_at`, newest wins, corrupt record -> error marker not partial list, empty folder -> empty (not error), unreadable folder -> error.
11. G5 `clone_status`: fresh (<6 h), exactly 6 h (boundary: fresh, as `age <= PRODUCTION_GATE_MAX_AGE` `DASH:6221`), 6 h + 1 s (stale/untrusted), missing, tampered, wrong environment; every production page contains the strip with the age; stale clone shows age but queue rows show "Clone unavailable" with reason `inventory_snapshot_stale`.
12. G9 scope: default lists every open CO; `?dev=1` lists only Dev-onboarded; queue read failure shows the note and never an empty "clear" list.
13. G7 `production_row_state` table test: each combination of match status x approval state x record outcome x lock yields the expected chips and a next step; unknown input -> blocked with the owner decision named.
14. Fail-closed matrix: for each production page, patch each source to raise (`ReadUnavailable`, `InventoryError`, `ApprovalUnavailable`, `ProductionCreateRecordError`, `OSError`) and assert HTTP 200/503 with warn wording and no "Clear", "OK", "None", "Verified" or "Created" chip.
15. VM tests (extend `test_vm_snapshot_dashboard.py`): approvals/records not published -> "Not published to this copy" (not "Not approved yet"); with the G3 member present -> shown read-only; no POST form on any VM production page; no Windows command in clone hints (existing wording tests).
16. `test_summary_approval_and_create_approval_are_labelled_differently` (both states on one page).
17. `test_match_age_is_none_for_a_stale_clone_and_the_page_uses_clone_status` (pins the V finding 0.3).
18. `test_blocked_states_name_the_owner_decision` (each blocker code in `REVIEW_BLOCKER_TEXT` maps to a text; add owner decision text per docs/45 D1-D6).

Run (suggested, by the implementer; not run here): `python -m pytest integration/tests/test_dev_prod_areas.py integration/tests/test_onboard_review.py integration/tests/test_production_match.py integration/tests/test_vm_snapshot_dashboard.py integration/tests/test_redash_inventory.py integration/tests/test_dashboard_search.py integration/tests/test_production_*.py -q` (A: the repo may use `unittest`; the test files end with `unittest.main()`).

## 5. Verified vs assumed (summary)

Verified in code (with citations above): clone loading and the 6 h rule; the stale-clone loses its age on the per-CO result; `_prod_scope` default; all production page functions and their fields; review summary, hash scope and blockers; the two approval files and their different lifetimes; the new modules' public API and that the dashboard does not use them; VM snapshot allow-list excludes approvals and records; VM POSTs refused for all roles; `PRODUCTION_RUNNER_SUPPORTED = False` (`session_readiness.py:64`); `production_create.evaluate_gate` order.

Assumptions (not verified): whether the CE-only/Surface preflight (`evaluate_*_fill_preflight`) can run from VM snapshot data; where a desktop default folder for `production-create-records` and `production_create_approvals.json` should live (no state dir on the desktop, `state_paths.py:26-39`); the CSM-request source; which fields `production_create`'s final API will expose after the fix in progress; the contents of `test_co_page_layout.py`, `test_dashboard_stage_tracker.py`, `test_attended_open_onboardings_dashboard.py`; that no test was run for this document.

## 6. Risks

- **Misleading "None/Not approved" on the VM** when the approval files are simply not published (0.5, G3). Use "Not published to this copy".
- **Two approvals confused** by operators: the 4 h summary approval does not authorize a create; only the D1 record does (and even that leaves the runner locked).
- **Stale clone presented as fresh**: per-CO `age_seconds` is `None` past 6 h; always render the age from `clone_status()`.
- **Performance**: calling `build_review_summary` per queue row runs the full preflight for each CO (G8); use the match prefetch only.
- **A Create control by accident**: a future change that wires `production_create.run` into a route. Tests 1-3 pin absence; the existing `test_production_create.py` scan only guards the module.
- **Safety test blind spot**: `test_new_code_never_touches_a_production_session_or_writes` scans a source slice; code placed outside it is unprotected.
- **Records containing free text** (`plan_summary`, `tenant_name`) are redacted by pattern only (`production_create_record.py:55-122`); display them escaped and length-limited anyway.
- **Moving `/` to production** changes many Dev-page tests and the old Dev `/` route; keep redirects (docs/46 line 17).
