from __future__ import annotations

from dataclasses import dataclass

from .pipeline_models import WorkbenchRun
from .services import ClaudeReviewService, ChatGPTAdjudicationService


@dataclass(frozen=True)
class OrchestratorResult:
    run: WorkbenchRun
    stopped: bool
    stop_reason: str = ""


class RunOrchestrator:
    """
    Executes the canonical pipeline in order:
      1) Claude review (hard gate)
      2) If Claude != REJECT, ChatGPT adjudication produces final spec

    One pass only. No autonomous loops.
    """

    def __init__(
        self,
        *,
        claude: ClaudeReviewService,
        chatgpt: ChatGPTAdjudicationService,
    ):
        self._claude = claude
        self._chatgpt = chatgpt

    def run(self, *, input_spec: str) -> OrchestratorResult:
        wb = WorkbenchRun(input_spec=(input_spec or ""))

        claude_out = self._claude.review(wb.input_spec)
        wb.claude.state = claude_out.state
        wb.claude.reasoning = claude_out.reasoning
        wb.claude.proposed_revisions = claude_out.proposed_revisions
        wb.claude.raw_response = claude_out.raw_response or None

        if wb.claude.state == "REJECT":
            wb.chatgpt.state = "NOT_RUN"
            wb.chatgpt.final_spec = ""
            wb.chatgpt.reasoning = ""
            wb.chatgpt.raw_response = None
            return OrchestratorResult(run=wb, stopped=True, stop_reason="Claude rejected the spec.")

        chat_out = self._chatgpt.adjudicate(
            input_spec=wb.input_spec,
            claude_state=wb.claude.state,
            claude_reasoning=wb.claude.reasoning,
            claude_proposed_revisions=wb.claude.proposed_revisions,
        )
        wb.chatgpt.state = chat_out.state
        wb.chatgpt.final_spec = chat_out.final_spec
        wb.chatgpt.reasoning = chat_out.reasoning
        wb.chatgpt.raw_response = chat_out.raw_response or None

        return OrchestratorResult(run=wb, stopped=False, stop_reason="")

