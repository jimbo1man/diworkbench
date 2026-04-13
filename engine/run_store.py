"""
run_store.py — Durable run-based persistence for DI Workbench v3 Slice A

Each workbench run is saved as a single JSON file under:
    <workbench_root>/runs/<ISO_timestamp>_<artifact_name>_<short_id>.json

RunStore is the only place that reads or writes these files.
The persisted RunRecord is always authoritative over Streamlit session state.

Fix 1 — Legacy adapter:
    migrate_legacy_artifact(artifact_name, root) reads the old scattered
    ArtifactStore layout (specs/, contracts/, reviews/) and assembles a
    RunRecord from those files.  Used once on first load of an old project.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .run_record import (
    RunRecord,
    ReviewResult,
    ReviewIssue,
    ImplementationReviewResult,
    ImplementationReviewFinding,
    RevisionPatch,
    CostRecord,
    TimestampRecord,
    VALID_STATUSES,
    _now,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid enum sets used during normalization (Fix 4)
# ---------------------------------------------------------------------------

_VALID_DECISIONS  = {"APPROVE", "REVISE", "BLOCKED", "PENDING", "UNKNOWN"}
_VALID_SEVERITIES = {"low", "medium", "high", "unknown"}
_VALID_SCOPES     = {"allowed", "forbidden"}
_VALID_TYPES      = {
    "ambiguity", "missing_definition", "implementation_risk",
    "contract_violation", "unknown",
}


class RunStore:
    """Save, load, list, and normalize RunRecord objects from disk."""

    def __init__(self, root: Path):
        self.runs_dir = root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Filename helpers
    # ------------------------------------------------------------------

    def _make_filename(self, run_id: str, artifact_name: str, created_at: str) -> str:
        """
        Format: 2026-03-27T11-40-12_pattern_engine_<short_id>.json
        Filenames sort chronologically; short_id makes them unique.
        """
        try:
            dt = datetime.fromisoformat(created_at)
            ts = dt.strftime("%Y-%m-%dT%H-%M-%S")
        except Exception:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")

        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", artifact_name)
        short_id = run_id[:8]
        return f"{ts}_{safe_name}_{short_id}.json"

    def _find_existing_path(self, run_id: str) -> Optional[Path]:
        """
        Return the existing file for this run_id, if any.
        Fast path: glob by short_id suffix (covers the common case).
        Slow path: read every file and compare run_id field.
        Prevents duplicate files when artifact_name is renamed.
        """
        short_id = run_id[:8]
        for candidate in self.runs_dir.glob(f"*_{short_id}.json"):
            return candidate
        for candidate in self.runs_dir.glob("*.json"):
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                if raw.get("run_id") == run_id:
                    return candidate
            except Exception:
                continue
        return None

    def _path_for(self, record: RunRecord) -> Path:
        """
        Return the canonical path for a record.
        Reuses the existing file (stable by run_id) when it exists,
        so artifact renames never create duplicate history files.
        """
        existing = self._find_existing_path(record.run_id)
        if existing is not None:
            return existing
        filename = self._make_filename(
            record.run_id,
            record.artifact_name,
            record.timestamps.created_at,
        )
        return self.runs_dir / filename

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(self, record: RunRecord) -> Path:
        """Persist a RunRecord to disk. Returns the file path written."""
        record.touch()
        path = self._path_for(record)
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self, path: Path) -> RunRecord:
        """Load and normalize a RunRecord from a file path."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_to_run_record(raw)

    def load_by_filename(self, filename: str) -> Optional[RunRecord]:
        path = self.runs_dir / filename
        if not path.exists():
            return None
        return self.load(path)

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_runs(self) -> list[dict]:
        """
        Return run summaries sorted newest-first.
        Corrupted or unreadable files are skipped silently.
        """
        summaries = []
        for path in sorted(self.runs_dir.glob("*.json"), reverse=True):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                summaries.append({
                    "filename":      path.name,
                    "run_id":        raw.get("run_id", ""),
                    "artifact_name": raw.get("artifact_name", ""),
                    "status":        raw.get("status", "drafted"),
                    "created_at":    raw.get("timestamps", {}).get("created_at", ""),
                    "updated_at":    raw.get("timestamps", {}).get("updated_at", ""),
                })
            except Exception:
                continue
        return summaries


