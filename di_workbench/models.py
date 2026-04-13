
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


ReviewStatus = Literal["APPROVE", "REVISE", "UNKNOWN"]
DiffStatus = Literal["clean", "violation"]
NextAction = Literal["approve_and_execute", "architect_revise", "reject_changes"]


@dataclass
class ReviewIssue:
    severity: Literal["low", "medium", "high", "critical"]
    scope: Literal["ALLOWED", "FORBIDDEN"]
    issue_type: str
    message: str
    proposed_fix: str = ""


@dataclass
class ReviewResult:
    status: ReviewStatus = "UNKNOWN"
    summary: str = ""
    issues: List[ReviewIssue] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffIssue:
    severity: Literal["medium", "high"] = "medium"
    scope: Literal["ALLOWED", "FORBIDDEN"] = "FORBIDDEN"
    issue_type: str = "scope_violation"
    path: str = ""
    message: str = ""


@dataclass
class DiffResult:
    status: DiffStatus = "clean"
    summary: str = ""
    issues: List[DiffIssue] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionResult:
    action: NextAction
    reason: str
    can_execute: bool


@dataclass
class WorkbenchState:
    architect_spec: Dict[str, Any] = field(default_factory=dict)
    contract: Dict[str, Any] = field(default_factory=dict)
    review: Dict[str, Any] = field(default_factory=dict)
    diff: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "architect_spec": self.architect_spec,
            "contract": self.contract,
            "review": self.review,
            "diff": self.diff,
        }
