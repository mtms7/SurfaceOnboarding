"""Read-only commercial-evidence extraction for Phase 1 shadow validation.

This module deliberately operates on one local PDF, DOCX, or text file at a
time. It never calls Salesforce, Workato, or Surface, never writes a copy of
the source document, and never returns document text or customer PII.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree


MAX_PDF_PAGES = 20
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MIN_TEXT_CHARACTERS = 80
CORE_PLUS_RULE_ID = "bundle.core_plus.v2"
DATE_PATTERN = re.compile(r"\b(?:0[1-9]|[12]\d|3[01])[-/](?:0[1-9]|1[0-2])[-/]\d{4}\b")
CE_MODULE_PATTERN = re.compile(r"credential(?:s)?\s+exposure\s+module", re.IGNORECASE)
ENDPOINT_SCOPE_PATTERN = re.compile(r"\b\d{1,3}(?:,\d{3})*\s+end\s+points?\b", re.IGNORECASE)
CORE_PLUS_PATTERN = re.compile(r"\bpentera\s+core\s+plus\s+(enterprise|commercial)\b", re.IGNORECASE)


class EvidenceError(ValueError):
    """A safe, stable error code for a fail-closed evidence result."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _normalise_space(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split())


def _normalise_date(value: str) -> str:
    return datetime.strptime(value.replace("/", "-"), "%d-%m-%Y").date().isoformat()


def _document_state(start_date: str, end_date: str, today: date) -> str:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise EvidenceError("invalid_product_term")
    if today < start:
        return "pending"
    if today > end:
        return "expired"
    return "active"


def _subscription_section(page_text: str) -> str:
    """Return only product/term content, never signature or file metadata."""
    lowered = page_text.casefold()
    start = lowered.find("subscription details")
    if start < 0:
        raise EvidenceError("subscription_details_not_found")
    ends = [
        value
        for value in (
            lowered.find("payment terms", start),
            lowered.find("signature", start),
            lowered.find("appendix", start),
        )
        if value >= 0
    ]
    return page_text[start : min(ends) if ends else len(page_text)]


def _term_dates(section: str) -> tuple[str, str]:
    dates: list[str] = []
    for raw_date in DATE_PATTERN.findall(section):
        normalised = _normalise_date(raw_date)
        if normalised not in dates:
            dates.append(normalised)
    if len(dates) != 2:
        raise EvidenceError("ambiguous_product_term_dates")
    return dates[0], dates[1]


def parse_contract_pages(
    pages: Iterable[str],
    *,
    document_ref: str,
    extraction_method: str,
    today: date | None = None,
) -> dict[str, object]:
    """Return sanitised evidence for an explicit Core Plus CE bundle rule.

    OCR evidence deliberately remains unverified until a human visually checks
    the underlying source document.
    """
    if not document_ref.startswith("ContentVersion:"):
        raise EvidenceError("safe_document_reference_required")

    today = today or date.today()
    for page_number, raw_page in enumerate(pages, start=1):
        page = _normalise_space(raw_page)
        product_match = CORE_PLUS_PATTERN.search(page)
        if product_match is None:
            continue
        section = _subscription_section(page)
        product_match = CORE_PLUS_PATTERN.search(section)
        if product_match is None:
            continue

        start_date, end_date = _term_dates(section)
        state = _document_state(start_date, end_date, today)
        ce_wording_explicit = bool(CE_MODULE_PATTERN.search(section))
        scope_match = ENDPOINT_SCOPE_PATTERN.search(section)
        scope = scope_match.group(0) if scope_match else "not_extracted"

        entitlement = "bundled_verified"
        review_reason = ""
        if not ce_wording_explicit:
            entitlement = "unverified"
            review_reason = "credential_exposure_bundle_wording_not_found"
        elif state != "active":
            entitlement = "unverified"
            review_reason = f"commercial_document_{state}"
        elif extraction_method == "local_ocr":
            entitlement = "unverified"
            review_reason = "ocr_requires_visual_confirmation"

        return {
            "result": "commercial_evidence_extracted",
            "proceed": False,
            "action_required": "human_review_required",
            "document_ref": document_ref,
            "document_state": state,
            "canonical_product": "core_plus_" + product_match.group(1).casefold(),
            "license_scope": scope,
            "start_date": start_date,
            "end_date": end_date,
            "credential_exposure": entitlement,
            "review_reason": review_reason,
            "evidence": {
                "page": page_number,
                "section": "Subscription Details",
                "rule_id": CORE_PLUS_RULE_ID,
                "extraction_method": extraction_method,
                "explicit_ce_bundle_clause_found": ce_wording_explicit,
            },
        }
    raise EvidenceError("supported_core_plus_enterprise_evidence_not_found")


