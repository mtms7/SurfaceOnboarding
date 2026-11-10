# Request draft: dedicated Salesforce integration user for Surface onboarding (2026-11-06)

Prepared for the owner to send; the assistant sends nothing. No key, token, password or customer data is in it.
Fill in the names in angle brackets before sending. Supersedes the read-only scope of docs/44 (2026-10-14) and the
write option S3 of docs/54 (2026-10-19): the automation now also writes the stage and the Surface Account ID.

## Why

Every Salesforce read and write of the onboarding automation runs today as the owner's personal user
(milton.stevenson@pentera.io, `sf` CLI alias `surface-onboarding`, OneLogin SSO + MFA). That ties the automation to one
person, gives it far more access than it needs, and blocks running it unattended on the VM.

## Facts behind it (read-only, owner's `sf` session, 2026-11-06)

**The owner's user today**

- Profile "Customer Solutions", license Salesforce, role "Customer Ops - ROW". API Enabled comes from the profile.
- Permission sets: Knowledge management (gives View Setup), Stage Path, T3 Fields (Case) See and Edit, Approve Uninstalled
  Connected Apps, Chamelio Contract Access, four Zoom sets. No permission set groups. No View All Data / Modify All Data.
- Through the profile: `Customer_Onboarding__c` Read / Create / Edit / View All (no Delete); `Account` Read / Create / Edit /
  View All; `DealHub_Subscription__c` Read / Create / Edit / Delete; Edit on `Onboarding_Stage__c`, `Account_UUID__c`,
  `Surface_Account_ID__c`, `Surface_Tenant_Region__c` and `Onboarding_Approval_Status__c`.
- So the automation could today change approval status, create COs and delete DealHub rows. It never does (code), but the
  access is there.

**The org**

- "Salesforce Integration" user license: 5 total, 1 used (4 free).
- No validation rule on `Customer_Onboarding__c` was returned by a Tooling API read (0 rows; the owner's View Setup comes
  from a permission set, so this is likely but not certain: please confirm, together with any Flow / trigger on the stage).

**What the automation actually uses (from the code, 2026-11-06)**

| Object | Read | Edit |
|---|---|---|
| Customer_Onboarding__c | Id, Name, CreatedDate, LastModifiedDate, Account__c, Account_Name__c, Account_Country__c, Onboarding_Approval_Status__c, Onboarding_Product__c, Onboarding_Type__c, Submission_Date__c, Primary_User_Name__c, Primary_User_Email__c, Main_Domain__c, Alternate_Domains__c, Email_Domains__c, Surface_Tenant_Region__c, Completed_Time_Stamp__c, Onboarding_Stage__c, Account_UUID__c, Surface_Account_ID__c | **Onboarding_Stage__c, Account_UUID__c, Surface_Account_ID__c only** |
| Customer_Onboarding__c list view | `Open_Onboardings` (results) | none |
| Account | Name, Delivery_Engineer__c | none |
| User | Name (Delivery Engineer) | none |
| DealHub_Subscription__c | Id, SystemModstamp, DealHub_Account__c, Product_Full_Name__c, DealHub_Status__c, DealHub_Subscription_Start_Date__c, DealHub_Subscription_End_Date__c | none |
| Organization | Id | none |

Never: create or delete any record, change approval status, comments or any other field. Every write is read back.
Stage values written: Request Approved -> Account Scanning -> Scan Completed Successfully -> User Created -> Onboarding Completed.

---

## Slack version (short)

> Hi <Salesforce admin team>, a request for a Salesforce integration user.
>
> Our Surface customer onboarding automation (Customer Onboarding object) runs today on my personal Salesforce user. I'd like
> a dedicated, least-privilege API-only user instead, so it doesn't depend on me and can later run unattended on our
> onboarding VM.
>
> 1. **User:** one "Salesforce Integration" license user (we have 4 free), e.g. `surface-onboarding.integration@pentera.io`,
>    profile "Minimum Access - API Only Integrations".
> 2. **Access:** one custom permission set: Read on Customer_Onboarding__c, Account, DealHub_Subscription__c (view all
>    records); Edit on Customer_Onboarding__c limited by field-level security to three fields: Onboarding_Stage__c,
>    Account_UUID__c, Surface_Account_ID__c. No create, delete, approval status, View/Modify All Data or Setup. Field list in
>    the attached doc.
> 3. **Sign-in:** an External Client App (or Connected App) with the JWT bearer flow, admin-approved users only, IP-restricted.
>    I send you only the public certificate; the private key never leaves our machines.
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
and it ties the automation to my own sign-in. I would like a dedicated integration user with only the access below.

