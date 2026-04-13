"""
contract_validator.py
Validates that a contract file is well-formed and enforces
locked field / forbidden change rules against proposed revisions.
Also provides strict reviewer JSON schema validation.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path


REQUIRED_CONTRACT_FIELDS = {
    "artifact",
    "schema_locked",
    "locked_fields",
    "forbidden_changes",
    "review_scope",
}

REQUIRED_ISSUE_FIELDS = {
    "id", "type", "severity", "scope", "target", "message", "proposed_fix"
}

VALID_DECISIONS = {"APPROVE", "REVISE", "BLOCKED"}
VALID_SEVERITIES = {"low", "medium", "high"}
VALID_SCOPES = {"allowed", "forbidden"}
VALID_TYPES = {"ambiguity", "missing_definition", "implementation_risk", "contract_violation"}

OPTIONALITY_PATTERNS = [
    "optional",
    "if applicable",
    "may be omitted",
    "not required",
    "when available",
    "can be null",
    "deprecated",
]


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class ContractValidator:
    """
    Validates contract structure and checks proposed changes
    against locked field / forbidden change rules.
    """

    # ── Contract schema ───────────────────────────────────────────────────────

    def validate_contract_schema(self, contract: dict) -> ValidationResult:
        errors = []
        warnings = []

        missing = REQUIRED_CONTRACT_FIELDS - set(contract.keys())
        if missing:
            errors.append(f"Contract missing required fields: {sorted(missing)}")

        if "schema_locked" in contract and not isinstance(contract["schema_locked"], bool):
            errors.append("schema_locked must be a boolean")

        if "locked_fields" in contract:
            if not isinstance(contract["locked_fields"], list):
                errors.append("locked_fields must be a list")
            elif len(contract["locked_fields"]) == 0:
                warnings.append("locked_fields is empty — no fields are protected")

        if "forbidden_changes" in contract:
            if not isinstance(contract["forbidden_changes"], list):
                errors.append("forbidden_changes must be a list")
            elif len(contract["forbidden_changes"]) == 0:
                warnings.append("forbidden_changes is empty — no mutations are forbidden")

        if "review_scope" in contract:
            if not isinstance(contract["review_scope"], list):
                errors.append("review_scope must be a list")
            elif len(contract["review_scope"]) == 0:
                warnings.append("review_scope is empty — reviewer has no defined scope")

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    # ── Reviewer JSON schema (strict) ─────────────────────────────────────────

    def validate_review_schema(self, review: dict) -> ValidationResult:
        """
        Strictly validates reviewer output structure and enum values.
        Must pass before the review reaches the diff guard.
        Returns invalid if any field is missing, wrong type, or out-of-enum.
        """
        errors = []
        warnings = []

        if "decision" not in review:
            errors.append("Missing required field: 'decision'")
        else:
            if review["decision"] not in VALID_DECISIONS:
                errors.append(
                    f"Invalid decision '{review['decision']}'. "
                    f"Must be one of: {sorted(VALID_DECISIONS)}"
                )

        if "issues" not in review:
            errors.append("Missing required field: 'issues'")
        elif not isinstance(review["issues"], list):
            errors.append("'issues' must be a list")
        else:
            seen_ids = []
            for i, issue in enumerate(review["issues"]):
                prefix = f"issues[{i}]"
                if not isinstance(issue, dict):
                    errors.append(f"{prefix}: must be an object")
                    continue

                missing = REQUIRED_ISSUE_FIELDS - set(issue.keys())
                if missing:
                    errors.append(f"{prefix}: missing fields {sorted(missing)}")

                if "severity" in issue and issue["severity"] not in VALID_SEVERITIES:
                    errors.append(
                        f"{prefix}: invalid severity '{issue['severity']}'. "
                        f"Must be one of: {sorted(VALID_SEVERITIES)}"
                    )

                if "scope" in issue and issue["scope"] not in VALID_SCOPES:
                    errors.append(
                        f"{prefix}: invalid scope '{issue['scope']}'. "
                        f"Must be one of: {sorted(VALID_SCOPES)}"
                    )

                if "type" in issue and issue["type"] not in VALID_TYPES:
                    errors.append(
                        f"{prefix}: invalid type '{issue['type']}'. "
                        f"Must be one of: {sorted(VALID_TYPES)}"
                    )

                if "id" in issue:
                    seen_ids.append(issue["id"])

                if "proposed_fix" in issue:
                    proposed_fix = issue["proposed_fix"]
                    if not isinstance(proposed_fix, str) or not proposed_fix.strip():
                        errors.append(
                            f"{prefix}: 'proposed_fix' must be a non-empty string"
                        )

            duplicate_ids = sorted({iid for iid in seen_ids if seen_ids.count(iid) > 1})
            if duplicate_ids:
                errors.append(
                    f"Duplicate issue IDs detected: {duplicate_ids}. "
                    f"All issue IDs must be unique."
                )

        if review.get("decision") == "APPROVE" and review.get("issues"):
            high_issues = [
                iss for iss in review["issues"]
                if isinstance(iss, dict) and iss.get("severity") == "high"
            ]
            if high_issues:
                warnings.append(
                    "decision=APPROVE but high-severity issues present — "
                    "system will override decision based on diff result."
                )

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    # ── Locked field checks ───────────────────────────────────────────────────

    def check_field_locked(self, field_path: str, contract: dict) -> bool:
        """
        Returns True if a given field path is covered by a locked_fields entry.
        Supports wildcard patterns like canonical_record.metrics.<n>.value
        Matching is case-insensitive.
        """
        field_path = field_path.lower()
        locked = contract.get("locked_fields", [])
        for pattern in locked:
            pattern = pattern.lower()
            parts = re.split(r"<[^>]+>", pattern)
            regex = r"[^.]+".join(re.escape(p) for p in parts)
            if re.fullmatch(regex, field_path):
                return True
        return False

    def check_change_forbidden(self, change_type: str, contract: dict) -> bool:
        return change_type in contract.get("forbidden_changes", [])

    def check_in_review_scope(self, scope_item: str, contract: dict) -> bool:
        return scope_item in contract.get("review_scope", [])

    # ── Spec diff validation ──────────────────────────────────────────────────

    def _has_optionality_near_token(self, text: str, token: str, window_words: int = 20) -> bool:
        """
        Detects semantic weakening around a locked token, e.g.:
        'output.score (optional)' or 'output.score may be omitted'
        """
        lowered_text = text.lower()
        lowered_token = token.lower()

        words = lowered_text.split()
        token_variants = {lowered_token, lowered_token.split(".")[-1]}

        for i, word in enumerate(words):
            cleaned = re.sub(r"[^\w.]", "", word)
            if cleaned in token_variants or lowered_token in cleaned:
                start = max(0, i - window_words)
                end = min(len(words), i + window_words + 1)
                context = " ".join(words[start:end])
                if any(pattern in context for pattern in OPTIONALITY_PATTERNS):
                    return True
        return False

    def validate_spec_revision(
        self, previous_spec: str, revised_spec: str, contract: dict
    ) -> ValidationResult:
        """
        Compares previous and revised spec text to detect changes that would
        violate the locked contract. Checks for removed locked field tokens,
        semantic weakening near locked fields, removed sections, and suspicious
        length reduction.
        """
        errors = []
        warnings = []

        locked_fields = contract.get("locked_fields", [])

        locked_tokens: set[str] = set()
        for pattern in locked_fields:
            parts = re.split(r"[.<>]", pattern)
            for p in parts:
                if p and len(p) > 1 and not re.fullmatch(r"[a-z]", p):
                    locked_tokens.add(p)

        prev_lower = previous_spec.lower()
        revised_lower = revised_spec.lower()

        for token in locked_tokens:
            token_lower = token.lower()
            if token_lower in prev_lower and token_lower not in revised_lower:
                errors.append(
                    f"Locked field token '{token}' was present in previous spec "
                    f"but is absent in revised spec. Possible removal or rename."
                )
            elif token_lower in revised_lower and self._has_optionality_near_token(revised_spec, token):
                errors.append(
                    f"Locked field token '{token}' is present but weakened by optional/hedged "
                    f"language in revised spec."
                )

        prev_headings = [
            line.strip().lower()
            for line in previous_spec.splitlines()
            if line.strip().startswith("#")
        ]
        revised_headings = [
            line.strip().lower()
            for line in revised_spec.splitlines()
            if line.strip().startswith("#")
        ]
        removed_headings = set(prev_headings) - set(revised_headings)
        if removed_headings:
            warnings.append(
                f"Section heading(s) present in previous spec but absent in revision: "
                f"{sorted(removed_headings)}. Verify no required sections were dropped."
            )

        prev_section_count = sum(
            1 for line in previous_spec.splitlines() if line.strip().startswith("##")
        )
        revised_section_count = sum(
            1 for line in revised_spec.splitlines() if line.strip().startswith("##")
        )
        if revised_section_count < prev_section_count:
            warnings.append(
                f"Revised spec has fewer sections than previous "
                f"({revised_section_count} vs {prev_section_count}). "
                f"Possible structural weakening."
            )

        prev_len = len(previous_spec.strip())
        revised_len = len(revised_spec.strip())
        if prev_len > 0 and revised_len < prev_len * 0.75:
            errors.append(
                f"Revised spec is significantly shorter than previous "
                f"({revised_len} vs {prev_len} chars, "
                f">{round((1 - revised_len/prev_len)*100)}% reduction). "
                f"Possible output contract weakening."
            )

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    # ── Contract-level review validation ─────────────────────────────────────

    def validate_review_against_contract(
        self, review: dict, contract: dict
    ) -> ValidationResult:
        errors = []
        warnings = []

        issues = review.get("issues", [])
        for issue in issues:
            issue_id = issue.get("id", "?")
            scope = issue.get("scope", "")
            issue_type = issue.get("type", "")
            target = issue.get("target", "")

            if scope == "forbidden":
                errors.append(
                    f"[{issue_id}] Reviewer self-reported forbidden scope on '{target}' "
                    f"(type: {issue_type}). This issue must be rejected."
                )

            if target and self.check_field_locked(target, contract):
                if scope != "forbidden":
                    errors.append(
                        f"[{issue_id}] Issue targets locked field '{target}' "
                        f"but scope is not marked forbidden. Reclassifying as forbidden."
                    )

            if issue_type == "contract_violation":
                errors.append(
                    f"[{issue_id}] Issue type 'contract_violation' detected on '{target}'. "
                    f"Reviewer may not propose changes to contract structure."
                )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    # ── File I/O ──────────────────────────────────────────────────────────────

    def load_contract(self, artifact: str, contracts_dir: Path) -> dict:
        path = contracts_dir / f"{artifact}.contract.json"
        if not path.exists():
            raise FileNotFoundError(f"No contract found for artifact '{artifact}' at {path}")
        with open(path) as f:
            return json.load(f)

    def save_contract(self, contract: dict, contracts_dir: Path) -> Path:
        artifact = contract.get("artifact")
        if not artifact:
            raise ValueError("Contract must have an 'artifact' field")
        path = contracts_dir / f"{artifact}.contract.json"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(contract, f, indent=2)
        return path