from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .pipeline_models import WorkbenchRun, _now_iso


@dataclass(frozen=True)
class ApprovedArtifact:
    run_id: str
    approved_at: str
    final_spec: str
    claude_state: str
    claude_reasoning: str
    claude_proposed_revisions: str
    chatgpt_state: str


class RunRepository:
    """
    Persistence for pipeline runs + approved artifacts.

    - Runs: saved to runs/ as timestamped JSON.
    - Approved artifact: saved to approved/latest_approved_spec.json.
    """

    def __init__(self, root: Path):
        self._root = root
        self._runs_dir = root / "runs"
        self._approved_dir = root / "approved"
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._approved_dir.mkdir(parents=True, exist_ok=True)

    def save_run(self, run: WorkbenchRun) -> Path:
        path = self._runs_dir / self._make_run_filename(run)
        path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        return path

    def save_human_decision(self, *, run: WorkbenchRun, decision: str) -> WorkbenchRun:
        run.human_decision.state = decision  # type: ignore[assignment]
        run.human_decision.decided_at = _now_iso()
        self.save_run(run)
        return run

    def promote_approved(self, *, run: WorkbenchRun) -> Path:
        approved = ApprovedArtifact(
            run_id=run.id,
            approved_at=run.human_decision.decided_at or _now_iso(),
            final_spec=run.chatgpt.final_spec,
            claude_state=run.claude.state,
            claude_reasoning=run.claude.reasoning,
            claude_proposed_revisions=run.claude.proposed_revisions,
            chatgpt_state=run.chatgpt.state,
        )
        path = self._approved_dir / "latest_approved_spec.json"
        path.write_text(json.dumps(approved.__dict__, indent=2), encoding="utf-8")
        return path

    def get_latest_approved(self) -> Optional[dict]:
        path = self._approved_dir / "latest_approved_spec.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_recent_runs(self, limit: int = 10) -> list[dict]:
        summaries: list[dict] = []
        for path in sorted(self._runs_dir.glob("*.json"), reverse=True)[: max(limit, 0)]:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                summaries.append(
                    {
                        "filename": path.name,
                        "id": raw.get("id", ""),
                        "createdAt": raw.get("created_at", ""),
                        "claudeState": (raw.get("claude") or {}).get("state", ""),
                        "chatgptState": (raw.get("chatgpt") or {}).get("state", ""),
                        "humanDecision": (raw.get("human_decision") or {}).get("state", ""),
                    }
                )
            except Exception:
                continue
        return summaries

    def _make_run_filename(self, run: WorkbenchRun) -> str:
        try:
            dt = datetime.fromisoformat(run.created_at.replace("Z", "+00:00"))
            ts = dt.strftime("%Y-%m-%dT%H-%M-%S")
        except Exception:
            ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
        short_id = (run.id or "")[:8] or "run"
        safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", "two_step_review")
        return f"{ts}_{safe}_{short_id}.json"

