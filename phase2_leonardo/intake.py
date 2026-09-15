"""Strict Case 3 intake normalization for the OPA host.

This module is deliberately side-effect free. It accepts one Workato-normalized
request, classifies domains using a pinned Public Suffix List snapshot, checks
the restricted candidate Surface/Core commercial mapping, and returns a draft Leonardo
Development manifest. It never authorizes or performs an external action.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


INTAKE_CONTRACT_VERSION = "surface-case3-intake-v2"
PREFLIGHT_CONTRACT_VERSION = "surface-case3-preflight-v2"
MAPPING_POLICY_VERSION = "surface-case3-partial-owner-mapping-2026-08-31-v5"
ENGINE_VALUE = "case_3_combined_baseline"
TARGET_ENVIRONMENT = "leonardo-development"
MAX_TEXT = 512
MAX_LIST_ITEMS = 1000
PSL_PATH = Path(__file__).with_name("data") / "public_suffix_list.dat"

_CO_PATTERN = re.compile(r"^CO-[0-9]+$")
_SF_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+$")
_SURFACE_PRODUCT = re.compile(r"^Pentera Surface Go - ([1-9][0-9]*) Subdomains$", re.I)
_CORE_PRODUCT = re.compile(r"^Pentera Core Plus Commercial - ([1-9][0-9]*) End Points$", re.I)
_ALLOWED_QUANTITY_SOURCES = {"dealhub_quantity", "owner_approved_product_tier"}
_ALLOWED_ADDONS = {
    "sva essentials": "credential_exposure_entitlement",
}

_COMPANY_TRANSLITERATION = str.maketrans(
    {
        "Æ": "AE", "æ": "ae", "Ø": "O", "ø": "o", "Ð": "D", "ð": "d",
        "Þ": "Th", "þ": "th", "Ł": "L", "ł": "l", "Œ": "OE", "œ": "oe",
        "ß": "ss", "’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    }
)


class IntakeError(ValueError):
    """Sanitized stable error; source values are never included."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _exact_object(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IntakeError(f"{path}:must_be_object")
    if set(value) != expected:
        raise IntakeError(f"{path}:schema_mismatch")
    return value


