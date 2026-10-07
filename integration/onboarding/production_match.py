"""Production match: does this CO already have a tenant in PRODUCTION, and does it agree with the CO? (pure)

Owner decisions 2026-10-06 / 2026-10-07. Read-only and informational: it reads only the production-clone snapshot
(Redash) that ``leonardo_inventory.load_latest`` already verifies (same 6 h age and tamper rules as the Start gate),
never writes, and a "no tenant found" is never a clearance. First hit wins:

  clone_unavailable  snapshot missing / older than 6 h / tampered / no alternate-domain data, or the CO source unreadable
  not_in_production  no candidate (same name key, or a primary / alternate domain equal to a CO domain)
  exists_other       candidates exist, but none is a live, paid tenant with the CO's EXACT name (BLOCKS migration)
  ambiguous          more than one live, paid tenant with the exact name
  exists_matches     exactly one such tenant and every REQUIRED field agrees
  exists_differs     exactly one such tenant; a required field differs or is unknown (field codes listed)

Required: tenant name, primary domain, alternate domains, licence type, licence end, SpyCloud OFF (CE routes only).
Informational (never block): licence start, operator assigned, scan status, Salesforce account id ("not checked").
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any

from integration.onboarding.leonardo_inventory import (
    PRODUCTION_GATE_MAX_AGE, InventoryError, _name_key, canonical_domain, duplicate_precheck, is_live_paid_tenant,
    load_latest)

STATUSES = ("clone_unavailable", "not_in_production", "exists_other", "ambiguous", "exists_matches", "exists_differs")
REQUIRED_FIELDS = ("tenant_name", "primary_domain", "alternate_domains", "license_type", "license_end", "spycloud_off")
NOT_A_CLEARANCE = "No tenant found. This is not a clearance: the clone can lag up to a day."
MAX_LISTED = 8


def load_snapshot(root: Any, now: datetime, max_age: timedelta = PRODUCTION_GATE_MAX_AGE) -> Mapping[str, Any] | str:
    """The verified production-clone snapshot, or the reason code it cannot be used (never raises)."""
    try:
        return load_latest(root, "prod-clone", max_age=max_age, now=now)
    except InventoryError as error:
        return error.reason


def _norm(value: Any) -> str:
    """The renewal domains check's normalizer: case-folded, whitespace collapsed, no trailing dot."""
    return " ".join(str(value).casefold().split()).rstrip(".") if value is not None else ""


def _enum(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold()) if isinstance(value, str) else ""


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            return None
    return None


