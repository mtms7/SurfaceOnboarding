# Production automated CE-only create: owner runbook (2026-10-17)

Status: built as LOCAL CODE + TESTS ONLY. `PRODUCTION_DRY_RUN_SUPPORTED` and `PRODUCTION_RUNNER_SUPPORTED` are both `False` in `integration/onboarding/session_readiness.py`. Nothing here ran against BackOffice, Salesforce, Redash or the VM. Unlocking and the live create for CO-0798 are separate owner approvals.

## What exists

| Piece | File |
|---|---|
| Live building blocks (one-create request guard, one-Confirm click log, confirm click, live duplicate verdict, bounded readback) | `integration/onboarding/production_create_live.py` |
| Orchestrator (new: required live duplicate check, `not_clicked`, contract-compare gate) | `integration/onboarding/production_create.py` |
| Runner: `--production-create --co CO --revision REV --owner-go` | `tools/attended_ce_only_playwright.py` (`run_production_create`) |
| Dashboard card, gates, typed confirmation, one-time tokens, UUID write | `integration/onboarding/production_create_ui.py` + small hooks in `tools/serve_attended_open_onboardings_dashboard.py` |
| Tests | `integration/tests/test_production_create_{live,ui,runner}.py` |

While either constant is `False`: the production CO pages contain no form, button or input; every POST to `/attended/production-create`, `/attended/production-uuid-review` and `/attended/production-uuid-confirm` is refused before anything is read or started; the runner flag refuses before any Salesforce read or browser. The VM never shows a control.

## What one click does (in this order)

1. Dashboard (desktop, you signed in as the approval issuer) re-reads the CO from Salesforce and re-judges every gate (below). One-time token, typed CO reference, authorization box.
2. Runner starts: dry-run constant, live target (runner constant, sessions flag, approval reference, exact origin), Salesforce source, Redash clone facts, ordered gate. You sign in with SSO and MFA in the production window if asked.
3. In the page: service worker check, session check, early live duplicate check, form fill with the docs/49 and sheet values (start date = day of creation, expiry = start + 1 year - 1 day capped at the subscription end), every control read back.
4. Final gate with a fresh clock, then the LIVE duplicate pre-check (BackOffice itself: exact name with " - CE Only", primary domain text search, demo-domain list; any match, ambiguity or error refuses), then the `attempt_started` record, then the approval is consumed, then ONE click on Confirm. The request guard lets exactly one `POST /api/v1/backoffice/account/add` through; a second is aborted and counted.
5. Readback by `getAllDetailedAccounts` (read only, up to 6 tries, 3 s apart), compared with docs/49 (`production_contract.compare_readback`): zero mismatches required, the accountUuid is extracted into the redacted record only (never printed).
6. Result word on the CO page. The UUID write to Salesforce is a separate step (D3) with its own button and confirmation.

## Owner steps and which need an explicit go

| # | Step | Go needed |
|---|---|---|
| 1 | Refresh the Redash clone (< 6 h at the moment of the click) | no |
| 2 | `python tools/issue_production_approval.py --co CO-0798` (typed `ISSUE CO-0798`; one CO, 7 days, consumed by the create) | YES (D1) |
| 3 | Reviewed commit flipping `PRODUCTION_DRY_RUN_SUPPORTED = True` (nothing else) | YES |
| 4 | Dry run: `python tools/attended_ce_only_playwright.py --production-dry-run --co CO-0798 --revision <REV>` with `SURFACE_ONBOARDING_PRODUCTION_SESSIONS=1` and `SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF=<ref>`; passes only if the record says `dry_run_passed` | YES |
| 5 | Reviewed commit flipping `PRODUCTION_RUNNER_SUPPORTED = True` (the dry-run constant must already be True) | YES |
| 6 | Start the dashboard on the desktop with the sessions flag, sign in (Salesforce SSO). Open `/onboard/CO-0798`: the "Create in production" card appears only if every gate passes | no |
| 7 | Type `CO-0798`, tick the authorization, click "Create in production". Sign in with MFA in the production window if asked and leave it open | YES (this click) |
| 8 | Read the result word on the CO page | no |
| 9 | If `created_verified`: "Write Account UUID to Salesforce", then "Confirm write to Salesforce" (field `Account_UUID__c` only, read-after-write) | YES (D3, one go per write) |
| 10 | Reviewed commit setting both constants back to `False` (relock) | YES |

