"""
trainiq/dedup/detector.py — Cross-provider activity deduplication (issue #37).

The same real-world ride is often stored twice: once synced from Peloton,
once synced from Strava (`strava` or `strava_unofficial`) when the ride is
auto-posted there. Nothing links the two rows today, so any total that sums
across `normalized_activities` double-counts them. This module finds those
cross-provider duplicate pairs among already-stored `normalized_activities`
rows and records a merge/flag decision for each in `dedup_links` — it never
deletes or modifies `raw_activities`/`normalized_activities` rows; both sides
of a pair stay in storage as evidence.

Scope: every pairing among `peloton`, `strava`, `strava_unofficial`
(`PROVIDERS_IN_SCOPE`). `peloton_csv` is out of scope (see requirements doc).

Winner rule (which side is "primary" for an auto-linked pair), in priority
order:

1. If one side's provider is `peloton`, it is primary: Peloton carries
   power/HR for the ride, Strava contributes GPS/distance. This is the BO's
   rule verbatim (issue #37).
2. Otherwise (a `strava` vs. `strava_unofficial` pair — neither side is
   Peloton, so rule 1 doesn't apply), `strava` (official, Tier 1) is
   primary over `strava_unofficial` (Tier 2): `StravaUnofficialConnector`
   never reports HR/power (`avg_hr`/`avg_power`/`max_power` are always
   `None`), so the official connector is never a strictly-worse source for
   the same ride. This extends the BO's rule using the existing
   `Connector.capability_tier` concept rather than inventing a new one for
   a case the BO's wording doesn't directly name.

Matching: start-time proximity (the primary signal, normalized per-provider
to a common `datetime` — see `normalize_start_time`) plus at least one of
duration proximity or matching canonical `discipline` (the secondary
signal). A time-proximity match alone is never sufficient to even score a
pair, let alone auto-link it (requirement AC1). Every scored pair gets a
numeric confidence score; only pairs at or above `AUTO_LINK_THRESHOLD` are
auto-linked, everything else is recorded `flagged:needs_review` for human
review, never silently merged.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

PROVIDERS_IN_SCOPE = ("peloton", "strava", "strava_unofficial")

# BO's live evidence (issue #37): real duplicate pairs in production start
# within +/-5 minutes of each other. Primary signal's gating window.
TIME_WINDOW_S = 300

# Secondary-signal gate: duration must agree within this many seconds to
# count as a duration match. Flat, not relative - the fixture's real clock-
# skew-only case (1800s vs 1790s) and the BO's production data both involve
# single-ride duration gaps far smaller than this; a flat 2-minute tolerance
# is generous enough to absorb that without matching genuinely different
# rides.
DURATION_TOLERANCE_S = 120

# Confidence weights (sum to 1.0) and auto-link threshold. No BO-specified
# value exists for either - these are the stated, documented values this
# design commits to. A pair that clears the primary gate below but scores
# under AUTO_LINK_THRESHOLD is flagged, never silently auto-linked.
TIME_WEIGHT = 0.5
DURATION_WEIGHT = 0.25
DISCIPLINE_WEIGHT = 0.25
AUTO_LINK_THRESHOLD = 0.6

RESOLUTION_PREFIX_LINKED = "linked:primary="
RESOLUTION_FLAGGED = "flagged:needs_review"


@dataclass(frozen=True)
class _Row:
    id: int
    provider: str
    start_time: datetime
    duration_s: int
    discipline: str


@dataclass(frozen=True)
class _ScoredPair:
    activity_id_a: int
    activity_id_b: int
    confidence_score: float
    resolution: str


@dataclass(frozen=True)
class BackfillResult:
    scanned: int  # in-scope normalized_activities rows read
    candidate_pairs: int  # pairs that passed the primary gate
    linked: int  # of those, auto-linked (confidence >= threshold)
    flagged: int  # of those, flagged as ambiguous
    skipped_existing: int  # candidate pairs already present in dedup_links


def normalize_start_time(provider: str, raw_start_time: str) -> datetime:
    """Dispatches on `provider`, never on sniffing `raw_start_time`'s shape
    (see module docstring). Raises ValueError for a provider outside
    PROVIDERS_IN_SCOPE - this function is only ever called with one of the
    three in-scope providers, so that's a genuine programming-error guard,
    not a normal-path outcome."""
    if provider == "peloton":
        return datetime.fromtimestamp(int(raw_start_time), tz=timezone.utc)
    if provider in ("strava", "strava_unofficial"):
        return datetime.fromisoformat(raw_start_time)
    raise ValueError(f"Unsupported provider for start_time normalization: {provider!r}")


def primary_provider(provider_a: str, provider_b: str) -> str:
    """The generalized winner rule (see module docstring). Returns whichever
    of provider_a/provider_b is primary. Raises ValueError if either side is
    outside PROVIDERS_IN_SCOPE, or if both sides are the same provider
    (same-provider pairs are never scored - see _find_candidate_pairs)."""
    for p in (provider_a, provider_b):
        if p not in PROVIDERS_IN_SCOPE:
            raise ValueError(f"Unsupported provider for primary_provider: {p!r}")
    if provider_a == provider_b:
        raise ValueError(f"primary_provider called with identical providers: {provider_a!r}")

    if "peloton" in (provider_a, provider_b):
        return "peloton"
    # Remaining case: {"strava", "strava_unofficial"} - official wins.
    return "strava"


def _fetch_rows(conn: sqlite3.Connection) -> list[_Row]:
    cursor = conn.execute(
        "SELECT id, provider, external_id, start_time, duration_s, discipline "
        "FROM normalized_activities WHERE provider IN (?, ?, ?)",
        PROVIDERS_IN_SCOPE,
    )
    rows = []
    for row in cursor.fetchall():
        rows.append(
            _Row(
                id=row["id"],
                provider=row["provider"],
                start_time=normalize_start_time(row["provider"], row["start_time"]),
                duration_s=row["duration_s"],
                discipline=row["discipline"],
            )
        )
    return rows


def _find_candidate_pairs(rows: list[_Row]) -> list[tuple[_Row, _Row]]:
    """Sort-and-sweep over rows ordered by normalized start time: walk the
    sorted list once, and for each row only compare forward against rows
    within TIME_WINDOW_S - O(n log n) instead of an O(n^2) pairwise scan.
    Same-provider pairs are skipped (cross-provider duplication only)."""
    ordered = sorted(rows, key=lambda r: r.start_time)
    pairs: list[tuple[_Row, _Row]] = []
    n = len(ordered)
    for i in range(n):
        x = ordered[i]
        for j in range(i + 1, n):
            y = ordered[j]
            delta = (y.start_time - x.start_time).total_seconds()
            if delta > TIME_WINDOW_S:
                break
            if x.provider == y.provider:
                continue
            pairs.append((x, y))
    return pairs


def _score_pair(x: _Row, y: _Row) -> _ScoredPair | None:
    """Applies the primary gate and, if it passes, computes the confidence
    score and resolution. Returns None if the pair fails the primary gate
    (no dedup_links row is written for it at all)."""
    delta_seconds = abs((y.start_time - x.start_time).total_seconds())
    duration_match = abs(x.duration_s - y.duration_s) <= DURATION_TOLERANCE_S
    discipline_match = x.discipline == y.discipline

    if not duration_match and not discipline_match:
        return None

    time_score = max(0.0, 1.0 - delta_seconds / TIME_WINDOW_S)
    confidence = round(
        TIME_WEIGHT * time_score
        + DURATION_WEIGHT * (1.0 if duration_match else 0.0)
        + DISCIPLINE_WEIGHT * (1.0 if discipline_match else 0.0),
        4,
    )

    if x.id < y.id:
        activity_id_a, activity_id_b = x.id, y.id
    else:
        activity_id_a, activity_id_b = y.id, x.id

    if confidence >= AUTO_LINK_THRESHOLD:
        resolution = f"{RESOLUTION_PREFIX_LINKED}{primary_provider(x.provider, y.provider)}"
    else:
        resolution = RESOLUTION_FLAGGED

    return _ScoredPair(
        activity_id_a=activity_id_a,
        activity_id_b=activity_id_b,
        confidence_score=confidence,
        resolution=resolution,
    )


def _already_recorded(conn: sqlite3.Connection, activity_id_a: int, activity_id_b: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM dedup_links WHERE activity_id_a = ? AND activity_id_b = ?",
        (activity_id_a, activity_id_b),
    ).fetchone()
    return row is not None


def run_backfill(conn: sqlite3.Connection) -> BackfillResult:
    """Entry point. Reads every in-scope normalized_activities row, finds
    candidate cross-provider pairs via sort-and-sweep on normalized start
    time, scores each candidate that passes the primary gate, and writes
    any not already present in dedup_links. Commits once at the end (all-
    or-nothing for a single invocation). Returns a summary; never raises
    for "no activities found" or "no pairs found" (both are valid,
    reportable outcomes, not errors)."""
    rows = _fetch_rows(conn)
    pairs = _find_candidate_pairs(rows)

    candidate_pairs = 0
    linked = 0
    flagged = 0
    skipped_existing = 0

    for x, y in pairs:
        scored = _score_pair(x, y)
        if scored is None:
            continue
        candidate_pairs += 1

        if _already_recorded(conn, scored.activity_id_a, scored.activity_id_b):
            skipped_existing += 1
            continue

        conn.execute(
            "INSERT INTO dedup_links (activity_id_a, activity_id_b, confidence_score, resolution) "
            "VALUES (?, ?, ?, ?)",
            (scored.activity_id_a, scored.activity_id_b, scored.confidence_score, scored.resolution),
        )
        if scored.resolution.startswith(RESOLUTION_PREFIX_LINKED):
            linked += 1
        else:
            flagged += 1

    conn.commit()

    return BackfillResult(
        scanned=len(rows),
        candidate_pairs=candidate_pairs,
        linked=linked,
        flagged=flagged,
        skipped_existing=skipped_existing,
    )


def primary_activity_ids(conn: sqlite3.Connection) -> set[int]:
    """Every normalized_activities.id that is NOT the secondary side of an
    auto-linked pair - i.e. the set a future summary/totals layer should sum
    over to count each linked pair once. Flagged (ambiguous) pairs
    contribute BOTH sides to this set unchanged, since they are explicitly
    not merged (requirement: "never silently merge"); only an auto-linked
    pair's secondary side is excluded."""
    all_ids: set[int] = set()
    secondary_ids: set[int] = set()

    for row in conn.execute("SELECT id FROM normalized_activities"):
        all_ids.add(row["id"])

    for row in conn.execute(
        "SELECT activity_id_a, activity_id_b, resolution FROM dedup_links "
        "WHERE resolution LIKE ?",
        (f"{RESOLUTION_PREFIX_LINKED}%",),
    ):
        primary = row["resolution"].removeprefix(RESOLUTION_PREFIX_LINKED)
        a_provider_row = conn.execute(
            "SELECT provider FROM normalized_activities WHERE id = ?", (row["activity_id_a"],)
        ).fetchone()
        a_provider = a_provider_row["provider"] if a_provider_row else None
        if a_provider == primary:
            secondary_ids.add(row["activity_id_b"])
        else:
            secondary_ids.add(row["activity_id_a"])

    return all_ids - secondary_ids
