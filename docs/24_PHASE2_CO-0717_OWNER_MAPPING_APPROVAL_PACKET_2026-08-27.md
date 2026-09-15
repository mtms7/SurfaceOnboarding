# Phase 2 CO-0717 owner mapping approval packet

Date: 2026-08-27  
Target: Leonardo Development only  
Status: **owner mapping decisions required; `proceed=false`; no Leonardo activity authorized**

## Purpose

Use this packet to record the Commercial/DealHub and CSE/Surface decisions
needed to convert the successful masked CO-0717 Workato selector test into a
versioned, local-only Case 3 mapping. Approval of this packet does not authorize
a Leonardo form fill, submit, account create, Salesforce writeback, production
access, or an unattended browser run.

## Evidence available for review

### Directly verified

- Exactly one CO-0717 source record exists; it is Approved and in stage `New`.
- The CO requests new Surface and Credential Exposure and has no existing
  Surface Account ID or Account UUID.
- Three active DealHub rows share one opportunity and one term. The relevant
  product classes are Surface Go, Core Plus Commercial with SVA Essentials,
  and remaining usage value. Nine other rows are expired.
- The restricted intake contains two distinct registrable-root Credential
  Exposure domain candidates. Current guidance grants one such domain with
  Core+ and requires an add-on for each additional domain; no active
  additional-domain add-on has been authoritatively identified in the selected
  bundle. The raw domain values are intentionally omitted here.
- The dedicated quantity fields are null for the Surface and Core license rows.
- Salesforce metadata confirms `Primary_User__c` is a Contact lookup with
  relationship name `Primary_User__r`. Workato may therefore retrieve only the
  exact referenced Contact in a masked read; it must not choose an arbitrary
  Contact from the Account.
- Workato Development job `j-AbN99CCz-mQc384-CD`, recipe version `31`, completed
  successfully with the trigger, Salesforce search, DealHub search, and Ruby
  classifier masked. Every selected non-null subscription output now requires
  its source Record ID and SystemModstamp; neither raw value is retained here.
  No external write occurred.
- The one-time webhook deduplication header protected only that transport
  submission; it is not a durable execution idempotency lock.
- The recipe remains inactive; the available action is **Start recipe**.
- A live read-only Leonardo Development re-verification opened the blank Add
  Account form and both Advanced options sections, entered nothing, selected no
  `Confirm`, and closed with `Cancel`.

### Historical or unverified guidance

`Surface Customer Onboarding - Settings and Toggles.txt` contains useful local
candidate defaults, but it has no Guru verification state or last-modified
metadata. The configured read-only Guru connector was unavailable on
2026-08-27, so this file must not be treated as current authoritative approval.
The owner decisions below must either cite a current verified Guru card or
explicitly supersede the local candidate guidance.

No customer name, contact, domain, opportunity identifier, subscription
identifier, credential, token, MFA code, cookie, or webhook address belongs in
this packet.

## Commercial and DealHub decisions

Complete every row. `Pending`, blank, or conditional answers keep the request
blocked.

| Decision | Verified evidence | Required owner response |
| --- | --- | --- |
| One-bundle rule | Current Surface and Core rows share one opportunity and term. | Approve or reject treating one current Surface row plus one current Core row plus explicit add-ons on the same opportunity/term as one Case 3 bundle. |
| Remaining usage value | A current row exists but is not an obvious Leonardo license. | Approve exclusion from Leonardo license selection, or provide its exact supported treatment. |
| Surface quantity | Product text identifies a 500-subdomain tier; the dedicated quantity field is null. | Identify the authoritative numeric field or approve a versioned product-code-to-quantity mapping. Do not approve inference from free text alone. |
| Core quantity | Product text identifies a 500-endpoint tier; the dedicated quantity field is null. | Identify the authoritative numeric field or approve a versioned product-code-to-quantity mapping. |
| Core/SVA entitlement | Core Plus Commercial and SVA Essentials are present. | State whether this grants Credential Exposure and list the exact Leonardo module, interval, domain-entitlement, and license effects. |
| Additional CE domain | Two CE root-domain candidates are requested; current guidance provides one with Core+. | Identify and bind the exact active additional-domain add-on, or approve exactly one of the two candidates for CE scanning and treat the other only according to an explicitly approved Surface-domain rule. |
| Supported add-ons | The router currently recognizes a conservative interim marker list. | Supply the exact allowed product codes/names and their Leonardo effects. Unknown current products must continue to reject. |
| Identity and revision binding | Product Code, Opportunity ID, Quantity, and System Modstamp are captured. | Approve the exact fields that bind each selected subscription and require a re-read on revision change. |
| Dates | The current bundle has one shared start/end term. | Confirm those subscription dates, rather than CO submission date or UI defaults, are authoritative for Leonardo. |

