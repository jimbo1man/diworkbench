"""
app.py — DI Workbench v3 (Slice A + Slice B)

Slice A: governed workflow shell (RunRecord, state machine, persistence)
Slice B: decision intelligence (scoring, comparison, resolution, insights)

Fix 1 — DI is never persisted:
  RunRecord holds ONLY primary data.
  Scores, resolutions, comparison, and insights are computed at runtime
  and stored in session_state["di"] as a plain dict.
  _save_record() writes only the RunRecord to disk.

Fix 2 — Recomputation happens:
  - on initial load (_init_session)
  - after every action that changes primary data
  - when the prior run selection changes

Session state keys:
  "run_record"         — active RunRecord (authoritative, persisted)
  "prior_record"       — optional prior RunRecord for comparison (not persisted)
  "di"                 — dict of computed DI outputs (not persisted)
  "intent_request"     — ephemeral user request for Architect generation
  "raw_architect_text"             — ephemeral API output, display-only
  "raw_reviewer_text"              — ephemeral API output, display-only
  "raw_implementation_text"        — ephemeral API output, display-only
  "raw_implementation_review_text" — ephemeral API output, display-only
"""

import json
import logging
import os
from pathlib import Path

import anthropic
import requests
import streamlit as st
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Engine imports — Slice A
# ---------------------------------------------------------------------------
from engine.run_record import (
    RunRecord,
    RevisionPatch,
    ScoreRecord,
    ImplementationReviewResult,
    new_run,
)
from engine.run_store import RunStore, _normalize_issue, _review_dict_to_review_result
from engine.workflow_transitions import (
    apply_transition,
    mark_spec_edited,
    mark_contract_edited,
)
from engine.decision_engine import DecisionEngine
from engine.review_normalization import normalize_review
from engine.status_adapter import map_engine_decision_to_run_status
from engine.workflow_state import WorkflowRecord, WorkflowState

# ---------------------------------------------------------------------------
# Engine imports — Slice B (Fix 3: independent modules, UI calls them)
# ---------------------------------------------------------------------------
from engine.run_scoring import compute_score
from engine.issue_resolution import compute_resolutions, resolution_counts
from engine.run_comparison import compare_runs, spec_diff_lines
from engine.run_insights import compute_insights

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

st.set_page_config(page_title="DI Workbench", layout="wide")
load_dotenv()

WORKBENCH_ROOT = Path(__file__).parent
store = RunStore(WORKBENCH_ROOT)
decision_engine = DecisionEngine()

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CONTRACT = {
    "artifact": "pattern_engine",
    "schema_locked": True,
    "locked_fields": ["output.score", "output.insights"],
    "forbidden_changes": [],
    "review_scope": ["clarity", "ambiguity", "implementation_risk", "missing_definition"],
}

DEFAULT_SPEC = """\
Objective
Design a daily health scoring engine that computes a fair daily score from available inputs, \
adjusts appropriately when some inputs are missing, and produces actionable insights that \
explain the score and identify improvement opportunities.

Context
The system evaluates one daily record at a time. Each daily record may include sleep, \
workout, and meditation inputs. Some inputs may be missing on a given day.

Requirements

Input Model
- sleep_hours: float or null (0.0-24.0)
- workout_minutes: integer or null (0-300)
- meditation_minutes: integer or null (0-180)

Output Contract
- output.score: float (0.0-100.0, rounded to 1 decimal)
- output.insights: list of 1-5 human-readable actionable strings
"""

# ---------------------------------------------------------------------------
# DI computation (Fix 1, Fix 2) — returns a plain dict, never mutates record
# ---------------------------------------------------------------------------

def _compute_di(record: RunRecord, prior: RunRecord | None) -> dict:
    """
    Compute all Slice B derived outputs from primary data.
    Returns a plain dict so nothing is accidentally persisted.
    Called explicitly — never called inside _save_record.
    """
    current_score = compute_score(record)
    prior_score   = compute_score(prior) if prior else ScoreRecord()

    resolutions = compute_resolutions(record, prior) if prior else []
    comparison  = compare_runs(record, prior, current_score, prior_score) if prior else None
    insights    = compute_insights(record, current_score, resolutions, comparison)

    return {
        "score":       current_score,
        "prior_score": prior_score,
        "resolutions": resolutions,
        "comparison":  comparison,
        "insights":    insights,
    }


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _load_record() -> RunRecord:
    return st.session_state["run_record"]

def _load_prior() -> RunRecord | None:
    return st.session_state.get("prior_record")

def _load_di() -> dict:
    return st.session_state.get("di", {})

def _save_record(record: RunRecord) -> None:
    """Write-through: update session state AND persist to disk.
    Never persists DI outputs — only the RunRecord itself."""
    st.session_state["run_record"] = record
    store.save(record)

def _refresh_di() -> None:
    """Recompute DI from current session state and store result."""
    st.session_state["di"] = _compute_di(_load_record(), _load_prior())

def _set_action(record: RunRecord, message: str) -> RunRecord:
    record.last_action = message
    return record

def _init_session() -> None:
    if "run_record" not in st.session_state:
        record = new_run("pattern_engine", DEFAULT_CONTRACT)
        record.architect_spec = DEFAULT_SPEC
        _save_record(record)
    if "prior_record" not in st.session_state:
        st.session_state["prior_record"] = None
    if "di" not in st.session_state:
        _refresh_di()
    if "intent_request" not in st.session_state:
        st.session_state["intent_request"] = ""
    for key in (
        "raw_architect_text",
        "raw_reviewer_text",
        "raw_implementation_text",
        "raw_implementation_review_text",
    ):
        if key not in st.session_state:
            st.session_state[key] = ""

_init_session()

# ---------------------------------------------------------------------------
# Anthropic helpers
# ---------------------------------------------------------------------------

def _get_reviewer_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY in environment.")
    return anthropic.Anthropic(api_key=api_key)

def _get_openai_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in environment.")
    return api_key