The 24 h limit: the passing dry run must be at most 24 hours old when the card renders.

## Gates for the control (first refusal wins; all re-judged at POST)

Desktop not VM; both constants True; sessions flag and an unexpired approval reference; you are the approval issuer; a valid unused one-CO approval bound to the CURRENT Salesforce revision (`approval_revision_drift` otherwise); CE-only New route; Salesforce data fresh and Salesforce session ready; no Account UUID yet; clone fresh (< 6 h); a passing dry run for this CO and approval (24 h); no earlier attempt waiting for your review; no run already launched. The token is spent by the first POST whatever its outcome.

## Result states

| State | Meaning | What you do |
|---|---|---|
| `created_verified` | Tenant exists, readback matches docs/49 exactly | Step 9 (UUID write) |
| `created_mismatch` | A tenant exists but the readback differs (field names are in the record `readback_mismatches`) | Stop. Never create again. Open the record, decide an edit in BackOffice (D2) |
| `create_outcome_unknown` | The click may or may not have created a tenant: no/ambiguous response, readback never found the row, an interrupted attempt, or `attempt_started` with nothing after it | Check BackOffice by hand. The CO stays blocked until you review. Never retry blindly; a new create needs a new approval and your explicit clearing of the review |
| `confirm_not_clicked` (refusal) | Confirm was missing, disabled or covered: nothing was sent, but the approval was already consumed | Check BackOffice, then a new approval |
| refusals before the click (`duplicate_in_production`, `demo_domain_conflict`, `duplicate_check_unavailable`, `clone_stale`, `approval_revision_drift`, `dry_run_required`, ...) | Nothing was created and the approval is NOT consumed (consumption happens only after the live duplicate check) | Fix the named cause |

Records are append-only in `production-create-records/`; UUID write results are appended to `production-create-records/production-uuid-writes.jsonl` (the value is stored as a SHA-256 only). A recorded UUID write (even a rejected one) blocks another automatic write: check Salesforce first.

## Recovery (D2)

No delete or disable path is known. A wrongly created tenant is recorded (tenant name, account id, UUID, domains, plan, key form fields; no secrets) and YOU decide whether to rename or fix parameters by edit in BackOffice. Never re-click Create after an uncertain outcome.

## Residual risks

- The create HAR was observed once (CO-0797). The form-to-payload mapping and the readback grammar (`unique_fields` name + domain) are verified only by tests and the earlier read-only probe until the dry run and the first create.
- The Confirm "element under the pointer" check and the Dev helpers (`_locate_confirm_button`, `_confirm_enabled`) are reused from the Dev runner but have never run against production.
- A click can be sent and the response lost: that is `create_outcome_unknown` by design, not a retry.
- The approval is consumed before the click: a failure between consume and click costs a new approval.
- No delete path (D2). The domain duplicate check is a text search: a partial match refuses (false positives are accepted).
- The runner window can wait up to 15 minutes for MFA after you click; the gates are re-run with a fresh clock before the approval is consumed.
- The dashboard's "fresh Salesforce data" at page render is the short display read; the POST re-reads the CO from Salesforce.
- Same-second records order by rank in the status logic, but the older `create_records_by_co` landing chip still shows the last file name in a same-second tie.

## Review fixes (same day, after the independent review)

