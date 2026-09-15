"""Fail-closed selection contract for DealHub subscription candidates.

This module mirrors the narrow selection gate used by the Workato Development
router.  It deliberately returns no source values in an error so callers can
log the category without exposing subscription data.
"""

from collections.abc import Mapping, Sequence
from typing import Any


STATUS_RANK = {
    "active": 0,
    "pending": 1,
    "trial": 2,
}

CURRENT_STATUSES = frozenset(STATUS_RANK)
IGNORED_STATUSES = frozenset({"expired"})
ADMINISTRATIVE_PRODUCT_MARKERS = ("remaining usage value",)
ADDON_MARKERS = (
    "credential exposure",
    "ce module",
    "ransomwareready",
    "ransomware ready",
    "security validation advisor",
    "sva",
)


class DealHubSelectionError(ValueError):
    """Sanitized selection error that never includes source values."""

    def __init__(self, error_category: str) -> None:
        self.error_category = error_category
        super().__init__(error_category)


class AmbiguousSubscriptionSelection(DealHubSelectionError):
    """Raised when multiple candidates share the best available status rank."""

    error_category = "ambiguous_subscription_selection"

    def __init__(self) -> None:
        super().__init__(self.error_category)


def _status_rank(row: Mapping[str, Any]) -> int:
    status = row.get("status")
    normalized = "" if status is None else str(status).strip().lower()
    return STATUS_RANK.get(normalized, 3)


def select_unambiguous_best(
    rows: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return the unique best-ranked row, or fail closed on a top-rank tie.

    An empty candidate set is handled by separate missing-evidence gates.
    Lower-ranked alternatives are retained as evidence but do not invalidate a
    unique best-ranked candidate. This conservative intake-level rule blocks
    equal-rank multi-product evidence until opportunity-bound selection exists.
    """

    if not rows:
        return None

    best_rank = min(_status_rank(row) for row in rows)
    best_rows = [row for row in rows if _status_rank(row) == best_rank]
    if len(best_rows) != 1:
        raise AmbiguousSubscriptionSelection()
    return best_rows[0]


def _text(row: Mapping[str, Any], *keys: str) -> str:
    return " ".join(
        str(row.get(key)).strip().lower()
        for key in keys
        if row.get(key) not in (None, "")
    )


def _is_administrative(row: Mapping[str, Any]) -> bool:
    product_text = _text(
        row,
        "product_full_name",
        "product_family",
        "product_sub_family",
        "product_code",
    )
    return any(marker in product_text for marker in ADMINISTRATIVE_PRODUCT_MARKERS)


def classify_product_groups(row: Mapping[str, Any]) -> frozenset[str]:
    """Classify one current commercial row as Surface, Core, and/or add-on.

    A Core row may also carry explicit add-on evidence such as an SVA package.
    Remaining-usage-value rows are administrative and return no product group.
    """

    if _is_administrative(row):
        return frozenset()

    product_text = _text(
        row,
        "product_full_name",
        "product_family",
        "product_sub_family",
        "product_code",
    )
    addon_text = _text(row, "addon_type", "sva_type", "service_package")
    groups: set[str] = set()
    if "surface" in product_text:
        groups.add("surface")
    if "core" in product_text:
        groups.add("core")
    if any(marker in f"{product_text} {addon_text}" for marker in ADDON_MARKERS):
        groups.add("addon")
    return frozenset(groups)


def _opportunity_id(row: Mapping[str, Any]) -> str:
    value = row.get("opportunity_id", row.get("dealhub_opportunity_id"))
    return "" if value is None else str(value).strip()


def _addon_key(row: Mapping[str, Any]) -> str:
    return _text(
        row,
        "addon_type",
        "sva_type",
        "service_package",
        "product_code",
        "product_full_name",
        "product_family",
        "product_sub_family",
    )


def select_surface_core_addons(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Select a one-opportunity Surface/Core bundle and explicit add-ons.

    Expired history and remaining-usage-value rows are ignored. Equal status
    ranks are valid across different product groups, but ties within Surface,
    Core, or the same add-on identity fail closed. Every relevant current row
    must be recognized and bound to the same opportunity.
    """

    relevant: list[Mapping[str, Any]] = []
    classified: list[tuple[Mapping[str, Any], frozenset[str]]] = []
    for row in rows:
        status = _text(row, "status")
        if status in IGNORED_STATUSES:
            continue
        if status not in CURRENT_STATUSES:
            raise DealHubSelectionError("unsupported_subscription_status")
        if _is_administrative(row):
            continue
        groups = classify_product_groups(row)
        if not groups:
            raise DealHubSelectionError("unknown_subscription_product")
        relevant.append(row)
        classified.append((row, groups))

    if not relevant:
        raise DealHubSelectionError("missing_relevant_subscription")

    opportunities = {_opportunity_id(row) for row in relevant}
    if "" in opportunities:
        raise DealHubSelectionError("missing_subscription_opportunity")
    if len(opportunities) != 1:
        raise DealHubSelectionError("ambiguous_subscription_opportunity")

    surface_rows = [row for row, groups in classified if "surface" in groups]
    core_rows = [row for row, groups in classified if "core" in groups]
    addon_rows = [row for row, groups in classified if "addon" in groups]

    addons_by_key: dict[str, list[Mapping[str, Any]]] = {}
    for row in addon_rows:
        key = _addon_key(row)
        if not key:
            raise DealHubSelectionError("unknown_subscription_addon")
        addons_by_key.setdefault(key, []).append(row)

    return {
        "opportunity_id": next(iter(opportunities)),
        "surface": select_unambiguous_best(surface_rows),
        "core": select_unambiguous_best(core_rows),
        "addons": [
            select_unambiguous_best(addons_by_key[key])
            for key in sorted(addons_by_key)
        ],
    }
