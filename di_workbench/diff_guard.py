
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set


def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    flattened: Dict[str, Any] = {}

    if isinstance(obj, dict):
        for key, value in obj.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten(value, next_prefix))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            next_prefix = f"{prefix}[{index}]"
            flattened.update(_flatten(value, next_prefix))
    else:
        flattened[prefix] = obj

    return flattened


def _path_is_allowed(path: str, allowed_paths: Set[str]) -> bool:
    if not allowed_paths:
        return False
    return any(path == p or path.startswith(f"{p}.") or path.startswith(f"{p}[") for p in allowed_paths)


def classify_diff(
    original: Dict[str, Any],
    revised: Dict[str, Any],
    allowed_paths: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    allowed = set(allowed_paths or [])
    original_flat = _flatten(original)
    revised_flat = _flatten(revised)

    issues: List[Dict[str, Any]] = []

    all_paths = sorted(set(original_flat.keys()) | set(revised_flat.keys()))

    for path in all_paths:
        old_exists = path in original_flat
        new_exists = path in revised_flat

        if not old_exists and new_exists:
            if not _path_is_allowed(path, allowed):
                issues.append({
                    "severity": "high",
                    "scope": "FORBIDDEN",
                    "issue_type": "unauthorized_addition",
                    "path": path,
                    "message": f"Added unauthorized field/value at '{path}'.",
                })
            continue

        if old_exists and not new_exists:
            if not _path_is_allowed(path, allowed):
                issues.append({
                    "severity": "high",
                    "scope": "FORBIDDEN",
                    "issue_type": "unauthorized_removal",
                    "path": path,
                    "message": f"Removed protected field/value at '{path}'.",
                })
            continue

        if original_flat[path] != revised_flat[path] and not _path_is_allowed(path, allowed):
            issues.append({
                "severity": "medium",
                "scope": "FORBIDDEN",
                "issue_type": "unauthorized_modification",
                "path": path,
                "message": f"Modified protected field/value at '{path}'.",
            })

    status = "violation" if issues else "clean"
    summary = "Scope violations detected." if issues else "No scope violations detected."

    return {
        "status": status,
        "summary": summary,
        "issues": issues,
    }