1. **Search proof before any clearance.** The generic `search` filter is unverified for domains (docs/42, docs/48), so the live duplicate check first runs the SAME name and domain searches for a control tenant taken deterministically from the fresh Redash clone (a live tenant with a unique name and domain) and requires each to return it; otherwise `search_unproven` (or `search_control_unavailable` with no control) and nothing is clicked. Only a 16-character hash of the control name is recorded (`search_control`). Owner proof, attended and read only (nothing typed or clicked), before the dry run: set `SURFACE_ONBOARDING_PRODUCTION_PROBE=1` and `SURFACE_ONBOARDING_PRODUCTION_PROBE_REF=PROBE-SEARCH-20261017`, then `python tools/attended_ce_only_playwright.py --production-search-probe` (sign in with MFA). Result `production_search_probe_complete` means both searches returned the known tenant; the redacted report is in `production-probe/`. The field-scoped `or/field` grammar is documented only for names (docs/49), so it is not used for domains.
2. **Salesforce re-read.** Right before the approval is consumed and the click, the runner re-reads the CO: a changed revision or a non-empty Account UUID refuses (`approval_revision_drift`, `salesforce_prior_uuid_present`), an unreadable CO refuses (`source_recheck_unavailable`); the gate is re-run with a fresh clock and the fresh revision.
3. **The CLI flag proves nothing.** `--owner-go` is not accepted alone. The runner needs a file-backed one-time launch ticket (`production-launch-tickets/<CO>.json`, 2 minute TTL, bound to CO, revision and approval reference, minted by the dashboard only after the typed confirmation, consumed by an atomic rename). It also enforces in the runner: ticket operator = approval issuer, and a passing dry run for this approval at most 24 h old. A direct CLI start refuses (`launch_ticket_invalid`). Residual: anyone who can write the state folder can mint a ticket, as with the approval file.
4. **Confirm preflight.** The dry run and the live run both run the locate / enabled / scroll-into-view / element-under-pointer checks of Confirm without clicking (`confirm_preflight_failed`, before the approval is consumed). If Confirm still cannot be used at click time the result is `confirm_not_clicked` (nothing was sent, approval consumed). You can clear exactly that case: `python tools/clear_production_attempt.py --co CO-0798 --approval-ref <old ref>` (interactive, type `CLEAR CO-0798`) appends an `owner_review_cleared` record; it works only for an `attempt_started` followed by the `confirm_not_clicked` refusal of the same approval. A NEW approval and a NEW dry run are still required. Unknown, mismatched or verified creates can never be cleared this way.
5. **Before arming:** service workers are re-checked, any popup or any second production-origin page refuses (`extra_pages_open`). The automation Chrome exposes a loopback remote-debugging (CDP) port while a signed-in production session is open; the runner closes it at the end, keep the window to yourself while it runs.
6. **Readback** also compares the license start day (one day tolerance for the picker time zone), the expiration day, MFA on and the organization email. Only field NAMES (`licenseStartDate`, `licenseExpirationDate`, `mfaRequired`, `organizationEmail`) are recorded, never values. The row paths for these four are not yet verified: a path the row does not carry shows as a mismatch name, so check them first with the existing read-only `--production-readback` on an existing tenant.
7. **UUID write:** the plan is re-read immediately before the update; a `write_started` line is appended BEFORE the write (no line, no write); a failed result record is reported (`record_write_failed`) and the intent line keeps blocking. After `write_uncertain` a second write needs your recorded review: `python tools/clear_production_attempt.py --co CO-0798 --uuid-write` (type `CLEAR UUID CO-0798`).

## Second review fixes (same day)

- **Control tenant.** It must not let the domain control pass through the name field: tenants whose normalised name contains their primary domain or its first label are excluded, and the control's domain must be owned by exactly ONE clone tenant (primary or alternate, deleted included). The control proves the NAME search and the primary-domain search only; it does NOT prove alternate-domain or user-email-domain coverage of the generic search (the duplicate check still treats any returned row as a match, but an empty answer for those two kinds of domain is not proven).
- **Owner clearing needs identity.** `tools/clear_production_attempt.py` requires `--operator EMAIL` equal to the issuer of the approval (Milton only) and, for a not-clicked attempt, `--approval-ref` and `--revision`. The audit record carries operator, time, reason, approval reference and revision; the `--uuid-write` line carries operator, time and reason. The clear is keyed by CO + approval reference + revision and counted per attempt, so a later crashed attempt under a reset or reused reference is never hidden.
- **Guard at context level.** The request guard routes on the browser CONTEXT (`context.route`), so popups and new pages are covered; a context that cannot be routed stops the run. After arming, any popup, new page or service worker before or after the click makes the result `create_outcome_unknown` (CO blocked), never a clean submit.
- **Launch ticket MAC.** The ticket carries an HMAC-SHA256 over every field (operator included) keyed by 32 random bytes in `production-launch-tickets/.ticket.key` (0600) in the state folder, never the repository (`production-launch-tickets/` is in `.gitignore`). A failed launch deletes the ticket. Honest limit: a process running as the same Windows user can read the key and mint a ticket, exactly as it can edit the approval file; this is a same-user trust limit, not a defence against a hostile local user.
- **Exact country.** The strict readback compares `accountCountryCode` with the ISO-2 value of the selected Country option (the value the form submits), not just an ISO-2 shape; an unreadable value refuses before the consume (`country_code_unavailable`).
- **Click-time checks.** The 24 h dry-run age is judged again right before the approval is consumed. Production paths ignore `SURFACE_SF_TARGET_ORG`: a different alias refuses (`salesforce_alias_not_pinned`; the UUID write uses the pinned alias `surface-onboarding`).

