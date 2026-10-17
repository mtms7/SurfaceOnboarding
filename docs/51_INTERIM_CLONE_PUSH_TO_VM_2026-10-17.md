# 51. Interim clone push to the VM (2026-10-17)

Status: design plus new, untested-on-the-VM scripts. Nothing here is installed, registered or run against the VM or Redash by the
repository. Every VM step is operator-run and needs the owner's separate go. The final solution is unchanged: the VM collects the
clone itself (`_step18a/b/c`, `surface-onboarding-redash-collect.*`) once SecOps allows outbound 443 to `redash.pentera.io`.

## 1. Problem and facts

* The VM dashboard trusts the production clone only when it is 6 h old or less (`production_create.CLONE_MAX_AGE`); older means the
  production checks fail closed and the banner shows the true age.
* The VM cannot reach Redash (outbound 443 times out). The PC can (on VPN). Redash query 251 now refreshes hourly on the owner's
  schedule, so the cached result is fresh hourly; the collector's own refresh POST still fails with the per-query key, which
  `--cached-ok` treats as an honest state.
* Today the clone reaches the VM only by a human: collector, `publish_vm_snapshot.py`, `scp`, `_step12a`.
* The dashboard already prefers `<state>/clone-inventory/prod-clone/latest.json` over the clone inside the signed snapshot
  (`state_paths.clone_inventory_root`), and the VM Redash collector writes exactly there. The interim path writes there too, so it
  and the final path are interchangeable and never conflict.

## 2. Recommended design (push from the PC, restricted key)

```
PC task (hourly, signed in, VPN up)                                    VM (172.26.37.20)
 1 redash_inventory_collector.py --collect --cached-ok  (DPAPI key)
 2 build_clone_push_bundle.py  -> signed zip of exactly 2 files
 3 ssh -T -i clone_push_ed25519 workato@VM clone-push < bundle.zip ---> sshd: command=gate,from=<VPN>,restrict
                                                                         gate: marker present? -> sudo -n (no args) ->
                                                                         ingest program: flock, env -i, timeout 300 ->
                                                                         tools/ingest_clone_push.py (stdin)
   <------ one line: ingested|unchanged|refused:<code> ----------------- writes <state>/clone-inventory/prod-clone atomically
```

* **Clone-only, no Salesforce.** `build_clone_push_bundle.py` reads only the local clone pair the collector wrote, re-verifies it
  (hash, name, environment, at most 6 h old, not from the future) and signs a bundle with the existing snapshot HMAC format
  (`vm_snapshot.build_bundle`). `publish_vm_snapshot.py` has no clone-only mode (`--only` selects state files and skips the
  inventory), and a full snapshot ingest would replace `current` and drop the VM refresher's `queue.json`; so a separate bundle that
  never touches `snapshots/` is both smaller and safer. `publish_vm_snapshot.py` is unchanged.
* **VM validation** (`tools/ingest_clone_push.py`, stdlib only, the bundle comes on stdin, size capped, never executed or
  extracted to a client-chosen path): `verify_bundle` (HMAC, member names, sizes, sha256, no links or encryption); exactly the two
  clone members; producer `desktop-clone-push`; bundle time not in the future (5 min skew); the clone re-verified by
  `inventory.load_latest` (hash, name prefix `redash-prod-clone-`, environment `prod-clone`, at most 6 h old, not future); refuse
  older-than-current and shrink below 90% of the current row count; same bundle again is `unchanged`; write with the inventory's own
  atomic writer (snapshot file, then `latest.json`), keep 4; read back and compare the hash. Any refusal leaves the previous clone
  current. Output is one line of codes and counts.
* **Age is honest.** The clone's age is the Redash result's `retrieved_at`, not the push time, so a stale Redash cache is never
  disguised as fresh.
* **Forced-command key**, same pattern as the tunnel key (`_step15c`): dedicated ed25519, `command=` pinned to a root-owned gate,
  `restrict` (no pty, no port/agent/X11 forwarding, no rc), `from=<PC's VPN address>`, tagged `surface-clone-push` for exact revert.
  The gate ignores the client's command, requires the operator-created approval marker `/etc/surface-onboarding/approved-clone-push`
  (same pattern as `approved-redash-collector`), and reaches the service user only through a sudoers rule for one root-owned program
  with no arguments (validated with `visudo -cf`).
