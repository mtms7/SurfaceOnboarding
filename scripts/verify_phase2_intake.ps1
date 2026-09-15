$ErrorActionPreference = 'Stop'

Write-Host 'Read-only verification of the OPA Phase 2 intake service.'
Write-Host 'Enter the VM password only at the SSH prompt. It is not saved.'

$remoteScript = @'
set -eu
hostname
date -u
cd /home/workato

python3 - <<'PY'
import glob
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

from phase2_leonardo.intake import prepare_case3
from phase2_leonardo.test_intake import valid_payload


def fail(message):
    raise SystemExit(message)


def read_cmdline(pid):
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    return [part.decode("utf-8", "strict") for part in raw.split(b"\0") if part]


def is_phase2_process(argv):
    return bool(
        argv
        and any(
            argv[index : index + 2] == ["-m", "phase2_leonardo.server_intake"]
            for index in range(len(argv) - 1)
        )
    )


pid_text = Path("/home/workato/phase2-intake.pid").read_text(encoding="ascii").strip()
if not re.fullmatch(r"[0-9]+", pid_text):
    fail("process_identity_check=failed")
managed_pid = int(pid_text)

matched_pids = []
for cmdline_path in glob.glob("/proc/[0-9]*/cmdline"):
    pid = int(cmdline_path.split("/")[2])
    if is_phase2_process(read_cmdline(pid)):
        matched_pids.append(pid)

if matched_pids != [managed_pid]:
    fail("process_identity_check=failed")

status_lines = Path(f"/proc/{managed_pid}/status").read_text(encoding="utf-8").splitlines()
uid_line = next((line for line in status_lines if line.startswith("Uid:")), None)
if uid_line is None or int(uid_line.split()[2]) != os.geteuid():
    fail("process_owner_check=failed")

print("process_count=1")
print("process_identity_match=true")
print("process_owner_match=true")

tcp = subprocess.run(
    ["ss", "-H", "-ltn", "sport = :8789"],
    check=True,
    capture_output=True,
    text=True,
).stdout.splitlines()
if len(tcp) != 1 or len(tcp[0].split()) < 4 or tcp[0].split()[3] != "127.0.0.1:8789":
    fail("listener_scope_check=failed")

tcp_process = subprocess.run(
    ["ss", "-H", "-ltnp", "sport = :8789"],
    check=True,
    capture_output=True,
    text=True,
).stdout.splitlines()
listener_pids = {
    int(value)
    for line in tcp_process
    for value in re.findall(r"pid=([0-9]+)", line)
}
if len(tcp_process) != 1 or listener_pids != {managed_pid}:
    fail("listener_owner_check=failed")

udp = subprocess.run(
    ["ss", "-H", "-lun", "sport = :8789"],
    check=True,
    capture_output=True,
    text=True,
).stdout.splitlines()
if udp:
    fail("udp_listener_check=failed")

print("listener_count=1")
print("listener_address=loopback_ipv4_only")
print("listener_owner_match=true")
print("udp_listener_count=0")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("synthetic_probe=failed")
        result[key] = value
    return result


request = urllib.request.Request(
    "http://127.0.0.1:8789/v1/phase2/prepare-case3",
    data=json.dumps(valid_payload(), separators=(",", ":")).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    if response.status != 200:
        fail("synthetic_probe=failed")
    if not response.headers.get("Content-Type", "").lower().startswith("application/json"):
        fail("synthetic_probe=failed")
    if response.headers.get("Cache-Control", "").lower() != "no-store":
        fail("synthetic_probe=failed")
    if response.headers.get("X-Content-Type-Options", "").lower() != "nosniff":
        fail("synthetic_probe=failed")
    body = response.read(1_048_577)

if len(body) > 1_048_576:
    fail("synthetic_probe=failed")
actual = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
schema = json.loads(
    Path("phase2_leonardo/contracts/case3-preflight-response.schema.json").read_text(
        encoding="utf-8"
    ),
    object_pairs_hook=unique_object,
)

required = schema.get("required")
properties = schema.get("properties")
if schema.get("additionalProperties") is not False or not isinstance(required, list):
    fail("synthetic_probe=failed")
if not isinstance(properties, dict) or set(actual) != set(required) or set(actual) != set(properties):
    fail("synthetic_probe=failed")
for key, definition in properties.items():
    if "const" in definition and actual.get(key) != definition["const"]:
        fail("synthetic_probe=failed")

expected_groups = [
    "account_enums_and_collision_rules",
    "settings",
    "attack_modules",
    "license_semantics",
    "addon_taxonomy",
    "duplicate_and_readback_rules",
]
if actual.get("unapproved_field_groups") != expected_groups:
    fail("synthetic_probe=failed")
for key in ("evidence_hash", "public_suffix_list_sha256"):
    if not re.fullmatch(r"[0-9a-f]{64}", actual.get(key, "")):
        fail("synthetic_probe=failed")

expected = prepare_case3(valid_payload())
if actual != expected:
    fail("synthetic_probe=failed")

print("synthetic_probe=pass")
print("response_contract=surface-case3-preflight-v2")
print("mapping_policy=v5")
print("decision=owner_review")
print("proceed=false")
print("leonardo_request_allowed=false")
print("response_body_retained=false")
PY

systemctl is-active workato-agent.service
sha256sum /home/workato/phase2_leonardo/intake.py /home/workato/phase2_leonardo/policy.py /home/workato/phase2_leonardo/server_intake.py /home/workato/phase2_leonardo/data/public_suffix_list.dat
'@

# OpenSSH joins command-line arguments and lets the remote shell parse them.
# Passing the script as an argument therefore strips Python quotes and
# backslashes. A Base64 command argument preserves the exact UTF-8 bytes while
# keeping SSH standard input separate from the interactive password prompt.
# The encoded program contains no credential or customer data. Pipefail makes
# any decoder error fail the verification even if Bash received a partial
# program.
$remoteBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript))
$remoteCommand = "bash -o pipefail -c 'printf %s $remoteBase64 | base64 -d | bash'"
ssh -o ConnectTimeout=15 -o NumberOfPasswordPrompts=1 workato@172.26.37.20 $remoteCommand

if ($LASTEXITCODE -ne 0) {
    throw "Read-only verification failed with exit code $LASTEXITCODE"
}
