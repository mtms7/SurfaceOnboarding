# Ubuntu 22 OPA VM Requirements And Setup

## Recommended VM

Minimum from Workato:

```text
OS: Linux 64-bit
RAM: 8 GB minimum
Disk: 768 MB minimum for one OPA
CPU: 800 MHz 64-bit Intel/AMD minimum
Network: outbound TCP 443 to Workato OPA gateways
```

Recommended for this project:

```text
OS: Ubuntu Server 22.04 LTS
CPU: 2 vCPU
RAM: 8 GB minimum, 16 GB preferred
Disk: 40 GB minimum
Network: RND VPN path
Internal IP: static/reserved preferred
Outbound NAT IP: stable and documented
Service account: Workato installer creates workato system user
Monitoring: OS patching, service status, disk, CPU, memory, and outbound connectivity
```

Reasoning:

- Workato's minimum disk requirement is tiny, but operational logging, package updates, and support evidence need breathing room.
- 2 vCPU and 8-16 GB RAM is conservative and avoids resource noise during testing.
- The VM becomes part of the trusted administrative automation path and should be treated as production-like infrastructure.

## Validated RND Path

Known good test from local RND lab:

```text
Host: mainnode / 172.26.37.12
Outbound NAT IP: 199.203.203.177
sg3.workato.com:443: reachable
sg4.workato.com:443: reachable
donatello.dev.app.pentera.io: HTTP 200 root
donatello.dev.app.pentera.io/api/v1/userProfile/: HTTP 401 unauthenticated
donatello.dev.app.pentera.io/api/v1/authenticated/getAuthorities: HTTP 401 unauthenticated
```

Interpretation:

- `401` is good for unauthenticated API checks.
- It proves the request reached the Donatello application/API layer.
- It is better than CloudFront/WAF `403`.

## Network Requirements

Allow outbound TCP 443 from the OPA VM to the US Workato OPA gateways:

```text
sg3.workato.com
sg4.workato.com
```

Current US gateway IPs documented by Workato:

```text
sg3.workato.com:
54.224.75.148
52.206.161.203
52.204.114.159

sg4.workato.com:
54.91.65.247
54.221.112.165
3.216.209.184
```

Allow OPA VM to reach target application endpoints:

```text
Donatello dev: https://donatello.dev.app.pentera.io
Presale:       https://presale.app.pentera.io
BO final:      https://app.pentera.io
```

Use Donatello dev for the current two-week testing window. BO production writes remain blocked until approved.

## TLS Inspection Rule

Do not TLS-inspect Workato OPA gateway traffic:

```text
sg3.workato.com
sg4.workato.com
```

Workato OPA uses mutual TLS and Workato private PKI. TLS inspection breaks the trust chain. Configure a targeted `Do Not Inspect` rule for Workato gateway FQDNs only.

## Preflight Commands

Use the bundled script:

```bash
chmod +x scripts/ubuntu_opa_preflight.sh
./scripts/ubuntu_opa_preflight.sh
```

Manual command set:

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates gnupg wget dnsutils netcat-openbsd openssl jq
```

```bash
targets=(
  sg3.workato.com
  sg4.workato.com
  donatello.dev.app.pentera.io
  presale.app.pentera.io
  app.pentera.io
)

for target in "${targets[@]}"; do
  echo
  echo "==== $target ===="
  dig +short "$target"
  nc -vz -w 5 "$target" 443
done
```

```bash
curl -sS -I --connect-timeout 10 https://donatello.dev.app.pentera.io/
curl -sS -D - -o /dev/null --connect-timeout 10 https://donatello.dev.app.pentera.io/api/v1/userProfile/
curl -sS -D - -o /dev/null --connect-timeout 10 https://donatello.dev.app.pentera.io/api/v1/authenticated/getAuthorities
curl -s https://checkip.amazonaws.com
```

TLS gateway check:

```bash
for host in sg3.workato.com sg4.workato.com; do
  echo
  echo "==== TLS check: $host ===="
  timeout 10 openssl s_client -connect "$host:443" -servername "$host" -brief </dev/null
done
```

The raw OpenSSL check may show `unable to get local issuer certificate` because Workato uses private PKI. The critical signal is `CONNECTION ESTABLISHED`.

## Workato OPA Install On Ubuntu 22

In Workato:

1. Go to `Tools > On-prem groups`.
2. Create/select group:

```text
surface-onboarding-rnd-opa
```

3. Click `Add new agent`.
4. Select Linux and DEB package.
5. Copy the current installation and activation commands from the Workato wizard.

On the VM, Workato's DEB path currently uses:

```bash
echo 'deb [ signed-by=/usr/share/keyrings/workato.gpg ] https://workato-public.s3.amazonaws.com/DAA34553/repo/deb/ stable main' | sudo tee /etc/apt/sources.list.d/workato.list
wget -qO - 'https://workato-public.s3.amazonaws.com/DAA34553/repo/archive.key' | sudo gpg -o /usr/share/keyrings/workato.gpg --dearmor
sudo apt-get update
sudo apt install workato-agent
```

Then run the activation command from the Workato wizard. The activation code is time limited, so generate it when ready.

Start and enable the service:

```bash
sudo systemctl start workato-agent.service
sudo systemctl enable workato-agent.service
sudo systemctl status workato-agent.service
```

In Workato, click `Test agent`.

## Host Hardening Checklist

- Patch Ubuntu before installing OPA.
- Restrict SSH access to approved admins.
- Use key-based SSH where possible.
- Disable password SSH if allowed by IT policy.
- Keep local firewall enabled unless IT policy says otherwise.
- Restrict access to OPA config and cert/key folders.
- Monitor `workato-agent.service`.
- Record certificate expiration and renew before one year.
- Do not store Donatello/BO passwords on the VM when cloud profiles are available.