## Third review fixes (same day)

- **Ticket key.** Exactly 32 bytes (64 hex characters) or it counts as missing: an empty, short, long or odd-hex key file is never used for signing or checking. The key is made atomically (full temp file, then hard link into place), so a crash between steps or a concurrent mint can never expose a half-written key; minting replaces an invalid key file atomically. No key rotation: delete `.ticket.key` to invalidate every outstanding ticket.
- **One ticket folder.** The dashboard (mint) and the runner (consume) both use `production_launch_ticket.ticket_root()`: `%LOCALAPPDATA%\SurfaceOnboarding` on Windows (or the absolute `SURFACE_ONBOARDING_TICKET_ROOT`). It never falls back to the repository, and refuses a repository, OneDrive or Documents location. On Windows the folder is restricted to the current user with `icacls /inheritance:r` (best effort). Residual same-user limit as before.
- **Replay.** Every consumed nonce is recorded in `.used-nonces`; a copied ticket cannot be replayed inside its 2 minute TTL; non-object JSON and an unreadable nonce log refuse.
- **After the poll.** A popup, new page or service worker that appears after the click, even after the readback poll found a matching row, turns the result into `create_outcome_unknown` (no VERIFIED record).
- **Org pin.** When `SURFACE_SF_EXPECTED_ORG_ID` is set, the dry run and the live run compare the Salesforce org Id (first 15 characters) and refuse on a mismatch or an unreadable org (`salesforce_org_not_pinned`); the dry run also refuses an alias override.
- **Limits that remain.** The runner attaches to the automation Chrome over CDP, so `service_workers='block'` cannot be set on its existing context; service workers are detected and refused instead. WebSocket traffic and browser extensions are invisible to the request guard. The dashboard tab lives in the same automation window; context-level routing intercepts its requests too (its own local origin is not the production origin, so it is passed through untouched), but keep the window to yourself while a run is active.

## Final review fixes (same day)

- **Org pin is mandatory.** The dry run and the live run REFUSE (`salesforce_org_not_pinned`) when no expected org Id is known. The expected Id is `SURFACE_SF_EXPECTED_ORG_ID`, else the launcher's file `%LOCALAPPDATA%\SurfaceOnboarding\salesforce-expected-org-id.txt` (read only, never written by this code); a malformed Id or an org mismatch also refuses.
- **Ticket hardening fails closed.** A non-zero `icacls` exit, an exception or a missing user name means NO ticket is minted; the hardening check runs on every mint. `SURFACE_ONBOARDING_TICKET_ROOT` must be a local drive path: UNC, `//` and network drives are refused.
- **Wording.** A readback that returns nothing (no row, session or reply problem, or a popup after the click) is recorded as `created_unverified` with reason `readback_unavailable`; the dashboard shows and treats it as `create_outcome_unknown` (the CO stays blocked). Code, comments and this runbook now agree.

## Page-own table reads replace the cookie fetch (2026-10-18)

- **Why.** Live 2026-10-18: after a human SSO+MFA sign-in both in-page `fetch` searches returned 401 (the page uses an in-page token header, docs/48 note). The dry run and the create could never pass; everything failed closed.
- **How.** Every production read (`--production-search-probe`, `--production-readback`, the dry-run and live duplicate checks, the session checks and the post-click readback) now triggers the PAGE's own `getAllDetailedAccounts` request (first read: reload; later reads: the in-app column sort, a logged `sort_table_header` click; with the verified form open: only a dispatched click, never a reload) and rewrites only `tableServerData`. The guards (`production_probe.RequestGuard(require_read_body=True)`, the dry-run and live guards) pass that POST only when its body validates (HAR key set, offset 0, at most 100 rows, one `search` clause or the verified `or` variant) and still abort every other write; the live guard still allows exactly one armed `account/add`.
- **Session and demo list.** Session = status of the page's own table reply plus the page's own `getAuthorities` / `accountInfo` statuses when seen (401/403 `production_session_expired`, otherwise `production_session_not_ready`). The demo list is compared only if the page fetched it itself; if it never did the check fails closed as `duplicate_check_unavailable`. A late check with the form open and no usable sort header also fails closed (nothing is clicked or created).
- **Open item.** Whether the Accounts page issues the demo-domain GET on its own, and the real Playwright route / response behaviour, are verified only against fakes. The first dry run decides.

