# Request pack: read-only Salesforce and Redash access for the onboarding VM (2026-10-14)

Prepared for the owner to send. It contains no credential, key, token or customer data. Status: **drafted; nothing requested, nothing created, nothing installed.** 2026-10-15: the owner directed that the VM must run without the owner's computer (docs/43 section 9), so the hold on this pack is lifted for the owner to send; the owner sends it, the assistant sends nothing. Background and design: `docs/43_VM_NATIVE_ONBOARDING_SERVER_DESIGN_2026-10-13.md`, setup text in `integration/deployment/vm_pilot/README.md`.

## 1. Summary (for every reader)

The Surface onboarding dashboard on VM `workato-opa-01` (172.26.37.20, Ubuntu 22.04) must show the live onboarding queue to the team without depending on the owner's laptop. Today it shows a snapshot the owner copies by hand. We ask for:

1. **Salesforce admin:** a read-only, non-human API identity (Connected App, OAuth JWT bearer) that can read one list view and one object's fixed fields. No write access.
2. **Redash owner:** the existing read-only API key of saved query 251 (production clone, owner approval 2026-10-05) installed on the VM instead of the owner's laptop.
3. **SecOps / network:** outbound HTTPS from the VM to three named hosts, later OneLogin sign-in and an inbound rule for the team.

Everything runs as two systemd timers (Salesforce every 10 minutes, Redash hourly), each switched off until an operator creates an approval marker after your approval is recorded. Rollback is one command per timer.

## 2. Request to the Salesforce admin

**Identity.** A Connected App using the **OAuth 2.0 JWT bearer flow** and one integration user, pre-authorized by profile or permission set, with:

- **No** `full` or `refresh_token` scope; only `api` (and `web` is not needed).
- Login IP range restricted to **172.26.37.20**; session timeout as short as policy allows (the client requests a new token when one expires).
- A certificate uploaded to the Connected App. **The private key is generated on the VM and never leaves it**; we send you only the public certificate (`sf-reader.crt`). We need back: the **consumer key** (client id), the **integration username**, and the **My Domain login URL** (`https://<mydomain>.my.salesforce.com`). None of these is secret in the JWT flow.

**Permissions: read only, exactly this.**

| Object | Access | Fields (read) |
| --- | --- | --- |
| `Customer_Onboarding__c` | Read | `Name`, `Onboarding_Approval_Status__c`, `Onboarding_Stage__c`, `Onboarding_Product__c`, `Onboarding_Type__c`, `Account__c`, `Account_Name__c`, `Account__r.Name` (via the list view), `Submission_Date__c`, `Primary_User_Name__c`, `Main_Domain__c`, `Alternate_Domains__c`, `Email_Domains__c`, `Onboarding_Comments__c`, `Surface_Account_ID__c`, `Account_UUID__c`, `LastModifiedDate` |
| List view `Open_Onboardings` on `Customer_Onboarding__c` | Visible to the integration user | the list view's results and its `ListView` metadata row |

Please confirm or decide:

- `Onboarding_Comments__c` is free text and may contain sensitive notes. The dashboard shows it on each CO page today. If you prefer, we exclude it from the VM (the page then shows "not published").
- No Create, Edit, Delete, "Modify All" or "View All Data". No other object. (A later phase, V5 in docs/43, would add read of `DealHub_Subscription__c` and `Account` identifiers for approval summaries; it is **not** part of this request.)

**What the client sends (all of it, GET only):**

- `SELECT Id FROM ListView WHERE SobjectType = 'Customer_Onboarding__c' AND DeveloperName = 'Open_Onboardings' LIMIT 2`
- `GET /services/data/v67.0/sobjects/Customer_Onboarding__c/listviews/<id>/results`
- `SELECT <the fields above> FROM Customer_Onboarding__c WHERE Name = 'CO-nnnn' LIMIT 2`, one per queued or previously published CO (roughly 10 to 30 requests per refresh, about 150 per hour).

The client refuses any other call shape in code (tested): only a single `SELECT`, or a GET of that exact list-view path; no redirects; response size and record counts capped. Expected API usage is well below 1% of a typical daily limit.

## 3. Request to the Redash owner