def _tenant_dates(value: Any) -> set[str]:
    """A clone licence date as ISO dates (epoch ms in UTC and local time: a day boundary may fall between)."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        try:
            return {datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat(),
                    datetime.fromtimestamp(value / 1000).date().isoformat()}
        except (OverflowError, OSError, ValueError):
            return set()
    iso = _iso(value)
    return {iso} if iso else set()


def _shown_date(value: Any) -> str:
    dates = _tenant_dates(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and dates:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return min(dates) if dates else "—"


def _list_text(items: Any) -> str:
    values = sorted(items)
    if not values:
        return "none"
    return ", ".join(values[:MAX_LISTED]) + (" +" + str(len(values) - MAX_LISTED) + " more" if len(values) > MAX_LISTED else "")


def _field(code: str, label: str, expected: str, production: str, result: str, required: bool) -> dict[str, Any]:
    return {"code": code, "label": label, "expected": expected, "production": production, "result": result,
            "required": required}


def _summary(tenant: Mapping[str, Any]) -> dict[str, Any]:
    lic = tenant.get("license") if isinstance(tenant.get("license"), dict) else {}
    return {"id": tenant.get("id"), "name": tenant.get("account_name"), "domain": tenant.get("account_domain"),
            "created": tenant.get("created"), "deleted": tenant.get("is_deleted") is True,
            "enabled": tenant.get("enabled") is True, "live_paid": is_live_paid_tenant(tenant),
            "license_type": lic.get("type")}


def compare_fields(tenant: Mapping[str, Any], plan: Mapping[str, Any], tenant_names: Any = ()) -> list[dict[str, Any]]:
    """Required fields (match / differs / unknown) then the informational ones, for the one target tenant."""
    lic = tenant.get("license") if isinstance(tenant.get("license"), dict) else {}
    scan = tenant.get("scan") if isinstance(tenant.get("scan"), dict) else {}
    names = [name for name in tenant_names or () if _name_key(name)]
    rows: list[dict[str, Any]] = []

    def decide(code: str, label: str, expected: Any, produced: Any, shown_expected: str, shown_produced: str,
               equal: Any = lambda a, b: a == b) -> None:
        # An expected alternate-domain set may legitimately be empty; every other missing value is "unknown".
        expected_missing = expected is None or (expected == "" and code != "alternate_domains")
        if expected_missing or produced is None or (isinstance(produced, str) and not produced):
            result = "unknown"
        else:
            result = "match" if equal(expected, produced) else "differs"
        rows.append(_field(code, label, shown_expected, shown_produced, result, True))

    name = tenant.get("account_name")
    decide("tenant_name", "Tenant name", _name_key(names[0]) if names else None,
           _name_key(name) or None, names[0] if names else "—", str(name or "—"))
    primary = canonical_domain(plan.get("primary_domain"))
    decide("primary_domain", "Primary domain", primary, canonical_domain(tenant.get("account_domain")),
           primary or "—", str(canonical_domain(tenant.get("account_domain")) or "—"))
    wanted = {item for item in map(_norm, plan.get("alternate_domains") or ()) if item}
    have_raw = tenant.get("alternate_domains")
    have = {item for item in map(_norm, have_raw) if item} if isinstance(have_raw, list) else None
    decide("alternate_domains", "Alternate domains", wanted, have if have is not None else None,
           _list_text(wanted), _list_text(have) if have is not None else "—")
    expected_type = plan.get("license_type")
    decide("license_type", "Licence type", _enum(expected_type) or None, _enum(lic.get("type")) or None,
           str(expected_type or "—"), str(lic.get("type") or "—"))
    expected_end = _iso(plan.get("license_end"))
    end_value = lic.get("expiration_date")
    decide("license_end", "Licence end", expected_end, _tenant_dates(end_value) or None,
           expected_end or "—", _shown_date(end_value), lambda want, got: want in got)
    if plan.get("spycloud_off"):
        spy = tenant.get("spycloud_enabled")
        decide("spycloud_off", "SpyCloud OFF", True, spy if isinstance(spy, bool) else None, "OFF",
               {True: "ON", False: "OFF"}.get(spy, "—") if isinstance(spy, bool) else "—", lambda _w, got: got is False)
    else:
        rows.append(_field("spycloud_off", "SpyCloud OFF", "not required (Surface only)", "—", "na", False))
    start_expected, start_value = _iso(plan.get("license_start")), lic.get("start_date")
    start_result = ("info" if start_expected is None or not _tenant_dates(start_value)
                    else ("match" if start_expected in _tenant_dates(start_value) else "differs"))
    rows.append(_field("license_start", "Licence start", start_expected or "not planned", _shown_date(start_value),
                       start_result, False))
    operator = tenant.get("operator_assigned")
    rows.append(_field("operator_assigned", "Operator assigned", "—",
                       {True: "yes", False: "no"}.get(operator, "—") if isinstance(operator, bool) else "—", "info", False))
    status = scan.get("status")
    rows.append(_field("scan_status", "Scan status", "—", str(status).title() if isinstance(status, str) and status else "—",
                       "info", False))
    rows.append(_field("salesforce_account_id", "Salesforce account id", "—", "—", "not_checked", False))
    return rows


def _result(status: str, reason: str = "", **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, "fields": [], "differs": [], "candidates": [], "target": None,
            "captured_at": None, "age_seconds": None, "tenants_checked": 0, **extra}


def production_match(co_source: Mapping[str, Any] | None, plan: Mapping[str, Any] | None,
                     snapshot: Mapping[str, Any] | str | None) -> dict[str, Any]:
    """``co_source``: {"tenant_names": (...), "domains": (...)}; ``plan``: see ``compare_fields`` (primary_domain,
    alternate_domains, license_type, license_end, license_start, spycloud_off); ``snapshot``: the verified clone
    payload, or a reason string / None when it cannot be used. Pure; never raises on bad data (fails closed)."""
    if not isinstance(snapshot, Mapping):
        return _result("clone_unavailable", snapshot if isinstance(snapshot, str) and snapshot else "inventory_snapshot_missing")
    tenants = [tenant for tenant in snapshot.get("tenants") or () if isinstance(tenant, dict)]
    base = {"captured_at": snapshot.get("captured_at"), "age_seconds": snapshot.get("age_seconds"),
            "tenants_checked": len(tenants)}
    if not any(isinstance(tenant.get("alternate_domains"), list) for tenant in tenants):
        return _result("clone_unavailable", "alternate_domains_missing", **base)
    names = [name for name in (co_source or {}).get("tenant_names") or () if isinstance(name, str) and _name_key(name)]
    domains = [domain for domain in (co_source or {}).get("domains") or () if canonical_domain(domain)]
    if not isinstance(co_source, Mapping) or not isinstance(plan, Mapping) or not names or not domains:
        return _result("clone_unavailable", "co_source_unreadable", **base)
    keys = {_name_key(name) for name in names}
    candidates = []
    for tenant in tenants:
        hit = duplicate_precheck({"tenants": [tenant]}, names[0], domains)["matches"]
        if hit or _name_key(tenant.get("account_name")) in keys:
            candidates.append(tenant)
    if not candidates:
        return _result("not_in_production", NOT_A_CLEARANCE, **base)
    listed = [_summary(tenant) for tenant in candidates]
    exact = [tenant for tenant in candidates if is_live_paid_tenant(tenant) and _name_key(tenant.get("account_name")) in keys]
    if not exact:
        return _result("exists_other", "Only trial, evaluation, POV, disabled or deleted tenants match; migration is blocked "
                       "until the owner resolves it.", candidates=listed, **base)
    if len(exact) > 1:
        return _result("ambiguous", str(len(exact)) + " live, paid tenants have the exact name.", candidates=listed, **base)
    fields = compare_fields(exact[0], plan, names)
    differs = [row["code"] for row in fields if row["required"] and row["result"] != "match"]
    return _result("exists_differs" if differs else "exists_matches", "", fields=fields, differs=differs,
                   candidates=listed, target=_summary(exact[0]), **base)
