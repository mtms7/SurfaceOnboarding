# CO production status report - 2026-10-17

Read-only, local. No external call, nothing written outside this file. No UUID, e-mail or password appears here: field names, statuses and dates only.

## Method
- Snapshot: `snapshot-20261017T180026Z` (local copy `surface-vm-snapshot-20261017T180026Z.zip`): 18 queued COs, `queue.json`, `co_details.json`, `production_inputs.json`.
- Clone: newest local `redash-prod-clone-inventory-20261017T170003Z` (5,447 tenants, query 251). It is older than 6 h at the time of this run, so the code was evaluated as of 30 min after its capture (otherwise every CO would read "Clone stale").
- Code: the dashboard's own `production_match.production_match` (as `production_match_for` calls it on the VM: inputs from `production_inputs.json`, UUID from `co_details.json`) and `production_views.production_check` for the chip text. CE-only checks apply to engine `case_2_new_ce_only` only.
- Expected licence end = plan `license_end` in `production_inputs.json` (owner rule: start + 1 year - 1 day, capped at the DealHub subscription end). Actual = clone `accountLicense.expirationDate` (UTC date).

## Result per CO
Status = what the dashboard shows now. "SF UUID" = Salesforce `Account_UUID__c` is set (from `co_details.json`).

| CO | Account | Route / product | Dashboard status | SF UUID | Reason / warnings | Recommended owner action |
|---|---|---|---|---|---|---|
| CO-0679 | Tidhar Group Ltd. | CE-only / Credential Exposure, New | **Clear** (not_in_production) | no | No tenant by name, primary or alternate domain. Not a clearance: the clone lags. | Ready for the manual create once approval, timing window and the BackOffice duplicate check pass. |
| CO-0798 | Advanced Technology International | CE-only / CE, New | **Clear** | no | Same. Expected end 2027-06-21. | Same. |
| CO-0797 | Grupo Patio | CE-only / CE, New (stage still Request Approved) | **Onboarded** | yes | UUID owner found; name, primary domain, alternates and licence type match; enabled; licence active; end 2027-05-20 = expected 2027-05-20; counts 1/1/1; SpyCloud on; " - CE Only" present. No warnings. | None for the tenant. Move the Onboarding Stage in Salesforce yourself (still Request Approved). |
| CO-0780 | State Land service (Valsts Zemes dienests) | CE-only / CE, New | **Onboarded: review** | yes | `identity_mismatch`, field **tenant name differs** only. Primary domain, alternates and licence type match; end 2027-06-30 = expected 2027-06-30; counts 1/1/1; SpyCloud on; both names end " - CE Only". The expected name is 52 characters (full account name incl. the bracketed local-language name), the tenant's is 28 (the short name). | Confirm the tenant is the right one for this CO (UUID owner, domain matches) and that the shortened name is intended; then accept. No create. Optional: record the rule that bracketed local names are dropped. |
| CO-0765 | Orlando Health | CE-only / CE, New | **Onboarded** (amber warning) | yes | `expiry_differs`: **expected 2027-10-11, actual 2029-10-11** (+2 years). Everything else matches (counts 1/1/1, SpyCloud on, suffix present). | Check the DealHub range and the tenant licence in BackOffice. If the term is one year, correct the expiration to 2027-10-11; if the contract really runs to 2029, record the exception. |
| CO-0781 | South Staffordshire College | CE-only / CE, New | **Onboarded** (amber warning) | yes | `expiry_differs`: **expected 2027-09-29, actual 2029-09-29** (+2 years). Everything else matches. | Same as CO-0765. Both show the same +2 years pattern: the create probably used the full multi-year term, not the one-year rule. |
| CO-0757 | uMngeni-uThukela Water | Surface + CE, New (case 3) | **Clear** | no | No tenant found. Expected end 2027-11-02. | Ready for the manual create when approval, window and the BackOffice duplicate check pass. |
| CO-0795 | American Integrity Insurance Company | Surface + CE, New | **Clear** | no | No tenant found; approval Pending. | Wait for approval. CO-0795 and CO-0796 are for the same account: confirm only one is onboarded. |
| CO-0796 | American Integrity Insurance Company | Surface + CE, New | **Clear** | no | No tenant found; Salesforce approval empty. | Same; resolve which of the two COs is real before any create. |
| CO-0767 | A2A | Renewal of Surface + CE (case 6) | **Duplicate** (`exists_differs`) | no | One live paid tenant with the exact name plus 3 trial/evaluation tenants. `license_end` differs: expected 2029-10-26, actual 2029-09-27. | This is a renewal, so an existing tenant is expected: the "Duplicate" chip is misleading (proposal C). Review the term, then do the renewal edit; no create. |
| CO-0649 | KfW | Surface + CE, New (stage User Created) | **Not checked** | yes | No `production_inputs.json` entry. Offline check against the clone: exactly one tenant owns the CO's UUID; name equals the account name; primary domain is one of the CO domains; enabled; end 2029-09-29; counts 10000/1000/1000; SpyCloud on. | Looks onboarded. Make the page say so (proposal A). |
| CO-0791 | Guardia Civil | Surface + CE, New (stage Scan Completed Successfully) | **Not checked** | yes | No inputs entry. Offline: one UUID owner; name = account name; domain matches; enabled; end 2027-09-30; counts 10000/500/500. | Looks onboarded. Same. |
| CO-0766 | Payments Network of the Philippines, Inc. (PNPI) | Surface + CE, New (User Created) | **Not checked** | yes | No inputs entry. Offline: one UUID owner; **name differs** from the account name (long account name with a previous-name bracket); domain matches; enabled; end 2027-09-30; counts 10000/500/500. | Would be "Onboarded: review (name differs)". Confirm identity, then accept. |
| CO-0769 | Illy Caffe S.p.A. | Renewal of Credential Exposure (single product) | **Not checked** | yes | No inputs entry (single-product renewals are not a supported route). Offline: one UUID owner with the " - CE Only" suffix; name differs from the plain account name (expected: the CE name is derived); domain matches; enabled; end 2029-02-28; counts 1/1/1. | Renewal of an existing tenant: identity OK by UUID; renewal edit, no create. |
| CO-0777 | St. Amant Inc. | Renewal of Surface + new CE module | **Not checked** | yes | No inputs entry. Offline: one UUID owner; name differs; domain matches; enabled; **licence end 2026-10-09 is already past**. | The tenant licence expired 8 days ago: renew now in BackOffice, then verify. |
| CO-0792 | Cornerstone Systems, Inc. | Renewal of Credential Exposure | **Not checked** | yes | No inputs entry. Offline: one UUID owner; name differs; domain matches; enabled; end 2027-09-30; counts 1/1/1. | Renewal of an existing tenant; no create. |
| CO-0782 | TIB Bank | Renewal of Credential Exposure | **Not checked** | no | No inputs entry; no UUID in Salesforce (a renewal should have one). | Find the existing tenant and write its UUID into Salesforce before any action. |
| CO-0793 | PNPI | Renewal of Surface | **Not checked** | no | No inputs entry; approval Pending, stage New, no Email Domains. | Wait for approval and the missing domains; locate the existing tenant. |

