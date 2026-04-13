
import json
from pathlib import Path

import streamlit as st

from engine.decision_engine import DecisionEngine
from engine.workflow_state import WorkflowRecord, WorkflowState
from engine.artifact_store import ArtifactStore
from engine.backlog_manager import BacklogManager


st.set_page_config(page_title="DI Workbench", layout="wide")

ROOT = Path(".")
store = ArtifactStore(ROOT)
engine = DecisionEngine()
backlog = BacklogManager(ROOT)


def load_json(text: str, fallback: dict) -> dict:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else fallback
    except Exception:
        return fallback


def default_contract(artifact: str) -> dict:
    return {
        "artifact": artifact,
        "schema_locked": True,
        "locked_fields": [
            "output.score",
            "output.insights",
            "canonical_record.metrics.<n>.value",
        ],
        "forbidden_changes": [
            "remove_required_output",
            "rename_locked_field",
            "weaken_output_contract",
        ],
        "review_scope": [
            "clarity",
            "ambiguity",
            "implementation_risk",
            "missing_definition",
        ],
    }


def default_review() -> dict:
    return {
        "decision": "APPROVE",
        "issues": []
    }


def ensure_session_defaults(artifact: str, spec_default: str, contract_default: dict, review_default: dict) -> None:
    current_artifact = st.session_state.get("artifact_name")
    if current_artifact != artifact:
        st.session_state["artifact_name"] = artifact
        st.session_state["spec_text"] = spec_default
        st.session_state["contract_text"] = json.dumps(contract_default, indent=2)
        st.session_state["review_text"] = json.dumps(review_default, indent=2)
        st.session_state["decision_dict"] = None
        st.session_state["diff_dict"] = None
        st.session_state["last_action_message"] = ""
        st.session_state["loop_log"] = []


def load_or_create_workflow(artifact: str) -> WorkflowRecord:
    existing = store.load_workflow(artifact)
    if existing:
        try:
            state = WorkflowState(existing.get("state", WorkflowState.DRAFT.value))
        except Exception:
            state = WorkflowState.DRAFT

        return WorkflowRecord(
            artifact=existing.get("artifact", artifact),
            state=state,
            revision_loops=existing.get("revision_loops", 0),
            max_loops=existing.get("max_loops", 2),
            contract_frozen=existing.get("contract_frozen", False),
            created_at=existing.get("created_at"),
            updated_at=existing.get("updated_at"),
            history=existing.get("history", []),
        )
    return WorkflowRecord(artifact=artifact)


def persist_run(
    artifact: str,
    spec_text: str,
    contract: dict,
    review: dict,
    diff_dict: dict,
    workflow_record: WorkflowRecord,
    revision: int,
) -> None:
    store.save_spec(artifact, spec_text, revision=revision)
    store.save_contract(artifact, contract)
    store.save_review(artifact, review, loop=revision)
    store.save_diff(artifact, diff_dict, loop=revision)
    store.save_workflow(artifact, workflow_record.to_dict())


def render_backlog_sidebar() -> None:
    st.sidebar.header("Backlog")
    title = st.sidebar.text_input("New backlog item")
    project = st.sidebar.selectbox("Project", ["DI Workbench", "Lucy Platform"])
    notes = st.sidebar.text_area("Notes")

    if st.sidebar.button("Add to Backlog"):
        if not title.strip():
            st.sidebar.warning("Enter a backlog item title first.")
        else:
            item = backlog.add_item(title.strip(), project, notes.strip())
            st.sidebar.success(f"Added {item['id']}")


