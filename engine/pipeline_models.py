from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


ReviewState = Literal["APPROVE", "REVISE", "REJECT", "NOT_RUN"]
HumanDecisionState = Literal["APPROVED", "REJECTED", "PENDING"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClaudeReviewBlock(BaseModel):
    state: ReviewState = "NOT_RUN"
    reasoning: str = ""
    proposed_revisions: str = ""
    raw_response: Optional[str] = None


class ChatGPTAdjudicationBlock(BaseModel):
    state: ReviewState = "NOT_RUN"
    final_spec: str = ""
    reasoning: str = ""
    raw_response: Optional[str] = None


class HumanDecisionBlock(BaseModel):
    state: HumanDecisionState = "PENDING"
    decided_at: Optional[str] = None


class WorkbenchRun(BaseModel):
    """
    Canonical run shape for the two-step review pipeline:
      Input Spec -> Claude gate -> ChatGPT adjudicated artifact -> Human decision.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: str = Field(default_factory=_now_iso)
    input_spec: str = ""

    claude: ClaudeReviewBlock = Field(default_factory=ClaudeReviewBlock)
    chatgpt: ChatGPTAdjudicationBlock = Field(default_factory=ChatGPTAdjudicationBlock)
    human_decision: HumanDecisionBlock = Field(default_factory=HumanDecisionBlock)

