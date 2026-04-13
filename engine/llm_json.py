from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMJSONParseError(ValueError):
    message: str
    preview: str

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.message} Raw preview (first ~500 chars):\n{self.preview}"


def _preview(text: str, limit: int = 500) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "…"


def _strip_outer_code_fence(text: str) -> str:
    """
    If the entire payload is a single ``` fenced block, unwrap it.
    Handles ```json, ```JSON, and trailing ``` fences.
    """
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if len(lines) < 3:
        return t
    # Drop opening fence (``` or ```json)
    lines = lines[1:]
    # Drop closing fence if present
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_first_fenced_block(text: str) -> str | None:
    """
    Extract the first triple-backtick block content, if present.
    Useful for responses like:
      Here is the result:
      ```json
      {...}
      ```
    """
    t = text or ""
    start = t.find("```")
    if start == -1:
        return None
    end = t.find("```", start + 3)
    if end == -1:
        return None
    block = t[start : end + 3]
    return _strip_outer_code_fence(block)


def _extract_first_balanced_json_object(text: str) -> str | None:
    """
    Return the first balanced {...} candidate, respecting JSON strings.
    This avoids naive slicing from first '{' to last '}'.
    """
    s = text or ""
    in_string = False
    escape = False
    depth = 0
    start_idx: int | None = None

    for i, ch in enumerate(s):
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        # not in string
        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
            continue
        if ch == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start_idx is not None:
                return s[start_idx : i + 1]
            continue

    return None


def parse_llm_json_response(raw_text: str) -> dict[str, Any]:
    """
    Robustly parse a single JSON object from an LLM response.

    Strategy:
      1) direct json.loads(raw_text)
      2) unwrap outer ``` fenced block, retry
      3) extract first ``` fenced block content, retry
      4) extract first balanced {...} object, retry
      5) raise LLMJSONParseError with a preview
    """
    raw = (raw_text or "").strip()
    if not raw:
        raise LLMJSONParseError("LLM returned empty output; expected JSON object.", preview="")

    def _try_load(candidate: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    parsed = _try_load(raw)
    if parsed is not None:
        return parsed

    unwrapped = _strip_outer_code_fence(raw)
    if unwrapped != raw:
        parsed = _try_load(unwrapped)
        if parsed is not None:
            return parsed

    fenced = _extract_first_fenced_block(raw)
    if fenced:
        parsed = _try_load(fenced)
        if parsed is not None:
            return parsed

    candidate = _extract_first_balanced_json_object(raw)
    if candidate:
        parsed = _try_load(candidate)
        if parsed is not None:
            return parsed

    raise LLMJSONParseError("Failed to parse JSON object from LLM response.", preview=_preview(raw))

