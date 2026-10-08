"""Plan a Leonardo Development "mirror" of a production tenant for renewal testing (owner decision 2026-10-06).

Renewal COs (Cases 4-6) target tenants that exist only in production. To exercise a renewal in Leonardo
Development, the runner first creates a mirror: a Dev tenant with the production tenant's name, domains,
licence type and quantities, taken from the production-clone snapshot (Redash query 251).

This module is pure. It opens no connection and runs nothing; its only input beyond its arguments is the
snapshot, read through the existing verified loader (``leonardo_inventory.load_latest``). It fails closed with
a stable reason code and never returns or logs more than the plan needs.

Reason codes (``MirrorError.reason``):
  mirror_clone_unavailable          snapshot missing, stale (> 6 h), tampered, or without alternate domains
  mirror_prod_tenant_not_found      no non-deleted production tenant matches the CO's name or primary domain
  mirror_prod_tenant_ambiguous      more than one distinct live, paid tenant has the CO's exact name
  mirror_co_identity_unavailable    the CO supplied no usable tenant name / domain
  mirror_prod_license_incomplete    the production licence lacks a required count or date
  mirror_license_type_unmapped      the production licence type has no known Add Account option
  mirror_interval_unmapped          the production scanning interval has no known Add Account option
  mirror_lc_domains_unavailable     Leaked Credentials allowed but the CO has no email domain to scan
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any, Mapping

from . import leonardo_inventory as inventory
from .redash_inventory import parse_timestamp_ms

PROD_CLONE_ENV = "prod-clone"
# Add Account "Type" and "Scanning interval" options (live values in the plan, section on the Add Account
# form probe; the label capitalisation of all but "Prepaid annual subscription" is unverified live), by
# normalised production value. Anything else fails closed.
LICENSE_TYPE_OPTIONS = {
    "evaluation": "Evaluation", "trial": "Trial", "prepaidmonthlysubscription": "Prepaid monthly subscription",
    "prepaidannualsubscription": "Prepaid annual subscription", "paygmonthlysubscription": "PAYG monthly subscription"}
INTERVAL_OPTIONS = {"none": "None", "daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}
MIRROR_LC_INTERVAL = "Weekly"  # the standard CE overlay


class MirrorError(Exception):
    """A fail-closed stop; ``reason`` is a stable, value-free code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class MirrorPlan:
    prod_id: str
    tenant_name: str
    primary_domain: str
    alternate_domains: tuple[str, ...]
    license_type: str  # Add Account "Type" option label
    scanning_interval: str  # Add Account "Scanning interval" option label
    scanning_frequency: str | None  # production licence value, informational
    assets: int
    domains: int
    subdomains: int
    leaked_credentials_allowed: bool
    lc_domains_count: int
    lc_domains: tuple[str, ...]  # the CO's email domains, at most ``lc_domains_count``
    spycloud_enabled: bool | None  # production value, informational (Dev SpyCloud stays ON as Leonardo creates it)
    start_date: date
    end_date: date
    production_expiration: date | None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold()) if isinstance(value, str) else ""


def _date_of(value: Any) -> date | None:
    try:
        stamp = parse_timestamp_ms(value)
    except inventory.InventoryError:
        return None
    if stamp is None:
        return None
    try:
        return datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