def _extract_text(response) -> str:
    return "".join(
        block.text for block in response.content if hasattr(block, "text")
    ).strip()

def _extract_json_object(text: str) -> dict:
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found.")
    return json.loads(text[start:end + 1])

def _blocked_review(message: str, fix: str) -> dict:
    return {
        "decision": "BLOCKED",
        "issues": [{
            "id": "system_001",
            "type": "implementation_risk",
            "severity": "high",
            "scope": "forbidden",
            "target": "reviewer.integration",
            "message": message,
            "proposed_fix": fix,
        }],
    }

def _call_architect(intent: str, contract: dict) -> tuple:
    """Architect is routed to OpenAI, not Anthropic.
    Returns: (spec_text, raw_text, input_tokens, output_tokens)
    """
    try:
        api_key = _get_openai_api_key()
    except Exception as exc:
        return f"Objective\nUnavailable\n\nContext\n{exc}", "", 0, 0

    system_prompt = (
        "You are the Architect for the DI Workbench. "
        "Produce a complete, implementation-ready architect spec from the user request. "
        "Be precise, opinionated, concrete, and deterministic. "
        "Respect the locked contract. Return spec text only with no markdown fences or commentary."
    )
    user_prompt = (
        f"Locked contract:\n{json.dumps(contract, indent=2)}\n\n"
        f"User request:\n{intent}\n\n"
        "Generate the architect spec in clear sections and exact implementation language."
    )

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("OPENAI_ARCHITECT_MODEL", "gpt-5"),
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
            },
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return f"Objective\nUnavailable\n\nContext\nArchitect failed: {exc}", "", 0, 0

    raw = payload.get("output_text", "").strip()
    usage = payload.get("usage", {}) or {}
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    return raw or DEFAULT_SPEC, raw, input_tokens, output_tokens


def _format_reviewer_findings(review_result) -> str:
    issues = getattr(review_result, "issues", []) or []
    if not issues:
        return "No reviewer findings."

    lines: list[str] = []
    for idx, issue in enumerate(issues, start=1):
        lines.append(f"{idx}. id: {issue.id}")
        lines.append(f"   type: {issue.type}")
        lines.append(f"   severity: {issue.severity}")
        lines.append(f"   scope: {issue.scope}")
        lines.append(f"   target: {issue.target}")
        lines.append(f"   message: {issue.message}")
        lines.append(f"   proposed_fix: {issue.proposed_fix}")
    return "\n".join(lines)


def _call_implementation_spec(architect_spec: str, review_result) -> tuple:
    """
    Generate a Cursor-ready implementation spec from the architect spec and
    reviewer findings using the same OpenAI request pattern as Architect.
    Returns: (implementation_spec, raw_text, input_tokens, output_tokens)
    """
    try:
        api_key = _get_openai_api_key()
    except Exception as exc:
        return (
            "TASK\nUnavailable\n\nOBJECTIVE\nUnavailable\n\nVALIDATION\n"
            f"OpenAI configuration error: {exc}",
            "",
            0,
            0,
        )

    system_prompt = (
        "Convert the approved Architect Spec and Reviewer Findings into a Cursor-ready "
        "Implementation Spec.\n\n"
        "Rules:\n"
        "* No architecture discussion\n"
        "* No optional improvements\n"
        "* No refactoring outside scope\n"
        "* Use explicit file paths\n"
        "* Use atomic, executable steps\n"
        "* Be precise and deterministic\n\n"
        "Return plain text only with these exact top-level sections in this order:\n"
        "TASK\n"
        "OBJECTIVE\n"
        "FILES TO CREATE\n"
        "FILES TO MODIFY\n"
        "FILES TO DELETE\n"
        "EXACT CHANGES\n"
        "DO NOT MODIFY\n"
        "VALIDATION\n"
        "OUTPUT REQUIREMENTS"
    )
    user_prompt = (
        f"Architect Spec:\n{architect_spec.strip() or 'No architect spec available.'}\n\n"
        f"Reviewer Findings:\n{_format_reviewer_findings(review_result)}\n\n"
        "Generate the implementation spec now."
    )

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv(
                    "OPENAI_IMPLEMENTATION_MODEL",
                    os.getenv("OPENAI_ARCHITECT_MODEL", "gpt-5"),
                ),
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
            },
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return (
            "TASK\nUnavailable\n\nOBJECTIVE\nUnavailable\n\nVALIDATION\n"
            f"Implementation spec generation failed: {exc}",
            "",
            0,
            0,
        )

    raw = payload.get("output_text", "").strip()
    usage = payload.get("usage", {}) or {}
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    return raw or "TASK\nUnavailable", raw, input_tokens, output_tokens


def _format_execution_result(execution_result: dict) -> str:
    if not isinstance(execution_result, dict) or not execution_result:
        return "No execution result recorded."

    files_changed = execution_result.get("files_changed") or []
    if not isinstance(files_changed, list):
        files_changed = []

    return "\n".join([
        f"implementation_status: {execution_result.get('implementation_status', 'not_started')}",
        f"validation_status: {execution_result.get('validation_status', 'partial')}",
        f"files_changed: {', '.join(str(f) for f in files_changed) if files_changed else '(none)'}",
        f"notes: {execution_result.get('notes', '')}",
        f"next_action: {execution_result.get('next_action', 'none')}",
    ])


def _fallback_implementation_review(message: str, required_fix: str) -> dict:
    return {
        "decision": "FIX_REQUIRED",
        "findings": [{
            "id": "impl_001",
            "severity": "high",
            "target": "implementation_review.integration",
            "message": message,
            "required_fix": required_fix,
        }],
        "summary": "Implementation review failed and requires manual follow-up.",
    }


