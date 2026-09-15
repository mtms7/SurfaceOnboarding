# Local Implementation Status

**Updated:** 2026-09-12  
**Scope:** local repository scaffold, VM staging history, and a desktop-only,
attended Salesforce queue preview. No Salesforce update, Leonardo, VM,
Workato, OPA, or production action was performed by the dashboard work.

## Implemented and locally verified

- Strict metadata-only workflow, sync-job, audit-event, and queue contracts.
- State-transition, source-revision, idempotency, and single-flight safeguards.
- Six visible route registrations, all disabled with `mapping_not_approved`.
- Manual-review threshold policy for counts greater than 60.
- Local role checks for metadata-only sync requests; no worker or client exists.
- Fixed-scope synthetic source-reference catalog and end-to-end local intake
  flow with idempotency, revision-restart, and same-revision conflict guards.
- Fail-closed source-read outcome contract for authentication, timeout,
  transport, invalid-JSON, schema-drift, and ambiguity cases.
- Strict in-memory source-schema parser that reduces synthetic JSON only to
  identifier/revision metadata and retains neither raw input nor parse errors.
- CLI error boundary that emits fixed rejection codes rather than exposing
  internal source, adapter, authorization, or parser exception details.
- Caller-configured in-memory sync rate limiter that blocks before job or audit
  creation; operational thresholds remain an owner/deployment decision.
- Pure configured-time poll planner; it does not install a timer or execute work.
- Reviewer-only manual-review decisions bound to an exact record, revision, and
  intent hash; disabled mappings remain unreleasable. An in-memory repository
  preserves one immutable decision per snapshot and audits only the first save.
- Process-local CLI commands: `queue`, `audit`, `readiness`, and `sync`.
- Offline readiness report with owner approvals defaulting to not approved.
- Unexecuted PostgreSQL schema draft (including metadata-only manual-review
  decisions) and non-deployable FastAPI skeleton.
- FastAPI factory guard that rejects construction before web-identity approval;
  no listener can initialize in the current configuration.
- Exact Development-only origin policy for a future Leonardo adapter; it rejects
  production, redirects, paths, ports, and lookalike hosts without I/O.
- Decision-only execution gate requiring ready state, mapping approval, exact
  Development origin, and authentication approval before it can return allowed.
- Disabled Salesforce writeback boundary that rejects every request pending
  owner-approved field mapping and separate write authority.
- CI checks for tests, sensitive artifact names/types, and forbidden network or
  process-execution capability in `integration/onboarding`.
- Pinned, non-deploying host profile for `workato-opa-01` on Ubuntu 22.04,
  requiring loopback-only web binding.
- Guarded systemd preparation templates for a future web process and poller;
  both require an approval marker, and the timer has no schedule or enablement
  instruction. The colocated direct service is explicitly isolated from OPA.

The latest local verification ran 67 integration tests and 145 repository
tests, plus compilation, artifact, and offline-boundary checks.

## Deliberate constraints

- No source payload, customer content, credentials, MFA material, browser
  state, raw response, endpoint, or production target is stored in this
  scaffold.
- All mappings remain disabled. An enabled mapping requires an
  `approved-` version and cannot retain a disabled reason.
- The only accepted target configuration is Leonardo Development, but no
  Leonardo adapter or authentication path is implemented.
- The local FastAPI module must not be exposed: individual identity, TLS,
  reverse-proxy, CSRF, and pilot-group approvals remain unresolved.

## Salesforce source-gate evidence

On 2026-09-10, a bounded, read-only query for `CO-0717` returned exactly one
`Customer_Onboarding__c` record. The CO number matched; the source record and
its revision were present; and the saved `Primary_User__c` lookup resolved
consistently to exactly one Contact with a revision. No identifiers, personal
data, raw revisions, or payload values were retained. No Salesforce write was
performed.

This is source-shape evidence only. It does not establish owner intent for the
Contact, approve a mapping, authorize Workato/OPA, or authorize Leonardo or
Salesforce writeback.

The same read-only session reconciled the CO's own `Account__c` lookup to the
DealHub subscription source. A bounded query returned 12 linked subscription
rows, including three active rows. The active rows share one opportunity and
one term, and each has product-identity and record/revision metadata. Quantity
and SVA/service-package values are present only on a subset of those active
rows. No product values, account values, identifiers, dates, or revisions were
retained. This is evidence for a future owner mapping review only; it does not
authorize selection, classification, or any external operation.

## VM preflight evidence

