"""Tests for trainiq.export.classes (issue #72, AC 1-9).

`FakeCatalog` is the `ClassCatalog` test double the architecture doc calls
for: it records every call so tests can assert the call budget (AC 6), and
lets each test script metadata/search responses (or failures) per call.
"""

from __future__ import annotations

import json
from datetime import date

from trainiq.export import classes
from trainiq.sync.engine import AuthenticationError, TransientError

from tests.conftest import insert_activity

def _class_type(name, id_, fitness_discipline="cycling", is_active=True):
    """`GET /api/ride/metadata_mappings`'s `class_types` entries, real shape
    (BO's live capture, 2026-10-10): a list of objects, not an id-keyed
    dict. Extra keys (`display_name`, `list_order`, ...) are omitted here
    since `_build_lookups` only reads `id`/`name`/`fitness_discipline`/
    `is_active`."""
    return {"id": id_, "name": name, "fitness_discipline": fitness_discipline, "is_active": is_active}


def _instructor(id_, name):
    """`metadata_mappings`'s `instructors` entries, real shape: a list of
    objects (id, name, plus unrelated keys `_build_lookups` ignores)."""
    return {"id": id_, "name": name}


_METADATA = {
    "class_types": [
        _class_type("Power Zone", "pz-id"),
        _class_type("Low Impact", "li-id"),
        _class_type("Climb", "climb-id"),
    ],
    "instructors": [
        _instructor("inst-1", "Matt Wilpers"),
        _instructor("inst-2", "Ally Love"),
    ],
}


def _archived(*rows, total=None):
    return {"data": list(rows), "total": total if total is not None else len(rows)}


def _class(id_, title="Ride", instructor_id="inst-1", difficulty=7.5, air_time=1760000000):
    return {
        "id": id_, "title": title, "instructor_id": instructor_id,
        "difficulty_estimate": difficulty, "original_air_time": air_time,
    }


class FakeCatalog:
    def __init__(self, metadata=_METADATA, search_responses=None, raise_on_metadata=None, raise_on_search=None):
        self.metadata = metadata
        self.search_responses = search_responses or {}
        self.raise_on_metadata = raise_on_metadata
        self.raise_on_search = raise_on_search
        self.metadata_calls = 0
        self.search_calls: list[tuple[str, int]] = []

    def fetch_ride_metadata_mappings(self):
        self.metadata_calls += 1
        if self.raise_on_metadata is not None:
            raise self.raise_on_metadata
        return self.metadata

    def fetch_archived_classes(self, class_type_id, duration_s, limit=8):
        self.search_calls.append((class_type_id, duration_s))
        if self.raise_on_search is not None:
            raise self.raise_on_search
        return self.search_responses.get((class_type_id, duration_s), _archived())


def _insert_peloton_ride(db, external_id, start_time, class_type, provider_class_id=None):
    return insert_activity(
        db, provider="peloton", external_id=external_id, start_time=start_time,
        discipline="cycling", class_type=class_type, provider_class_id=provider_class_id,
    )


# --- used_types() ----------------------------------------------------------

