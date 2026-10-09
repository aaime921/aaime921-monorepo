"""
Tests for Epic 6, slice 4: training load computation. Pure computation
over a normalized dict + an optional AthleteProfile — no connector, no
Sync Engine, no persistence, and (per the Chief Architect's explicit
sequencing) no dependency on source_confidence at all.
"""

from __future__ import annotations

import ast

from trainiq.athlete.profile import AthleteProfile
from trainiq.normalization.load import (
    TrainingLoadMethod,
    compute_training_load,
)


# --- Q1: no athlete_profile available yet (the actual current state) ------

def test_no_profile_at_all_resolves_to_unknown_never_fabricated():
    """This IS today's real, correct behavior — not a placeholder. No
    caller in this codebase can currently supply a populated
    AthleteProfile, since Epic 7 doesn't exist yet."""
    normalized = {"duration_s": 1800, "avg_hr": 145, "avg_power": 200}
    result = compute_training_load(normalized, profile=None)

    assert result.load is None
    assert result.method == TrainingLoadMethod.UNKNOWN
    assert "athlete_profile not available" in result.reason


def test_reason_and_load_are_mutually_exclusive():
    """By construction, whenever load is a real number, reason must be
    None, and vice versa — never both meaningful, per the module's own
    stated contract."""
    unknown_result = compute_training_load({"duration_s": 1800}, profile=None)
    assert unknown_result.load is None and unknown_result.reason is not None

    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190)
    real_result = compute_training_load({"duration_s": 1800, "avg_hr": 145}, profile)
    assert real_result.load is not None and real_result.reason is None


# --- TRIMP (primary method, per ADR-015) -----------------------------------

def test_trimp_computed_when_hr_and_baseline_present():
    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190)
    normalized = {"duration_s": 1800, "avg_hr": 145, "avg_power": None}

    result = compute_training_load(normalized, profile)

    assert result.method == TrainingLoadMethod.TRIMP
    assert result.load is not None
    assert result.load > 0


def test_trimp_uses_female_specific_weighting_constants():
    """Milestone A §3: Banister TRIMP has sex-specific coefficients — this
    is a required mathematical input, not an optional demographic nicety.
    Confirms male and female profiles produce genuinely different results
    for identical HR/duration inputs."""
    normalized = {"duration_s": 1800, "avg_hr": 145}
    male_profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190)
    female_profile = AthleteProfile(sex="female", resting_hr=50, max_hr=190)

    male_result = compute_training_load(normalized, male_profile)
    female_result = compute_training_load(normalized, female_profile)

    assert male_result.load != female_result.load


def test_trimp_missing_sex_falls_back_to_unknown_even_with_hr_present():
    """sex is a required TRIMP input, not optional — missing it must not
    silently default to one sex's coefficients."""
    profile = AthleteProfile(sex=None, resting_hr=50, max_hr=190)
    normalized = {"duration_s": 1800, "avg_hr": 145}

    result = compute_training_load(normalized, profile)

    assert result.method == TrainingLoadMethod.UNKNOWN
    assert result.load is None


def test_trimp_heart_rate_reserve_is_clamped_not_left_to_explode():
    """Defensive engineering choice, explicitly documented in the module:
    an avg_hr below resting_hr (noisy sensor data) must not invert or
    blow up the exponential term."""
    profile = AthleteProfile(sex="male", resting_hr=100, max_hr=190)
    normalized = {"duration_s": 1800, "avg_hr": 50}  # below resting_hr — bad data

    result = compute_training_load(normalized, profile)

    assert result.method == TrainingLoadMethod.TRIMP
    assert result.load == 0.0  # clamped HRR of 0, not a negative/nonsensical value


# --- TSS (opportunistic enhancement, per ADR-015) --------------------------

def test_tss_computed_when_power_and_ftp_present():
    profile = AthleteProfile(ftp_watts=250)
    normalized = {"duration_s": 3600, "avg_power": 250, "avg_hr": None}  # 1 hour at FTP

    result = compute_training_load(normalized, profile)

    assert result.method == TrainingLoadMethod.TSS
    assert result.load == 100.0  # one hour at threshold power is defined as TSS 100


