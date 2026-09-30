# Case 1 (New Surface Account Only) — Guru-derived notes, 2026-09-29

Source: two Verified Guru cards, read in the operator's authenticated Chrome on 2026-09-29
("Surface Customer Onboarding - New Surface Account Only", updated ~4 months earlier; and
"Surface & Credential Exposure Onboarding Guide", updated ~4 months earlier). This is a
summary for implementation planning, not an authorization. Owner answers to the open
questions below are still required before the route is built.

## Before creating (guide)

- Validate the Salesforce request against the opportunity: customer name, country,
  domain(s), license type, expiration date, number of subdomains. Mismatch or missing
  data → reject and ask the creator to resubmit.
- The subscription must be **active**; note when it takes effect and when it expires.
- Product rows (DealHub): newer model e.g. `Pentera Surface Go - 500 Subdomains`
  (a `Pentera Core Plus Commercial` row means CE is also purchased → Case 3, not Case 1);
  older model e.g. `Pentera Surface Software - Essential - 150 Sub-Domains & 1 Domains`.
- Pending approval → move to Approved before creating. Salesforce is the source of truth
  for domain, country, license limits, expiration, CSM, TA, primary user.
- The primary domain must be valid and **not a subdomain**.

## Add Account form (Case 1)

| Area | Guru value |
| --- | --- |
| Company name | Salesforce account name (example shows no suffix) |
| Account Type | Customer |
| Company primary domain | from Salesforce |
| Alternate Domains / SubDomains / Networks | from Salesforce **if provided** |
| User email domains | `pentera.io` at creation |
| Country | from Salesforce |
| MFA | ON, always |
| Primary user | operator's own account, email `first.last+companyname@pentera.io`; phone/job title blank |
| Operator Account | assign based on the relevant **TA and CSM** from Salesforce |
| Scanning interval | Prime = Weekly, Go = Monthly (older model: Enterprise = Weekly, Essentials/Professional = Monthly) |
| Scan now | **ON** for a new Surface (guide, Case 1) |
| Advanced: Maximum scan duration | 90 hours unless instructed otherwise |
| Advanced toggles | Automated discovery OFF, Recon Subdomains ON, Multiple attack stacks OFF, MAS for subdomains (disabled), Web dictionary brute force ON, Web dorking OFF, Nuclei ON, Authenticated Testing OFF, Static outbound IP ON only if Salesforce/contract explicitly requests it, AI OFF |
| Notifications / Multiple users / API access | ON |
| Leaked Credentials | guide: "Leaked Credentials remain" (CE not in scope) |
| License | Include Provisioning ON, Include subdomains ON, Type = Prepaid annual subscription; assets = default unless the license says otherwise (example 10000); domains/subdomains and expiration from Salesforce (example 10000 / 500) |

## After creating

1. Save, search the tenant, open details; update Salesforce with the UID and UUID; move the
   request to **Account Scanning** and mark it the current onboarding stage.
2. Scans take ≥3 hours: follow up the next day to confirm the scan completed and produced
   valid results (subdomains, vulnerabilities, collected data).
3. Then create the primary (customer) user: edit the account to add the customer's email
   domain to User email domains (e.g. `pentera.io, customer.tld`), then create a new admin
   user in tenant user management.
4. Move the Salesforce request to **User Created** and mark it the current onboarding stage.

## Surface License Tiers Breakdown (Guru card, **Unverified**, updated ~1 year earlier)

- Naming update: the old Surface tiers **Essentials** and **Enterprise** are now **Surface Go**
  and **Surface Prime** respectively.
- Surface Go = 500 subdomains, Surface Prime = 1,000 subdomains; domains and users no limit.
- Scan frequency: Go = Monthly, Prime = Weekly (discovery). Provisioning (the attack itself):
  Go weekly, Prime daily.
- **API** and **Credential Exposure (Threat Intel)** "depend on Core Plus" for both tiers.
  Pentera Core Plus is provisioned with a single CE email domain; more are an add-on.

## Owner decisions recorded 2026-09-29 (operator)

- Scan now ON in Dev, with a dashboard reminder to turn scanning off later.
- Operator Account skipped in Dev. Leaked Credentials OFF, Phishing OFF (CE enabled later for Surface + CE).
- Assets 10,000 unless the licence says otherwise. Number of domains = main + alternate domains.
  Subdomains = number in the product name (Go 500, Prime 1,000) + add-on rows.
- License dates: same rule as CE (start = run day; expiration = min(start + 1 year − 1 day, subscription end)).
- Pending Surface subscriptions count when they start within 14 days.
- A Core Plus baseline row (Commercial or Enterprise) does not block: create the Surface tenant
  and show a reminder to enable CE later.
- Every Surface Go / Prime CO needs a revision-bound manual scope review before Start Onboarding.
- First version: create + readback, then the scan-status sweep. Salesforce updates and
  customer-user creation stay manual.

## Live Add Account form defaults (no-submit probe, 2026-09-29)

Observed with Advanced options expanded; the form was cancelled, nothing created.

- **Maximum scan Duration (hours)**: number input, only visible after expanding Advanced
  options, no name/data-am, **default 24** (Guru wants 90 → must be set).
- **Number of subdomains**: number input, **default 50000** (must be overwritten).
  Number of assets / Number of domains: empty text inputs.
- Toggle defaults: MFA ON, Scan now ON, Automated discovery ON, Recon Subdomains ON,
  Multiple attack stacks OFF, MAS for subdomains disabled, Web dictionary brute force ON,
  Web dorking OFF, **Nuclei OFF**, Authenticated Testing OFF, Static outbound IP OFF, AI OFF,
  Web Agent disabled, Notifications ON, Multiple users ON, API access ON, Phishing OFF,
  Leaked Credentials OFF (its interval and scanned-domains fields are disabled while OFF),
  Include Provisioning ON, Include subdomains ON.
- Scanning interval default None (None/Daily/Weekly/Monthly); License Type default Evaluation.
- Operator Account: react-select, placeholder "Select Operator Accounts", nothing selected.

## Differences from the CE-only route (Case 2)

- Company name has no `- CE Only` suffix.
- Scanning interval from the license tier and **Scan now ON** (CE: None/OFF).
- Advanced options: a Surface profile with several toggles ON (CE: all OFF), plus maximum
  scan duration 90 h.
- Notifications, Multiple users, API access ON (CE: OFF).
- Operator Account assigned from TA/CSM (CE: none).
- License quantities from Salesforce / defaults (CE: fixed 1/1/1).
- The readback must tolerate a tenant whose scan has already started (CE tenants never scan).
