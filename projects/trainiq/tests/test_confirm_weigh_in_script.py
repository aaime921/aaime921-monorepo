"""
Tests for scripts/confirm_weigh_in.py (ADR-039 / Issue #38, AC6's un-flag
mechanism). Exercises confirm_weigh_in() directly against a real schema-v4
database — the one part of this script that isn't just argparse/printing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from trainiq.storage.schema import open_db

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "confirm_weigh_in.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("confirm_weigh_in", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script_module()


@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


def _insert_weigh_in(conn, external_id, flagged, reason=None, provider="eufy"):
    conn.execute(
        "INSERT INTO weigh_ins (provider, external_id, timestamp, weight_kg, "
        "is_weight_flagged_implausible, weight_plausibility_reason) "
        "VALUES (?, ?, '2026-01-01T00:00:00+00:00', 20.0, ?, ?)",
        (provider, external_id, 1 if flagged else 0, reason),
    )
    conn.commit()


def test_confirming_a_flagged_reading_sets_confirmed_fields(db):
    _insert_weigh_in(db, "w1", flagged=True, reason="deviates from baseline")

    reason = script.confirm_weigh_in(db, "eufy", "w1")

    assert reason == "deviates from baseline"
    row = db.execute(
        "SELECT bo_confirmed_valid, bo_confirmed_at, is_weight_flagged_implausible FROM weigh_ins WHERE external_id = 'w1'"
    ).fetchone()
    assert row["bo_confirmed_valid"] == 1
    assert row["bo_confirmed_at"] is not None
    assert row["is_weight_flagged_implausible"] == 1  # original verdict untouched — auditable, not reverted


def test_confirming_a_nonexistent_row_raises_and_writes_nothing(db):
    with pytest.raises(script.NoSuchWeighIn):
        script.confirm_weigh_in(db, "eufy", "does-not-exist")


def test_confirming_an_unflagged_row_raises_and_writes_nothing(db):
    _insert_weigh_in(db, "w2", flagged=False)

    with pytest.raises(script.WeighInNotFlagged):
        script.confirm_weigh_in(db, "eufy", "w2")

    row = db.execute("SELECT bo_confirmed_valid FROM weigh_ins WHERE external_id = 'w2'").fetchone()
    assert row["bo_confirmed_valid"] == 0  # untouched


def test_confirming_is_scoped_to_provider_and_external_id(db):
    """A row with the same external_id under a different provider must
    not be confused for the one being confirmed."""
    _insert_weigh_in(db, "shared-id", flagged=True, reason="r1", provider="eufy")
    _insert_weigh_in(db, "shared-id", flagged=True, reason="r2", provider="other_scale")

    script.confirm_weigh_in(db, "eufy", "shared-id")

    eufy_row = db.execute(
        "SELECT bo_confirmed_valid FROM weigh_ins WHERE provider = 'eufy' AND external_id = 'shared-id'"
    ).fetchone()
    other_row = db.execute(
        "SELECT bo_confirmed_valid FROM weigh_ins WHERE provider = 'other_scale' AND external_id = 'shared-id'"
    ).fetchone()
    assert eufy_row["bo_confirmed_valid"] == 1
    assert other_row["bo_confirmed_valid"] == 0
