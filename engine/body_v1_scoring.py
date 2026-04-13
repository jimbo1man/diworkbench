"""
body_v1_scoring.py

Deterministic Body Scoring v1 for Lucy Body records.

This scorer consumes only the current Body v1 metrics while leaving the
canonical ``metrics`` container open for future metrics that are not yet part
of the scoring model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .body_v1_validator import validate_body_v1_record


SLEEP_WEIGHT = 0.45
WORKOUT_WEIGHT = 0.30
MEDITATION_WEIGHT = 0.25

BODY_V1_SCORING_METRICS = ("sleep_hours", "workout_completed", "meditation_minutes")


@dataclass(frozen=True)
class BodyV1ScoreResult:
    sleep_score: float | None
    workout_score: float | None
    meditation_score: float | None
    daily_score: float | None
    status: str


def score_body_v1(payload: dict[str, Any]) -> BodyV1ScoreResult:
    """
    Score a normalized Body v1 record.

    Validation is intentionally separate from scoring, but the scorer refuses to
    process invalid input so callers get a clean deterministic failure instead of
    silently scoring malformed data.
    """
    validation = validate_body_v1_record(payload)
    if not validation.valid:
        joined_errors = "; ".join(validation.errors)
        raise ValueError(f"Invalid Body v1 record: {joined_errors}")

    record = validation.normalized_record
    metrics = record["metrics"]

    sleep_hours = metrics["sleep_hours"]["value"]
    workout_completed = metrics["workout_completed"]["value"]
    meditation_minutes = metrics["meditation_minutes"]["value"]

    sleep_score = _score_sleep(sleep_hours)
    workout_score = _score_workout(workout_completed)
    meditation_score = _score_meditation(meditation_minutes)

    available_scores = [
        (sleep_score, SLEEP_WEIGHT),
        (workout_score, WORKOUT_WEIGHT),
        (meditation_score, MEDITATION_WEIGHT),
    ]
    available_non_null = [(score, weight) for score, weight in available_scores if score is not None]

    if not available_non_null:
        return BodyV1ScoreResult(
            sleep_score=sleep_score,
            workout_score=workout_score,
            meditation_score=meditation_score,
            daily_score=None,
            status="insufficient_data",
        )

    weight_sum = sum(weight for _, weight in available_non_null)
    daily_score = sum(score * weight for score, weight in available_non_null) / weight_sum

    return BodyV1ScoreResult(
        sleep_score=sleep_score,
        workout_score=workout_score,
        meditation_score=meditation_score,
        daily_score=round(daily_score, 1),
        status="scored",
    )


def _score_sleep(sleep_hours: float | int | None) -> float | None:
    if sleep_hours is None:
        return None

    sleep_hours = _clamp(float(sleep_hours), 0.0, 12.0)

    if sleep_hours < 4:
        score = 0.0
    elif 4 <= sleep_hours < 6:
        score = 40 + ((sleep_hours - 4) / 2) * 30
    elif 6 <= sleep_hours <= 8:
        score = 70 + ((sleep_hours - 6) / 2) * 30
    elif 8 < sleep_hours <= 9:
        score = 100 - ((sleep_hours - 8) / 1) * 10
    else:
        score = 90 - ((sleep_hours - 9) / 3) * 30

    return round(score, 1)


def _score_workout(workout_completed: bool | None) -> float | None:
    if workout_completed is None:
        return None
    return 100.0 if workout_completed is True else 0.0


def _score_meditation(meditation_minutes: float | int | None) -> float | None:
    if meditation_minutes is None:
        return None

    meditation_minutes = _clamp(float(meditation_minutes), 0.0, 60.0)

    if meditation_minutes <= 0:
        score = 0.0
    elif 0 < meditation_minutes < 10:
        score = (meditation_minutes / 10) * 60
    elif 10 <= meditation_minutes < 20:
        score = 60 + ((meditation_minutes - 10) / 10) * 25
    elif 20 <= meditation_minutes <= 30:
        score = 85 + ((meditation_minutes - 20) / 10) * 15
    else:
        score = 100.0

    return round(score, 1)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