On 2026-09-10, the user ran the supplied read-only preflight in their existing
MobaXterm session. `workato-opa-01` reported Ubuntu 22.04; the existing
`workato-agent.service` was active and enabled; and no listener occupied local
TCP port 8000. The command made no VM change. The direct-integration service
must remain separately isolated from the Workato OPA identity and runtime.

After explicit VM-change authorization, the user created the locked
`surface-onboarding` system account and the empty
`/opt/surface-onboarding/app` and `/var/lib/surface-onboarding` directories.
The account is non-login and both directories are owned by that account with
mode `0750`. No package, application, configuration, service, timer, OPA, or
network change was made.

The subsequent read-only inventory found 101 GiB free on the VM filesystem and
no active PostgreSQL service. Its native Python is 3.10.12, while the current
package metadata requires Python 3.12 or later. The only identified
Python-3.11-plus dependency in the local scaffold is `StrEnum`; no runtime
baseline change has been made pending an explicit deployment decision.

After explicit authorization, Python 3.12.14 was built from an official source
archive whose SHA-256 was verified, then installed with `altinstall` at
`/opt/surface-onboarding/runtime/python-3.12.14`. The OS Python 3.10 and OPA
were not replaced or changed. The installed interpreter passed an import check
for TLS, SQLite, compression, readline, and virtual-environment support. No
application dependency, virtual environment, service, timer, database, or
network listener has been installed or started.

After separate authorization, a fresh virtual environment was created at
`/opt/surface-onboarding/venv` by the `surface-onboarding` account. It uses the
isolated Python 3.12.14 interpreter and its own pip, and is owned by the
service account with mode `0750`. It contains no application dependencies;
no service, timer, database, or listener has been started.

After explicit authorization, the isolated environment received binary-wheel
installations of FastAPI 0.141.1 and Uvicorn 0.52.4 plus their resolved runtime
dependencies. The exact Python-3.12/Linux resolution is recorded in
`requirements.runtime.lock`. The application source has not been copied to the
VM, and no listener or service has been started.

The reviewed scaffold was then staged on `workato-opa-01` under
`/opt/surface-onboarding/app` and ownership was assigned to the service
account. The initial archive omitted the unexecuted migration draft; no runtime
operation occurred, the omission was detected by the VM test suite, and a
checksum-verified corrected archive was staged in its place. The corrected
staging passed all 67 integration tests plus the artifact and offline-boundary
checks using the isolated virtual environment. No database migration, service,
timer, listener, Salesforce operation, Leonardo operation, or OPA operation
has run.

A read-only reverse-proxy inventory found Nginx and Apache inactive, with no
listeners on TCP ports 80, 443, or 8000. This confirms that no onboarding web
application is exposed. Installing or enabling a proxy remains blocked pending
the SSO, TLS, ingress, and five-operator identity decisions.

The offline readiness report now also separates VM/SecOps host-deployment
approval and database/backup/operations approval from the existing web-identity
and external-system gates. Both new gates default to blocked; VM staging does
not make either condition ready.

The refreshed scaffold containing those readiness gates was checksum-verified,
staged on the VM, and retested under the `surface-onboarding` account. All 67
integration tests and both local safety checks passed. It remains an inert
staging installation with no running service or external adapter.

The R4 scaffold adds a strict security baseline and stronger systemd template
confinement: no direct ingress, corporate TLS and SSO/MFA required at a
separately approved proxy, empty Linux capability sets, limited address
families, and protected runtime ownership. Its archive checksum was verified,
it was staged on the VM, and all 67 integration tests plus both safety checks
passed as the isolated service account. It does not authorize any service,
proxy, database, or external connection.

The R5 scaffold additionally carries a review-only reverse-proxy approval
packet. Its checksum was verified, it replaced the inert R4 application staging
while retaining R4 as a rollback directory, and the same 67 tests plus both
safety checks passed. It still contains no proxy configuration and did not
enable a service, listener, database, or external adapter.

R6 adds an offline release archive verifier that requires the exact checksum
sidecar, rejects unsafe tar members and size abuse before privileged extraction,
and requires the core security artifacts. The bootstrap verifier and R6 archive
were independently checksum-verified before extraction; R6 was then staged on
the VM with R5 retained as a rollback directory. All 70 integration tests and
both safety checks passed as the isolated service account. The 148-test full
local suite also passed. No service, listener, proxy, database, or external
adapter was enabled.

The owner states that future internal-team access will be through the RND VPN
in Israel. This is a declared network-intent fact, not a verified ingress
configuration or an authorization approval: corporate SSO/MFA, exact VPN source
ranges, the five named operators, and application role controls remain pending.