def _normalize_implementation_review(review_raw: object) -> dict:
    if not isinstance(review_raw, dict):
        return _fallback_implementation_review(
            "Implementation review returned an invalid payload.",
            "Ensure Claude returns valid JSON matching the required schema.",
        )

    decision = str(review_raw.get("decision") or "").upper().strip()
    if decision not in {"APPROVE", "FIX_REQUIRED"}:
        decision = "FIX_REQUIRED"

    findings_raw = review_raw.get("findings") or []
    if not isinstance(findings_raw, list):
        findings_raw = []

    findings: list[dict] = []
    for idx, item in enumerate(findings_raw):
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "medium").lower().strip()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        findings.append({
            "id": str(item.get("id") or f"impl_{idx + 1:03d}"),
            "severity": severity,
            "target": str(item.get("target") or "implementation"),
            "message": str(item.get("message") or "(no message)"),
            "required_fix": str(item.get("required_fix") or ""),
        })

    return {
        "decision": decision,
        "findings": findings,
        "summary": str(review_raw.get("summary") or ""),
    }


def _call_implementation_review(
    architect_spec: str,
    contract: dict,
    review_result,
    implementation_spec: str,
    execution_result: dict,
) -> tuple:
    """
    Ask Claude to review whether the observed implementation outcome matches the
    approved plan and constraints.
    Returns: (review_dict, raw_text, input_tokens, output_tokens)
    """
    try:
        client = _get_reviewer_client()
    except Exception as exc:
        return (
            _fallback_implementation_review(
                str(exc),
                "Add ANTHROPIC_API_KEY to .env and rerun implementation review.",
            ),
            "",
            0,
            0,
        )

    system_prompt = (
        "You are the Implementation Reviewer in a DI Workbench workflow.\n"
        "Return JSON only. No markdown fences. No commentary.\n\n"
        "Rules:\n"
        "* Do not redesign the system\n"
        "* Do not write production code\n"
        "* Evaluate implementation fidelity only\n"
        "* Check whether implementation outcome aligns with:\n"
        "  * approved plan\n"
        "  * locked contract\n"
        "  * execution result\n"
        "* Prefer concrete, bounded findings\n\n"
        'Required JSON schema:\n{\n  "decision": "APPROVE" | "FIX_REQUIRED",\n'
        '  "findings": [\n    {\n      "id": "string",\n'
        '      "severity": "low" | "medium" | "high",\n'
        '      "target": "string",\n'
        '      "message": "string",\n'
        '      "required_fix": "string"\n    }\n  ],\n'
        '  "summary": "string"\n}'
    )
    user_prompt = (
        f"Architect Spec:\n{architect_spec.strip() or 'No architect spec available.'}\n\n"
        f"Locked Contract:\n{json.dumps(contract or {}, indent=2)}\n\n"
        f"Reviewer Findings:\n{_format_reviewer_findings(review_result)}\n\n"
        f"Implementation Spec:\n{implementation_spec.strip() or 'No implementation spec available.'}\n\n"
        f"Execution Result:\n{_format_execution_result(execution_result)}\n\n"
        "Evaluate implementation fidelity and return JSON only."
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1600,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        return (
            _fallback_implementation_review(
                f"Implementation review call failed: {exc}",
                "Check API key and connectivity, then rerun implementation review.",
            ),
            "",
            0,
            0,
        )

    raw = _extract_text(response)
    in_tok = getattr(response.usage, "input_tokens", 0)
    out_tok = getattr(response.usage, "output_tokens", 0)
    try:
        parsed = _extract_json_object(raw)
    except Exception as exc:
        return (
            _fallback_implementation_review(
                f"Could not parse implementation review JSON: {exc}",
                "Inspect the raw response and rerun implementation review.",
            ),
            raw,
            in_tok,
            out_tok,
        )

    return _normalize_implementation_review(parsed), raw, in_tok, out_tok


def _compute_next_action(record: RunRecord) -> dict:
    review_result = getattr(record, "review_result", None)
    review_issues = getattr(review_result, "issues", []) or []
    review_decision = str(getattr(review_result, "decision", "") or "").strip().upper()
    has_meaningful_review = bool(review_issues) or review_decision not in ("", "PENDING", "UNKNOWN")

    implementation_spec_text = str(getattr(record, "implementation_spec", "") or "").strip()
    execution_result = getattr(record, "execution_result", {}) or {}
    if not isinstance(execution_result, dict):
        execution_result = {}

    implementation_review_obj = getattr(record, "implementation_review", None)
    if hasattr(implementation_review_obj, "model_dump"):
        implementation_review = implementation_review_obj.model_dump()
    elif isinstance(implementation_review_obj, dict):
        implementation_review = implementation_review_obj
    else:
        implementation_review = {}

    if not has_meaningful_review:
        return {
            "recommended_action": "run_review",
            "reason": "Run a review before generating an implementation spec.",
            "severity": "warning",
        }

    if not implementation_spec_text:
        return {
            "recommended_action": "generate_implementation_spec",
            "reason": "Generate an implementation spec from the approved design and review findings.",
            "severity": "info",
        }

    if not execution_result:
        return {
            "recommended_action": "record_execution_result",
            "reason": "Record what happened after implementation before running implementation review.",
            "severity": "warning",
        }

    implementation_status = str(execution_result.get("implementation_status") or "not_started")
    if implementation_status == "not_started":
        return {
            "recommended_action": "record_execution_result",
            "reason": "Implementation has not started yet. Update the execution result after running the implementation spec.",
            "severity": "warning",
        }

    if implementation_status == "failed":
        return {
            "recommended_action": "retry",
            "reason": "Implementation failed. Retry execution or correct the implementation attempt before review.",
            "severity": "error",
        }

    if not implementation_review:
        return {
            "recommended_action": "run_implementation_review",
            "reason": "Run implementation review to verify fidelity against the plan and contract.",
            "severity": "info",
        }

    implementation_review_decision = str(implementation_review.get("decision") or "").upper()
    if implementation_review_decision == "APPROVE":
        return {
            "recommended_action": "finalize",
            "reason": "Implementation matches the plan and constraints. This run is ready to finalize.",
            "severity": "success",
        }

    if implementation_review_decision == "FIX_REQUIRED":
        findings = implementation_review.get("findings") or []
        if not isinstance(findings, list):
            findings = []
        has_high_severity = any(
            isinstance(finding, dict) and str(finding.get("severity") or "").lower() == "high"
            for finding in findings
        )
        if has_high_severity:
            return {
                "recommended_action": "revise_spec",
                "reason": "High-severity implementation review findings suggest the plan or execution approach needs revision.",
                "severity": "error",
            }
        return {
            "recommended_action": "retry",
            "reason": "Implementation review found bounded issues. Apply fixes and retry.",
            "severity": "warning",
        }

    return {
        "recommended_action": "none",
        "reason": "No next action recommended.",
        "severity": "info",
    }

def _call_reviewer(spec: str, contract: dict) -> tuple:
    try:
        client = _get_reviewer_client()
    except Exception as exc:
        return _blocked_review(str(exc), "Add ANTHROPIC_API_KEY to .env."), "", 0, 0
    system_prompt = (
        "You are the reviewer in a DI Workbench workflow.\n"
        "Return JSON only. No markdown fences. No commentary.\n\n"
        'Required JSON schema:\n{\n  "decision": "APPROVE" | "REVISE" | "BLOCKED",\n'
        '  "issues": [\n    {\n      "id": "string",\n'
        '      "type": "ambiguity" | "missing_definition" | "implementation_risk" | "contract_violation",\n'
        '      "severity": "low" | "medium" | "high",\n      "scope": "allowed" | "forbidden",\n'
        '      "target": "string",\n      "message": "string",\n      "proposed_fix": "string"\n    }\n  ]\n}'
    )
    user_prompt = (
        f"Locked contract:\n{json.dumps(contract, indent=2)}\n\n"
        f"Architect spec:\n{spec}\n\nReview the spec. Return JSON only."
    )
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1400, temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        return _blocked_review(f"Review call failed: {exc}", "Check API key and connectivity."), "", 0, 0
    raw     = _extract_text(response)
    in_tok  = getattr(response.usage, "input_tokens", 0)
    out_tok = getattr(response.usage, "output_tokens", 0)
    try:
        parsed = _extract_json_object(raw)
    except Exception as exc:
        return _blocked_review(f"Could not parse reviewer JSON: {exc}", "See raw response."), raw, in_tok, out_tok
    parsed = normalize_review(parsed)
    review_result = _review_dict_to_review_result(parsed)
    return {"decision": review_result.decision, "issues": [i.model_dump() for i in review_result.issues]}, raw, in_tok, out_tok

# ---------------------------------------------------------------------------
# Sidebar — Run History + Prior Run selector
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Run History")

    if st.button("+ New Run", use_container_width=True):
        record = new_run("pattern_engine", DEFAULT_CONTRACT)
        record.architect_spec = DEFAULT_SPEC
        record = _set_action(record, "New run created.")
        _save_record(record)
        st.session_state["prior_record"] = None
        st.session_state["intent_request"] = ""
        st.session_state["raw_architect_text"] = ""
        st.session_state["raw_reviewer_text"] = ""
        st.session_state["raw_implementation_text"] = ""
        st.session_state["raw_implementation_review_text"] = ""
        _refresh_di()
        st.rerun()

    st.divider()

    runs = store.list_runs()
    if not runs:
        st.info("No saved runs yet.")
    else:
        recent_runs = runs[:3]
        st.caption(f"{len(runs)} run(s) saved")
        st.caption("Showing 3 most recent runs")
        for run_summary in recent_runs:
            is_current = run_summary["run_id"] == _load_record().run_id
            label = (
                f"{'▶ ' if is_current else ''}"
                f"{run_summary['artifact_name']}  |  "
                f"{run_summary['status']}  |  "
                f"{run_summary['created_at'][:19].replace('T', ' ')}"
            )
            if st.button(label, key=f"load_{run_summary['filename']}", use_container_width=True):
                loaded = store.load_by_filename(run_summary["filename"])
                if loaded:
                    loaded = _set_action(loaded, f"Loaded: {run_summary['filename']}")
                    st.session_state["run_record"]  = loaded
                    st.session_state["prior_record"] = None
                    st.session_state["intent_request"] = ""
                    st.session_state["raw_architect_text"] = ""
                    st.session_state["raw_reviewer_text"] = ""
                    st.session_state["raw_implementation_text"] = ""
                    st.session_state["raw_implementation_review_text"] = ""
                    _refresh_di()
                    st.rerun()

    st.divider()
    st.subheader("Compare Against")
    prior_options = [r for r in runs if r["run_id"] != _load_record().run_id]
    if prior_options:
        prior_labels = ["(none)"] + [
            f"{r['artifact_name']} | {r['status']} | {r['created_at'][:16].replace('T',' ')}"
            for r in prior_options
        ]
        selected_idx = st.selectbox(
            "Prior run", range(len(prior_labels)),
            format_func=lambda i: prior_labels[i],
            key="prior_run_selector",
        )
        if selected_idx > 0:
            chosen       = prior_options[selected_idx - 1]
            current_prior = _load_prior()
            if current_prior is None or current_prior.run_id != chosen["run_id"]:
                prior_loaded = store.load_by_filename(chosen["filename"])
                if prior_loaded:
                    st.session_state["prior_record"] = prior_loaded
                    _refresh_di()
                    st.rerun()
        else:
            if _load_prior() is not None:
                st.session_state["prior_record"] = None
                _refresh_di()
                st.rerun()
    else:
        st.caption("Save more runs to enable comparison.")

# ---------------------------------------------------------------------------
# Main — header
# ---------------------------------------------------------------------------

st.title("DI Workbench")
record = _load_record()
prior  = _load_prior()
di     = _load_di()

col_name, col_status = st.columns([3, 1])
with col_name:
    new_artifact_name = st.text_input(
        "Artifact Name", value=record.artifact_name, key="artifact_name_input"
    )
    if new_artifact_name != record.artifact_name:
        record.artifact_name = new_artifact_name
        _save_record(record)

with col_status:
    STATUS_ICON = {
        "drafted":  "🟡 drafted",
        "reviewed": "🔵 reviewed",
        "revise":   "🟠 revise",
        "approved": "🟢 approved",
    }
    st.metric("Status", STATUS_ICON.get(record.status, record.status))

st.divider()

# ---------------------------------------------------------------------------
# Intent / Request (ephemeral input for Architect generation)
# ---------------------------------------------------------------------------

st.subheader("Intent / Request")
st.caption("Architect generation is routed to OpenAI. Reviewer remains on Claude.")
intent_value = st.text_area(
    "Intent / Request",
    value=st.session_state.get("intent_request", ""),
    height=180,
    key="intent_request_input",
    placeholder="Describe what you want the Architect to design. This is separate from the Architect Spec field below.",
    label_visibility="collapsed",
)
st.session_state["intent_request"] = intent_value

# ---------------------------------------------------------------------------
# Architect Spec
# ---------------------------------------------------------------------------

st.subheader("Architect Spec")
spec_value = st.text_area(
    "Spec", value=record.architect_spec, height=260,
    key="spec_textarea", label_visibility="collapsed",
)
if spec_value != record.architect_spec:
    record.architect_spec = spec_value
    record = mark_spec_edited(record)
    _save_record(record)
    _refresh_di()

# ---------------------------------------------------------------------------
# Implementation Spec
# ---------------------------------------------------------------------------

st.subheader("Implementation Spec")
implementation_spec_value = getattr(record, "implementation_spec", "") or ""
st.text_area(
    "Implementation Spec",
    value=implementation_spec_value or "No implementation spec generated yet.",
    height=220,
    key="implementation_spec_textarea",
    label_visibility="collapsed",
    disabled=True,
)

# Locked Contract
# ---------------------------------------------------------------------------

st.subheader("Locked Contract")
contract_text = st.text_area(
    "Contract JSON", value=json.dumps(record.locked_contract, indent=2),
    height=180, key="contract_textarea", label_visibility="collapsed",
)
try:
    parsed_contract = json.loads(contract_text)
    if not isinstance(parsed_contract, dict):
        raise ValueError()
    if parsed_contract != record.locked_contract:
        record.locked_contract = parsed_contract
        record = mark_contract_edited(record)
        _save_record(record)
        _refresh_di()
except Exception:
    parsed_contract = record.locked_contract

# ---------------------------------------------------------------------------
# Execution Result
# ---------------------------------------------------------------------------

st.subheader("Execution Result")
execution_result = getattr(record, "execution_result", {}) or {}
if not isinstance(execution_result, dict):
    execution_result = {}

if not execution_result:
    st.info("No execution result recorded yet.")
else:
    impl_status = str(execution_result.get("implementation_status") or "not_started")
    validation_status = str(execution_result.get("validation_status") or "partial")
    files_changed = execution_result.get("files_changed") or []
    if not isinstance(files_changed, list):
        files_changed = []
    notes = str(execution_result.get("notes") or "")
    next_action = str(execution_result.get("next_action") or "none")

    st.markdown(f"**Implementation Status:** `{impl_status}`")
    st.markdown(f"**Validation Status:** `{validation_status}`")
    st.markdown(
        f"**Files Changed:** {', '.join(files_changed) if files_changed else 'None recorded.'}"
    )
    st.markdown(f"**Notes:** {notes or 'No notes recorded.'}")
    st.markdown(f"**Next Action:** `{next_action}`")

with st.expander("Update Execution Result"):
    implementation_status_options = ["not_started", "complete", "failed"]
    validation_status_options = ["pass", "fail", "partial"]
    next_action_options = ["none", "retry", "revise_spec"]

    current_implementation_status = str(
        execution_result.get("implementation_status") or "not_started"
    )
    if current_implementation_status not in implementation_status_options:
        current_implementation_status = "not_started"

    current_validation_status = str(
        execution_result.get("validation_status") or "partial"
    )
    if current_validation_status not in validation_status_options:
        current_validation_status = "partial"

    current_next_action = str(execution_result.get("next_action") or "none")
    if current_next_action not in next_action_options:
        current_next_action = "none"

    current_files_changed = execution_result.get("files_changed") or []
    if not isinstance(current_files_changed, list):
        current_files_changed = []

    implementation_status = st.selectbox(
        "implementation_status",
        implementation_status_options,
        index=implementation_status_options.index(current_implementation_status),
        key="execution_result_implementation_status",
    )
    validation_status = st.selectbox(
        "validation_status",
        validation_status_options,
        index=validation_status_options.index(current_validation_status),
        key="execution_result_validation_status",
    )
    files_changed = st.text_input(
        "files_changed",
        value=", ".join(str(f) for f in current_files_changed),
        placeholder="e.g. app.py, engine/status_adapter.py",
        key="execution_result_files_changed",
    )
    notes = st.text_area(
        "notes",
        value=str(execution_result.get("notes") or ""),
        placeholder="What happened during implementation?",
        key="execution_result_notes",
        height=100,
    )
    next_action = st.selectbox(
        "next_action",
        next_action_options,
        index=next_action_options.index(current_next_action),
        key="execution_result_next_action",
    )

    if st.button("Save Execution Result", key="save_execution_result_btn"):
        parsed_files = [f.strip() for f in files_changed.split(",") if f.strip()]
        record.execution_result = {
            "implementation_status": implementation_status,
            "validation_status": validation_status,
            "files_changed": parsed_files,
            "notes": notes.strip(),
            "next_action": next_action,
        }
        record.last_action = "Execution result updated."
        _save_record(record)
        st.rerun()

# ---------------------------------------------------------------------------
# Implementation Review
# ---------------------------------------------------------------------------

st.subheader("Implementation Review")
implementation_review_obj = getattr(record, "implementation_review", None)
if hasattr(implementation_review_obj, "model_dump"):
    implementation_review = implementation_review_obj.model_dump()
elif isinstance(implementation_review_obj, dict):
    implementation_review = implementation_review_obj
else:
    implementation_review = {}

if not implementation_review:
    st.info("No implementation review recorded yet.")
else:
    decision = str(implementation_review.get("decision") or "UNKNOWN")
    summary = str(implementation_review.get("summary") or "")
    findings = implementation_review.get("findings") or []
    if not isinstance(findings, list):
        findings = []

    if decision == "APPROVE":
        st.success("Implementation approved.")
    elif decision == "FIX_REQUIRED":
        st.error("Implementation requires fixes.")

    st.markdown(f"**Decision:** `{decision}`")
    st.markdown(f"**Summary:** {summary or 'No summary provided.'}")

    if not findings:
        st.info("No implementation review findings.")
    else:
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            with st.container(border=True):
                st.markdown(
                    f"**{finding.get('id', 'impl_000')}** · `{finding.get('severity', 'medium')}`"
                )
                st.markdown(f"**Target:** `{finding.get('target', 'implementation')}`")
                st.markdown(f"**Message:** {finding.get('message', '(no message)')}")
                st.markdown(
                    f"**Required Fix:** {finding.get('required_fix', '') or 'No fix specified.'}"
                )

# ---------------------------------------------------------------------------
# Next Action
# ---------------------------------------------------------------------------

st.subheader("Next Action")
next_action = _compute_next_action(record)
recommended_action = str(next_action.get("recommended_action") or "none")
recommendation_reason = str(next_action.get("reason") or "No next action recommended.")
recommendation_severity = str(next_action.get("severity") or "info").lower()

if recommendation_severity == "warning":
    st.warning(recommendation_reason)
elif recommendation_severity == "success":
    st.success(recommendation_reason)
elif recommendation_severity == "error":
    st.error(recommendation_reason)
else:
    st.info(recommendation_reason)

st.markdown(f"**Recommended Action:** `{recommended_action}`")

# ---------------------------------------------------------------------------
# Action buttons
# ---------------------------------------------------------------------------

st.divider()
btn_a, btn_b, btn_c, btn_d, btn_e = st.columns(5)
st.caption("Pattern: OpenAI = Architect · Claude = Reviewer")

with btn_a:
    if st.button("Generate Architect Spec", use_container_width=True):
        intent = st.session_state.get("intent_request", "").strip() or "Generate a spec."
        spec, raw, in_tok, out_tok = _call_architect(intent, parsed_contract)
        record.architect_spec = spec
        record.cost.architect_input_tokens += in_tok
        record.cost.architect_output_tokens += out_tok
        record.cost.rerun_count += 1
        record.cost.recalculate_cost()
        record = mark_spec_edited(record)
        record = _set_action(record, "Architect spec generated.")
        st.session_state["raw_architect_text"] = raw
        st.session_state["intent_request"] = intent
        _save_record(record)
        _refresh_di()
        st.rerun()

with btn_b:
    if st.button("Run Review", use_container_width=True):
        from engine.run_record import ReviewResult as RR
        review_dict, raw, in_tok, out_tok = _call_reviewer(record.architect_spec, parsed_contract)
        issues = [_normalize_issue(i, idx) for idx, i in enumerate(review_dict.get("issues", []))]
        record.review_result = RR(decision=review_dict["decision"], issues=issues)
        record.cost.reviewer_input_tokens += in_tok
        record.cost.reviewer_output_tokens += out_tok
        record.cost.rerun_count += 1
        record.cost.recalculate_cost()
        if not isinstance(record.metadata, dict):
            record.metadata = {}
        workflow_record = WorkflowRecord(
            artifact=record.artifact_name,
            state=WorkflowState.UNDER_REVIEW,
            revision_loops=int(record.metadata.get("revision_loops", 0) or 0),
            max_loops=int(record.metadata.get("max_loops", 2) or 2),
        )
        engine_result = decision_engine.process_review(workflow_record, parsed_contract, review_dict)
        resolution = map_engine_decision_to_run_status(workflow_state=engine_result.next_state.value)
        record, _, _ = apply_transition(record, "reviewed", force=True)
        if resolution.run_status != "reviewed":
            record, ok, transition_msg = apply_transition(record, resolution.run_status)
            if not ok:
                logger.error("review status transition failed: %s", transition_msg)
        record.last_action = resolution.last_action
        record.metadata["last_action"] = resolution.last_action
        record.metadata["review_result"] = resolution.review_result
        record.metadata["can_retry"] = resolution.can_retry
        record.metadata["terminal"] = resolution.terminal
        record.metadata["status_summary"] = resolution.summary
        st.session_state["raw_reviewer_text"] = raw
        _save_record(record)
        _refresh_di()
        st.rerun()

with btn_c:
    if st.button("Generate Implementation Spec", use_container_width=True):
        review_result = getattr(record, "review_result", None)
        review_issues = getattr(review_result, "issues", []) or []
        review_decision = str(getattr(review_result, "decision", "") or "").strip().upper()
        has_meaningful_review = bool(review_issues) or review_decision not in ("", "PENDING", "UNKNOWN")
        if not has_meaningful_review:
            st.warning("Run a review before generating implementation spec.")
        else:
            implementation_spec, raw, in_tok, out_tok = _call_implementation_spec(
                getattr(record, "architect_spec", ""),
                record.review_result,
            )
            record.implementation_spec = implementation_spec
            record.cost.architect_input_tokens += in_tok
            record.cost.architect_output_tokens += out_tok
            record.cost.rerun_count += 1
            record.cost.recalculate_cost()
            record = _set_action(record, "Implementation spec generated.")
            st.session_state["raw_implementation_text"] = raw
            _save_record(record)
            _refresh_di()
            st.rerun()

with btn_d:
    if st.button("Run Implementation Review", use_container_width=True):
        implementation_spec_text = str(getattr(record, "implementation_spec", "") or "").strip()
        execution_result = getattr(record, "execution_result", {}) or {}
        has_execution = (
            isinstance(execution_result, dict)
            and bool(execution_result)
            and execution_result.get("implementation_status") != "not_started"
        )
        if not implementation_spec_text or not has_execution:
            st.warning("Generate implementation spec and record a valid execution result before running implementation review.")
        else:
            review_dict, raw, in_tok, out_tok = _call_implementation_review(
                architect_spec=str(getattr(record, "architect_spec", "") or ""),
                contract=parsed_contract,
                review_result=record.review_result,
                implementation_spec=implementation_spec_text,
                execution_result=execution_result,
            )
            record.implementation_review = ImplementationReviewResult.model_validate(review_dict)
            record.cost.reviewer_input_tokens += in_tok
            record.cost.reviewer_output_tokens += out_tok
            record.cost.rerun_count += 1
            record.cost.recalculate_cost()
            record.last_action = "Implementation review completed."
            st.session_state["raw_implementation_review_text"] = raw
            _save_record(record)
            st.rerun()

with btn_e:
    if st.button("Save Run", use_container_width=True):
        record = _set_action(record, "Run saved.")
        _save_record(record)
        st.rerun()

if record.last_action and record.last_action != "Idle":
    st.caption(record.last_action)

# ---------------------------------------------------------------------------
# Status banner
# ---------------------------------------------------------------------------

status_summary = record.metadata.get("status_summary", "Run a review to determine workflow status.")
engine_review_result = record.metadata.get("review_result", "PENDING")
st.info(f"Engine Decision: {engine_review_result} — {status_summary}")

st.divider()

# ---------------------------------------------------------------------------
# Reviewer Findings
# ---------------------------------------------------------------------------

st.subheader("Reviewer Findings")
issues = record.review_result.issues
if not issues:
    st.info("No findings. Run a review to see results here.")
else:
    severity_counts: dict[str, int] = {}
    for iss in issues:
        sev = (iss.severity or "unknown").lower()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    parts = []
    for sev in ("high", "medium", "low", "unknown"):
        n = severity_counts.get(sev, 0)
        if n:
            emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(sev, "⚪")
            parts.append(f"{emoji} {n} {sev}")
    st.markdown(f"**{len(issues)} issue(s):** " + "  ·  ".join(parts))

    for iss in issues:
        sev   = (iss.severity or "unknown").lower()
        emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(sev, "⚪")
        with st.container(border=True):
            h_col, b_col = st.columns([4, 1])
            with h_col:
                st.markdown(f"**{emoji} {iss.id}** — `{iss.type}`")
            with b_col:
                st.markdown(f"`{iss.severity}` · `{iss.scope}`")
            st.markdown(f"**Target:** `{iss.target}`")
            st.markdown(f"**Message:** {iss.message}")
            if iss.proposed_fix and iss.proposed_fix != "No proposed fix provided.":
                st.markdown(f"**Proposed Fix:** {iss.proposed_fix}")

    with st.expander("Raw Reviewer JSON (debug)"):
        st.json(record.review_result.model_dump())

st.divider()

# ---------------------------------------------------------------------------
# Revision Patches
# ---------------------------------------------------------------------------

st.subheader("Revision Patches")
patches = record.revision_patches
if patches:
    for patch in patches:
        with st.container(border=True):
            st.markdown(f"**Issue:** `{patch.issue_id}`  ·  **Status:** `{patch.status}`")
            st.markdown(patch.change_summary)
else:
    st.info("No revision patches recorded yet.")

with st.expander("Add Revision Patch"):
    patch_issue_id = st.text_input("Issue ID", key="patch_issue_id_input")
    patch_status   = st.selectbox("Status", ["addressed", "partial", "deferred", "wont_fix"], key="patch_status_input")
    patch_summary  = st.text_area("Change Summary", key="patch_summary_input", height=80,
                                  placeholder="Describe what was changed to address this issue...")
    if st.button("Add Patch", key="add_patch_btn"):
        if patch_issue_id.strip() and patch_summary.strip():
            record.revision_patches.append(RevisionPatch(
                issue_id=patch_issue_id.strip(),
                status=patch_status,
                change_summary=patch_summary.strip(),
            ))
            record = _set_action(record, f"Patch added for issue: {patch_issue_id.strip()}")
            _save_record(record)
            _refresh_di()
            st.rerun()
        else:
            st.warning("Issue ID and Change Summary are both required.")

st.divider()

# ===========================================================================
# SLICE B — Decision Intelligence panels (all read from di dict, not record)
# ===========================================================================

st.header("Decision Intelligence")

score      = di.get("score", ScoreRecord())
ins        = di.get("insights")
resolutions = di.get("resolutions", [])
cmp        = di.get("comparison")

# ---------------------------------------------------------------------------
# Score Panel
# ---------------------------------------------------------------------------

st.subheader("Run Score")
s1, s2, s3, s4, s5 = st.columns(5)
s1.metric("Completeness", f"{score.completeness_score:.0f}")
s2.metric("Clarity",      f"{score.clarity_score:.0f}")
s3.metric("Feasibility",  f"{score.feasibility_score:.0f}")
s4.metric("Governance",   f"{score.governance_score:.0f}")
s5.metric("Overall (/100)", f"{score.overall_score:.1f}")

if prior and cmp:
    delta_color = "green" if cmp.score_delta >= 0 else "red"
    sign = "+" if cmp.score_delta >= 0 else ""
    st.caption(
        f"vs prior: overall **:{delta_color}[{sign}{cmp.score_delta:.1f}]**  ·  "
        f"issues **{'+' if cmp.issue_count_delta >= 0 else ''}{cmp.issue_count_delta}**  ·  "
        f"high-severity **{'+' if cmp.high_severity_delta >= 0 else ''}{cmp.high_severity_delta}**"
    )

st.divider()

# ---------------------------------------------------------------------------
# Insights Panel
# ---------------------------------------------------------------------------

st.subheader("Insights")
if ins:
    if ins.approval_rationale:
        st.markdown(f"**Approval Rationale:** {ins.approval_rationale}")
    if ins.blocking_concerns:
        st.markdown("**Blocking Concerns:**")
        for concern in ins.blocking_concerns:
            st.markdown(f"- {concern}")
    if ins.improvement_summary:
        st.markdown("**Improvements:**")
        for item in ins.improvement_summary:
            st.markdown(f"- ✅ {item}")
    if ins.regression_summary:
        st.markdown("**Regressions / Concerns:**")
        for item in ins.regression_summary:
            st.markdown(f"- ⚠️ {item}")
    if ins.cost_effectiveness_note:
        st.markdown(f"**Cost Effectiveness:** {ins.cost_effectiveness_note}")
    if not any([ins.approval_rationale, ins.blocking_concerns,
                ins.improvement_summary, ins.regression_summary,
                ins.cost_effectiveness_note]):
        st.info("Run a review to generate insights.")
else:
    st.info("Run a review to generate insights.")

st.divider()

# ---------------------------------------------------------------------------
# Issue Resolution Panel
# ---------------------------------------------------------------------------

st.subheader("Issue Resolution Tracking")

if not resolutions:
    if prior:
        st.info("No prior issues to track. Run a review on both runs.")
    else:
        st.info("Select a prior run in the sidebar to enable resolution tracking.")
else:
    counts = resolution_counts(resolutions)
    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("✅ Resolved",          counts.get("resolved", 0))
    rc2.metric("🟡 Claimed Addressed", counts.get("claimed_addressed", 0))
    rc3.metric("⬜ Unaddressed",       counts.get("unaddressed", 0))
    rc4.metric("🔴 Regressed",         counts.get("regressed", 0))

    STATUS_EMOJI = {
        "resolved":          "✅",
        "claimed_addressed": "🟡",
        "unaddressed":       "⬜",
        "regressed":         "🔴",
    }
    with st.expander(f"Resolution detail ({len(resolutions)} issue(s))"):
        for res in resolutions:
            emoji = STATUS_EMOJI.get(res.status, "•")
            match_note = f" _(matched by {res.match_method})_" if res.match_method != "id" else ""
            st.markdown(
                f"{emoji} **{res.issue_id}** — `{res.status}`{match_note}  \n"
                f"_{res.notes}_"
            )

st.divider()

# ---------------------------------------------------------------------------
# Comparison Panel
# ---------------------------------------------------------------------------

st.subheader("Run Comparison")
if not cmp:
    st.info("Select a prior run in the sidebar to enable comparison.")
else:
    cmp_cols = st.columns(5)
    cmp_cols[0].metric("Status", cmp.current_status,
                       delta=f"was {cmp.prior_status}" if cmp.status_changed else None)
    cmp_cols[1].metric("Score Δ",    f"{cmp.score_delta:+.1f}")
    cmp_cols[2].metric("Issues Δ",   f"{cmp.issue_count_delta:+d}")
    cmp_cols[3].metric("High-sev Δ", f"{cmp.high_severity_delta:+d}")
    cmp_cols[4].metric("Cost Δ ($)", f"{cmp.estimated_cost_delta:+.4f}")

    if cmp.improved_items:
        st.markdown("**Improved:**")
        for item in cmp.improved_items:
            st.markdown(f"- ✅ {item}")
    if cmp.worsened_items:
        st.markdown("**Worsened:**")
        for item in cmp.worsened_items:
            st.markdown(f"- ⚠️ {item}")
    if not cmp.improved_items and not cmp.worsened_items:
        st.info("No significant changes detected between runs.")

    if prior:
        with st.expander("Spec diff (current vs prior)"):
            diff_lines = spec_diff_lines(prior.architect_spec, record.architect_spec)
            if not diff_lines:
                st.info("Specs are identical.")
            else:
                st.code("\n".join(diff_lines), language="diff")

st.divider()

# ---------------------------------------------------------------------------
# Cost Panel
# ---------------------------------------------------------------------------

st.subheader("Cost")
cost = record.cost
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Arch. Input",   cost.architect_input_tokens)
c2.metric("Arch. Output",  cost.architect_output_tokens)
c3.metric("Rev. Input",    cost.reviewer_input_tokens)
c4.metric("Rev. Output",   cost.reviewer_output_tokens)
c5.metric("Est. Cost ($)", f"{cost.estimated_total_cost:.4f}")
c6.metric("Reruns",        cost.rerun_count)

st.divider()

# ---------------------------------------------------------------------------
# Debug expanders
# ---------------------------------------------------------------------------

with st.expander("Raw Architect Response"):
    raw_arch = st.session_state.get("raw_architect_text", "")
    if raw_arch:
        st.text_area("Architect raw output", value=raw_arch, height=200, key="debug_arch")
    else:
        st.info("No architect response yet.")

with st.expander("Raw Reviewer Response"):
    raw_rev = st.session_state.get("raw_reviewer_text", "")
    if raw_rev:
        st.text_area("Reviewer raw output", value=raw_rev, height=200, key="debug_rev")
    else:
        st.info("No reviewer response yet.")

with st.expander("Raw Implementation Spec Response"):
    raw_impl = st.session_state.get("raw_implementation_text", "")
    if raw_impl:
        st.text_area("Implementation raw output", value=raw_impl, height=200, key="debug_impl")
    else:
        st.info("No implementation spec generated yet.")

with st.expander("Raw Implementation Review Response"):
    raw_impl_review = st.session_state.get("raw_implementation_review_text", "")
    if raw_impl_review:
        st.text_area(
            "Implementation review raw output",
            value=raw_impl_review,
            height=200,
            key="debug_impl_review",
        )
    else:
        st.info("No implementation review recorded yet.")

with st.expander("Full Run Record (JSON)"):
    st.json(json.loads(record.model_dump_json()))
