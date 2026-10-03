"""
trainiq.normalization.taxonomy — Epic 6, slice 2: Canonical Discipline Taxonomy

Per Milestone A §5: Normalization owns a single canonical discipline enum,
with an explicit, versioned mapping table from each provider's native
vocabulary to that enum — never inferred, never guessed. An unrecognized
raw value is logged (semantic drift — R-NORM-02, R-PELOTON-07) and falls
back to OTHER, never silently miscategorized as something more specific
than the evidence supports (Constitution Principle 1, applied to
categorization rather than a numeric value this time).

Scope note: this module only maps `discipline_raw` (already extracted by
each connector's normalize()) to the canonical `Discipline` enum. It does
not touch connectors, the Sync Engine, or persistence — those are later
slices in the Epic 6 sequence (source_confidence, training load, and
finally the routing slice where SynchronizationEngine starts consuming
record_kind).

Only ACTIVITY-kind connectors (Strava, Peloton) have a discipline to map —
WEIGH_IN-kind data (Eufy) has no discipline concept at all.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from trainiq.logging_setup import diagnostic_logger


class Discipline(str, Enum):
    """The canonical taxonomy. Deliberately small — matches what Milestone
    A/C's analytics actually distinguish (cycling gets opportunistic TSS,
    everything else uses TRIMP), not an exhaustive sport catalog."""
    CYCLING = "cycling"
    RUNNING = "running"
    STRENGTH = "strength"
    YOGA = "yoga"
    OTHER = "other"


# Versioned per-provider mapping (Milestone A §5). Bump MAPPING_VERSION and
# add a dated comment whenever a provider's vocabulary changes — this is
# exactly the kind of drift R-NORM-02/R-PELOTON-07 exist to catch, and a
# version number makes it possible to reason about which mapping produced
# a given historical record.
MAPPING_VERSION = 1

_STRAVA_MAP: dict[str, Discipline] = {
    "Ride": Discipline.CYCLING,
    "VirtualRide": Discipline.CYCLING,
    "EBikeRide": Discipline.CYCLING,
    "MountainBikeRide": Discipline.CYCLING,
    "GravelRide": Discipline.CYCLING,
    "Run": Discipline.RUNNING,
    "TrailRun": Discipline.RUNNING,
    "VirtualRun": Discipline.RUNNING,
    "WeightTraining": Discipline.STRENGTH,
    "Workout": Discipline.STRENGTH,
    "Crossfit": Discipline.STRENGTH,
    "Yoga": Discipline.YOGA,
}

# Peloton's fitness_discipline values that a connector's semantic-drift
# check already recognizes as valid Peloton categories (see
# trainiq.connectors.peloton._KNOWN_FITNESS_DISCIPLINES) still need mapping
# onto the canonical taxonomy here — recognized-by-the-connector and
# mapped-to-a-canonical-bucket are two different, deliberately separate
# concerns (connector-level "is this a value I've seen before" vs.
# Normalization-level "which canonical bucket does it belong in").
_PELOTON_MAP: dict[str, Discipline] = {
    "cycling": Discipline.CYCLING,
    "running": Discipline.RUNNING,
    "strength": Discipline.STRENGTH,
    "yoga": Discipline.YOGA,
    "meditation": Discipline.OTHER,
    "stretching": Discipline.OTHER,
    "cardio": Discipline.OTHER,
}

_PROVIDER_MAPS: dict[str, dict[str, Discipline]] = {
    "strava": _STRAVA_MAP,
    "peloton": _PELOTON_MAP,
    # ARCH-PEL-001: peloton_csv (historical/manual CSV import) is a
    # distinct provider identity from peloton (future live connector) —
    # provenance must stay separable for a future CSV-vs-live
    # reconciliation. Both share the SAME _PELOTON_MAP object, not a
    # copy — no taxonomy duplication, no new discipline categories.
    "peloton_csv": _PELOTON_MAP,
}


def map_discipline(provider: str, discipline_raw: Optional[str]) -> Discipline:
    """The single entry point for canonical discipline mapping. Never
    guesses: an unrecognized raw value, an unrecognized provider, or a
    missing raw value all fall back to OTHER, logged — never silently
    assigned to CYCLING/RUNNING/STRENGTH/YOGA without evidence."""
    provider_map = _PROVIDER_MAPS.get(provider)
    if provider_map is None:
        diagnostic_logger().warning(
            f"normalization: no discipline mapping defined for provider {provider!r} "
            f"(mapping_version={MAPPING_VERSION}) — falling back to OTHER"
        )
        return Discipline.OTHER

    if discipline_raw is None:
        diagnostic_logger().warning(
            f"normalization: {provider} record has no discipline_raw at all "
            f"(mapping_version={MAPPING_VERSION}) — falling back to OTHER"
        )
        return Discipline.OTHER

    canonical = provider_map.get(discipline_raw)
    if canonical is None:
        # This IS the semantic-drift signal (R-NORM-02/R-PELOTON-07) —
        # logged here, at the point the mapping actually fails, which is a
        # more precise place to catch it than the connector-level
        # "unrecognized value" check (which only knows "I haven't seen
        # this before," not "the canonical taxonomy has no bucket for it").
        diagnostic_logger().warning(
            f"normalization: unrecognized {provider} discipline_raw={discipline_raw!r} "
            f"(mapping_version={MAPPING_VERSION}) — falling back to OTHER, not guessed"
        )
        return Discipline.OTHER

    return canonical