# ---------------------------------------------------------------------------
# Fix 1 — Legacy migration adapter
#
# Reads the old ArtifactStore on-disk layout and assembles a RunRecord.
# Old layout (under workbench root):
#   specs/<artifact>.spec.md
#   contracts/<artifact>.contract.json
#   reviews/<artifact>.review.json        (or .review.loop0.json etc.)
#   workflow/<artifact>.workflow.json
#
# Also handles the old server.py review shape:
#   { "decision": ..., "rationale": ..., "findings": [...] }
# ---------------------------------------------------------------------------

def migrate_legacy_artifact(artifact_name: str, root: Path) -> Optional[RunRecord]:
    """
    Attempt to build a RunRecord from the old scattered ArtifactStore files.
    Returns None if no legacy data is found.
    Never raises — logs and returns None on any error.
    """
    try:
        spec_path     = root / "specs"    / f"{artifact_name}.spec.md"
        contract_path = root / "contracts" / f"{artifact_name}.contract.json"
        review_path   = root / "reviews"  / f"{artifact_name}.review.json"

        # Need at least a spec or contract to have something meaningful
        if not spec_path.exists() and not contract_path.exists():
            return None

        # --- spec ---
        architect_spec = ""
        if spec_path.exists():
            architect_spec = spec_path.read_text(encoding="utf-8")

        # --- contract ---
        locked_contract: dict = {}
        if contract_path.exists():
            try:
                locked_contract = json.loads(contract_path.read_text(encoding="utf-8"))
                if not isinstance(locked_contract, dict):
                    locked_contract = {}
            except Exception:
                locked_contract = {}

        # --- review (try canonical, then loop variants) ---
        review_raw: dict = {}
        if review_path.exists():
            try:
                review_raw = json.loads(review_path.read_text(encoding="utf-8"))
            except Exception:
                review_raw = {}
        else:
            # Try loop variants: .review.loop0.json, .review.loop1.json …
            for loop_file in sorted(
                (root / "reviews").glob(f"{artifact_name}.review.loop*.json"),
                reverse=True,
            ):
                try:
                    review_raw = json.loads(loop_file.read_text(encoding="utf-8"))
                    break
                except Exception:
                    continue

        # Normalise the review dict into our canonical shape
        review_result = _review_dict_to_review_result(review_raw)

        # --- timestamps: try workflow file, fallback to now ---
        now = _now()
        created_at = now
        workflow_path = root / "workflow" / f"{artifact_name}.workflow.json"
        if workflow_path.exists():
            try:
                wf = json.loads(workflow_path.read_text(encoding="utf-8"))
                created_at = str(wf.get("created_at") or now)
            except Exception:
                pass

        record = RunRecord(
            artifact_name=artifact_name,
            status="drafted",
            architect_spec=architect_spec,
            locked_contract=locked_contract,
            review_result=review_result,
            timestamps=TimestampRecord(created_at=created_at, updated_at=now),
            last_action=f"Migrated from legacy artifact: {artifact_name}",
        )
        logger.info("migrate_legacy_artifact: assembled RunRecord for %r", artifact_name)
        return record

    except Exception as exc:
        logger.error("migrate_legacy_artifact failed for %r: %s", artifact_name, exc)
        return None


def _review_dict_to_review_result(review_raw: dict) -> ReviewResult:
    """
    Convert any known review dict shape into a ReviewResult.
    Handles:
      - Current v3 shape:  { decision, issues: [...] }
      - Old server.py:     { decision, rationale, findings: [...] }
      - Old app.py:        { decision, issues: [...] }  (same keys, different field names in issues)
    """
    if not isinstance(review_raw, dict):
        return ReviewResult()

    decision = str(review_raw.get("decision") or "PENDING").upper()
    if decision not in _VALID_DECISIONS:
        decision = "PENDING"

    # Support both "issues" (v3) and "findings" (old server.py)
    issues_raw = review_raw.get("issues") or review_raw.get("findings") or []
    if not isinstance(issues_raw, list):
        issues_raw = []

    issues = [_normalize_issue(i, idx) for idx, i in enumerate(issues_raw)]
    return ReviewResult(decision=decision, issues=issues)


