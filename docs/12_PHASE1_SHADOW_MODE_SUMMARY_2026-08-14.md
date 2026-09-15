# Phase 1 Shadow Mode - Implementation Summary

**Date:** 2026-08-14  
**Purpose:** Current technical summary for the Surface onboarding validation work.

## Outcome

The Phase 1 shadow-mode path is proven end to end for synthetic data:

```text
Workato HTTP action -> OPA group -> VM-local validator -> sanitized decision
```

The deliberate DealHub/contract end-date mismatch returned HTTP `200` and the
correct safe decision:

```json
{
  "decision": "manual_review_required",
  "proceed": false,
  "action_required": "contact_csm"
}
```

No production or customer-side action was performed.

## Implemented Components

| Component | Purpose | State |
| --- | --- | --- |
| `/healthz` | VM-local service health proof | Verified |
| `/v1/validate` | Deterministic normalized DealHub-vs-contract comparison | Verified with synthetic mismatch |
| `commercial_evidence.py` | Offline PDF/DOCX/text extraction and narrow Core Plus CE rule | Local code complete; VM test pending |
| `test_commercial_evidence.py` | Parser safety/regression checks | 5 local tests passing; VM test pending |
| Local OCR dependencies | Render scanned PDFs locally, never in Workato | Installed on VM |

## Evidence Rules

- License start/end dates come from the product subscription details, not the
  filename, signature date, Salesforce modified time, or offer-valid-through date.
- Credential Exposure is `bundled_verified` only when every relevant active
  commercial document is consistent and an explicit purchased-package clause
  includes the Credential(s) Exposure Module.
- A missing standalone DealHub CE row is not evidence that CE is absent.
- Any missing field, conflicting active document, ambiguous selection, unreadable
  content, or OCR-only output blocks automatic progress.

## Current Limits

- The supplied CO-0693 example is a scanned PDF, requiring local OCR.
- OCR output cannot automatically establish entitlement; a human must visually
  confirm the source before CE becomes verified.
- Salesforce document discovery/download is not built yet.
- No live CO router integration exists for the commercial-evidence step.

## Safe Next Milestones

1. Run the copied parser tests on `workato-opa-01`.
2. Run one controlled local OCR test using a generic temporary filename and
   return sanitized JSON only.
3. Define read-only Salesforce `ContentDocumentLink` discovery and a strict
   contract-selection policy.
4. Add one OPA-backed Workato HTTP action after the existing normalized DealHub
   step, retaining results only as shadow job output.

## Persistent Controls

- Keep Workato recipes stopped; use one-time **Test** only.
- Never send documents, customer filenames, Salesforce credentials, or raw OCR
  text through Workato.
- Keep all external systems read-only during Phase 1.
- Treat every validation success as review-only: `proceed` remains `false`.
