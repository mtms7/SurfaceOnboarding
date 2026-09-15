"""Fail-closed Credential Exposure entitlement classification.

This module receives normalised DealHub product facts and optional sanitized
commercial-document evidence.  It never reads Salesforce, downloads a file,
or writes a Customer Onboarding record.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


CE_MODULE = re.compile(r"\bcredential(?:s)? exposure module\b", re.IGNORECASE)


def _manual(reason: str) -> dict[str, object]:
    return {"credential_exposure_ready": False, "manual_review_required": True,
            "email_domain_limit": 0, "evidence_source": "", "reason": reason}


def _positive_quantity(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def evaluate_credential_exposure_entitlement(
    subscriptions: Sequence[Mapping[str, Any]], *, contract_evidence: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Prefer an explicit CE Module quantity; otherwise accept explicit Core evidence.

    Bundled Core evidence carries the owner-defined one-email-domain baseline.
    Any result remains review-only and cannot authorize a Salesforce or
    Leonardo mutation.
    """
    module_rows = [row for row in subscriptions if CE_MODULE.search(str(row.get("product_full_name") or ""))]
    if len(module_rows) == 1:
        quantity = _positive_quantity(module_rows[0].get("quantity"))
        if quantity is None:
            return _manual("credential_exposure_module_quantity_invalid")
        return {"credential_exposure_ready": True, "manual_review_required": False,
                "email_domain_limit": quantity, "evidence_source": "dealhub_ce_module", "reason": ""}
    if len(module_rows) > 1:
        return _manual("credential_exposure_module_ambiguous")
    if contract_evidence is None:
        return _manual("credential_exposure_evidence_missing")
    if (contract_evidence.get("credential_exposure") != "bundled_verified"
            or not str(contract_evidence.get("canonical_product", "")).startswith("core_plus_")):
        return _manual("core_contract_credential_exposure_unverified")
    return {"credential_exposure_ready": True, "manual_review_required": False,
            "email_domain_limit": 1, "evidence_source": "core_contract_bundle", "reason": ""}
