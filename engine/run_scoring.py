"""
run_scoring.py — Deterministic quality scoring for DI Workbench v3 Slice B

All scoring is explicit, rule-based, and reproducible.
No model calls. No opaque magic numbers.

Fix 5 — Normalization contract:
  - Every subscore is clamped to [0.0, 100.0] before return.
  - Weights: completeness 25% + clarity 25% + feasibility 30% + governance 20% = 100%.
  - Overall is also clamped to [0.0, 100.0].
  - No subscore can go negative or exceed 100 regardless of input.

Score components:
  completeness_score — spec, contract, review, patches present where expected
  clarity_score      — penalized by ambiguity / missing_definition issues
  feasibility_score  — penalized by high/medium severity and risk-type issues
  governance_score   — additive: rewarded for contract, review, valid status, patches
"""

from __future__ import annotations

from .run_record import RunRecord, ScoreRecord

# Weights must sum to 1.0 exactly.
_W_COMPLETENESS = 0.25
_W_CLARITY      = 0.25
_W_FEASIBILITY  = 0.30
_W_GOVERNANCE   = 0.20
assert abs((_W_COMPLETENESS + _W_CLARITY + _W_FEASIBILITY + _W_GOVERNANCE) - 1.0) < 1e-9


def _clamp(value: float) -> float:
    """Clamp a float to [0.0, 100.0]."""
    return max(0.0, min(100.0, value))


def _engine_review_result(record: RunRecord) -> str:
    """
    Return the engine-authoritative workflow result stored in metadata.
    Older runs without metadata fall back to PENDING.
    """
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    return str(metadata.get("review_result") or "PENDING").upper()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_score(record: RunRecord) -> ScoreRecord:
    """
    Compute a ScoreRecord from the current RunRecord state.
    Deterministic and side-effect-free — does not mutate the record.
    All returned values are in [0.0, 100.0].
    """
    completeness = _clamp(_score_completeness(record))
    clarity      = _clamp(_score_clarity(record))
    feasibility  = _clamp(_score_feasibility(record))
    governance   = _clamp(_score_governance(record))

    overall = _clamp(
        completeness * _W_COMPLETENESS
        + clarity    * _W_CLARITY
        + feasibility * _W_FEASIBILITY
        + governance  * _W_GOVERNANCE
    )

    return ScoreRecord(
        completeness_score=round(completeness, 1),
        clarity_score=round(clarity, 1),
        feasibility_score=round(feasibility, 1),
        governance_score=round(governance, 1),
        overall_score=round(overall, 1),
    )


# ---------------------------------------------------------------------------
# Sub-scorers — each returns a raw float; _clamp() applied by caller
# ---------------------------------------------------------------------------

def _score_completeness(record: RunRecord) -> float:
    """
    Starts at 100. Deducts for missing required artifacts:
      -30  no architect_spec (empty or whitespace-only)
      -25  no locked_contract (empty dict)
      -25  review has never been run (engine result is PENDING)
      -20  status is revise with no revision patches recorded
    Maximum deduction: 100. Floor enforced by _clamp().
    """
    score = 100.0
    engine_result = _engine_review_result(record)

    if not record.architect_spec.strip():
        score -= 30.0

    if not record.locked_contract:
        score -= 25.0

    if engine_result == "PENDING":
        score -= 25.0

    if record.status == "revise" and not record.revision_patches:
        score -= 20.0

    return score


def _score_clarity(record: RunRecord) -> float:
    """
    Starts at 100. Penalizes ambiguity and missing_definition issues:
      -10 per ambiguity or missing_definition issue (total cap: -50)
      -5  per any other issue beyond the first two (total cap: -30)
    Bonus:
      +10 if engine result is APPROVED
    All contributions capped individually before subtraction.
    """
    score = 100.0
    issues = record.review_result.issues
    engine_result = _engine_review_result(record)

    clarity_deduction = 0.0
    other_deduction   = 0.0
    other_count       = 0

    for iss in issues:
        t = (iss.type or "").lower()
        if t in ("ambiguity", "missing_definition"):
            clarity_deduction += 10.0
        else:
            other_count += 1
            if other_count > 2:
                other_deduction += 5.0

    score -= min(clarity_deduction, 50.0)
    score -= min(other_deduction,   30.0)

    if engine_result == "APPROVED":
        score += 10.0

    return score


def _score_feasibility(record: RunRecord) -> float:
    """
    Starts at 100. Penalizes severity and risk-type issues:
      -20 per high-severity issue (total cap: -60)
      -8  per medium-severity issue (total cap: -24)
      -5  per implementation_risk or contract_violation regardless of severity (total cap: -20)
    Bonus:
      +15 if engine result is APPROVED
    """
    score = 100.0
    issues = record.review_result.issues
    engine_result = _engine_review_result(record)

    high_ded   = 0.0
    medium_ded = 0.0
    risk_ded   = 0.0

    for iss in issues:
        sev = (iss.severity or "").lower()
        t   = (iss.type or "").lower()

        if sev == "high":
            high_ded += 20.0
        elif sev == "medium":
            medium_ded += 8.0

        if t in ("implementation_risk", "contract_violation"):
            risk_ded += 5.0

    score -= min(high_ded,   60.0)
    score -= min(medium_ded, 24.0)
    score -= min(risk_ded,   20.0)

    if engine_result == "APPROVED":
        score += 15.0

    return score


def _score_governance(record: RunRecord) -> float:
    """
    Additive from 0. Rewarded for evidence of governance:
      +25  locked_contract is non-empty
      +25  review has been run (engine result is not PENDING)
      +20  status is past drafted (reviewed | revise | approved)
      +15  at least one revision patch recorded
      +15  status is approved (full governance cycle complete)
    Maximum possible: 100. Enforced by _clamp().
    """
    score = 0.0
    engine_result = _engine_review_result(record)

    if record.locked_contract:
        score += 25.0

    if engine_result != "PENDING":
        score += 25.0

    if record.status in ("reviewed", "revise", "approved"):
        score += 20.0

    if record.revision_patches:
        score += 15.0

    if record.status == "approved":
        score += 15.0

    return score
