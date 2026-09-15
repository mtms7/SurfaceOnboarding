"""Strictly redacted read-only queue view for the future operator dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from html import escape
import re

from .models import EngineValue


class QueueType(StrEnum):
    BLOCKED = "blocked"
    MANUAL_REVIEW = "manual_review"
    ELIGIBLE = "eligible"
    ACTIVE = "active"
    COMPLETE = "complete"


class SalesforceDisplayStage(StrEnum):
    NEW = "New"
    REQUEST_APPROVED = "Request Approved"
    ACCOUNT_SCANNING = "Account Scanning"
    SCAN_COMPLETED = "Scan Completed Successfully"
    USER_CREATED = "User Created"
    ONBOARDING_COMPLETED = "Onboarding Completed"


class QueueSort(StrEnum):
    QUEUE_TYPE = "queue_type"
    ROUTE = "route"
    LAST_UPDATED = "last_updated"


REFERENCE_PATTERN = re.compile(r"^CO-[A-Z0-9-]{3,28}$")
REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_QUEUE_PRIORITY = {
    QueueType.BLOCKED: 0,
    QueueType.MANUAL_REVIEW: 1,
    QueueType.ELIGIBLE: 2,
    QueueType.ACTIVE: 3,
    QueueType.COMPLETE: 4,
}
_ONBOARDING_DETAIL_FIELDS = (
    "Account",
    "Onboarding Product",
    "Onboarding Type",
    "Primary User",
    "Main Domain",
    "Alternative Domains",
    "Email Domains",
    "Onboarding Comments",
    "Onboarding Stage",
    "Surface Account ID",
    "Account UUID",
)


@dataclass(frozen=True, slots=True)
class ReadOnlyQueueItem:
    """Safe projection: no customer fields, revisions, IDs, or payloads."""

    reference: str
    queue_type: QueueType
    route: EngineValue
    source_stage: SalesforceDisplayStage
    reason_code: str
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not REFERENCE_PATTERN.fullmatch(self.reference):
            raise ValueError("invalid_queue_display_reference")
        if not isinstance(self.queue_type, QueueType):
            raise ValueError("invalid_queue_display_type")
        if not isinstance(self.route, EngineValue):
            raise ValueError("invalid_queue_display_route")
        if not isinstance(self.source_stage, SalesforceDisplayStage):
            raise ValueError("invalid_queue_display_stage")
        if not isinstance(self.reason_code, str) or not REASON_PATTERN.fullmatch(self.reason_code):
            raise ValueError("invalid_queue_display_reason")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("queue_display_updated_at_must_be_timezone_aware")


def sort_read_only_queue(items: tuple[ReadOnlyQueueItem, ...], *, sort_by: QueueSort = QueueSort.QUEUE_TYPE
                         ) -> tuple[ReadOnlyQueueItem, ...]:
    """Deterministic safe ordering; default groups the queue by operational type."""
    if not isinstance(sort_by, QueueSort):
        raise ValueError("invalid_queue_sort")
    if sort_by is QueueSort.QUEUE_TYPE:
        key = lambda item: (_QUEUE_PRIORITY[item.queue_type], item.updated_at, item.reference)
    elif sort_by is QueueSort.ROUTE:
        key = lambda item: (item.route.value, item.updated_at, item.reference)
    else:
        key = lambda item: (item.updated_at, item.reference)
    return tuple(sorted(items, key=key, reverse=sort_by is QueueSort.LAST_UPDATED))


def _label(value: str) -> str:
    return value.replace("_", " ").title()


def render_read_only_queue(items: tuple[ReadOnlyQueueItem, ...]) -> str:
    """Render escaped safe metadata only; no link, form, script, or action exists."""
    ordered = sort_read_only_queue(items)
    rows = "".join(
        "<tr><td><a href=\"/co/{reference}\">{reference}</a></td><td><span class=\"badge {queue_type}\">{queue_label}</span></td>"
        "<td>{route}</td><td>{stage}</td><td>{reason}</td><td>{updated}</td>"
        "<td><span class=\"badge pending\">None</span></td></tr>".format(
            reference=escape(item.reference), queue_type=escape(item.queue_type.value),
            queue_label=escape(_label(item.queue_type.value)), route=escape(item.route.value),
            stage=escape(item.source_stage.value), reason=escape(_label(item.reason_code)),
            updated=escape(item.updated_at.strftime("%Y-%m-%d %H:%M UTC")),
        ) for item in ordered
    ) or "<tr><td colspan=\"7\">No safe queue metadata is available.</td></tr>"
    counts = {queue_type: sum(item.queue_type is queue_type for item in items) for queue_type in QueueType}
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><title>Surface onboarding — queue preview</title>
<style>
:root {{ color-scheme: dark; --bg:#09111f; --panel:#111c2e; --line:#263755; --text:#e7eefb; --muted:#9db0ce; --good:#4fd1a4; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,sans-serif; }} main {{ max-width:1360px; margin:0 auto; padding:42px 28px; }} a {{ color:#83c7ff; }} .eyebrow {{ color:var(--good); font-weight:700; letter-spacing:.08em; text-transform:uppercase; font-size:.75rem; }} h1 {{ margin:8px 0; font-size:clamp(1.8rem,4vw,2.7rem); }} .lede,.muted {{ color:var(--muted); }} .notice {{ margin:28px 0; padding:16px 18px; border:1px solid #6a4b1b; border-radius:10px; background:#2b210f; color:#ffe1a8; }} .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }} .card,.table {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; }} .card {{ padding:18px; }} .metric {{ font-size:2rem; font-weight:750; margin:8px 0 0; }} .table {{ margin-top:22px; overflow:auto; }} table {{ width:100%; border-collapse:collapse; min-width:920px; }} th,td {{ padding:14px 16px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap; }} th {{ color:var(--muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; }} tr:last-child td {{ border-bottom:0; }} .badge {{ display:inline-block; padding:3px 9px; border-radius:999px; font-size:.8rem; font-weight:700; }} .blocked {{ color:#ffd1d1; background:#48202a; }} .manual_review {{ color:#ffe1a8; background:#49391d; }} .eligible {{ color:#c5ffe9; background:#174536; }} .active {{ color:#cfe4ff; background:#1d3857; }} .complete,.pending {{ color:#d4def0; background:#263755; }} footer {{ color:var(--muted); font-size:.85rem; margin-top:22px; }} @media(max-width:800px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main>
<div class="eyebrow">Read-only design preview · synthetic metadata</div><h1>Surface onboarding queue</h1>
<p class="lede">Salesforce-style operational view, grouped by queue type. This temporary page contains no live source values and cannot perform any action.</p>
<div class="notice"><strong>Not deployed.</strong> Corporate SSO/MFA, identity propagation, RND-VPN source ranges, and route mappings remain approval gates.</div>
<section class="grid" aria-label="Queue summary"><div class="card"><div class="muted">Blocked</div><div class="metric">{counts[QueueType.BLOCKED]}</div><div class="muted">Source or mapping gate</div></div><div class="card"><div class="muted">Manual review</div><div class="metric">{counts[QueueType.MANUAL_REVIEW]}</div><div class="muted">Awaiting reviewer decision</div></div><div class="card"><div class="muted">Eligible</div><div class="metric">{counts[QueueType.ELIGIBLE]}</div><div class="muted">Execution remains disabled</div></div><div class="card"><div class="muted">Active / complete</div><div class="metric">{counts[QueueType.ACTIVE] + counts[QueueType.COMPLETE]}</div><div class="muted">Preview has no live workflow</div></div></section>
<section class="table" aria-label="Read-only queue sorted by queue type"><table><thead><tr><th>CO number</th><th>Queue type</th><th>Route</th><th>Salesforce stage</th><th>Reason</th><th>Last updated</th><th>Available action</th></tr></thead><tbody>{rows}</tbody></table></section>
<footer>Preview contract: escaped metadata only · no scripts · no external assets · no credentials · no workflow execution</footer></main></body></html>"""


