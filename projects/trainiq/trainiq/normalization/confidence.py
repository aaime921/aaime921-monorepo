"""
trainiq.normalization.confidence — Epic 6, slice 3: source_confidence

Per the Chief Architect's explicit framing for this slice: source_confidence
is metadata ABOUT the normalization mapping, not a property a connector
reports about itself. A connector supplies raw values; the Normalization
Engine decides how complete/reliable the resulting canonical record is.
Connectors never compute, return, or influence this value — if they did,
every connector author would eventually need to invent their own subjective
scoring, exactly the drift this separation is meant to prevent.

Deliberately simple: an equal-weight completeness ratio over the fields
that are genuinely optional (present-or-absent depending on what the
provider/hardware actually reported), not the fields that are always
required and therefore uninformative about completeness (e.g. start_time,
duration_s, external_id are never optional, so their presence says nothing
about how complete a given record is). No invented weighting scheme is
applied — that would be fabricating precision this project has no evidence
to support, the same discipline Milestone C applied to signal thresholds.
If real usage ever shows equal weighting is wrong, that's a future,
evidence-driven revision — not something to guess at now.
"""

from __future__ import annotations

from trainiq.connectors.base import RecordKind

# The fields considered when scoring completeness, per record kind. Chosen
# because each is genuinely optional depending on what hardware/pairing the
# athlete actually used (a paired HR strap, a power meter, a scale that
# measures body composition) — not because these are the "important"
# fields in some other sense.
_ACTIVITY_OPTIONAL_FIELDS: tuple[str, ...] = ("avg_hr", "avg_power", "distance_m", "calories")
_WEIGH_IN_OPTIONAL_FIELDS: tuple[str, ...] = ("weight_kg", "body_fat_pct", "muscle_mass_pct")


def compute_source_confidence(record_kind: RecordKind, normalized: dict) -> float:
    """Returns a value in [0.0, 1.0] — the fraction of the record-kind's
    optional fields that are actually present (not None) in this specific
    record. 1.0 means every optional field was populated; 0.0 means none
    were (still a valid record — e.g. a Peloton strength class with no
    paired HR monitor — just one the rest of the system should treat with
    appropriately reduced confidence, never as an error)."""
    fields = _ACTIVITY_OPTIONAL_FIELDS if record_kind == RecordKind.ACTIVITY else _WEIGH_IN_OPTIONAL_FIELDS
    present = sum(1 for field in fields if normalized.get(field) is not None)
    return present / len(fields)
