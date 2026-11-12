# Request draft: dedicated Salesforce integration user for Surface onboarding (2026-11-06, revised 2026-11-12)

Prepared for the owner to send; the assistant sends nothing. No key, token, password or customer data is in it.
Fill in the names in angle brackets before sending. Supersedes the read-only scope of docs/44 (2026-10-14) and the
write option S3 of docs/54 (2026-10-19): the automation now also writes the stage and the Surface Account ID.

**Revision 2026-11-12 (from a code review of every Salesforce call):**

- Added reads that were missing: `Onboarding_Comments__c`, `Primary_User__c`, the Primary User **Contact** (FirstName,
  LastName, Email) and the **ListView** object (to find `Open_Onboardings`).
- The old CO-0741 repair route (`/attended/confirm-comment-update`) still writes `Onboarding_Comments__c`,
  `Onboarding_Approval_Status__c` and `Onboarding_Stage__c='Request Approved'`. It is retired before the switch, so the
  integration user never needs Edit on approval status or comments.
- Owner decisions 2026-11-12: a teammate added by the owner can onboard while the owner is away; each person signs in
  to the dashboard with their own Salesforce SSO + MFA (identity only, on a separate alias that is logged out at once) and to
  BackOffice with their own account; the integration user does every Salesforce read and write; the dashboard runs on
  one shared onboarding host (Windows first, the VM later); the production approval stays manual in Salesforce
  (`Onboarding_Approval_Status__c` = Approved by the business, never by this user).
- Optional new field `Onboarding_Automation_Operator__c` so Salesforce shows which person triggered each change.
- Sign-in app scopes: `api` only (plus `refresh_token`, `offline_access` if the `sf` CLI needs them); no `openid` on this
  app, because people's identity comes from their own sign-in.
- More questions for the admins: MFA exemption, list views and public groups for the integration license, Contact access.

## Why

Every Salesforce read and write of the onboarding automation runs today as the owner's personal user
(milton.stevenson@pentera.io, `sf` CLI alias `surface-onboarding`, OneLogin SSO + MFA). That ties the automation to one
person, gives it far more access than it needs, blocks teammates from onboarding while the owner is away, and blocks
running it unattended on the VM.

## Facts behind it (read-only, owner's `sf` session, 2026-11-06)

**The owner's user today**

- Profile "Customer Solutions", license Salesforce, role "Customer Ops - ROW". API Enabled comes from the profile.
- Permission sets: Knowledge management (gives View Setup), Stage Path, T3 Fields (Case) See and Edit, Approve Uninstalled
  Connected Apps, Chamelio Contract Access, four Zoom sets. No permission set groups. No View All Data / Modify All Data.
- Through the profile: `Customer_Onboarding__c` Read / Create / Edit / View All (no Delete); `Account` Read / Create / Edit /
  View All; `DealHub_Subscription__c` Read / Create / Edit / Delete; Edit on `Onboarding_Stage__c`, `Account_UUID__c`,
  `Surface_Account_ID__c`, `Surface_Tenant_Region__c` and `Onboarding_Approval_Status__c`.
- So the automation could today change approval status, create COs and delete DealHub rows. The onboarding flow never
  does; the one exception is the old CO-0741 repair route, retired before the switch.

**The org**

- "Salesforce Integration" user license: 5 total, 1 used (4 free).
- No validation rule on `Customer_Onboarding__c` was returned by a Tooling API read (0 rows; the owner's View Setup comes
  from a permission set, so this is likely but not certain: please confirm, together with any Flow / trigger on the stage).

**What the automation actually uses (from the code, re-checked 2026-11-12)**

| Object | Read | Edit |
|---|---|---|
| Customer_Onboarding__c | Id, Name, CreatedDate, LastModifiedDate, Account__c, Account_Name__c, Account_Country__c, Onboarding_Approval_Status__c, Onboarding_Product__c, Onboarding_Type__c, Submission_Date__c, Onboarding_Comments__c, Primary_User__c, Primary_User_Name__c, Primary_User_Email__c, Main_Domain__c, Alternate_Domains__c, Email_Domains__c, Surface_Tenant_Region__c, Completed_Time_Stamp__c, Onboarding_Stage__c, Account_UUID__c, Surface_Account_ID__c | **Onboarding_Stage__c, Account_UUID__c, Surface_Account_ID__c only** (+ Onboarding_Automation_Operator__c if created) |
| ListView | Id (SobjectType = Customer_Onboarding__c, DeveloperName = Open_Onboardings), then its results through REST | none |
| Account (through Account__c) | Name, Delivery_Engineer__c | none |
| User (through Delivery_Engineer__c) | Name | none |
| Contact (through Primary_User__c) | FirstName, LastName, Email | none |
| DealHub_Subscription__c | Id, SystemModstamp, DealHub_Account__c, Product_Full_Name__c, DealHub_Status__c, DealHub_Subscription_Start_Date__c, DealHub_Subscription_End_Date__c | none |
| Organization | Id (checks it is the right org) | none |

