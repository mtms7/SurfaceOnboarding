# Production CE-only: decisions D1-D6 (question, recommendation, your decision) - 2026-10-16

Source: `docs/42` section 5. **Nothing here is approved until you write your decision and the date.** Until then production BackOffice (BO) writes stay blocked, `PRODUCTION_RUNNER_SUPPORTED` stays `False`, and no production read happens.

Candidate: **CO-0797, Grupo Patio**, Credential Exposure (New), Request Approved, route CE-only, queue "Ready to onboard", start May 21 (148 days overdue, so the timing rule "start minus 2 days" is already open).

## D1 - Written approval scope for production writes
**Question:** What exactly may one approval allow, for how long, and who may issue it?
**Recommendation:** CE-only, exactly one named CO per approval (CO-0797 first), expiry 7 days (the code allows up to 31), issued by you (Milton) only, with SecOps informed by a note, not blocking. Reference format like `PROD-CE-0797-20261016`. The create consumes the approval; a retry needs a fresh go from you. The approval records the Salesforce revision of the CO and fails if it drifts.
**Decision:** ACCEPTED 2026-10-16 (owner): as recommended. CE-only, exactly one named CO per approval (CO-0797 first), expiry 7 days, issued by Milton only, consumed by the create, SecOps informed by note.

## D2 - Rollback for a wrongly created tenant (hard gate)
**Question:** If a production tenant is created wrongly, how is it removed or neutralized, and who does it? (No BO delete/disable path is documented.)
**Recommendation:** Ask the BO owner for a written answer before any create (suggested wording: "If I create a Credential Exposure tenant in BO production by mistake, can you delete or disable it, how long does it take, and who do I ask?"). Treat "no answer" as "no production create". Interim safety: dry run first, one CO, never re-click Confirm on an uncertain outcome.
**Decision / BO owner and answer:** DECIDED 2026-10-16 (owner), differs from the recommendation: no BO delete/disable owner answer was obtained. Instead, if a tenant is created wrongly, the run records its details (tenant name, account id, UUID, domains, plan, key form fields; no secrets) in a local, redacted record, and the owner decides whether to rename or fix the parameters in BO by edit. Residual risk accepted by the owner: a delete/disable path is still unknown. Conditions I add: one CO per approval; dry run before any create; an uncertain submit is never re-clicked; the D4 probe must confirm that BO production offers an Edit path (read only, nothing saved).

## D3 - Salesforce UUID/stage writeback in production
**Question:** Does this tool write the tenant UUID/stage back to Salesforce, or does the CSM do it (Q13)?
**Recommendation:** The tool writes nothing to Salesforce for the first production creates (writeback stays off, as today). The CSM or you record the UUID. Revisit after three clean runs.
**Decision:** DECIDED 2026-10-16 (owner), differs from the recommendation: for CE-only New (New Credential Exposure only), the tool collects the Account UUID after the verified create and writes it to Salesforce `Customer_Onboarding__c.Account_UUID__c` (the existing Salesforce IDs panel and write plan, today Dev-only). Scope: that one field on that one CO; no stage change, no other fields; Surface-only and renewals excluded. Conditions I add: only after BO readback_verified; value is a 32-hex UUID read from the readback; the field must be empty (or equal) and the CO revision unchanged; a separate explicit go per write; read-after-write verification in Salesforce; uncertain results never retried blindly.

## D4 - Read-only BO production probe
**Question:** May one attended, read-only probe of BO production run (login page, create-form field names, `getAllDetailedAccounts`, `account/add` response shape, tenant search and readback) with MFA typed by you?
**Recommendation:** Yes, once, in the dedicated profile `backoffice-production-automation` (never your Dev profile), no create, no click on Confirm, results recorded as field names and pass/fail only (no values), compared with the Dev contract. SecOps is told, not asked, if your policy allows read-only access by you.
**Decision:** ACCEPTED 2026-10-16 (owner): as recommended. One attended read-only probe, dedicated profile, field names only, no create.

## D5 - BO operator field at create
**Question:** Leave the operator field empty at create, as in Dev (docs/38 S4)?
**Recommendation:** Yes, empty, confirmed against the D4 probe (if production requires one, stop and ask).
**Decision:** ACCEPTED 2026-10-16 (owner): as recommended. Operator field empty, confirmed against the D4 probe.

## D6 - First candidate and timing
**Question:** Is CO-0797 the first production create, and does the start-minus-2-days rule apply (or a CSM request)?
**Recommendation:** Yes, CO-0797. Its start date is already past, so no CSM request is needed. It must still pass: fresh Redash clone (collected by you, under 6 h old), no duplicate name or domain in production, not more than one live paid tenant with the exact name, exactly one valid email domain, no prior UUID.
**Decision:** ACCEPTED 2026-10-16 (owner): as recommended. CO-0797 (Grupo Patio) first; clone under 6 h, no duplicate, one valid email domain (patio.cl seen in Salesforce), Account UUID empty (seen in Salesforce).

## What happens after you decide (my side, local, no production access)
1. Record D1-D6 in the plan with the date.
2. Build and test the guarded production path (docs/42 section 3 and 4), one reviewed commit per change.
3. Refresh the Redash clone (you run it), D4 probe (you sign in, I read), dry run without Confirm, your explicit go, one create, readback.

**Honest timing:** the production path is not built yet and D2 needs an answer from the BO owner. If D2 is answered quickly, the build, probe and dry run are a few hours of work; the create itself is last. If D2 is not answered today, CO-0797 can still be onboarded in Leonardo Development today (the dashboard is ready), and production follows when the gate is open.
