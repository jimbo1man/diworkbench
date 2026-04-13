"""
This is the single normalization boundary for all reviewer outputs.

Converts Streamlit-style (`issues`) and FastAPI-style (`findings`) payloads into
one canonical dict shape: `{"decision": str, "issues": [dict, ...]}` compatible
with RunRecord.review_result and DecisionEngine / ContractValidator consumers.
"""

from __future__ import annotations

from typing import Any

_VALID_DECISIONS = frozenset({"APPROVE", "REVISE", "BLOCKED", "PENDING", "UNKNOWN"})

_INVALID_PAYLOAD_RESPONSE: dict[str, Any] = {
    "decision": "BLOCKED",
    "issues": [
        {
            "id": "invalid_payload",
            "severity": "high",
            "scope": "forbidden",
            "message": "Invalid reviewer payload",
            "proposed_fix": "Ensure reviewer returns valid JSON",
        },
    ],
}


def _normalize_scope(raw: object) -> str:
    s = str(raw or "allowed").strip()
    upper = s.upper()
    if upper in ("ALLOWED", "FORBIDDEN"):
        return upper.lower()
    lower = s.lower()
    if lower in ("allowed", "forbidden"):
        return lower
    return "allowed"


def _normalize_severity(raw: object) -> str:
    return str(raw or "unknown").strip().lower() or "unknown"


def _normalize_issue_dict(raw: object, idx: int) -> dict[str, Any]:
    """Build one canonical issue dict from a Streamlit issue or FastAPI finding."""
    fallback_id = f"issue_{idx + 1:03d}"

    if not isinstance(raw, dict):
        return {
            "id": fallback_id,
            "type": "unknown",
            "severity": "unknown",
            "scope": "allowed",
            "target": "spec",
            "message": str(raw) if raw else "",
            "proposed_fix": "",
        }

    # Streamlit canonical + FastAPI finding aliases
    issue_id = raw.get("id") or raw.get("title") or fallback_id
    message = raw.get("message") if raw.get("message") is not None else raw.get("detail")
    if message is None:
        message = ""
    proposed = raw.get("proposed_fix")
    if proposed is None:
        proposed = raw.get("suggested_fix")
    if proposed is None:
        proposed = ""

    issue_type = raw.get("type")
    if issue_type is None or str(issue_type).strip() == "":
        issue_type = "unknown"
    else:
        issue_type = str(issue_type).strip().lower()

    target = raw.get("target") or raw.get("path") or "spec"
    target = str(target).strip() or "spec"

    return {
        "id": str(issue_id).strip() or fallback_id,
        "type": issue_type,
        "severity": _normalize_severity(raw.get("severity")),
        "scope": _normalize_scope(raw.get("scope")),
        "target": target,
        "message": str(message),
        "proposed_fix": str(proposed),
    }


def normalize_review(payload: dict) -> dict:
    """
    Convert any reviewer payload into canonical ``{"decision", "issues"}`` form.

    Accepts:
        - Streamlit-style: ``decision`` + ``issues``
        - FastAPI-style: ``decision`` + ``findings`` (mapped to ``issues``)

    Missing ``issues`` / ``findings`` defaults to an empty list. ``decision`` is
    uppercased; values outside APPROVE / REVISE / BLOCKED / PENDING / UNKNOWN
    are coerced to BLOCKED.

    If ``payload`` is not a ``dict`` (e.g. ``None``), returns a BLOCKED result
    with a single synthetic issue (see module invalid-payload contract).
    """
    if payload is None or not isinstance(payload, dict):
        return dict(_INVALID_PAYLOAD_RESPONSE)

    # Collect issue rows: prefer ``issues`` when present, else ``findings``
    raw_items: list[Any]
    if "issues" in payload:
        issues_val = payload.get("issues")
        if issues_val is None:
            raw_items = []
        elif not isinstance(issues_val, list):
            return dict(_INVALID_PAYLOAD_RESPONSE)
        else:
            raw_items = issues_val
    elif "findings" in payload:
        findings_val = payload.get("findings")
        if findings_val is None:
            raw_items = []
        elif not isinstance(findings_val, list):
            return dict(_INVALID_PAYLOAD_RESPONSE)
        else:
            raw_items = findings_val
    else:
        raw_items = []

    decision_raw = payload.get("decision")
    if decision_raw is None or (isinstance(decision_raw, str) and not decision_raw.strip()):
        decision = "PENDING"
    else:
        decision = str(decision_raw).upper().strip()

    if decision not in _VALID_DECISIONS:
        decision = "BLOCKED"

    issues_out = [_normalize_issue_dict(item, i) for i, item in enumerate(raw_items)]

    return {
        "decision": decision,
        "issues": issues_out,
    }