def _text(value: Any, code: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise IntakeError(code)
    value = value.strip()
    if (not value and not allow_empty) or len(value) > MAX_TEXT:
        raise IntakeError(code)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise IntakeError(code)
    return value


def _iso_timestamp(value: Any, code: str) -> str:
    text = _text(value, code)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise IntakeError(code) from error
    if parsed.tzinfo is None:
        raise IntakeError(code)
    return text


def _iso_date(value: Any, code: str) -> date:
    text = _text(value, code)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise IntakeError(code) from error
    if parsed.isoformat() != text:
        raise IntakeError(code)
    return parsed


def normalize_company_name(value: Any, *, combined_case: bool = True) -> str:
    """Return an English/ASCII-safe Leonardo account name."""
    source = _text(value, "account.company_name:invalid")
    source = source.translate(_COMPANY_TRANSLITERATION)
    source = unicodedata.normalize("NFKD", source)
    source = "".join(character for character in source if not unicodedata.combining(character))
    source = source.encode("ascii", "ignore").decode("ascii")
    source = re.sub(r"[^A-Za-z0-9 .,&'()/_-]+", " ", source)
    source = " ".join(source.split()).strip(" .,-_/\t")
    if combined_case:
        source = re.sub(r"\s*-\s*CE\s+Only$", "", source, flags=re.I).rstrip()
    if not source or len(source) > 128:
        raise IntakeError("account.company_name:normalization_failed")
    return source


def _idna_name(value: str, code: str) -> str:
    value = value.translate(str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."}))
    labels = value.rstrip(".").split(".")
    if len(labels) < 2 or any(not label for label in labels):
        raise IntakeError(code)
    encoded: list[str] = []
    try:
        for label in labels:
            ascii_label = label.encode("idna").decode("ascii").lower()
            if len(ascii_label) > 63 or not _DOMAIN_LABEL.fullmatch(ascii_label):
                raise IntakeError(code)
            encoded.append(ascii_label)
    except UnicodeError as error:
        raise IntakeError(code) from error
    result = ".".join(encoded)
    if len(result) > 253:
        raise IntakeError(code)
    return result


class PublicSuffixList:
    """Dependency-free PSL resolver supporting exact, wildcard, and exception rules."""

    def __init__(self, path: Path = PSL_PATH) -> None:
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise IntakeError("domain_policy:public_suffix_list_unavailable") from error
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.exact: set[str] = set()
        self.wildcards: set[str] = set()
        self.exceptions: set[str] = set()
        for raw_line in raw.decode("utf-8").splitlines():
            rule = raw_line.strip()
            if not rule or rule.startswith("//"):
                continue
            target = self.exact
            if rule.startswith("!"):
                target = self.exceptions
                rule = rule[1:]
            elif rule.startswith("*."):
                target = self.wildcards
                rule = rule[2:]
            try:
                normalized = ".".join(
                    label.encode("idna").decode("ascii").lower() for label in rule.split(".")
                )
            except UnicodeError as error:
                raise IntakeError("domain_policy:public_suffix_list_invalid") from error
            target.add(normalized)
        if not self.exact:
            raise IntakeError("domain_policy:public_suffix_list_invalid")

    def registrable_domain(self, domain: str) -> str:
        labels = domain.split(".")
        exception_lengths = [
            len(rule.split("."))
            for rule in self.exceptions
            if domain == rule or domain.endswith("." + rule)
        ]
        if exception_lengths:
            suffix_labels = max(exception_lengths) - 1
        else:
            exact_lengths = [
                len(rule.split("."))
                for rule in self.exact
                if domain == rule or domain.endswith("." + rule)
            ]
            wildcard_lengths = [
                len(rule.split(".")) + 1
                for rule in self.wildcards
                if len(labels) > len(rule.split("."))
                and ".".join(labels[-len(rule.split(".")):]) == rule
            ]
            suffix_labels = max([1, *exact_lengths, *wildcard_lengths])
        if len(labels) <= suffix_labels:
            raise IntakeError("domain:public_suffix_not_registrable")
        return ".".join(labels[-(suffix_labels + 1):])


@dataclass(frozen=True)
class DomainCandidate:
    value: str
    kind: str
    registrable: str | None


def _split_candidates(value: Any, code: str) -> list[str]:
    if isinstance(value, str):
        items = re.split(r"[,;\r\n]+", value)
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        raise IntakeError(code)
    result = [item.strip() for item in items if item.strip()]
    if len(result) > MAX_LIST_ITEMS:
        raise IntakeError(code)
    return result


def normalize_domain_candidate(value: Any, psl: PublicSuffixList) -> DomainCandidate:
    source = _text(value, "domain:invalid")
    if "@" in source or "*" in source or any(character.isspace() for character in source):
        raise IntakeError("domain:invalid")

    host = source
    if "://" in source:
        try:
            parsed = urlsplit(source)
            if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password:
                raise IntakeError("domain:url_invalid")
            host = parsed.hostname or ""
            _ = parsed.port
        except ValueError as error:
            raise IntakeError("domain:url_invalid") from error
    else:
        host = source.rstrip(".")

    try:
        if "/" in host:
            network = ipaddress.ip_network(host, strict=False)
            return DomainCandidate(str(network), "network", None)
        address = ipaddress.ip_address(host)
        return DomainCandidate(str(address), "network", None)
    except ValueError:
        pass

    if host.lower().startswith("www."):
        host = host[4:]
    normalized = _idna_name(host, "domain:invalid")
    registrable = psl.registrable_domain(normalized)
    kind = "root_domain" if normalized == registrable else "subdomain"
    return DomainCandidate(normalized, kind, registrable)


def normalize_domains(
    primary_value: Any,
    candidate_values: Any,
    ce_domain_values: Any,
    psl: PublicSuffixList,
) -> dict[str, Any]:
    primary = normalize_domain_candidate(primary_value, psl)
    if primary.kind != "root_domain":
        raise IntakeError("account.primary_domain:must_be_registrable_root")

    alternate: list[str] = []
    subdomains: list[str] = []
    networks: list[str] = []
    seen: set[tuple[str, str]] = set()
    for raw in _split_candidates(candidate_values, "account.domain_candidates:invalid"):
        candidate = normalize_domain_candidate(raw, psl)
        if candidate.kind == "root_domain" and candidate.value == primary.value:
            continue
        key = (candidate.kind, candidate.value)
        if key in seen:
            continue
        seen.add(key)
        if candidate.kind == "root_domain":
            alternate.append(candidate.value)
        elif candidate.kind == "subdomain":
            subdomains.append(candidate.value)
        else:
            networks.append(candidate.value)

    ce_domains: list[str] = []
    for raw in _split_candidates(ce_domain_values, "account.ce_email_domains:invalid"):
        if _EMAIL_PATTERN.fullmatch(raw):
            raise IntakeError("account.ce_email_domains:domain_required_not_email")
        candidate = normalize_domain_candidate(raw, psl)
        if candidate.kind != "root_domain":
            raise IntakeError("account.ce_email_domains:registrable_root_required")
        if candidate.value not in ce_domains:
            ce_domains.append(candidate.value)
    # Guru states that Core+ includes one Credential Exposure domain. Until an
    # additional-domain add-on taxonomy is authoritatively mapped and bound to
    # a selected DealHub record, fail closed on any other count.
    if len(ce_domains) != 1:
        raise IntakeError("account.ce_email_domains:exactly_one_entitled_without_addon")

    distinct_root_domains = {primary.value, *alternate, *ce_domains}

    return {
        "primary_domain": primary.value,
        "alternate_domains": sorted(alternate),
        "subdomains": sorted(subdomains),
        "email_domains": ce_domains,
        "networks": sorted(networks),
        "alternate_domains_csv": ", ".join(sorted(alternate)),
        "subdomains_csv": ", ".join(sorted(subdomains)),
        "distinct_root_domain_count": len(distinct_root_domains),
    }


def _subscription(value: Any, kind: str) -> dict[str, Any]:
    expected = {
        "record_id", "system_modstamp", "opportunity_id", "product_full_name", "status",
        "quantity", "quantity_source", "unit", "start_date", "end_date",
    }
    row = _exact_object(value, expected, f"entitlements.{kind}")
    record_id = _text(row["record_id"], f"entitlements.{kind}.record_id:invalid")
    if not _SF_ID_PATTERN.fullmatch(record_id):
        raise IntakeError(f"entitlements.{kind}.record_id:invalid")
    revision = _iso_timestamp(row["system_modstamp"], f"entitlements.{kind}.system_modstamp:invalid")
    opportunity_id = _text(row["opportunity_id"], f"entitlements.{kind}.opportunity_id:invalid")
    if not _SF_ID_PATTERN.fullmatch(opportunity_id):
        raise IntakeError(f"entitlements.{kind}.opportunity_id:invalid")
    product = _text(row["product_full_name"], f"entitlements.{kind}.product:invalid")
    if _text(row["status"], f"entitlements.{kind}.status:invalid").casefold() != "active":
        raise IntakeError(f"entitlements.{kind}.status:not_active")
    quantity = row["quantity"]
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise IntakeError(f"entitlements.{kind}.quantity:invalid")
    quantity_source = _text(row["quantity_source"], f"entitlements.{kind}.quantity_source:invalid")
    if quantity_source not in _ALLOWED_QUANTITY_SOURCES:
        raise IntakeError(f"entitlements.{kind}.quantity_source:unsupported")
    unit = _text(row["unit"], f"entitlements.{kind}.unit:invalid")
    pattern = _SURFACE_PRODUCT if kind == "surface" else _CORE_PRODUCT
    match = pattern.fullmatch(product)
    expected_unit = "Subdomains" if kind == "surface" else "End Points"
    if not match or unit != expected_unit:
        raise IntakeError(f"entitlements.{kind}.product_mapping:unsupported")
    if int(match.group(1)) != quantity:
        raise IntakeError(f"entitlements.{kind}.quantity:product_tier_mismatch")
    start = _iso_date(row["start_date"], f"entitlements.{kind}.start_date:invalid")
    end = _iso_date(row["end_date"], f"entitlements.{kind}.end_date:invalid")
    if end < start:
        raise IntakeError(f"entitlements.{kind}.term:invalid")
    return {
        "record_id": record_id,
        "system_modstamp": revision,
        "opportunity_id": opportunity_id,
        "product_full_name": product,
        "quantity": quantity,
        "quantity_source": quantity_source,
        "unit": unit,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


def _validate_addons(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 100:
        raise IntakeError("entitlements.add_ons:invalid")
    normalized: list[dict[str, str]] = []
    for item in value:
        row = _exact_object(item, {"record_id", "system_modstamp", "product_full_name", "status"}, "entitlements.add_on")
        record_id = _text(row["record_id"], "entitlements.add_on.record_id:invalid")
        if not _SF_ID_PATTERN.fullmatch(record_id):
            raise IntakeError("entitlements.add_on.record_id:invalid")
        revision = _iso_timestamp(row["system_modstamp"], "entitlements.add_on.system_modstamp:invalid")
        name = _text(row["product_full_name"], "entitlements.add_on.product:invalid")
        if _text(row["status"], "entitlements.add_on.status:invalid").casefold() != "active":
            raise IntakeError("entitlements.add_on.status:not_active")
        effect = _ALLOWED_ADDONS.get(name.casefold())
        if effect is None:
            raise IntakeError("entitlements.add_on.product_mapping:unsupported")
        normalized.append({"record_id": record_id, "system_modstamp": revision, "product_full_name": name, "effect": effect})
    return normalized


def _company_email_tag(company_name: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", normalize_company_name(company_name))
    if not tokens:
        raise IntakeError("primary_user.email:company_tag_invalid")
    tag = "".join(tokens).lower() if len(tokens) <= 2 else "".join(token[0] for token in tokens).lower()
    if not tag or len(tag) > 48:
        raise IntakeError("primary_user.email:company_tag_invalid")
    return tag


def build_primary_user_email(organization_email: Any, company_name: str) -> str:
    """Return the owner-approved customer-specific Pentera email alias."""
    email = _text(organization_email, "primary_user.email:invalid")
    if not _EMAIL_PATTERN.fullmatch(email):
        raise IntakeError("primary_user.email:invalid")
    local, domain = email.rsplit("@", 1)
    if domain.casefold() != "pentera.io":
        return email
    if "+" in local:
        raise IntakeError("primary_user.email:organization_email_required")
    result = f"{local.casefold()}+{_company_email_tag(company_name)}@pentera.io"
    if len(result) > MAX_TEXT:
        raise IntakeError("primary_user.email:invalid")
    return result


def _primary_user(value: Any, company_name: str) -> dict[str, Any]:
    expected = {"first_name", "last_name", "email", "phone", "job_title", "operator_account"}
    source = _exact_object(value, expected, "primary_user")
    raw_operator = source["operator_account"]
    operator = None
    if raw_operator is not None:
        operator = _text(raw_operator, "primary_user.operator_account:invalid", allow_empty=True) or None
    return {
        "first_name": _text(source["first_name"], "primary_user.first_name:invalid"),
        "last_name": _text(source["last_name"], "primary_user.last_name:invalid"),
        "email": build_primary_user_email(source["email"], company_name),
        "phone": _text(source["phone"], "primary_user.phone:invalid", allow_empty=True),
        "job_title": _text(source["job_title"], "primary_user.job_title:invalid", allow_empty=True),
        "mfa_enabled": True,
        "operator_account": operator,
    }


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def prepare_case3(payload: Any, *, psl: PublicSuffixList | None = None) -> dict[str, Any]:
    """Build a deterministic, unapproved Case 3 draft; always proceed=false."""
    root = _exact_object(
        payload,
        {"contract_version", "target_environment", "source", "routing", "account", "primary_user", "entitlements"},
        "request",
    )
    if root["contract_version"] != INTAKE_CONTRACT_VERSION:
        raise IntakeError("contract_version:unsupported")
    if root["target_environment"] != TARGET_ENVIRONMENT:
        raise IntakeError("target_environment:blocked")

    source = _exact_object(root["source"], {"co_number", "sf_record_id", "source_revision"}, "source")
    co_number = _text(source["co_number"], "source.co_number:invalid")
    sf_record_id = _text(source["sf_record_id"], "source.sf_record_id:invalid")
    if not _CO_PATTERN.fullmatch(co_number) or not _SF_ID_PATTERN.fullmatch(sf_record_id):
        raise IntakeError("source:identifier_invalid")
    source_revision = _iso_timestamp(source["source_revision"], "source.source_revision:invalid")

    routing = _exact_object(root["routing"], {"engine_value", "special_requirements"}, "routing")
    if routing["engine_value"] != ENGINE_VALUE:
        raise IntakeError("routing.engine_value:unsupported")
    if routing["special_requirements"] is not False:
        raise IntakeError("routing.special_requirements:separate_policy_required")

    account = _exact_object(
        root["account"],
        {"company_name", "primary_domain", "domain_candidates", "ce_email_domains", "country"},
        "account",
    )
    resolver = psl or PublicSuffixList()
    domains = normalize_domains(account["primary_domain"], account["domain_candidates"], account["ce_email_domains"], resolver)

    entitlements = _exact_object(root["entitlements"], {"surface", "core", "add_ons"}, "entitlements")
    surface = _subscription(entitlements["surface"], "surface")
    core = _subscription(entitlements["core"], "core")
    addons = _validate_addons(entitlements["add_ons"])
    if surface["opportunity_id"] != core["opportunity_id"]:
        raise IntakeError("entitlements:opportunity_mismatch")
    if (surface["start_date"], surface["end_date"]) != (core["start_date"], core["end_date"]):
        raise IntakeError("entitlements:term_mismatch")

    normalized_source = {"co_number": co_number, "sf_record_id": sf_record_id, "source_revision": source_revision}
    normalized_account = {
        "company_name": normalize_company_name(account["company_name"]),
        "account_type": "Customer",
        **{key: domains[key] for key in ("primary_domain", "alternate_domains", "subdomains", "email_domains", "networks")},
        "country": _text(account["country"], "account.country:invalid"),
    }
    user = _primary_user(root["primary_user"], normalized_account["company_name"])
    settings = {
        "scanning_interval": "Monthly",
        "scan_now": True,
        "maximum_scan_duration_hours": 90,
        "automated_discovery": False,
        "recon_subdomains": True,
        "multiple_attack_stacks": False,
        "mas_for_subdomains": False,
        "web_dictionary_brute_force": True,
        "web_dorking": False,
        "nuclei": True,
        "authenticated_testing": False,
        "static_outbound_ip": False,
        "ai": False,
        "notifications": True,
        "multiple_users": True,
        "api_access": True,
    }
    attack_modules = {
        "phishing": False,
        "leaked_credentials": True,
        "leaked_credentials_interval": "Weekly",
        "leaked_credentials_domains": domains["email_domains"],
    }
    license_data = {
        "include_provisioning": True,
        "include_subdomains": True,
        "type": "Prepaid annual subscription",
        # New Surface accounts use the owner-approved baseline, not the
        # unrelated Core endpoint quantity.  Web Agent and SpyCloud remain
        # absent from this contract so a runner cannot touch either control.
        "number_of_assets": 10_000,
        "number_of_domains": domains["distinct_root_domain_count"],
        "number_of_subdomains": surface["quantity"],
        "start_date": surface["start_date"],
        "expiration_date": surface["end_date"],
    }
    source_bindings = {
        "surface_record_id": surface["record_id"],
        "surface_system_modstamp": surface["system_modstamp"],
        "core_record_id": core["record_id"],
        "core_system_modstamp": core["system_modstamp"],
        "opportunity_id": surface["opportunity_id"],
        "add_ons": addons,
    }
    draft = {
        "source": normalized_source,
        "routing": {"engine_value": ENGINE_VALUE, "policy_version": MAPPING_POLICY_VERSION},
        "account": normalized_account,
        "primary_user": user,
        "settings": settings,
        "attack_modules": attack_modules,
        "license": license_data,
    }
    evidence_material = {"draft": draft, "source_bindings": source_bindings, "psl_sha256": resolver.sha256}
    return {
        "contract_version": PREFLIGHT_CONTRACT_VERSION,
        "target_environment": TARGET_ENVIRONMENT,
        "decision": "candidate_mapping_requires_owner_review",
        "proceed": False,
        "leonardo_request_allowed": False,
        "action_required": "hash_bound_owner_approval",
        "mapping_authority": "partial_owner_recorded_normalization_only",
        "execution_mapping_status": "unapproved",
        "unapproved_field_groups": [
            "account_enums_and_collision_rules",
            "settings",
            "attack_modules",
            "license_semantics",
            "addon_taxonomy",
            "duplicate_and_readback_rules",
        ],
        "mapping_policy_version": MAPPING_POLICY_VERSION,
        "public_suffix_list_sha256": resolver.sha256,
        "evidence_hash": _canonical_hash(evidence_material),
        "onboarding_comments": f"{surface['start_date']} - {surface['end_date']}",
        "alternate_domains_csv": domains["alternate_domains_csv"],
        "subdomains_csv": domains["subdomains_csv"],
        "source_bindings": source_bindings,
        "draft_manifest_sections": draft,
    }
