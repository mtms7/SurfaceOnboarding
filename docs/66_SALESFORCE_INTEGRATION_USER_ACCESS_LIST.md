# Surface Onboarding automation: Salesforce integration user access list

Attachment to the request "Salesforce integration user for the Surface Onboarding automation" (2026-11-12).
Prepared by Milton Stevenson, Customer Solutions.

## The user

| Item | Requested |
|---|---|
| Username | `surface-oi@pentera.io` (or with a suffix if the name is already taken) |
| Email | Team mailbox `<to be confirmed>` |
| User license | Salesforce Integration |
| Profile | Minimum Access - API Only Integrations |
| Permission set license | Salesforce API Integration |
| Permission set | Surface Onboarding Automation (below) |

## Permission set "Surface Onboarding Automation"

### Object access

| Object | Read | Create | Edit | Delete | View All Records |
|---|---|---|---|---|---|
| Customer_Onboarding__c | Yes | No | Yes (3 fields only) | No | Yes |
| Account | Yes | No | No | No | Yes |
| Contact | Yes | No | No | No | Your choice (or through the parent account) |
| DealHub_Subscription__c | Yes | No | No | No | Yes |
| User | Yes (Name) | No | No | No | No |

### Field access

| Object | Read only | Read and edit |
|---|---|---|
| Customer_Onboarding__c | Id, Name, CreatedDate, LastModifiedDate, Account__c, Account_Name__c, Account_Country__c, Onboarding_Approval_Status__c, Onboarding_Product__c, Onboarding_Type__c, Submission_Date__c, Onboarding_Comments__c, Primary_User__c, Primary_User_Name__c, Primary_User_Email__c, Main_Domain__c, Alternate_Domains__c, Email_Domains__c, Surface_Tenant_Region__c, Completed_Time_Stamp__c | **Onboarding_Stage__c, Account_UUID__c, Surface_Account_ID__c** (plus Onboarding_Automation_Operator__c, if created) |
| Account | Name, Delivery_Engineer__c | none |
| User | Name (the Delivery Engineer) | none |
| Contact | FirstName, LastName, Email (the CO's Primary User only) | none |
| DealHub_Subscription__c | Id, SystemModstamp, DealHub_Account__c, Product_Full_Name__c, DealHub_Status__c, DealHub_Subscription_Start_Date__c, DealHub_Subscription_End_Date__c | none |
| Organization | Id (to check it is the right org) | none |

### Other access

- **List view:** "Open Onboardings" on Customer_Onboarding__c, shared with this user or with a public group it is in (the automation finds the list view and reads its results).
- **Stage values it writes** (must be available on the record type it uses): Account Scanning, Scan Completed Successfully, User Created, Onboarding Completed. The CO reaches "Request Approved" through the business approval, never through this user.

### Not needed: please do not grant

- Create or Delete on any object.
- Edit on Onboarding_Approval_Status__c, Onboarding_Comments__c or any field not listed above.
- View All Data, Modify All Data, View Setup, Customize Application.
- Files / Content, Chatter, Apex, Tooling API, Bulk API, reports.

## Sign-in

| Item | Requested |
|---|---|
| App | External Client App (or Connected App) "Surface Onboarding Automation" |
| OAuth flow | JWT bearer (certificate, no password) |
| Scopes | `api` (plus `refresh_token` and `offline_access` only if the Salesforce CLI needs them) |
| Authorization | Admin-approved users are pre-authorized, through the permission set above only |
| IP restriction | Our onboarding host's egress addresses `<to be confirmed with IT>` |
| Session timeout | Per your policy |
| Certificate | We send the public certificate only. The private key is generated on our onboarding host, never leaves it, and is rotated yearly. |
| Please send back | The client ID (consumer key) and the final username. Neither is secret. |

## How the automation uses this access

1. About once a minute: one read of recently changed COs, to show the onboarding queue.
2. When an operator (signed in with their own account) starts an onboarding: read that one approved CO, its account, its Primary User contact and its DealHub subscriptions.
3. After the tenant is created in BackOffice (by that operator's own account): write the tenant IDs and the stage on that CO, then read them back to confirm.

Nothing else is read or written. Every write is read back. Logs hold only counts, reason codes and the operator's identity, never record contents or keys.

## Rollout

1. Read-only test first: sign in as the integration user and read one CO.
2. One write on a single CO, with the Salesforce team informed.
3. Switch the automation over and stop using the personal login.
