# Case 3 Mapping Worksheet — New Surface + Credential Exposure

**Status:** draft; execution mapping remains disabled pending named owner approval.

**Scope:** Leonardo Development attended onboarding only. This document contains
field rules and decision slots only; it must not contain customer values,
credentials, MFA material, browser state, raw Salesforce records, or Leonardo
responses.

## Evidence used

- `Surface Customer Onboarding - New Surface Account Only` — Guru **Verified**,
  updated 3 months ago.
- `Surface Customer Onboarding - New Credential Exposure only` — Guru
  **Verified**, updated 3 months ago.
- `Surface & Credential Exposure Onboarding Guide` — Guru **Unverified**,
  updated 4 months ago; used only as supporting context for the Case 3
  combination.

The verified component guides establish the Case 3 baseline: combine the
applicable Surface and Credential Exposure controls. They do not replace the
project's newer approval, idempotency, exact-source, duplicate, and readback
gates.

## Owner-attested CE entitlement decision — 2026-09-12

The automation owner attested that, for the approved Case 3 route, a selected
**Pentera Core Plus Commercial** entitlement grants Credential Exposure. This
decision remains effective for this project until the owner explicitly
supersedes it.

The Credential Exposure scope is exactly one email domain, taken from the
approved Salesforce **Email Domains** value for the reviewed source revision.
Alternative domains and subdomains are Account fields only; they do not expand
the Credential Exposure email-domain entitlement.

The supporting `Surface License Tiers Breakdown` Guru card was visibly
**Unverified** and updated one year ago when read. This entry is therefore an
owner-attested decision, not a claim that the Guru card is verified. It fails
closed if the selected subscription, approved email-domain value, or source
revision changes.

## Owner-attested Case 3 settings decisions — 2026-09-12

For a new combined Surface and Credential Exposure account, the automation
owner decided the following. These decisions remain effective until explicitly
superseded by the owner.

| Area | Owner decision | Source / guard |
| --- | --- | --- |
| Credential Exposure | Enable Leaked Credentials. Use exactly the one approved Email Domains value as the CE scanned-domain value. | Do not store the domain text here. Stop if the bound source revision or selected entitlement changes. |
| CE interval | Weekly. | The CE-only guide's CE setting applies to the combined route. |
| Account domain limit | **3**: count the distinct registrable roots from the approved Main Domain plus approved Alternative Domains only. | This is the Case 3 combined-account rule. The CE scanned email domain and subdomains do not increase this account-domain limit; duplicates count once. |
| Start and expiration | Use the one parsed ISO range in Onboarding Comments only when it exactly matches the selected DealHub subscription start and end dates. A trailing non-date test marker is ignored by the parser. | Stop on missing, malformed, or mismatched dates; never inherit the form defaults. |
| Case 3 precedence | Apply the Surface guide's settings for the Surface portion and the CE guide's Leaked Credentials settings for the CE portion. The CE-only instruction to disable all Surface advanced options and Scan now does not apply to this combined route. | Each final control is recorded below; no UI default is authoritative. |

### Combined toggle matrix

| Control | Case 3 decision | Evidence / condition |
| --- | --- | --- |
| MFA | Enabled | Non-negotiable control. |
| Surface scan interval | Go = monthly; Prime = weekly. | Bind to the selected current tier; stop if tier is ambiguous. |
| Scan now | Enabled | New Surface portion. |
| Automated discovery | Disabled | Surface guide. |
| Nuclei | Enabled | Surface guide. |
| Static outbound IP | Disabled unless the contract explicitly requires it. | Surface guide. |
| Maximum scan duration | 90 hours unless separately approved otherwise. | Surface guide. |
| Leaked Credentials | Enabled | Owner-attested CE decision. |
| Leaked Credentials interval | Weekly | Owner-attested CE decision. |
| Leaked Credentials scanned domains | Exactly one approved Email Domains value. | Owner-attested CE decision. |
| Include Provisioning | Enabled | Both component guides. |
| Include subdomains | Enabled | Both component guides. |
| Recon Subdomains | Enabled | Owner-attested Case 3 decision. |
| Multiple attack stacks | Disabled by default. | An explicit special-case approval is required to enable it. |
| MAS for subdomains | Disabled by default. | An explicit special-case approval is required to enable it. |
| Web dorking | Disabled | Owner-attested Case 3 decision. |
| Authenticated Testing | Disabled | Owner-attested Case 3 decision. |
| AI | Disabled | Owner-attested Case 3 decision. |
| Web Agent | Leave untouched. Do not set, enable, or disable it. | Owner instruction 2026-09-12; excluded until the instruction is explicitly updated. |
| SpyCloud | Leave untouched. Do not set, enable, or disable it. | Owner instruction 2026-09-12; excluded until the instruction is explicitly updated. |
| Web dictionary brute force | Enabled | Owner-attested Case 3 decision. |
| Multiple users | Enabled | Owner-attested Case 3 decision. |
| Notifications | Enabled | Owner-attested Case 3 decision. |
| API access | Enabled | Owner-attested Case 3 decision. |
| Phishing | Disabled | Owner-attested Case 3 decision. |
| License type | Prepaid annual subscription | Owner-attested Case 3 decision. |
| Number of assets | 10,000 | Owner instruction 2026-09-12: default for every new Surface account; do not derive it from Core endpoint quantity. |
| Number of domains | 3 | Owner-attested Case 3 combined-account decision; do not substitute the CE-only one-domain rule. |
| Number of subdomains | 500 plus the sum of any explicitly approved Surface subdomain add-ons. | Stop if add-on entitlement is missing or ambiguous. |