def render_backlog_table() -> None:
    items = backlog.list_items()
    st.subheader("Backlog")
    if not items:
        st.info("No backlog items yet.")
        return
    rows = [
        {
            "ID": item.get("id", ""),
            "Project": item.get("project", ""),
            "Created": item.get("created_at", ""),
            "Title": item.get("title", ""),
            "Status": item.get("status", ""),
            "Notes": item.get("notes", ""),
        }
        for item in items
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def apply_workflow_decision(record: WorkflowRecord, decision_dict: dict) -> WorkflowRecord:
    next_state_value = decision_dict.get("next_state", WorkflowState.BLOCKED.value)
    action = decision_dict.get("action", "UNKNOWN")
    rationale = decision_dict.get("rationale", "")

    try:
        next_state = WorkflowState(next_state_value)
    except Exception:
        next_state = WorkflowState.BLOCKED

    if record.state == WorkflowState.DRAFT:
        record.transition(WorkflowState.ARCHITECTED, reason="Workbench run initialized.")

    if record.state == WorkflowState.ARCHITECTED:
        record.transition(WorkflowState.UNDER_REVIEW, reason="Submitted to review.")

    if next_state == WorkflowState.REVIEW_REVISE_ALLOWED:
        if record.state == WorkflowState.UNDER_REVIEW:
            record.transition(WorkflowState.REVIEW_REVISE_ALLOWED, reason=f"{action}: {rationale}")
        record.increment_loop()

    elif next_state == WorkflowState.REVIEW_REVISE_FORBIDDEN:
        if record.state == WorkflowState.UNDER_REVIEW:
            record.transition(WorkflowState.REVIEW_REVISE_FORBIDDEN, reason=f"{action}: {rationale}")

    elif next_state == WorkflowState.APPROVED:
        if record.state == WorkflowState.UNDER_REVIEW:
            record.transition(WorkflowState.APPROVED, reason=f"{action}: {rationale}")

    elif next_state == WorkflowState.BLOCKED:
        if record.state == WorkflowState.UNDER_REVIEW:
            record.transition(WorkflowState.BLOCKED, reason=f"{action}: {rationale}")
        elif record.state in {WorkflowState.REVIEW_REVISE_ALLOWED, WorkflowState.REVIEW_REVISE_FORBIDDEN, WorkflowState.IMPLEMENTING}:
            record.transition(WorkflowState.BLOCKED, reason=f"{action}: {rationale}")

    return record


def apply_allowed_revisions_to_spec(spec_text: str, diff_dict: dict, loop_no: int) -> str:
    allowed = diff_dict.get("allowed", [])
    if not allowed:
        return spec_text

    lines = []
    lines.append("")
    lines.append(f"## Revision Loop {loop_no} Directives")
    lines.append("")
    lines.append("Applied bounded reviewer revisions:")
    lines.append("")
    for item in allowed:
        issue_id = item.get("issue_id", "UNKNOWN")
        target = item.get("target", "")
        proposed_fix = item.get("proposed_fix", "") or "No proposed fix supplied."
        message = item.get("message", "")
        lines.append(f"- [{issue_id}] Target: {target}")
        lines.append(f"  - Proposed fix: {proposed_fix}")
        if message:
            lines.append(f"  - Reviewer note: {message}")

    block = "\n".join(lines)
    return spec_text.rstrip() + "\n\n" + block


def reset_for_next_review_pass(artifact: str, record: WorkflowRecord, contract: dict) -> WorkflowRecord:
    if record.state == WorkflowState.REVIEW_REVISE_ALLOWED:
        record.transition(WorkflowState.ARCHITECTED, reason="Allowed revisions applied; ready for next review pass.")
        store.save_workflow(artifact, record.to_dict())
        store.save_contract(artifact, contract)
    return record


def execute_auto_loop(artifact: str, workflow_record: WorkflowRecord, contract: dict, review: dict) -> tuple[WorkflowRecord, dict | None, dict | None, list]:
    loop_log = []
    final_decision = None
    final_diff = None

    while True:
        decision = engine.process_review(workflow_record, contract, review)
        decision_dict = decision.to_dict()
        diff_dict = decision.diff_result.to_dict()

        workflow_record = apply_workflow_decision(workflow_record, decision_dict)

        loop_entry = {
            "loop": workflow_record.revision_loops,
            "state": workflow_record.state.value,
            "action": decision_dict.get("action", ""),
            "next_state": decision_dict.get("next_state", ""),
            "rationale": decision_dict.get("rationale", ""),
        }
        loop_log.append(loop_entry)

        persist_run(
            artifact=artifact,
            spec_text=st.session_state["spec_text"],
            contract=contract,
            review=review,
            diff_dict=diff_dict,
            workflow_record=workflow_record,
            revision=workflow_record.revision_loops,
        )

        final_decision = decision_dict
        final_diff = diff_dict

        if decision_dict.get("next_state") != WorkflowState.REVIEW_REVISE_ALLOWED.value:
            break

        if workflow_record.revision_loops >= workflow_record.max_loops:
            if workflow_record.state != WorkflowState.BLOCKED:
                workflow_record.transition(
                    WorkflowState.BLOCKED,
                    reason="Max revision loops reached during auto-loop execution.",
                )
            blocked_decision = {
                "next_state": WorkflowState.BLOCKED.value,
                "action": "ESCALATE_MAX_LOOPS",
                "rationale": f"Max revision loops ({workflow_record.max_loops}) reached without convergence.",
                "auto_route": False,
            }
            final_decision = blocked_decision
            st.session_state["last_action_message"] = "Auto-loop stopped at max revision limit."
            store.save_workflow(artifact, workflow_record.to_dict())
            break

        st.session_state["spec_text"] = apply_allowed_revisions_to_spec(
            st.session_state["spec_text"],
            diff_dict,
            workflow_record.revision_loops,
        )
        workflow_record = reset_for_next_review_pass(artifact, workflow_record, contract)

    return workflow_record, final_decision, final_diff, loop_log


def render_workflow_status(record: WorkflowRecord, decision_dict: dict | None) -> None:
    last_transition = record.history[-1] if record.history else None
    next_action = decision_dict.get("action", "Not run") if decision_dict else "Not run"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current State", record.state.value)
    c2.metric("Revision Loops", f"{record.revision_loops} / {record.max_loops}")
    c3.metric("Next Action", next_action)
    c4.metric("Last Transition", f"{last_transition['to']}" if last_transition else "None")

    if st.session_state.get("last_action_message"):
        st.info(st.session_state["last_action_message"])

    if decision_dict:
        rationale = decision_dict.get("rationale", "")
        if decision_dict.get("auto_route"):
            st.success(rationale)
        else:
            st.warning(rationale)


def render_diff_summary(diff_dict: dict | None) -> None:
    st.subheader("Diff Summary")
    if not diff_dict:
        st.info("Run the engine to see diff summary.")
        return

    allowed = len(diff_dict.get("allowed", []))
    forbidden = len(diff_dict.get("forbidden", []))
    blockers = len(diff_dict.get("blockers", []))
    d1, d2, d3 = st.columns(3)
    d1.metric("Allowed", allowed)
    d2.metric("Forbidden", forbidden)
    d3.metric("Blockers", blockers)

    if forbidden > 0:
        st.error(f"{forbidden} forbidden change attempt(s) detected.")
    elif blockers > 0:
        st.error(f"{blockers} blocker issue(s) detected.")
    elif allowed > 0:
        st.warning(f"{allowed} allowed revision(s) detected.")
    else:
        st.success("No forbidden changes or blockers detected.")


def render_issue_section(title: str, items: list, kind: str) -> None:
    st.subheader(title)
    if not items:
        st.info(f"No {title.lower()}.")
        return

    for item in items:
        issue_id = item.get("issue_id", "UNKNOWN")
        target = item.get("target", "")
        message = item.get("message", "")
        reason = item.get("reason", "")
        severity = item.get("severity", "")
        proposed_fix = item.get("proposed_fix", "")

        body = f"[{issue_id}] {target}\n\n{message}"
        if reason:
            body += f"\n\nReason: {reason}"
        if proposed_fix:
            body += f"\n\nProposed fix: {proposed_fix}"
        if severity:
            body += f"\n\nSeverity: {severity}"

        if kind == "forbidden":
            st.error(body)
        elif kind == "blocker":
            st.error(body)
        else:
            st.warning(body)


def render_loop_log(loop_log: list) -> None:
    st.subheader("Auto-Loop Log")
    if not loop_log:
        st.info("No loop activity yet.")
        return
    st.dataframe(loop_log, use_container_width=True, hide_index=True)


def main() -> None:
    render_backlog_sidebar()

    st.title("DI Workbench")
    st.caption("Auto-loop execution, driven by the real engine package.")

    artifact = st.text_input("Artifact Name", "pattern_engine")
    workflow_record = load_or_create_workflow(artifact)

    spec_default = (
        "# Objective\n"
        "Implement a governed review loop for DI Workbench.\n\n"
        "# Context\n"
        "Architect produces a spec, reviewer returns structured findings, "
        "and the engine governs routing."
    )
    contract_default = default_contract(artifact)
    review_default = default_review()

    existing_snapshot = store.snapshot(artifact)
    if existing_snapshot.get("spec"):
        spec_default = existing_snapshot["spec"]
    if existing_snapshot.get("contract"):
        contract_default = existing_snapshot["contract"]
    if existing_snapshot.get("review"):
        review_default = existing_snapshot["review"]

    ensure_session_defaults(artifact, spec_default, contract_default, review_default)

    st.text_area("Architect Spec (Markdown)", key="spec_text", height=240)

    col_a, col_b = st.columns(2)
    with col_a:
        st.text_area("Locked Contract JSON", key="contract_text", height=240)
    with col_b:
        st.text_area("Reviewer Findings JSON", key="review_text", height=240)

    contract = load_json(st.session_state["contract_text"], contract_default)
    review = load_json(st.session_state["review_text"], review_default)

    if st.button("Run Engine", type="primary"):
        workflow_record = load_or_create_workflow(artifact)
        workflow_record, final_decision, final_diff, loop_log = execute_auto_loop(
            artifact=artifact,
            workflow_record=workflow_record,
            contract=contract,
            review=review,
        )
        st.session_state["decision_dict"] = final_decision
        st.session_state["diff_dict"] = final_diff
        st.session_state["loop_log"] = loop_log
        if not st.session_state.get("last_action_message"):
            st.session_state["last_action_message"] = "Auto-loop execution complete."
        st.rerun()

    decision_dict = st.session_state.get("decision_dict")
    diff_dict = st.session_state.get("diff_dict")
    loop_log = st.session_state.get("loop_log", [])
    workflow_record = load_or_create_workflow(artifact)

    pane1, pane2, pane3, pane4 = st.columns(4)

    with pane1:
        st.subheader("Architect Spec")
        st.code(st.session_state["spec_text"], language="markdown")

    with pane2:
        st.subheader("Locked Contract")
        st.json(contract)

    with pane3:
        st.subheader("Reviewer Findings")
        st.json(review)

    with pane4:
        st.subheader("Decision")
        if decision_dict:
            st.json(decision_dict)
        else:
            st.info("Run the engine to see governed output.")

    st.divider()
    render_workflow_status(workflow_record, decision_dict)
    st.divider()
    render_loop_log(loop_log)
    st.divider()
    render_diff_summary(diff_dict)

    if diff_dict:
        c1, c2, c3 = st.columns(3)
        with c1:
            render_issue_section("Allowed Revisions", diff_dict.get("allowed", []), "allowed")
        with c2:
            render_issue_section("Forbidden Changes", diff_dict.get("forbidden", []), "forbidden")
        with c3:
            render_issue_section("Blockers", diff_dict.get("blockers", []), "blocker")

    st.divider()
    with st.expander("Workflow Record"):
        st.json(workflow_record.to_dict())

    snapshot_dict = store.snapshot(artifact)
    if snapshot_dict:
        with st.expander("Artifact Snapshot"):
            st.json(snapshot_dict)

    st.divider()
    render_backlog_table()


if __name__ == "__main__":
    main()
