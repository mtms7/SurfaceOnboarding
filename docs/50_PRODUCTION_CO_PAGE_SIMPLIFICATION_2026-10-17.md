# 50 Production CO page simplification (2026-10-17)

Owner feedback: the production CO page (`/co/CO-xxxx?env=prod`) is cluttered. Goal: onboard CO-0798 (CE-only New) by hand in BackOffice, guided by one page. Guidance only; no create/confirm/submit control; `PRODUCTION_RUNNER_SUPPORTED` and `PRODUCTION_DRY_RUN_SUPPORTED` stay False.

Layout, top to bottom (one flat column, no inner scroll):
1. Header: CO ref, account, CE-only chip, and ONE verdict banner: "Ready to onboard" or "Blocked: <single most important reason>". Reason priority: refusal (approval, UUID, domain, duplicate, DealHub range) > clone stale/unavailable ("check BackOffice yourself for a duplicate before creating") > stale VM snapshot > timing window.
2. "Before you start": at most 5 one-line checks (route, approved, one email domain, UUID empty, duplicate check with the clone age/match in that one line).
3. Match details (only when a duplicate or ambiguous match exists): the matched tenants and differing fields, blocking.
4. "Enter in BackOffice > Add Account": one flat table in form order of only the fields to type or select (copy-friendly value column; Country and Expiration date are plain rows). One sentence replaces every Leave/default row.
5. "After you click Confirm": five numbered one-liners.
6. Footer: "Production create is locked in the dashboard: this page is guidance only." plus the data age line (clone capture; Salesforce snapshot time on the VM).

Removed from this page: Dev tab/chips and any Leonardo Development text (the Dev queue stays at `/dev`, the Dev CO page keeps its own switcher), the Onboard card (runner, session, approval, dry run, owner go, desktop address), the Production match card, the "Before migration" card, the repeated gate lists. A missing match is never a clearance: an unavailable or stale clone always yields "Blocked".

VM: rows that cannot apply are omitted; the one row that must stay (Country) reads "unavailable on this server" with the note to take it from the Salesforce Account. `/onboard/CO-xxxx` (the summary-approval review page) is unchanged.

## Review fixes (same day)
- Fail closed: Ready needs a clone item and a timing item (found by stable keys, not labels), both ok. Any unavailable key row (for example Country on the PC without a read, or on the VM) gives "Blocked: <field> unavailable: take it from the Salesforce Account".
- A recorded production-duplicate stop (`production_duplicate` / `duplicate_production_clone_match` in the check state) blocks the verdict and shows its tenants even when the current clone says not in production. The Dev start-gate wording stays off the page.
- The collapsed sentence is explicit: Alternate Domains, SubDomains, Networks empty; Operator Account and Scanning interval None; MFA on; Scan now, Notifications, Multiple users, API access, Phishing off; every Advanced switch off (docs/49).
- The clone status is read once per page and passed to both the sheet and the verdict.

## Addendum 2026-10-17 (r34 live findings): per-CO production match
- Cause of "Clone stale" after a push: the per-CO match is a 5 min display read (`production_match_display`) keyed only by CO, product and type, so a match computed before the push was replayed. The key now includes `_match_stamp()` (clone `captured_at` + `rows_sha256` + published snapshot name): a new push or snapshot is reflected at once. Stale (> 6 h), tampered and missing clones still fail closed.
- Status vocabulary added to `production_match`: `onboarded` (the CO's own Salesforce Account UUID from `co_details.json` equals exactly one clone `accountUuid`; UUIDs are normalised, compared and never shown), `uuid_not_in_clone` (UUID set, no tenant: blocks; the clone may be older than the create) and `not_checked` (the CO's inputs are not prepared, e.g. Surface and Surface + CE COs; never worded as a clone problem). A different UUID on a name/domain match stays a blocking Duplicate; two UUID owners or a second live paid exact-name tenant is Ambiguous. `exists_other` now reads "Other tenants only".
- Onboarded review (warnings for the owner, never a create gate; clone fields only): tenant disabled/deleted, licence not enabled or expired, end differs from the DealHub rule (plan `license_end`), CE-only counts not 1/1/1, SpyCloud off, leaked credentials not allowed, name without " - CE Only". Not in query 251, so not checked: country, user email domains, Advanced flags, MFA.
- Desktop: the UUID is not passed (no change there); the VM reads it from the published `co_details.json`.
- Review follow-ups: the UUID owner is identity-checked (name incl. suffix rule, primary domain, alternates, licence type; any difference is a visible warning and a name/domain mismatch relabels the chip "Onboarded: review"); any other live paid tenant matching the CO by name or domain makes it a blocking Ambiguous (shown "Live paid x N" only when N >= 2 are live and paid, else "Ambiguous"). A missing DealHub end reads "expiry not verified". The display cache drops expired entries and superseded stamps and is capped at 512 entries; a cached match is re-aged at read time (`computed_at` + `age_seconds`) and fails closed after 6 h. Filters: Onboarded, UUID not in clone, Not checked and Clone unavailable are separate. The desktop passes no UUID, so a duplicate there says "UUID check runs on the VM dashboard."