## Gap-review fixes (2026-10-18)

- **MFA readback path.** The strict readback reads `primaryUser.isMfaRequired` (the Dev-verified row path) and nothing else for `mfaRequired`; the stale `mfaRequired` / `accountSettings.mfaRequired` / `accountLicense.mfaRequired` candidates are gone, so an absent or non-boolean value is a `mfaRequired` mismatch name. The path is in the projection requested from `getAllDetailedAccounts`. The strict row also checks `multipleAttackStacksEnabled` is off at its Dev-verified path; the top-level `fullNucleiScanEnabled` / `authenticatedTestingEnabled` get no readback check (nullable and not in the create body: a check would risk a false `created_mismatch`).
- **Advanced controls gate.** All nine advanced targets are OFF (from the Dev plan). A control that is absent still refuses `advanced_control_absent` / `advanced_section_not_found`; now ANY control whose state differs from its target (or a report without the comparison) refuses `advanced_mismatch`, naming the controls only, before any Confirm click or approval consume. The dry run shares the check, so it cannot pass in that case.
- **Create body comparison.** The request guard keeps the ONE allowed `account/add` body redacted: booleans, numbers, null and setting strings (type, country, intervals, license type, counts, dates) as sent; identifying strings (name, domain, email, person names) are never kept, only matched or not. After the click it is compared with `expected_create_payload` and the mismatching field NAMES go to the record as `payload_mismatches` (`unexpected:<name>` for extra fields, `body_unreadable` / `payload_expectation_unavailable` when it could not be judged). A non-empty list is `created_unverified` / `readback_payload_mismatch` (shown as `created_mismatch`); a readback never compared (`readback_payload_unchecked`) shows as `create_outcome_unknown`. Nothing is aborted before the send.

## Readback diagnostic (2026-10-18)

The first live `--production-readback --co CO-0797` compared 13 license and setting paths as missing. To learn which page-own read variant returns them, `--production-readback` now also records, names only, three variants for the CO's tenant (exact name search): A = the page's own `unique_fields` kept and no projection key, B = `unique_fields` override only, C = the current override plus projection (the primary read, whose verdict is unchanged). A and B are two more reloads of the same page after the primary read, one at a time, no clicks, and only when the primary read answered 200. For each variant the redacted report (`variants`) holds the result word (`ok`, `unavailable`, `session_expired`, `not_found`, `ambiguous`), row count, total (capped at 1000) and the sorted READBACK_PICK_PATHS names present and absent in the matched row; the printed line adds `variant_result` and `variant_absent` (fixed enum, validated by `production_contract.variant_line_problem`). A failed variant is `unavailable`. The looser variants exist only in the readback probe class: the reader's default mode and every create, dry-run and duplicate read stay strict (name + domain override and a projection, `read_body_problem(strict=True)`). Not verified: real server behaviour of A and B. The next step decides the compared paths from this output.

## Projection removed (2026-10-18)

