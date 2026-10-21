# Case 3 (Surface & Credential Exposure, New) production create contract, learned from the manual create of CO-0800 (2026-10-21)

**Status:** observed fact, structure only. Source: two HAR files of the owner's manual BackOffice create (path A), read with `tools/har_contract_reader.py`. Both held one successful `POST /api/v1/backoffice/account/add` (200) with identical bodies; the files had no Authorization/Cookie/Set-Cookie headers; the owner deletes them. No name, domain, e-mail, Account ID or UUID is recorded here. Nothing external was touched. The automated Case 3 create is NOT built; both production lock constants stay False.

Code side: `integration/onboarding/production_case3_contract.py`, golden tests `integration/tests/test_case3_create_contract.py`.

## 1. The create body (46 leaf fields; all pinned by the golden test)
| Field | Observed | Note |
|---|---|---|
| accountType / country | `customer`, ISO-2 | lower case in the body, `Customer` in the row |
| accountName | bare account name | no " - CE Only" suffix |
| alternateDomains, subDomains | empty lists | |
| userEmailDomains | one internal domain | |
| leakedCredentialsScannedDomains | one domain (the CE e-mail domain) | |
| leakedCredentialsScanningInterval | `NO_SCHEDULE` observed, **a manual mistake** | owner 2026-10-21: future creates run WEEKLY; the contract says WEEKLY |
| leakedCredentialsSettings.spyCloudSettings.enabled | true | |
| scanningInterval | `MONTHLY` | tier rule Monthly/Weekly is the owner's |
| scanNow | **true** | OPEN: see section 4 |
| campaignsTimeoutInHours | 90 | form default is 24 |
| mfaRequired | true | |
| operatorAccountNames | one name | the owner assigned an operator at create; see section 4 |
| Off | automatedDiscovery, webDorking, staticOutboundIp, ai, webAiAttacker, vodBeta, phishing, both multi-attack-stack flags | |
| On | subDomainsRecon, webDictionaryBruteForce, fullNucleiScan | Nuclei ON per Guru |
| license | `prepaid annual subscription`; assets "10000" and domains "10000" are STRINGS; subDomainsNumber 500 is a number; enabled, provisioning, leakedCredentials, notifications, multipleUsers, apiAccess all true; reconLevel null | |
| license.startDate | ISO UTC with the clock time of the click | run day |
| license.expirationDate | `2029-09-30T07:00:00.000Z` | calendar day kept, clock 07:00Z; the contract compares the DAY only |
| additionalNetworks | null | |

## 2. The stored row (create response)
Summary only: licence type, counts 10000/10000/500, `scanningFrequency` MONTHLY, `reconLevel` ALL_ASSETS, leaked-credentials domain count 1, start and expiry as epoch milliseconds. **The response carries no Account ID or UUID.** Both were read by hand from the tenant Details tab (Account ID = 24 hex, Account uuid = 32 hex) and entered by hand into Salesforce (`Surface_Account_ID__c`, `Account_UUID__c`). A future writer must read them back from the tenant row (list call `getAllDetailedAccounts`), never from the create response.

## 3. Date handling
Start = the click time in UTC. Expiry = the picked calendar day, sent as 07:00Z (a local-midnight conversion). Any check must compare the day, not the instant.

## 4. Owner answers 2026-10-21 (previously open)
1. **Expiry:** comes from the Salesforce Subscription End Date for every case (the dashboard shows it as "License end"). Not a form default.
2. **scanNow:** a scan started at create; the automation sends `true` (contract pins `OBSERVED_SCAN_NOW = True`).
3. **Operator at create:** the Technical Advisor collected from Salesforce (CO-0800: the Frank Hoeffle Operator). This supersedes the 2026-09-30 "empty" rule for Case 3; the contract accepts one name or empty until the Case 3 source read is built. Not yet built: the source field for the TA and the mapping TA name -> BackOffice operator name (fail closed when unmapped).
4. **Static IP:** no Salesforce field exists. The automation keeps Static outbound IP OFF; the owner turns it on by hand when asked.
5. **Approval card label:** fixed (a Case 3 approval says "One CO, Surface & Credential Exposure"); test `test_approval_scope_label.py`.
6. **Leaked Credentials interval:** the manual `NO_SCHEDULE` was a mistake; the automation must set WEEKLY.
7. **Number of domains:** owner rule 2026-10-21: assets 10000, domains 10000, subdomains from the Surface subscription and add-ons (CO-0800: 500). The runner now types 10000 for Surface creates (changed 2026-10-21); the summary flags any other value.
8. **Long account names:** see docs/60 (proposed convention, awaiting owner approval).

## 5. What the automation still lacks
Create gate for Case 3 (today CE-only), approval scope, guarded `Surface_Account_ID__c`/`Account_UUID__c` writer plus stage move, a Surface scan check, progress steps, independent review, a reviewed unlock and relock. Next safe step: a read-only readback of the created tenant against the plan.