Never: create or delete any record; change approval status, comments or any other field; Apex, Tooling, Bulk, Chatter,
Files or reports. Every write is read back.
Stage values written: Account Scanning -> Scan Completed Successfully -> User Created -> Onboarding Completed (the CO
arrives at Request Approved from the business approval).

---

## Slack version (short)

> Hi <Salesforce admin team>, a request for a Salesforce integration user.
>
> Our Surface customer onboarding automation (Customer Onboarding object) runs today on my personal Salesforce user. I'd like
> a dedicated, least-privilege API-only user instead, so onboarding doesn't depend on me (teammates can run it while I'm
> away, each signing in with their own SSO) and can later run unattended on our onboarding VM.
>
> 1. **User:** one "Salesforce Integration" license user (we have 4 free), e.g. `surface-onboarding.integration@pentera.io`,
>    profile "Minimum Access - API Only Integrations", permission set license "Salesforce API Integration".
> 2. **Access:** one custom permission set: Read on Customer_Onboarding__c, Account, Contact, DealHub_Subscription__c;
>    Edit on Customer_Onboarding__c limited by field-level security to three fields: Onboarding_Stage__c,
>    Account_UUID__c, Surface_Account_ID__c. No create, delete, approval status, View/Modify All Data or Setup. Field list in
>    the attached doc.
> 3. **Sign-in:** an External Client App (or Connected App) with the JWT bearer flow, admin-approved users only, IP-restricted.
>    I send you only the public certificate; the private key never leaves our onboarding host.
> 4. Please share the "Open Onboardings" list view with the user, and tell me if any Flow / validation rule on the stage
>    would block it.
>
> Happy to walk through it. SecOps cc'd for the stored key.

## Email version

**To:** <Salesforce admin team / owner of the org>
**Cc:** Orion Mondragon (team leader); <SecOps contact>; John Ostrander
**Subject:** Request: least-privilege Salesforce integration user for the Surface onboarding automation

Hi <name>,

The Surface Customer Onboarding automation reads approved Customer Onboarding (CO) records and, after it creates the
customer's tenant, writes back the tenant IDs and moves the onboarding stage. Today it runs on my personal Salesforce user.
That user has much broader access than the automation needs (for example it could change approval status or create COs),
and it ties the automation to my own sign-in, so nobody can onboard while I am away.

I would like a dedicated integration user with only the access below. The people using the onboarding dashboard
(me and teammates I add) keep signing in with their own SSO + MFA, and the approval of each CO stays a manual step in
Salesforce by the business; this user only does the automation's reads and writes.

**1. The user**

- License: **Salesforce Integration** (the org has 5, 1 used).
- Profile: **Minimum Access - API Only Integrations**; permission set license **Salesforce API Integration**.
- Suggested username: `surface-onboarding.integration@pentera.io`; email: the team mailbox <...> (not a personal one).

**2. One custom permission set, "Surface Onboarding Automation"**

- `Customer_Onboarding__c`: Read, Edit, View All Records. No Create, no Delete.
  - Field Edit: `Onboarding_Stage__c`, `Account_UUID__c`, `Surface_Account_ID__c` only (plus
    `Onboarding_Automation_Operator__c` if you agree to create it, see 4).
  - Field Read (read-only): Account__c, Account_Name__c, Account_Country__c, Onboarding_Approval_Status__c,
    Onboarding_Product__c, Onboarding_Type__c, Submission_Date__c, Onboarding_Comments__c, Primary_User__c,
    Primary_User_Name__c, Primary_User_Email__c, Main_Domain__c, Alternate_Domains__c, Email_Domains__c,
    Surface_Tenant_Region__c, Completed_Time_Stamp__c.
  - Stage values it writes, available on the record type it uses: Account Scanning, Scan Completed Successfully, User
    Created, Onboarding Completed.
