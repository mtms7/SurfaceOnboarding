# Phase 2 Case 3 OPA Preflight Deployment

Date: 2026-08-27  
Deployment ID: `20260827-184608`  
Host: `workato-opa-01` (`172.26.37.20`)  
Scope: OPA-side validation and draft-manifest preparation only

## Verified outcome

The owner-provided deployment console output established that:

- every allowlisted package file passed SHA-256 verification before execution;
- the 33 focused `phase2_leonardo` staging/live tests passed;
- `GET http://127.0.0.1:8789/healthz` returned `status=ok`, capability
  `phase2_case3_preflight`, and policy
  `surface-case3-owner-2026-08-27-v4`;
- TCP port `8789` listened only on `127.0.0.1`;
- `workato-agent.service` remained `active`; and
- the deployment completed as ID `20260827-184608`.

Two earlier attempts failed closed before installation. The first detected
Windows carriage returns in the checksum manifest. The second passed checksum
verification but stopped when the sanitized CO-0697 regression fixture was
absent from staging. Both packaging defects were corrected before the
successful deployment.

## Deployed capability

The loopback service provides:

- `POST /v1/phase2/prepare-case3` for strict Case 3 normalization,
  Surface/Core entitlement and term validation, Public Suffix List domain
  classification, temporary date-only Onboarding Comments generation, and an
  unapproved draft manifest; and
- `POST /v1/phase2/validate-manifest` for contract, approval, duplicate,
  idempotency, and prior-state policy evaluation.

The service has no external HTTP client, browser runtime, credential handler,
Salesforce writer, Workato mutator, or Leonardo executor. It suppresses HTTP
request logging, sets `Cache-Control: no-store`, and always leaves external
execution unauthorized.

Deployment `20260827-184608` passed checksum verification, 33 remote tests,
health, loopback-listener, and Workato-agent service-state verification. Policy
v4 implements the owner-recorded distinct-root domain count, deterministic
Pentera plus-address rule, and nullable Operator Account. MFA remains a
security invariant rather than a commercial-mapping approval. The deployment
fails closed unless exactly one Credential Exposure root domain is supplied
when no authoritative additional-domain add-on mapping is bound to DealHub
evidence. No Workato caller has yet been saved or tested against v4.

## Boundaries and remaining gates

This deployment does **not** establish that the Workato Development router is
connected to port `8789`. It does not prove a masked CO-0717 OPA request, a
Leonardo duplicate search, a hash-bound one-run approval, an idempotency lock,
an attended browser runner, account creation, read-after-write verification,
or Salesforce writeback.

The next safe action is one masked Workato Development test that sends only the
strict CO-0717 normalized intake to `/v1/phase2/prepare-case3`, confirms
`proceed=false`, captures only redacted operational evidence, and leaves the
recipe inactive. Customer values must remain masked in Workato job data.

## Subsequent local hardening (not deployed in this evidence)

On 2026-08-31 the local package was hardened as
`surface-case3-partial-owner-mapping-2026-08-31-v5` with preflight response
contract `surface-case3-preflight-v2`. The execution-approved mapping set is
empty; the response labels the output as a candidate requiring owner review and
lists unresolved field groups while keeping both authorization booleans false.
Customer-derived test examples were replaced with synthetic RFC-style values.

This section is local implementation evidence only. It does not change the
verified v4 deployment facts above. A new remote checksum/test/health/listener
verification and a new deployment identifier are required before Workato may
test against v5.
