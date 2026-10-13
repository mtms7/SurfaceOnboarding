# VM-native onboarding server: design and approval requests (2026-10-13)

Status: **proposal, nothing built, nothing activated.** It records the owner direction of 2026-10-13 and what each part needs before it may run. It does not relax any gate: docs/36, docs/40 section 7 and the 2026-09-15 / 2026-10-08 boundaries stay in force until the approvals below are recorded in `integration/IMPLEMENTATION_PLAN.md`.

## 1. Owner direction (2026-10-13)

- The VM (`workato-opa-01`) is the **team onboarding server**: it must work when the owner is away. Nothing may depend on the owner's laptop, a manual zip, or a hand-opened tunnel.
- **The VM reads Salesforce itself** (reverses the 2026-10-08 "no Salesforce on the VM" decision; the owner chose this over desktop auto-publish).
- The Redash inventory collection also runs on the VM (design first; no Redash credential is placed on the VM before its approval).
- **Before any onboarding write the dashboard shows a summary of everything that will be sent**; an operator approves it; only then does the run proceed. If anything is wrong, the CO is corrected in Salesforce and the process starts again from a fresh summary.

## 2. Why CO-0780 is missing on the VM today

The VM shows the last published snapshot (2026-10-09 20:14 UTC; the amber banner says so). It has no Salesforce connection by design, so CO-0780 (created later) cannot appear. Section 3 removes the cause; until then a manual publish is the only way.

## 3. Components

| # | Component | Runs on the VM as | Needs |
| --- | --- | --- | --- |
| A | **Salesforce reader**: a systemd timer (default every 10 min) runs the existing fixed read-only queries (queue, CO detail fields) and writes the same snapshot layout into `<STATE_DIR>/snapshots/`; the dashboard code that reads snapshots is unchanged | `surface-onboarding`, no shell | Non-human Salesforce identity (docs/40 section 3: Connected App, OAuth JWT bearer, read-only permission set, key file root-owned 0640, never in the repo or unit); SecOps and Salesforce admin approval of the **wider fixed-query scope** (the approval packet covers one record today) |
| B | **Redash collector**: a timer runs the existing read-only `--collect` allow-list (production clone, users-per-tenant) and writes the inventory files the snapshot already carries | `surface-onboarding` | A read-only Redash API key in a root-owned 0640 file (replaces Windows DPAPI), outbound HTTPS VM to Redash named in the SecOps request. Owner rule stays: new queries only, never edit an existing one |
| C | **Browser runner** (Leonardo Dev, later production BackOffice) | a separate service user, not colocated with `workato-agent` (docs/34, docs/36) | Chromium and a display strategy; **MFA** (section 5); phase 2 approvals |
| D | **Review summary and approval gate** (section 4) | the dashboard | Local code and tests only; no external approval to build |
| E | **Identity**: OneLogin application, allow-list, operator role, firewall for 8443, certificate (docs/40 section 7) | nginx, oauth2-proxy | SecOps, Identity, owner. Removes the tunnel and the fixed interim identity |

Failure behaviour for A and B (fail closed, visible): a failed or partial read never replaces the last good snapshot; the banner shows the age of the last good one and goes amber after 6 h; refusal codes only, no payloads in logs. Overlapping runs are refused with a lock file. Salesforce write paths stay absent from the VM (the id writeback is disabled in VM mode and is not part of A).

## 4. Review summary and approval gate (applies to Dev now, to production later)

1. **Summary page** (read-only, built from the same source data the run would use): CO, account, route and case, tenant name, main and email domains, product and tier, subscription start and the timing window, the exact values for every form field the runner will fill (secrets never shown), duplicate and clone checks with their results, the Salesforce record revision (last modified), and what will be written afterwards (for example Salesforce id writeback: allowed or not).
2. **Approve** is a POST by a named operator (not a viewer). It records the operator, time, CO, route, a **hash of the summary** and the Salesforce revision, and expires (proposal: 4 hours). The run starts only while a matching, unexpired, unconsumed approval exists; the approval is consumed by the run.
3. **If anything is wrong**: nobody edits the run. The CO is corrected in Salesforce; the next read changes the revision and the hash, the old approval becomes invalid, a new summary is shown, and the operator approves again. Drift between approval and start (a different revision, hash, route or CO) stops the run with nothing created.
4. Production adds the docs/42 requirements on top (approval reference, scope CE-only and exactly one named CO, expiry up to 31 days, dry run without Confirm, readback, rollback). Production stays blocked until docs/42 D1-D6 are decided.

Part D can be built and tested locally now and also strengthens the Dev flow. It reuses the existing Review and start page logic (`tools/serve_attended_open_onboardings_dashboard.py`), adds the approval record under the state directory, and adds tests for each refusal above.

## 5. The hard problem: MFA and unattended runs

AGENTS.md and plan section 10: **MFA is preserved**; no password, code, cookie or token is stored or typed by the assistant. A team that runs onboarding while the owner is away needs one of these (owner and SecOps choose; all are decisions, not assumptions):

1. **Attended MFA, persisted session** (closest to today): the operator who starts a run completes login and MFA once in the automation browser (viewed through an approved remote-view channel), and the persisted profile keeps the session for its lifetime (the desktop already does this for Leonardo). Any approved operator can do it; no secret is stored on the server.
2. **Non-human account with SecOps-approved MFA handling** for the automation (for example a service account on a conditional-access policy). Gives true unattended runs but is a SecOps policy decision and a stored credential.
3. **Keep browser runs on an operator's own machine**, with A, B, D and E on the VM (read and review everywhere, create from an attended desktop).

Recommendation: build A, B and D plus E first (they need no MFA). Treat C as its own design after the owner and SecOps choose among the three options above.

## 6. Order of work

1. (Local, no approval) Part D: summary, approval record, tests. Plan an owner review of the summary wording.
2. (Local) Part A and B as modules with injectable credentials and fake backends; timers and units as review artifacts with the same hardening baseline as the dashboard unit. Nothing installed.
3. (Owner and SecOps, operator sends) Request pack: Salesforce Connected App and read-only permission set for the fixed queries; Redash read-only API key; outbound allow-list (Salesforce login and instance hosts, Redash host, OneLogin); OneLogin app, firewall 8443, certificate.
4. (Each step operator-run, approved, one block at a time) Install the credentials as root-owned files, install the timers, verify one read, compare the VM queue with the desktop queue (CO-0780 must appear), then retire manual publish.
5. Phase 2 browser runner, after the MFA decision.

## 7. Decisions needed

| # | Decision | Owner |
| --- | --- | --- |
| V1 | Confirm: VM-side Salesforce read via JWT Connected App, read-only, fixed queries (supersedes docs/41 "no Salesforce on the VM") | Owner; SecOps and Salesforce admin |
| V2 | Refresh interval (proposal 10 min) and the staleness threshold (6 h) | Owner |
| V3 | Redash read-only API key on the VM and the outbound allow-list | Owner; SecOps |
| V4 | MFA approach for VM browser runs (section 5, options 1-3) | Owner; SecOps |
| V5 | Who may approve a summary (the operator list), the approval lifetime (proposal 4 h), and whether a second person must approve production | Owner |
| V6 | OneLogin, firewall 8443, certificate (docs/40 section 7, unchanged) | SecOps, Identity |

Boundaries unchanged by this document: no external write, no credential created or stored, no Salesforce, Redash, Leonardo or BackOffice change, no production access.
