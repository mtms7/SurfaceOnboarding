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