# ---------------------------------------------------------------------------
# Normalization — converts any partial payload into a canonical RunRecord
# ---------------------------------------------------------------------------

def _normalize_to_run_record(raw: dict) -> RunRecord:
    """
    Convert any dict (v3, legacy, or partial) into a valid RunRecord.
    Missing blocks are filled with safe defaults.
    Never raises; always returns a usable record.
    """
    if not isinstance(raw, dict):
        logger.warning("_normalize_to_run_record received non-dict; returning blank record")
        return RunRecord()

    # Scalar fields
    run_id        = str(raw.get("run_id") or uuid.uuid4())
    artifact_name = str(raw.get("artifact_name") or "untitled")
    architect_spec = str(raw.get("architect_spec") or "")
    implementation_spec = str(raw.get("implementation_spec") or "")
    execution_result = raw.get("execution_result") or {}
    implementation_review = _implementation_review_dict_to_result(
        raw.get("implementation_review")
    )
    last_action   = str(raw.get("last_action") or "Idle")

    if not isinstance(execution_result, dict):
        execution_result = {}

    locked_contract = raw.get("locked_contract") or {}
    if not isinstance(locked_contract, dict):
        locked_contract = {}

    metadata = raw.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    # Status
    status = str(raw.get("status") or "drafted").lower()
    if status not in VALID_STATUSES:
        logger.warning("_normalize_to_run_record: unknown status %r → drafted", status)
        status = "drafted"

    # Review result — support nested (v3) and top-level (older server.py)
    rr_raw = raw.get("review_result") or {}
    if not isinstance(rr_raw, dict):
        rr_raw = {}
    if not rr_raw and isinstance(raw.get("review"), dict):
        rr_raw = raw["review"]
    review_result = _review_dict_to_review_result(rr_raw)

    # Revision patches
    patches_raw = raw.get("revision_patches") or []
    if not isinstance(patches_raw, list):
        patches_raw = []
    patches: list[RevisionPatch] = []
    for p in patches_raw:
        if isinstance(p, dict):
            try:
                patches.append(RevisionPatch(
                    issue_id=str(p.get("issue_id") or ""),
                    status=str(p.get("status") or "addressed"),
                    change_summary=str(p.get("change_summary") or ""),
                ))
            except Exception as exc:
                logger.warning("Skipping malformed patch: %s", exc)

    # Cost
    cost_raw = raw.get("cost") or {}
    if not isinstance(cost_raw, dict):
        cost_raw = {}
    arch_in  = _safe_int(cost_raw.get("architect_input_tokens"))
    arch_out = _safe_int(cost_raw.get("architect_output_tokens"))
    rev_in   = _safe_int(cost_raw.get("reviewer_input_tokens"))
    rev_out  = _safe_int(cost_raw.get("reviewer_output_tokens"))
    stored_cost   = _safe_float(cost_raw.get("estimated_total_cost"))
    total_tokens  = arch_in + arch_out + rev_in + rev_out
    computed_cost = (arch_in + rev_in) * 3.0 / 1_000_000 + (arch_out + rev_out) * 15.0 / 1_000_000
    estimated_cost = stored_cost if stored_cost > 0.0 else (computed_cost if total_tokens > 0 else 0.0)
    cost = CostRecord(
        architect_input_tokens=arch_in,
        architect_output_tokens=arch_out,
        reviewer_input_tokens=rev_in,
        reviewer_output_tokens=rev_out,
        estimated_total_cost=estimated_cost,
        rerun_count=_safe_int(cost_raw.get("rerun_count")),
    )

    # Timestamps
    ts_raw = raw.get("timestamps") or {}
    if not isinstance(ts_raw, dict):
        ts_raw = {}
    now = _now()
    timestamps = TimestampRecord(
        created_at=str(ts_raw.get("created_at") or now),
        updated_at=str(ts_raw.get("updated_at") or now),
    )

    return RunRecord(
        run_id=run_id,
        artifact_name=artifact_name,
        status=status,           # type: ignore[arg-type]
        architect_spec=architect_spec,
        implementation_spec=implementation_spec,
        execution_result=execution_result,
        implementation_review=implementation_review,
        locked_contract=locked_contract,
        review_result=review_result,
        revision_patches=patches,
        cost=cost,
        timestamps=timestamps,
        metadata=metadata,
        last_action=last_action,
    )