def test_used_types_ranks_by_count_then_name(db):
    ids = set()
    ids.add(_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone"))
    ids.add(_insert_peloton_ride(db, "2", "2026-10-02T07:00:00+00:00", "Power Zone"))
    ids.add(_insert_peloton_ride(db, "3", "2026-10-03T07:00:00+00:00", "Climb"))

    result = classes.used_types(db, ids, as_of=date(2026, 10, 10))

    assert result == ["Power Zone", "Climb"]


def test_used_types_splits_comma_joined_tags(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone, Tabata")}

    result = classes.used_types(db, ids, as_of=date(2026, 10, 10))

    assert set(result) == {"Power Zone", "Tabata"}


def test_used_types_ignores_sentinel_and_null_class_type(db):
    ids = set()
    ids.add(_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "not_a_class"))
    ids.add(_insert_peloton_ride(db, "2", "2026-10-02T07:00:00+00:00", "lookup_failed"))
    ids.add(_insert_peloton_ride(db, "3", "2026-10-03T07:00:00+00:00", None))
    ids.add(_insert_peloton_ride(db, "4", "2026-10-04T07:00:00+00:00", ""))

    assert classes.used_types(db, ids, as_of=date(2026, 10, 10)) == []


def test_used_types_excludes_rides_after_as_of(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-11T07:00:00+00:00", "Power Zone")}

    assert classes.used_types(db, ids, as_of=date(2026, 10, 10)) == []


# --- done_before() ----------------------------------------------------------

def test_done_before_latest_date_wins(db):
    ids = set()
    ids.add(_insert_peloton_ride(db, "1", "2026-09-01T07:00:00+00:00", "Power Zone", provider_class_id="class-1"))
    ids.add(_insert_peloton_ride(db, "2", "2026-10-01T07:00:00+00:00", "Power Zone", provider_class_id="class-1"))

    result = classes.done_before(db, ids, as_of=date(2026, 10, 10))

    assert result == {"class-1": date(2026, 10, 1)}


def test_done_before_counts_sentinel_class_type_row_if_provider_class_id_set(db):
    ids = {_insert_peloton_ride(db, "1", "2026-09-01T07:00:00+00:00", "lookup_failed", provider_class_id="class-1")}

    assert classes.done_before(db, ids, as_of=date(2026, 10, 10)) == {"class-1": date(2026, 9, 1)}


def test_done_before_excludes_rows_with_no_provider_class_id(db):
    ids = {_insert_peloton_ride(db, "1", "2026-09-01T07:00:00+00:00", "Power Zone", provider_class_id=None)}

    assert classes.done_before(db, ids, as_of=date(2026, 10, 10)) == {}


# --- build(): done-before vs new, instructor resolution --------------------

def test_build_marks_done_before_and_new(db):
    ids = {_insert_peloton_ride(db, "1", "2026-09-01T07:00:00+00:00", "Power Zone", provider_class_id="class-1")}
    catalog = FakeCatalog(search_responses={
        ("pz-id", 2700): _archived(_class("class-1"), _class("class-2")),
    })

    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    section = next(s for s in data["sections"] if s["duration_min"] == 45 and s["class_type"] == "Power Zone")
    rows_by_id = {c["id"]: c for c in section["classes"]}
    assert rows_by_id["class-1"]["done_before"] == "2026-09-01"
    assert rows_by_id["class-2"]["done_before"] is None


def test_build_resolves_instructor_name(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone")}
    catalog = FakeCatalog(search_responses={("pz-id", 2700): _archived(_class("c1", instructor_id="inst-2"))})

    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    section = next(s for s in data["sections"] if s["duration_min"] == 45)
    assert section["classes"][0]["instructor"] == "Ally Love"


# --- unused types omitted / cap ---------------------------------------------

def test_build_omits_unused_class_types(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone")}
    catalog = FakeCatalog()

    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    types_in_sections = {s["class_type"] for s in data["sections"]}
    assert types_in_sections == {"Power Zone"}
    assert catalog.search_calls == [("pz-id", s) for s in (1200, 1800, 2700, 3600)]


def test_build_unmatched_tag_never_searched(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Not A Real Type")}
    catalog = FakeCatalog()

    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    assert data["unmatched_tags"] == ["Not A Real Type"]
    assert catalog.search_calls == []


def test_build_caps_at_max_types(db):
    ids = set()
    names = ["Power Zone", "Low Impact", "Climb", "Music", "Intervals", "Progression", "Groove"]
    for i, name in enumerate(names):
        ids.add(_insert_peloton_ride(db, str(i), f"2026-10-0{i + 1}T07:00:00+00:00", name))
    metadata = {"class_types": [_class_type(n, f"id-{n}") for n in names], "instructors": []}
    catalog = FakeCatalog(metadata=metadata)

    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    # All seven tags are ridden exactly once, so the cap breaks ties by
    # name ascending: "Progression" sorts last among the seven and is cut.
    assert data["not_shown"] == ["Progression"]
    types_in_sections = {s["class_type"] for s in data["sections"]}
    assert "Progression" not in types_in_sections
    assert len(types_in_sections) == 6


# --- real metadata_mappings shape: lists, cycling+active filter ------------

def test_build_filters_out_inactive_and_non_cycling_class_types(db):
    # A history tag that matches an inactive or non-cycling catalog entry
    # must be treated as unmatched, not resolved to a searchable type id —
    # the 169-item real catalog is mostly inactive/other-discipline noise.
    ids = set()
    ids.add(_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone"))
    ids.add(_insert_peloton_ride(db, "2", "2026-10-02T07:00:00+00:00", "Yoga Flow"))
    metadata = {
        "class_types": [
            _class_type("Power Zone", "pz-id", fitness_discipline="cycling", is_active=False),
            _class_type("Yoga Flow", "yoga-id", fitness_discipline="yoga", is_active=True),
        ],
        "instructors": [],
    }
    catalog = FakeCatalog(metadata=metadata)

    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    assert set(data["unmatched_tags"]) == {"Power Zone", "Yoga Flow"}
    assert catalog.search_calls == []


def test_build_real_shaped_metadata_produces_classes_for_power_zone(db):
    """Integration-style per the BO's rework note: a fake catalog returning
    the real `metadata_mappings`/`archived` shapes (lists of objects, extra
    unrelated keys included, plus inactive/non-cycling noise that must be
    filtered out) produces a non-empty `peloton_classes.md` for a history
    tag like "Power Zone" — this is exactly the case that silently produced
    "class catalog unavailable" on the BO's real account before this fix."""
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone")}
    metadata = {
        "class_types": [
            {
                "id": "pz-id", "name": "Power Zone", "display_name": "Power Zone",
                "fitness_discipline": "cycling", "is_active": True, "list_order": 9,
                "standalone_display_name": "Power Zone", "source_of_save": "class",
            },
            {
                "id": "warmup-id", "name": "Warm Up Ride", "display_name": "Warm Up",
                "fitness_discipline": "cycling", "is_active": False, "list_order": 30,
            },
            {
                "id": "yoga-id", "name": "Yoga Flow", "display_name": "Yoga Flow",
                "fitness_discipline": "yoga", "is_active": True, "list_order": 2,
            },
        ],
        "instructors": [
            {
                "id": "inst-1", "name": "Matt Wilpers", "first_name": "Matt",
                "fitness_disciplines": ["cycling"], "image_url": "https://example/x.png", "bio": "...",
            },
        ],
    }
    catalog = FakeCatalog(
        metadata=metadata,
        search_responses={("pz-id", 2700): _archived(_class("class-1"))},
    )

    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)
    md = classes.render_md(data)

    assert data["status"] == "ok"
    section = next(s for s in data["sections"] if s["duration_min"] == 45 and s["class_type"] == "Power Zone")
    assert section["status"] == "ok"
    assert section["classes"][0]["instructor"] == "Matt Wilpers"
    assert "class catalog unavailable" not in md.lower()
    assert "Power Zone" in md


# --- empty data[] / failures -------------------------------------------------

def test_build_empty_data_is_empty_status_not_unavailable(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone")}
    catalog = FakeCatalog(search_responses={("pz-id", 2700): _archived()})

    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    section = next(s for s in data["sections"] if s["duration_min"] == 45)
    assert section["status"] == "empty"
    assert section["classes"] == []


def test_build_no_catalog_is_unavailable(db):
    data = classes.build(db, set(), as_of=date(2026, 10, 10), catalog=None)

    assert data["status"] == "unavailable"
    assert data["sections"] == []


def test_build_metadata_failure_is_unavailable(db):
    catalog = FakeCatalog(raise_on_metadata=TransientError("peloton: rate limited"))

    data = classes.build(db, set(), as_of=date(2026, 10, 10), catalog=catalog)

    assert data["status"] == "unavailable"
    assert "reason" in data


def test_build_metadata_malformed_is_unavailable(db):
    catalog = FakeCatalog(metadata={"class_types": "not a list"})

    data = classes.build(db, set(), as_of=date(2026, 10, 10), catalog=catalog)

    assert data["status"] == "unavailable"


def test_build_metadata_dict_shaped_class_types_is_unavailable(db):
    # Regression: the real API returns class_types/instructors as lists
    # (BO's live capture), never as the id-keyed dicts this code originally
    # assumed. A dict-shaped response must still degrade cleanly, not be
    # silently accepted.
    catalog = FakeCatalog(metadata={
        "class_types": {"Power Zone": "pz-id"},
        "instructors": {"inst-1": "Matt Wilpers"},
    })

    data = classes.build(db, set(), as_of=date(2026, 10, 10), catalog=catalog)

    assert data["status"] == "unavailable"


def test_build_one_search_failing_marks_only_that_section_unavailable_without_auth_error(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone")}

    class MalformedOnceCatalog(FakeCatalog):
        def fetch_archived_classes(self, class_type_id, duration_s, limit=8):
            self.search_calls.append((class_type_id, duration_s))
            if duration_s == 1200:
                return {"not": "a valid shape"}
            return _archived(_class("c1"))

    catalog = MalformedOnceCatalog()
    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    by_duration = {s["duration_min"]: s for s in data["sections"]}
    assert by_duration[20]["status"] == "unavailable"
    assert by_duration[30]["status"] == "ok"
    # Malformed response is not an auth/rate-limit error, so later calls still happen.
    assert len(catalog.search_calls) == 4


def test_build_auth_error_stops_further_calls(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone")}
    catalog = FakeCatalog(raise_on_search=AuthenticationError("peloton: session rejected"))

    data = classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    assert len(catalog.search_calls) == 1
    assert all(s["status"] == "unavailable" for s in data["sections"])


def test_build_call_count_bounded_by_types_times_durations(db):
    ids = set()
    for i, name in enumerate(["Power Zone", "Climb"]):
        ids.add(_insert_peloton_ride(db, str(i), f"2026-10-0{i + 1}T07:00:00+00:00", name))
    catalog = FakeCatalog()

    classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    assert catalog.metadata_calls == 1
    assert len(catalog.search_calls) == 2 * 4  # 2 used types x 4 durations
    assert all(call[1] in classes.DURATIONS_S.values() for call in catalog.search_calls)


def test_build_every_query_is_cycling_only_via_catalog_contract(db):
    # browse_category=cycling is hardcoded in PelotonConnector.fetch_archived_classes
    # itself (see tests/test_peloton_class_catalog.py); build() only ever calls
    # the ClassCatalog protocol method, never builds the query itself.
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone")}
    catalog = FakeCatalog()

    classes.build(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    assert catalog.search_calls  # at least one call was made through the catalog contract


# --- determinism / size ------------------------------------------------------

def test_build_byte_equal_on_rerun(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone", provider_class_id="class-1")}
    catalog = FakeCatalog(search_responses={("pz-id", 2700): _archived(_class("class-1"))})

    md1, js1 = classes.render(db, ids, as_of=date(2026, 10, 10), catalog=catalog)
    md2, js2 = classes.render(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    assert md1 == md2
    assert js1 == js2


def test_build_size_under_cap_with_six_types_eight_rows(db):
    names = ["Power Zone", "Low Impact", "Climb", "Music", "Intervals", "Progression"]
    ids = set()
    for i, name in enumerate(names):
        ids.add(_insert_peloton_ride(db, str(i), f"2026-10-0{(i % 9) + 1}T07:00:00+00:00", name))
    metadata = {
        "class_types": [_class_type(n, f"id-{n}") for n in names],
        "instructors": [_instructor("inst-1", "Matt Wilpers")],
    }
    rows = [_class(f"c{i}", title="X" * 40) for i in range(8)]
    catalog = FakeCatalog(metadata=metadata, search_responses={
        (f"id-{n}", d): _archived(*rows) for n in names for d in classes.DURATIONS_S.values()
    })

    md, js = classes.render(db, ids, as_of=date(2026, 10, 10), catalog=catalog)

    assert len(md.encode("utf-8")) < 50_000
    assert len(js.encode("utf-8")) < 50_000


# --- render_md() --------------------------------------------------------------

def test_render_md_unavailable_states_class_catalog_unavailable():
    md = classes.render_md({"as_of": "2026-10-10", "status": "unavailable", "reason": "x", "sections": [], "unmatched_tags": [], "not_shown": []})

    assert "class catalog unavailable" in md.lower()


def test_render_md_includes_not_shown_cap_line():
    data = {
        "as_of": "2026-10-10", "status": "ok", "sections": [], "unmatched_tags": [],
        "not_shown": ["Groove", "Theme"],
    }

    md = classes.render_md(data)

    assert "Not shown (cap): Groove, Theme" in md


def test_render_json_shape_round_trips(db):
    ids = {_insert_peloton_ride(db, "1", "2026-10-01T07:00:00+00:00", "Power Zone")}
    catalog = FakeCatalog(search_responses={("pz-id", 2700): _archived(_class("c1"))})

    _, js = classes.render(db, ids, as_of=date(2026, 10, 10), catalog=catalog)
    payload = json.loads(js)

    assert payload["as_of"] == "2026-10-10"
    assert payload["status"] == "ok"
    section = next(s for s in payload["sections"] if s["duration_min"] == 45)
    assert section["classes"][0]["id"] == "c1"
