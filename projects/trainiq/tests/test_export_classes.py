"""Tests for trainiq.export.classes (issue #72, AC 1-9).

`used_types`/`done_before` are pure functions over the DB, tested
directly; `build`/`render_md`/`to_json` are tested through a fake
`ClassCatalog` (no network), per the architecture doc's "pure renderer +
a small fetch layer" split.
"""

from __future__ import annotations

from datetime import date

from trainiq.connectors.peloton import CLASS_TYPE_LOOKUP_FAILED, CLASS_TYPE_NOT_A_CLASS
from trainiq.export.classes import DURATIONS_S, MAX_TYPES, build, done_before, render_md, to_json, used_types
from trainiq.sync.engine import AuthenticationError, TransientError

from tests.conftest import insert_activity

_METADATA = {
    "class_types": [
        {"id": "ct_pz", "name": "Power Zone"},
        {"id": "ct_li", "name": "Low Impact"},
    ],
    "instructors": [{"id": "i1", "name": "Robin Arzon"}],
}


class FakeCatalog:
    def __init__(self, metadata=None, metadata_error=None, archived=None, archived_errors=None):
        self.metadata = metadata
        self.metadata_error = metadata_error
        self.archived = archived or {}
        self.archived_errors = archived_errors or {}
        self.metadata_calls = 0
        self.archived_calls: list[tuple[str, int, int]] = []

    def fetch_ride_metadata_mappings(self):
        self.metadata_calls += 1
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.metadata

    def fetch_archived_classes(self, class_type_id, duration_s, limit=8):
        self.archived_calls.append((class_type_id, duration_s, limit))
        key = (class_type_id, duration_s)
        if key in self.archived_errors:
            raise self.archived_errors[key]
        return self.archived.get(key)


def _archived(*entries, total=None):
    return {"data": list(entries), "total": total if total is not None else len(entries)}


def _entry(class_id, title="45 min Power Zone", instructor_id="i1", difficulty=8.4, air_time=1760000000):
    return {
        "id": class_id, "title": title, "instructor_id": instructor_id,
        "difficulty_estimate": difficulty, "original_air_time": air_time,
    }


# --- used_types / done_before (pure DB functions) ---------------------------

def test_used_types_counts_tags_split_from_comma_joined_class_type(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone, Tabata")

    counts = used_types(db, {a})

    assert counts == {"Power Zone": 1, "Tabata": 1}


def test_used_types_ignores_sentinel_and_null_class_type(db):
    not_a_class = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                                   start_time="2026-09-01T07:00:00+00:00", class_type=CLASS_TYPE_NOT_A_CLASS)
    lookup_failed = insert_activity(db, provider="peloton", discipline="cycling", external_id="2",
                                     start_time="2026-09-02T07:00:00+00:00", class_type=CLASS_TYPE_LOOKUP_FAILED)
    null_type = insert_activity(db, provider="peloton", discipline="cycling", external_id="3",
                                 start_time="2026-09-03T07:00:00+00:00", class_type=None)

    counts = used_types(db, {not_a_class, lookup_failed, null_type})

    assert counts == {}


def test_used_types_ignores_non_peloton_and_non_cycling(db):
    strava = insert_activity(db, provider="strava", discipline="cycling", external_id="1",
                              start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone")
    running = insert_activity(db, provider="peloton", discipline="running", external_id="2",
                               start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone")

    assert used_types(db, {strava, running}) == {}


def test_done_before_latest_date_wins(db):
    older = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                             start_time="2026-08-01T07:00:00+00:00", provider_class_id="ride-1")
    newer = insert_activity(db, provider="peloton", discipline="cycling", external_id="2",
                             start_time="2026-09-15T07:00:00+00:00", provider_class_id="ride-1")

    result = done_before(db, {older, newer})

    assert result == {"ride-1": date(2026, 9, 15)}


def test_done_before_counts_ride_even_with_sentinel_class_type(db):
    """The Architect's answer to the BA's open question: a non-NULL
    provider_class_id still counts for done-before even when class_type
    itself is a lookup sentinel."""
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00",
                         class_type=CLASS_TYPE_LOOKUP_FAILED, provider_class_id="ride-1")

    assert done_before(db, {a}) == {"ride-1": date(2026, 9, 1)}


