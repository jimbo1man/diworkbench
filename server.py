from __future__ import annotations

from dotenv import load_dotenv
from pathlib import Path
import os
import json
import uuid

from datetime import datetime
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from engine.decision_engine import DecisionEngine
from engine.review_normalization import normalize_review
from engine.workflow_state import WorkflowState

env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

print("OPENAI KEY LOADED:", bool(os.getenv("OPENAI_API_KEY")))
print("ANTHROPIC KEY LOADED:", bool(os.getenv("ANTHROPIC_API_KEY")))

app = FastAPI(title="DI Workbench v2")


class SessionResponse(BaseModel):
    session_id: str


class ToolRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


SESSIONS: Dict[str, Dict[str, Any]] = {}


def _safe_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump()
    if hasattr(value, "dict") and callable(value.dict):
        return value.dict()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return value


def _build_session_state(session_id: str) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "created_at": datetime.utcnow().isoformat(),
        "workflow_state": None,
        "decision_engine": None,
    }


def _get_session(session_id: str) -> Optional[Dict[str, Any]]:
    return SESSIONS.get(session_id)


def _ensure_workflow_state(session: Dict[str, Any]) -> Any:
    if session["workflow_state"] is None:
        try:
            session["workflow_state"] = WorkflowState()
        except TypeError:
            session["workflow_state"] = {}
    return session["workflow_state"]


def _ensure_decision_engine(session: Dict[str, Any]) -> Any:
    if session["decision_engine"] is None:
        try:
            session["decision_engine"] = DecisionEngine()
        except TypeError:
            session["decision_engine"] = None
    return session["decision_engine"]


def _call_first_available(obj: Any, method_names: list[str], *args, **kwargs) -> Any:
    for name in method_names:
        method = getattr(obj, name, None)
        if callable(method):
            return method(*args, **kwargs)
    raise AttributeError(f"No compatible method found on {type(obj).__name__}: {method_names}")


@app.get("/")
def root():
    return {"service": "DI Workbench v2", "status": "running"}


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "sessions": len(SESSIONS),
    }


@app.post("/session", response_model=SessionResponse)
def create_session():
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = _build_session_state(session_id)
    return {"session_id": session_id}


@app.post("/tool")
def run_tool(request: ToolRequest):
    tool_name = request.tool_name
    args = request.arguments or {}

    if tool_name == "ping":
        return {"result": "pong"}

    if tool_name == "echo":
        return {"result": args}

    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required for this tool"}

    session = _get_session(session_id)
    if not session:
        return {"error": f"Unknown session_id: {session_id}"}

    if tool_name == "workflow_get":
        return {
            "session_id": session_id,
            "workflow_state": _safe_to_dict(session.get("workflow_state")),
        }

    if tool_name == "workflow_update":
        workflow_state = _ensure_workflow_state(session)
        payload = args.get("payload", {})

        if isinstance(workflow_state, dict):
            if not isinstance(payload, dict):
                return {"error": "workflow_update requires a dict payload"}
            workflow_state.update(payload)
            return {
                "session_id": session_id,
                "workflow_state": workflow_state,
            }

        try:
            result = _call_first_available(
                workflow_state,
                ["update", "apply", "merge", "set_state"],
                payload,
            )
            return {
                "session_id": session_id,
                "result": _safe_to_dict(result),
                "workflow_state": _safe_to_dict(workflow_state),
            }
        except Exception as e:
            return {"error": f"workflow_update failed: {str(e)}", "session_id": session_id}

    if tool_name == "decision_evaluate":
        decision_engine = _ensure_decision_engine(session)
        if decision_engine is None:
            return {"error": "DecisionEngine could not be initialized", "session_id": session_id}

        payload = args.get("payload", {})
        workflow_state_obj = _ensure_workflow_state(session)

        record = payload.get("record")
        contract = payload.get("contract")
        review = payload.get("review")

        if record is None or contract is None or review is None:
            return {
                "error": "decision_evaluate requires payload.record, payload.contract, and payload.review",
                "session_id": session_id,
            }

        try:
            result = decision_engine.process_review(
                record=record,
                contract=contract,
                review=review,
            )
            return {
                "session_id": session_id,
                "result": _safe_to_dict(result),
                "workflow_state": _safe_to_dict(workflow_state_obj),
            }
        except Exception as e:
            return {"error": f"decision_evaluate failed: {str(e)}", "session_id": session_id}

    if tool_name == "decision_with_state":
        decision_engine = _ensure_decision_engine(session)
        workflow_state = _ensure_workflow_state(session)

        if decision_engine is None:
            return {"error": "DecisionEngine could not be initialized", "session_id": session_id}

        payload = args.get("payload", {})

        try:
            result = _call_first_available(
                decision_engine,
                ["evaluate", "decide", "run", "process"],
                payload,
                workflow_state=workflow_state,
            )
            return {
                "session_id": session_id,
                "result": _safe_to_dict(result),
                "workflow_state": _safe_to_dict(workflow_state),
            }
        except TypeError:
            try:
                result = _call_first_available(
                    decision_engine,
                    ["evaluate", "decide", "run", "process"],
                    payload,
                    workflow_state,
                )
                return {
                    "session_id": session_id,
                    "result": _safe_to_dict(result),
                    "workflow_state": _safe_to_dict(workflow_state),
                }
            except Exception as e:
                return {"error": f"decision_with_state failed: {str(e)}", "session_id": session_id}
        except Exception as e:
            return {"error": f"decision_with_state failed: {str(e)}", "session_id": session_id}

    return {"error": f"Unknown tool: {tool_name}"}

