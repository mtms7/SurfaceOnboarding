"""Production match: does this CO already have a tenant in PRODUCTION, and does it agree with the CO? (pure)

Owner decisions 2026-10-06 / 2026-10-07. Read-only and informational: it reads only the production-clone snapshot
(Redash) that ``leonardo_inventory.load_latest`` already verifies (same 6 h age and tamper rules as the Start gate),
never writes, and a "no tenant found" is never a clearance. First hit wins:

  clone_unavailable  snapshot missing / older than 6 h / tampered / no alternate-domain data
  not_checked        the clone is fine but the CO's inputs (names, domains, plan) are not available for this CO
  onboarded          the CO's own Account UUID (Salesforce) equals exactly one clone tenant's accountUuid: the tenant is
                     reviewed (enabled, licence, SpyCloud, counts, name suffix); deviations are warnings, not a gate
  uuid_not_in_clone  the CO holds an Account UUID but no clone tenant has it and nothing else matches (blocks; the clone may
                     be older than the create)
  not_in_production  no candidate (same name key, or a primary / alternate domain equal to a CO domain)
  exists_other       candidates exist, but none is a live, paid tenant with the CO's EXACT name (BLOCKS migration)
  ambiguous          more than one live, paid tenant with the exact name
  exists_matches     exactly one such tenant and every REQUIRED field agrees
  exists_differs     exactly one such tenant; a required field differs or is unknown (field codes listed)

Required: tenant name, primary domain, alternate domains, licence type, licence end.
Informational (never block): SpyCloud (owner 2026-10-08: ON is Leonardo's default; the production value is only shown,
CE routes only), licence start, operator assigned, scan status, Salesforce account id ("not checked").
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any

from integration.onboarding.production_contract import LICENSE_COUNT, LICENSE_TYPE, NAME_SUFFIX
from integration.onboarding.leonardo_inventory import (
    PRODUCTION_GATE_MAX_AGE, InventoryError, _name_key, canonical_domain, duplicate_precheck, is_live_paid_tenant,
    load_latest)

STATUSES = ("clone_unavailable", "not_checked", "onboarded", "uuid_not_in_clone", "not_in_production", "exists_other", "ambiguous", "exists_matches", "exists_differs",
            "tenant_found", "tenant_missing")
REQUIRED_FIELDS = ("tenant_name", "primary_domain", "alternate_domains", "license_type", "license_end")
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
    if plan.get("spycloud_shown"):
        # Informational only (owner 2026-10-08): the production value is shown, never compared, never blocks.
        spy = tenant.get("spycloud_enabled")
        rows.append(_field("spycloud", "SpyCloud", "ON (default)",
                           {True: "ON", False: "OFF"}.get(spy, "—") if isinstance(spy, bool) else "—", "info", False))
    else:
        rows.append(_field("spycloud", "SpyCloud", "not applicable (Surface only)", "—", "na", False))
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


_UUID_KEY = re.compile(r"[0-9a-f]{32}")
NOT_IN_QUERY_251 = "Not in query 251, so not checked: country, user email domains, Advanced flags, MFA."
WARNING_TEXT = {
    "tenant_disabled": "tenant disabled", "tenant_deleted": "tenant deleted", "license_not_enabled": "licence not enabled",
    "license_expired": "licence expired", "license_end_unknown": "licence end unknown", "expiry_differs": "licence end differs from the DealHub rule",
    "license_counts_differ": "licence counts are not 1/1/1", "spycloud_off": "SpyCloud off or unknown",
    "leaked_credentials_off": "leaked credentials not allowed or unknown", "name_suffix_missing": "name lacks the ' - CE Only' suffix",
    "identity_mismatch": "IDENTITY MISMATCH: the tenant that owns this UUID has a different name or primary domain",
    "license_type_differs": "licence type is not the expected prepaid annual subscription",
    "alternate_domains_differ": "alternate domains differ from the plan"}


def uuid_key(value: Any) -> str | None:
    """A normalised Account UUID (hyphens stripped, lower case, 32 hex) or None. Never shown or logged."""
    if not isinstance(value, str):
        return None
    key = value.strip().replace("-", "").casefold()
    return key if _UUID_KEY.fullmatch(key) else None


def review_onboarded(tenant: Mapping[str, Any], plan: Mapping[str, Any], *, ce_only: bool, today: date,
                     names: Any = ()) -> dict[str, Any]:
    """Owner review of a tenant the CO already owns (warnings only; never a create gate). Uses only clone fields.

    The UUID owner is not trusted blindly: a name or primary-domain mismatch is ``identity_mismatch`` (the label becomes
    "Onboarded: review"), and every required field that differs from the plan becomes a visible warning."""
    lic = tenant.get("license") if isinstance(tenant.get("license"), dict) else {}
    plan = plan if isinstance(plan, Mapping) else {}
    warnings: list[str] = []

    def warn(code: str) -> None:
        if code not in warnings:
            warnings.append(code)

    for row in compare_fields(tenant, plan, names):
        if not row["required"] or row["result"] == "match":
            continue
        if row["code"] in ("tenant_name", "primary_domain"):
            warn("identity_mismatch")
        elif row["code"] == "license_type":
            warn("license_type_differs")
        elif row["code"] == "alternate_domains" and row["result"] == "differs":
            warn("alternate_domains_differ")
    if tenant.get("enabled") is not True:
        warn("tenant_disabled")
    if tenant.get("is_deleted") is True:
        warn("tenant_deleted")
    if lic.get("enabled") is not True:
        warn("license_not_enabled")
    ends = _tenant_dates(lic.get("expiration_date"))
    expected = _iso(plan.get("license_end"))
    if not ends:
        warn("license_end_unknown")
    else:
        if max(ends) < today.isoformat():
            warn("license_expired")
        if expected and expected not in ends:
            warn("expiry_differs")
    if ce_only:
        if _enum(lic.get("type")) != _enum(LICENSE_TYPE):
            warn("license_type_differs")
        if any(lic.get(key) != LICENSE_COUNT or isinstance(lic.get(key), bool)
               for key in ("assets_number", "domains_number", "subdomains_number")):
            warn("license_counts_differ")
        if tenant.get("spycloud_enabled") is not True:
            warn("spycloud_off")
        if lic.get("leaked_credentials_allowed") is not True:
            warn("leaked_credentials_off")
        if not str(tenant.get("account_name") or "").endswith(NAME_SUFFIX):
            warn("name_suffix_missing")
    shown = _shown_date(lic.get("expiration_date")) if ends else "unknown"
    if warnings:
        line = "Onboarded - check: " + "; ".join(WARNING_TEXT[code] for code in warnings) + "."
    elif expected:
        line = ("Onboarded - tenant enabled, license active until " + shown + " (matches DealHub end rule: expected "
                + expected + ").")
    else:
        line = "Onboarded - tenant enabled, license active until " + shown + " (expiry not verified: DealHub end unknown)."
    return {"line": line, "warnings": warnings, "not_in_query": NOT_IN_QUERY_251}


def _result(status: str, reason: str = "", **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, "fields": [], "differs": [], "candidates": [], "target": None,
            "captured_at": None, "age_seconds": None, "tenants_checked": 0, **extra}


def _uuid_not_in_clone(candidates: list[dict[str, Any]], base: Mapping[str, Any]) -> dict[str, Any]:
    """The CO holds an Account UUID no clone tenant carries yet (the clone lags the create). Name or domain matches that are
    only trial, evaluation, POV, disabled or deleted tenants are listed but do not turn it into "Other tenants only" (owner
    request 2026-10-28, CO-0824 / CO-0813); a live paid match is never handled here (it stays a duplicate). Still blocks."""
    reason = "UUID set but tenant not found in clone (the clone may be older than the create)."
    if candidates:
        reason += " Other non-live tenants match by name or domain."
    return _result("uuid_not_in_clone", reason, candidates=[_summary(tenant) for tenant in candidates][:MAX_LISTED], **base)


def identity_match(identity: Mapping[str, Any] | None, snapshot: Mapping[str, Any] | str | None, *,
                   renewal: bool) -> dict[str, Any]:
    """The clone check for a CO whose plan cannot be built (renewals, Surface + CE New, anything but CE-only New).

    ``identity``: {"names": (...), "domains": (...), "uuid": str | None}, all from Salesforce. Same statuses as
    ``production_match`` where they apply: ``onboarded`` (exactly one tenant carries the CO's Account UUID; the plan is NOT
    reviewed), ``ambiguous``, ``uuid_not_in_clone``, ``not_in_production`` (new routes) or ``tenant_missing`` (a renewal
    expects its tenant), ``exists_other``, ``exists_differs`` with ``differs == ["plan_not_compared"]`` (a live paid tenant has
    the exact name: a duplicate of a new onboarding) or ``tenant_found`` (renewal). It can only block, never clear: a miss is
    never a clearance. Pure; never raises on bad data (fails closed)."""
    if not isinstance(snapshot, Mapping):
        return _result("clone_unavailable", snapshot if isinstance(snapshot, str) and snapshot else "inventory_snapshot_missing")
    tenants = [tenant for tenant in snapshot.get("tenants") or () if isinstance(tenant, dict)]
    base = {"captured_at": snapshot.get("captured_at"), "age_seconds": snapshot.get("age_seconds"),
            "tenants_checked": len(tenants), "identity_only": True}
    if not any(isinstance(tenant.get("alternate_domains"), list) for tenant in tenants):
        return _result("clone_unavailable", "alternate_domains_missing", **base)
    ident = identity if isinstance(identity, Mapping) else {}
    names = [name for name in ident.get("names") or () if isinstance(name, str) and _name_key(name)]
    domains = [domain for domain in ident.get("domains") or () if canonical_domain(domain)]
    account_uuid = ident.get("uuid")
    uuid_set = isinstance(account_uuid, str) and bool(account_uuid.strip())
    if not names and not domains and not uuid_set:
        return _result("not_checked", "co_identity_unreadable", **base)
    keys = {_name_key(name) for name in names}
    first = names[0] if names else ""
    candidates = []
    for tenant in tenants:
        if duplicate_precheck({"tenants": [tenant]}, first, domains)["matches"] or _name_key(tenant.get("account_name")) in keys:
            candidates.append(tenant)
    if uuid_set:
        wanted = uuid_key(account_uuid)
        owner = [tenant for tenant in tenants if wanted is not None and uuid_key(tenant.get("account_uuid")) == wanted]
        if len(owner) > 1:
            return _result("ambiguous", "More than one tenant carries this CO's Account UUID.",
                           candidates=[_summary(tenant) for tenant in owner], **base)
        if owner:
            rivals = [tenant for tenant in candidates if tenant is not owner[0] and is_live_paid_tenant(tenant)]
            if rivals:
                return _result("ambiguous", "Another live, paid tenant matches this CO by name or domain besides the tenant "
                               "that owns its Account UUID.", candidates=[_summary(tenant) for tenant in [owner[0], *rivals]], **base)
            review = {"line": "Onboarded - tenant found by the CO's Account UUID (identity check only; the plan was not compared).",
                      "warnings": []}
            return _result("onboarded", review["line"], candidates=[_summary(owner[0])], target=_summary(owner[0]), review=review,
                           **base)
        if not any(is_live_paid_tenant(tenant) for tenant in candidates):
            return _uuid_not_in_clone(candidates, base)
    if not candidates:
        if renewal:
            return _result("tenant_missing", "No tenant with this name or domain in the clone: a renewal expects its tenant "
                           "(the clone can lag up to a day). Check BackOffice.", **base)
        return _result("not_in_production", NOT_A_CLEARANCE, **base)
    listed = [_summary(tenant) for tenant in candidates]
    if renewal:
        return _result("tenant_found", str(len(candidates)) + " tenant(s) match by name or domain (identity check only).",
                       candidates=listed, target=listed[0] if len(listed) == 1 else None, **base)
    exact = [tenant for tenant in candidates if is_live_paid_tenant(tenant) and _name_key(tenant.get("account_name")) in keys]
    if not exact:
        return _result("exists_other", "Only trial, evaluation, POV, disabled or deleted tenants match; migration is blocked "
                       "until the owner resolves it.", candidates=listed, **base)
    if len(exact) > 1:
        return _result("ambiguous", str(len(exact)) + " live, paid tenants have the exact name.", candidates=listed, **base)
    return _result("exists_differs", "A live, paid tenant has this CO's exact name; its plan was not compared (identity check only).",
                   differs=["plan_not_compared"], candidates=listed, target=_summary(exact[0]), **base)


def production_match(co_source: Mapping[str, Any] | None, plan: Mapping[str, Any] | None,
                     snapshot: Mapping[str, Any] | str | None, *, account_uuid: Any = None, ce_only: bool = False,
                     today: date | None = None) -> dict[str, Any]:
    """``co_source``: {"tenant_names": (...), "domains": (...)}; ``plan``: see ``compare_fields`` (primary_domain,
    alternate_domains, license_type, license_end, license_start, spycloud_shown); ``snapshot``: the verified clone
    payload, or a reason string / None when it cannot be used. ``account_uuid`` is the CO's own Salesforce Account UUID (never
    stored or shown). Pure; never raises on bad data (fails closed)."""
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
        return _result("not_checked", "co_source_unreadable", **base)
    keys = {_name_key(name) for name in names}
    candidates = []
    for tenant in tenants:
        hit = duplicate_precheck({"tenants": [tenant]}, names[0], domains)["matches"]
        if hit or _name_key(tenant.get("account_name")) in keys:
            candidates.append(tenant)
    uuid_set = isinstance(account_uuid, str) and bool(account_uuid.strip())
    if uuid_set:
        wanted = uuid_key(account_uuid)
        owned = [tenant for tenant in tenants if wanted is not None and uuid_key(tenant.get("account_uuid")) == wanted]
        if len(owned) > 1:
            return _result("ambiguous", "More than one tenant carries this CO's Account UUID.",
                           candidates=[_summary(tenant) for tenant in owned], **base)
        if owned:
            rivals = [tenant for tenant in candidates if tenant is not owned[0] and is_live_paid_tenant(tenant)]
            if rivals:  # a candidate matches the CO by name or domain: another live paid tenant is a duplicate of the owner
                return _result("ambiguous", "Another live, paid tenant matches this CO by name or domain besides the tenant "
                               "that owns its Account UUID.",
                               candidates=[_summary(tenant) for tenant in [owned[0], *rivals]], **base)
            review = review_onboarded(owned[0], plan, ce_only=ce_only, today=today or datetime.now(timezone.utc).date(),
                                     names=names)
            return _result("onboarded", review["line"], fields=compare_fields(owned[0], plan, names), candidates=[_summary(owned[0])],
                           target=_summary(owned[0]), review=review, **base)
        if not any(is_live_paid_tenant(tenant) for tenant in candidates):
            return _uuid_not_in_clone(candidates, base)
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
