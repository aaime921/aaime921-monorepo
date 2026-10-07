"""
trainiq.normalization.plausibility — ADR-039 / Issue #38

Flags a weigh-in record as implausible when it's very unlikely to be the
athlete's own body — e.g. another person, a pet, or an object briefly on
the scale (see issue #38's evidence: 7 of 568 Eufy readings at 18.9-35.8 kg
against the BO's real 80-88.3 kg baseline, every one also carrying a
`body_fat_pct` of 0.0 or 5.0).

Per the requirements (docs/trainiq/requirements/38-*.md), the rule is
relative to the athlete's own history, not a fixed absolute kg range —
athletes' baseline weights differ. Provider-agnostic by design: this is a
property of weight/body-composition values, not of Eufy specifically, so
it lives parallel to `confidence.py`/`load.py`, not inside
`connectors/eufy.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Optional, Sequence

# Calibratable defaults, documented in ADR-039 — not derived from a larger
# dataset (the issue only gives us 568 points from one athlete), chosen to
# comfortably separate the BO's ~80-88 kg baseline from the ~19-36 kg
# outliers (a 75%+ gap) while staying loose enough not to flag ordinary
# week-to-week fluctuation or a genuine multi-month trend.
DEFAULT_WEIGHT_DEVIATION_THRESHOLD_PCT = 25.0
DEFAULT_ROLLING_WINDOW_SIZE = 5  # how many prior readings feed the median
DEFAULT_MIN_HISTORY_FOR_WEIGHT_CHECK = 3  # below this, skip the weight-deviation check entirely
DEFAULT_BODY_FAT_FLOOR_PCT = 3.0  # essential-fat floor; see ADR-039's Risks section
                                  # for why this is a conservative second signal, not
                                  # the primary one — the weight axis alone already
                                  # catches all 7 of the BO's known bad records.


@dataclass(frozen=True)
class WeighInPlausibility:
    is_plausible: bool
    reason: Optional[str]  # None iff is_plausible; else a human-readable explanation


def evaluate_weigh_in_plausibility(
    weight_kg: Optional[float],
    body_fat_pct: Optional[float],
    recent_weights_kg: Sequence[float],
    *,
    deviation_threshold_pct: float = DEFAULT_WEIGHT_DEVIATION_THRESHOLD_PCT,
    min_history: int = DEFAULT_MIN_HISTORY_FOR_WEIGHT_CHECK,
    body_fat_floor_pct: float = DEFAULT_BODY_FAT_FLOOR_PCT,
) -> WeighInPlausibility:
    """Pure function, no I/O. `recent_weights_kg` must already be the
    athlete's prior UNFLAGGED (or BO-confirmed-valid) readings strictly
    before this one's timestamp, newest-first, already limited to the
    rolling window — this function does no filtering/ordering itself.

    Never fabricates a verdict from insufficient data: with fewer than
    `min_history` prior readings, the weight-deviation axis is skipped,
    not assumed-plausible via a default baseline.

    Both axes are independent; either flags the reading:
    1. `body_fat_pct` at or below `body_fat_floor_pct`.
    2. `weight_kg` deviates from the median of `recent_weights_kg` by more
       than `deviation_threshold_pct` percent, once enough history exists.

    Boundary is strict `>` — a reading exactly at the threshold is NOT
    flagged (deterministic, see tests).
    """
    if body_fat_pct is not None and body_fat_pct <= body_fat_floor_pct:
        return WeighInPlausibility(
            is_plausible=False,
            reason=(
                f"body_fat_pct {body_fat_pct} is at or below the physiological "
                f"floor of {body_fat_floor_pct}"
            ),
        )

    if weight_kg is not None and len(recent_weights_kg) >= min_history:
        baseline = median(recent_weights_kg)
        if baseline > 0:
            deviation_pct = abs(weight_kg - baseline) / baseline * 100
            if deviation_pct > deviation_threshold_pct:
                return WeighInPlausibility(
                    is_plausible=False,
                    reason=(
                        f"weight_kg {weight_kg} deviates {deviation_pct:.1f}% from the "
                        f"athlete's rolling median baseline of {baseline:.1f} kg, "
                        f"exceeding the {deviation_threshold_pct}% threshold"
                    ),
                )

    return WeighInPlausibility(is_plausible=True, reason=None)