def _extract_docx_pages(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(document_xml)
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise EvidenceError("invalid_docx") from error

    text = " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    if len(_normalise_space(text)) < MIN_TEXT_CHARACTERS:
        raise EvidenceError("document_text_unavailable")
    return [text]


def _extract_pdf_text_pages(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as error:
        raise EvidenceError("pdf_text_extractor_unavailable") from error
    try:
        reader = PdfReader(path)
        if len(reader.pages) > MAX_PDF_PAGES:
            raise EvidenceError("pdf_page_limit_exceeded")
        pages = [page.extract_text() or "" for page in reader.pages]
    except EvidenceError:
        raise
    except Exception as error:
        raise EvidenceError("unreadable_or_encrypted_pdf") from error
    if sum(len(_normalise_space(page)) for page in pages) < MIN_TEXT_CHARACTERS:
        raise EvidenceError("ocr_required")
    return pages


def _extract_pdf_ocr_pages(path: Path) -> list[str]:
    """Use local renderer/OCR only; the temporary page images are deleted."""
    renderer = shutil.which("pdftoppm")
    ocr = shutil.which("tesseract")
    if not renderer or not ocr:
        raise EvidenceError("local_ocr_unavailable")
    with tempfile.TemporaryDirectory(prefix="phase1-commercial-evidence-") as temp_dir:
        prefix = Path(temp_dir) / "page"
        try:
            subprocess.run(
                [renderer, "-png", "-r", "200", str(path), str(prefix)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=45,
            )
            images = sorted(Path(temp_dir).glob("page-*.png"))
            if not images or len(images) > MAX_PDF_PAGES:
                raise EvidenceError("pdf_page_limit_exceeded")
            pages = []
            for image in images:
                result = subprocess.run(
                    [ocr, str(image), "stdout", "--psm", "6"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    timeout=45,
                )
                pages.append(result.stdout)
        except EvidenceError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise EvidenceError("local_ocr_failed") from error
    if sum(len(_normalise_space(page)) for page in pages) < MIN_TEXT_CHARACTERS:
        raise EvidenceError("ocr_text_unavailable")
    return pages


def extract_local_evidence(
    path: Path,
    *,
    document_ref: str,
    allow_local_ocr: bool = False,
    today: date | None = None,
) -> dict[str, object]:
    """Extract one local file without returning source content or PII."""
    if not path.is_file():
        raise EvidenceError("document_not_found")
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise EvidenceError("document_size_limit_exceeded")
    suffix = path.suffix.casefold()
    if suffix == ".docx":
        pages, method = _extract_docx_pages(path), "docx_text"
    elif suffix == ".pdf":
        try:
            pages, method = _extract_pdf_text_pages(path), "pdf_text"
        except EvidenceError as error:
            if error.code != "ocr_required" or not allow_local_ocr:
                raise
            pages, method = _extract_pdf_ocr_pages(path), "local_ocr"
    elif suffix == ".txt":
        try:
            pages, method = [path.read_text(encoding="utf-8")], "text_fixture"
        except UnicodeDecodeError as error:
            raise EvidenceError("document_text_unavailable") from error
    else:
        raise EvidenceError("unsupported_document_format")
    return parse_contract_pages(
        pages,
        document_ref=document_ref,
        extraction_method=method,
        today=today,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description="Extract safe Phase 1 commercial evidence locally.")
    parser.add_argument("--input", required=True, help="Local PDF, DOCX, or text fixture path")
    parser.add_argument("--document-ref", required=True, help="Salesforce ContentVersion:<id> reference")
    parser.add_argument("--allow-local-ocr", action="store_true", help="Use local pdftoppm + tesseract only")
    arguments = parser.parse_args()
    try:
        result = extract_local_evidence(
            Path(arguments.input),
            document_ref=arguments.document_ref,
            allow_local_ocr=arguments.allow_local_ocr,
        )
    except EvidenceError as error:
        result = {
            "result": "commercial_evidence_unverified",
            "proceed": False,
            "action_required": "contact_csm",
            "reason": error.code,
        }
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
