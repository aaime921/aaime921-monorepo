"""
Tests for Epic 6, slice 3: source_confidence. Pure computation over a
normalized dict — no connector, no Sync Engine, no persistence. Confirms
the Chief Architect's explicit framing: confidence is metadata about the
normalization mapping, not something any connector reports about itself.
"""

from __future__ import annotations

import ast

from trainiq.connectors.base import RecordKind
from trainiq.normalization.confidence import compute_source_confidence


# --- Activity records --------------------------------------------------

def test_activity_with_all_optional_fields_present_is_full_confidence():
    normalized = {"avg_hr": 145, "avg_power": 210, "distance_m": 30000.0, "calories": 450}
    assert compute_source_confidence(RecordKind.ACTIVITY, normalized) == 1.0


def test_activity_with_no_optional_fields_present_is_zero_confidence():
    """Still a VALID record (e.g. a Peloton strength class with no paired
    HR monitor) — zero confidence is not an error state, per the module's
    own docstring."""
    normalized = {"avg_hr": None, "avg_power": None, "distance_m": None, "calories": None}
    assert compute_source_confidence(RecordKind.ACTIVITY, normalized) == 0.0


def test_activity_with_half_the_optional_fields_present():
    normalized = {"avg_hr": 145, "avg_power": None, "distance_m": 30000.0, "calories": None}
    assert compute_source_confidence(RecordKind.ACTIVITY, normalized) == 0.5


def test_activity_missing_keys_entirely_treated_same_as_none():
    """A normalized dict that simply doesn't have the key at all (as
    opposed to having the key set to None) must be treated identically —
    .get() with no default already handles this, but worth asserting
    explicitly since it's an easy thing to get subtly wrong."""
    normalized = {"avg_hr": 145}  # avg_power, distance_m, calories keys absent entirely
    assert compute_source_confidence(RecordKind.ACTIVITY, normalized) == 0.25


def test_activity_confidence_ignores_always_required_fields():
    """start_time, duration_s, external_id are never optional — their
    presence must not inflate the score, since it says nothing about
    THIS record's actual completeness relative to others."""
    complete_required_only = {
        "start_time": "2026-01-05T07:00:00+00:00", "duration_s": 1800, "external_id": "1",
        "avg_hr": None, "avg_power": None, "distance_m": None, "calories": None,
    }
    assert compute_source_confidence(RecordKind.ACTIVITY, complete_required_only) == 0.0


# --- Weigh-in records ----------------------------------------------------

def test_weigh_in_with_all_optional_fields_present_is_full_confidence():
    normalized = {"weight_kg": 75.4, "body_fat_pct": 18.2, "muscle_mass_pct": 34.1}
    assert compute_source_confidence(RecordKind.WEIGH_IN, normalized) == 1.0


def test_weigh_in_with_only_weight_present_reflects_sparse_scale_model():
    """Milestone 3 §4: metrics are model-dependent and sparse — a basic
    scale reporting only weight should score accordingly, not be padded
    to look more complete than it is."""
    normalized = {"weight_kg": 80.0, "body_fat_pct": None, "muscle_mass_pct": None}
    assert compute_source_confidence(RecordKind.WEIGH_IN, normalized) == 1 / 3


def test_weigh_in_and_activity_use_different_field_sets():
    """Confirms the two record kinds are genuinely scored against their
    own relevant fields, not a shared generic set — an activity-shaped
    dict scored as if it were a weigh-in (or vice versa) would silently
    produce a meaningless number."""
    activity_shaped = {"avg_hr": 145, "weight_kg": 999}  # weight_kg irrelevant for ACTIVITY
    weigh_in_shaped = {"weight_kg": 80.0, "avg_hr": 999}  # avg_hr irrelevant for WEIGH_IN

    # avg_hr present, avg_power/distance_m/calories absent -> 1/4
    assert compute_source_confidence(RecordKind.ACTIVITY, activity_shaped) == 0.25
    # weight_kg present, body_fat_pct/muscle_mass_pct absent -> 1/3
    assert compute_source_confidence(RecordKind.WEIGH_IN, weigh_in_shaped) == 1 / 3


# --- Determinism (no fabricated precision, no invented weighting) ---------

def test_confidence_is_a_simple_deterministic_ratio_not_a_hidden_weighting_scheme():
    """Two different fields being present must produce the identical score
    to two other different fields being present, for the same record kind
    — confirming equal weighting, not a hidden per-field importance score
    this project has no evidence to justify."""
    normalized_a = {"avg_hr": 145, "avg_power": None, "distance_m": None, "calories": None}
    normalized_b = {"avg_hr": None, "avg_power": None, "distance_m": 30000.0, "calories": None}
    assert compute_source_confidence(RecordKind.ACTIVITY, normalized_a) == \
        compute_source_confidence(RecordKind.ACTIVITY, normalized_b)


# --- Scope containment: connectors never compute or influence this ---------

def test_confidence_module_has_no_dependency_on_sync_engine_or_specific_connectors():
    """Confirms, at the actual import level, that this module doesn't
    import from the Sync Engine or any specific connector module — it only
    depends on the shared RecordKind enum, which is Foundation-level
    Connector Framework surface, not a specific provider's code."""
    import trainiq.normalization.confidence as confidence_module

    with open(confidence_module.__file__) as f:
        tree = ast.parse(f.read())

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(m.startswith("trainiq.sync") for m in imported_modules)
    assert not any(
        m.startswith("trainiq.connectors.") and m != "trainiq.connectors.base"
        for m in imported_modules
    )


def test_no_connector_defines_or_returns_a_confidence_related_value(caplog):
    """The strongest version of the Chief Architect's requirement: not
    just 'this module doesn't depend on connectors,' but 'no connector has
    been given anything resembling a confidence score to compute or
    return.' A blind text search for the word 'confidence' across each
    file is too blunt — it flags ordinary English in research-note
    docstrings (e.g. 'established with high confidence,' describing how
    certain Milestone 2's research was, which has nothing to do with a
    computed score). Checked precisely instead, via the AST: no function
    or method name contains 'confidence', no dict key literal (the shape
    every normalize() return uses) contains 'confidence', and no
    attribute assignment does either."""
    import ast

    import trainiq.connectors.eufy as eufy_module
    import trainiq.connectors.peloton as peloton_module
    import trainiq.connectors.strava as strava_module

    for module in (strava_module, peloton_module, eufy_module):
        with open(module.__file__) as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert "confidence" not in node.name.lower(), (
                    f"{module.__name__}.{node.name} must not exist — connectors don't compute confidence"
                )
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        assert "confidence" not in key.value.lower(), (
                            f"{module.__name__} returns a dict key {key.value!r} — "
                            f"connectors must not report their own confidence"
                        )
            if isinstance(node, ast.Attribute):
                assert "confidence" not in node.attr.lower(), (
                    f"{module.__name__} has an attribute {node.attr!r} — "
                    f"connectors must not track their own confidence"
                )