- Install the **existing per-query API key of saved query 251** on `Prod (Cloned) - Mgmt` (read-only; owner approval 2026-10-05) on the VM, **or** issue a second key if Redash supports it so the desktop and the VM rotate independently. If regenerating the single key is the only option, tell us first: it invalidates the desktop's copy.
- The VM uses exactly four calls on `redash.pentera.io`: GET and POST of `/api/queries/251/results`, GET of one job and one result by number. It never creates, edits or deletes a query. The existing rule stays: **new queries only, never edit 251**.
- The key is delivered by your secure channel to the VM operator, who places it as a root-owned file (`0640 root:surface-redash-reader`). It is never pasted into chat, a ticket or shell history.

## 4. Request to SecOps and network

| Item | Detail |
| --- | --- |
| Outbound from 172.26.37.20 (HTTPS 443) | `<mydomain>.my.salesforce.com` (login and instance; the instance host returned by login must also be allowed if different), `redash.pentera.io`; later the OneLogin host for oauth2-proxy |
| Inbound (later) | TCP 8443 from the subnet SecOps names; `127.0.0.1:8000` and `:4180` never exposed |
| Identity (later) | OneLogin application (OIDC) for oauth2-proxy, allow-list of named users, an operator role; today only the owner is allowed, behind an SSH tunnel |
| Certificate | Pilot self-signed (90 days); internal-CA certificate for a DNS name preferred before the team uses it |
| Data at rest | Customer names, domains, onboarding comments and tenant inventory in `/var/lib/surface-onboarding` (mode 0750, service user only; last 5 snapshots) and the production clone in `/var/lib/surface-onboarding/clone-inventory` |
| Secrets on the VM | Salesforce private key and Redash key as root-owned files readable only by two dedicated groups that **only the two timer services** hold; the dashboard process cannot read either |
| Account separation | Services run as `surface-onboarding`, not as `workato`; the Workato agent and OPA are untouched |

## 5. What it can and cannot do (safety properties, each covered by an automated test)

- Reads only: no Salesforce write, no Redash write, no Leonardo or BackOffice access, no browser on the VM.
- Fails closed: any failure leaves the previous snapshot in place; the banner shows its age and turns amber after 6 hours.
- Nothing is printed or logged except counts and short reason codes (no tokens, keys or record values).
- Off by default: each timer needs an approval marker file that an operator creates after approval; removing it stops the job.
- MFA is untouched: nothing here signs a human in; production creates and browser runs are out of scope and remain blocked (docs/42).

## 6. Rollout and verification (operator-run, one block at a time, each with your approval)

1. Create the two groups; generate the key pair on the VM; send the admin `sf-reader.crt`.
2. Receive consumer key, username and login URL; write `sf-reader.json` (identity values only); run the **dry run** (no network).
3. Install the Redash key file; create the approval markers one at a time; enable one timer first (Salesforce).
4. **Acceptance check:** the VM queue matches the desktop queue (a new CO such as CO-0780 appears within one interval) and the banner age stays under 15 minutes. Compare counts only, not values.
5. Enable the Redash timer; confirm `/prod/tenants` on the VM shows a clone younger than 2 hours.
6. Retire the manual snapshot copy for the queue.

**Rollback:** `systemctl disable --now surface-onboarding-sf-refresh.timer surface-onboarding-redash-collect.timer`, remove the two markers; the dashboard keeps serving the last snapshot.

## 7. Decisions we need back

| # | From | Decision |
| --- | --- | --- |
| V1 | Salesforce admin, SecOps | Approve the read scope in section 2 (supersedes the single-record approval for CO-0717); decide the `Onboarding_Comments__c` question |
| V2 | Owner | Refresh interval (proposal 10 minutes) |
| V3 | Redash owner, SecOps | Key arrangement in section 3 and the outbound allow-list |
| V6 | SecOps, Identity | OneLogin application, firewall rule for 8443, certificate (docs/40 section 7) |
| V4, V5 | Owner, SecOps | MFA approach for browser runs, and who may approve summaries on the VM (later phase; not needed for this request) |

Contacts and ticket numbers: `<to be filled in by the owner when sending>`.
