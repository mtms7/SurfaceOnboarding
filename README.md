# Surface Workato OPA Onboarding Project

Date: 2026-07-10
Owner: Milton Stevenson
Recommended model for this project: 5.6 Terra

## Purpose

This folder is the implementation and handoff pack for the Surface Customer Onboarding automation in Workato using Workato On-Premise Agent (OPA).

The goal is to take a Salesforce Customer Onboarding record, run a guarded `Review CO-XXX` validation, ask for approval to proceed, execute the correct onboarding case workflow, retrieve the created account UUID, and update Salesforce after separate authorization.

Phase 2 development targets Leonardo Development (`https://leonardo.dev.app.pentera.io/`). The final production target remains BackOffice (`https://app.pentera.io`). Production writes require separate approval.

## Folder Contents

- `docs/00_PROJECT_HANDOFF.md`: what we learned, what we tried, and current decisions.
- `docs/01_TWO_WEEK_PLAN.md`: two-hour daily sessions for the next two weeks.
- `docs/02_OPA_UBUNTU22_VM_REQUIREMENTS.md`: minimum VM requirements and Ubuntu 22 OPA setup steps.
- `docs/03_WORKATO_RECIPE_ARCHITECTURE.md`: Workato recipe/component map.
- `docs/04_SECURITY_AND_RISK_REVIEW.md`: security controls, MFA, logging, and risk decisions.
- `docs/05_TEST_PLAN_AND_ACCEPTANCE.md`: testing gates and acceptance criteria.
- `docs/06_DECISION_LOG_AND_OPEN_ITEMS.md`: decisions, open questions, and reminders.
- `docs/07_SECURITY_REVIEW_EMAIL_THREAD.md`: Or/Ran security review email thread, architecture options, and sync agenda.
- `docs/15_PHASE1_SANITIZATION_AND_WORKATO_TEST_2026-08-21.md`: latest validator sanitization, VM verification, and one-time Workato test evidence.
- `docs/16_WORKATO_QUEUE_SYNTHETIC_HANDOFF_2026-08-22.md`: dashboard-safe queue and synthetic synchronization evidence.
- `docs/17_INACTIVE_REPEATABLE_QUEUE_TEST_HARNESS_2026-08-23.md`: inactive repeatable test, reconciliation, Workflow App, and CSE gate handoff.
- `docs/18_LEONARDO_READ_ONLY_DISCOVERY_2026-08-24.md`: Leonardo Development visible-UI field and security discovery.
- `docs/19_PHASE2_LEONARDO_SAFE_EXECUTION_HANDOFF_2026-08-24.md`: Phase 2 architecture, contracts, controls, blockers, one-week plan, and new-task prompt.
- `docs/20_PHASE2_CO-0697_EXECUTION_AND_API_REQUEST_PLAN_2026-08-24.md`: verified CO-0697 intake status, attended no-API request path, and prioritized Leonardo service/API request inventory.
- `docs/21_PHASE2_CO-0715_DECISION_MEMO_2026-08-26.md` through `docs/24_PHASE2_CO-0717_OWNER_MAPPING_APPROVAL_PACKET_2026-08-27.md`: current Phase 2 intake, diagnostic, validation-gate, and owner-mapping records.
- `docs/25_PHASE2_CASE3_OPA_DEPLOYMENT_2026-08-27.md`: prior v4 deployment history.
- `docs/26_PHASE2_CASE3_V5_VALIDATE_ONLY_HARDENING_2026-08-31.md`: v5 fail-closed contract and Workato hardening state.
- `docs/27_PHASE2_CASE3_V5_DEPLOYMENT_EVIDENCE_2026-08-31.md`: verified v5 deployment evidence, limits, and next safe gates.
- `docs/28_PHASE2_CO-0717_PRIMARY_USER_DERIVATION_AND_TEST_GATE_2026-08-31.md`: deterministic Primary User derivation and the remaining source-correction gate.
- `docs/29_PHASE2_CO-0717_LEONARDO_DEVELOPMENT_MAPPING_AND_FILLING_DECISION_MEMO_2026-08-31.md`: current mapping authority, attended phases, owners, approvals, and stop conditions.
- `docs/30_PHASE2_OPA_READ_ONLY_VERIFIER_TRANSPORT_FIX_2026-08-31.md`: SSH quoting failure diagnosis, exact transport fix, local validation, and live-retry boundary.
- `docs/31_PHASE2_CO-0717_CALLER_ATTEMPT_BLOCKED_BY_RECIPE_VALIDATION_2026-08-31.md`: masked source gate and the first authorized caller attempt, which Workato blocked before job creation because Step 15 treated the engine literal as a formula.
- `docs/32_PHASE2_WORKATO_STEP15_LITERAL_CORRECTION_2026-08-31.md`: authorized single-field Step 15 Text-literal correction, save/reload evidence, and inactive-state verification.
- `phase2_leonardo/`: strict validate-only contracts, fail-closed policy, fully synthetic blocked fixture, focused tests, and a pure-local attended-phase planner. It contains no external client or browser automation.
- `phase2_leonardo/attended_phases.py`: advisory, redacted phase-gate evaluation only; every phase is non-callable and the current v5 policy always remains blocked.
- `diagrams/surface_workato_opa_workflow.svg`: visual workflow diagram.
- `diagrams/surface_workato_opa_workflow.mmd`: Mermaid source for the diagram.
- `scripts/ubuntu_opa_preflight.sh`: Ubuntu connectivity validation script for OPA VM candidates.
- `scripts/workato_test_payloads.json`: safe sample payloads for recipe testing.

