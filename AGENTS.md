# Surface Workato OPA Agent Instructions

## Scope

This repository implements and documents the guarded Surface Customer Onboarding automation from Salesforce through Workato and OPA to Donatello/BackOffice. Use the `surface-workato-opa` skill for project-specific work.

## Source Priority

Start with `README.md` and `docs/08_TECHNICAL_IMPLEMENTATION_GUIDE.md`. Open the detailed architecture, security, test, or decision documents only as needed. Use newer dated evidence to establish observed state, but do not relax an approval or safety gate unless an authorized decision explicitly does so.

## Working Rules

- Inspect current implementation and tests before changing files.
- Keep router engine values, output contracts, rejection states, idempotency, approval gates, and read-after-write verification compatible with the documented architecture.
- For `phase1_validator` changes, update focused tests and run the relevant suite.
- Distinguish verified facts from assumptions and historical evidence.
- Preserve unrelated user files and changes.

## Security and External Systems

- Local edits and read-only analysis do not authorize external Workato, Salesforce, OPA, Donatello, or BackOffice mutations.
- Donatello dev is the intended test environment; production BackOffice writes are blocked without separate explicit approval.
- Preserve MFA. Never expose or persist passwords, MFA codes, bearer tokens, cookies, OPA activation commands, or raw sensitive payloads.
- Fail closed for ambiguity, mismatched source data, unsupported routes, duplicates, missing authority, authentication/WAF failures, inactive OPA, unexpected schemas, or missing/expired approval.
- Do not claim an external change succeeded without direct verification.

## Communication

Lead with the current outcome and next safe action. List blockers and the owner decision required to clear each one. For advisory or diagnostic requests, do not perform external writes.
