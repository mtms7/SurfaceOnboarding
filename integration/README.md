# Direct onboarding integration scaffold

This package is the local-only foundation for the direct Salesforce-to-Surface
onboarding service described in `IMPLEMENTATION_PLAN.md`.

It deliberately contains no Salesforce CLI invocation, HTTP client, browser
automation, credential retrieval, MFA handling, or Leonardo operation.  All
route mappings are visible but disabled until their owners approve a versioned
mapping.  The supplied adapters are in-memory test doubles only.

## Local checks

Use Python 3.12 or later and install the development dependencies declared in
`pyproject.toml`, then run:

```powershell
python -m unittest discover -s integration/tests -t . -v
python tools/check_integration_artifacts.py
python tools/check_integration_offline_boundary.py
python tools/check_release_archive.py RELEASE_ARCHIVE CHECKSUM_SIDECAR
```

The second check rejects commonly committed sensitive artifact types and
obvious credential-bearing filenames under `integration/`. It is a guardrail,
not a substitute for approved secret management or a full secret scanner.
The third check rejects external transport and process-execution capabilities
from the local scaffold implementation.
The fourth check verifies a release archive before privileged extraction: it
requires an exact checksum sidecar, rejects unsafe tar members and compression
size abuse, and requires the deployment safety artifacts to be present.

`migrations/001_initial_metadata.sql` is a reviewed schema draft only. Do not
run it until the PostgreSQL deployment, service identity, and approval gates
have been completed.

The local CLI is deliberately limited to metadata-only behavior:

```powershell
python -m integration.onboarding.cli queue
python -m integration.onboarding.cli audit
python -m integration.onboarding.cli readiness
python -m integration.onboarding.cli sync --actor local.operator --role operator --all
```

It does not query Salesforce or start a worker; its in-memory state lasts only
for the one process invocation.

The local service also retains process-local, metadata-only audit events for
sync requests and coalesced requests. It has no file, database, or external
audit sink.

`readiness` is an offline gap report. It defaults every owner approval to not
approved and does not probe Salesforce, Leonardo, a VM, or a secret backend.

The synthetic intake module accepts only preloaded identifier/revision metadata
and applies the local mapping and manual-review gates. It cannot query a source
system or start an execution path.

Synthetic intake creates local audit metadata only when it creates or revises a
workflow. Repeating the same source revision does not create another audit event.

The manual-review contract binds a reviewer decision to an exact record,
revision, and intent hash. It cannot release an item until the route mapping is
separately enabled, and it has no persistence or execution capability.
Its local repository allows one immutable decision per exact snapshot and is an
in-memory preparation for the unexecuted database schema.
The local service may audit a reviewer decision, but it cannot release or
execute the corresponding workflow.

The FastAPI factory is blocked by the same web-identity configuration gate; it
cannot initialize a listener in the current scaffold.

Any future Leonardo adapter must pass the exact-origin policy before use. The
policy allows only the documented Development HTTPS origin and performs no I/O.

The execution gate is an evaluation-only contract: it requires a ready item,
approved mapping, exact Development origin, and explicit authentication approval
before it can return `allowed`. It does not have an execution method.

Workflow transitions reject blocker reasons on non-terminal states, preventing a
stale rejection or manual-review reason from being carried into a later state.

Salesforce writeback is represented only by a correlation-only disabled stub.
It rejects every request until the owner-approved fields and separate write
authorization exist.

The poll planner calculates future configured local times only. It does not
install a scheduler, invoke a timer, or request a Salesforce synchronization.

`deployment/` contains inert preparation artifacts for a future colocated
Ubuntu 22.04 deployment on `workato-opa-01`. They bind the future web service
only to loopback and require approval markers; they do not install, enable, or
contact that VM. The direct service must not reuse the Workato OPA identity,
files, certificate, or browser state.

`deployment/SECURITY_BASELINE.md` defines the additional fail-closed posture:
corporate TLS and SSO/MFA at a separately approved proxy, no direct ingress,
least-privilege host isolation, approved secret handling, and blocked egress
until each external identity and scope is authorized.

`deployment/REVERSE_PROXY_APPROVAL_PACKET.md` supplies the owner-decision and
security-acceptance evidence required before an approved proxy configuration
can be designed. It is deliberately not a deployable configuration.

`onboarding/web_activation.py` validates only opaque, time-bounded approval
references for a future listener. It cannot enable the listener, fetch a
secret, or authenticate a user; the app factory remains blocked independently.

`deployment/THREAT_MODEL.md` is the release-gated risk register for the
internal RND-VPN deployment; it makes no external connection or activation.

`onboarding/ui_preview.py` is a static, redacted design preview of the future
operator queue. It is not a listener or web application, contains no live
source data, and offers no state-changing action.

`onboarding/dashboard_view.py` adds the strict, escaped metadata-only queue
projection used by that preview. It accepts only safe reference and reason-code
formats and rejects customer fields, payloads, and free-form error text.

`tools/serve_loopback_preview.py` is the VM-only temporary visual-review tool.
It serves the static preview on `127.0.0.1` for an authenticated SSH tunnel
only; it must not be installed, proxied, or exposed on the RND VPN.

`tools/serve_attended_co0717_viewer.py` is a separate, desktop-only pilot
viewer. An operator starts it manually after their own Salesforce CLI browser
login with MFA. It binds only to desktop loopback, reads exactly `CO-0717` on
each page load, never writes data, disables caching and request logs, and must
never run on the VM, receive a copied token, be proxied, or be generalized to
other records. It is an attended test aid, not an automation or production
data path.

`requirements.runtime.lock` records the reviewed, exact Python 3.12/Linux
runtime resolution first installed into the isolated VM virtual environment.
It is not a service-start authorization and must be regenerated through a
reviewed dependency update rather than an unbounded install.

The source-read contract represents only fixed scopes, validated reference
metadata, and masked outcomes. Authentication, timeout, transport, invalid
JSON, schema-drift, and ambiguity outcomes fail closed without raw CLI output.
Targeted reference results must contain at most one matching record.
Synthetic batch intake additionally requires candidates to match a complete,
successful source snapshot exactly before it creates any local workflows.

The source-schema parser reduces a synthetic JSON document to validated
identifier/revision references in memory only. It retains neither the document
nor raw parse errors.

The CLI emits a fixed rejection code for failed sync requests rather than
echoing exception details.

The on-demand rate limiter is an in-memory, caller-configured sliding-window
contract. When supplied to the local service, it rejects before job or audit
creation; it does not choose deployment thresholds or invoke synchronization.

## Boundaries

- No mapping or adapter in this directory authorizes an external action.
- The only Leonardo target accepted by a future adapter is Development; the
  current adapter interface has no mutation method.
- Source records represented here carry identifiers and revisions only. Raw
  Salesforce and Leonardo data must stay in their authoritative systems.