* **Cadence.** Hourly task; worst case clone age is about 2 h (Redash refresh hour plus collector hour plus push); the 6 h gate
  tolerates 3 or 4 missed cycles.

## 2a. Hardening added after review

* **High-water record** `/var/lib/surface-onboarding/clone-push-high-water.json` (largest row count and newest `captured_at` ever
  accepted). Older-than-ever and shrink (below 90% of the MAXIMUM, so 10% steps cannot compound) are checked against it even when
  `latest.json` is missing or corrupt. It is updated only after a verified install and read-back. A corrupt record refuses
  (`high_water_unreadable`); it is never silently reset. A deliberate clean-up: the owner deletes that one file on the VM.
  Not detectable: a different tenant set with the same count; the production gate must keep treating a missing match as non-clearing.
* **Ownership, stated plainly:** that file is owned by the service user `surface-onboarding`, NOT root (the ingest runs as that user).
  Acceptable because the only remote actor, the push key, can reach nothing but the ingest program and cannot write the file; a
  party able to write it already runs as the service user, who owns the clone and snapshot stores too (so root ownership would add no
  protection against that party). If root ownership is later required: have the root-owned ingest program (`_step22a`) keep the
  record at `/var/lib/surface-onboarding/..` with `chattr +a`, or pass the two numbers through a root-written file; not done now.
* **First-push floor:** with no high-water record and no valid current clone, at least 1000 tenants (`FIRST_PUSH_MIN_ROWS`;
  production is about 5,400), else `below_first_push_floor`. Only an owner-created root-owned file
  `/etc/surface-onboarding/clone-push-min-rows` (one integer) lowers it; nothing in a bundle can.
* **Approval marker enforced in the ingest program** (the root-owned program the sudoers rule runs, where privilege changes), as well
  as in the gate: without `/etc/surface-onboarding/approved-clone-push` the sudoers rule is useless (`refused:not_approved`).
* **Restore on failed read-back:** the previous `latest.json` is restored (or removed if there was none) before `readback_failed`
  is reported, so a refusal never leaves new data live; the high-water record is not advanced.
* **Also:** SIGTERM/SIGHUP cleanup and a sweep of `.incoming-*` folders older than 1 h; a full-snapshot ingest
  (`vm_snapshot.ingest_bundle`) refuses the `desktop-clone-push` producer.
* **`from=` rules (step 22B):** one IPv4 address or range with prefix /16 or longer, or one IPv6 address or range /48 or longer;
  0.0.0.0, `::` and any /0 are refused. **Re-run:** step 22B replaces its own tagged line (a changed `from=` takes effect) and
  recreates a removed marker; a same key present without our tag is refused.

## 3. Options for the VM to PULL (honest list)

| Option | Verdict |
| --- | --- |
| Redash directly | Blocked until SecOps allows it. This stays the FINAL solution (`_step18*`). |
| GitHub or other public Git/release host | Rejected: VM egress is closed (releases are uploaded with scp), it would need the same SecOps change, and clone data (tenant names, domains) must not sit in a third-party repo. |
| Azure blob, SharePoint, OneDrive, S3 | Rejected for now: egress is closed the same way; needs a stored credential on the VM (worse than a forced key) and puts customer tenant data in another system. Revisit only as part of the SecOps request. |
| Internal artifact share or file server reachable from both | Unknown; worth asking SecOps/IT, but a VM pull needs a mount or credential on the VM and the PC still has to be on to produce the file, so it adds a hop without removing the PC. |
| Salesforce (the VM can read it) | Rejected: storing the clone there is a Salesforce write (not authorized) and a data-policy question. |
| Reverse tunnel so the VM reaches Redash through the PC | Rejected: needs port forwarding on the key (widens it), TLS host mismatch, keeps the Redash key on the VM, and still needs the PC on. |
| PC pushes to the VM over the existing SSH/VPN path | Chosen: no new egress, no new secret on the VM, no new data store. |

## 4. Failure modes

* PC asleep, off, signed out, or VPN down: the task does not run or logs `unreachable` (exit 4); the clone ages; at 6 h the VM
  fails closed and the banner shows the true age. `StartWhenAvailable` catches up on wake; one run restores freshness.
