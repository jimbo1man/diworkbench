from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal, Protocol

import anthropic
import requests

from .pipeline_models import ReviewState
from .llm_json import LLMJSONParseError, parse_llm_json_response


@dataclass(frozen=True)
class ClaudeReviewResult:
    state: ReviewState
    reasoning: str
    proposed_revisions: str
    raw_response: str = ""
    parse_error: str = ""


@dataclass(frozen=True)
class ChatGPTAdjudicationResult:
    state: ReviewState
    final_spec: str
    reasoning: str
    raw_response: str = ""
    parse_error: str = ""


class ClaudeReviewService(Protocol):
    def review(self, spec: str) -> ClaudeReviewResult: ...


class ChatGPTAdjudicationService(Protocol):
    def adjudicate(
        self, *, input_spec: str, claude_reasoning: str, claude_proposed_revisions: str, claude_state: ReviewState
    ) -> ChatGPTAdjudicationResult: ...


def _extract_anthropic_text(response: anthropic.types.Message) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts).strip()


def _clamp_review_state(value: object) -> ReviewState:
    v = str(value or "").upper().strip()
    if v in ("APPROVE", "REVISE", "REJECT", "NOT_RUN"):
        return v  # type: ignore[return-value]
    return "REVISE"


class AnthropicClaudeReviewService:
    """
    Real Claude reviewer. Canonical architecture assumes this is the first hard gate.
    """

    def __init__(self, *, model: str | None = None):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("Missing ANTHROPIC_API_KEY in environment.")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model or os.getenv("ANTHROPIC_CLAUDE_REVIEW_MODEL", "claude-sonnet-4-20250514")

    def review(self, spec: str) -> ClaudeReviewResult:
        system_prompt = (
            "You are Claude acting as the first review gate for a build specification.\n"
            "Return JSON only. No markdown fences. No additional keys.\n\n"
            'Schema:\n{\n  "state": "APPROVE" | "REVISE" | "REJECT",\n'
            '  "reasoning": "concise reasoning (max ~8 bullet lines or short paragraph)",\n'
            '  "proposedRevisions": "proposed edits to the spec (empty string if none)"\n}\n\n'
            "Rules:\n"
            "- Be concise and high-signal.\n"
            "- If REJECT, the pipeline must stop.\n"
            "- If REVISE, propose bounded revisions that preserve intent.\n"
        )
        user_prompt = f"Build spec to review:\n\n{(spec or '').strip()}"

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1400,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = _extract_anthropic_text(response)
        try:
            parsed = parse_llm_json_response(raw_text)
            state = _clamp_review_state(parsed.get("state"))
            if state == "NOT_RUN":
                state = "REVISE"
            reasoning = str(parsed.get("reasoning") or "").strip()
            proposed = str(parsed.get("proposedRevisions") or "").strip()
            return ClaudeReviewResult(
                state=state,
                reasoning=reasoning,
                proposed_revisions=proposed,
                raw_response=raw_text,
                parse_error="",
            )
        except LLMJSONParseError as e:
            return ClaudeReviewResult(
                state="REVISE",
                reasoning="Reviewer returned an unparseable JSON response. See Debug / Advanced for raw output.",
                proposed_revisions="",
                raw_response=raw_text,
                parse_error=str(e),
            )