Live finding (read-only diagnostic): requests with an explicit `projection` of dotted paths made the server return rows WITHOUT nearly all compared paths (13 false mismatches), while the page's own `unique_fields` and the `[accountName, accountDomain]` override, both sent with no projection, returned the full set of real paths; the only "absent" names were fallback candidates that do not exist. Every strict read (duplicate checks, session check, dry run, create, post-click readback, search probe) now sends the `[accountName, accountDomain]` override and NO `projection` (the page's own one is removed; `read_body_problem(strict=True)` refuses a body that has one), `items_per_page` stays 100, and the reducers (`search_reply`, `readback_reply`) pick the needed fields from the full rows in Python and keep nothing else (`primaryUser.email` is compared in memory only, never logged or recorded). The compared row paths are now only the real ones (`accountLicense.startDate`, `accountLicense.expirationDate`, `accountLicense.reconLevel`, `accountLicense.scanningFrequency`, `accountSettings.reconSettings.automatedDiscoveryEnabled`, `primaryUser.isMfaRequired`, `primaryUser.email`); a missing path still fails closed as a mismatch name. The readback diagnostic now has two variants: A (the page's own `unique_fields`, no projection) and B (the strict read); the projection variant C is gone. Not verified: the new strict body against the live server (fakes only).

## Review fixes 1-3 (2026-10-18)

- **1 Form integrity during the live reads.** The in-app sort trigger now asks the DOM about each `thead th button` candidate and uses only one OUTSIDE any dialog (`[role=dialog]`, `[role=alertdialog]`, `[aria-modal=true]`, `dialog`, `.modal`, MUI dialog/modal roots); a button inside a dialog, or one that cannot be classified, is never clicked or dispatched on (no other candidate: the read fails closed, never a reload with the form open). After the live duplicate pre-check, the Salesforce re-check and the fresh gate, and immediately before the intent record and the approval consume, `production_create.run` calls a new REQUIRED `live_form_recheck`: the same read-only controls compare as the fill (texts, blanks, selects, toggles, dates, operator account empty, scan now) plus the nine advanced controls against their targets and the exact country code. Any difference, unreadable control or exception refuses `form_changed_during_checks`; a live run without the check refuses `form_recheck_missing`. Nothing is armed, consumed or clicked in either case.
- **2 Rewrite proof.** A rewritten request is recorded only after `route.fallback(post_data=...)` returned; if it raised, the request goes on unchanged and is not recorded (the read fails closed). A reply counts only for a recorded request whose own body (`post_data`) carries the rewritten fields (search value, `unique_fields` override, offset, page size) and no dropped key (no `projection`), not by object identity alone.
- **3 Control proof.** `production_create_live.control_reply_problem` is shared by the live duplicate pre-check's control step and `--production-search-probe`: a readable 200 with an integer `total <= len(rows)` (a filter the server ignored reports more matches than rows, so it is `search_unproven`) that contains the control's row: exact normalised name for the name search; for the domain search a row with the exact normalised primary domain AND the control's name. The probe report and printed line keep their shape.
- **Not verified.** The real DOM closest-dialog check, Playwright's `request.post_data` after a fallback override, and the live server's reply shapes are covered by fakes only. `PRODUCTION_RUNNER_SUPPORTED` and `PRODUCTION_DRY_RUN_SUPPORTED` remain False.

## Review fixes F1-F3 (2026-10-18)

- **F1 Complete form re-check.** The fill records the informational advanced controls it read (SpyCloud, which feeds `leakedCredentialsSettings.spyCloudSettings.enabled`) and the sanitized form's control set (name, label, type, data-am; no values). `live_form_recheck` now also compares SpyCloud exactly (`spycloud_state`) and re-runs the read-only schema check plus an exact control-set comparison (`form_schema`), so a re-render that flips SpyCloud, adds an unknown control or removes a known one refuses `form_changed_during_checks` before the intent record, the consume and any click.
- **F2 One resolved header.** The sort header is resolved once to an element handle, classified (outside any dialog) on that handle, and the same handle is clicked or dispatched on. It is re-classified immediately before the click; a detached handle or a failed evaluation means no click (the read fails closed or falls back as before).
- **F3 Re-check coverage.** `production_create.run` requires the re-check result to name every key the fill verified plus `advanced_controls`, `country_code`, `spycloud_state` and `form_schema`; a smaller result refuses `form_recheck_missing` (approval not consumed, nothing clicked).
- **Not verified.** Playwright's real `element_handle()`/`evaluate`/`dispatch_event` and the live form's control set are covered by fakes only. Both production locks remain False.

## Live dry-run fixes (2026-10-18, CO-0798)

- **Dry run #1** refused `toggle_click_failed` on `scan_now`: the pre-click hit test ran on a control outside the viewport. Toggles are now scrolled into view first (as the Confirm path already did); the refusal record names the failing stage (`toggle.<key>.stage_read|scroll|hit_test|click|state`).
- **Dry run #2** refused `advanced_mismatch`: production defaults `automatedDiscoveryEnabled`, `subDomainsReconEnabled` and `webDictionaryBruteForceEnabled` ON, while the CE-only target (docs/49) is all OFF, and the fill only read the advanced switches. Owner go: the fill now SETS each advanced switch to its target through the same toggle path (scroll, hit test, plain click, verified; never forced) and then reads them, so `advanced_problem` remains the gate. The live pre-Confirm re-check stays read-only (it never clicks).
- **SpyCloud** has no switch in the production Add form (`absent`). Absent at the fill and absent at the re-check is stable (`spycloud_state` pass); appearing or changing refuses `form_changed_during_checks`. The readback still requires `leakedCredentialsSettings.spyCloudSettings.enabled = True` (server default).