R8 carries this RND-VPN boundary clarification and the validation-only,
time-bounded web-activation attestation contract. Its archive and bootstrap
verifier were independently checksum-verified before extraction; it was staged
with R6 retained as rollback content. All 75 integration tests and both safety
checks passed as the isolated service account. No service, listener, proxy,
database, firewall rule, or external adapter was enabled.

## Controlled read-only CO-0717 test — 2026-09-10

A fresh, fixed-field Salesforce read returned exactly one `CO-0717` record.
Its source revision, Primary User reference, CO Account reference, and required
classification fields were present; neither external-account identifier field
was populated. The referenced Primary User resolved to exactly one Contact with
a revision, first/last-name and email fields present, and no plus alias in its
email address. No raw value, identifier, revision, name, email, or account
value was retained.

The Contact's Account reference did **not** equal the CO's `Account__c`
reference. This was recorded only as a diagnostic comparison. The existing
owner-mapping evidence defines `Primary_User__c` itself as the authoritative
exact-Contact relationship and explicitly forbids choosing an arbitrary
Account Contact; it does not require the referenced Contact to have the same
Account reference as the CO. The owner confirmed this is intentional for the
test CO. Future intake must bind the exact lookup and both revisions, not add
an undocumented same-Account requirement. The test made no Salesforce,
DealHub, Leonardo, OPA, VM, or service change.

## External gates — still pending

1. Salesforce owner/SecOps approves a read-only service identity, scope, and
   secure token handling.
2. Owners approve all six versioned field/toggle/entitlement mappings.
3. Leonardo Product/Engineering and SecOps approve an MFA-preserving
   authentication design and a read-only test scope.
4. IT/SecOps approves individual web identities and the five-user pilot.
5. Operations selects the real twice-daily schedule and deployment posture.

`CO-0717` has not been used as a fixture or changed by this work.

## Safe next local work

- Add unit tests for synthetic source-reference contracts and schema-drift
  failure categories.
- Add a database repository implementation only after PostgreSQL deployment
  authority is granted; do not run the migration beforehand.
- Add a real Salesforce read adapter only after the read-only identity and
  exact fixed-query contract are approved.
- Install nothing on `workato-opa-01` until VM/SecOps, SSO, service identity,
  secret-provider, PostgreSQL, backup/monitoring, and schedule decisions are
  recorded; the local deployment templates do not authorize those changes.

## Desktop attended queue preview — 2026-09-11

A separate desktop-only preview was added at
`tools/serve_attended_open_onboardings_dashboard.py`. It binds only to
`127.0.0.1:8012`; it has no scheduler, database, cache, VM transport,
Leonardo adapter, mutation endpoint, or generic-query input. Each page request
uses the operator's existing Salesforce CLI session, holds the result only in
memory for rendering, suppresses request logging, and returns `no-store` and
frame-denial security headers.

The preview uses the fixed Salesforce `Open_Onboardings` list-view scope. Its
compact, clickable queue cards show the CO reference, source readiness,
submission date, priority group, approval/stage/product/type/account, and a
safe Deal subscription start/end summary derived transiently from Onboarding
Comments. Raw comments are not repeated in the queue. The CO detail view
retains the requested Salesforce review fields and reports the source rule
separately from the Leonardo execution gate.

The source-ready rule for the preview is now explicitly owner-defined:
`Onboarding_Approval_Status__c == Approved` displays **Source ready to
onboard**; any other value displays **Source not ready**. A date-range parser
also validates a single `YYYY-MM-DD - YYYY-MM-DD` subscription period from the
comment without retaining its source text. A trailing non-date marker such as
`TEST` is accepted. This date validation is supporting scheduling evidence;
it does not change the owner-defined approval-status readiness label.

On 2026-09-11, a bounded attended read verified that `CO-0717` has one current
source record, has the owner-defined approved status, and has a comment that
passes the single-date-range parser. No raw comment, customer value, token,
cookie, credential, or Salesforce result was written to a file or normal log.
The preview renderer, comment-date tests (including the non-date suffix case),
and local loopback endpoint checks passed.

For a source-ready CO, the detail view now displays a **Start manual
onboarding** control. It is intentionally disabled and has no request handler,
Leonardo navigation, browser action, or persistence effect. Its visible blocker
states the required mapping, duplicate/authority, and idempotency checks. The
control is absent for COs that are not source ready. This provides the future
operator workflow affordance without allowing the dashboard to bypass an
external execution gate.

For a source-ready CO, the desktop-only dashboard now also displays an
operator-initiated **Check Leonardo Development session** control. It opens
only the exact Development tenant-management route after the operator clicks
it: the browser reaching that route is the attended session signal, while an
SSO redirect shows that authentication is still required. The dashboard retains
no credential, MFA code, cookie, or browser state. A successful browser launch
is not clearance of any mapping, authority, duplicate, idempotency,
confirmation, or readback gate.

