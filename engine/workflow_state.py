"""
workflow_state.py
Manages the Workbench v2 state machine.
Valid states and transition logic for the governed pipeline.
"""

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class WorkflowState(str, Enum):
    DRAFT = "DRAFT"
    ARCHITECTED = "ARCHITECTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    REVIEW_REVISE_ALLOWED = "REVIEW_REVISE_ALLOWED"
    REVIEW_REVISE_FORBIDDEN = "REVIEW_REVISE_FORBIDDEN"
    APPROVED = "APPROVED"
    IMPLEMENTING = "IMPLEMENTING"
    BLOCKED = "BLOCKED"
    COMPLETE = "COMPLETE"


# Valid transitions: from_state -> set of allowed to_states
VALID_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.DRAFT: {WorkflowState.ARCHITECTED},
    WorkflowState.ARCHITECTED: {WorkflowState.UNDER_REVIEW, WorkflowState.ARCHITECTED},
    WorkflowState.UNDER_REVIEW: {
        WorkflowState.REVIEW_REVISE_ALLOWED,
        WorkflowState.REVIEW_REVISE_FORBIDDEN,
        WorkflowState.APPROVED,
        WorkflowState.BLOCKED,
    },
    WorkflowState.REVIEW_REVISE_ALLOWED: {
        WorkflowState.ARCHITECTED,  # auto-revision back to architect
        WorkflowState.BLOCKED,
    },
    WorkflowState.REVIEW_REVISE_FORBIDDEN: {
        WorkflowState.UNDER_REVIEW,  # reviewer resubmits within scope
        WorkflowState.BLOCKED,
    },
    WorkflowState.APPROVED: {WorkflowState.IMPLEMENTING},
    WorkflowState.IMPLEMENTING: {WorkflowState.COMPLETE, WorkflowState.BLOCKED},
    WorkflowState.BLOCKED: {WorkflowState.ARCHITECTED, WorkflowState.DRAFT},  # human reset
    WorkflowState.COMPLETE: set(),
}


@dataclass
class WorkflowRecord:
    artifact: str
    state: WorkflowState = WorkflowState.DRAFT
    revision_loops: int = 0
    max_loops: int = 2
    contract_frozen: bool = False
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    history: list[dict] = field(default_factory=list)

    def transition(self, new_state: WorkflowState, reason: str = "") -> None:
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {self.state} -> {new_state}. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        self.history.append({
            "from": self.state.value,
            "to": new_state.value,
            "reason": reason,
            "at": datetime.utcnow().isoformat(),
        })
        self.state = new_state
        self.updated_at = datetime.utcnow().isoformat()

    def increment_loop(self) -> bool:
        """Increment revision loop counter. Returns True if still within limit."""
        self.revision_loops += 1
        return self.revision_loops <= self.max_loops

    def loops_exhausted(self) -> bool:
        return self.revision_loops >= self.max_loops

    def to_dict(self) -> dict:
        return {
            "artifact": self.artifact,
            "state": self.state.value,
            "revision_loops": self.revision_loops,
            "max_loops": self.max_loops,
            "contract_frozen": self.contract_frozen,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "history": self.history,
        }
