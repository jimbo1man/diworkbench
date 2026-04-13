"""
workflow_transitions.py — Strict state machine for DI Workbench v3 Slice A

Allowed statuses: drafted | reviewed | revise | approved

ALL status mutations must go through apply_transition().
No direct assignment to record.status is permitted outside this module.

This module only enforces legal RunRecord transitions.
It does not decide workflow outcomes. Decision authority lives in
``engine.decision_engine``; callers must obtain the desired target status from
engine policy first, then route the mutation through ``apply_transition()``.

Transition table (Fix 3 — explicit, no silent fallback):
    drafted  → reviewed
    reviewed → drafted | approved | revise
    revise   → drafted | reviewed
    approved → drafted | reviewed

"drafted" is always reachable (spec/contract edits reset to it from any state).
"reviewed" is always reachable (a review run always lands here first).
"""

from __future__ import annotations

import logging

from .run_record import RunRecord, VALID_STATUSES, ALLOWED_TRANSITIONS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core transition gate — every status change passes through here
# ---------------------------------------------------------------------------

def apply_transition(
    record: RunRecord,
    new_status: str,
    *,
    force: bool = False,
) -> tuple[RunRecord, bool, str]:
    """
    The single entry point for all status mutations.

    Returns:
        (updated_record, success, message)

    Fix 3 — explicit guards:
    - Rejects statuses not in VALID_STATUSES (always).
    - Rejects transitions not in ALLOWED_TRANSITIONS unless force=True.
      force=True is used only for the two unconditional resets
      (spec edited → drafted, contract edited → drafted) which must
      succeed from any state.
    - On rejection the record is returned unchanged with success=False
      and a descriptive message. No silent fallback.
    """
    new_status = new_status.lower().strip()

    # Guard 1: target status must be valid
    if new_status not in VALID_STATUSES:
        msg = (
            f"Transition rejected: '{new_status}' is not a valid status. "
            f"Allowed: {sorted(VALID_STATUSES)}"
        )
        logger.warning(msg)
        return record, False, msg

    old_status = record.status

    # Guard 2: transition must be in the allowed table (unless forced)
    if not force:
        allowed_targets = ALLOWED_TRANSITIONS.get(old_status, set())
        if new_status not in allowed_targets:
            msg = (
                f"Transition rejected: {old_status!r} → {new_status!r} is not allowed. "
                f"From '{old_status}' the valid targets are: {sorted(allowed_targets)}"
            )
            logger.warning(msg)
            return record, False, msg

    record.status = new_status  # type: ignore[assignment]
    record.touch()
    msg = f"Status: {old_status} → {new_status}"
    logger.debug(msg)
    return record, True, msg


# ---------------------------------------------------------------------------
# Named transition helpers — thin, named wrappers around apply_transition
# ---------------------------------------------------------------------------

def mark_spec_edited(record: RunRecord) -> RunRecord:
    """
    Spec content was changed by the user.
    Resets to drafted from any state (force=True — this is always valid).
    """
    record, _, _ = apply_transition(record, "drafted", force=True)
    return record


def mark_contract_edited(record: RunRecord) -> RunRecord:
    """
    Locked contract was changed by the user.
    Contract edits are governance-significant — resets to drafted from any
    post-draft state (force=True). No-op if already drafted.
    """
    if record.status != "drafted":
        record, _, _ = apply_transition(record, "drafted", force=True)
    return record


def mark_review_complete(record: RunRecord) -> RunRecord:
    """
    A review execution just finished.
    Always transitions to ``reviewed`` first, regardless of prior state.
    Uses force=True because a review run is always a valid event regardless
    of current state (e.g. re-reviewing an approved spec is legitimate).

    Callers that need a final post-review status must obtain that target from
    the engine decision layer and then call ``apply_transition()`` explicitly.
    """
    record, _, _ = apply_transition(record, "reviewed", force=True)
    return record