The local regression suite (106 tests), integration artifact check, and
offline-boundary check passed after this attended-only change. The browser
launch was not invoked during verification.

The local `docs/33_CASE3_MAPPING_WORKSHEET_2026-09-12.md` records a
schema-only Case 3 decision matrix derived from the verified Surface-only and
Credential-Exposure-only Guru guides. It keeps the route disabled pending
named owner approval, a fresh source revision, duplicate review, idempotency,
and one-run confirmation.

The worksheet also records the owner's 2026-09-12 attestation that the selected
Pentera Core Plus Commercial entitlement grants Credential Exposure for the
Case 3 route with exactly one approved Email Domains value. The supporting
license-tier Guru card remains visibly unverified, so this is recorded as an
owner decision rather than Guru verification and fails closed on source or
subscription change.

The worksheet additionally records the owner's 2026-09-12 Case 3 decisions:
Leaked Credentials enabled for the one approved Email Domains value with a
weekly interval; distinct Main-plus-Alternative-domain counting; source and
DealHub date equality; and the combined Surface/CE toggle precedence. It keeps
every unspecified Leonardo setting, license enum, quantity limit, and
primary-user binding blocked for an explicit decision.

The owner subsequently resolved the remaining Case 3 account and control
decisions: Prepaid annual subscription; 10,000 assets; three account domains;
500 subdomains plus explicitly approved add-ons; and the documented combined
Surface/CE advanced-toggle matrix. The single CE email-domain entitlement
remains distinct from the three-domain Case 3 account limit. Exact primary-user
binding, current-source revalidation, idempotency, and a separate fill-and-pause
approval remain required.

This preview is not the deployable application and must not be copied to the
VM or exposed to the network. In particular, its desktop Salesforce CLI
session is attended and cannot satisfy the planned service-identity or
twice-daily polling requirement.

## Leonardo Development creation gate — current state

The owner's request to onboard `CO-0717` in Leonardo Development is a request
for a future external mutation, not evidence that the required execution gates
have passed. Current local evidence does **not** establish an approved route
mapping, Leonardo authentication/authority, duplicate result, idempotency
binding, form-schema verification, or post-create readback contract. The
current v5 Case 3 planner remains deliberately validate-only and reports the
mapping as execution-unapproved; it has no create capability.

Before any Leonardo Development create, the operator must complete and record
the following in order: a fresh matching Salesforce source revision; an
owner-approved mapping for the exact route and settings; an attended,
MFA-preserving Leonardo Development authentication and authority check; a
zero-result duplicate check; an intent hash and single-flight idempotency key;
and a named one-run human confirmation after fill-and-pause. The create must
then be followed by exact Development readback. Salesforce writeback remains a
separate, unapproved action.

On 2026-09-11, an operator-attended, read-only browser preflight reached the
exact Leonardo Development origin and identified its login page. No active
Leonardo session was available. No credential was entered or retained; no MFA
prompt was completed; and no Leonardo lookup, form interaction, create,
update, or Salesforce write occurred. The next permitted attended step is for
the operator to complete SSO/MFA directly, followed only by a read-only
identity/authority and duplicate preflight after the mapping gate is resolved.

The operator then completed SSO/MFA directly. A subsequent attended,
read-only inspection verified the Development tenant-management view, including
its accessible tenant-search control, account-add entry point, tenant-table
schema, and pagination controls. No customer-derived query was entered, no
search result was evaluated, no account form was opened, and no mutation,
download, screenshot, trace, HAR, raw response, or browser credential state
was retained. The observed accessible control names are discovery evidence for
a future guarded Playwright adapter only; they are not a versioned mapping or
an authorization to automate the UI.

## Leonardo Development duplicate preflight — 2026-09-11

The local, transient-only duplicate policy is implemented in
`integration/onboarding/tenant_duplicate_policy.py`. It has no Leonardo client,
persistence, or logging. The required attended lookup order is: exact canonical
main domain; full company name; then an accent-normalized full-name variation.
An exact domain or normalized full-name result blocks creation. A meaningful
partial name or an initialism (for example, a shortened historical tenant
name) is ambiguous and requires human review; it can never automatically clear
or confirm a create. A no-result search only clears that individual search
step, not the separate mapping, authority, idempotency, confirmation, or
post-create-readback gates.

On 2026-09-11, the authenticated operator performed those three read-only
searches for the current `CO-0717` source. Each returned no candidate. No
tenant row, customer value beyond the operator-supplied search terms,
credential, screenshot, trace, HAR, raw response, or browser state was saved.
No account form was opened and no Leonardo or Salesforce mutation occurred.

