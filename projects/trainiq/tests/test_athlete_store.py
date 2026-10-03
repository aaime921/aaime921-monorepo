"""
Tests for Epic 7, slice 2: athlete profile persistence (load/save). No
SynchronizationEngine, no training-load engine — that wiring is the next
slice. This tests the CRUD layer entirely on its own, against a real
SQLite connection (schema v3).
"""

from __future__ import annotations

import ast

import pytest

from trainiq.athlete.profile import AthleteProfile
from trainiq.athlete.store import load_athlete_profile, save_athlete_profile
from trainiq.storage.schema import open_db


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


# --- load_athlete_profile() ------------------------------------------------

def test_load_returns_none_when_no_profile_persisted_yet(db):
    """The genuinely "nothing known" case — a fresh install, before any
    setup has run. Distinguished deliberately from a profile with all
    fields None (see the next test)."""
    assert load_athlete_profile(db) is None


def test_load_returns_a_real_profile_with_none_fields_when_row_exists_but_sparse(db):
    """A row that exists but is mostly empty is NOT the same as no row at
    all — this is the specific distinction the module's docstring commits
    to, verified directly."""
    db.execute("INSERT INTO athlete_profile (id, sex) VALUES (1, 'male')")
    db.commit()

    profile = load_athlete_profile(db)

    assert profile is not None
    assert profile.sex == "male"
    assert profile.resting_hr is None
    assert profile.max_hr is None
    assert profile.ftp_watts is None


def test_load_returns_all_five_fields_correctly(db):
    db.execute(
        "INSERT INTO athlete_profile (id, sex, date_of_birth, resting_hr, max_hr, ftp_watts) "
        "VALUES (1, 'female', '1990-05-15', 52, 185, 230)"
    )
    db.commit()

    profile = load_athlete_profile(db)

    assert profile == AthleteProfile(
        sex="female", date_of_birth="1990-05-15", resting_hr=52, max_hr=185, ftp_watts=230
    )


# --- save_athlete_profile() -------------------------------------------------

def test_save_then_load_round_trips_exactly(db):
    original = AthleteProfile(sex="male", date_of_birth="1988-03-01", resting_hr=48, max_hr=192, ftp_watts=260)

    save_athlete_profile(db, original)
    loaded = load_athlete_profile(db)

    assert loaded == original


def test_save_with_partial_fields_leaves_others_null(db):
    save_athlete_profile(db, AthleteProfile(sex="male"))

    loaded = load_athlete_profile(db)

    assert loaded == AthleteProfile(sex="male", date_of_birth=None, resting_hr=None, max_hr=None, ftp_watts=None)


def test_save_is_an_upsert_never_creates_a_second_row(db):
    """The realistic usage pattern: the athlete updates their profile over
    time. Must always replace the singleton row, never violate the
    CHECK(id=1) constraint from slice 1 by attempting a second insert."""
    save_athlete_profile(db, AthleteProfile(sex="male", resting_hr=50))
    save_athlete_profile(db, AthleteProfile(sex="male", resting_hr=50, ftp_watts=250))

    count = db.execute("SELECT COUNT(*) FROM athlete_profile").fetchone()[0]
    loaded = load_athlete_profile(db)

    assert count == 1
    assert loaded.ftp_watts == 250


def test_save_can_clear_a_previously_set_field(db):
    """Upsert semantics must be a full replace, not a merge — saving a new
    AthleteProfile with a field left as None must actually clear it, not
    silently preserve the old value."""
    save_athlete_profile(db, AthleteProfile(sex="male", ftp_watts=250))
    save_athlete_profile(db, AthleteProfile(sex="male", ftp_watts=None))

    loaded = load_athlete_profile(db)

    assert loaded.ftp_watts is None


def test_repeated_save_of_identical_profile_is_idempotent(db):
    profile = AthleteProfile(sex="female", resting_hr=55, max_hr=180)
    save_athlete_profile(db, profile)
    save_athlete_profile(db, profile)
    save_athlete_profile(db, profile)

    count = db.execute("SELECT COUNT(*) FROM athlete_profile").fetchone()[0]
    assert count == 1
    assert load_athlete_profile(db) == profile


# --- Scope containment: no Sync Engine dependency yet -----------------------

def test_store_module_has_no_dependency_on_sync_engine():
    """This slice deliberately does not wire into SynchronizationEngine —
    that's the next slice. Checked at the actual import level."""
    import trainiq.athlete.store as store_module

    with open(store_module.__file__) as f:
        tree = ast.parse(f.read())

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(m.startswith("trainiq.sync") for m in imported_modules)


def test_athlete_profile_dataclass_itself_gained_no_persistence_methods():
    """The Chief Architect's explicit requirement, re-verified at this
    slice: AthleteProfile stays a pure model. Persistence logic belongs
    entirely in this store module, never as methods on the dataclass."""
    import trainiq.athlete.profile as profile_module

    with open(profile_module.__file__) as f:
        tree = ast.parse(f.read())

    class_def = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "AthleteProfile")
    method_names = [n.name for n in class_def.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert method_names == []
