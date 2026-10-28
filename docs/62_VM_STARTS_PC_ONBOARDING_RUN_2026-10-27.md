# 62. VM starts the onboarding run on the owner's PC (owner decision 2026-10-27)

Status: built and tested locally, **locked by default**, not deployed. Nothing here creates a tenant by itself.

## Owner decision (recorded 2026-10-27, in chat)

> "I authorize the VM to start the PC onboarding run itself, with no click on the PC, keeping SSO and MFA by me. Record it as an owner
> decision, list the extra gates, and build it tests first, locked by default."

Rules it changes (and only these):

| Earlier rule | Change |
|---|---|
| docs/53 "one click, once, on the PC" | The click can be given on the VM page instead. The PC starts the same guarded create. |
| Owner decision 2026-10-08 "the VM refuses every POST" | Exactly ONE owner-only POST is added: `/attended/remote-start-request`. It writes a request file and creates nothing. Locked unless the VM is switched on. |

Rules it does **not** change: MFA stays a human step in the BackOffice window; no browser runs on the VM (docs/34, 36, 40); no password,
MFA code, token or cookie is stored or sent; every create gate (Approved CO, one-CO approval, unlocked build, sessions, duplicate and plan
checks, launch ticket, one run at a time, read-after-write) still runs on the PC with a live Salesforce read; production writes still need
the owner's per-CO action (here: the request, then SSO and MFA).

## How it works

1. VM page, production CO view: a "Start onboarding on my PC" button (only when switched on and the CO is Approved, CE-only New or Case 3
   New, and not already created) posts `reference` to `/attended/remote-start-request`.
2. The VM dashboard writes `<state>/remote-start-request.json` (CO, operator, time, 5-minute expiry, random nonce) and one log line.
3. The PC dashboard (only when started with `-RemoteStart -ProductionSessions`) polls every 20 s over the **same restricted forced-command SSH
   key** as the clone push, sending an HMAC-signed bundle of one file (producer `desktop-remote-poll`). The VM answers one line:
   `remote-start CO-x <nonce> <expiry>` once per request, else `none`.
4. The PC accepts the request (requester is the configured owner, not expired, lifetime at most 5 minutes plus 60 s skew, nonce unseen,
   at most 3 per hour, a signed-in owner session on the PC dashboard, and the live CO revision equals the one the owner saw), then runs the
   dashboard's own create path (`create_ui.remote_start_values` builds the same single-use token the page button would, then
   `create_ui.handle_create_post` re-evaluates every gate) with the owner as operator.
5. The runner opens the BackOffice window; the owner signs in with SSO and MFA (15-minute wait, as today). The result words reach the VM
   page through the existing records push.

## Extra gates added for this decision

