"""
trainiq.export.classes — peloton_classes.md/.json (issue #72).

Extends the #71 export (`trainiq.export.run_export`) with a Peloton bike
class-candidate catalog: for each duration the BO might ride (20/30/45/60
min) and each class type the BO's own history actually uses, the newest
`ROWS_PER_SECTION` classes from Peloton's archived-ride catalog, marked
`done before (date)` (from history) or `new`.

Network calls go through the `ClassCatalog` protocol (satisfied by
`PelotonConnector`) so `build()` is fully testable offline with a fake; a
`None` catalog, a failed metadata call, or any auth/rate-limit error always
degrades to "class catalog unavailable" rather than breaking the export
(AC 7) — nothing here raises for an API failure, only for a programming
error.

Call budget (AC 6): exactly one metadata call plus at most one archived-ride
search per (duration x matched class type), never per raw history tag —
`MAX_TYPES` caps the types searched, and a tag the metadata catalog doesn't
recognize is never searched at all (see `unmatched_tags`).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Optional, Protocol

from trainiq.export.data import parse_timestamp
from trainiq.export.fmt import fmt, md_table
from trainiq.sync.engine import AuthenticationError, TransientError

DURATIONS_MIN = (20, 30, 45, 60)
DURATIONS_S = {20: 1200, 30: 1800, 45: 2700, 60: 3600}

# Architect's limits (docs/trainiq/architecture/72-peloton-class-candidates.md):
# worst case 6 types x 4 durations x 8 rows = 192 rows, within the #71 50 KB
# target alongside the other six files.
ROWS_PER_SECTION = 8
MAX_TYPES = 6

# Stored `normalized_activities.class_type` sentinels that mean "no class
# type for this ride" — NOT "unknown," never guessed at (per the
# architecture doc's answer to the BA's open question). A ride with one of
# these still counts for done-before as long as `provider_class_id` is set.
_NO_CLASS_TYPE_SENTINELS = frozenset({None, "", "not_a_class", "lookup_failed"})


class ClassCatalog(Protocol):
    """Satisfied by `PelotonConnector`; tests pass a fake with the same
    two-method shape so `build()` never depends on real network I/O."""

    def fetch_ride_metadata_mappings(self) -> Optional[dict]: ...

    def fetch_archived_classes(self, class_type_id: str, duration_s: int, limit: int = 8) -> Optional[dict]: ...


def _peloton_cycling_rows(conn: sqlite3.Connection, primary_ids: set[int], as_of: date) -> list[sqlite3.Row]:
    """Every Peloton cycling row in `primary_ids` (dedup already applied by
    the caller) with a start date on or before `as_of` — the same
    determinism rule every other renderer applies (AC 8: no wall-clock,
    same DB plus same `as_of` always yields the same file)."""
    rows = conn.execute(
        "SELECT id, start_time, class_type, provider_class_id FROM normalized_activities "
        "WHERE provider = 'peloton' AND discipline = 'cycling'"
    ).fetchall()
    return [
        row for row in rows
        if row["id"] in primary_ids and parse_timestamp(row["start_time"]).date() <= as_of
    ]


def used_types(conn: sqlite3.Connection, primary_ids: set[int], as_of: date) -> list[str]:
    """Class-type tags from the BO's own cycling history, most-ridden
    first, ties broken by name ascending — the `MAX_TYPES` cap is applied
    by the caller (`build()`), not here, so a test can see the full
    ranking. `class_type` is a `", "`-joined set of tags (issue #58); each
    tag is counted once per ride it appears on."""
    counts: dict[str, int] = {}
    for row in _peloton_cycling_rows(conn, primary_ids, as_of):
        class_type = row["class_type"]
        if class_type in _NO_CLASS_TYPE_SENTINELS:
            continue
        for tag in class_type.split(", "):
            tag = tag.strip()
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts, key=lambda tag: (-counts[tag], tag))


def done_before(conn: sqlite3.Connection, primary_ids: set[int], as_of: date) -> dict[str, date]:
    """`provider_class_id` -> the latest date the BO rode that class.
    Independent of `used_types`'s class-type filtering (the architecture
    doc's answer to the BA's open question): a ride with a non-NULL
    `provider_class_id` counts here even when its `class_type` is a
    sentinel or NULL."""
    latest: dict[str, date] = {}
    for row in _peloton_cycling_rows(conn, primary_ids, as_of):
        class_id = row["provider_class_id"]
        if class_id is None:
            continue
        ride_date = parse_timestamp(row["start_time"]).date()
        if class_id not in latest or ride_date > latest[class_id]:
            latest[class_id] = ride_date
    return latest


def _match_class_type_id(tag: str, class_types: dict) -> Optional[str]:
    """Case-insensitive match of a history tag against the metadata
    catalog's class-type names (the architecture doc: "Tags are matched to
    catalog names case-insensitively"). Returns None, never guesses, for a
    tag not in the catalog (e.g. a non-class-type tag)."""
    tag_lower = tag.lower()
    for name, type_id in class_types.items():
        if isinstance(name, str) and name.lower() == tag_lower:
            return type_id
    return None


def _air_date(original_air_time: object) -> Optional[str]:
    if original_air_time is None:
        return None
    try:
        return datetime.fromtimestamp(int(original_air_time), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def _class_row(raw: dict, instructors: dict, done_before_map: dict[str, date]) -> dict:
    class_id = raw.get("id")
    class_id_str = str(class_id) if class_id is not None else None
    instructor_id = raw.get("instructor_id")
    instructor_name = instructors.get(instructor_id) if instructor_id is not None else None
    done = done_before_map.get(class_id_str) if class_id_str is not None else None
    return {
        "id": class_id_str,
        "title": raw.get("title"),
        "instructor": instructor_name,
        "difficulty": raw.get("difficulty_estimate"),
        "air_date": _air_date(raw.get("original_air_time")),
        "done_before": done.isoformat() if done is not None else None,
    }


def build(
    conn: sqlite3.Connection,
    primary_ids: set[int],
    as_of: date,
    catalog: Optional[ClassCatalog],
) -> dict:
    """Pure (besides the calls through `catalog`) and never raises on an
    API failure — every expected failure (no catalog, a failed/malformed
    metadata or search response, an auth/rate-limit error) degrades to a
    well-formed `status: "unavailable"` result instead (AC 7). A genuinely
    unexpected exception is still possible (a programming error) and is
    the outer `run_export` try/except's job to catch, not this function's."""
    if catalog is None:
        return {"as_of": as_of.isoformat(), "status": "unavailable", "reason": "no catalog configured", "sections": [], "unmatched_tags": [], "not_shown": []}

    try:
        metadata = catalog.fetch_ride_metadata_mappings()
    except (AuthenticationError, TransientError) as exc:
        return {"as_of": as_of.isoformat(), "status": "unavailable", "reason": str(exc), "sections": [], "unmatched_tags": [], "not_shown": []}

    class_types = metadata.get("class_types") if isinstance(metadata, dict) else None
    instructors = metadata.get("instructors") if isinstance(metadata, dict) else None
    if not isinstance(class_types, dict) or not isinstance(instructors, dict):
        return {"as_of": as_of.isoformat(), "status": "unavailable", "reason": "class catalog unavailable", "sections": [], "unmatched_tags": [], "not_shown": []}

    ranked_types = used_types(conn, primary_ids, as_of)
    used_capped = ranked_types[:MAX_TYPES]
    not_shown = ranked_types[MAX_TYPES:]

    matched_types: list[tuple[str, str]] = []
    unmatched_tags: list[str] = []
    for tag in used_capped:
        type_id = _match_class_type_id(tag, class_types)
        if type_id is None:
            unmatched_tags.append(tag)
        else:
            matched_types.append((tag, type_id))

    done_before_map = done_before(conn, primary_ids, as_of)

    sections: list[dict] = []
    stop_reason: Optional[str] = None
    for duration_min in DURATIONS_MIN:
        duration_s = DURATIONS_S[duration_min]
        for tag, type_id in matched_types:
            if stop_reason is not None:
                sections.append({
                    "duration_min": duration_min, "class_type": tag,
                    "status": "unavailable", "total": None, "classes": [],
                })
                continue

            try:
                response = catalog.fetch_archived_classes(type_id, duration_s, limit=ROWS_PER_SECTION)
            except (AuthenticationError, TransientError) as exc:
                # AC 7 / architecture "Failure handling": stop issuing
                # further calls rather than hammering a rate-limited or
                # no-longer-authenticated API; this and every remaining
                # section are marked unavailable, never retried.
                stop_reason = str(exc)
                sections.append({
                    "duration_min": duration_min, "class_type": tag,
                    "status": "unavailable", "total": None, "classes": [],
                })
                continue

            data = response.get("data") if isinstance(response, dict) else None
            if not isinstance(data, list):
                sections.append({
                    "duration_min": duration_min, "class_type": tag,
                    "status": "unavailable", "total": None, "classes": [],
                })
                continue

            classes = [_class_row(raw, instructors, done_before_map) for raw in data[:ROWS_PER_SECTION] if isinstance(raw, dict)]
            sections.append({
                "duration_min": duration_min,
                "class_type": tag,
                "status": "ok" if classes else "empty",
                "total": response.get("total"),
                "classes": classes,
            })

    return {
        "as_of": as_of.isoformat(),
        "status": "ok",
        "sections": sections,
        "unmatched_tags": unmatched_tags,
        "not_shown": not_shown,
    }


def render_md(data: dict) -> str:
    lines = ["# Peloton class candidates", f"As of: {data['as_of']}", ""]

    if data["status"] == "unavailable":
        lines.append("Class catalog unavailable.")
        lines.append("")
        return "\n".join(lines)

    headers = ["Title", "Instructor", "Difficulty", "Air date", "Class id", "Status"]
    current_duration: Optional[int] = None
    for section in data["sections"]:
        if section["duration_min"] != current_duration:
            current_duration = section["duration_min"]
            lines.append(f"## {current_duration} min")
            lines.append("")
        lines.append(f"### {section['class_type']}")
        if section["status"] == "unavailable":
            lines.append("Class catalog unavailable.")
        elif section["status"] == "empty":
            lines.append("No classes found.")
        else:
            rows = [
                [
                    c["title"] or "-",
                    c["instructor"] or "-",
                    fmt(c["difficulty"], 1),
                    c["air_date"] or "-",
                    c["id"] or "-",
                    f"done before ({c['done_before']})" if c["done_before"] else "new",
                ]
                for c in section["classes"]
            ]
            lines.append(md_table(headers, rows))
        lines.append("")

    if data["unmatched_tags"]:
        lines.append(f"Unmatched history tags: {', '.join(data['unmatched_tags'])}")
        lines.append("")
    if data["not_shown"]:
        lines.append(f"Not shown (cap): {', '.join(data['not_shown'])}")
        lines.append("")

    return "\n".join(lines)


def render(
    conn: sqlite3.Connection,
    primary_ids: set[int],
    as_of: date,
    catalog: Optional[ClassCatalog],
) -> tuple[str, str]:
    data = build(conn, primary_ids, as_of, catalog)
    md = render_md(data)
    js = json.dumps(data, sort_keys=True, indent=2)
    return md, js
