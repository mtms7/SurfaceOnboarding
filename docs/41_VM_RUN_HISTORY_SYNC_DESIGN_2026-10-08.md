# VM run-history and queue sync design (phase 1, proposal)

Date: 2026-10-08. Status: **proposal, nothing built.** Extends `docs/40_VM_TEAM_ACCESS_DEPLOYMENT_PLAN_2026-10-07.md` (phase 1: read-only VM, runs stay on the owner's Windows desktop).

## 1. Goal and principle

Team members open `https://<vm>:8443/` and see the same pages as the owner (queue, CO pages with stage tracker, Onboarded on Dev, History, Tenants, Production read-only), **as of the last publish**. Only the owner starts runs, on the desktop.

**One writer, one direction.** The desktop is the only writer of every state file and the only machine that talks to Salesforce, Leonardo and Redash. It publishes a masked snapshot; the VM only reads it. State is never split or merged, so there is nothing to reconcile.

**Consequence for Salesforce:** in this design the **VM needs no Salesforce connection at all** (no `sf` profile, no service identity, no token on the server). The owner's existing desktop login keeps working exactly as today. This removes the "Salesforce read scope for the team dashboard" approval from the VM rollout (docs/40 step 6) and keeps credentials off a shared server. A live Salesforce connection from the VM would be phase 2 and needs its own decision.

## 2. What is published

| Data | Source today | In the snapshot |
| --- | --- | --- |
| Per-CO state files | `integration/attended_*.json` (about 100 KB in total) | Copied as they are (they are already masked metadata: ids, outcomes, times; no credentials) |
| Run log and progress | `attended_ce_only_run_log.json`, `attended_ce_only_progress.json` | Copied (event step/outcome/time only) |
| Queue (open onboardings) | live Salesforce read (`queue_rows`) | One fixed-field list: CO, account, product, type, approval, stage, dates |
| CO detail | live Salesforce read, 5-minute display cache | The same 12 display fields per queued or onboarded CO |
| Leonardo inventory (Dev) and production clone | `%LOCALAPPDATA%\SurfaceOnboarding\...` | Latest masked snapshot files |

Never published: cookies, tokens, MFA values, the automation browser profile, `sf` auth files, the Redash key, raw payloads, anything outside the allow-list above. A publish refuses a file that is not on the explicit list.

## 3. Snapshot format and integrity

- One directory `snapshot-<UTC timestamp>/` with the files above plus `manifest.json`: file names, sizes, SHA-256 per file, schema version, created-at, producing host label.
- The manifest is signed with HMAC-SHA256 using a key that exists only on the desktop (DPAPI-protected) and on the VM (root-owned `0640` file, never in the repo). The VM rejects an unsigned, stale-schema, oversize or tampered snapshot and keeps serving the previous one.
- Atomic switch on the VM: unpack to `incoming/`, verify, then swap the `current` symlink. Keep the last 5 snapshots; the dashboard never reads a half-written one.

## 4. Transport (owner choice)

| Option | How | Notes |
| --- | --- | --- |
| A. Manual (start here) | `tools/publish_vm_snapshot.py` writes a zip; the owner copies it to the VM and runs `ingest` | No new access or approval; slowest; fine for the pilot |
| B. Push | the same zip sent with `scp`/`rsync` over SSH from the desktop, key limited to one inbound folder | Needs a restricted SSH key for the VM user (VM owner decision) |
| C. Pull | the VM fetches from an approved internal share | Needs a share and a VM egress rule |

All three use the same signed bundle, so the choice can change later without code changes.

## 5. What changes in the dashboard (VM mode only)

1. A **snapshot data source**: in VM mode `queue_rows`, CO detail, readbacks and the other `attended_*` reads come from `current/`; there is no `sf` call, no browser launch, no Leonardo or Redash call. Desktop mode is unchanged.
2. A visible **"Data as of <time> (published from the desktop)"** banner on every page, amber when older than a threshold (default 6 hours), so nobody mistakes it for live state.
3. VM pages hide or disable every Start, Confirm, Prepare sessions and check button (viewers still get refusals server-side, as built in step 0).
4. Tests: a snapshot with a bad signature, a changed file, an extra file, a symlink or traversal path, an oversize file, or an old schema is refused and the previous snapshot stays; VM mode makes no subprocess or network call; the banner shows; desktop mode unaffected.

## 6. Security properties

- No credential or token ever leaves the desktop or sits on the VM.
- Integrity: signed manifest, hash per file, strict allow-list, size caps, no path components, no links.
- Confidentiality: the snapshot holds customer names and domains already shown on the desktop dashboard. Access is the same allow-list as the dashboard (docs/40); transport B or C must be encrypted (SSH or HTTPS); the VM folder is `0750`, owner `surface-onboarding`.
- Availability: if publishing stops, the VM keeps the last good snapshot and shows its age.
- Audit: each publish and ingest is logged (time, snapshot id, file count; no content).

## 7. Build plan (local first, no approvals needed to start)

1. `integration/onboarding/vm_snapshot.py` (pure): manifest, allow-list, HMAC, verify, atomic ingest helpers.
2. `tools/publish_vm_snapshot.py` (desktop, read-only on local files; reads the queue and details through the dashboard's existing fixed reads) and `tools/ingest_vm_snapshot.py` (VM side, stdlib).
3. Dashboard VM-mode snapshot data source and banner.
4. Tests as in section 5; documentation in `integration/deployment/vm_pilot/README.md`.

Rough size: one focused agent session plus review.

## 8. Decisions needed from the owner

1. Transport A, B or C (A recommended for the pilot).
2. Staleness threshold (6 hours proposed) and whether the owner wants an automatic publish after each run ends.
3. Confirm that the customer names and domains on CO pages may be shown to the allow-listed viewers on the VM.
4. Confirm that the VM does not call Salesforce in phase 1.

## 9. Not covered here

Running onboardings from the VM (phase 2: visible browser, Leonardo SSO/MFA, Salesforce identity on the server) and the infrastructure approvals in docs/40 section 7 that do not depend on this design (port and firewall, OneLogin application, certificate).

## 10. Build note (2026-10-08, local code and tests only)

Owner decisions taken: transport A (manual zip), optional publish after each run (default off), customer names and domains visible to allow-listed viewers, no Salesforce on the VM in phase 1. Built locally and tested, nothing deployed: `integration/onboarding/vm_snapshot.py`, `tools/publish_vm_snapshot.py`, `tools/ingest_vm_snapshot.py`, the VM data-source switch in the dashboard, the interim tunnel nginx template and tests (`test_vm_snapshot*.py`). Usage and layout: `integration/deployment/vm_pilot/README.md`. Known gaps: the production clone (about 17 MB) exceeds the 5 MB per-file cap and is not published; DealHub rows, the monthly History chart and queue start dates are not in the snapshot, so those cards show as unavailable on the VM; the desktop key file is a plain file (no DPAPI wrapper yet).
