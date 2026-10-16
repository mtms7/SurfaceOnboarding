# Production-first dashboard redesign (proposal) - 2026-10-16

Status: design only. No dashboard file was changed. `PRODUCTION_RUNNER_SUPPORTED` stays `False`; nothing here authorizes a production write. Mockup: `docs/mockups/production_dashboard.html` (static, fake data, 3 views).

## Why
Today the landing page `/` is Leonardo Development ("Onboardings", DEV), and the production-checked review lives at `/onboard` as a plain 5-column table. The owner works on production (Redash clone, duplicate and live-paid checks, approval, create). Production should be the first thing seen; Dev becomes secondary.

## Look (unchanged, reused exactly)
Tokens from `PENTERA_CSS`: `--nav #0f1626`, `--nav-active #283043`, `--canvas #f4f5f7`, `--card #fff`, `--line #e5e7eb`, `--text #2e3440`, `--muted #6b7280`, `--heading #4b5563`, `--primary #1f7ae0`/`#1664c0`, chips ok/bad/warn/info/neutral, 10 px cards, 14 px system font, `.dense` tables, `.kv` grids, `.blockers`, `.banner*`, `.seg`, `.envpill`. No new colours, icons or libraries. Production keeps the amber `--env-prod` pill/bar (red is never an environment colour).

## Information architecture
Sidebar order (one navy aside, as today):
1. **PRODUCTION** group (amber head, hollow dots): **Onboarding** (landing, `/`), Tenants (`/prod/tenants`), History (`/history`, closed onboardings), Approvals (`/prod/approvals`). **Owner direction 2026-10-16:** the production menu has only these four. Matches is not a menu item: the match details (duplicate name/domain, live paid tenant count) live inside each CO's *Duplicate validation* card. Readiness is not a menu item either: clone age and trust show in the clone-status strip on every production page. D2 create records are shown on the CO detail page, with no separate Records menu.
2. **TOOLS**: Sessions (`/connection`) stay here.
3. **LEONARDO DEVELOPMENT** last, a collapsed `<details>` (Onboardings, Onboarded on Dev, Dev tenants). It opens by itself when a Dev page is active. Dev pages keep the navy DEV pill/bar so nobody mistakes them for production.

Routes: `/onboard` becomes an alias of the new landing; the Dev queue moves from `/` to `/dev` (old URLs redirect). Approvals is a read-only list over `production_create_approvals.json`; create records appear on the CO page (redacted, from the D2 record files) (no secrets, no raw payloads).

## Production queue (landing) columns
Filter bar (existing `.fbar`/`.fchip`): All, Ready for dry run, Blocked, Approved, Created. One dense table, sticky first column:
`CO` (link) | `Account` | `Product / type` | `Start` (with "2-day window open/closed") | `Production checks` (one chip: Clear / Duplicate / Live paid x N / Clone stale) | `Approval` (chip: None / Valid until / Expired / Used) | `Create` (chip: Locked / Dry run ready / Dry run passed / Created / Verified) | `Next step`.
A strip above shows the clone age chip ("Clone 2 h 10 min old, limit 6 h") and one lock chip while the runner is unsupported. Rows without an automated route stay listed, greyed, with reason.

## Production CO detail (`/onboard/CO-xxxx`), one column of cards, top to bottom
1. **Header**: CO, account, route chip, status chips, lock banner when locked.
2. **Salesforce facts**: account, product, type, route, start date, source revision, tenant name, domains, plan (what would be sent).
3. **Production checks**: clone captured time and age vs the 6 h rule; duplicate by name; duplicate by domain; live paid tenant count (must be at most 1 exact name); timing window (start minus 2 days, or CSM request); one valid email domain; Account UUID empty. Each row is chip + one-line reason. A missing match is never a clearance.
4. **Approval** (D1): one CO, one route, source revision, issuer, expiry (7 days), consumed state, reference. Button "Approve this summary" only when checks pass.
5. **Create** (D2/D4/D5): steps Dry run, Owner go, Submit, Readback. Dry run button is secondary (blue outline); "Create in production" is a separate primary button behind a confirm checkbox and is **absent** while locked.
6. **D2 record**: link to the redacted local record (tenant name, account id, UUID, domains, plan; no secrets) and the line "Wrong create: owner edits parameters in BO; delete path unknown".
7. **UUID writeback** (D3): separate card, separate go, enabled only after `readback_verified`; field `Account_UUID__c` only, must be empty or equal, revision unchanged, read-after-write in Salesforce.

Locked state (`PRODUCTION_RUNNER_SUPPORTED=False`): warn banner at the top ("Production create is locked. Review and dry run only."), Create card shows steps greyed with `disabled` buttons removed entirely (no create button in the HTML, not just disabled), writeback card shows "Locked until a verified create". Blocked state: red `.blockers` list with the owner decision needed. Fail closed on any unreadable state.

## Remove or simplify versus today
- Remove the Dev queue as landing page and the `ONBOARDING / production-checked` group (merged into PRODUCTION).
- Remove the tile row (6 tiles) on the production landing; the filter chips already count.
- Replace the per-page "Production view, read-only" prodnote with the single env pill plus lock chip.
- Collapse `/prod/matches` "Latest Start-gate checks" into the CO page Production checks card.
- Drop repeated clone lines on every page: keep one clone-age strip on the queue and the checks card.
- Keep the Dev CO page, runner and history unchanged, only regrouped.

## Impact on tests (must be updated with the real build)
`test_dev_prod_areas.py` pins nav `ORDER`, group heads (`ONBOARDING...`, `DEV · LEONARDO`, `PRODUCTION · BO`, `TOOLS`), `navgroup dev` before `prod` before `tools`, one `.active`, and env pills. `test_co_page_layout.py`, `test_dashboard_stage_tracker.py` and `test_attended_open_onboardings_dashboard.py` pin Dev CO and queue markup; `render_onboard_queue`/`page_onboard_review` tests need the new columns. Keep class names (`navgroup`, `envpill`, `chip-*`, `dense`, `kv`) so existing selectors survive; change only order, labels and `NAV_GROUPS`/`ENV_BY_ACTIVE`.

## Open items
Exact queue data sources for "Create" chip states and the Approvals list file location were not verified; `production_create_record` field names must be read before the build.