def test_done_before_ignores_rides_with_no_provider_class_id(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", provider_class_id=None)

    assert done_before(db, {a}) == {}


# --- build(): catalog unavailable -------------------------------------------

def test_build_catalog_none_whole_file_unavailable(db):
    data = build(db, set(), None, date(2026, 10, 10))

    assert data["status"] == "unavailable"
    assert data["reason"] == "class catalog unavailable"
    assert data["sections"] == []
    assert "class catalog unavailable" in render_md(data)


def test_build_metadata_returns_none_whole_file_unavailable(db):
    catalog = FakeCatalog(metadata=None)

    data = build(db, set(), catalog, date(2026, 10, 10))

    assert data["status"] == "unavailable"
    assert catalog.archived_calls == []


def test_build_metadata_raises_whole_file_unavailable(db):
    catalog = FakeCatalog(metadata_error=AuthenticationError("session rejected"))

    data = build(db, set(), catalog, date(2026, 10, 10))

    assert data["status"] == "unavailable"
    assert catalog.archived_calls == []


def test_build_metadata_with_no_class_types_whole_file_unavailable(db):
    catalog = FakeCatalog(metadata={"class_types": [], "instructors": []})

    data = build(db, set(), catalog, date(2026, 10, 10))

    assert data["status"] == "unavailable"


# --- build(): sections, matching, done-before --------------------------------

def test_build_unused_types_are_omitted(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone")
    archived = {("ct_pz", d): _archived(_entry("ride-pz")) for d in DURATIONS_S.values()}
    catalog = FakeCatalog(metadata=_METADATA, archived=archived)

    data = build(db, {a}, catalog, date(2026, 10, 10))

    types_seen = {s["class_type"] for s in data["sections"]}
    assert types_seen == {"Power Zone"}
    assert ("ct_li", 1200) not in catalog.archived_calls and all(c[0] != "ct_li" for c in catalog.archived_calls)


def test_build_marks_done_before_and_new(db):
    done = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                            start_time="2026-08-01T07:00:00+00:00", class_type="Power Zone",
                            provider_class_id="ride-done")
    archived = {("ct_pz", 2700): _archived(_entry("ride-done"), _entry("ride-new", title="New Ride"))}
    catalog = FakeCatalog(metadata=_METADATA, archived=archived)

    data = build(db, {done}, catalog, date(2026, 10, 10))

    section = next(s for s in data["sections"] if s["duration_min"] == 45 and s["class_type"] == "Power Zone")
    by_id = {c["id"]: c for c in section["classes"]}
    assert by_id["ride-done"]["done_before"] == "2026-08-01"
    assert by_id["ride-new"]["done_before"] is None
    assert "done before (2026-08-01)" in render_md(data)
    assert "new" in render_md(data)


def test_build_resolves_instructor_name_and_falls_back_to_dash(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone")
    archived = {("ct_pz", 2700): _archived(
        _entry("r1", instructor_id="i1"), _entry("r2", instructor_id="unknown-instructor")
    )}
    catalog = FakeCatalog(metadata=_METADATA, archived=archived)

    data = build(db, {a}, catalog, date(2026, 10, 10))

    section = next(s for s in data["sections"] if s["class_type"] == "Power Zone" and s["duration_min"] == 45)
    by_id = {c["id"]: c for c in section["classes"]}
    assert by_id["r1"]["instructor"] == "Robin Arzon"
    assert by_id["r2"]["instructor"] == "-"


def test_build_empty_data_is_distinct_status_from_unavailable(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone")
    archived = {("ct_pz", 2700): _archived()}
    catalog = FakeCatalog(metadata=_METADATA, archived=archived)

    data = build(db, {a}, catalog, date(2026, 10, 10))

    section = next(s for s in data["sections"] if s["class_type"] == "Power Zone" and s["duration_min"] == 45)
    assert section["status"] == "empty"
    assert "No classes found" in render_md(data)


def test_build_one_malformed_search_only_sinks_its_own_section(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone")
    archived = {
        ("ct_pz", 1200): {"not_data": True},  # malformed: no "data" key
        ("ct_pz", 2700): _archived(_entry("r1")),
    }
    catalog = FakeCatalog(metadata=_METADATA, archived=archived)

    data = build(db, {a}, catalog, date(2026, 10, 10))

    by_duration = {s["duration_min"]: s for s in data["sections"] if s["class_type"] == "Power Zone"}
    assert by_duration[20]["status"] == "unavailable"
    assert by_duration[45]["status"] == "ok"
    # every duration x used-type combo is still attempted - one failure doesn't stop the rest
    assert len(catalog.archived_calls) == len(DURATIONS_S)


def test_build_auth_error_stops_all_further_calls(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone, Low Impact")
    archived_errors = {("ct_li", 1200): AuthenticationError("session rejected")}
    catalog = FakeCatalog(metadata=_METADATA, archived={}, archived_errors=archived_errors)

    data = build(db, {a}, catalog, date(2026, 10, 10))

    # selected types sorted by name: "Low Impact" before "Power Zone", so the
    # very first call (20 min x Low Impact) is the one that raises.
    assert len(catalog.archived_calls) == 1
    assert all(s["status"] == "unavailable" for s in data["sections"])


def test_build_transient_error_also_stops_further_calls(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone")
    archived_errors = {("ct_pz", 1200): TransientError("rate limited")}
    catalog = FakeCatalog(metadata=_METADATA, archived_errors=archived_errors)

    data = build(db, {a}, catalog, date(2026, 10, 10))

    assert len(catalog.archived_calls) == 1
    assert all(s["status"] == "unavailable" for s in data["sections"])


def test_build_unmatched_tag_is_listed_and_never_searched(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Some Future Tag")
    catalog = FakeCatalog(metadata=_METADATA)

    data = build(db, {a}, catalog, date(2026, 10, 10))

    assert data["unmatched_tags"] == ["Some Future Tag"]
    assert catalog.archived_calls == []
    assert "Unmatched history tags: Some Future Tag" in render_md(data)


def test_build_caps_at_max_types_and_lists_the_rest_as_not_shown(db):
    metadata = {
        "class_types": [{"id": f"ct{i}", "name": f"Type {i}"} for i in range(MAX_TYPES + 2)],
        "instructors": [],
    }
    primary_ids = set()
    for i in range(MAX_TYPES + 2):
        # more rides of the lower-numbered types, so ties never decide the cap
        for _ in range(MAX_TYPES + 2 - i):
            primary_ids.add(insert_activity(
                db, provider="peloton", discipline="cycling", external_id=f"{i}-{_}",
                start_time=f"2026-09-{(i % 28) + 1:02d}T07:00:00+00:00", class_type=f"Type {i}",
            ))
    catalog = FakeCatalog(metadata=metadata, archived={})

    data = build(db, primary_ids, catalog, date(2026, 10, 10))

    shown_types = {s["class_type"] for s in data["sections"]}
    assert len(shown_types) == MAX_TYPES
    assert len(data["not_shown"]) == 2
    assert shown_types.isdisjoint(data["not_shown"])


def test_build_call_count_is_bounded_by_types_times_durations(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone, Low Impact")
    catalog = FakeCatalog(metadata=_METADATA, archived={})

    build(db, {a}, catalog, date(2026, 10, 10))

    assert catalog.metadata_calls == 1
    assert len(catalog.archived_calls) == 2 * len(DURATIONS_S)  # 2 used types x 4 durations


# --- determinism / size ------------------------------------------------------

def test_build_render_is_byte_equal_on_rerun(db):
    a = insert_activity(db, provider="peloton", discipline="cycling", external_id="1",
                         start_time="2026-09-01T07:00:00+00:00", class_type="Power Zone",
                         provider_class_id="ride-done")
    archived = {("ct_pz", d): _archived(_entry("ride-done"), _entry("ride-new")) for d in DURATIONS_S.values()}

    md_outputs = []
    json_outputs = []
    for _ in range(2):
        catalog = FakeCatalog(metadata=_METADATA, archived=archived)
        data = build(db, {a}, catalog, date(2026, 10, 10))
        md_outputs.append(render_md(data))
        json_outputs.append(to_json(data))

    assert md_outputs[0] == md_outputs[1]
    assert json_outputs[0] == json_outputs[1]


def test_build_size_under_50kb_with_six_types_and_eight_rows_each(db):
    metadata = {
        "class_types": [{"id": f"ct{i}", "name": f"Type {i}"} for i in range(MAX_TYPES)],
        "instructors": [{"id": "i1", "name": "Robin Arzon"}],
    }
    primary_ids = set()
    for i in range(MAX_TYPES):
        primary_ids.add(insert_activity(
            db, provider="peloton", discipline="cycling", external_id=f"seed-{i}",
            start_time=f"2026-09-{i + 1:02d}T07:00:00+00:00", class_type=f"Type {i}",
        ))
    archived = {
        (f"ct{i}", d): _archived(*[_entry(f"ride-{i}-{d}-{n}") for n in range(8)])
        for i in range(MAX_TYPES) for d in DURATIONS_S.values()
    }
    catalog = FakeCatalog(metadata=metadata, archived=archived)

    data = build(db, primary_ids, catalog, date(2026, 10, 10))

    assert len(render_md(data).encode("utf-8")) < 50 * 1024
    assert len(to_json(data).encode("utf-8")) < 50 * 1024