# ---------------------------------------------------------------------------
# Fix 4 — Hardened issue normalization
# ---------------------------------------------------------------------------

def _normalize_issue(raw: object, idx: int) -> ReviewIssue:
    """
    Convert any value into a fully-populated ReviewIssue.

    Fix 4 guarantees:
    - Every field is always a non-empty string.
    - severity, scope, type are clamped to valid enum values.
    - id is always unique (falls back to positional index).
    - No path can produce a partial issue that breaks UI rendering.

    Also handles older field name aliases:
        title        → id
        detail       → message
        suggested_fix → proposed_fix
    """
    fallback_id = f"issue_{idx + 1:03d}"

    if not isinstance(raw, dict):
        return ReviewIssue(
            id=fallback_id,
            severity="unknown",
            type="unknown",
            scope="allowed",
            target="spec",
            message=str(raw) if raw else "(no message)",
            proposed_fix="No proposed fix provided.",
        )

    # Resolve field aliases (old server.py shape)
    issue_id     = _nonempty(raw.get("id"), raw.get("title"), fallback_id)
    message      = _nonempty(raw.get("message"), raw.get("detail"), "(no message)")
    proposed_fix = _nonempty(
        raw.get("proposed_fix"), raw.get("suggested_fix"), "No proposed fix provided."
    )

    # Clamp enum fields to valid values
    severity = str(raw.get("severity") or "unknown").lower()
    if severity not in _VALID_SEVERITIES:
        severity = "unknown"

    scope = str(raw.get("scope") or "allowed").lower()
    if scope not in _VALID_SCOPES:
        scope = "allowed"

    issue_type = str(raw.get("type") or "unknown").lower()
    if issue_type not in _VALID_TYPES:
        issue_type = "unknown"

    target = str(raw.get("target") or "spec").strip() or "spec"

    return ReviewIssue(
        id=str(issue_id),
        severity=severity,
        type=issue_type,
        scope=scope,
        target=target,
        message=str(message),
        proposed_fix=str(proposed_fix),
    )


def _implementation_review_dict_to_result(raw: object) -> ImplementationReviewResult | None:
    """
    Convert any implementation review payload into a normalized persisted form.
    Missing or malformed values safely normalize to None or empty/default fields.
    """
    if raw is None or raw == "":
        return None
    if not isinstance(raw, dict):
        return None

    decision = str(raw.get("decision") or "").upper().strip()
    if decision not in {"APPROVE", "FIX_REQUIRED"}:
        decision = ""

    findings_raw = raw.get("findings") or []
    if not isinstance(findings_raw, list):
        findings_raw = []

    findings: list[ImplementationReviewFinding] = []
    for idx, item in enumerate(findings_raw):
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "medium").lower().strip()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        findings.append(
            ImplementationReviewFinding(
                id=str(item.get("id") or f"impl_{idx + 1:03d}"),
                severity=severity,
                target=str(item.get("target") or "implementation"),
                message=str(item.get("message") or "(no message)"),
                required_fix=str(item.get("required_fix") or ""),
            )
        )

    return ImplementationReviewResult(
        decision=decision,
        findings=findings,
        summary=str(raw.get("summary") or ""),
    )


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _nonempty(*candidates) -> object:
    """Return the first candidate that is a non-empty, non-None value."""
    for c in candidates:
        if c is not None and str(c).strip():
            return c
    return candidates[-1]  # last candidate is always the fallback


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