Counts: Clear 5 (0679, 0798, 0757, 0795, 0796); Onboarded 3 (0797 clean, 0765 and 0781 with the licence-end warning); Onboarded: review 1 (0780); Duplicate 1 (0767, a renewal); Not checked 8 (0649, 0791, 0766, 0769, 0777, 0792, 0782, 0793). No CO is Ambiguous, "Other tenants only" or "UUID not in clone".

Informational on every onboarded CO: the tenant's licence start is the create day, not the DealHub start. It is shown but never warned (licence start is informational by design).

## Why eight COs are "Not checked"
The only cause is that `production_inputs.json` has no entry for them. The VM never reads Salesforce; the publisher builds an entry per queued CO from the runner's source readers (`production_review_inputs`), and a reader that raises simply leaves no entry (`production_inputs_unavailable`; the reason code is not stored). By route:
- **Surface + CE, New (0649, 0766, 0791):** the case-3 reader refuses these three while 0757/0795/0796 (same route, no UUID, stage Request Approved/New) work. The three are already created (UUID set, later stages). The reader needs a Core Plus DealHub term and an unambiguous CE term, so the DealHub rows have probably changed since the create. The exact code (for example `case3_core_plus_missing`, a term mismatch or `case3_source_unavailable`) is not recorded, and finding it needs a Salesforce read, which this task forbids.
- **Single-product renewals (0769, 0782, 0792, 0793):** `renewal_route_not_supported` (only the three two-product renewal routes have a reader).
- **0777:** a supported renewal route, so a `renewal_blocked_*` or source problem (not stored).

What IS available in `co_details.json` for all 18 COs: Account_Name__c, Account_UUID__c, Main_Domain__c, Alternate_Domains__c, Email_Domains__c, Onboarding_Product__c / Type__c, approval status, stage, Surface_Account_ID__c. Name, primary domain and UUID are therefore available for every CO that has them (Credential-Exposure-only COs have no Main Domain and use Email_Domains__c; 0793 has no Email Domains and no UUID). NOT available without the readers: the planned licence end and type (DealHub), and the country.

## Proposal (not implemented): minimal safe change
A. **Identity-only match from `co_details.json` when the inputs entry is missing, only for COs whose UUID is set.**
   - Build `co_source` from co_details: names = the two derived forms (`surface_names` and `ce_only_names` of Account_Name__c), domains = Main + Alternate + Email domains (canonical). Plan = primary domain only, `license_type: None`, `license_end: None`, so licence comparisons read "unknown", never "match".
   - Use it ONLY when the CO's UUID is set and exactly one clone tenant owns it: result `onboarded` with a note "licence not verified: no DealHub inputs", create still blocked; a name or domain difference stays "Onboarded: review".
   - Every other case stays `not_checked`: no UUID, UUID with no owner, several owners. A "Clear" is never produced without the real inputs (a UUID-less match from co_details would skip the DealHub and route checks).
B. **Say why:** store the reader's reason code per CO in `production_inputs.json` (`{"unavailable": "<code>"}`, a code only), so the list says "Not checked: <code>" instead of a generic message and a transient failure is distinguishable from an unsupported route.
C. **Renewals:** for renewal engines show "Existing tenant (renewal)" instead of "Duplicate" when the match is `exists_*`, since a renewal expects the tenant (CO-0767 reads Duplicate today). The create and approval gates are unchanged.
D. **CO-0765 / CO-0781:** no code change; owner decision on the licence end (see table).

Every step keeps the fail-closed rules: no UUID, no owner, ambiguity, a stale clone and missing inputs all stay blocking; a missing match is never a clearance.
