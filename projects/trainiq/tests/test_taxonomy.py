"""
Tests for Epic 6, slice 2: canonical discipline taxonomy. Pure mapping
logic — no connector, no Sync Engine, no persistence involved. This slice
deliberately does not wire into anything else yet (see the roadmap
sequence: routing happens in a later slice).
"""

from __future__ import annotations

from trainiq.normalization.taxonomy import Discipline, MAPPING_VERSION, map_discipline


# --- Strava mappings ---------------------------------------------------

def test_strava_ride_maps_to_cycling():
    assert map_discipline("strava", "Ride") == Discipline.CYCLING


def test_strava_virtual_and_ebike_ride_variants_map_to_cycling():
    assert map_discipline("strava", "VirtualRide") == Discipline.CYCLING
    assert map_discipline("strava", "EBikeRide") == Discipline.CYCLING


def test_strava_run_variants_map_to_running():
    assert map_discipline("strava", "Run") == Discipline.RUNNING
    assert map_discipline("strava", "TrailRun") == Discipline.RUNNING
    assert map_discipline("strava", "VirtualRun") == Discipline.RUNNING


def test_strava_weight_training_maps_to_strength():
    assert map_discipline("strava", "WeightTraining") == Discipline.STRENGTH


def test_strava_yoga_maps_to_yoga():
    assert map_discipline("strava", "Yoga") == Discipline.YOGA


def test_strava_walk_maps_to_other_explicitly_not_via_warning():
    """Issue #36: Walk is a deliberate OTHER mapping, not a side effect of
    the unrecognized-value warning path — a documented side effect of
    reusing _STRAVA_MAP for strava_unofficial. loguru output isn't
    captured by pytest's `caplog` (stdlib logging) — see
    test_connector_declaring_no_incremental_support_logs_explanatory_note
    in test_sync_engine.py for this codebase's actual working pattern,
    used here too."""
    import io

    from loguru import logger

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        result = map_discipline("strava", "Walk")
    finally:
        logger.remove(handler_id)

    assert result == Discipline.OTHER
    assert "unrecognized" not in log_stream.getvalue()


# --- strava_unofficial mappings (issue #36) --------------------------------

def test_strava_unofficial_run_maps_to_running():
    assert map_discipline("strava_unofficial", "Run") == Discipline.RUNNING


def test_strava_unofficial_ride_maps_to_cycling():
    assert map_discipline("strava_unofficial", "Ride") == Discipline.CYCLING


def test_strava_unofficial_workout_maps_to_strength():
    assert map_discipline("strava_unofficial", "Workout") == Discipline.STRENGTH


def test_strava_unofficial_walk_maps_to_other_explicitly_not_via_warning():
    """AC4: Walk is a known, deliberately-mapped key for strava_unofficial
    too — this call must not log the "unrecognized discipline_raw" warning
    (nor the "no discipline mapping defined for provider" one, which would
    fire if strava_unofficial were still missing from _PROVIDER_MAPS),
    since both the old (buggy) and new (fixed) behavior return
    Discipline.OTHER and only the warning distinguishes them."""
    import io

    from loguru import logger

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        result = map_discipline("strava_unofficial", "Walk")
    finally:
        logger.remove(handler_id)

    assert result == Discipline.OTHER
    logged = log_stream.getvalue()
    assert "unrecognized" not in logged
    assert "no discipline mapping defined for provider" not in logged


# --- Peloton mappings ----------------------------------------------------

def test_peloton_cycling_maps_to_cycling():
    assert map_discipline("peloton", "cycling") == Discipline.CYCLING


def test_peloton_strength_maps_to_strength_not_ride():
    """The specific, named R-PELOTON-06 concern: strength must map to
    STRENGTH, never CYCLING, despite Peloton's own API historically
    modeling all classes under a 'ride' resource. This connector already
    preserves fitness_discipline distinctly (Epic 3), so this test proves
    the taxonomy layer doesn't reintroduce the confusion at this stage."""
    assert map_discipline("peloton", "strength") == Discipline.STRENGTH


def test_peloton_known_but_uncategorized_disciplines_map_to_other():
    """meditation/stretching/cardio are real, recognized Peloton
    categories (the connector's own _KNOWN_FITNESS_DISCIPLINES includes
    them) but don't fit the four primary buckets — OTHER, not an error."""
    assert map_discipline("peloton", "meditation") == Discipline.OTHER
    assert map_discipline("peloton", "stretching") == Discipline.OTHER
    assert map_discipline("peloton", "cardio") == Discipline.OTHER


# --- Fallback / semantic drift behavior (never guess) ----------------------

def test_unrecognized_discipline_value_falls_back_to_other_not_guessed(caplog):
    result = map_discipline("strava", "underwater_basket_weaving")
    assert result == Discipline.OTHER


def test_missing_discipline_raw_falls_back_to_other():
    assert map_discipline("strava", None) == Discipline.OTHER
    assert map_discipline("peloton", None) == Discipline.OTHER


def test_unknown_provider_falls_back_to_other():
    """A provider with no mapping table at all (shouldn't happen for any
    currently-implemented connector, but defensively) must not crash or
    guess — same fallback discipline as any other unrecognized case."""
    assert map_discipline("some_future_provider", "Ride") == Discipline.OTHER


def test_eufy_has_no_mapping_table_by_design():
    """Eufy is WEIGH_IN-kind, not ACTIVITY-kind — it has no discipline
    concept at all. Calling map_discipline for it falls back to OTHER via
    the unknown-provider path, which is correct: nothing should ever call
    this for a WEIGH_IN record in the first place (a later slice's
    responsibility to ensure), but if it did, this must not crash."""
    assert map_discipline("eufy", "irrelevant") == Discipline.OTHER


# --- Versioning --------------------------------------------------------

def test_mapping_version_is_a_stable_integer():
    """Just confirms the version marker exists and is usable in log
    messages / future comparisons — not asserting a specific value, since
    that will change over time by design."""
    assert isinstance(MAPPING_VERSION, int)
    assert MAPPING_VERSION >= 1


# --- Scope containment: this slice touches nothing else yet ----------------

def test_taxonomy_module_has_no_dependency_on_sync_engine_or_connectors():
    """This slice is pure mapping logic. Confirms, at the actual import
    level (not a naive substring search, which would false-positive on
    this module's own explanatory comments referencing connector code by
    name), that it doesn't import from the Sync Engine or any connector —
    if it did, that would mean scope crept beyond what this slice was
    supposed to do."""
    import ast

    import trainiq.normalization.taxonomy as taxonomy_module

    with open(taxonomy_module.__file__) as f:
        tree = ast.parse(f.read())

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(m.startswith("trainiq.sync") for m in imported_modules)
    assert not any(m.startswith("trainiq.connectors") for m in imported_modules)
