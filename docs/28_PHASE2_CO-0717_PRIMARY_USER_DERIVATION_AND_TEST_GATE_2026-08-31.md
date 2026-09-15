# Phase 2 CO-0717 Primary User derivation and test gate

Date: 2026-08-31  
Status: **local derivation and Salesforce relationship readback verified; first Workato attempt blocked before execution; corrected test remains pending**  
Target: Leonardo Development validate-only preparation

## Verified local facts

- The Case 3 v2 intake requires first name, last name, organization email,
  phone, job title, and an explicit nullable Operator Account.
- The OPA preflight derives the customer-specific alias only when the source
  organization mailbox belongs to `pentera.io`. It rejects an already
  plus-addressed Pentera mailbox so that a stale or manually constructed alias
  cannot be silently reused.
- The alias tag is derived from the normalized combined-account name. The
  normalization is ASCII-safe and removes a trailing `- CE Only` suffix for a
  combined Surface and Credential Exposure account.
- One- and two-word names concatenate all normalized alphanumeric words.
  Names with more than two words concatenate the first character of every
  normalized alphanumeric word. The result is lowercase and deterministic.
- Non-Pentera primary-user email addresses are preserved unchanged.
- MFA is always emitted as `true`. Operator Account may be `null`; its absence
  must not block the validate-only draft.
- Focused synthetic regression tests cover the two-word rule, the more-than-two
  initials rule, normalization before aliasing, external-address preservation,
  exact first/last-name propagation, mandatory MFA, and nullable Operator
  Account. No CO-0717 Contact or email value is stored in the tests.

## Owner-supplied intent, retained only as a masked statement

The owner stated that CO-0717 should reference the intended owner Contact and
use that Contact's exact first name, last name, and Pentera organization
mailbox. The combined account has a two-word normalized company name, so the
implemented rule deterministically selects the concatenated two-word alias
form. This document intentionally omits the restricted Contact and alias
values.

## Remaining ambiguity resolved by the current contract

The phrase "use the first letter when the account name is more than two words"
is interpreted as **the first character of every normalized alphanumeric
word**, not only the first word and not an arbitrary abbreviation. Punctuation
does not become a word. This is the behavior already encoded in v5 and now
covered by focused tests.

If the business owner instead intends stop-word removal, a curated acronym, or
special treatment of punctuation such as ampersands and hyphens, that is a new
mapping revision. It must fail closed until explicitly approved and tested; it
must not be inferred during a Workato or Leonardo run.

## Primary User gate before one Workato test

The authorized correction changed only `Primary_User__c`. A separate
read-only reconciliation confirmed exactly one CO, exactly one eligible
intended organization Contact, and an exact saved relationship match. Raw IDs,
email values, and the prior relationship are not retained here. The source
must still be reread immediately before the one-run test to bind its current
revision.

The Workato caller is not ready merely because the local derivation passes.
Immediately before the single authorized test, all of these checks must pass:

1. Re-read exactly one `CO-0717` source record.
2. Confirm its Primary User relationship resolves to exactly one Contact and
   is the owner-intended Contact.
3. Read first name, last name, and the original organization email from that
   Contact. Do not place a prebuilt plus alias in the request.
4. Confirm the source email is not already plus-addressed and that the account
   name normalizes without ambiguity.
5. Bind the CO source revision and the Contact identity/revision used for the
   request. Any change after review stops the run and requires a fresh read.
6. Keep the trigger, HTTP request, HTTP response, and all data-bearing guard
   steps masked. Logs and static stop messages must not contain user or account
   values.
7. Assert the complete v2 response contract: v5 partial mapping policy,
   unapproved mapping status, both authorization booleans false, both required
   hashes present and lowercase 64-hex, and the exact six-value
   unapproved-field-group set.
8. Stop without retry on zero/multiple Contacts, blank or malformed fields,
   relationship mismatch, source revision drift, schema drift, or any response
   assertion failure.
9. Obtain fresh action-time approval for exactly one masked Development
   transmission. Do not select **Test** or **Start recipe** under structural-edit
   approval alone.

## Boundaries

The first approved caller attempt passed the source gate but Workato created no
job because Step 15's engine comparison literal was in Formula mode. The
recipe remained inactive. The separately authorized structural correction is
now complete and the recipe was again verified inactive without Test or Start.
Any later test still requires a fresh source reread and new action-time
approval; the no-retry approval cannot be reused.

This checkpoint does not authorize or perform a Salesforce relationship
update, Workato test, Leonardo duplicate lookup, form fill, confirmation,
account creation, writeback, or production action. The OPA result remains an
unapproved validate-only draft and can never authorize an external action.
