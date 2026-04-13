
from __future__ import annotations

from typing import Any, Dict


def decide_next_action(review: Dict[str, Any], diff: Dict[str, Any]) -> Dict[str, Any]:
    review_status = str(review.get("status", "UNKNOWN")).upper()
    diff_status = str(diff.get("status", "clean")).lower()

    if review_status == "REVISE":
        return {
            "action": "architect_revise",
            "reason": "Reviewer requested revision.",
            "can_execute": False,
        }

    if diff_status == "violation":
        return {
            "action": "reject_changes",
            "reason": "DiffGuard found forbidden modifications.",
            "can_execute": False,
        }

    return {
        "action": "approve_and_execute",
        "reason": "Review approved and DiffGuard is clean.",
        "can_execute": True,
    }
