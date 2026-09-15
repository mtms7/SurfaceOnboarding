# Leonardo Development Read-Only Discovery

Date: 2026-08-24  
Environment: `https://leonardo.dev.app.pentera.io/`  
Scope: Authenticated visible-UI discovery only. No create, update, delete, export, account-record inspection, credential inspection, browser-storage inspection, or production access.

## Verified access and navigation

- The authenticated development session can open `Tenant Management` and `Audits`.
- Tenant Management exposes account search, filtering, column selection, CSV download, pagination, `Add Account`, and `Add Operator Account` controls.
- A synthetic, non-existent discovery string was entered into the global search. Leonardo returned `No matching records found`; the search was then cleared.
- The blank Add Account form was opened to inventory its structure and closed with `Cancel`. `Confirm` remained disabled and was never selected.
- Audits exposes the columns `Time`, `Account Name`, `Severity`, `User`, `Category`, `Type`, and `Description`. The view showed zero audit rows during this discovery; the Leonardo owner must confirm whether this is expected data state or a permission limitation.

No customer names, domains, email addresses, contact details, or account-row contents from the development tenant are retained in this document.

## Add Account form contract observed

### Account details

- Company name
- Account type: `Customer` or `Demo`
- Company primary domain
- Alternate domains, comma-separated
- Subdomains, comma-separated
- User email domains, comma-separated
- Networks, comma-separated
- Country

### Primary user

- First name
- Last name
- Organization email
- Phone number
- Job title
- Multi-factor authentication, enabled by default
- Operator account selection

MFA must remain enabled for user-based onboarding. Credentials and MFA codes must never be persisted in Workato tables, recipes, logs, local files, or chat.

### Account settings

- Scanning interval: `None`, `Daily`, `Weekly`, or `Monthly`
- Scan now
- Maximum scan duration in hours; visible default was `24`
- Automated discovery
- Recon subdomains
- Multiple attack stacks
- MAS for subdomains
- Web dictionary brute force
- Web dorking
- Nuclei
- Authenticated Testing
- Static outbound IP
- AI
- Notifications
- Multiple users
- API access

Several feature controls are enabled by default. A future automation must map every security-relevant option explicitly and verify the saved state after creation; it must not inherit UI defaults silently.

### Attack modules

- Phishing
- Leaked Credentials
- Leaked Credentials scanning interval: `None`, `Daily`, `Weekly`, or `Monthly`
- Leaked Credentials scanned domains, comma-separated
- SpyCloud, displayed as enabled and not editable in this form state

The Leaked Credentials interval and domain inputs were disabled while the parent module was not selected. Workato mapping must preserve this dependency and fail closed for inconsistent combinations.

### License

- Include Provisioning
- Include subdomains
- Type: `Evaluation`, `Trial`, `Prepaid monthly subscription`, `Prepaid annual subscription`, or `PAYG monthly subscription`
- Number of assets
- Number of domains
- Number of subdomains
- Start date
- Expiration date

Provisioning and subdomain controls were enabled by default. License dates and quantities must come from validated DealHub/CO evidence and receive CSE approval; defaults are not authoritative.

## Security and implementation implications

1. Use the Tenant Management search as a pre-create duplicate check for exact company name and exact primary domain. Alternate-domain and user-email-domain collision behavior still requires an owner-approved read-only test.
2. Keep account/contact/domain details in the restricted Workato table only. The dashboard-safe table must continue to omit private comments, contacts, domains, attachments, and raw payloads.
3. Require exact CO/source keys, supported route, unexpired CSE approval, duplicate check, deterministic idempotency key, explicit feature/license mapping, and a masked dry-run review before any Leonardo create action.
4. Preserve MFA. Do not inspect or automate browser cookies, tokens, password stores, local storage, or MFA secrets.
5. Keep `workato-opa-01` (`172.26.37.20`) as a minimal hardened OPA host. Do not add Playwright or a browser runtime there without a separate SecOps architecture approval.
6. The Audits view may support independent operational evidence, but its zero-row state must be clarified before relying on it for read-after-write verification.

## Remaining owner decisions

- Confirm that browser/UI automation is an approved Leonardo Development integration method, or provide a supported API/service interface.
- Identify the authoritative Leonardo owner for form semantics and later test approval.
- Map Salesforce/DealHub fields to every required Leonardo field and security-relevant option.
- Define duplicate rules for alternate domains, subdomains, email domains, networks, and operator accounts.
- Identify where a successful create exposes the stable Leonardo account UUID and how it can be read back safely.
- Define the exact synthetic account fixture, expected settings, cleanup/retention policy, and separate authorization for one development create.
- Confirm the intended Salesforce destination field for the returned UUID.

## CO-0717 live form re-verification — 2026-08-27

An authenticated Chrome session was claimed only on the exact Leonardo
Development origin. The blank **Add Account** form and both **Advanced
options** sections were opened, no field was populated, `Confirm` was not
selected, and the form was closed with `Cancel`. No tenant-row contents were
retained.

The current visible defaults were re-verified as follows:

| Control | Visible state |
| --- | --- |
| Account type / country | Unselected |
| MFA / Scan now | Enabled / enabled |
| Scanning interval / Leaked Credentials interval | `None` / `None` |
| Maximum scan duration | `24` hours |
| Automated discovery / Recon subdomains | Enabled / enabled |
| Multiple attack stacks / MAS for subdomains | Disabled / disabled; MAS control disabled |
| Web dictionary brute force / Web dorking / Nuclei | Enabled / disabled / disabled |
| Authenticated Testing / Static outbound IP / AI | Disabled / disabled / disabled |
| Notifications / Multiple users / API access | Enabled / enabled / enabled |
| Phishing / Leaked Credentials | Disabled / disabled |
| SpyCloud | Enabled and disabled for editing |
| Include Provisioning / Include subdomains | Enabled / enabled |
| License type / Number of assets | `Evaluation` / `50000` |

These are observed UI defaults, not an approved Case 3 mapping. In particular,
they conflict with several historical local candidates. A manifest builder must
therefore provide every value explicitly and fail closed if Leonardo changes a
field, enum, dependency, enabled state, or default.

## Next safe step

Create a local-only, sanitized Leonardo field-mapping and dry-run contract. Then extend the inactive Workato approval gate so every absent, denied, expired, duplicate, ambiguous, or mismatched condition returns `proceed=false`. Do not add a Leonardo connection or create action until the owner decisions above are recorded and separately authorized.
