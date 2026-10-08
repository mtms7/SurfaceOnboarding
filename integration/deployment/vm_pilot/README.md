# VM team-access pilot: review artifacts

**REVIEW ARTIFACT - not installed; needs approvals per docs/40 section 7.**

Nothing in this folder is run, installed, or enabled by the repository. Each file is a draft for VM/SecOps and
Identity review. Placeholders (`<ANGLE_BRACKETS>`, placeholder paths) must be replaced by the approvers; no
certificate, private key, client credential, or cookie key belongs in the repository.

| File | Purpose | Target path after approval |
| --- | --- | --- |
| `nginx-surface-onboarding.conf.template` | TLS listener `172.26.37.20:8443`, headers, strips identity headers, proxies to oauth2-proxy | `/etc/nginx/sites-available/surface-onboarding.conf` |
| `nginx-surface-onboarding-tunnel.conf.template` | **Interim only**: nginx on `127.0.0.1:8443` for an SSH tunnel before the OneLogin app exists; sets one fixed viewer identity (see "Interim review over an SSH tunnel") | `/etc/nginx/sites-available/surface-onboarding-tunnel.conf` |
| `oauth2-proxy.cfg.template` | OneLogin OIDC, e-mail allow-list, Secure cookie, passes `X-Forwarded-Email`, upstream `http://127.0.0.1:8000` | `/etc/oauth2-proxy/oauth2-proxy.cfg` |
| `users.txt.template` | **Recommended single source of truth**: `email` (viewer) or `email operator` lines | `/etc/surface-onboarding/users.txt` |
| `render_allowed_emails.py` | Renders oauth2-proxy's plain e-mail list from `users.txt` (idempotent, refuses malformed lines) | run on the VM, output `/etc/oauth2-proxy/allowed-emails.txt` |
| `allowed-emails.txt.template` | Legacy allow-list form (oauth2-proxy list; also the legacy dashboard `_FILE`) | `/etc/oauth2-proxy/allowed-emails.txt` (normally rendered, see below) |
| `operators.txt.template` | Legacy operators subset (dashboard `_FILE`) | `/etc/surface-onboarding/operators.txt` |
| `surface-onboarding-dashboard.service.template` | Dashboard unit (user `surface-onboarding`, limits, `scripts/run_vm_dashboard.sh`) | `/etc/systemd/system/surface-onboarding-dashboard.service` |
| `surface-onboarding-oauth2-proxy.service.template` | oauth2-proxy unit (own user, limits) | `/etc/systemd/system/surface-onboarding-oauth2-proxy.service` |

## Request path and trust

```
browser --HTTPS 8443--> nginx (172.26.37.20) --> oauth2-proxy 127.0.0.1:4180 --OIDC--> OneLogin
                                                   |  verified e-mail in X-Forwarded-Email
                                                   +--HTTP--> dashboard 127.0.0.1:8000
```

The dashboard (VM mode) accepts `X-Forwarded-Email` only when the TCP peer is loopback, the `X-Surface-Proxy-Secret`
header carries exactly the shared secret (constant-time comparison; secret missing, shorter than 32 characters,
mismatched or sent twice means no identity is trusted), exactly one well-formed e-mail value is present, and the address
is on its allow-list. Unknown user: 403. Viewer: GET only. Operator: GET plus the local
acknowledgements and read-only checks listed in `VM_OPERATOR_POSTS`; every runner / Leonardo / Salesforce-write POST
is refused for all roles in phase 1. State lives under `/var/lib/surface-onboarding`.

## Self-signed certificate (command text only, not run; pilot fallback per docs/40 section 1)

Run on the VM as root, only after the placement and certificate steps are approved:

```
install -d -m 0750 -o root -g nginx /etc/nginx/surface-onboarding/tls
openssl req -x509 -newkey rsa:3072 -sha256 -days 90 -nodes \
  -subj "/CN=172.26.37.20/O=Surface onboarding pilot" \
  -addext "subjectAltName=IP:172.26.37.20" \
  -keyout /etc/nginx/surface-onboarding/tls/surface-onboarding.key \
  -out    /etc/nginx/surface-onboarding/tls/surface-onboarding.crt
chmod 0640 /etc/nginx/surface-onboarding/tls/surface-onboarding.key
chmod 0644 /etc/nginx/surface-onboarding/tls/surface-onboarding.crt
openssl x509 -in /etc/nginx/surface-onboarding/tls/surface-onboarding.crt -noout -subject -ext subjectAltName -dates -fingerprint -sha256
```

Teammates should verify the SHA-256 fingerprint out of band (or import the certificate) rather than learning to click
through warnings. Replace with an internal-CA certificate for a DNS name later, then enable HSTS in the nginx file.

## Firewall rule (text only, not applied)

Allow TCP 8443 only from the subnet SecOps names; everything else stays default-deny. `127.0.0.1:8000` and
`127.0.0.1:4180` are never exposed.

```
# ufw
ufw allow from <APPROVED_SUBNET_CIDR> to 172.26.37.20 port 8443 proto tcp comment 'surface-onboarding dashboard pilot'

# nftables equivalent (inside the existing inet filter input chain)
ip saddr <APPROVED_SUBNET_CIDR> ip daddr 172.26.37.20 tcp dport 8443 ct state new accept
```

