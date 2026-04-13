"""
body_v1_validator.py

Canonical model enforcement for Lucy Body v1 records.

The ``metrics`` container remains extensible for future Apple Health metrics,
but Body v1 explicitly requires and validates only the current scoring inputs:
sleep_hours, workout_completed, and meditation_minutes.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any


REQUIRED_TOP_LEVEL_KEYS = {"date", "habits", "metrics", "metadata"}
REQUIRED_BODY_V1_METRICS = (
    "sleep_hours",
    "workout_completed",
    "meditation_minutes",
)
ALLOWED_SOURCE_VALUES = {"imported", "manual"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class BodyV1ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    normalized_record: dict[str, Any] = field(default_factory=dict)


def normalize_body_v1_record(payload: Any) -> dict[str, Any]:
    """
    Return a normalized copy of the input record.

    Normalization rules:
    - empty strings become None
    - 0 is preserved as a real value
    - False is preserved as a real value
    - nested dict/list structure is preserved
    """
    normalized = deepcopy(payload)
    return _normalize_value(normalized)


def validate_body_v1_record(payload: Any) -> BodyV1ValidationResult:
    """
    Validate a Body v1 record after normalization.

    The validator locks the current canonical shape for Body v1 required inputs
    without assuming the ``metrics`` container can never grow in the future.
    Extra metrics are allowed and ignored by Body v1 validation/scoring.
    """
    normalized = normalize_body_v1_record(payload)
    errors: list[str] = []

    if not isinstance(normalized, dict):
        return BodyV1ValidationResult(
            valid=False,
            errors=["Body v1 record must be an object."],
            normalized_record={},
        )

    missing_top_level = REQUIRED_TOP_LEVEL_KEYS - set(normalized.keys())
    if missing_top_level:
        errors.append(f"Missing top-level keys: {sorted(missing_top_level)}")

    date_value = normalized.get("date")
    if not isinstance(date_value, str) or not _DATE_RE.fullmatch(date_value):
        errors.append("date must be a string in YYYY-MM-DD format.")

    habits = normalized.get("habits")
    if not isinstance(habits, dict):
        errors.append("habits must be an object.")

    metrics = normalized.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics must be an object.")
        metrics = {}

    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("metadata must be an object.")

    if isinstance(metrics, dict):
        _validate_required_metric(
            metrics,
            "sleep_hours",
            errors,
            allow_bool=False,
            accepted_types=(int, float),
        )
        _validate_required_metric(
            metrics,
            "workout_completed",
            errors,
            allow_bool=True,
            accepted_types=(bool,),
        )
        _validate_required_metric(
            metrics,
            "meditation_minutes",
            errors,
            allow_bool=False,
            accepted_types=(int, float),
        )

    return BodyV1ValidationResult(
        valid=not errors,
        errors=errors,
        normalized_record=normalized,
    )


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped == "" else stripped
    return value


def _validate_required_metric(
    metrics: dict[str, Any],
    metric_name: str,
    errors: list[str],
    *,
    allow_bool: bool,
    accepted_types: tuple[type, ...],
) -> None:
    metric_obj = metrics.get(metric_name)
    if not isinstance(metric_obj, dict):
        errors.append(f"metrics.{metric_name} must be an object.")
        return

    if "value" not in metric_obj:
        errors.append(f"metrics.{metric_name}.value is required.")
    else:
        _validate_metric_value(
            metric_name,
            metric_obj.get("value"),
            errors,
            allow_bool=allow_bool,
            accepted_types=accepted_types,
        )

    source = metric_obj.get("source")
    if source not in ALLOWED_SOURCE_VALUES:
        errors.append(
            f"metrics.{metric_name}.source must be one of {sorted(ALLOWED_SOURCE_VALUES)}."
        )


def _validate_metric_value(
    metric_name: str,
    value: Any,
    errors: list[str],
    *,
    allow_bool: bool,
    accepted_types: tuple[type, ...],
) -> None:
    if value is None:
        return

    if allow_bool:
        if isinstance(value, bool):
            return
        errors.append(f"metrics.{metric_name}.value must be boolean or null.")
        return

    if isinstance(value, bool):
        errors.append(f"metrics.{metric_name}.value must be numeric or null.")
        return

    if not isinstance(value, accepted_types):
        errors.append(f"metrics.{metric_name}.value must be numeric or null.")
