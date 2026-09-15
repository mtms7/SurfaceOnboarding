# Phase 1 commercial evidence: offline extractor

`commercial_evidence.py` processes a single local PDF, DOCX, or text fixture.
It is read-only: it does not call Salesforce, Workato, Surface, or BackOffice;
does not retain a document copy; and does not return source text or customer
PII.

## Current rule

The rule accepts `Pentera Core Plus Enterprise` or `Pentera Core Plus
Commercial` only when the same `Subscription Details` section explicitly says
it includes the Credential(s) Exposure Module. Missing or ambiguous
dates/wording always return unverified.

Scanned PDF OCR output is also unverified until a human visually confirms the
source. That rule keeps the workflow fail-closed.

## Local use

Use a Salesforce ContentVersion reference, never a customer filename:

```bash
python3 -m phase1_validator.commercial_evidence \
  --input /secure-temp/contract.pdf \
  --document-ref ContentVersion:068-masked
```

For a scanned PDF, `pdftoppm` and `tesseract` must both be installed on the VM
and local OCR must be explicitly enabled:

```bash
python3 -m phase1_validator.commercial_evidence \
  --input /secure-temp/contract.pdf \
  --document-ref ContentVersion:068-masked \
  --allow-local-ocr
```

Do not expose this script to Workato yet. The future VM-only Salesforce adapter
will perform read-only document discovery/download; Workato will send only a
CO ID/number and normalised DealHub facts, never a document, filename, URL, or
Salesforce credential.

## Attended Account-PDF check

`tools/validate_account_ce_entitlement.py` is the controlled desktop helper.
It first checks DealHub. When an explicit `Credential Exposure Module` product
with a valid quantity exists, no PDF is downloaded. Otherwise it needs a
normal Salesforce CLI browser session and an **operator-selected** Account-
linked PDF `ContentVersion` ID. It verifies that the selected version belongs
to the CO Account before downloading it into a private temporary directory,
processes it locally, deletes it automatically, and prints only a sanitised
decision. It never chooses a document by filename and never writes Salesforce.

An explicit DealHub `Credential Exposure Module` wins and its positive quantity
becomes the Email Domains limit. Otherwise, explicit verified Core Plus bundle
evidence supplies the owner-defined one-email-domain baseline. Every result is
review-only (`proceed: false`).