def test_tss_preferred_over_trimp_when_both_are_available():
    """ADR-015: TSS is the opportunistic enhancement — when both power+FTP
    AND HR+baseline are available, TSS wins, since it's the more precise
    method where the data supports it."""
    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190, ftp_watts=250)
    normalized = {"duration_s": 1800, "avg_power": 200, "avg_hr": 145}

    result = compute_training_load(normalized, profile)

    assert result.method == TrainingLoadMethod.TSS


def test_tss_missing_ftp_falls_back_to_trimp_not_unknown():
    """Power is present but FTP isn't known — must fall back to TRIMP if
    HR+baseline are available, not jump straight to Unknown."""
    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190, ftp_watts=None)
    normalized = {"duration_s": 1800, "avg_power": 200, "avg_hr": 145}

    result = compute_training_load(normalized, profile)

    assert result.method == TrainingLoadMethod.TRIMP


# --- Neither method available ------------------------------------------

def test_no_power_no_hr_resolves_to_unknown_with_specific_reason():
    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190, ftp_watts=250)
    normalized = {"duration_s": 1800, "avg_power": None, "avg_hr": None}

    result = compute_training_load(normalized, profile)

    assert result.method == TrainingLoadMethod.UNKNOWN
    assert "power+FTP" in result.reason
    assert "HR+baseline" in result.reason


def test_missing_or_invalid_duration_resolves_to_unknown():
    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190)
    assert compute_training_load({"duration_s": None, "avg_hr": 145}, profile).method == TrainingLoadMethod.UNKNOWN
    assert compute_training_load({"duration_s": 0, "avg_hr": 145}, profile).method == TrainingLoadMethod.UNKNOWN


# --- Independence from source_confidence (Chief Architect's explicit requirement) --

def test_load_module_has_no_dependency_on_confidence_module():
    """The specific property requested: training_load must not depend on
    source_confidence in any way — they describe different things and
    must remain computable independently. Checked at the actual import
    level, not by behavior alone."""
    import trainiq.normalization.load as load_module

    with open(load_module.__file__) as f:
        tree = ast.parse(f.read())

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any("confidence" in m for m in imported_modules)


def test_training_load_is_computable_regardless_of_what_confidence_would_be():
    """Functional proof of independence, not just an import-graph check: a
    sparse record (which would score LOW source_confidence) and a complete
    record (which would score HIGH) both compute training_load correctly
    and identically well, because load only reads the fields it actually
    needs — it has no notion of an overall completeness score at all."""
    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190)
    sparse_record = {"duration_s": 1800, "avg_hr": 145, "avg_power": None, "distance_m": None, "calories": None}
    complete_record = {"duration_s": 1800, "avg_hr": 145, "avg_power": None, "distance_m": 10000.0, "calories": 300}

    sparse_result = compute_training_load(sparse_record, profile)
    complete_result = compute_training_load(complete_record, profile)

    assert sparse_result.load == complete_result.load  # extra unrelated fields change nothing
    assert sparse_result.method == complete_result.method == TrainingLoadMethod.TRIMP


# --- Console noise (AC1/AC6, issue #44) ------------------------------------

def test_unknown_training_load_never_prints_to_console_across_many_records(tmp_path, capsys):
    """The "training_load unknown" message is per-record diagnostic detail
    with no operator-facing significance — once logging is configured (as
    every real entry point, including the re-normalize script, now does),
    it must never reach stdout/stderr, however many records are processed
    in a run."""
    from trainiq import logging_setup

    logging_setup.configure(tmp_path)

    for _ in range(50):
        compute_training_load({"duration_s": 1800}, profile=None)

    captured = capsys.readouterr()
    assert "training_load unknown" not in captured.out
    assert "training_load unknown" not in captured.err

    diagnostic_content = (tmp_path / "diagnostic.log").read_text()
    assert diagnostic_content.count("training_load unknown") == 50
