"""
status_adapter.py

Maps the workflow-state outcome produced by DecisionEngine onto the persisted
RunRecord status model used by the Streamlit runtime.
"""

from dataclasses import dataclass


@dataclass
class StatusResolution:
    run_status: str
    last_action: str
    review_result: str
    can_retry: bool
    terminal: bool
    summary: str


def map_engine_decision_to_run_status(workflow_state: str) -> StatusResolution:
    """
    Convert an EngineDecision workflow-state value into a RunRecord status and
    related UI/runtime metadata.
    """
    if workflow_state == "APPROVED":
        return StatusResolution(
            run_status="approved",
            last_action="approved",
            review_result="APPROVED",
            can_retry=False,
            terminal=True,
            summary="Review passed and artifact is approved.",
        )

    if workflow_state == "REVIEW_REVISE_ALLOWED":
        return StatusResolution(
            run_status="revise",
            last_action="revise_allowed",
            review_result="REVIEW_REVISE_ALLOWED",
            can_retry=True,
            terminal=False,
            summary="Review found allowed issues; revision may proceed.",
        )

    if workflow_state == "REVIEW_REVISE_FORBIDDEN":
        return StatusResolution(
            run_status="reviewed",
            last_action="revise_forbidden",
            review_result="REVIEW_REVISE_FORBIDDEN",
            can_retry=False,
            terminal=True,
            summary="Review found forbidden issues; revision is not allowed.",
        )

    if workflow_state == "BLOCKED":
        return StatusResolution(
            run_status="reviewed",
            last_action="blocked",
            review_result="BLOCKED",
            can_retry=False,
            terminal=True,
            summary="Workflow is blocked pending manual intervention.",
        )

    raise ValueError(f"Unknown workflow state: {workflow_state}")
