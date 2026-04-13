"""
run_record.py — Canonical data models for DI Workbench v3 (Slice A + Slice B)

RunRecord is the single source of truth for every workbench run.
It contains ONLY primary data:
    architect spec, implementation spec, execution result, contract, review
    result, implementation review, revision patches, cost, status, timestamps.

Slice B model classes (ScoreRecord, IssueResolutionRecord, ComparisonSummary,
InsightRecord) are defined here so the engine modules can return typed objects,
but they are NOT fields on RunRecord and are NEVER persisted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Slice A — Primary data models (persisted)
# ---------------------------------------------------------------------------

class ReviewIssue(BaseModel):
    id: str = "issue_001"
    severity: str = "unknown"
    type: str = "unknown"
    scope: str = "allowed"
    target: str = "spec"
    message: str = "(no message)"
    proposed_fix: str = "No proposed fix provided."


class ReviewResult(BaseModel):
    decision: str = "PENDING"
    issues: List[ReviewIssue] = Field(default_factory=list)


class ImplementationReviewFinding(BaseModel):
    id: str = "impl_001"
    severity: str = "medium"
    target: str = "implementation"
    message: str = "(no message)"
    required_fix: str = ""


class ImplementationReviewResult(BaseModel):
    decision: str = ""
    findings: List[ImplementationReviewFinding] = Field(default_factory=list)
    summary: str = ""


class RevisionPatch(BaseModel):
    issue_id: str
    status: str = "addressed"
    change_summary: str


class CostRecord(BaseModel):
    architect_input_tokens: int = 0
    architect_output_tokens: int = 0
    reviewer_input_tokens: int = 0
    reviewer_output_tokens: int = 0
    estimated_total_cost: float = 0.0
    rerun_count: int = 0

    def recalculate_cost(self) -> None:
        """
        Recompute estimated_total_cost from current token counts.
        Placeholder rates: $3/M input, $15/M output (Sonnet-class).
        """
        total_input  = self.architect_input_tokens + self.reviewer_input_tokens
        total_output = self.architect_output_tokens + self.reviewer_output_tokens
        self.estimated_total_cost = (
            total_input  * 3.0  / 1_000_000
            + total_output * 15.0 / 1_000_000
        )


class TimestampRecord(BaseModel):
    created_at: str = Field(default_factory=lambda: _now())
    updated_at: str = Field(default_factory=lambda: _now())


# ---------------------------------------------------------------------------
# Slice A — Root record (the only thing persisted to disk)
# ---------------------------------------------------------------------------

RunStatus = Literal["drafted", "reviewed", "revise", "approved"]

VALID_STATUSES: set[str] = {"drafted", "reviewed", "revise", "approved"}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "drafted":  {"reviewed"},
    "reviewed": {"drafted", "approved", "revise"},
    "revise":   {"drafted", "reviewed"},
    "approved": {"drafted", "reviewed"},
}


class RunRecord(BaseModel):
    """
    Primary persisted record. Contains only source data.
    Derived decision intelligence (scores, comparisons, insights, resolutions)
    is computed at runtime by the engine modules and never stored here.
    """
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    artifact_name: str = "untitled"
    status: RunStatus = "drafted"
    architect_spec: str = ""
    implementation_spec: str = ""
    execution_result: dict = Field(default_factory=dict)
    implementation_review: ImplementationReviewResult | None = None
    locked_contract: dict = Field(default_factory=dict)
    review_result: ReviewResult = Field(default_factory=ReviewResult)
    revision_patches: List[RevisionPatch] = Field(default_factory=list)
    cost: CostRecord = Field(default_factory=CostRecord)
    timestamps: TimestampRecord = Field(default_factory=TimestampRecord)
    metadata: dict = Field(default_factory=dict)
    last_action: str = "Idle"

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.timestamps.updated_at = _now()


# ---------------------------------------------------------------------------
# Slice B — Derived intelligence models (computed at runtime, never persisted)
# ---------------------------------------------------------------------------

class ScoreRecord(BaseModel):
    """
    Deterministic quality scores for a run.
    Weights: completeness 25% + clarity 25% + feasibility 30% + governance 20% = 100%.
    All subscores are bounded [0, 100].
    """
    completeness_score: float = 0.0
    clarity_score: float = 0.0
    feasibility_score: float = 0.0
    governance_score: float = 0.0
    overall_score: float = 0.0


IssueResolutionStatus = Literal[
    "unaddressed", "claimed_addressed", "resolved", "regressed"
]


class IssueResolutionRecord(BaseModel):
    """
    Tracks how a single reviewer issue was handled between runs.
    Matched by issue_id first; falls back to type+target for unlabelled issues.
    """
    issue_id: str
    match_method: str = "id"          # "id" | "type_target" | "new"
    status: IssueResolutionStatus = "unaddressed"
    prior_run_id: Optional[str] = None
    current_run_id: str
    notes: str = ""


class ComparisonSummary(BaseModel):
    """Delta summary between current and a chosen prior run."""
    prior_run_id: Optional[str] = None
    current_run_id: str
    status_changed: bool = False
    prior_status: Optional[str] = None
    current_status: str = ""
    prior_score: float = 0.0
    current_score: float = 0.0
    score_delta: float = 0.0
    issue_count_delta: int = 0
    high_severity_delta: int = 0
    estimated_cost_delta: float = 0.0
    rerun_delta: int = 0
    improved_items: List[str] = Field(default_factory=list)
    worsened_items: List[str] = Field(default_factory=list)


class InsightRecord(BaseModel):
    """Rule-based concise insights tied directly to score, resolutions, severity."""
    approval_rationale: str = ""
    blocking_concerns: List[str] = Field(default_factory=list)
    improvement_summary: List[str] = Field(default_factory=list)
    regression_summary: List[str] = Field(default_factory=list)
    cost_effectiveness_note: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run(artifact_name: str, locked_contract: dict) -> RunRecord:
    """Create a fresh RunRecord for a new workbench run."""
    return RunRecord(
        artifact_name=artifact_name,
        locked_contract=locked_contract,
    )
