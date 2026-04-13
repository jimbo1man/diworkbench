"""
body_v1_insights.py

Minimal deterministic insight generation for Lucy Body Scoring v1.
"""

from __future__ import annotations

from typing import Any

from .body_v1_scoring import BodyV1ScoreResult
from .body_v1_validator import validate_body_v1_record


def compute_body_v1_insights(
    payload: dict[str, Any],
    score_result: BodyV1ScoreResult,
) -> list[str]:
    """
    Generate up to three concise Body v1 insights from validated data and
    explicit score outputs.
    """
    validation = validate_body_v1_record(payload)
    if not validation.valid:
        joined_errors = "; ".join(validation.errors)
        raise ValueError(f"Invalid Body v1 record: {joined_errors}")

    if score_result.daily_score is None:
        return ["Not enough data to calculate today's score."]

    metrics = validation.normalized_record["metrics"]
    tracked_values = [
        metrics["sleep_hours"]["value"],
        metrics["workout_completed"]["value"],
        metrics["meditation_minutes"]["value"],
    ]

    insights: list[str] = []

    if score_result.sleep_score is not None and score_result.sleep_score < 70:
        insights.append("Sleep was the biggest drag on today's score.")

    if score_result.workout_score == 0:
        insights.append("No workout was recorded today.")

    if score_result.meditation_score is not None and score_result.meditation_score < 60:
        insights.append("A short meditation session could meaningfully improve consistency.")

    if score_result.daily_score >= 85:
        insights.append("Strong day across the tracked body metrics.")

    if any(value is None for value in tracked_values):
        insights.append("Today's score was calculated from partial data.")

    return insights[:3]