The historical/guide candidate for a user created by the operator does not
override the project's newer exact-primary-user source binding. The exact
source Contact and its revision must be re-read and approved before any fill.

## Mandatory pre-create gates

All gates must pass for the same fresh source revision before a create is even
eligible for one-run confirmation.

| Gate | Required outcome | Owner / evidence |
| --- | --- | --- |
| Source review | Exact CO and subscription revision; active entitlement; matching country, domains, license, limits, dates, and approved status | Salesforce / Commercial owner |
| Duplicate review | No exact canonical-primary-domain match, full-company-name match, or normalized-full-name match | Attended Leonardo Development search |
| Ambiguity | Partial name, initialism, malformed result, incomplete search, or any mismatch stops for manual review | Operator + mapping owner |
| Mapping | Every field below has an approved source and decision | Surface / Product / Commercial owner |
| Identity | Attended Leonardo Development session and required authority available | Operator; MFA remains enabled |
| Idempotency | Exact source revision and reviewed intent have one single-flight key | Operator |
| Create | Named one-run confirmation after form review | Named approver |

No search result or browser launch clears another gate. A Salesforce writeback
is a separate unapproved action.

## Combined Case 3 baseline

| Leonardo area | Rule | Decision still required |
| --- | --- | --- |
| Account type | `Customer` | None, unless owner records an exception. |
| Company, country, primary domain | Use the exact approved Salesforce values. Primary domain must be valid and not a subdomain. | Source-field mapping and revision. |
| Alternate domains, subdomains, email domains, networks | Include only values explicitly approved from Salesforce / contract. | Exact inclusion list. |
| Primary user | Do not choose a fallback contact. Bind the exact approved primary-user source record and revision. | Current owner approval, because historical Guru guidance describes an operator-created user rather than the project's exact-source relationship. |
| MFA | Enabled. | None; disabling MFA is prohibited. |
| Operator account | Do not create or select an operator during the initial account fill. Defer operator creation until the tenant's first scan completes successfully and valid results are confirmed. | Owner decision recorded 2026-09-12; a later, separately approved post-scan action remains required. |
| Surface scanning | Prime = weekly; Go = monthly; older-license variants require owner confirmation. Enable `Scan now` for the Surface portion. | License-family classification. |
| Surface advanced settings | Automated discovery off; Nuclei on; static outbound IP only when the contract explicitly requires it; maximum scan duration defaults to 90 hours unless approved otherwise. | Exact field names and exception values from current form / contract. |
| Credential Exposure | Enable Leaked Credentials; CE scanning interval = weekly; scan exactly the one approved source Email Domains value. | Product approval for any CE domain beyond that one licensed scope. |
| CE-vs-Surface toggle conflict | The CE-only guide disables Surface controls, but Case 3 is not CE-only. Apply the Surface baseline above and record every final toggle explicitly. | Owner-approved combined toggle matrix. |
| License | Include Provisioning on; Include subdomains on; use the approved license type; assets, domains, subdomains, start date, and expiration date come from the approved source. | Licensed domain count and all non-default limits/dates. |

## Fresh source identity revalidation — 2026-09-12

Following the operator's explicit approval, a bounded read-only Salesforce
query returned redacted pass/fail results only. It confirmed exactly one
`CO-0717` record, a present source revision, a present exact Primary User
reference, exactly one resolved contact, a present contact revision, populated
required contact fields, and an email without a pre-existing plus alias. No
identifiers, names, mailbox, account value, or raw revision was retained.

This clears only the source-identity portion of the pre-create gates for that
observed revision. It does not bind the commercial subscription/date evidence,
issue an idempotency key, fill a Leonardo form, create an account, or authorize
Salesforce writeback. Operator creation is intentionally deferred until after a
successful first scan under the owner's 2026-09-12 decision.

## Commercial-term review — 2026-09-12

