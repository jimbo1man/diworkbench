"""
run_comparison.py — Deterministic run-to-run comparison for Slice B

Computes a ComparisonSummary between two RunRecords.
All logic is deterministic — no model calls.

Fix 3 — clean separation: scores are passed in as explicit arguments
rather than read from record.score (which is no longer a persisted field).

Compared dimensions:
  status        — did it change, and in which direction?
  overall score — computed externally and passed in
  issue count   — total and high-severity
  cost          — estimated_total_cost and rerun_count
"""

from __future__ import annotations

import difflib

from .run_record import RunRecord, ComparisonSummary, ScoreRecord


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compare_runs(
    current: RunRecord,
    prior: RunRecord,
    current_score: ScoreRecord,
    prior_score: ScoreRecord,
) -> ComparisonSummary:
    """
    Build a ComparisonSummary comparing current vs prior.
    Scores are passed in explicitly so this function has no dependency
    on persisted derived state.

    Populates improved_items and worsened_items with human-readable strings
    tied directly to concrete signal values.
    """
    improved: list[str] = []
    worsened: list[str] = []

    # --- Status ---
    status_changed = current.status != prior.status
    if status_changed:
        if current.status == "approved":
            improved.append(
                f"Status advanced to approved (was {prior.status})."
            )
        elif prior.status == "approved":
            worsened.append(
                f"Status regressed from approved to {current.status}."
            )
        else:
            # Neutral direction change — just note it
            improved.append(
                f"Status changed from {prior.status} to {current.status}."
            )

    # --- Score delta (Fix 6: concrete values always shown) ---
    score_delta = round(current_score.overall_score - prior_score.overall_score, 1)
    if score_delta > 0:
        improved.append(
            f"Overall score improved by {score_delta:.1f} points "
            f"({prior_score.overall_score:.1f} → {current_score.overall_score:.1f})."
        )
    elif score_delta < 0:
        worsened.append(
            f"Overall score dropped by {abs(score_delta):.1f} points "
            f"({prior_score.overall_score:.1f} → {current_score.overall_score:.1f})."
        )

    # --- Issue count delta ---
    prior_count   = len(prior.review_result.issues)
    current_count = len(current.review_result.issues)
    issue_delta   = current_count - prior_count

    if issue_delta < 0:
        improved.append(
            f"Issue count reduced by {abs(issue_delta)} "
            f"({prior_count} → {current_count})."
        )
    elif issue_delta > 0:
        worsened.append(
            f"Issue count increased by {issue_delta} "
            f"({prior_count} → {current_count})."
        )

    # --- High-severity delta ---
    prior_high   = sum(1 for i in prior.review_result.issues   if i.severity == "high")
    current_high = sum(1 for i in current.review_result.issues if i.severity == "high")
    high_delta   = current_high - prior_high

    if high_delta < 0:
        improved.append(
            f"High-severity issues reduced by {abs(high_delta)} "
            f"({prior_high} → {current_high})."
        )
    elif high_delta > 0:
        worsened.append(
            f"High-severity issues increased by {high_delta} "
            f"({prior_high} → {current_high})."
        )

    # --- Cost delta ---
    cost_delta = round(
        current.cost.estimated_total_cost - prior.cost.estimated_total_cost, 6
    )
    if cost_delta > 0:
        worsened.append(f"Estimated cost increased by ${cost_delta:.4f}.")
    elif cost_delta < 0:
        improved.append(f"Estimated cost decreased by ${abs(cost_delta):.4f}.")

    # --- Rerun delta ---
    rerun_delta = current.cost.rerun_count - prior.cost.rerun_count
    if rerun_delta > 0:
        worsened.append(f"{rerun_delta} additional rerun(s) since prior run.")

    return ComparisonSummary(
        prior_run_id=prior.run_id,
        current_run_id=current.run_id,
        status_changed=status_changed,
        prior_status=prior.status,
        current_status=current.status,
        prior_score=prior_score.overall_score,
        current_score=current_score.overall_score,
        score_delta=score_delta,
        issue_count_delta=issue_delta,
        high_severity_delta=high_delta,
        estimated_cost_delta=cost_delta,
        rerun_delta=rerun_delta,
        improved_items=improved,
        worsened_items=worsened,
    )


# ---------------------------------------------------------------------------
# Spec diff utility
# ---------------------------------------------------------------------------

def spec_diff_lines(prior_spec: str, current_spec: str) -> list[str]:
    """
    Return a unified-style diff of the two spec texts as a list of strings.
    Each line is prefixed with '+', '-', or ' '.
    Returns an empty list if specs are identical.
    """
    prior_lines   = prior_spec.splitlines(keepends=True)
    current_lines = current_spec.splitlines(keepends=True)
    return list(difflib.unified_diff(
        prior_lines,
        current_lines,
        fromfile="prior spec",
        tofile="current spec",
        lineterm="",
    ))
