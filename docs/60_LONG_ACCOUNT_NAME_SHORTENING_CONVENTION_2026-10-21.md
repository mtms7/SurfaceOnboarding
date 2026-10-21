# Long account names: a standard way to shorten the tenant name (RULES APPROVED by the owner 2026-10-21)

**Status:** the rules in section 1 are APPROVED as written (owner, 2026-10-21). Short name lives on the DASHBOARD CARD (owner, 2026-10-21): the dashboard proposes it by these rules and the owner confirms or edits it before create; no Salesforce change. The card is not built yet. Nothing is implemented and nothing is in Guru yet. Guru is an external system: the text in section 3 is ready to paste, and the owner updates the card.

**Need:** names such as "Barmherzige Brüder gemeinnützige Träger GmbH" are long. The owner created CO-0800 as "Barmherzige Brüder gemeinnützige TG", which is easy to recognise in the tenant list.

## 1. Rules
1. **Use the Salesforce account name unchanged whenever it is 40 characters or fewer.** Shorten only above that. (40 is a proposed working limit, not a measured BackOffice limit.)
2. **Never touch the first two words.** They carry the identity ("Barmherzige Brüder"). Keep spelling, capitals and umlauts exactly as in Salesforce.
3. **Compress only the tail:** the last word plus the legal form (GmbH, gGmbH, AG, SE, KG, UG, e.V., Ltd, Inc, B.V., S.A.) become one initialism of capital letters, no dots, no spaces: "Träger GmbH" -> "TG".
4. **Drop filler words in the middle only if still too long** (und, of, the, für), never an identity word.
5. **Still too long?** Initialise the middle words from the right, one at a time, until it fits. Stop at once when it fits.
6. **A single-word company** keeps its word and the legal form unchanged; only drop the legal form if the name is still too long.
7. **Record the short name in the Salesforce CO** (a text field or a Chatter note) so the dashboard, the duplicate check and the readback compare the same string.
8. **Ambiguity fails closed:** if two accounts shorten to the same name, keep one more word of the tail for both.

## 2. Worked examples
| Salesforce name | Tenant name |
|---|---|
| Barmherzige Brüder gemeinnützige Träger GmbH | Barmherzige Brüder gemeinnützige TG |
| Advanced Technology International (short enough) | unchanged |
| Example Städtische Kliniken Betriebs gGmbH | Example Städtische Kliniken BG |

## 3. Text for the Guru card (paste)
"Long account names: if the Salesforce account name is longer than 40 characters, keep the first two words exactly, turn the last word plus the legal form into capital initials (Träger GmbH = TG), and drop only filler words if it is still too long. Use the Salesforce name unchanged when it is 40 characters or fewer. Write the short name in the CO so everyone uses the same string."

## 4. What changes in the automation (NOT built)
- Today the tenant name is the bare Salesforce `Account_Name__c`; `tenant_duplicate_policy` treats an abbreviated name as ambiguous for review. A shortened name needs ONE explicit source (a short-name field or an owner-approved override on the CO), compared against the Salesforce full name by rule, never guessed by the code.
- The primary-user e-mail alias is derived from the account name; it must keep using one agreed string (the short name).
- Decision needed from the owner: approve the rules, set the length limit, and choose where the short name lives (Salesforce field, or the dashboard card that the owner confirms before create).

## 5. Exact edit for the Guru card "Surface Customer Onboarding - New Surface Account Only" (draft, read 2026-10-21; the owner pastes it)
**Where:** in the bullet that starts "Fill account details from Salesforce: company name, ...", add the block below as a sub-bullet directly under it, so it sits at the exact step where the name is typed. The card has no other place that mentions the name.

**Draft text (sub-bullet under "Fill account details"):**
- **Company name length rule:** use the Salesforce account name exactly as written. If it is longer than 40 characters, shorten it like this: keep the first two words, then turn the last word plus the legal form into capital letters (Träger GmbH = TG). Example: "Barmherzige Brüder gemeinnützige Träger GmbH" becomes "Barmherzige Brüder gemeinnützige TG". Only if it is still too long, drop small words like "und" or "für". Never change the first two words. Write the short name in the onboarding request so everyone uses the same name.

## 6. Rewrite of the card step "Save the account, then search for it..." (draft; the owner pastes it)
**Replace the bullet** "Save the account, then search for it and click on details, update the SF request with the UID and the UUID and save. Then move the request status to Account Scanning and Mark as Current Onboarding Stage." **with a numbered block:**

**After you click Confirm (the account is created):**
1. In Tenant Management, type the company name in the search box and open the account (click the row, tab **Details**).
2. Copy **Account ID** (24 characters). The create does not show it, so you must read it here.
3. Copy **Account uuid** (32 characters).
4. Open the Salesforce onboarding request (CO). Paste Account ID into **Surface Account ID** and Account uuid into **Account UUID**. Check each one letter by letter against BackOffice, then **Save**.
5. Move the request status to **Account Scanning**, then click **Mark as Current Onboarding Stage**.
6. Confirm the account shows a scan started (Scan column / Last scan). Do not create the account twice.

Note: the two IDs are different values; do not swap them. Salesforce API names are `Surface_Account_ID__c` and `Account_UUID__c` (confirm the screen labels before publishing).
