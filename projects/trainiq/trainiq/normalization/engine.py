"""
trainiq.normalization.engine — Epic 6, slice 5: the Normalization Engine

One pipeline, two canonical record kinds — per the Chief Architect's
explicit framing after slice 1. This module is the single place that
combines the three independent pieces built in slices 2-4 (taxonomy,
confidence, training load) into a record ready for persistence. It does
not itself decide WHERE that record is persisted — that's
SynchronizationEngine's job (slice 5's other half), using `record_kind`
alone to route, never by inspecting which keys happen to be present.

ACTIVITY-shaped input -> normalized_activities row (discipline mapped,
confidence scored, load computed-or-Unknown).
WEIGH_IN-shaped input -> weigh_ins row (near-identical pass-through — no
discipline, no load; Milestone 3 §4 established weigh-in data doesn't need
either).
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from trainiq.athlete.profile import AthleteProfile
from trainiq.connectors.base import RecordKind
from trainiq.normalization.confidence import compute_source_confidence
from trainiq.normalization.load import compute_training_load
from trainiq.normalization.plausibility import evaluate_weigh_in_plausibility
from trainiq.normalization.taxonomy import map_discipline


def build_canonical_record(
    provider: str,
    record_kind: RecordKind,
    normalized: dict[str, Any],
    athlete_profile: Optional[AthleteProfile] = None,
    recent_weights_kg: Sequence[float] = (),
) -> dict[str, Any]:
    """Returns a dict with exactly the columns the target table expects
    (normalized_activities or weigh_ins) — the caller (SynchronizationEngine)
    decides which table based on `record_kind`, this function only builds
    the values. `recent_weights_kg` is ignored on the ACTIVITY path — it
    only feeds the WEIGH_IN path's plausibility check (ADR-039)."""
    if record_kind == RecordKind.ACTIVITY:
        return _build_activity_record(provider, normalized, athlete_profile)
    return _build_weigh_in_record(provider, normalized, recent_weights_kg)


def _build_activity_record(
    provider: str, normalized: dict[str, Any], athlete_profile: Optional[AthleteProfile]
) -> dict[str, Any]:
    discipline = map_discipline(provider, normalized.get("discipline_raw"))
    confidence = compute_source_confidence(RecordKind.ACTIVITY, normalized)
    load_result = compute_training_load(normalized, athlete_profile)

    return {
        "provider": provider,
        "external_id": normalized["external_id"],
        "start_time": normalized["start_time"],
        "duration_s": normalized["duration_s"],
        "discipline": discipline.value,
        "distance_m": normalized.get("distance_m"),
        "avg_hr": normalized.get("avg_hr"),
        "max_hr": normalized.get("max_hr"),
        "avg_power": normalized.get("avg_power"),
        "max_power": normalized.get("max_power"),
        "calories": normalized.get("calories"),
        # Issue #48: plain pass-through, same pattern as distance_m/avg_hr
        # above — no new derived logic, no taxonomy, no confidence-scoring
        # involvement (see Risks/tradeoffs).
        "elevation_gain_m": normalized.get("elevation_gain_m"),
        "moving_time_s": normalized.get("moving_time_s"),
        "is_indoor": normalized.get("is_indoor"),
        "training_load": load_result.load,
        "training_load_method": load_result.method.value,
        "source_confidence": confidence,
        # Issue #46: passed through verbatim — this function does no
        # interpretation of these, same as every other already-normalized
        # field above. Absence (None) is meaningful (see peloton.py's
        # normalize() docstring on the three cases it represents) and must
        # reach upsert_normalized_activity()'s COALESCE logic unchanged,
        # not be coerced to anything else here.
        "activity_title": normalized.get("activity_title"),
        "instructor_name": normalized.get("instructor_name"),
        "class_type": normalized.get("class_type"),
        "planned_duration_s": normalized.get("planned_duration_s"),
        "provider_class_id": normalized.get("provider_class_id"),
        "sport_type_raw": normalized.get("sport_type_raw"),
        "difficulty_estimate": normalized.get("difficulty_estimate"),
    }


def _build_weigh_in_record(
    provider: str, normalized: dict[str, Any], recent_weights_kg: Sequence[float] = ()
) -> dict[str, Any]:
    # No discipline, no training load — Milestone 3 §4: weigh-in data has
    # neither concept.
    #
    # NOTE, discovered while building this function (checked against the
    # actual schema, not assumed): unlike normalized_activities, the
    # weigh_ins table has no source_confidence column at all — it was
    # never provisioned in schema v1. compute_source_confidence() COULD be
    # called here (weigh-in data is genuinely sparse/model-dependent per
    # Milestone 3 §4, so the concept applies), but there's nowhere to
    # persist the result today. Not fixed here — flagged as BACKLOG.md
    # BL-009 rather than silently expanding this slice's scope into a
    # schema migration that wasn't part of what was approved.
    #
    # ADR-039 / Issue #38, corrected by Issue #42: plausibility is
    # evaluated here, independent of provider — any connector with
    # record_kind == WEIGH_IN gets this for free. `recent_weights_kg` must
    # already be the athlete's prior readings whose WEIGHT was not flagged
    # (or was BO-confirmed), rolling-window-limited and chronologically
    # prior to this record — that filtering/ordering is the caller's
    # (SynchronizationEngine's) job, not this function's. The weight and
    # body-fat verdicts are independent (Issue #42) — one never suppresses
    # the other.
    verdict = evaluate_weigh_in_plausibility(
        normalized.get("weight_kg"), normalized.get("body_fat_pct"), recent_weights_kg
    )
    return {
        "provider": provider,
        "external_id": normalized["external_id"],
        "timestamp": normalized["timestamp"],
        "weight_kg": normalized.get("weight_kg"),
        "body_fat_pct": normalized.get("body_fat_pct"),
        "muscle_mass_pct": normalized.get("muscle_mass_pct"),
        "is_weight_flagged_implausible": not verdict.is_weight_plausible,
        "weight_plausibility_reason": verdict.weight_reason,
        "is_body_fat_flagged_implausible": not verdict.is_body_fat_plausible,
        "body_fat_plausibility_reason": verdict.body_fat_reason,
    }
