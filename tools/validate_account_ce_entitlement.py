"""Attended, read-only CE entitlement validator for one CO and Account PDF.

The operator must select the exact Salesforce ContentVersion ID.  The script
will not choose a document by customer filename, download every attachment, or
write Salesforce.  It uses the current Salesforce CLI browser session and
deletes its temporary PDF before returning a sanitised result.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase1_validator.ce_entitlement import evaluate_credential_exposure_entitlement
from phase1_validator.commercial_evidence import EvidenceError, extract_local_evidence


REFERENCE = re.compile(r"CO-[0-9]{4,10}$")
SF_ID = re.compile(r"[A-Za-z0-9]{15,18}$")
FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


class ValidationUnavailable(RuntimeError):
    pass


def sf_json(args: list[str]) -> object:
    try:
        done = subprocess.run(["sf.cmd", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, timeout=45, check=False, text=True)
        if done.returncode or len(done.stdout.encode()) > 512 * 1024:
            raise ValidationUnavailable()
        return json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ValidationUnavailable() from exc


def _records(response: object) -> list[dict[str, object]]:
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            raise ValidationUnavailable()
        return records
    except (KeyError, TypeError):
        raise ValidationUnavailable() from None


def dealhub_quantity_field() -> str:
    """Resolve the displayed Dealhub Quantity field without guessing its API name."""
    response = sf_json(["sobject", "describe", "--sobject", "DealHub_Subscription__c", "--json"])
    try:
        fields = response["result"]["fields"]  # type: ignore[index]
        matches = [field.get("name") for field in fields if isinstance(field, dict)
                   and str(field.get("label", "")).casefold() == "dealhub quantity"]
        if response["status"] != 0 or len(matches) != 1 or not isinstance(matches[0], str) or not FIELD_NAME.fullmatch(matches[0]):
            raise ValidationUnavailable()
        return matches[0]
    except (KeyError, TypeError):
        raise ValidationUnavailable() from None


def _result(entitlement: dict[str, object]) -> dict[str, object]:
    return {
        "result": "credential_exposure_entitlement_evaluated",
        "proceed": False,
        "action_required": "human_review_required",
        "credential_exposure_ready": entitlement["credential_exposure_ready"],
        "manual_review_required": entitlement["manual_review_required"],
        "email_domain_limit": entitlement["email_domain_limit"],
        "evidence_source": entitlement["evidence_source"],
        "reason": entitlement["reason"],
    }


def validate(reference: str, content_version_id: str | None = None, *, allow_local_ocr: bool = False) -> dict[str, object]:
    if not REFERENCE.fullmatch(reference) or (content_version_id is not None and not SF_ID.fullmatch(content_version_id)):
        raise ValidationUnavailable()
    co_rows = _records(sf_json(["data", "query", "--query", "SELECT Account__c FROM Customer_Onboarding__c WHERE Name = '" + reference + "' LIMIT 2", "--json"]))
    if len(co_rows) != 1 or not isinstance(co_rows[0].get("Account__c"), str) or not SF_ID.fullmatch(co_rows[0]["Account__c"]):
        raise ValidationUnavailable()
    account_id = co_rows[0]["Account__c"]
    quantity_field = dealhub_quantity_field()
    subscriptions = _records(sf_json(["data", "query", "--query", "SELECT Product_Full_Name__c, " + quantity_field + " FROM DealHub_Subscription__c WHERE DealHub_Account__c = '" + account_id + "' LIMIT 100", "--json"]))
    normalized_subscriptions = [{"product_full_name": row.get("Product_Full_Name__c"), "quantity": row.get(quantity_field)} for row in subscriptions]
    explicit_module = evaluate_credential_exposure_entitlement(normalized_subscriptions)
    if explicit_module["credential_exposure_ready"]:
        return _result(explicit_module)
    if content_version_id is None:
        return _result({"credential_exposure_ready": False, "manual_review_required": True,
                        "email_domain_limit": 0, "evidence_source": "", "reason": "core_contract_content_version_required"})
    links = _records(sf_json(["data", "query", "--query", "SELECT ContentDocumentId FROM ContentDocumentLink WHERE LinkedEntityId = '" + account_id + "' LIMIT 100", "--json"]))
    document_ids = {row.get("ContentDocumentId") for row in links if isinstance(row.get("ContentDocumentId"), str)}
    version_rows = _records(sf_json(["data", "query", "--query", "SELECT Id, ContentDocumentId, ContentSize, FileType FROM ContentVersion WHERE Id = '" + content_version_id + "' LIMIT 2", "--json"]))
    if len(version_rows) != 1:
        raise ValidationUnavailable()
    version = version_rows[0]
    if (version.get("Id") != content_version_id or version.get("ContentDocumentId") not in document_ids
            or version.get("FileType") != "PDF" or not isinstance(version.get("ContentSize"), int)
            or version["ContentSize"] < 1 or version["ContentSize"] > MAX_DOCUMENT_BYTES):
        raise ValidationUnavailable()
    with tempfile.TemporaryDirectory(prefix="surface-ce-evidence-") as directory:
        evidence_path = Path(directory) / "evidence.pdf"
        try:
            download = subprocess.run(
                ["sf.cmd", "api", "request", "rest", "--url", "/services/data/v67.0/sobjects/ContentVersion/" + content_version_id + "/VersionData", "--method", "GET", "--output-file", str(evidence_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=90, check=False,
            )
            if download.returncode or not evidence_path.is_file() or evidence_path.stat().st_size < 1:
                raise ValidationUnavailable()
            contract = extract_local_evidence(evidence_path, document_ref="ContentVersion:selected", allow_local_ocr=allow_local_ocr, today=date.today())
        except EvidenceError:
            contract = {"credential_exposure": "unverified", "canonical_product": "", "review_reason": "commercial_document_unverified"}
    return _result(evaluate_credential_exposure_entitlement(normalized_subscriptions, contract_evidence=contract))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only CE entitlement check for one selected Account PDF.")
    parser.add_argument("--co", required=True)
    parser.add_argument("--content-version", help="Explicit Account-linked PDF ContentVersion ID; needed only when no CE Module is found")
    parser.add_argument("--allow-local-ocr", action="store_true")
    args = parser.parse_args()
    try:
        result = validate(args.co, args.content_version, allow_local_ocr=args.allow_local_ocr)
    except ValidationUnavailable:
        result = {"result": "credential_exposure_entitlement_unverified", "proceed": False,
                  "action_required": "human_review_required", "reason": "attended_source_validation_unavailable"}
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
