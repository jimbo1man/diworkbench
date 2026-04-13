"""
run_insights.py — Rule-based insight generation for DI Workbench v3 Slice B

Fix 6 — Every insight statement is tied directly to a concrete signal:
  - Score values are referenced by their exact computed numbers.
  - Resolution results reference specific counts and issue ids.
  - Severity counts are referenced explicitly.
  - No generic or disconnected statements are produced.

Fix 3 — Clean separation: all derived inputs (score, resolutions, comparison)
are passed as explicit arguments. This function reads nothing from the record
except primary data (status, cost, engine review result, issues).
"""

from __future__ import annotations

from .run_record import (
    RunRecord,
    ScoreRecord,
    IssueResolutionRecord,
    ComparisonSummary,
    InsightRecord,
)
from .issue_resolution import resolution_counts


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

def compute_insights(
    record: RunRecord,
    score: ScoreRecord,
    resolutions: list[IssueResolutionRecord],
    comparison: ComparisonSummary | None,
) -> InsightRecord:
    """
    Derive InsightRecord from explicit inputs.
    Never reads record.score, record.issue_resolutions, or record.comparison_summary
    (those fields no longer exist on RunRecord).

    Parameters
    ----------
    record      : current RunRecord (primary data only)
    score       : ScoreRecord computed by run_scoring.compute_score(record)
    resolutions : list from issue_resolution.compute_resolutions(current, prior)
                  or [] when no prior run is selected
    comparison  : ComparisonSummary from run_comparison.compare_runs(...)
                  or None when no prior run is selected
    """
    issues = record.review_result.issues
    engine_result = _engine_review_result(record)

    return InsightRecord(
        approval_rationale    = _approval_rationale(record, score, engine_result, issues),
        blocking_concerns     = _blocking_concerns(engine_result, issues, score),
        improvement_summary   = _improvement_summary(comparison, resolutions, score),
        regression_summary    = _regression_summary(comparison, resolutions),
        cost_effectiveness_note = _cost_effectiveness(record, score, comparison),
    )


# ---------------------------------------------------------------------------
# Sub-generators — each tied to explicit signal values (Fix 6)
# ---------------------------------------------------------------------------

def _approval_rationale(
    record: RunRecord,
    score: ScoreRecord,
    engine_result: str,
    issues: list,
) -> str:
    high_count   = sum(1 for i in issues if i.severity == "high")
    medium_count = sum(1 for i in issues if i.severity == "medium")
    total        = len(issues)

    if record.status == "approved" or engine_result == "APPROVED":
        if total == 0:
            return (
                f"Approved with no reviewer issues. "
                f"Overall score: {score.overall_score:.1f}/100 "
                f"(completeness {score.completeness_score:.0f}, "
                f"governance {score.governance_score:.0f})."
            )
        if high_count == 0:
            return (
                f"Approved: 0 high-severity issues. "
                f"{medium_count} medium, {total - medium_count - high_count} low/unknown. "
                f"Overall score: {score.overall_score:.1f}/100."
            )
        return (
            f"Approved despite {high_count} high-severity issue(s) "
            f"({total} total). Overall score: {score.overall_score:.1f}/100. "
            "Governance review is recommended."
        )

    if engine_result == "PENDING":
        completeness_note = (
            f" Completeness score is {score.completeness_score:.0f}/100."
            if score.completeness_score < 75 else ""
        )
        return f"No review run yet.{completeness_note} Run a review to evaluate this spec."

    if engine_result in ("REVIEW_REVISE_ALLOWED", "REVIEW_REVISE_FORBIDDEN", "BLOCKED"):
        return (
            f"Decision: {engine_result}. "
            f"{total} issue(s) found — {high_count} high, {medium_count} medium. "
            f"Feasibility score: {score.feasibility_score:.0f}/100. "
            f"Clarity score: {score.clarity_score:.0f}/100. "
            f"Overall: {score.overall_score:.1f}/100."
        )

    return (
        f"Status: {record.status}. "
        f"Overall score: {score.overall_score:.1f}/100."
    )


