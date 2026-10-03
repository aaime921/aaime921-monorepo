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

from typing import Any, Optional

from trainiq.athlete.profile import AthleteProfile
from trainiq.connectors.base import RecordKind
from trainiq.normalization.confidence import compute_source_confidence
from trainiq.normalization.load import compute_training_load
from trainiq.normalization.taxonomy import map_discipline


def build_canonical_record(
    provider: str,
    record_kind: RecordKind,
    normalized: dict[str, Any],
    athlete_profile: Optional[AthleteProfile] = None,
) -> dict[str, Any]:
    """Returns a dict with exactly the columns the target table expects
    (normalized_activities or weigh_ins) — the caller (SynchronizationEngine)
    decides which table based on `record_kind`, this function only builds
    the values."""
    if record_kind == RecordKind.ACTIVITY:
        return _build_activity_record(provider, normalized, athlete_profile)
    return _build_weigh_in_record(provider, normalized)


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
        "training_load": load_result.load,
        "training_load_method": load_result.method.value,
        "source_confidence": confidence,
    }


def _build_weigh_in_record(provider: str, normalized: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "provider": provider,
        "external_id": normalized["external_id"],
        "timestamp": normalized["timestamp"],
        "weight_kg": normalized.get("weight_kg"),
        "body_fat_pct": normalized.get("body_fat_pct"),
        "muscle_mass_pct": normalized.get("muscle_mass_pct"),
    }
