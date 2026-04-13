"""
artifact_store.py
Durable file-based artifact persistence.
Each artifact type gets its own subdirectory under the workbench root.

Contracts support versioning: each save writes a versioned backup
(e.g. pattern_engine.contract.v1.json) while maintaining the canonical
latest path (pattern_engine.contract.json) for normal loads.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


ARTIFACT_DIRS = {
    "spec": "specs",
    "contract": "contracts",
    "review": "reviews",
    "diff": "diffs",
    "handoff": "handoffs",
    "workflow": "workflow",
}


class ArtifactStore:
    """
    Reads and writes named artifact files.
    All artifacts are stored as JSON or Markdown under the workbench root.
    """

    def __init__(self, root: Path):
        self.root = root
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for subdir in ARTIFACT_DIRS.values():
            (self.root / subdir).mkdir(parents=True, exist_ok=True)

    def _path(self, artifact: str, kind: str, ext: str = "json") -> Path:
        subdir = ARTIFACT_DIRS[kind]
        return self.root / subdir / f"{artifact}.{kind}.{ext}"

    # ── SPEC ──────────────────────────────────────────────────────────

    def save_spec(self, artifact: str, spec_text: str, revision: int = 0) -> Path:
        path = self._path(artifact, "spec", "md")
        if path.exists() and revision > 0:
            backup = self.root / "specs" / f"{artifact}.spec.r{revision - 1}.md"
            shutil.copy(path, backup)
        path.write_text(spec_text, encoding="utf-8")
        return path

    def load_spec(self, artifact: str) -> Optional[str]:
        path = self._path(artifact, "spec", "md")
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # ── CONTRACT (versioned) ──────────────────────────────────────────
    #
    # Canonical path: <artifact>.contract.json  (always latest)
    # Versioned backup: <artifact>.contract.v<N>.json
    #
    # Version number is derived from how many versioned backups already
    # exist for this artifact, so it always increments correctly even
    # across process restarts.

    def _next_contract_version(self, artifact: str) -> int:
        """Return the next version integer (1-based) for this artifact's contract."""
        contracts_dir = self.root / "contracts"
        existing = list(contracts_dir.glob(f"{artifact}.contract.v*.json"))
        return len(existing) + 1

    def save_contract(self, artifact: str, contract: dict) -> Path:
        """
        Save contract to canonical path and write a versioned backup.
        Adds 'version' and 'saved_at' metadata into the contract object.
        """
        version = self._next_contract_version(artifact)
        contract = dict(contract)  # avoid mutating caller's dict
        contract["version"] = version
        contract["saved_at"] = datetime.utcnow().isoformat()

        contracts_dir = self.root / "contracts"
        serialized = json.dumps(contract, indent=2)

        # Versioned backup
        versioned_path = contracts_dir / f"{artifact}.contract.v{version}.json"
        versioned_path.write_text(serialized, encoding="utf-8")

        # Canonical latest
        latest_path = self._path(artifact, "contract")
        latest_path.write_text(serialized, encoding="utf-8")

        return latest_path

    def load_contract(self, artifact: str) -> Optional[dict]:
        """Load the canonical (latest) contract. Backward compatible."""
        path = self._path(artifact, "contract")
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    def load_contract_version(self, artifact: str, version: int) -> Optional[dict]:
        """Load a specific versioned contract backup."""
        path = self.root / "contracts" / f"{artifact}.contract.v{version}.json"
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    def list_contract_versions(self, artifact: str) -> list[int]:
        """Return sorted list of available contract version numbers."""
        contracts_dir = self.root / "contracts"
        versions = []
        for p in contracts_dir.glob(f"{artifact}.contract.v*.json"):
            try:
                v = int(p.stem.split(".v")[-1])
                versions.append(v)
            except ValueError:
                pass
        return sorted(versions)

    # ── REVIEW ────────────────────────────────────────────────────────

    def save_review(self, artifact: str, review: dict, loop: int = 0) -> Path:
        review["saved_at"] = datetime.utcnow().isoformat()
        review["loop"] = loop
        path = self.root / "reviews" / f"{artifact}.review.loop{loop}.json"
        path.write_text(json.dumps(review, indent=2), encoding="utf-8")
        latest = self._path(artifact, "review")
        latest.write_text(json.dumps(review, indent=2), encoding="utf-8")
        return latest

    def load_review(self, artifact: str) -> Optional[dict]:
        path = self._path(artifact, "review")
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    # ── DIFF ──────────────────────────────────────────────────────────

    def save_diff(self, artifact: str, diff: dict, loop: int = 0) -> Path:
        diff["saved_at"] = datetime.utcnow().isoformat()
        diff["loop"] = loop
        path = self.root / "diffs" / f"{artifact}.diff.loop{loop}.json"
        path.write_text(json.dumps(diff, indent=2), encoding="utf-8")
        latest = self._path(artifact, "diff")
        latest.write_text(json.dumps(diff, indent=2), encoding="utf-8")
        return latest

    def load_diff(self, artifact: str) -> Optional[dict]:
        path = self._path(artifact, "diff")
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    # ── HANDOFF ───────────────────────────────────────────────────────

    def save_handoff(self, artifact: str, handoff: dict) -> Path:
        handoff["saved_at"] = datetime.utcnow().isoformat()
        path = self._path(artifact, "handoff")
        path.write_text(json.dumps(handoff, indent=2), encoding="utf-8")
        return path

    def load_handoff(self, artifact: str) -> Optional[dict]:
        path = self._path(artifact, "handoff")
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    # ── WORKFLOW STATE ────────────────────────────────────────────────

    def save_workflow(self, artifact: str, record: dict) -> Path:
        path = self.root / "workflow" / f"{artifact}.workflow.json"
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return path

    def load_workflow(self, artifact: str) -> Optional[dict]:
        path = self.root / "workflow" / f"{artifact}.workflow.json"
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    # ── SNAPSHOT ──────────────────────────────────────────────────────

    def snapshot(self, artifact: str) -> dict:
        """Return all current artifacts for a given artifact name."""
        return {
            "artifact": artifact,
            "spec": self.load_spec(artifact),
            "contract": self.load_contract(artifact),
            "review": self.load_review(artifact),
            "diff": self.load_diff(artifact),
            "handoff": self.load_handoff(artifact),
            "workflow": self.load_workflow(artifact),
        }

    def list_artifacts(self) -> list[str]:
        """Return list of artifact names that have at least a spec or contract."""
        names = set()
        for kind in ("spec", "contract"):
            subdir = ARTIFACT_DIRS[kind]
            for f in (self.root / subdir).glob(f"*.{kind}.*"):
                # Exclude versioned backups from artifact name extraction
                if ".v" in f.stem.split(f".{kind}")[-1]:
                    continue
                name = f.name.split(f".{kind}.")[0]
                names.add(name)
        return sorted(names)