## Environment variables read by the dashboard

| Variable | Meaning |
| --- | --- |
| `SURFACE_ONBOARDING_RUNTIME=vm` | Turns on every VM behaviour below; unset/`desktop` keeps today's behaviour |
| `SURFACE_ONBOARDING_PROXY_SECRET_FILE` (preferred) / `SURFACE_ONBOARDING_PROXY_SECRET` | Shared secret nginx sends in `X-Surface-Proxy-Secret`; at least 32 printable characters without spaces. A named file that cannot be read fails closed (no fallback to the variable) |
| `SURFACE_ONBOARDING_USERS_FILE` | Recommended: the single users file (`email` or `email operator` per line, `#` comments). Any malformed line admits nobody |
| `SURFACE_ONBOARDING_ALLOWED_USERS` / `_FILE` | Legacy allow-list (comma separated and/or one per line); when set it wins over the users file for the allow-list; empty means nobody |
| `SURFACE_ONBOARDING_OPERATORS` / `_FILE` | Legacy subset of the allow-list that may POST; when set it wins over the users file for operators |
| `SURFACE_ONBOARDING_PUBLIC_ORIGIN` | The single public origin, e.g. `https://172.26.37.20:8443` (Host, Origin, Referer) |
| `SURFACE_ONBOARDING_STATE_DIR` | State folder (VM default `/var/lib/surface-onboarding`); in VM mode the dashboard reads `<state dir>/snapshots/current/` (see "Published snapshots") |
| `SURFACE_ONBOARDING_SNAPSHOT_STALE_HOURS` | Age after which the "Data as of" banner turns amber (default `6`; a missing, non-numeric or non-positive value means the default) |

## Shared proxy secret (command text only, not run; built locally 2026-10-08)

Run on the VM as root, only after the placement steps are approved. Never paste the value into a ticket, chat, shell
history, or the repository; the commands below write it straight into root-owned files.

```
# 1. Dashboard copy (read by the dashboard as SURFACE_ONBOARDING_PROXY_SECRET_FILE).
install -d -m 0750 -o root -g surface-onboarding /etc/surface-onboarding
umask 0137
openssl rand -hex 32 > /etc/surface-onboarding/proxy-secret          # 64 hex characters
chown root:surface-onboarding /etc/surface-onboarding/proxy-secret
chmod 0640 /etc/surface-onboarding/proxy-secret

# 2. nginx copy: ONE line `set $surface_proxy_secret "<value>";`, built from the file above without printing it.
printf 'set $surface_proxy_secret "%s";\n' "$(cat /etc/surface-onboarding/proxy-secret)" \
  > /etc/nginx/surface-onboarding-proxy-secret.conf
chown root:<NGINX_GROUP> /etc/nginx/surface-onboarding-proxy-secret.conf
chmod 0640 /etc/nginx/surface-onboarding-proxy-secret.conf
nginx -t && systemctl reload nginx
```

Rotation (same commands, new value): write both files, then `systemctl restart surface-onboarding-dashboard` and
`nginx -t && systemctl reload nginx`, one after the other; requests fail closed (403) in the short gap between the two
and recover once both hold the new value. Rotate whenever someone with shell access to the VM leaves or the value may
have been exposed. The dashboard never logs, echoes, or stores the value. A shell user who can read
`/etc/surface-onboarding/proxy-secret` (root, the service user, root-equivalents) can still forge identities; the check
removes the forging path for every other local account.

## One allow-list source (built locally 2026-10-08)

`/etc/surface-onboarding/users.txt` (template: `users.txt.template`) is the single place to add or remove a person.
After each edit, re-render the oauth2-proxy list and restart nothing but the proxy (it re-reads the file on change; if
the installed version does not, restart `surface-onboarding-oauth2-proxy`):

```
python3 /opt/surface-onboarding/app/integration/deployment/vm_pilot/render_allowed_emails.py \
  /etc/surface-onboarding/users.txt /etc/oauth2-proxy/allowed-emails.txt
```

The script lower-cases, de-duplicates and sorts; it rewrites the output atomically only when it changed, refuses a
malformed or empty users file (non-zero exit, old output kept) and prints only a count or a line number. The dashboard
reads `users.txt` directly (`SURFACE_ONBOARDING_USERS_FILE`), so no second copy can drift. Operators are still a subset
of the allow-list, and operators still cannot start runs on the VM. The legacy variables continue to work and win over
the users file when set.

## Interim review over an SSH tunnel (built locally 2026-10-08, review artifact)

`nginx-surface-onboarding-tunnel.conf.template` lets the owner review the VM dashboard before the OneLogin application
exists. nginx listens ONLY on `127.0.0.1:8443` (TLS with the pilot self-signed certificate above), forwards to
`http://127.0.0.1:8000`, replaces `X-Forwarded-Email` with the owner's address (placeholder `<OWNER_EMAIL>`), sends the
same `X-Surface-Proxy-Secret` from the same include file, strips the other client-supplied identity headers and sets
the same security headers. Use it with a tunnel from the owner's laptop:

```
ssh -L 8443:127.0.0.1:8443 <VM_USER>@172.26.37.20        # then open https://localhost:8443/
```

While it is in use set `SURFACE_ONBOARDING_PUBLIC_ORIGIN=https://localhost:8443` in the unit, and do not install the
full nginx site or oauth2-proxy.

Limits, accepted for the interim only:

- There is no login. Every request that reaches `127.0.0.1:8443` is treated as `<OWNER_EMAIL>`, so **any local shell
  user on the VM who connects to that port gets viewer access** (the port is loopback-only, so nobody off the VM can).
- `<OWNER_EMAIL>` must be a plain **viewer** in `users.txt` (no `operator` flag) for this interim, so the worst case is
  read-only access to the published snapshot, which the dashboard already serves to every allow-listed viewer.
- It is not a team rollout: teammates cannot reach it, and it is not a substitute for the identity gate.

Replacement: when the OneLogin application, the certificate and the firewall rule are approved, remove this site
(`rm /etc/nginx/sites-enabled/surface-onboarding-tunnel.conf`), install `nginx-surface-onboarding.conf.template` and
oauth2-proxy as above, restore `SURFACE_ONBOARDING_PUBLIC_ORIGIN=https://172.26.37.20:8443`, and, if the owner should
keep operator rights, add the `operator` flag back to the owner's line in `users.txt`.

## Published snapshots: the VM data source (built locally 2026-10-08, docs/41)

In VM mode the dashboard has no Salesforce, Leonardo, Redash or browser connection and starts no process. It reads the
last snapshot the desktop published. Owner decisions 2026-10-08: transport A (the owner copies a zip by hand), the
desktop may publish automatically after each run (default OFF), customer names and domains may be shown to allow-listed
viewers, the VM has no Salesforce connection in phase 1.

Desktop (owner, Windows):

```
python tools/publish_vm_snapshot.py --key-file <KEY_FILE_OUTSIDE_REPO> --out-dir <LOCAL_FOLDER>
python tools/publish_vm_snapshot.py --dry-run                 # lists what would be included; reads no Salesforce
```

It writes `surface-vm-snapshot-<UTC stamp>.zip` into the folder and prints counts and the path only. The zip holds only
allow-listed files (the `attended_*.json` state files that exist, `queue.json`, `co_details.json` with exactly the
dashboard's fixed display fields, and the latest Dev inventory); it refuses any other name. The production clone
(about 17 MB) is larger than the 5 MB per-file cap and is not published, so the Production tenant pages show it as
unavailable. Optional automatic publish after each runner process ends: `SURFACE_VM_SNAPSHOT_AUTOPUBLISH=1` with
`SURFACE_VM_SNAPSHOT_KEY_FILE` and `SURFACE_VM_SNAPSHOT_OUT_DIR`; best effort, it never changes a run result and only
writes `surface-vm-snapshot-latest.zip` into that folder.

VM (after the owner copies the zip), as the service user:

```
python3 /opt/surface-onboarding/app/tools/ingest_vm_snapshot.py   --key-file /etc/surface-onboarding/snapshot.key --zip /path/to/surface-vm-snapshot-<stamp>.zip
```

It verifies everything first (HMAC-SHA256 over the canonical manifest, SHA-256 and size per file, allow-list, no
folders, links, duplicates, extra or missing files, files over 5 MB, bundle over 20 MB, schema version 1, nothing older
than the current snapshot), unpacks into `<STATE_DIR>/snapshots/incoming/`, atomically switches the `current` symlink,
keeps the newest 5 snapshots and prints only `ingested <name>` or `refused:<code>` (exit 2). On any failure the previous
snapshot stays current. The key is a file of at least 32 characters, never in the repository: on the VM
`/etc/surface-onboarding/snapshot.key` (root-owned, `0640 root:surface-onboarding`, create it like the proxy secret, never
echo it); on the desktop a file outside the repository, protected by the owner.

Layout under `<STATE_DIR>/snapshots/`: `incoming/` (staging, emptied every run), `snapshot-<UTC stamp>/` (one per
publish, with `manifest.json` and the files), `current` (symlink to one of them). Every page shows "Data as of <time>
(published from the desktop)", amber after `SURFACE_ONBOARDING_SNAPSHOT_STALE_HOURS`, or "No data published yet" when
nothing was ingested. Start, Confirm, Prepare sessions and every other POST form are removed from VM pages and the
server still refuses those POSTs. The unit keeps `ReadWritePaths=/var/lib/surface-onboarding`, so ingest needs no other
write access.

## Known limits to settle at approval

- The shared proxy secret closes the forged-header path for other local users (see above). Binding the dashboard to a
  Unix socket owned by the nginx/oauth2-proxy group remains an optional further hardening.
- Confirm on the VM that oauth2-proxy forwards `X-Surface-Proxy-Secret` to the upstream (the dashboard refuses every
  request when it does not arrive).
- The Redash collector key (DPAPI) stays on the desktop; the VM only reads published snapshots.