In the attended, read-only CO source view, the current DealHub related list
showed three current rows on one opportunity with one matching subscription
term. The reviewed product labels included the approved Surface Go
500-subdomain tier and Pentera Core Plus Commercial. The start and end dates
matched the one parsed ISO date range in Onboarding Comments, excluding its
trailing test marker. The owner previously confirmed that Core Plus Commercial
grants CE for this controlled Case 3 onboarding and that the Surface tier
supplies the 500-subdomain baseline.

The related-list view does not expose subscription revision metadata. Therefore
this observation confirms the visible commercial term and tier facts but does
not freshly bind the selected subscription identities and revisions. A bounded
metadata read is still required before the manifest can become fill-eligible.

### Subscription identity/revision revalidation — 2026-09-12

With the operator's explicit approval, a bounded read-only Salesforce query
returned only aggregate outcomes for the CO-linked DealHub subscriptions. It
found 12 linked rows, three current rows on one opportunity, a shared approved
term, one Surface Go 500-subdomain baseline row, one Core Plus Commercial row,
and no additional current Surface add-on. Every selected current row had a
present record identity and revision. Raw IDs, revisions, product strings,
dates, and account values were not retained.

The values must be held transiently and incorporated into the reviewed intent
and single-flight key at manifest preparation. This evidence clears the
fresh-subscription identity/revision *read* gate, but does not itself create an
idempotency key, authorize a form fill, or authorize tenant creation.

## Local draft-contract alignment — 2026-09-12

The local Case 3 draft contract and its focused tests now encode the owner's
10,000-asset default for new Surface accounts. SpyCloud and Web Agent are not
contract fields, ensuring a future runner cannot set either control while they
remain explicitly out of scope. The contract retains its existing execution
blocks and does not authorize a manifest, browser fill, or tenant creation.

## Candidate reviewed intent — 2026-09-12

A fresh bounded source re-read and in-memory local preparation produced a
non-executable candidate reviewed-intent hash:
`2116236116959afe7acd3f465ce659debe0e92bea4e87b09792f7834153f492a`.
Its redacted source-evidence hash is
`6c932d0bbe35c522a87df4618eb762dbf86a72d24732492d2b6b4c49dc4e2fc7`.

The candidate validates the approved limits of 10,000 assets, three domains,
and 500 subdomains; records the deferred operator; and omits the untouched
controls. It is invalidated by any source or selected-subscription revision
change. No approval attestation or idempotency key was created, and it cannot
be used to fill or create a Leonardo tenant.

## Readback contract after a separately confirmed create

Before any downstream action, read back and compare the created Development
tenant's account type, primary and approved secondary domains, country, final
toggle states, scan settings, license type and limits, dates, primary-user
binding, and account UUID. Any mismatch stops the flow for reconciliation.

The historical Guru workflow mentions writing identifiers and stages back to
Salesforce. This worksheet does **not** authorize that writeback; it remains a
separate project gate.

## Approval record

| Item | Required value |
| --- | --- |
| Mapping version | `approved-…` version assigned by owner |
| Exact source revision | Recorded at review time, without storing raw payloads here |
| Mapping owners | Surface, Product, Commercial / DealHub, and Salesforce as applicable |
| Approver and time | Named one-run approver and timestamp |
| Intent hash / idempotency key | Redacted correlation only |
| Create result | Readback outcome category only |

## Non-authorizing fill-and-pause approval packet — 2026-09-12

This is a preparation template only. Blank fields mean **not approved**. It
does not issue an idempotency key, make an external action callable, or permit
submit/create.

| Approval field | Required value |
| --- | --- |
| Requested operation | `fill_and_pause` only; stop before Confirm/submit |
| Target | Leonardo Development only |
| Bound reviewed-intent hash | `2116236116959afe7acd3f465ce659debe0e92bea4e87b09792f7834153f492a` |
| Requester | **Blank — required** |
| Independent approver | **Blank — required; must differ from requester** |
| Approver group / authority | **Blank — required** |
| Approval revision | **Blank — required positive integer** |
| Attestation / request key | **Blank — required** |
| Approval time (UTC) | **Blank — required** |
| Expiry time (UTC) | **Blank — required; no more than 60 minutes after approval** |
| Source and subscription re-read | Reconfirm immediately before use; any revision change invalidates this packet. |
| Scope exclusions | Operator deferred; Web Agent and SpyCloud untouched; no Salesforce writeback. |

## Attended dashboard start control — 2026-09-12

For a source-ready CO, the loopback dashboard may enable **Start manual
onboarding** only after the operator has opened the exact Leonardo Development
tenant-management route and explicitly attested that the active session has
admin authority. The local acknowledgement is bound to the current source
revision, expires after 15 minutes, and is consumed once. Starting opens only
tenant management; the operator manually selects Add Account. It does not
authorize, populate, submit, create, read back, or write back a tenant.
