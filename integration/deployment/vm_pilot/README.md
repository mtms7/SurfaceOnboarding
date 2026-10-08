# VM team-access pilot: review artifacts

**REVIEW ARTIFACT - not installed; needs approvals per docs/40 section 7.**

Nothing in this folder is run, installed, or enabled by the repository. Each file is a draft for VM/SecOps and
Identity review. Placeholders (`<ANGLE_BRACKETS>`, placeholder paths) must be replaced by the approvers; no
certificate, private key, client credential, or cookie key belongs in the repository.

| File | Purpose | Target path after approval |
| --- | --- | --- |
| `nginx-surface-onboarding.conf.template` | TLS listener `172.26.37.20:8443`, headers, strips identity headers, proxies to oauth2-proxy | `/etc/nginx/sites-available/surface-onboarding.conf` |
| `oauth2-proxy.cfg.template` | OneLogin OIDC, e-mail allow-list, Secure cookie, passes `X-Forwarded-Email`, upstream `http://127.0.0.1:8000` | `/etc/oauth2-proxy/oauth2-proxy.cfg` |
| `allowed-emails.txt.template` | Allow-list (owner only for now) for oauth2-proxy and the dashboard | `/etc/oauth2-proxy/allowed-emails.txt`, `/etc/surface-onboarding/allowed-emails.txt` |
| `operators.txt.template` | Operators subset (dashboard) | `/etc/surface-onboarding/operators.txt` |
| `surface-onboarding-dashboard.service.template` | Dashboard unit (user `surface-onboarding`, limits, `scripts/run_vm_dashboard.sh`) | `/etc/systemd/system/surface-onboarding-dashboard.service` |
| `surface-onboarding-oauth2-proxy.service.template` | oauth2-proxy unit (own user, limits) | `/etc/systemd/system/surface-onboarding-oauth2-proxy.service` |

## Request path and trust

```
browser --HTTPS 8443--> nginx (172.26.37.20) --> oauth2-proxy 127.0.0.1:4180 --OIDC--> OneLogin
                                                   |  verified e-mail in X-Forwarded-Email
                                                   +--HTTP--> dashboard 127.0.0.1:8000
```

The dashboard (VM mode) accepts `X-Forwarded-Email` only when the TCP peer is loopback, exactly one well-formed value is
present, and the address is on its allow-list. Unknown user: 403. Viewer: GET only. Operator: GET plus the local
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
| `SURFACE_ONBOARDING_ALLOWED_USERS` / `_FILE` | Allow-list (comma separated and/or one per line); empty means nobody |
| `SURFACE_ONBOARDING_OPERATORS` / `_FILE` | Subset of the allow-list that may POST |
| `SURFACE_ONBOARDING_PUBLIC_ORIGIN` | The single public origin, e.g. `https://172.26.37.20:8443` (Host, Origin, Referer) |
| `SURFACE_ONBOARDING_STATE_DIR` | State folder (VM default `/var/lib/surface-onboarding`) |

## Known limits to settle at approval

- Any local user on the VM can reach `127.0.0.1:8000` and send a forged header. Mitigation to consider: bind the
  dashboard to a Unix socket owned by the nginx/oauth2-proxy group, or add a shared-value check between oauth2-proxy and
  the dashboard (value kept outside the repository).
- The Redash collector key (DPAPI) stays on the desktop; the VM only reads published snapshots.