**1. The user**

- License: **Salesforce Integration** (the org has 5, 1 used).
- Profile: **Minimum Access - API Only Integrations**; permission set license **Salesforce API Integration**.
- Suggested username: `surface-onboarding.integration@pentera.io`; email: the team mailbox <...> (not a personal one).

**2. One custom permission set, "Surface Onboarding Automation"**

- `Customer_Onboarding__c`: Read, Edit, View All Records. No Create, no Delete.
  - Field Edit: `Onboarding_Stage__c`, `Account_UUID__c`, `Surface_Account_ID__c` only.
  - Field Read (read-only): Account__c, Account_Name__c, Account_Country__c, Onboarding_Approval_Status__c,
    Onboarding_Product__c, Onboarding_Type__c, Submission_Date__c, Primary_User_Name__c, Primary_User_Email__c,
    Main_Domain__c, Alternate_Domains__c, Email_Domains__c, Surface_Tenant_Region__c, Completed_Time_Stamp__c.
  - All five stage values (Request Approved, Account Scanning, Scan Completed Successfully, User Created, Onboarding
    Completed) available on the record type it uses.
- `Account`: Read, View All Records; fields Name, Delivery_Engineer__c.
- `DealHub_Subscription__c`: Read, View All Records; fields DealHub_Account__c, Product_Full_Name__c, DealHub_Status__c,
  DealHub_Subscription_Start_Date__c, DealHub_Subscription_End_Date__c.
- `User`: Read (Name only, for the Delivery Engineer).
- **Not needed and please not granted:** View All Data, Modify All Data, View Setup, Customize Application, Create or Delete
  on any object, Edit on approval status or any other field.
- Please share the `Open Onboardings` list view on Customer Onboarding with this user (or a group it is in).

**3. Sign-in: JWT bearer, no password**

- An External Client App (or Connected App) "Surface Onboarding Automation" with OAuth JWT bearer flow, scopes `api` and
  `openid`, "Admin approved users are pre-authorized", only the permission set above (or this user) authorized.
- Digital certificate: I send you the **public certificate** only; the private key is created and kept on our two machines
  (the owner's PC now, the onboarding VM `workato-opa-01` later) and never sent anywhere.
- IP restriction: Pentera's egress addresses that the PC and the VM use (<IT to confirm>); session timeout per your policy.
- Please send me (not secret): the client ID / consumer key of the app and the username once created.

**4. Two questions**

- Is any Flow, Apex trigger or validation rule on `Customer_Onboarding__c` that limits who may change the stage or the
  ID fields? It would need to allow this user.
- Changes made by the automation will show this user as Last Modified By instead of me. Is that fine for your reports?

**How it is used**

- About once a minute: one read of recently changed COs; on a click by a human operator (who still signs in with their own
  SSO + MFA): read one CO and its account and DealHub subscriptions, then after the tenant is created write the IDs and the
  stage and read them back. Nothing else.
- Logs hold only counts and reason codes, never record contents or keys.

Thanks,
Milton

---

## After the user exists (not built yet; each step with the owner's go)

1. Owner creates the key pair locally and sends the public certificate (the assistant never reads the private key).
2. Code: a second alias. Proposed: `surface-onboarding` = the integration user (`sf org login jwt`, used for every data
   read and write); a new `surface-onboarding-operator` = the human SSO + MFA sign-in, used only for the dashboard's
   identity check (`userinfo`) so the "who clicked" record stays a person. Allowed-user list, approval issuers, tests and VM
   scripts updated (they hard-code the owner's address today).
3. Same org pin; read-only proof first (identity + one CO read), then one write on a CO with the owner's go.
4. The VM can then read Salesforce directly with the same user (replaces the docs/44 read-only request).

**Owner decision needed before step 2:** keep the human SSO sign-in for the dashboard (proposed above), or replace it
(OneLogin OIDC needs an IT app; 2026-10-03 decision).