Commercial/DealHub owner name: ____________________  
Decision: Approve / Reject / Changes required  
Evidence or mapping version: ____________________  
UTC decision time: ____________________

## Candidate Case 3 Leonardo mapping decisions

The visible Leonardo Development form fields below were verified read-only.
Values in the **Candidate for owner review** column come from local historical
guidance or security invariants; none is an approved business default until the
named owner records a decision.

### Account and primary user

| Leonardo field | Candidate for owner review | Required source/decision |
| --- | --- | --- |
| Company name | Exact Salesforce company name; no CE-only suffix for a combined account | CSE owner confirms combined-account naming rule. |
| Account type | `Customer` | Leonardo/CSE owner confirms enum. |
| Primary domain | Restricted Salesforce/approved intake field | Confirm authoritative field and normalization. |
| Alternate domains | Explicit approved list only | Confirm source and collision behavior. |
| Subdomains | Explicit approved list only | Confirm source and whether recon should discover additional values. |
| User email domains | Explicit approved list only | Confirm source and collision behavior. |
| Networks | Explicit approved list only | Confirm source, format, and collision behavior. |
| Country | Salesforce operating country | Confirm source field and Leonardo enum normalization. |
| Primary-user fields | Exact referenced Contact; Pentera organization mailbox is converted to a customer-specific plus alias | Owner confirmed first/last name and organization-email source; raw values remain restricted and omitted here. |
| Operator account | Nullable; Technical Advisor may be selected when available | Owner confirmed that this field is not mandatory for the Case 3 form fill. |
| Multi-factor authentication | `true` | Non-negotiable for user-based onboarding. |

### Account settings

| Leonardo setting | Historical local candidate | Owner decision required |
| --- | --- | --- |
| Scanning interval | Not specified | Select the exact Leonardo enum. |
| Scan now | Enabled for new Surface | Approve or change for Case 3. |
| Maximum scan duration | `90` hours | Approve or change; visible UI default `24` is not authoritative. |
| Automated discovery | Disabled | Approve or change. |
| Recon subdomains | Enabled | Define exception conditions. |
| Multiple attack stacks | Disabled unless documented thresholds apply | Approve deterministic rule and required source facts. |
| MAS for subdomains | Disabled unless documented thresholds apply | Approve deterministic rule and any numeric threshold. |
| Web dictionary brute force | Enabled | Approve or change. |
| Web dorking | Disabled | Approve or change. |
| Nuclei | Enabled | Approve or change. |
| Authenticated Testing | Disabled | Approve or change. |
| Static outbound IP | Disabled | Approve request/entitlement rule. |
| AI | Disabled | Approve or change. |
| Notifications | Enabled | Approve or change. |
| Multiple users | Enabled for the newer model | Confirm the CO-0717 license model and value. |
| API access | Enabled | Approve or change. |

Current UI defaults are evidence of why explicit owner mapping is required,
not values to inherit. The live re-verification found these material conflicts
with the historical candidates: maximum scan duration `24` rather than `90`,
Automated discovery enabled rather than disabled, Nuclei disabled rather than
enabled, Leaked Credentials disabled, license type `Evaluation`, and Number of
assets `50000`. None of those UI defaults is authoritative for CO-0717.

### Attack modules and license

| Leonardo field | Historical/local candidate | Owner decision required |
| --- | --- | --- |
| Phishing | Disabled | Approve or change. |
| Leaked Credentials | Enabled only for approved CE entitlement | Confirm that the selected Core/SVA evidence grants this module. |
| Leaked Credentials interval | Weekly | Approve exact enum. |
| Leaked Credentials domains | Explicit entitled email-domain list | Define authoritative restricted source and entitlement-count rule. |
| SpyCloud | Visible as enabled and non-editable during discovery | Leonardo owner confirms invariant and verification behavior. |
| Include Provisioning | Enabled | Approve or change. |
| Include subdomains | Enabled | Approve or change. |
| License type | Historically generally prepaid annual | Select the exact Leonardo enum from the commercial contract. |
| Number of assets | Unresolved | Approve authoritative Core quantity mapping. |
| Number of domains | Count of distinct registrable roots across primary, alternate, and CE domain inputs | Owner confirmed summation rule; duplicates are counted once and subdomains remain governed by Number of subdomains. |
| Number of subdomains | Unresolved | Approve authoritative Surface quantity mapping. |
| Start/expiration dates | Shared active subscription term | Commercial and CSE owners approve the authoritative date fields. |