def render_read_only_detail(item: ReadOnlyQueueItem) -> str:
    """Render a safe detail page with a field layout but no customer values or actions."""
    source_fields = "".join(
        f"<dt>{escape(label)}</dt><dd class=\"unavailable\">Not loaded in synthetic preview</dd>"
        for label in _ONBOARDING_DETAIL_FIELDS
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>{escape(item.reference)} — Surface onboarding</title><style>body{{margin:0;background:#09111f;color:#e7eefb;font:16px/1.5 system-ui,sans-serif}}main{{max-width:1040px;margin:0 auto;padding:42px 28px}}a{{color:#83c7ff}}h1{{margin-bottom:4px}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}}.panel{{background:#111c2e;border:1px solid #263755;border-radius:12px;padding:20px;margin-top:24px}}.panel h2{{margin:0 0 12px;font-size:1rem}}dt{{color:#9db0ce;margin-top:16px;font-size:.84rem;text-transform:uppercase;letter-spacing:.05em}}dd{{margin:3px 0 0;font-weight:650}}.unavailable{{color:#9db0ce;font-weight:500;font-style:italic}}.notice{{margin:22px 0;padding:14px 16px;border:1px solid #6a4b1b;border-radius:10px;background:#2b210f;color:#ffe1a8}}@media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main><a href="/">← Queue</a><h1>{escape(item.reference)}</h1><p>Read-only detail layout. Customer values are intentionally not loaded into the synthetic preview.</p><div class="notice">Workflow execution remains disabled pending mapping, identity, and security approval.</div><section class="grid"><div class="panel"><h2>Onboarding record</h2><dl>{source_fields}</dl></div><div class="panel"><h2>Operational state</h2><dl><dt>Queue type</dt><dd>{escape(_label(item.queue_type.value))}</dd><dt>Route</dt><dd>{escape(item.route.value)}</dd><dt>Salesforce stage</dt><dd>{escape(item.source_stage.value)}</dd><dt>Reason category</dt><dd>{escape(_label(item.reason_code))}</dd><dt>Last updated</dt><dd>{escape(item.updated_at.strftime("%Y-%m-%d %H:%M UTC"))}</dd></dl></div></section><p>Preview contract: no live source values · no credentials · no workflow action.</p></main></body></html>"""
