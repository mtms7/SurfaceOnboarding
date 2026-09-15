# Phase 1 Validator (Shadow Mode)

This is a localhost-only, read-only comparison service for the Surface onboarding router. It compares **normalized** DealHub and contract evidence fields. It does not read Salesforce, parse PDFs/Word files, store request data, write files, or call Surface/BackOffice.

## Safety contract

- Binds only to `127.0.0.1:8787` by default.
- Contains no credentials or external HTTP calls.
- Does not log requests or write data to disk.
- Accepts requests up to 256 KiB; raw PDF/Word data must never be sent to it.
- Ignores contract metadata such as filenames, document URLs, and raw OCR text;
  those values are never part of the response contract and must not be sent by Workato.
- A matching result is still `proceed: false` because this is shadow mode.
- A missing or mismatched field returns `manual_review_required` and `contact_csm`.

## Local checks

Run from the project root:

```powershell
python -m unittest phase1_validator.test_server
```

## Ubuntu VM smoke test

Copy the `phase1_validator` folder to a non-sensitive path owned by the `workato` user. Then, from its parent directory:

```bash
python3 -m phase1_validator.server
```

In a second terminal on the same VM, use synthetic data only:

```bash
curl -sS http://127.0.0.1:8787/healthz
```

The service is intentionally not a systemd unit yet. Stop the foreground process with `Ctrl+C` after the smoke test.

## API

`POST /v1/validate` expects normalized field values under `dealhub` and `contract`. It compares these fields:

```text
product, start_date, end_date, status, subscription_name, license_type, quantity
```

It returns `manual_review_required` for any missing or mismatched field. A match returns `approved_for_review` but always sets `proceed` to `false`.

### Source-bound Phase 2 endpoint

`POST /v1/validate-bound` preserves the same shadow decision while adding a
strict, redacted evidence envelope for the Phase 2 Workato router. It requires:

```text
co_number, sf_record_id, source_revision, request_id, policy_version,
dealhub, contract
```

`dealhub` and `contract` must each contain exactly the seven comparison fields.
Unknown fields, duplicate JSON keys, malformed dates, invalid quantities,
invalid identifiers, and a mismatched policy version are rejected. An empty
value is permitted only to exercise the explicit missing-evidence fail-closed
path; it still returns `proceed: false`.

The response echoes only the control identifiers, returns a canonical
`evidence_hash` and bound `result_hash`, and reports mismatch field names and
types without the underlying values. Workato must re-read the Salesforce ID
and `SystemModstamp` after validation and discard the result if the revision
changed. This endpoint does not authorize provisioning or any downstream call.

## Onboarding Comment subscription-date extraction

`onboarding_comment_dates.py` is a local, side-effect-free parser for the
business-approved `YYYY-MM-DD - YYYY-MM-DD` DealHub term pattern in Salesforce
`Onboarding Comments`. It returns only normalised start/end dates and a queue
state; it never returns, logs, or writes the comment itself.

A single valid range produces `ready_for_cse_review`, with both `proceed` and
`eligible_for_automation` still `false`. Missing, invalid, reversed, or
multiple ranges produce the fail-closed `blocked` state and require CSE manual
validation. The future Workato implementation must retain raw comments only in
the restricted private store, with masking and approved retention; the
dashboard-safe view may expose only the derived fields.
