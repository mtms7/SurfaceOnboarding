"""Long account names (docs/60, rules approved by the owner 2026-10-21): a PROPOSAL for the tenant name, pure and without I/O.

Salesforce names of 40 characters or fewer are used unchanged. Longer names keep their first two words exactly; the last word plus
the legal form become capital initials ("Träger GmbH" -> "TG"); filler words in the middle are dropped; then middle words become
initials from the right, one at a time, until the name fits. A name that still does not fit gets no proposal (``cannot_shorten``):
the owner decides, nothing is guessed. The dashboard only shows the proposal; the owner confirms or edits the name before create.
Two accounts that shorten to the same name are caught by the duplicate check, not here.
"""

from __future__ import annotations

import re
from typing import Any

LIMIT = 40
KEEP = 2
FILLER = frozenset({"und", "für", "fur", "of", "the", "and", "de", "der", "die", "das", "zu", "zur", "von"})
LEGAL = frozenset({"gmbh", "ggmbh", "ag", "se", "kg", "ug", "ev", "ltd", "inc", "bv", "sa", "llc", "plc"})


def _legal(token: str) -> bool:
    return token.replace(".", "").casefold() in LEGAL


def _initial(word: str) -> str:
    letters = [char for char in word if char.isalpha()]
    return letters[0].upper() if letters else ""


def _joined(head: list[str], middle: list[str], tail: str) -> str:
    return " ".join([*head, *middle, tail])


def propose(name: Any) -> dict[str, Any]:
    """``{"proposed": str | None, "changed": bool, "steps": [...], "reason": ...}``. Raises ValueError for blank or odd input."""
    if not isinstance(name, str) or re.search(r"[\x00-\x1f\x7f]", name) or not " ".join(name.split()):
        raise ValueError("account_name_invalid")
    clean = " ".join(name.split())
    if len(clean) <= LIMIT:
        return {"proposed": clean, "changed": False, "steps": [], "reason": "short_enough"}
    tokens = clean.split()
    head, rest = tokens[:KEEP], tokens[KEEP:]
    refused = {"proposed": None, "changed": False, "steps": [], "reason": "cannot_shorten"}
    if not rest:
        return refused
    if _legal(rest[-1]):
        if len(rest) < 2:
            return refused
        tail = _initial(rest[-2]) + _initial(rest[-1])
        middle, steps = rest[:-2], ["last word and legal form -> " + tail]
    else:
        tail = _initial(rest[-1])
        middle, steps = rest[:-1], ["last word -> " + tail]
    if not tail or (len(tail) < 2 and _legal(rest[-1])):
        return refused
    if len(_joined(head, middle, tail)) <= LIMIT:
        return {"proposed": _joined(head, middle, tail), "changed": True, "steps": steps, "reason": "shortened"}
    kept = [word for word in middle if word.casefold() not in FILLER]
    if len(kept) != len(middle):
        steps.append("filler words dropped")
        middle = kept
    index = len(middle) - 1
    while len(_joined(head, middle, tail)) > LIMIT and index >= 0:
        initial = _initial(middle[index])
        if initial:
            middle[index] = initial
            steps.append("middle word %d -> %s" % (index + 1, initial))
        index -= 1
    if len(_joined(head, middle, tail)) > LIMIT:
        return refused
    return {"proposed": _joined(head, middle, tail), "changed": True, "steps": steps, "reason": "shortened"}
