# Phase 1 Sanitization Deployment and Workato Test Record

Date: 2026-08-21  
Scope: Phase 1 validator sanitization, guarded VM deployment verification, and one authorized Workato test.  
Environment: Workato Development and `workato-opa-01` (`172.26.37.20`).

## Outcome

The Phase 1 notes-validator sanitization change was staged, tested, deployed to the existing validator host, and exercised through exactly one authorized Workato Test. The test completed successfully. The Workato recipe remains inactive, and no Salesforce, Donatello, BackOffice, provisioning, account, or production write was performed.

## Safety Baseline and Rollback

- A vSphere snapshot named `workato-opa-01 08-21-26` completed before deployment.
- Snapshot evidence showed a size of 4.45 GB, virtual-machine memory included, and guest file-system quiescing disabled.
- The pre-sanitization backup is stored on the VM at:
  `/home/workato/phase1-operations/20260821-sanitization/backup`
- Staged files are stored at:
  `/home/workato/phase1-operations/20260821-sanitization/staging/phase1_validator`
- Deployment evidence is stored with mode `0600` at:
  `/home/workato/phase1-operations/20260821-sanitization/evidence/vm-deployment-verification-20260821.txt`

Pre-sanitization backup hashes:

| File | SHA-256 |
| --- | --- |
| `server.py` | `bc045ba5543f11a9fe240ceba951478de2e966c510cd551c8a5451df09bb8a08` |
| `test_server.py` | `6e6e585882775b696bedd16068edf74fd8ef59ba30c565ed539dcbf0aa48944a` |

Deployed and local working-copy hashes:

| File | SHA-256 |
| --- | --- |
| `server.py` | `3ee8021f539f9ef8554aad90840fb45220196b05a6b82a35b7b40bdf0168dbb4` |
| `test_server.py` | `3d8c8646ab964df6bee35ab60ba38f3ab2f5487f7dce12acd05f2995c12de148` |

## Validator Verification

The focused local suite was rerun on 2026-08-21:

```text
Ran 10 tests in 0.003s
OK
```

The operator-provided VM verification showed the same 10 tests passing after deployment. The notes validator was restarted from `/home/workato`, and the replacement process was observed as PID `129040` running:

```text
/home/workato/phase1-venv/bin/python -m phase1_validator.server_notes
```

Port `8788` returned the expected health contract:

```json
{"status":"ok","policy_version":"phase1-shadow-v1","capability":"rejection_note"}
```

A synthetic runtime sanitization check returned the expected fail-closed decision and confirmed that the supplied synthetic filename, URL, and raw-text markers were absent from the response. No real customer document content was used.

## Authorized Workato Test

Authorization was limited to one read-only Workato Test of `OPA Notes Output Test - Phase 1 Validator`. The recipe was not scheduled or left running, and the separate Salesforce writeback recipe was not invoked.

| Item | Verified result |
| --- | --- |
| Workato environment | Development |
| Recipe version | 2 |
| Job ID | `j-AbJr8HQk-XasftT-CD` |
| Start time | 2026-08-21 12:17:07 PDT |
| Final status | Successful |
| Health action | HTTP 200; `status=ok`; policy `phase1-shadow-v1`; capability `rejection_note` |
| Synthetic validation action | HTTP 200; `manual_review_required`; `proceed=false`; `action_required=contact_csm`; rejection note requested |
| Successful-job count after test | 2 |
| Failed-job count after test | 0 |
| Recipe state after test | Inactive |

Workato records a one-time Test as temporary Started and Stopped activity. Direct post-test inspection confirmed that the recipe returned to and remained `Inactive`. The `Start recipe` control was not used.

## Boundaries Preserved

- No Salesforce field or record was changed.
- No rejection-note writeback recipe was executed.
- No Donatello or BackOffice authentication was attempted.
- No authority or duplicate-account API was called.
- No account was created, updated, or provisioned.
- No OPA installation, activation, or infrastructure configuration was performed.
- MFA and fail-closed behavior remain required.
- No credentials, MFA codes, bearer tokens, cookies, activation commands, raw sensitive payloads, or document metadata are recorded here.

## Current Project Position

The one-hour safe validation package is complete:

1. Safe host and service baseline — complete.
2. Snapshot, backup, and rollback hashes — complete.
3. Sanitization staging and focused tests — complete.
4. Guarded deployment and runtime verification — complete.
5. One authorized read-only Workato Test — complete.
6. Local evidence and handoff — complete with this record.

## Next Approval Gate

The next external phase is a separately authorized Donatello-dev read-only authentication, authority, and duplicate-check test. Before that phase, obtain a recorded scope decision from Or, Ran, Elad, SecOps, and the relevant Surface/BackOffice owner. The approval must explicitly prohibit account creation, Salesforce writeback, and production BackOffice access.

The following unresolved owner decisions remain in force:

- Networking/SecOps: confirm the Workato gateway TLS-inspection posture.
- Donatello/Surface: confirm the approved dev authentication method and required read-only authority scope.
- Salesforce owner: confirm the eventual `accountUuid` target field before any future UUID writeback.

Until those approvals are recorded, keep Workato, Salesforce, OPA configuration, Donatello, and BackOffice read-only and keep the tested recipe inactive.