The local policy regression tests were added but could not be executed on this
desktop because neither a Python executable nor Python launcher is installed
or available on `PATH`. They remain required in the configured project/VM
runtime before this policy is treated as release-ready.

The policy now aggregates only redacted outcome labels. It returns
`no_candidate` only when the exact-domain, full-name, and normalized-name
searches all completed and no candidate disposition was returned. An incomplete
search, malformed result, partial name, or initialism remains
`manual_review`; an exact domain or normalized full-name match remains
`block_create`.

## CO-0717 Primary User revalidation — 2026-09-12

With the operator's explicit approval, a bounded Salesforce read revalidated
the current `CO-0717` source identity without retaining raw values. It returned
exactly one matching CO record, a present source revision and Primary User
reference, and exactly one resolved Primary User Contact with a revision,
required name/email fields, and no pre-existing plus alias. The check emitted
only these redacted outcomes and made no Salesforce, Leonardo, DealHub,
Workato, OPA, or VM change. It is not a create authorization and does not
clear the remaining commercial/date binding, operator, idempotency, and
separately named fill-and-pause gates.

## CO-0717 operator sequencing decision — 2026-09-12

The owner directed that no operator be created or selected during the initial
Case 3 tenant preparation. Operator creation is deferred until the tenant has
completed its first scan successfully and valid results have been confirmed.
This supersedes the historical Surface guide's earlier TA/CSM operator step
for this controlled onboarding. It neither authorizes tenant creation nor the
later operator action; each remains separately gated.

## CO-0717 attended commercial-term review — 2026-09-12

The operator-approved, read-only Salesforce view showed the current related
DealHub bundle as three current rows on a single opportunity with a shared
term. The Surface Go 500-subdomain tier and Core Plus Commercial were visible,
and the subscription dates agreed with the parsed date range in Onboarding
Comments after ignoring its test suffix. This validates the visible term and
tier facts only. The related-list UI did not show subscription revision
metadata, so selected record identity/revision binding remains a separate
fresh gate. No source, Leonardo, DealHub, Workato, OPA, or VM change occurred.

Later on 2026-09-12, the operator approved a bounded DealHub Subscription
metadata read. Its redacted outcome returned 12 CO-linked rows and three
current rows; the current rows shared one opportunity and the approved term,
contained exactly one Surface Go 500-subdomain baseline and Core Plus
Commercial, and had no additional current Surface add-on. Selected rows all
had a record identity and revision. No raw identifier, revision, product,
date, or account value was retained. This clears the source-read portion of
subscription identity/revision binding only; an intent hash and idempotency key
are still required before any form interaction.

## Case 3 untouched-control and asset decisions — 2026-09-12

The owner clarified that the default asset limit for every new Surface account
is 10,000 and must not be derived from the Core endpoint quantity. Web Agent
and SpyCloud are excluded from this controlled onboarding: no action may set,
enable, or disable either control until the owner explicitly updates that
instruction. The local Phase 2 draft contract was updated with focused
regressions: it now emits 10,000 assets for new Surface intake and omits
SpyCloud entirely. Web Agent is likewise absent from the contract. The
contract remains execution-blocked; no execution manifest or idempotency key
was issued.

## CO-0717 non-executable candidate intent — 2026-09-12

A fresh, bounded Salesforce re-read was processed only in memory into the
local Case 3 candidate contract. The candidate returned the reviewed-intent
hash `2116236116959afe7acd3f465ce659debe0e92bea4e87b09792f7834153f492a`
and redacted source-evidence hash
`6c932d0bbe35c522a87df4618eb762dbf86a72d24732492d2b6b4c49dc4e2fc7`.
It validated the owner-approved 10,000 asset, three-domain, and 500-subdomain
limits; deferred operator; and omitted the untouched controls. It remains
non-executable: no approval attestation, idempotency key, Leonardo form fill,
or create action occurred. Any relevant source revision change invalidates it.

## Attended manual-start control — 2026-09-12

The loopback-only dashboard now enables **Start manual onboarding** only after
all of these local conditions are met: the CO remains source-ready; the
operator runs the exact Leonardo Development session check; and the resulting
15-minute, one-time acknowledgement still matches the current source revision.
The operator must then explicitly attest that their Leonardo Development admin
session is active. The control consumes its acknowledgement and opens only the
exact tenant-management route. It cannot inspect VPN, cookies, credentials, or
roles, and it cannot fill, submit, create, approve, or write back anything.
Source drift, expiration, missing attestation, replay, or browser-launch
failure fail closed.