def _blocking_concerns(engine_result: str, issues: list, score: ScoreRecord) -> list[str]:
    concerns: list[str] = []

    if engine_result in ("REVIEW_REVISE_ALLOWED", "REVIEW_REVISE_FORBIDDEN", "BLOCKED"):
        high_issues = [i for i in issues if i.severity == "high"]
        if high_issues:
            ids = ", ".join(f"'{i.id}'" for i in high_issues[:5])
            tail = "…" if len(high_issues) > 5 else "."
            concerns.append(
                f"{len(high_issues)} high-severity issue(s) blocking approval: "
                f"{ids}{tail} "
                f"Feasibility score: {score.feasibility_score:.0f}/100."
            )

        ambig_issues = [
            i for i in issues
            if (i.type or "").lower() in ("ambiguity", "missing_definition")
        ]
        if ambig_issues:
            ids = ", ".join(f"'{i.id}'" for i in ambig_issues[:5])
            tail = "…" if len(ambig_issues) > 5 else "."
            concerns.append(
                f"{len(ambig_issues)} ambiguity/missing-definition finding(s) "
                f"reducing clarity score to {score.clarity_score:.0f}/100: "
                f"{ids}{tail}"
            )

    if score.completeness_score < 50:
        concerns.append(
            f"Completeness score is {score.completeness_score:.0f}/100 — "
            "spec, contract, or review is likely missing."
        )

    if score.governance_score < 40:
        concerns.append(
            f"Governance score is {score.governance_score:.0f}/100 — "
            "ensure contract, review result, and patches are in place."
        )

    return concerns


def _improvement_summary(
    comparison: ComparisonSummary | None,
    resolutions: list[IssueResolutionRecord],
    score: ScoreRecord,
) -> list[str]:
    items: list[str] = []

    if comparison:
        # Pull directly from comparison's concrete deltas (Fix 6)
        items.extend(comparison.improved_items)

    counts = resolution_counts(resolutions)
    resolved_n = counts.get("resolved", 0)
    if resolved_n:
        resolved_ids = [
            r.issue_id for r in resolutions if r.status == "resolved"
        ][:5]
        ids_str = ", ".join(f"'{i}'" for i in resolved_ids)
        tail = "…" if len([r for r in resolutions if r.status == "resolved"]) > 5 else ""
        items.append(
            f"{resolved_n} issue(s) resolved since prior run: {ids_str}{tail}."
        )

    return items


def _regression_summary(
    comparison: ComparisonSummary | None,
    resolutions: list[IssueResolutionRecord],
) -> list[str]:
    items: list[str] = []

    if comparison:
        items.extend(comparison.worsened_items)

    counts = resolution_counts(resolutions)

    regressed_n = counts.get("regressed", 0)
    if regressed_n:
        reg_ids = [r.issue_id for r in resolutions if r.status == "regressed"][:5]
        ids_str = ", ".join(f"'{i}'" for i in reg_ids)
        items.append(
            f"{regressed_n} previously-patched issue type(s) reappeared: {ids_str}."
        )

    claimed_n = counts.get("claimed_addressed", 0)
    if claimed_n:
        cl_ids = [r.issue_id for r in resolutions if r.status == "claimed_addressed"][:5]
        ids_str = ", ".join(f"'{i}'" for i in cl_ids)
        items.append(
            f"{claimed_n} issue(s) patched but still present in current review: {ids_str}."
        )

    return items


def _cost_effectiveness(
    record: RunRecord,
    score: ScoreRecord,
    comparison: ComparisonSummary | None,
) -> str:
    reruns = record.cost.rerun_count
    cost   = record.cost.estimated_total_cost

    if reruns == 0:
        return "No reruns recorded yet. Run the architect or reviewer to track cost."

    cost_per_rerun = cost / reruns

    if comparison:
        delta   = comparison.score_delta
        r_delta = comparison.rerun_delta

        if delta <= 0 and r_delta > 0:
            return (
                f"{r_delta} rerun(s) added at ~${cost_per_rerun:.4f} each "
                f"with no score gain (delta: {delta:+.1f}). "
                "Consider revising the spec before rerunning."
            )
        if delta > 0:
            return (
                f"Score improved by {delta:.1f} points across "
                f"{reruns} total rerun(s) at ~${cost_per_rerun:.4f} each "
                f"(overall: {score.overall_score:.1f}/100). "
                "Iteration appears productive."
            )

    # No comparison available — use absolute values
    if cost > 0.0001:
        return (
            f"{reruns} rerun(s), estimated total cost ${cost:.4f} "
            f"(~${cost_per_rerun:.4f}/run). "
            f"Overall score: {score.overall_score:.1f}/100."
        )

    return (
        f"{reruns} rerun(s) completed. "
        "Cost estimate is near zero — token counts may not be fully populated."
    )
