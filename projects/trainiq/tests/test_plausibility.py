"""
Tests for ADR-039 / Issue #38's weigh-in plausibility rule, corrected by
Issue #42 to decouple the weight and body-fat verdicts. Pure function, no
DB — fixture values reproduce the issues' own evidence tables.
"""

from __future__ import annotations

from trainiq.normalization.plausibility import evaluate_weigh_in_plausibility

BASELINE = [83.0, 84.0, 85.0, 86.0, 87.0]  # median 85.0


def test_normal_reading_within_baseline_is_plausible():
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=85.0, body_fat_pct=18.0, recent_weights_kg=BASELINE
    )
    assert verdict.is_weight_plausible
    assert verdict.weight_reason is None
    assert verdict.is_body_fat_plausible
    assert verdict.body_fat_reason is None


def test_obvious_outlier_reproducing_issue_evidence_is_flagged():
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=20.7, body_fat_pct=5.0, recent_weights_kg=BASELINE
    )
    assert not verdict.is_weight_plausible
    assert "weight_kg" in verdict.weight_reason
    assert "deviates" in verdict.weight_reason


def test_borderline_just_under_threshold_is_not_flagged():
    # median 85.0, 25% threshold -> 63.75 is exactly 25% deviation.
    # 63.76 is just under 25% deviation.
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=63.76, body_fat_pct=18.0, recent_weights_kg=BASELINE
    )
    assert verdict.is_weight_plausible


def test_borderline_just_over_threshold_is_flagged():
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=63.0, body_fat_pct=18.0, recent_weights_kg=BASELINE
    )
    assert not verdict.is_weight_plausible


def test_deviation_exactly_at_threshold_is_not_flagged():
    # Strict ">" boundary: exactly 25.0% deviation from median 85.0 is 63.75.
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=63.75, body_fat_pct=18.0, recent_weights_kg=BASELINE
    )
    assert verdict.is_weight_plausible


def test_insufficient_history_skips_weight_axis_entirely():
    # Only 2 prior readings, below min_history of 3 -> weight axis skipped
    # regardless of how extreme the value is.
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=20.0, body_fat_pct=18.0, recent_weights_kg=[84.0, 86.0]
    )
    assert verdict.is_weight_plausible


def test_body_fat_at_floor_flags_independent_of_weight():
    """A genuinely implausible non-zero body-fat value (between 0 and the
    floor) flags only the body-fat axis; the weight axis is untouched."""
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=85.0, body_fat_pct=1.5, recent_weights_kg=BASELINE
    )
    assert not verdict.is_body_fat_plausible
    assert "body_fat_pct" in verdict.body_fat_reason
    assert verdict.is_weight_plausible
    assert verdict.weight_reason is None


def test_body_fat_just_above_floor_does_not_flag_on_that_axis():
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=85.0, body_fat_pct=3.1, recent_weights_kg=BASELINE
    )
    assert verdict.is_body_fat_plausible


def test_none_weight_skips_weight_axis_without_error():
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=None, body_fat_pct=18.0, recent_weights_kg=BASELINE
    )
    assert verdict.is_weight_plausible


def test_none_body_fat_skips_body_fat_axis_without_error():
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=85.0, body_fat_pct=None, recent_weights_kg=BASELINE
    )
    assert verdict.is_body_fat_plausible


def test_empty_history_skips_weight_axis():
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=20.0, body_fat_pct=18.0, recent_weights_kg=[]
    )
    assert verdict.is_weight_plausible


# --- Issue #42: body-fat 0.0 ("not measured") vs. a genuine low reading ----


def test_body_fat_zero_does_not_flag_either_axis_and_body_fat_axis_is_plausible():
    """Core regression (AC6): body_fat_pct=0.0 against a consistent
    baseline must not flag the weight, and — because 0 is Eufy's
    "not measured" sentinel, not a genuine floor violation — must not
    flag the body-fat axis either now that the `> 0` guard excludes it."""
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=82.2, body_fat_pct=0.0, recent_weights_kg=BASELINE
    )
    assert verdict.is_weight_plausible
    assert verdict.weight_reason is None
    assert verdict.is_body_fat_plausible
    assert verdict.body_fat_reason is None


def test_body_fat_zero_with_also_implausible_weight_flags_only_weight_axis():
    """Both axes are independent even when one, both, or neither trips:
    a zero body-fat sentinel alongside a genuinely implausible weight
    must flag the weight axis without the zero sentinel also (incorrectly)
    flagging the body-fat axis."""
    verdict = evaluate_weigh_in_plausibility(
        weight_kg=19.15, body_fat_pct=0.0, recent_weights_kg=BASELINE
    )
    assert not verdict.is_weight_plausible
    assert verdict.is_body_fat_plausible
    assert verdict.body_fat_reason is None
