# CO-0717 read-only Salesforce detail approval packet

This is an approval and implementation packet. It is not a Salesforce
connection, credential, OAuth registration, VM configuration, or activation
runbook. It authorizes no write to Salesforce, Leonardo, Workato, OPA,
Donatello, BackOffice, or production.

## Fixed pilot scope

The initial integration is limited to one source record: `Customer_Onboarding__c`
where `Name = 'CO-0717'`. It must request at most two records and stop unless
exactly one is returned. It has no generic SOQL interface, queue enumeration,
polling schedule, writeback, or retry loop.

The only approved display projection is:

| Display label | Salesforce field |
| --- | --- |
| Account | `Account_Name__c` |
| Onboarding Product | `Onboarding_Product__c` |
| Onboarding Type | `Onboarding_Type__c` |
| Primary User | `Primary_User_Name__c` |
| Main Domain | `Main_Domain__c` |
| Alternative Domains | `Alternate_Domains__c` |
| Email Domains | `Email_Domains__c` |
| Onboarding Comments | `Onboarding_Comments__c` |
| Onboarding Stage | `Onboarding_Stage__c` |
| Surface Account ID | `Surface_Account_ID__c` |
| Account UUID | `Account_UUID__c` |

`Name` and `LastModifiedDate` are required only to prove fixed scope and bind a
source revision. The application must not retain a Salesforce record ID,
lookup ID, response body, or generic source payload.

## Required identity and authorization decisions

Before a provider is implemented or enabled, the Salesforce and security
owners must approve all of the following outside this repository:

1. A dedicated, non-human, read-only integration identity. Do not use the
   developer desktop CLI session, browser cookies, a shared administrator, or
   a personal MFA session on the VM.
2. Object/field permissions limited to the listed read projection. No create,
   edit, delete, bulk, metadata, or Salesforce writeback permissions.
3. A connected-app or equivalent machine identity design with a short-lived
   token strategy, approved audience, login policy, IP/network policy, token
   revocation owner, expiry/rotation policy, and incident procedure.
4. Runtime secret delivery from an approved secret provider or protected host
   mechanism. Secrets must never appear in an archive, unit file, environment
   dump, command history, browser, source tree, or application log.
5. The proxy/SSO/MFA design in `REVERSE_PROXY_APPROVAL_PACKET.md`, including
   the exact five-user pilot allow-list. The loopback static preview cannot
   display live customer values.
6. Retention and logging approval for personal data and comments. By default,
   values stay only in process memory for a single authenticated response;
   logs contain only a generated correlation ID and masked outcome category.

## Required runtime controls

- Bind the returned record to the exact `CO-0717` name and a current source
  revision. Stop on zero or multiple records, revision drift, duplicate JSON
  keys, missing fields, unexpected fields, invalid types, or an oversized
  value.
- Allow only a protected `GET` detail operation. Do not permit query strings,
  a record-id parameter, arbitrary field selection, browser-side Salesforce
  calls, caching, or a generic data explorer.
- Enforce SSO/MFA and the approved operator role before the provider runs.
  The application must independently validate identity propagation from the
  proxy and reject absent, expired, malformed, duplicated, or unauthorized
  identity context.
- Set `Cache-Control: no-store`, omit referrers, escape all displayed values,
  and prohibit third-party assets, analytics, and generated API docs.
- Fail closed and return a generic masked error. Never include a source value,
  field value, token, cookie, or comment in an error, audit event, or metric.
- Leave Leonardo, OPA, Workato, all writeback mechanisms, and production
  targets disabled. A successful display read does not authorize any action.

## Evidence required before activation

The owners must record: identity approval; permission-set evidence; connected
app/security review; secret-provider health; proxy SSO/MFA test evidence;
operator allow-list; VM egress policy; source-revision and schema negative
tests; log-redaction evidence; emergency disable and rollback test; and a
separate acceptance that the record contains customer data suitable for this
   pilot display.

## Initial role and reauthentication policy

The initial operator is represented only by an owner-approved opaque IdP
subject reference; do not place a human name, email address, Salesforce token,
or browser session in source code. That one operator may request a manual
refresh and begin an OAuth reconnect after their Salesforce authorization has
expired. Approved members of the viewer group may see only the authenticated
read view; they cannot refresh, reconnect Salesforce, query another record,
change a role, or initiate a workflow action.

A reconnect must use the normal corporate OneLogin MFA flow through an
owner-approved OAuth authorization-code/PKCE implementation. The app must not
read Chrome sessions, request passwords or MFA codes, transfer browser cookies,
or use a personal desktop CLI session on the VM. A user session is never a
safe basis for unattended polling; the requested twice-daily job remains
blocked until a dedicated least-privilege Salesforce identity is approved.