def _add_one_year(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:  # February 29
        return value.replace(year=value.year + 1, day=28)


def mirror_dates(production_expiration: date | None, today: date) -> tuple[date, date]:
    """(start, end): start is today; end is the production expiration if after today, else today + 1 year - 1 day."""
    if production_expiration is not None and production_expiration > today:
        return today, production_expiration
    return today, _add_one_year(today) - timedelta(days=1)


def find_production_tenant(payload: Mapping[str, Any], tenant_names: Any, domains: Any) -> dict[str, Any]:
    """The single live, paid production tenant with the CO's exact tenant name (owner decision 2026-10-06).

    Trial / Evaluation / POV tenants sharing the customer's domain and disabled or deleted duplicates are never
    chosen (``inventory.is_live_paid_tenant``). Domains are not a match key: they are shared by those tenants.
    """
    names = {key for key in map(inventory._name_key, tenant_names or ()) if key}
    if not names:
        raise MirrorError("mirror_co_identity_unavailable")
    found: dict[str, dict[str, Any]] = {}
    for tenant in payload.get("tenants") or ():
        if not inventory.is_live_paid_tenant(tenant):
            continue
        if inventory._name_key(tenant.get("account_name")) in names:
            identity = tenant.get("id")
            found[identity if isinstance(identity, str) and identity else f"#{len(found)}"] = tenant
    if not found:
        raise MirrorError("mirror_prod_tenant_not_found")
    if len(found) > 1:
        raise MirrorError("mirror_prod_tenant_ambiguous")
    return next(iter(found.values()))


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise MirrorError("mirror_prod_license_incomplete")
    return value


def plan_from_payload(payload: Mapping[str, Any], tenant_names: Any, domains: Any, email_domains: Any,
                      today: date) -> MirrorPlan:
    """Build the Dev Add Account plan from a verified production-clone payload."""
    tenant = find_production_tenant(payload, tenant_names, domains)
    prod_id, name = tenant.get("id"), tenant.get("account_name")
    primary = inventory.canonical_domain(tenant.get("account_domain"))
    if not (isinstance(prod_id, str) and prod_id and isinstance(name, str) and " ".join(name.split()) and primary):
        raise MirrorError("mirror_prod_license_incomplete")
    lic = tenant.get("license") if isinstance(tenant.get("license"), dict) else {}
    license_type = LICENSE_TYPE_OPTIONS.get(_norm(lic.get("type")))
    if license_type is None:
        raise MirrorError("mirror_license_type_unmapped")
    interval = INTERVAL_OPTIONS.get(_norm(tenant.get("scanning_interval")))
    if interval is None:
        raise MirrorError("mirror_interval_unmapped")
    assets, domains_n, subdomains = (_count(lic.get("assets_number")), _count(lic.get("domains_number")),
                                     _count(lic.get("subdomains_number")))
    allowed = lic.get("leaked_credentials_allowed")
    if type(allowed) is not bool:
        raise MirrorError("mirror_prod_license_incomplete")
    lc_count = _count(lic.get("leaked_credentials_scanned_domains_number") or 0)
    lc_domains: tuple[str, ...] = ()
    if allowed:
        co_emails = tuple(dict.fromkeys(d for d in map(inventory.canonical_domain, email_domains or ()) if d))
        lc_domains = co_emails[:max(lc_count, 1)]
        if not lc_domains:
            raise MirrorError("mirror_lc_domains_unavailable")
    alternates = tenant.get("alternate_domains")
    alternate = tuple(d for d in (alternates if isinstance(alternates, list) else ()) if d != primary)
    expiration = _date_of(lic.get("expiration_date"))
    start, end = mirror_dates(expiration, today)
    spy = tenant.get("spycloud_enabled")
    frequency = lic.get("scanning_frequency")
    return MirrorPlan(
        prod_id=prod_id, tenant_name=" ".join(name.split()), primary_domain=primary, alternate_domains=alternate,
        license_type=license_type, scanning_interval=interval,
        scanning_frequency=frequency if isinstance(frequency, str) else None,
        assets=assets, domains=domains_n, subdomains=subdomains, leaked_credentials_allowed=allowed,
        lc_domains_count=lc_count, lc_domains=lc_domains, spycloud_enabled=spy if type(spy) is bool else None,
        start_date=start, end_date=end, production_expiration=expiration)


def plan_mirror(root: Path, tenant_names: Any, domains: Any, email_domains: Any, *, now: datetime,
                today: date | None = None) -> MirrorPlan:
    """Load the production clone (6 h freshness rule, alternate domains required) and plan the mirror."""
    try:
        payload = inventory.load_latest(root, PROD_CLONE_ENV, max_age=inventory.PRODUCTION_GATE_MAX_AGE, now=now)
    except inventory.InventoryError:
        raise MirrorError("mirror_clone_unavailable") from None
    if not any(isinstance(t, dict) and isinstance(t.get("alternate_domains"), list)
               for t in payload.get("tenants") or ()):
        raise MirrorError("mirror_clone_unavailable")
    return plan_from_payload(payload, tenant_names, domains, email_domains, today or now.date())


DEV_MIRROR_SKIP_REASON = "dev_mirror_skipped"


def mirror_tenant_ids(mirror_records: Any) -> frozenset[str]:
    """Dev tenant ids of the verified mirrors in the mirror record store (``attended_renewal_mirrors.json``).

    Only a record that says it is a verified, Development mirror of a production tenant and carries a Dev tenant
    id counts; ``create_attempted`` records and anything malformed are ignored (so they never hide a tenant).
    """
    if not isinstance(mirror_records, Mapping):
        return frozenset()
    ids = set()
    for record in mirror_records.values():
        if (isinstance(record, Mapping) and record.get("mirror_of_production") is True
                and record.get("status") == "verified" and record.get("environment") == "dev"):
            tenant_id = record.get("surface_account_id")
            if isinstance(tenant_id, str) and tenant_id:
                ids.add(tenant_id)
    return frozenset(ids)


def is_dev_mirror(tenant_id: Any, mirror_records: Any) -> bool:
    """True only when ``tenant_id`` equals the Dev tenant id of a verified mirror record (id match, no name guess).

    A mirror's licence is deliberately edited by the renewal, so its drift is expected; skipping anything else
    would hide real problems, so an unknown id, a missing id, or an unreadable store is NOT a mirror.
    """
    return isinstance(tenant_id, str) and bool(tenant_id) and tenant_id in mirror_tenant_ids(mirror_records)
