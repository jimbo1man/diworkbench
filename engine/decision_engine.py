"""
decision_engine.py
Ingests architect contract + reviewer JSON, runs diff guard,
determines next workflow state, and drives auto-revision routing.

AUTHORITY: Final decision is derived entirely from DiffResult.
Reviewer-provided 'decision' field is never used to determine system state.
"""

from dataclasses import dataclass
from typing import Optional
from .workflow_state import WorkflowState, WorkflowRecord
from .diff_guard import DiffGuard, DiffResult
from .contract_validator import ContractValidator


@dataclass
class EngineDecision:
    next_state: WorkflowState
    diff_result: DiffResult
    action: str
    rationale: str
    auto_route: bool
    blocker_summary: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "next_state": self.next_state.value,
            "action": self.action,
            "rationale": self.rationale,
            "auto_route": self.auto_route,
            "blocker_summary": self.blocker_summary,
            "diff_result": self.diff_result.to_dict(),
        }


class DecisionEngine:
    """
    The workflow controller. Given the current record, contract, and
    reviewer JSON, it:
      1. Validates the review JSON schema (strict — rejects before diff guard)
      2. Validates the contract is well-formed
      3. Runs the diff guard to classify all changes
      4. Determines the correct next WorkflowState from diff counts only
      5. Returns an EngineDecision the API layer can act on

    The reviewer-provided 'decision' field is explicitly ignored when
    determining system state. All routing is driven by DiffResult.
    """

    def __init__(self):
        self.diff_guard = DiffGuard()
        self.validator = ContractValidator()

    def process_review(
        self,
        record: WorkflowRecord,
        contract: dict,
        review: dict,
    ) -> EngineDecision:
        artifact = contract.get("artifact", "unknown")

        # Step 1: strict reviewer JSON schema validation — reject before diff guard
        schema_check = self.validator.validate_review_schema(review)
        if not schema_check.valid:
            return EngineDecision(
                next_state=WorkflowState.BLOCKED,
                diff_result=DiffResult(artifact=artifact),
                action="REVIEW_SCHEMA_INVALID",
                rationale=f"Reviewer output failed schema validation: {schema_check.errors}",
                auto_route=False,
                blocker_summary=(
                    "=== REVIEW REJECTED — SCHEMA VALIDATION FAILED ===\n"
                    + "\n".join(f"  • {e}" for e in schema_check.errors)
                    + "\n\nReviewer must resubmit with a valid structured JSON response."
                ),
            )

        # Step 2: validate contract is well-formed
        contract_check = self.validator.validate_contract_schema(contract)
        if not contract_check.valid:
            return EngineDecision(
                next_state=WorkflowState.BLOCKED,
                diff_result=DiffResult(artifact=artifact),
                action="CONTRACT_INVALID",
                rationale=f"Contract failed validation: {contract_check.errors}",
                auto_route=False,
                blocker_summary=(
                    "Contract file is malformed. Correct the contract before proceeding.\n"
                    + "\n".join(contract_check.errors)
                ),
            )

        # Step 3: run diff guard (reviewer 'decision' field is not passed here)
        diff = self.diff_guard.classify(review, contract)

        # Step 4: determine next state from diff counts only
        return self._decide(record, diff)

    def process_revision(
        self,
        previous_spec: str,
        revised_spec: str,
        contract: dict,
        record: WorkflowRecord,
    ) -> Optional["EngineDecision"]:
        """
        Post-revision contract enforcement.
        Compares previous spec against revised spec and blocks if locked
        fields were removed, renamed, or the output was significantly weakened.
        Returns None if revision is clean, or an EngineDecision(BLOCKED) if violated.
        """
        revision_check = self.validator.validate_spec_revision(
            previous_spec, revised_spec, contract
        )
        if not revision_check.valid:
            artifact = contract.get("artifact", "unknown")
            return EngineDecision(
                next_state=WorkflowState.BLOCKED,
                diff_result=DiffResult(artifact=artifact),
                action="REVISION_CONTRACT_VIOLATION",
                rationale=f"Revised spec violated locked contract: {revision_check.errors}",
                auto_route=False,
                blocker_summary=(
                    "=== REVISION REJECTED — CONTRACT VIOLATION ===\n"
                    + "\n".join(f"  • {e}" for e in revision_check.errors)
                    + "\n\nThe auto-revision introduced changes that conflict with the locked contract. "
                    "Human review required before proceeding."
                ),
            )
        return None  # revision is clean

    def _decide(self, record: WorkflowRecord, diff: DiffResult) -> EngineDecision:
        """
        Determines next state strictly from DiffResult counts.
        Priority order: forbidden > blockers > allowed > approve.
        Reviewer decision field is never consulted.
        """
        overall = diff.overall_decision

        # ── FORBIDDEN CHANGES — highest priority ──────────────────────────────
        if overall == "REVISE_FORBIDDEN":
            return EngineDecision(
                next_state=WorkflowState.REVIEW_REVISE_FORBIDDEN,
                diff_result=diff,
                action="REJECT_REVIEWER_OVERREACH",
                rationale=(
                    f"{len(diff.forbidden)} forbidden change attempt(s) automatically rejected. "
                    f"Reviewer must resubmit within allowed scope only."
                ),
                auto_route=False,
                blocker_summary=self._format_forbidden_summary(diff),
            )

        # ── BLOCKERS ──────────────────────────────────────────────────────────
        if overall == "BLOCKED":
            return EngineDecision(
                next_state=WorkflowState.BLOCKED,
                diff_result=diff,
                action="ESCALATE_BLOCKER",
                rationale=f"Unresolvable blockers detected: {diff.summary}",
                auto_route=False,
                blocker_summary=self._format_blocker_summary(diff),
            )

        # ── ALLOWED REVISIONS — within loop limit ─────────────────────────────
        if overall == "REVISE_ALLOWED" and not record.loops_exhausted():
            return EngineDecision(
                next_state=WorkflowState.REVIEW_REVISE_ALLOWED,
                diff_result=diff,
                action="AUTO_ROUTE_TO_ARCHITECT",
                rationale=(
                    f"{len(diff.allowed)} bounded allowed revision(s) detected. "
                    f"Auto-routing to Architect "
                    f"(loop {record.revision_loops + 1}/{record.max_loops})."
                ),
                auto_route=True,
            )

        # ── ALLOWED REVISIONS — loops exhausted ──────────────────────────────
        if overall == "REVISE_ALLOWED" and record.loops_exhausted():
            return EngineDecision(
                next_state=WorkflowState.BLOCKED,
                diff_result=diff,
                action="ESCALATE_MAX_LOOPS",
                rationale=(
                    f"Max revision loops ({record.max_loops}) reached without convergence. "
                    f"Surfacing unresolved issues for human arbitration."
                ),
                auto_route=False,
                blocker_summary=self._format_blocker_summary(diff),
            )

        # ── APPROVE ───────────────────────────────────────────────────────────
        return EngineDecision(
            next_state=WorkflowState.APPROVED,
            diff_result=diff,
            action="APPROVE_SPEC",
            rationale=(
                "No forbidden changes. No unresolved blockers. "
                "Spec is approved for implementation handoff."
            ),
            auto_route=True,
        )

    def _format_forbidden_summary(self, diff: DiffResult) -> str:
        lines = ["=== FORBIDDEN CHANGE ATTEMPTS — REJECTED ==="]
        for c in diff.forbidden:
            lines.append(f"  [{c.issue_id}] {c.target}: {c.message}")
            lines.append(f"    Reason: {c.reason}")
        lines.append("")
        lines.append("Reviewer must resubmit with only allowed-scope revisions.")
        return "\n".join(lines)

    def _format_blocker_summary(self, diff: DiffResult) -> str:
        lines = ["=== BLOCKER SUMMARY — HUMAN ESCALATION REQUIRED ==="]
        all_issues = diff.blockers + diff.allowed
        for c in all_issues:
            lines.append(f"  [{c.issue_id}] [{c.severity.upper()}] {c.target}")
            lines.append(f"    {c.message}")
            if c.proposed_fix:
                lines.append(f"    Proposed: {c.proposed_fix}")
        lines.append("")
        lines.append(
            "These items require a business or architectural decision "
            "before implementation can proceed."
        )
        return "\n".join(lines)
