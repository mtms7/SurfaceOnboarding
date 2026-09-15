#!/usr/bin/env bash
set -u

echo "Surface Workato OPA Ubuntu preflight"
echo "Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "MISSING: $1"
    return 1
  fi
  echo "OK: $1"
}

echo "== Tool check =="
require_cmd dig || true
require_cmd nc || true
require_cmd curl || true
require_cmd openssl || true
echo

targets=(
  sg3.workato.com
  sg4.workato.com
  donatello.dev.app.pentera.io
  presale.app.pentera.io
  app.pentera.io
)

echo "== DNS and TCP 443 =="
for target in "${targets[@]}"; do
  echo
  echo "---- $target ----"
  if command -v dig >/dev/null 2>&1; then
    dig +short "$target"
  else
    getent hosts "$target" || true
  fi

  if command -v nc >/dev/null 2>&1; then
    nc -vz -w 5 "$target" 443
  else
    timeout 5 bash -c "cat < /dev/null > /dev/tcp/$target/443" \
      && echo "TCP 443 OK" || echo "TCP 443 FAILED"
  fi
done

echo
echo "== HTTPS app checks =="
curl -sS -I --connect-timeout 10 https://donatello.dev.app.pentera.io/ || true
echo
curl -sS -I --connect-timeout 10 https://presale.app.pentera.io/ || true
echo
curl -sS -I --connect-timeout 10 https://app.pentera.io/ || true
echo

echo "== Donatello unauthenticated API checks =="
curl -sS -D - -o /dev/null --connect-timeout 10 https://donatello.dev.app.pentera.io/api/v1/userProfile/ || true
echo
curl -sS -D - -o /dev/null --connect-timeout 10 https://donatello.dev.app.pentera.io/api/v1/authenticated/getAuthorities || true
echo

echo "== Outbound NAT IP =="
curl -sS --connect-timeout 10 https://checkip.amazonaws.com || true
echo

echo "== Workato OPA gateway TLS checks =="
for host in sg3.workato.com sg4.workato.com; do
  echo
  echo "---- $host ----"
  timeout 10 openssl s_client -connect "$host:443" -servername "$host" -brief </dev/null || true
done

echo
echo "Interpretation:"
echo "- Workato gateway TCP 443 must succeed."
echo "- Donatello root should return HTTP 200 from the approved VPN path."
echo "- Donatello unauthenticated API checks should return 401 or another app-layer status, not CloudFront/WAF 403."
echo "- OpenSSL may show unable to get local issuer certificate because Workato OPA uses private PKI; CONNECTION ESTABLISHED is the useful signal."

