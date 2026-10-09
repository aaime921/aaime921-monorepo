"""
trainiq.connectors.strava_streams — Strava per-activity streams enrichment
(issue #50)

Requirements: docs/trainiq/requirements/50-strava-streams-hr-pace-gps.md
Architecture: docs/trainiq/architecture/50-strava-streams-hr-pace-gps.md

Separate step after sync, not part of download(): dedup (trainiq.dedup.
detector) runs offline, so at download time nobody knows which activities
are Peloton-linked. Driven by a per-row `streams_fetch_status` column, so
the sync checkpoint is never touched. Eligible rows are `strava_unofficial`
rows with `streams_fetch_status IS NULL`, limited to `primary_activity_ids()`
(trainiq.dedup.detector) — this excludes the secondary side of an
auto-linked Peloton/Strava pair (Peloton is primary for HR/power; the
Strava-only side is what needs enrichment).

Derived values are merged into the stored raw payload
(`raw_activities.payload_json`) under `_streams_*` keys, so
`StravaUnofficialConnector.normalize()` keeps reproducing them from raw
alone — a renormalize_provider() re-run needs no new network I/O. The
normalized_activities row is updated through a narrow UPDATE
(`_apply_streams_enrichment`), the same single-writer-invariant exception
already established by peloton.py's apply_class_metadata_update()/
apply_performance_update(). `streams_fetch_status` is written ONLY here,
never by upsert_normalized_activity().

Each row is one transaction: the raw-payload merge, the normalized_activities
UPDATE, and the activity_tracks write (if any) all commit together,
immediately after that row's fetch — so an interrupted run leaves no
partial/corrupt row, and a resumed run's eligible-row query naturally picks
up wherever it left off (AC8). `AuthenticationError`/`TransientError` from
the fetch itself propagate uncaught: whatever was already committed stays
committed, and the row being fetched when the error occurred stays NULL
(streams_fetch_status IS NULL), eligible for the next run (AC9).
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
import zlib
from dataclasses import dataclass
from typing import Any, Callable

from trainiq.connectors.strava_unofficial import PROVIDER as STRAVA_UNOFFICIAL_PROVIDER
from trainiq.connectors.strava_unofficial import STREAM_TYPES, StravaUnofficialConnector
from trainiq.dedup.detector import primary_activity_ids
from trainiq.sync.engine import AuthenticationError, TransientError

# Pacing (architecture doc's "Pacing" decision — no measured safe rate
# limit exists for this endpoint): a flat delay plus jitter between
# requests, not a per-request-type tuning knob. ~10 minutes for the full
# ~190-activity backfill.
DELAY_S = 3.0
JITTER_S = 2.0

# Cap on how many NEW (never-attempted) activities the post-sync hook in
# trainiq/app.py enriches per sync run — bounds exposure on every regular
# sync; the full backfill is scripts/backfill_strava_streams.py's job.
NEW_PER_SYNC_CAP = 10

STATUS_OK = "ok"               # at least one usable stream key
STATUS_NO_STREAMS = "no_streams"  # fetch succeeded, empty/unusable response
STATUS_UNAVAILABLE = "unavailable"  # confirmed 404 (activity gone/private)

_ENCODING_ZLIB_JSON_V1 = "zlib-json-v1"

# A track needs real coordinates — no `latlng` stream means no row (AC4),
# regardless of what else the response carries.
_MIN_LATLNG_POINTS = 1


def _time_weighted_avg(time_s: list[float] | None, values: list[float | None] | None) -> float | None:
    """Σ value[i]·dt[i] / Σ dt[i] over samples with a non-None value, where
    dt[i] = time[i] - time[i-1] for i >= 1. Returns None if either series
    is missing/too short or every weighted interval has a None value (no
    usable HR at all) — never fabricated, never a plain unweighted mean."""
    if not time_s or not values:
        return None
    n = min(len(time_s), len(values))
    weighted_sum = 0.0
    total_dt = 0.0
    for i in range(1, n):
        value = values[i]
        if value is None:
            continue
        dt = time_s[i] - time_s[i - 1]
        weighted_sum += value * dt
        total_dt += dt
    if total_dt <= 0:
        return None
    return weighted_sum / total_dt


def _moving_time_s(time_s: list[float] | None, moving: list[bool] | None) -> int | None:
    """Σ dt[i] where moving[i] is true, dt[i] = time[i] - time[i-1], i >= 1.
    None if either series is absent — moving_time_s must never be guessed
    from elapsed time (that's exactly the bug this issue fixes)."""
    if not time_s or not moving:
        return None
    n = min(len(time_s), len(moving))
    total = 0.0
    for i in range(1, n):
        if moving[i]:
            total += time_s[i] - time_s[i - 1]
    return round(total)


def _avg_pace_s_per_km(moving_time_s: int | None, distance: list[float] | None) -> float | None:
    """moving_time_s / (total distance in km). None if moving_time_s
    couldn't be derived, distance is absent, or the total distance is
    under 100 m (too short for a meaningful pace, and guards against
    division by a near-zero distance)."""
    if moving_time_s is None or not distance:
        return None
    total_distance_m = distance[-1]
    if total_distance_m is None or total_distance_m < 100:
        return None
    return moving_time_s / (total_distance_m / 1000)


def derive_stream_metrics(streams: dict[str, list]) -> dict[str, Any]:
    """Pure, fixture-testable (architecture doc, "Interfaces"). Returns
    avg_hr/max_hr (rounded to int — both are INTEGER columns), moving_time_s,
    avg_pace_s_per_km — each None if its required input stream is absent.
    Cadence/watts are out of scope (requirements doc) and never read here."""
    time_s = streams.get("time")
    heartrate = streams.get("heartrate")
    moving = streams.get("moving")
    distance = streams.get("distance")

    avg_hr = _time_weighted_avg(time_s, heartrate)
    moving_time_s = _moving_time_s(time_s, moving)

    return {
        "avg_hr": round(avg_hr) if avg_hr is not None else None,
        "max_hr": max((h for h in heartrate if h is not None), default=None) if heartrate else None,
        "moving_time_s": moving_time_s,
        "avg_pace_s_per_km": _avg_pace_s_per_km(moving_time_s, distance),
    }


def encode_track(streams: dict[str, list]) -> tuple[int, bytes] | None:
    """Pure, fixture-testable. Returns (point_count, zlib-compressed JSON
    bytes) for the GPS track, or None if `latlng` is absent/empty (AC4 — no
    track row for an indoor/GPS-less activity). Full resolution, no
    decimation (architecture doc). `alt` is null-padded to latlng's length
    when altitude is absent, never fabricated."""
    latlng = streams.get("latlng")
    if not latlng or len(latlng) < _MIN_LATLNG_POINTS:
        return None

    point_count = len(latlng)
    lat = [round(pair[0], 6) for pair in latlng]
    lng = [round(pair[1], 6) for pair in latlng]
    altitude = streams.get("altitude")
    alt = [altitude[i] if altitude is not None and i < len(altitude) else None for i in range(point_count)]
    time_s = streams.get("time") or []

    payload = json.dumps({"t": time_s, "lat": lat, "lng": lng, "alt": alt}, separators=(",", ":"))
    return point_count, zlib.compress(payload.encode("utf-8"))


def _has_usable_stream_data(streams: dict[str, list] | None) -> bool:
    if not streams:
        return False
    return any(streams.get(stream_type) for stream_type in STREAM_TYPES)


def _classify(streams: dict[str, list] | None) -> str:
    if streams is None:
        return STATUS_UNAVAILABLE
    if not _has_usable_stream_data(streams):
        return STATUS_NO_STREAMS
    return STATUS_OK


@dataclass(frozen=True)
class EnrichmentResult:
    processed: int
    ok: int
    no_streams: int
    unavailable: int


def _select_eligible_rows(conn: sqlite3.Connection, limit: int | None) -> list[dict]:
    """`strava_unofficial` rows never yet attempted (streams_fetch_status
    IS NULL), restricted to primary_activity_ids() (excludes the secondary
    side of an auto-linked Peloton pair), newest first."""
    eligible_ids = primary_activity_ids(conn)
    if not eligible_ids:
        return []
    placeholders = ",".join("?" for _ in eligible_ids)
    query = (
        "SELECT id, external_id FROM normalized_activities "
        f"WHERE provider = ? AND streams_fetch_status IS NULL AND id IN ({placeholders}) "
        "ORDER BY start_time DESC"
    )
    params: tuple = (STRAVA_UNOFFICIAL_PROVIDER, *eligible_ids)
    if limit is not None:
        query += " LIMIT ?"
        params = (*params, limit)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _apply_streams_enrichment(
    conn: sqlite3.Connection,
    activity_id: int,
    external_id: str,
    status: str,
    streams: dict[str, list] | None,
) -> None:
    """One row's full write, all in this one transaction (caller commits):
    merges `_streams_*` keys into the stored raw payload, narrow-UPDATEs
    normalized_activities (COALESCE — a failed/empty fetch must not wipe
    whatever moving_time_raw-derived value was already there), and writes
    the GPS track if present. No-op fields (metrics all None) still update
    streams_fetch_status, since that column's only job is "was this row
    attempted" — final per AC7."""
    metrics = derive_stream_metrics(streams) if streams else {}

    raw_row = conn.execute(
        "SELECT payload_json FROM raw_activities WHERE provider = ? AND external_id = ?",
        (STRAVA_UNOFFICIAL_PROVIDER, external_id),
    ).fetchone()
    if raw_row is not None:
        raw = json.loads(raw_row["payload_json"])
        raw["_streams_status"] = status
        raw["_streams_avg_hr"] = metrics.get("avg_hr")
        raw["_streams_max_hr"] = metrics.get("max_hr")
        raw["_streams_moving_time_s"] = metrics.get("moving_time_s")
        raw["_streams_avg_pace_s_per_km"] = metrics.get("avg_pace_s_per_km")
        conn.execute(
            "UPDATE raw_activities SET payload_json = ? WHERE provider = ? AND external_id = ?",
            (json.dumps(raw), STRAVA_UNOFFICIAL_PROVIDER, external_id),
        )

    conn.execute(
        """
        UPDATE normalized_activities
        SET avg_hr = COALESCE(?, avg_hr),
            max_hr = COALESCE(?, max_hr),
            moving_time_s = COALESCE(?, moving_time_s),
            avg_pace_s_per_km = COALESCE(?, avg_pace_s_per_km),
            streams_fetch_status = ?
        WHERE provider = ? AND external_id = ?
        """,
        (
            metrics.get("avg_hr"), metrics.get("max_hr"),
            metrics.get("moving_time_s"), metrics.get("avg_pace_s_per_km"),
            status, STRAVA_UNOFFICIAL_PROVIDER, external_id,
        ),
    )

    if streams:
        track = encode_track(streams)
        if track is not None:
            point_count, blob = track
            conn.execute(
                "INSERT OR REPLACE INTO activity_tracks (activity_id, point_count, encoding, track) "
                "VALUES (?, ?, ?, ?)",
                (activity_id, point_count, _ENCODING_ZLIB_JSON_V1, blob),
            )


def enrich_strava_streams(
    conn: sqlite3.Connection,
    connector: StravaUnofficialConnector,
    *,
    limit: int | None = None,
    delay_s: float = DELAY_S,
    jitter_s: float = JITTER_S,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[], float] = random.random,
) -> EnrichmentResult:
    """Entry point, used by both scripts/backfill_strava_streams.py and the
    trainiq/app.py post-sync hook. Walks eligible rows (`_select_eligible_rows`),
    sleeping `delay_s + jitter_fn() * jitter_s` seconds between requests
    (not before the first one). Commits after every row (AC8 resumability).
    AuthenticationError/TransientError from the fetch propagate uncaught —
    see module docstring."""
    rows = _select_eligible_rows(conn, limit)

    processed = ok = no_streams = unavailable = 0
    for index, row in enumerate(rows):
        if index > 0:
            sleep_fn(delay_s + jitter_fn() * jitter_s)

        streams = connector.fetch_activity_streams(row["external_id"])
        status = _classify(streams)
        _apply_streams_enrichment(conn, row["id"], row["external_id"], status, streams)
        conn.commit()

        processed += 1
        if status == STATUS_OK:
            ok += 1
        elif status == STATUS_NO_STREAMS:
            no_streams += 1
        else:
            unavailable += 1

    return EnrichmentResult(processed=processed, ok=ok, no_streams=no_streams, unavailable=unavailable)
