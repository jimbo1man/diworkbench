from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ClassifiedChange:
    issue_id: str
    target: str
    message: str
    reason: str = ""
    severity: str = "info"
    proposed_fix: Optional[str] = None
    scope: str = "ALLOWED"  # ALLOWED | FORBIDDEN | BLOCKER


@dataclass
class DiffResult:
    artifact: str = "unknown"
    summary: str = ""
    overall_decision: str = "APPROVE"  # APPROVE | REVISE_ALLOWED | REVISE_FORBIDDEN | BLOCKED
    allowed: List[ClassifiedChange] = field(default_factory=list)
    forbidden: List[ClassifiedChange] = field(default_factory=list)
    blockers: List[ClassifiedChange] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        def _serialize(items: List[ClassifiedChange]) -> List[Dict[str, Any]]:
            return [
                {
                    "issue_id": c.issue_id,
                    "target": c.target,
                    "message": c.message,
                    "reason": c.reason,
                    "severity": c.severity,
                    "proposed_fix": c.proposed_fix,
                    "scope": c.scope,
                }
                for c in items
            ]

        return {
            "artifact": self.artifact,
            "summary": self.summary,
            "overall_decision": self.overall_decision,
            "allowed": _serialize(self.allowed),
            "forbidden": _serialize(self.forbidden),
            "blockers": _serialize(self.blockers),
        }


class DiffGuard:
    """
    Compatibility implementation for the DI Workbench engine.
    Produces the DiffResult shape expected by decision_engine.py.
    """

    def classify(self, review: dict, contract: dict) -> DiffResult:
        artifact = contract.get("artifact", "unknown")
        findings = review.get("issues", []) or review.get("findings", []) or []

        result = DiffResult(
            artifact=artifact,
            summary="No issues detected.",
            overall_decision="APPROVE",
        )

        forbidden_targets = set(contract.get("forbidden_changes", []) or [])
        locked_targets = set(contract.get("locked_fields", []) or [])

        for idx, item in enumerate(findings, start=1):
            target = str(item.get("target", item.get("path", "unknown")))

            if target in forbidden_targets or target in locked_targets:
                scope = "FORBIDDEN"
            else:
                scope = str(item.get("scope", "allowed")).upper()

            severity = str(item.get("severity", "info")).lower()

            change = ClassifiedChange(
                issue_id=str(item.get("issue_id", item.get("id", f"ISSUE-{idx}"))),
                target=target,
                message=str(item.get("message", item.get("summary", "No message provided."))),
                reason=str(item.get("reason", "")),
                severity=severity,
                proposed_fix=item.get("proposed_fix"),
                scope=scope,
            )

            if scope == "FORBIDDEN":
                result.forbidden.append(change)
            elif severity in {"blocker", "critical"} or scope == "BLOCKER":
                result.blockers.append(change)
            else:
                result.allowed.append(change)

        if result.forbidden:
            result.overall_decision = "REVISE_FORBIDDEN"
            result.summary = f"{len(result.forbidden)} forbidden change attempt(s) detected."
        elif result.blockers:
            result.overall_decision = "BLOCKED"
            result.summary = f"{len(result.blockers)} blocker issue(s) detected."
        elif result.allowed:
            result.overall_decision = "REVISE_ALLOWED"
            result.summary = f"{len(result.allowed)} allowed revision(s) detected."
        else:
            result.overall_decision = "APPROVE"
            result.summary = "No forbidden changes or blockers detected."

        return result