- `Account`: Read, View All Records; fields Name, Delivery_Engineer__c.
- `Contact`: Read; fields FirstName, LastName, Email (only for the CO's Primary User; View All Records, or access through
  the parent account, whichever you prefer).
- `DealHub_Subscription__c`: Read, View All Records; fields DealHub_Account__c, Product_Full_Name__c, DealHub_Status__c,
  DealHub_Subscription_Start_Date__c, DealHub_Subscription_End_Date__c.
- `User`: Read (Name only, for the Delivery Engineer).
- **Not needed and please not granted:** View All Data, Modify All Data, View Setup, Customize Application, Create or Delete
  on any object, Edit on approval status, comments or any other field, Files/Content, Chatter, Apex, Tooling.
- Please share the `Open Onboardings` list view on Customer Onboarding with this user (or a public group it is in).

**3. Sign-in: JWT bearer, no password**

- An External Client App (or Connected App) "Surface Onboarding Automation" with the OAuth JWT bearer flow; scope `api`
  (plus `refresh_token` and `offline_access` only if the Salesforce CLI needs them); "Admin approved users are
  pre-authorized"; only the permission set above (or this user) authorized.
- Digital certificate: I send you the **public certificate** only. The private key is created and kept on one onboarding
  host at a time (a Windows onboarding host now, the onboarding VM `workato-opa-01` later; moving means a new key pair and
  a certificate swap) and is never sent anywhere. Yearly rotation.
- IP restriction: the egress addresses of that host (<IT to confirm>); session timeout per your policy.
- Please send me (not secret): the client ID / consumer key of the app and the username once created.

**4. Questions**

- Is any Flow, Apex trigger or validation rule on `Customer_Onboarding__c` limiting who may change the stage or the
  ID fields, or running in the changing user's context (for example on Onboarding Completed / Completed_Time_Stamp__c)?
  It would need to allow this user.
- Changes made by the automation will show this user as Last Modified By. Could you create a Text(80) field
  `Onboarding_Automation_Operator__c` on Customer_Onboarding__c (the email of the person who triggered the change,
  written in the same update) and turn on field history for `Onboarding_Stage__c`, `Account_UUID__c`,
  `Surface_Account_ID__c` and that field? Or is Last Modified By plus our own audit log enough for your reports?
- Can you confirm that an API-only integration user signing in with the JWT flow is exempt from the MFA requirement?
- Can a "Salesforce Integration" license user read list-view results and be a member of a public group in our org?
- For the Primary User Contact: View All Records on Contact, or access through the parent account?

**How it is used**

- About once a minute: one read of recently changed COs. On a click by a person signed in to the dashboard with their own
  SSO + MFA: read one approved CO, its account, its Primary User contact and its DealHub subscriptions; after the tenant
  is created (in BackOffice, by that person's own account), write the IDs and the stage and read them back. Nothing else.
- Logs hold only counts, reason codes and the operator's email, never record contents or keys.

Thanks,
Milton

---

## After the user exists (not built yet; each step with the owner's go)

1. Owner creates the key pair on the onboarding host and sends the public certificate (the assistant never reads the
   private key). Key file in a folder readable only by the account that runs the dashboard.
2. Code: two aliases. `surface-onboarding` = the integration user (`sf org login jwt`, used for every data read and write;
   readiness also checks that the alias's username is the integration user, so a person's session there fails closed);
   a new `surface-onboarding-operator` = the person's SSO + MFA sign-in, used only for the dashboard's identity check
   (`userinfo`), then logged out at once, so the "who clicked" record stays a person and no personal token stays on disk.
3. Users and roles: an admin-managed users file (admin / operator / viewer) replaces the owner-only allowed-user list;
   the owner is the first admin. The local owner-only production approval file (D1) is replaced by the Salesforce approval
   status (owner decision 2026-11-12, to be recorded as a decision first); the operator's click on the create stays.
4. Attribution: the operator's email in every write and run-start record (and in `Onboarding_Automation_Operator__c` if
   created). BackOffice: one browser profile per operator; the Case 3 primary user becomes the signed-in operator.
5. Retire the CO-0741 repair route; a test checks that every Salesforce call in the code stays inside the table above.
6. Same org pin; read-only proof first (identity + one CO read, queue count compared with the owner's view), then one
   write on a CO with the owner's go.
7. The VM can then read Salesforce directly with the same user (replaces the docs/44 read-only request).

Human sign-in decision (2026-11-12): keep Salesforce SSO for identity now; OneLogin OIDC later (needs an IT app).
