#!/bin/sh
# REVIEW ARTIFACT - Step 16C (OPTIONAL, operator-run on the VM as root; keep a second SSH session open while enabling):
#   sudo sh /tmp/_step16c_vm_firewall_vpn_only.sh [allowed-cidr ...]     default: 10.213.213.1/32
#   sudo sh /tmp/_step16c_vm_firewall_vpn_only.sh revert
# ufw is INACTIVE today. This enables it with: default deny incoming, allow outgoing, allow 22/tcp from anywhere
# (unchanged SSH behaviour, so no lockout), allow 8443/tcp only from the given range(s). Because nginx already denies
# other ranges (step 16B), this is a second layer, not the only control.
# SAFETY: it REFUSES to enable ufw if any listener other than 22 and 8443 is reachable from the network (unknown inbound
# services such as monitoring would be cut off). Loopback-only listeners are not affected by ufw.
# `revert` disables ufw and removes the 8443 rule.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
command -v ufw >/dev/null || { echo "ufw is not installed: stop here (installing a package needs separate approval)"; exit 1; }

if [ "${1:-}" = revert ]; then
  ufw --force disable
  for n in $(ufw status numbered | grep '8443' | sed -n 's/^\[ *\([0-9]*\)\].*/\1/p' | sort -rn); do ufw --force delete "$n" >/dev/null; done
  ufw status verbose; exit 0
fi

echo "== 1. listeners reachable from the network (must be only 22 and 8443) =="
OTHER=$(ss -ltnH | awk '{print $4}' | grep -v -E '^(127\.|\[::1\]|::1)' | sed 's/.*://' | sort -un | grep -v -E '^(22|8443)$' || true)
ss -ltnH | awk '{print $4}' | grep -v -E '^(127\.|\[::1\])' || true
if [ -n "$OTHER" ]; then echo "other network listeners on port(s): $(echo $OTHER | tr '\n' ' ')"; echo "stop here: tell Claude, a firewall could cut them off"; exit 1; fi

[ "$#" -ge 1 ] || set -- 10.213.213.1/32
for c in "$@"; do
  echo "$c" | grep -E -q '^([0-9]{1,3}\.){3}[0-9]{1,3}/(8|9|1[0-9]|2[0-9]|3[0-2])$' || { echo "not an IPv4 CIDR: $c"; exit 1; }
done

echo "== 2. rules (SSH first), then enable =="
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
for c in "$@"; do ufw allow from "$c" to any port 8443 proto tcp; done
ufw --force enable
ufw status verbose
echo "== 3. checks =="
curl -sk -m 10 -o /dev/null -w "tunnel path -> %{http_code} (expect 200)\n" https://localhost:8443/
echo "open a NEW ssh session now to prove SSH still works; undo: sudo sh /tmp/_step16c_vm_firewall_vpn_only.sh revert"
