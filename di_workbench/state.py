
from __future__ import annotations

from typing import Any, Dict


def build_workbench_state(
    architect_spec: Dict[str, Any] | None = None,
    contract: Dict[str, Any] | None = None,
    review: Dict[str, Any] | None = None,
    diff: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "architect_spec": architect_spec or {},
        "contract": contract or {},
        "review": review or {},
        "diff": diff or {},
    }


def update_state_section(
    state: Dict[str, Any],
    section: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    if section not in {"architect_spec", "contract", "review", "diff"}:
        raise ValueError(f"Unsupported section: {section}")

    next_state = dict(state)
    next_state[section] = payload
    return next_state
