"""
trainiq.export.classes — peloton_classes.md/.json (issue #72).

Real Peloton bike-class candidates for the coach to recommend a class for
the time available: per duration (20/30/45/60 min) x class type the BO has
actually ridden (from history, never guessed), the newest ~8 archived
classes, each marked done-before or new.

Pure renderer + a small fetch layer, per the architecture doc: `used_types`
and `done_before` are pure functions over already-loaded `Activity` rows,
and `build`'s only network-shaped dependency is the injected `catalog`
(`ClassCatalog` — `PelotonConnector` satisfies it; tests pass a fake), so
everything but the two HTTP calls is testable offline.

Failure handling (AC 7): a missing `catalog`, a failed/malformed metadata
call, or no usable `class_types` makes the WHOLE file state "class catalog
unavailable" — nothing else here is attempted. Once calls are underway, an
`AuthenticationError`/`TransientError` from any one search stops further
calls entirely (don't keep hammering a rate-limited or now-unauthenticated
session) and every remaining section is marked unavailable; any other
per-section failure (malformed response, confirmed-empty `data[]`) only
affects that one section.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Optional, Protocol

from trainiq.connectors.peloton import CLASS_TYPE_LOOKUP_FAILED, CLASS_TYPE_NOT_A_CLASS
from trainiq.export.data import load_activities
from trainiq.export.fmt import fmt, md_table
from trainiq.sync.engine import AuthenticationError, TransientError

DURATIONS_S = {20: 1200, 30: 1800, 45: 2700, 60: 3600}
ROWS_PER_SECTION = 8
MAX_TYPES = 6

_UNAVAILABLE_REASON = "class catalog unavailable"
_NO_CLASS_TYPE_SENTINELS = {None, "", CLASS_TYPE_NOT_A_CLASS, CLASS_TYPE_LOOKUP_FAILED}


class ClassCatalog(Protocol):
    def fetch_ride_metadata_mappings(self) -> Optional[dict[str, Any]]: ...

    def fetch_archived_classes(
        self, class_type_id: str, duration_s: int, limit: int = ROWS_PER_SECTION
    ) -> Optional[dict[str, Any]]: ...


def _peloton_cycling_activities(conn: sqlite3.Connection, primary_ids: set[int]) -> list:
    return [
        a for a in load_activities(conn, primary_ids)
        if a.provider == "peloton" and a.discipline == "cycling"
    ]


def used_types(conn: sqlite3.Connection, primary_ids: set[int]) -> dict[str, int]:
    """Tag -> ride count, over every Peloton cycling activity in history.
    `class_type` is a `", "`-joined list of tags (#58); a ride whose
    `class_type` is NULL/empty/a lookup sentinel contributes no tag at all
    — never guessed (BA's open question, answered by the Architect)."""
    counts: dict[str, int] = {}
    for a in _peloton_cycling_activities(conn, primary_ids):
        if a.class_type in _NO_CLASS_TYPE_SENTINELS:
            continue
        for tag in a.class_type.split(", "):
            tag = tag.strip()
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
    return counts


def done_before(conn: sqlite3.Connection, primary_ids: set[int]) -> dict[str, date]:
    """`provider_class_id` -> date of the BO's most recent ride of that
    class. A ride with a non-NULL `provider_class_id` counts here even if
    its `class_type` is NULL/sentinel (the Architect's answer to the BA's
    open question) — this is a different, wider filter than `used_types`."""
    latest: dict[str, date] = {}
    for a in _peloton_cycling_activities(conn, primary_ids):
        if not a.provider_class_id:
            continue
        ride_date = a.start_time.date()
        if a.provider_class_id not in latest or ride_date > latest[a.provider_class_id]:
            latest[a.provider_class_id] = ride_date
    return latest


def _index_by_name(entries: Any) -> dict[str, tuple[str, str]]:
    """lowercased catalog name -> (id, canonical-cased name)."""
    result: dict[str, tuple[str, str]] = {}
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") and entry.get("id") is not None:
            result[str(entry["name"]).strip().lower()] = (entry["id"], entry["name"])
    return result


def _index_instructors(entries: Any) -> dict[Any, str]:
    """instructor id -> name."""
    result: dict[Any, str] = {}
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") is not None and entry.get("name"):
            result[entry["id"]] = entry["name"]
    return result


def _select_types(tag_counts: dict[str, int], catalog_by_name: dict[str, tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Matches history tags to catalog names case-insensitively. Returns
    (selected [(name, id), ...] sorted by name, unmatched tag strings
    sorted, dropped-by-cap type names sorted) — `selected` is capped at
    `MAX_TYPES`, chosen by ride count desc then name asc (ties), per the
    architecture doc."""
    unmatched: list[str] = []
    matched: list[tuple[str, str, int]] = []  # (name, id, count)
    for tag, count in tag_counts.items():
        hit = catalog_by_name.get(tag.strip().lower())
        if hit is None:
            unmatched.append(tag)
            continue
        type_id, canonical_name = hit
        matched.append((canonical_name, type_id, count))

    matched.sort(key=lambda m: (-m[2], m[0].lower()))
    kept = matched[:MAX_TYPES]
    dropped = matched[MAX_TYPES:]

    selected = sorted(((name, type_id) for name, type_id, _ in kept), key=lambda t: t[0].lower())
    dropped_names = sorted({name for name, _, _ in dropped})
    unmatched_sorted = sorted(set(unmatched))
    return selected, unmatched_sorted, dropped_names


def _build_row(entry: dict[str, Any], instructors_by_id: dict[Any, str], done_before_map: dict[str, date]) -> dict[str, Any]:
    class_id = entry.get("id")
    instructor_id = entry.get("instructor_id")
    instructor = instructors_by_id.get(instructor_id, "-") if instructor_id is not None else "-"

    air_time = entry.get("original_air_time")
    air_date = (
        datetime.fromtimestamp(air_time, tz=timezone.utc).date().isoformat()
        if isinstance(air_time, (int, float)) else None
    )

    done_before_date = done_before_map.get(str(class_id)) if class_id is not None else None

    return {
        "id": class_id,
        "title": entry.get("title"),
        "instructor": instructor,
        "difficulty": entry.get("difficulty_estimate"),
        "air_date": air_date,
        "done_before": done_before_date.isoformat() if done_before_date else None,
    }


def build(
    conn: sqlite3.Connection,
    primary_ids: set[int],
    catalog: Optional[ClassCatalog],
    as_of: date,
) -> dict[str, Any]:
    """Never raises on an API error (AC 7) — any such failure is reflected
    in the returned `status`/section `status` fields instead. `as_of` is
    not in the architecture doc's literal signature but is required for
    determinism (AC 8: no wall-clock timestamps) and the `As of:` header —
    noted as a deviation in the PR."""
    data: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "status": "ok",
        "sections": [],
        "unmatched_tags": [],
        "not_shown": [],
    }

    if catalog is None:
        data["status"] = "unavailable"
        data["reason"] = _UNAVAILABLE_REASON
        return data

    try:
        metadata = catalog.fetch_ride_metadata_mappings()
    except Exception:  # noqa: BLE001 - any metadata failure means "unavailable", never a crash
        metadata = None

    class_types_raw = metadata.get("class_types") if isinstance(metadata, dict) else None
    if not class_types_raw:
        data["status"] = "unavailable"
        data["reason"] = _UNAVAILABLE_REASON
        return data

    catalog_by_name = _index_by_name(class_types_raw)
    instructors_by_id = _index_instructors(metadata.get("instructors"))

    tag_counts = used_types(conn, primary_ids)
    selected_types, unmatched_tags, dropped_names = _select_types(tag_counts, catalog_by_name)
    data["unmatched_tags"] = unmatched_tags
    data["not_shown"] = dropped_names

    done_before_map = done_before(conn, primary_ids)

    stopped = False
    for duration_min in sorted(DURATIONS_S):
        duration_s = DURATIONS_S[duration_min]
        for type_name, type_id in selected_types:
            section: dict[str, Any] = {
                "duration_min": duration_min, "class_type": type_name,
                "status": "ok", "total": None, "classes": [],
            }

            if stopped:
                section["status"] = "unavailable"
                data["sections"].append(section)
                continue

            try:
                raw = catalog.fetch_archived_classes(type_id, duration_s, limit=ROWS_PER_SECTION)
            except (AuthenticationError, TransientError):
                stopped = True
                section["status"] = "unavailable"
                data["sections"].append(section)
                continue
            except Exception:  # noqa: BLE001 - a malformed single search only sinks its own section
                section["status"] = "unavailable"
                data["sections"].append(section)
                continue

            if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
                section["status"] = "unavailable"
                data["sections"].append(section)
                continue

            section["total"] = raw.get("total")
            rows = raw["data"][:ROWS_PER_SECTION]
            if not rows:
                section["status"] = "empty"
            else:
                section["classes"] = [_build_row(entry, instructors_by_id, done_before_map) for entry in rows]
            data["sections"].append(section)

    return data


def render_md(data: dict[str, Any]) -> str:
    lines = ["# Peloton class candidates", f"As of: {data['as_of']}", ""]

    if data["status"] == "unavailable":
        lines.append(data.get("reason", _UNAVAILABLE_REASON))
        lines.append("")
        return "\n".join(lines)

    headers = ["Title", "Instructor", "Difficulty", "Air date", "Class ID", "Status"]
    sections_by_duration: dict[int, list[dict[str, Any]]] = {}
    for section in data["sections"]:
        sections_by_duration.setdefault(section["duration_min"], []).append(section)

    for duration_min in sorted(DURATIONS_S):
        sections = sections_by_duration.get(duration_min, [])
        lines.append(f"## {duration_min} min")
        if not sections:
            lines.append("No class types used in history.")
            lines.append("")
            continue
        for section in sections:
            lines.append(f"### {section['class_type']}")
            if section["status"] == "unavailable":
                lines.append(_UNAVAILABLE_REASON.capitalize() + ".")
            elif section["status"] == "empty":
                lines.append("No classes found.")
            else:
                rows = [
                    [
                        fmt(c["title"]), fmt(c["instructor"]), fmt(c["difficulty"], 1),
                        fmt(c["air_date"]), fmt(c["id"]),
                        f"done before ({c['done_before']})" if c["done_before"] else "new",
                    ]
                    for c in section["classes"]
                ]
                lines.append(md_table(headers, rows))
            lines.append("")

    if data["unmatched_tags"]:
        lines.append(f"Unmatched history tags: {', '.join(data['unmatched_tags'])}.")
        lines.append("")
    if data["not_shown"]:
        lines.append(f"Not shown (cap): {', '.join(data['not_shown'])}.")
        lines.append("")

    return "\n".join(lines)


def to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, indent=2)