## Current Best Path

The loopback-only Case 3 validate-only service is deployed at v5. The Workato
caller remains inactive and has not run against v5. Its masked v2 request,
source-binding, zero-add-on, authorization/decision, hash-format, and exact
unapproved-group guards have passed save/reload verification. The intended
CO-0717 Primary User relationship was corrected and independently read back as
an exact match. The first separately approved masked caller attempt was blocked
by Workato before job creation or transmission because Step 15 treated the
engine literal as a formula. The recipe remained inactive and the no-retry
approval cannot be reused. The separately authorized single-field mode
correction is now complete: Step 15's value is the Text literal
`case_3_combined_baseline`, save/reload verification passed, the prior formula
error is absent, and the recipe remains inactive. No Test or Start action was
used. The next safe action is a new masked source/revision gate followed by a
fresh one-run approval. The supplemental OPA verification now passes
cleanly: one managed process, one IPv4-loopback listener, no UDP listener, the
synthetic v2 response, active agent state, and all four deployed/local file
hashes were verified. Leonardo execution and all production writes remain
blocked.

The current complete local suite passes 78 tests, including seven focused
attended-phase planner tests. The planner cannot perform an external action;
even forged ready-looking evidence for the v5 partial mapping remains blocked.

Use an Ubuntu 22 VM on the RND VPN path as the OPA host.

Validated RND lab evidence:

The Donatello observations below are historical connectivity evidence only. They do not establish a supported Leonardo API contract.

- Internal VM/lab: `172.26.37.12`
- Outbound NAT IP: `199.203.203.177`
- Workato OPA gateways reachable: `sg3.workato.com`, `sg4.workato.com`
- Donatello dev root returned `HTTP 200`
- Donatello unauthenticated API checks returned `HTTP 401`, proving requests reached the app/API layer instead of being blocked by WAF

## Critical Security Rules

- Do not remove MFA from any user-based Workato or BackOffice flow.
- Do not store Donatello or BO passwords in files, recipe comments, Slack, or job logs.
- Keep secrets in Workato environment properties or approved secure connections.
- Keep the OPA host outbound-only to Workato; no inbound Workato firewall ports are required.
- Bypass TLS inspection only for Workato OPA gateway FQDNs.
- Fail closed on ambiguous source data, duplicate accounts, WAF blocks, missing authorities, schema drift, or unexpected response bodies.

## Official Workato References Checked

- Workato on-prem connectivity: https://docs.workato.com/en/on-prem
- Linux DEB OPA install: https://docs.workato.com/en/on-prem/groups/add-agent/linux-deb
- OPA connections: https://docs.workato.com/en/on-prem/agents/connection
- Workato IP allowlists: https://docs.workato.com/en/security/ip-allowlists