class StubClaudeReviewService:
    """
    Development stub for Claude. Enables the canonical Claude-first pipeline
    while Claude is temporarily unavailable/rate-limited.
    """

    def __init__(self, *, mode: Literal["approve", "revise", "reject"] = "revise"):
        self._mode = mode

    def review(self, spec: str) -> ClaudeReviewResult:
        spec = (spec or "").strip()
        if not spec:
            return ClaudeReviewResult(
                state="REJECT",
                reasoning="Spec is empty.",
                proposed_revisions="Provide a concrete build spec with requirements and constraints.",
                raw_response="",
            )
        if self._mode == "approve":
            return ClaudeReviewResult(
                state="APPROVE",
                reasoning="Looks implementation-ready. Requirements are clear and bounded.",
                proposed_revisions="",
                raw_response="",
            )
        if self._mode == "reject":
            return ClaudeReviewResult(
                state="REJECT",
                reasoning="Spec is too ambiguous for an implementation handoff.",
                proposed_revisions="Add explicit scope, files, constraints, and acceptance criteria.",
                raw_response="",
            )
        return ClaudeReviewResult(
            state="REVISE",
            reasoning="Spec is mostly clear, but needs tighter acceptance criteria and explicit constraints.",
            proposed_revisions="Add: (1) explicit in-scope/out-of-scope, (2) acceptance criteria, (3) test plan.",
            raw_response="",
        )


class OpenAIChatGPTAdjudicationService:
    """
    ChatGPT adjudicates Claude's output and produces ONE final revised spec artifact.
    """

    def __init__(self, *, model: str | None = None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Missing OPENAI_API_KEY in environment.")
        self._api_key = api_key
        self._model = model or os.getenv("OPENAI_ADJUDICATOR_MODEL", os.getenv("OPENAI_ARCHITECT_MODEL", "gpt-5"))

    def adjudicate(
        self, *, input_spec: str, claude_reasoning: str, claude_proposed_revisions: str, claude_state: ReviewState
    ) -> ChatGPTAdjudicationResult:
        system_prompt = (
            "You are ChatGPT acting as a final adjudicator.\n"
            "You receive the original build spec plus Claude's gate decision, reasoning, and proposed revisions.\n"
            "Your job is to produce ONE clean final build spec artifact for Cursor to implement.\n\n"
            "Return EXACTLY ONE JSON OBJECT and nothing else.\n"
            "Do not include markdown fences. Do not include prose before or after the JSON.\n"
            "Do not include any keys other than those in the schema.\n\n"
            "Schema (must match exactly):\n"
            "{\n"
            '  "state": "APPROVE" | "REVISE" | "REJECT",\n'
            '  "reasoning": "short string",\n'
            '  "final_spec": "full final implementation-ready spec as a string"\n'
            "}\n\n"
            "Rules:\n"
            "- state must be one of APPROVE, REVISE, REJECT.\n"
            "- final_spec must always be present (use empty string if REJECT).\n"
            "- reasoning must be concise.\n"
            "- One pass only. No loops. No alternatives.\n"
            "- Incorporate Claude's proposed revisions when beneficial.\n"
        )
        user_prompt = (
            f"Original spec:\n{(input_spec or '').strip()}\n\n"
            f"Claude gate state: {claude_state}\n"
            f"Claude reasoning:\n{(claude_reasoning or '').strip()}\n\n"
            f"Claude proposed revisions:\n{(claude_proposed_revisions or '').strip()}\n\n"
            "Adjudicate and produce the final spec JSON now."
        )

        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw_text = str(payload.get("output_text") or "").strip()
        try:
            parsed = parse_llm_json_response(raw_text)
            state = _clamp_review_state(parsed.get("state"))
            if state == "NOT_RUN":
                state = "REVISE"

            # Back-compat: accept either final_spec or finalSpec.
            final_spec = str(
                (parsed.get("final_spec") if "final_spec" in parsed else parsed.get("finalSpec")) or ""
            ).strip()
            reasoning = str(parsed.get("reasoning") or "").strip()
            return ChatGPTAdjudicationResult(
                state=state,
                final_spec=final_spec,
                reasoning=reasoning,
                raw_response=raw_text,
                parse_error="",
            )
        except LLMJSONParseError as e:
            fallback_spec = (input_spec or "").strip()
            return ChatGPTAdjudicationResult(
                state="REVISE",
                reasoning="Adjudicator returned an unparseable JSON response. Showing original spec as fallback. See Debug / Advanced for raw output.",
                final_spec=fallback_spec,
                raw_response=raw_text,
                parse_error=str(e),
            )

