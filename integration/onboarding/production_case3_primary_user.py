"""Case 3 primary user (owner request + HAR 2026-11-06, CO-0841): the customer's Admin user in the new tenant, then the
Salesforce stage User Created. Pure: plans, request matching, reply reading and texts; no I/O.

Learned from the owner's manual create (HAR 2026-11-06 21:01-21:02Z; only method, path, body field names and the role kept):

1. ``GET /api/v1/backoffice/account/control/<24-hex account id>`` (BackOffice row menu: take control of the tenant);
2. the tenant app; ``POST /api/v1/administration/users`` (the Users table, body ``{"tableServerData": ...}``), page
   ``/administration/users``;
3. ``POST /api/v1/administration/users/add`` with exactly ``{"firstName", "lastName", "email", "roleNames":
   ["USER_ACCOUNT_ADMIN"]}``; the reply echoes them; the Users table then lists the new user (state ``Created``,
   ``primaryUser`` false; the attended ``milton.stevenson+<alias>`` user stays the primary);
4. ``POST /api/v1/backoffice/account/giveUpControl`` with ``{}`` (back to BackOffice).

The person comes from the CO's Salesforce ``Primary_User__c`` Contact (first name, last name, email); the email must be
on one of the CO's own domains. The add request is let through ONLY when its body equals the plan exactly (a wrong role
or person is aborted by the guard, so the page can never create something else).
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

STEP = "primary_user"
ROLE = "USER_ACCOUNT_ADMIN"
CONTROL_PATH = re.compile(r"/api/v1/backoffice/account/control/([0-9a-f]{24})")
USERS_PATH = "/api/v1/administration/users"
ADD_PATH = "/api/v1/administration/users/add"
GIVE_UP_PATH = "/api/v1/backoffice/account/giveUpControl"
USERS_PAGE = "/administration/users"
STAGE_FROM, STAGE_TO = "Scan Completed Successfully", "User Created"
CASE3_PRODUCT, CASE3_TYPE = "Surface & Credential Exposure", "New Product Onboarding"
SOQL_FIELDS = ("Id, Name, LastModifiedDate, Onboarding_Product__c, Onboarding_Type__c, Onboarding_Stage__c, Main_Domain__c, "
               "Email_Domains__c, Primary_User__c, Primary_User__r.FirstName, Primary_User__r.LastName, Primary_User__r.Email")
_NAME = re.compile(r"[^\W\d_](?:[^\W\d_]|[ '’.-]){0,59}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+)")
_DOMAIN = re.compile(r"[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+")

# Results (a fixed word each; recorded in case3-followup-writes.jsonl as step "primary_user")
CREATED = "user_created_verified"
ALREADY = "user_already_present"
BY_HAND = "owner_confirmed_by_hand"
DONE_RESULTS = frozenset({CREATED, ALREADY, BY_HAND})
RESULT_TEXT = {
    CREATED: "The primary user was created as Admin in the tenant and read back from its Users list.",
    ALREADY: "The primary user already exists as Admin in the tenant (nothing was added).",
    BY_HAND: "The owner confirmed the primary user was created by hand in BackOffice.",
    "user_created_unverified": "The add request was accepted but the Users list did not show the user afterwards: check the "
                               "tenant's Users in BackOffice. It is not repeated.",
    "user_exists_other_role": "A user with this email exists in the tenant without the Admin role: change it by hand.",
    "add_rejected": "BackOffice refused the add request; nothing was created.",
    "add_body_mismatch": "The page tried to send a different user or role; the guard stopped it, nothing was created.",
    "add_no_signal": "No add request was seen after the click; nothing was created as far as the page shows. Check the tenant.",
    "add_form_unavailable": "The Add user form could not be filled; nothing was sent.",
    "control_item_not_found": "The tenant's row menu has no single take-control item; nothing was done.",
    "control_failed": "Taking control of the tenant failed; nothing was done.",
    "users_list_unavailable": "The tenant's Users list could not be read; nothing was added.",
    "row_not_found": "The verified tenant row was not found in BackOffice; nothing was done.",
    "session_expired": "The BackOffice session expired; nothing was done.",
}
CONTROL_NOT_RELEASED = "control_not_released"
RELEASE_TEXT = ("BackOffice is still in control of this tenant: in the app.pentera.io window, use Give up control before "
                "anything else.")


@dataclass(frozen=True)
class PrimaryUser:
    first_name: str
    last_name: str
    email: str = field(repr=False)  # never printed or recorded

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[1].lower()


def _domains(row: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in ("Main_Domain__c", "Email_Domains__c"):
        value = row.get(key)
        if isinstance(value, str):
            for part in re.split(r"[\s,;]+", value.strip().lower()):
                part = part.strip().lstrip("@")
                if _DOMAIN.fullmatch(part):
                    found.add(part)
    return found


def plan(row: Any) -> tuple[PrimaryUser | None, str]:
    """(the person, "") or (None, refusal code) from the fixed CO read (``SOQL_FIELDS``)."""
    if not isinstance(row, Mapping) or not isinstance(row.get("Id"), str):
        return None, "co_unreadable"
    if row.get("Onboarding_Product__c") != CASE3_PRODUCT or row.get("Onboarding_Type__c") != CASE3_TYPE:
        return None, "route_not_case3_new"
    contact = row.get("Primary_User__r")
    if not isinstance(contact, Mapping) or not row.get("Primary_User__c"):
        return None, "primary_user_missing"
    first, last, email = (contact.get(k) for k in ("FirstName", "LastName", "Email"))
    first = first.strip() if isinstance(first, str) else ""
    last = last.strip() if isinstance(last, str) else ""
    email = email.strip() if isinstance(email, str) else ""
    if not _NAME.fullmatch(first) or not _NAME.fullmatch(last):
        return None, "primary_user_name_invalid"
    match = _EMAIL.fullmatch(email)
    if match is None:
        return None, "primary_user_email_invalid"
    if match.group(1).lower() not in _domains(row):
        return None, "primary_user_domain_not_on_co"
    if email.lower().endswith("@pentera.io"):
        return None, "primary_user_is_internal"
    return PrimaryUser(first, last, email), ""


def stage_problem(row: Any) -> str | None:
    """None when the CO may get its primary user now (scan finished, user not yet recorded in Salesforce)."""
    stage = row.get("Onboarding_Stage__c") if isinstance(row, Mapping) else None
    if stage == STAGE_FROM:
        return None
    if stage == STAGE_TO:
        return None  # User Created already: the run only checks / adds the user, the stage stays
    if stage == "Account Scanning":
        return "scan_not_marked_finished"
    return "stage_not_expected"


def expected_body(user: PrimaryUser) -> dict[str, Any]:
    return {"firstName": user.first_name, "lastName": user.last_name, "email": user.email, "roleNames": [ROLE]}


def body_matches(post_data: Any, user: PrimaryUser) -> bool:
    """True only for exactly the planned add body (same keys; names exact; email case-insensitive; Admin role only)."""
    try:
        body = json.loads(post_data) if isinstance(post_data, (str, bytes)) else post_data
    except (ValueError, TypeError):
        return False
    if not isinstance(body, Mapping) or set(body) != {"firstName", "lastName", "email", "roleNames"}:
        return False
    return (body.get("firstName") == user.first_name and body.get("lastName") == user.last_name
            and isinstance(body.get("email"), str) and body["email"].strip().lower() == user.email.lower()
            and body.get("roleNames") == [ROLE])


def users_list_body_ok(post_data: Any) -> bool:
    """The Users table read: a body with only ``tableServerData`` (no write-ish key)."""
    try:
        body = json.loads(post_data) if isinstance(post_data, (str, bytes)) else post_data
    except (ValueError, TypeError):
        return False
    return isinstance(body, Mapping) and set(body) == {"tableServerData"} and isinstance(body["tableServerData"], Mapping)


def user_state(reply: Any, user: PrimaryUser) -> str | None:
    """From the Users table reply: "present_admin" | "present_other_role" | "absent", or None when unreadable."""
    paged = reply.get("pagination_response") if isinstance(reply, Mapping) else None
    rows = paged.get("table_data") if isinstance(paged, Mapping) else None
    total = paged.get("total_count") if isinstance(paged, Mapping) else None
    if not isinstance(rows, list):
        return None
    if type(total) is int and total > len(rows):
        return None  # more users than one page: the list is not complete (fail closed)
    mine = [r for r in rows if isinstance(r, Mapping) and isinstance(r.get("email"), str)
            and r["email"].strip().lower() == user.email.lower()]
    if not mine:
        return "absent"
    roles = mine[0].get("roleNames")
    return "present_admin" if isinstance(roles, list) and ROLE in roles else "present_other_role"


def control_id(method: str, path: str) -> str | None:
    match = CONTROL_PATH.fullmatch(path or "") if (method or "").upper() == "GET" else None
    return match.group(1) if match else None


def text(result: str | None) -> str:
    if not result:
        return ""
    base, _, rest = str(result).partition("+")
    out = RESULT_TEXT.get(base, "Primary user step: " + base.replace("_", " ") + ".")
    return out + (" " + RELEASE_TEXT if rest == CONTROL_NOT_RELEASED else "")
