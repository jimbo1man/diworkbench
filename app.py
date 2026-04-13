"""
app.py — DI Workbench v3 (Refactor: Two-step review pipeline)

Primary flow is a focused artifact pipeline:
  1) User provides Input Spec
  2) Claude performs first hard-gate review (APPROVE | REVISE | REJECT)
  3) If Claude != REJECT, ChatGPT adjudicates and produces ONE final spec artifact
  4) Human approves/rejects final spec

All diagnostics and legacy dashboard concepts must stay out of the primary flow.
"""

from pathlib import Path
import os

import streamlit as st
from dotenv import load_dotenv

from engine.run_orchestrator import RunOrchestrator
from engine.run_repository import RunRepository
from engine.services import (
    AnthropicClaudeReviewService,
    OpenAIChatGPTAdjudicationService,
    StubClaudeReviewService,
)
from engine.pipeline_models import WorkbenchRun


st.set_page_config(page_title="DI Workbench", layout="wide")
load_dotenv()

WORKBENCH_ROOT = Path(__file__).parent
repo = RunRepository(WORKBENCH_ROOT)


def _badge(state: str) -> str:
    state = (state or "").upper().strip()
    if state == "APPROVE":
        return "🟢 APPROVE"
    if state == "REVISE":
        return "🟠 REVISE"
    if state == "REJECT":
        return "🔴 REJECT"
    return "⚪ NOT_RUN"


def _build_orchestrator() -> RunOrchestrator:
    # Claude is canonical first gate. During Claude outage, keep architecture intact
    # by swapping in a stub via env var.
    claude_mode = str(os.getenv("CLAUDE_REVIEW_MODE", "stub")).lower().strip()
    if claude_mode == "real":
        claude = AnthropicClaudeReviewService()
    else:
        stub_outcome = str(os.getenv("CLAUDE_STUB_OUTCOME", "revise")).lower().strip()
        if stub_outcome not in {"approve", "revise", "reject"}:
            stub_outcome = "revise"
        claude = StubClaudeReviewService(mode=stub_outcome)  # type: ignore[arg-type]

    chatgpt = OpenAIChatGPTAdjudicationService()
    return RunOrchestrator(claude=claude, chatgpt=chatgpt)


def _init_session() -> None:
    if "input_spec" not in st.session_state:
        st.session_state["input_spec"] = ""
    if "run" not in st.session_state:
        st.session_state["run"] = None


_init_session()

st.title("DI Workbench")

# Sidebar: keep minimal, with optional debug.
with st.sidebar:
    st.caption("Artifact pipeline")
    latest = repo.get_latest_approved()
    if latest and latest.get("final_spec"):
        st.success("Latest approved spec is available.")
        if st.button("Load latest approved into input", use_container_width=True):
            st.session_state["input_spec"] = str(latest.get("final_spec") or "")
            st.session_state["run"] = None
            st.rerun()
    else:
        st.info("No approved spec yet.")

    with st.expander("Debug / Advanced", expanded=False):
        st.caption("Hidden by default. Not part of primary flow.")
        st.markdown(f"**Claude mode:** `{os.getenv('CLAUDE_REVIEW_MODE', 'stub')}`")
        if str(os.getenv("CLAUDE_REVIEW_MODE", "stub")).lower().strip() != "real":
            st.markdown(f"**Claude stub outcome:** `{os.getenv('CLAUDE_STUB_OUTCOME', 'revise')}`")
        runs = repo.list_recent_runs(limit=8)
        if runs:
            st.markdown("**Recent runs:**")
            for r in runs:
                st.markdown(
                    f"- `{r.get('createdAt','')[:19].replace('T',' ')}` "
                    f"Claude `{r.get('claudeState','')}` · ChatGPT `{r.get('chatgptState','')}` · Human `{r.get('humanDecision','')}`"
                )


orchestrator = _build_orchestrator()


st.divider()

# 1) Input Spec
st.subheader("Input Spec")
st.session_state["input_spec"] = st.text_area(
    "Input Spec",
    value=st.session_state.get("input_spec", ""),
    height=260,
    label_visibility="collapsed",
    placeholder="Paste your build spec here. This will be reviewed by Claude first.",
)

run_btn_col, _ = st.columns([1, 5])
with run_btn_col:
    if st.button("Run review pipeline", use_container_width=True):
        result = orchestrator.run(input_spec=st.session_state["input_spec"])
        wb_run = result.run
        repo.save_run(wb_run)
        st.session_state["run"] = wb_run
        st.rerun()


run: WorkbenchRun | None = st.session_state.get("run")

st.divider()

# 2) Claude Review
st.subheader("Claude Review")
if not run:
    st.info("Not run yet.")
else:
    st.markdown(f"**State:** `{_badge(run.claude.state)}`")
    if run.claude.reasoning:
        st.markdown("**Reasoning**")
        st.write(run.claude.reasoning)
    else:
        st.caption("No reasoning returned.")
    if run.claude.proposed_revisions:
        st.markdown("**Proposed revisions**")
        st.text_area(
            "Claude proposed revisions",
            value=run.claude.proposed_revisions,
            height=140,
            label_visibility="collapsed",
            disabled=True,
        )

    if run.claude.state == "REJECT":
        st.error("Pipeline stopped: Claude rejected the spec. ChatGPT did not run.")

st.divider()

# 3) Final Spec (dominant)
st.subheader("Final Spec")
if not run:
    st.info("Run the pipeline to generate a final spec.")
else:
    if run.claude.state == "REJECT":
        st.info("No final spec because Claude rejected.")
    else:
        st.markdown(f"**ChatGPT state:** `{_badge(run.chatgpt.state)}`")
        if run.chatgpt.reasoning:
            with st.expander("ChatGPT reasoning", expanded=False):
                st.write(run.chatgpt.reasoning)

        st.text_area(
            "Final Spec (copy-ready)",
            value=run.chatgpt.final_spec or "",
            height=420,
            label_visibility="collapsed",
        )

        a_col, r_col, _ = st.columns([1, 1, 6])
        with a_col:
            if st.button("Approve", use_container_width=True, type="primary"):
                run.human_decision.state = "APPROVED"
                repo.save_human_decision(run=run, decision="APPROVED")
                repo.promote_approved(run=run)
                st.success("Approved and persisted as latest approved spec.")
                st.session_state["run"] = run
                st.rerun()
        with r_col:
            if st.button("Reject", use_container_width=True):
                repo.save_human_decision(run=run, decision="REJECTED")
                st.warning("Rejected. Run was saved for history, but not promoted to approved.")
                st.session_state["run"] = run
                st.rerun()

        st.caption(f"Human decision: `{run.human_decision.state}`")

with st.expander("Debug / Advanced (raw responses)", expanded=False):
    if not run:
        st.info("No run yet.")
    else:
        if run.claude.raw_response:
            st.text_area("Claude raw response", value=run.claude.raw_response, height=160)
        if run.chatgpt.raw_response:
            st.text_area("ChatGPT raw response", value=run.chatgpt.raw_response, height=160)
        st.json(run.model_dump())