# =========================
# Collaboration Layer MVP
# =========================

OPENAI_ARCHITECT_MODEL = os.getenv("OPENAI_ARCHITECT_MODEL", "gpt-5")
ANTHROPIC_REVIEWER_MODEL = os.getenv("ANTHROPIC_REVIEWER_MODEL", "claude-sonnet-4-6")

ARCHITECT_SYSTEM = """
You are the DI Workbench Architect.

Your job:
- Convert the user's problem into a structured, implementation-ready spec.
- Be decisive and concrete.
- No code.
- No hedging.
- No fluff.

You must return markdown with these exact sections in this exact order:

## Objective
## Context
## Assumptions
## Requirements
## Constraints
## Proposed Architecture
## Implementation Plan
## Risks
## Open Questions
## Approval Checklist

Rules:
- Only include Open Questions if they are truly blocking.
- Include at least one concrete example for any data structure or payload.
- Write so a second model can implement it directly.
"""

REVIEWER_SYSTEM = """
You are the DI Workbench Reviewer.

You review the Architect spec and return strict JSON only.

Return exactly this shape:

{
  "decision": "APPROVE" | "REVISE",
  "rationale": "short explanation",
  "findings": [
    {
      "severity": "high" | "medium" | "low",
      "scope": "ALLOWED" | "FORBIDDEN",
      "type": "clarity" | "architecture" | "risk" | "missing_requirement" | "constraint_violation" | "implementation_gap",
      "title": "short finding title",
      "detail": "what is wrong or missing",
      "suggested_fix": "bounded fix"
    }
  ]
}

Rules:
- Use APPROVE only if the spec is implementation-ready.
- Use REVISE if there are material issues.
- Keep findings bounded and concrete.
- Do not return markdown.
- Do not wrap JSON in code fences.
"""

class ArchitectRequest(BaseModel):
    problem: str

class ReviewRequest(BaseModel):
    problem: str
    spec: str

def _extract_openai_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"].strip()

    parts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    text = content.get("text", "")
                    if text:
                        parts.append(text)

    return "\n".join(parts).strip()

def _extract_anthropic_text(data: dict) -> str:
    parts = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()

def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
    return text

def _extract_json_object(text: str) -> dict:
    cleaned = _strip_code_fences(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in reviewer response.")
    return json.loads(cleaned[start:end + 1])

def call_openai_architect(problem: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set.")

    prompt = f"""
User problem:
{problem}

Generate the DI Workbench Architect spec now.
""".strip()

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_ARCHITECT_MODEL,
            "instructions": ARCHITECT_SYSTEM,
            "input": prompt,
            "max_output_tokens": 2500,
        },
        timeout=120,
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail=f"OpenAI architect call failed: {response.status_code} {response.text}",
        )

    data = response.json()
    text = _extract_openai_text(data)

    if not text:
        raise HTTPException(status_code=500, detail="OpenAI architect returned empty output.")

    return text

def call_anthropic_reviewer(problem: str, spec: str) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set.")

    review_prompt = f"""
User problem:
{problem}

Architect spec:
{spec}

Review the spec and return strict JSON only.
""".strip()

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_REVIEWER_MODEL,
            "system": REVIEWER_SYSTEM,
            "max_tokens": 1800,
            "messages": [
                {
                    "role": "user",
                    "content": review_prompt,
                }
            ],
        },
        timeout=120,
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail=f"Anthropic reviewer call failed: {response.status_code} {response.text}",
        )

    data = response.json()
    text = _extract_anthropic_text(data)

    if not text:
        raise HTTPException(status_code=500, detail="Anthropic reviewer returned empty output.")

    try:
        parsed = _extract_json_object(text)
    except Exception:
        parsed = {
            "decision": "REVISE",
            "rationale": "Reviewer returned non-JSON output. See raw_text.",
            "findings": [],
            "raw_text": text,
        }

    if "raw_text" not in parsed:
        parsed["raw_text"] = text

    normalized = normalize_review(parsed)
    out: Dict[str, Any] = dict(normalized)
    if "raw_text" in parsed:
        out["raw_text"] = parsed["raw_text"]
    if "rationale" in parsed:
        out["rationale"] = parsed["rationale"]
    return out

@app.post("/architect")
def run_architect(req: ArchitectRequest):
    spec = call_openai_architect(req.problem)
    return {
        "ok": True,
        "spec": spec,
        "model": OPENAI_ARCHITECT_MODEL,
    }

@app.post("/review")
def run_review(req: ReviewRequest):
    result = call_anthropic_reviewer(req.problem, req.spec)
    return {
        "ok": True,
        "review": result,
        "model": ANTHROPIC_REVIEWER_MODEL,
    }