* Redash cache stale (owner's schedule stopped): the collector reads the stale result, the build refuses it past 6 h
  (`inventory_snapshot_stale`), the VM keeps the old one. Visible in the PC log and in step 22C.
* Redash query edited, shrunk result, auth/WAF failure: the collector fails closed (nothing newer is written locally); the push
  then re-sends the last good clone, which the VM reports as `unchanged`.
* VM refusals (`older_than_current`, `result_shrunk`, `bad_signature`, `future_bundle`, `not_approved`, `busy`) leave the previous
  clone; the code is in the PC log. A deliberate large clean-up needs the collector's `--accept-shrink` and, on the VM, a reviewed
  one-off (not automated).
* Two writers (this push plus the later VM collector): both use the same atomic writer and the newer capture wins on the push side;
  the VM collector's own shrink check applies on its side. Disable this task when the VM collector is enabled.

## 5. Security

* No password, key or token in the repository, the task, or the logs. The Redash key stays a DPAPI blob read only by the
  collector. The push private key and the HMAC key are files readable only by the owner's Windows user (the PC key file protection
  is the same as the tunnel key; the HMAC key file is the existing snapshot key). Risk: a compromised PC session can push a forged
  clone; the VM limits the damage (6 h gate, no shrink, no rollback, signature, name and environment checks, clone is "informational:
  it can block a duplicate, it never clears one").
* The key cannot open a shell, run another command, forward a port, or log in from another address. The only reachable code on the
  VM is the argument-free ingest program as `surface-onboarding`.
* No Salesforce, Workato, OPA, Donatello or BackOffice call exists in any new file. `check_integration_offline_boundary.py` still
  passes (new Python is under `tools/`).

## 6. Idempotency and rollback

Same bundle twice is `unchanged`; every step script is re-runnable and refuses to duplicate a key line. Rollback, in order:
`register_clone_push_task.ps1 -Remove`; `_step22b ... revert` (removes the key line and the marker: the push is dead at once);
`_step22a revert` (removes gate, ingest program, sudoers rule); to make the dashboard use the clone inside the signed snapshot again,
`_step22c fallback` prints the one `mv` that sets `clone-inventory/prod-clone` aside.

## 7. Operator steps (each VM step is a separate explicit owner go)

0. Release: stage and activate the next release (r34, containing `tools/ingest_clone_push.py`) with the usual
   `_stepNNa/b` pair. The VM stage gate runs the suite on Linux (the new tests are portable).
1. PC: `powershell -File tools\setup_clone_push_key.ps1` (creates the key; prints the public key path; nothing leaves the PC).
2. Upload the public key and the three scripts to the VM `/tmp` (operator `scp`, as for earlier steps).
3. VM: `sudo sh /tmp/step22b.sh hint` to see the PC's VPN source address (read-only).
4. VM (go): `sudo sh /tmp/step22a.sh` (gate, ingest program, sudoers rule).
5. VM (go): `sudo sh /tmp/step22b.sh /tmp/clone_push.pub <VPN-ADDRESS>` (key line plus approval marker).
6. PC: `tools\register_clone_push_task.ps1 -WhatIf`, then without `-WhatIf` (go): proves the key with an empty body, registers the
   hourly task `SurfaceOnboarding-CloneToVm`. The existing `SurfaceOnboarding-RedashInventory` task becomes redundant.
7. VM: `sudo sh /tmp/step22c.sh` after the first run: expect `clone age` under about 2 h and `trusted`; check the dashboard banner.

## 8. Remaining blocked on SecOps

Outbound 443 from the VM to `redash.pentera.io`, so `_step18b` can enable the VM collector and this interim path can be retired
(`register_clone_push_task.ps1 -Remove`, `_step22b revert`, `_step22a revert`). Also open: the HMAC key currently lives as a file on
the PC (DPAPI wrapping for it is a possible hardening); the PC's VPN source address must be stable enough for `from=`.

## 9. Files

`tools/build_clone_push_bundle.py`, `tools/ingest_clone_push.py`, `tools/run_clone_push.ps1`, `tools/register_clone_push_task.ps1`,
`tools/setup_clone_push_key.ps1`, `integration/deployment/vm_pilot/_step22a/_step22b/_step22c_*.sh`,
`integration/tests/test_clone_push.py` (20 tests).