| # | Gate | Where |
|---|---|---|
| G1 | Locked by default on both sides: the PC needs `SURFACE_ONBOARDING_REMOTE_START=1` (set only by `start_attended_dashboard.ps1 -RemoteStart`) AND `SURFACE_ONBOARDING_PRODUCTION_SESSIONS=1`; the VM needs the same variable in its dashboard unit. Exactly `"1"`. | `remote_start.PcConfig.from_env`, `start_remote_start_worker`, `vm_remote_start_enabled` |
| G2 | Owner only, enforced twice: the VM POST passes the existing identity, origin, secret and operator-role checks AND the requester must equal the ONE e-mail configured as `SURFACE_ONBOARDING_REMOTE_START_OWNER` (a second operator gets 403 and sees no button); the reply carries the requester and the PC refuses unless it equals its own configured owner (`wrong_requester`). | `do_POST` VM gate, `_is_remote_start_owner`, `remote_start.process` |
| G3 | The VM refuses a request unless the snapshot shows the CO Approved, on a supported route, and not already created. | `remote_start_problem` |
| G4 | One pending request at a time; 15 minutes per CO; 3 per hour (VM and PC each enforce a cap). | `request_remote_start`, `Inbox.accept` |
| G5 | The request expires after 5 minutes; the PC refuses expired, over-long-lived and replayed (nonce) requests; the nonce list survives a restart. | `Inbox.accept` |
| G6 | Delivery is one-shot and fail-closed: a request that cannot be marked delivered is not handed out; bad, forged or damaged files deliver nothing. | `deliver` |
| G7 | The poll channel is the existing signed, forced-command, approval-marker-gated channel; the poll bundle carries no data, is refused if it has other files or a future stamp, and can never be ingested as a snapshot. | `poll_remote_start`, `vm_snapshot` producer rules |
| G8 | The PC acts only as the configured owner (`SURFACE_ONBOARDING_REMOTE_START_OPERATOR`); `_issue_one_co_approval` still requires that operator on the issuer allow-list and the pinned Salesforce org. | `_start_remote_create` |
| G9 | The human gate: **a fresh BackOffice SSO and MFA sign-in for every remote start** (owner decision 2026-10-27, after the review). The runner child of a remote start gets `SURFACE_ONBOARDING_PRODUCTION_FRESH_SIGNIN=1` and then uses a brand-new temporary browser profile (never the persisted one, never a running Chrome), deleted with all its cookies at the end, so BackOffice and its SSO must ask for credentials and MFA. The button path is unchanged. | `launch_with_fresh_signin`, `leonardo_profile` |
| G10 | Words-only logs: the VM request log holds CO, requester e-mail, result word and time; the PC run log holds CO, result word and time; no nonce, no key, no payload. The SSH reply carries the CO, nonce, expiry, requester and revision, nothing else. | `_log`, `_run_log` |
| G12 | The owner approves what they saw: the request carries the CO's Salesforce `LastModifiedDate` from the VM snapshot; the PC refuses (`revision_changed`) when its live row differs. | `_post_remote_start`, `_start_remote_create` |
| G13 | The PC needs the owner signed in to the PC dashboard (an unexpired SSO session), as the button does (`dashboard_not_signed_in`). | `_owner_session_live` |
| G14 | Fail closed on damage: an unreadable inbox file refuses every request (`inbox_unreadable`, never overwritten); the VM refuses and removes a request it cannot log, because the rate limit is counted from that log; both logs are bounded. | `Inbox._load`, `request_remote_start`, `_trim` |
| G15 | The PC poller starts only after the dashboard port is bound, so a second PC dashboard process dies before it can poll. | `__main__` |
| G11 | The VM page shows the request state ("waiting for your PC", "your PC picked it up") so an unexpected window can be traced. | `_remote_start_html` |

## Decision taken: fresh sign-in

The open question "force a fresh BackOffice sign-in per remote start?" was answered yes on 2026-10-27 and is built (G9). A side effect: a
remote start opens its own new Chrome window and closes it at the end, instead of reusing the warm automation window.

## Not done / limits

* An approval is recorded before the later gates (sessions, clone freshness, prior attempt) run, in the remote flow exactly as with the
  button, so a request that then fails a later gate leaves a valid one-CO approval (7 days) behind.
* A comma-separated `-AllowedUser` silently keeps remote start off (the configured operator must be one e-mail).

* A compromised VM dashboard could write a request file. Effect: the PC starts a gated attempt for an Approved CO and waits for MFA the owner
  did not give; it cannot create without it. Cap: 3 per hour.
* The refresher runs as the same user as the dashboard; hiding the Salesforce session from the dashboard unit is step 28C.
* No PC desktop notification is added; the BackOffice window and the PC dashboard progress bar are the signal.

## Turning it on and off

* PC on: restart the dashboard with `-Restart -ProductionSessions -RemoteStart`. Off: restart without `-RemoteStart`.
* VM on: `_step28d_vm_remote_start_switch.sh on <owner-email>`. Off: `off` (removes the unit drop-in and restarts the dashboard).
