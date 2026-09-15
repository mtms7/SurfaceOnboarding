# Phase 1 Contract Evidence - Resume Handoff

**Prepared:** 2026-08-14  
**Owner:** Milton Stevenson  
**State:** Shadow mode only - no Salesforce, Slack, Workato, BackOffice, or tenant write is authorized.

> Read this file first when resuming. It supersedes the immediate-task sections
> of `10_NEW_CHAT_HANDOFF_2026-08-13.md` for the Phase 1 contract-evidence work.

## Current Objective

Build a deterministic, fail-closed comparison between active DealHub
subscription facts and the corresponding commercial PDF/DOCX evidence. A
mismatch must return `manual_review_required`, `proceed: false`, and
`action_required: contact_csm`. It must not select a winning source or create a
tenant.

## Guardrails

- Keep all Workato recipes stopped. Use one-time **Test** only; never click
  **Start recipe** for the scheduled connectivity recipe.
- Workato must never receive a contract file, customer filename, document URL,
  Salesforce session/token, or raw extracted document text.
- The VM processes a document only in a restrictive temporary directory and
  deletes the temporary copy after the controlled test.
- Do not run `apt upgrade`, `apt-get upgrade`, or `dist-upgrade`.
- No production BackOffice (`app.pentera.io`) action, Salesforce write, Slack
  notification, approval task, Playwright action, or tenant configuration is in scope.
- Missing, ambiguous, conflicting, expired, or OCR-only evidence remains
  `unverified`; the workflow fails closed.

## Completed and Verified

### Workato -> OPA -> local validator

- Workato connection: `phase1-validator-via-opa-shadow`.
- OPA group: `surface-onboarding-rnd`.
- Local endpoint: `http://127.0.0.1:8787` on `workato-opa-01`.
- Health check through Workato succeeded with HTTP `200`, OPA gateway proof,
  and `{"status":"ok","policy_version":"phase1-shadow-v1"}`.
- A synthetic mismatch request to `/v1/validate` succeeded. The deliberate
  end-date conflict correctly returned `manual_review_required`, `proceed:
  false`, and `action_required: contact_csm`.

### VM status

| Item | Verified state |
| --- | --- |
| Host | `workato-opa-01` / `172.26.37.20` |
| Workato agent | `workato-agent.service` active after package installation |
| Validator health | `GET http://127.0.0.1:8787/healthz` returns policy `phase1-shadow-v1` |
| PDF renderer | `pdftoppm` 22.02.0 installed |
| Local OCR | Tesseract 4.1.1 installed |
| Python environment | `/home/workato/phase1-venv` |
| PDF reader | `pypdf` 6.16.1 installed in the isolated virtual environment |

The approved package installation used `poppler-utils`, `tesseract-ocr`, and
`python3-venv`. Ubuntu also applied three related Python runtime package
updates and restarted `workato-agent.service`; the agent was subsequently
confirmed active. No general OS upgrade was run.

### Source code and tests

The following files exist locally and were copied successfully to the VM:

- `phase1_validator/commercial_evidence.py`
- `phase1_validator/test_commercial_evidence.py`

The source also contains `phase1_validator/COMMERCIAL_EVIDENCE.md` with the
operational contract and safeguards.

Local verification passed:

```text
Ran 5 tests ... OK
```

The supplied signed contract PDF was inspected locally without persisting a
copy. It has no embedded text layer, so the extractor correctly returned
`ocr_required`; with OCR absent on the workstation it returned
`local_ocr_unavailable`. It did not guess dates or entitlement. Temporary
rendered images were deleted.

## CO-0693 Evidence Observed by Human Visual Review

The supplied Subscription Details page shows the following commercial-evidence
candidate:

| Normalized field | Evidence |
| --- | --- |
| Package | `Pentera Core Plus Enterprise` |
| Scope | `10,000 End Points` |
| Start date | `2026-07-31` (from `31-07-2026`) |
| End date | `2027-07-30` (from `30-07-2027`) |
| Current state on 2026-08-14 | `active` |
| CE bundle wording | The package explicitly lists the `Credentials Exposure Module` |

This supports bundled Credential Exposure evidence, but it is **not yet a
complete CO-0693 decision**. The next validation must compare the normalised
DealHub facts and must consider every relevant active commercial document. A
standalone CE row is not required when an active, explicit bundle is confirmed.

## Immediate Resume Step

The network name `workato-opa-01` does not resolve from the Windows workstation,
but TCP/22 to `172.26.37.20` succeeded. SCP using the IP also succeeded after a
password retry. No administrator privileges were required for the copy.

On the VM, run only:

```bash
cd /home/workato
ls -l phase1_validator/commercial_evidence.py phase1_validator/test_commercial_evidence.py
/home/workato/phase1-venv/bin/python -m unittest phase1_validator.test_server phase1_validator.test_commercial_evidence
```

Expected result:

```text
Ran 5 tests ... OK
```

Stop there and capture the output. Do not upload the customer PDF or change a
Workato recipe during this checkpoint.

## Next Step After Passing VM Tests

Only after the VM unit tests pass, perform one separately controlled local OCR
test with a single approved commercial PDF:

1. Create a VM temporary directory owned by `workato`, mode `700`.
2. Transfer one PDF with a generic temporary name, never its customer filename.
3. Run `commercial_evidence.py` using the virtual-environment Python, with
   `--allow-local-ocr` and a safe document reference such as
   `ContentVersion:local-test-masked`.
4. Record only the sanitized JSON result. Never print raw OCR text.
5. Delete the temporary PDF and empty temporary directory immediately after
   the test. The original workstation PDF must remain unchanged.

Expected OCR behavior is still fail-closed: the parser can extract dates and
the CE clause, but sets `credential_exposure: unverified` with
`ocr_requires_visual_confirmation` until a human verifies the rendered source.

## Future Architecture - Do Not Implement Yet

1. A dedicated VM-local, read-only Salesforce adapter discovers relevant
   `ContentDocumentLink`/`ContentVersion` records for the CO and downloads the
   selected document only to controlled temporary storage.
2. The extractor normalizes product, date, scope, and explicit CE bundle
   wording; it retains no raw document content.
3. The existing validator compares this evidence to normalized DealHub data.
4. Workato sends only a CO identifier and normalized DealHub facts through
   OPA, then displays the sanitized shadow result as job output.

Do not create this Salesforce adapter or add an HTTP action to the live CO
router until the offline extractor and document selection rules are proven.

## First Prompt for a New Chat

```text
Read docs/11_PHASE1_CONTRACT_EVIDENCE_HANDOFF_2026-08-14.md first. Continue
the Surface Phase 1 contract-evidence work in shadow mode. I am a Workato
beginner: give exact, one-step-at-a-time instructions, say whether each step is
read-only, and stop for output after each checkpoint. No Workato recipe start,
Salesforce/Slack/BackOffice write, browser automation, or customer-document
transfer is allowed until explicitly approved. The immediate task is to run the
copied VM unit tests using /home/workato/phase1-venv/bin/python.
```
