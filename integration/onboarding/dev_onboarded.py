"""Which COs are "onboarded on Dev"? (pure; owner decision 2026-10-07)

Only VERIFIED local evidence counts: a create run recorded ``readback_verified`` with a readback carrying both ids, or
a renewal outcome ``renewal_edit_verified`` (confirm write, verified) / ``renewal_already_current`` (a read-only
verification that the Dev tenant already shows the new term). A verified Dev mirror alone is "Dev mirror" (not
onboarded). Dry runs, uncertain markers and ``attempted_unverified`` writes never count.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

CREATE_RESULTS = {"readback_verified": "Readback verified",
                  "readback_only_verified": "Readback verified (manual create)"}
RENEWAL_LABELS = {"renewal_edit_verified": "Renewal edit verified", "renewal_already_current": "Renewal already current"}


def _readback_ok(readback: Any) -> bool:
    return (isinstance(readback, Mapping) and isinstance(readback.get("surface_account_id"), str)
            and bool(readback["surface_account_id"]) and isinstance(readback.get("account_uuid"), str)
            and bool(readback["account_uuid"]))


def dev_onboarded_evidence(reference: str, *, record: Mapping[str, Any] | None, readback: Mapping[str, Any] | None,
                           outcome: Mapping[str, Any] | None, mirror_ids: Collection[str],
                           renewal_engines: Collection[str], uncertain: bool = False) -> list[dict[str, Any]]:
    """Evidence items for one CO: {"code", "label", "counts", "on" (ISO date or None)}; onboarded = any counts."""
    items: list[dict[str, Any]] = []
    if not _readback_ok(readback):
        return items
    tenant_id = readback["surface_account_id"]  # type: ignore[index]
    route = (record or {}).get("route")
    result = (record or {}).get("result")
    if (record is not None and not uncertain and route not in renewal_engines and result in CREATE_RESULTS):
        items.append({"code": "create_" + str(result), "label": CREATE_RESULTS[str(result)], "counts": True,
                      "on": str(record.get("completed_on") or readback.get("observed_on") or "")[:10] or None})  # type: ignore[union-attr]
    if isinstance(outcome, Mapping) and not uncertain:
        code, write = outcome.get("result"), outcome.get("leonardo_write")
        same_tenant = outcome.get("tenant_id") in (None, tenant_id)
        verified = ((code == "renewal_edit_verified" and outcome.get("mode") == "confirm_write" and write == "verified")
                    or (code == "renewal_already_current" and write != "attempted_unverified"))
        if verified and same_tenant:
            observed = outcome.get("observed_at")
            items.append({"code": str(code), "label": RENEWAL_LABELS[str(code)], "counts": True,
                          "on": observed.date().isoformat() if hasattr(observed, "date") else None})
    if tenant_id in mirror_ids:
        items.append({"code": "dev_mirror", "label": "Dev mirror", "counts": False, "on": None})
    return items


def dev_status(evidence: list[dict[str, Any]]) -> str:
    """"onboarded" (any verified evidence), "mirror" (a Dev mirror only) or "none"."""
    if any(item["counts"] for item in evidence):
        return "onboarded"
    return "mirror" if evidence else "none"