CSE/Surface owner name: ____________________  
Decision: Approve / Reject / Changes required  
Mapping/policy version: ____________________  
UTC decision time: ____________________

Owner-provided mapping revision recorded on 2026-08-27 as candidate policy
`surface-case3-owner-2026-08-27-v4`: normalized company naming; deterministic
Pentera plus-address aliasing; nullable Operator Account; and distinct-root
domain summation. This records mapping authority only and does not authorize an
external Workato test or Leonardo action.

Validate-only hardening revision
`surface-case3-partial-owner-mapping-2026-08-31-v5` supersedes v4 only for
policy labeling and fail-closed execution gating. It does not add business-field
approval. The four rules above remain the entire owner-recorded normalization
scope; every unsigned table in this packet remains unresolved.

Leonardo product owner name: ____________________  
Form semantics and enum decision: Approve / Reject / Changes required  
UTC decision time: ____________________

## Required control approvals

| Control | Owner | Approval required before |
| --- | --- | --- |
| Current Guru mapping and card metadata | CSE process owner | Mapping becomes authoritative |
| Supported Leonardo API/service identity or attended-UI exception | Leonardo Engineering/product owner and SecOps | Any Leonardo integration or form activity |
| Duplicate rules for name, primary/alternate/email domains, subdomains, networks, users, and operator accounts | Leonardo owner and CSE owner | Read-only preflight acceptance |
| Separate requester/approver RBAC and at-most-60-minute hash-bound approval | Workato owner and CSE management | `fill_and_pause` release |
| Atomic single-flight idempotency record that blocks pending, submitted, verification-pending, verified, and uncertain attempts | Workato owner | Any execution release |
| MFA, secret storage, browser-runner placement, network policy, evidence retention, and incident handling | SecOps | Development pilot |
| Stable UUID readback, field/toggle verification, and audit visibility | Leonardo owner | Any create approval |
| Salesforce UUID destination field and separate writeback approval | Salesforce owner | Salesforce writeback |
| Production workload identity, environment separation, schemas, rate/retry behavior, monitoring, rollback, and support ownership | Leonardo Engineering, SecOps, Workato owner, Salesforce owner | Production promotion |

## Decision outcomes

- **Approved:** create a versioned local mapping and run only local
  `validate_only` tests. Keep all Workato execution recipes inactive and keep
  `leonardo_request_allowed=false`.
- **Changes required:** update this packet and require a new mapping revision;
  do not reuse an earlier approval hash.
- **Rejected, incomplete, conflicting, or stale:** retain
  `manual_review_required`, `proceed=false`, and no Leonardo activity.

## Next safe sequence after complete signatures

1. Retrieve and record current Guru card title, verification state, and
   last-modified metadata through the minimum-scope read-only connector.
2. Encode the signed mapping as a strict, versioned local contract; add tests
   for missing values, unknown add-ons, quantity ambiguity, revision drift,
   toggle dependencies, and production-target rejection.
3. Re-read the restricted source, bind the selected subscription identities and
   revisions, build a sanitized CO-0717 reviewed-intent draft, and compute its
   canonical manifest hash locally. Obtain the separate operation approval only
   after that hash exists; then seal the approval revision and deterministic
   idempotency key. This avoids an approval/hash circularity. Do not include raw
   customer values in review artifacts.
4. Obtain separate approval for one Leonardo Development read-only duplicate
   and access preflight. Do not open Add Account or fill fields under that
   approval.
5. Prefer a supported Leonardo API with a short-lived least-privilege workload
   identity. Use an attended UI `fill_and_pause` path only if the Leonardo owner
   and SecOps approve the exception, with MFA preserved and a human confirmation
   immediately before submission.

Stop for any missing or expired approval, source revision change, unknown
product, missing quantity, mismatched term, duplicate/collision, schema or UI
drift, authentication/MFA/WAF/CAPTCHA problem, idempotency lock conflict,
unexpected response, missing UUID, or incomplete read-after-write evidence.
