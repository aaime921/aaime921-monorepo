"""
trainiq.normalization.renormalize — one-off re-normalization pass (issue #36)

`raw_activities` stores the untouched provider payload for every synced
record specifically so normalization can be re-run later without
re-fetching (sync/engine.py's own module docstring). A live sync will NOT
self-heal already-stored rows: `download()` only fetches activities after
the stored checkpoint, so already-fetched rows are never re-passed through
`normalize()`/`build_canonical_record()` by the normal sync path.

This module exists for exactly that situation — e.g. the `strava_unofficial`
taxonomy fix (issue #36), where 352 already-stored rows need their
`discipline` recomputed from existing raw payloads. Deliberately kept out of
`SynchronizationEngine` itself: that class's own docstring states that
retroactively recomputing past normalized records is "a new, separate
capability, not an extension of this engine's existing behavior."

Never touches `raw_activities` — only reads it. Idempotent: rerunning
produces identical `normalized_activities` rows, because persistence goes
through the same `upsert_normalized_activity()` keyed on
UNIQUE(provider, external_id) a live sync uses. Does not commit — the
caller owns the transaction boundary, matching how `SynchronizationEngine`
callers already commit.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

from trainiq.athlete.profile import AthleteProfile
from trainiq.connectors.base import Connector
from trainiq.logging_setup import diagnostic_logger
from trainiq.normalization.engine import build_canonical_record
from trainiq.sync.engine import upsert_normalized_activity


@dataclass(frozen=True)
class RenormalizeResult:
    read: int
    inserted: int
    updated: int
    skipped_malformed: int
    skipped_no_external_id: int


def renormalize_provider(
    conn: sqlite3.Connection,
    provider: str,
    connector: Connector,
    athlete_profile: Optional[AthleteProfile] = None,
    raw_transform: Optional[Callable[[dict], dict]] = None,
) -> RenormalizeResult:
    """Re-derives every normalized_activities row for `provider` from its
    already-stored raw_activities payload, via connector.normalize() +
    build_canonical_record() — the same transformation a live sync would
    apply, run again against old raw data.

    Malformed-record handling mirrors SynchronizationEngine.sync_connector()
    exactly (sync/engine.py): a row that fails to build a canonical record
    is logged and skipped, never allowed to abort the whole pass.

    `raw_transform`, when given, is applied to each row's parsed raw dict
    (`raw = raw_transform(raw)`) immediately after `json.loads(...)` and
    before `connector.normalize(raw)` — in-memory only, `raw_activities` is
    never written back to (issue #45: lets a one-off script inject a fact
    the stored payload itself doesn't carry, e.g. a confirmed distance
    unit, without touching the raw payload). Omitted/None preserves
    today's exact behavior — confirmed by inspection that the one existing
    call site (scripts/renormalize_strava_unofficial.py, issue #36) passes
    no such argument.

    Issue #48 note: this function is already fully generic (`provider` +
    any `Connector`), so re-normalizing the official `strava` provider the
    same way needs no new code here — just
    `renormalize_provider(conn, "strava", StravaConnector(...))` via a thin
    CLI wrapper mirroring scripts/renormalize_strava_unofficial.py. That
    wrapper isn't written yet because official-`strava` rows synced before
    issue #48 never had elevation_gain/moving_time/trainer captured into
    raw_activities in the first place (`_activity_to_raw_dict()` didn't
    read them) — re-normalization can't manufacture data that was never
    persisted, so those rows need a fresh sync first. Write the wrapper
    once that fresh sync has happened.
    """
    rows = conn.execute(
        "SELECT external_id, payload_json FROM raw_activities WHERE provider = ?",
        (provider,),
    ).fetchall()

    read = 0
    inserted = 0
    updated = 0
    skipped_malformed = 0
    skipped_no_external_id = 0

    for row in rows:
        read += 1
        external_id = row["external_id"]
        if not external_id:
            skipped_no_external_id += 1
            diagnostic_logger().warning(
                f"{provider}: raw_activities row with no external_id, skipping re-normalization"
            )
            continue

        raw = json.loads(row["payload_json"])
        if raw_transform is not None:
            raw = raw_transform(raw)
        normalized = connector.normalize(raw)

        try:
            canonical_record = build_canonical_record(
                provider, connector.record_kind, normalized, athlete_profile
            )
        except (KeyError, TypeError, ValueError) as exc:
            skipped_malformed += 1
            diagnostic_logger().warning(
                f"{provider}: malformed record (external_id={external_id!r}) "
                f"could not be re-normalized into a canonical record: {exc}"
            )
            continue

        outcome = upsert_normalized_activity(conn, canonical_record)
        if outcome == "inserted":
            inserted += 1
        else:
            updated += 1

    return RenormalizeResult(
        read=read,
        inserted=inserted,
        updated=updated,
        skipped_malformed=skipped_malformed,
        skipped_no_external_id=skipped_no_external_id,
    )
