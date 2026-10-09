"""
Tests for scripts/backfill_strava_streams.py (issue #50) — the thin CLI
wrapper around trainiq.connectors.strava_streams.enrich_strava_streams(),
which is itself thoroughly covered in tests/test_strava_streams.py. These
tests only verify the script's own wiring: dedup runs first, authenticate()
is checked, and auth/transient failures are reported with a non-zero exit
code instead of raising out of main().
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from trainiq.connectors.strava_unofficial import PROVIDER
from trainiq.storage.schema import open_db

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill_strava_streams.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("backfill_strava_streams", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script_module()


@pytest.fixture(autouse=True)
def in_memory_keyring():
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    class _InMemoryKeyring(FailKeyring):
        priority = 1

        def __init__(self):
            self._store: dict[tuple[str, str], str] = {}

        def set_password(self, service, username, password):
            self._store[(service, username)] = password

        def get_password(self, service, username):
            return self._store.get((service, username))

        def delete_password(self, service, username):
            key = (service, username)
            if key not in self._store:
                from keyring.errors import PasswordDeleteError
                raise PasswordDeleteError("not found")
            del self._store[key]

    original = keyring.get_keyring()
    keyring.set_keyring(_InMemoryKeyring())
    yield
    keyring.set_keyring(original)


def _seed_activity(db_path: Path) -> None:
    conn = open_db(db_path)
    conn.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES (?, 's1', '2026-01-05T07:00:00+00:00', 1800, 'running', NULL,
                NULL, NULL, NULL, NULL, NULL, NULL, 'unknown', 1.0)
        """,
        (PROVIDER,),
    )
    conn.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES (?, 's1', '{}', '2026-01-05T07:00:00+00:00')",
        (PROVIDER,),
    )
    conn.commit()
    conn.close()


class _FakeConnector:
    def __init__(self, authenticated: bool = True):
        self._authenticated = authenticated

    def authenticate(self):
        return self._authenticated

    def fetch_activity_streams(self, activity_id):
        return {"heartrate": [100, 110], "time": [0, 10]}


def test_main_runs_dedup_then_enriches_and_exits_zero(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "trainiq.db"
    _seed_activity(db_path)
    monkeypatch.setattr(script, "StravaUnofficialConnector", lambda credential_store: _FakeConnector())
    monkeypatch.setattr(sys, "argv", ["backfill_strava_streams.py", "--db-path", str(db_path)])

    exit_code = script.main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "STREAMS BACKFILL RESULT" in captured.out
    assert "ok:           1" in captured.out

    conn = open_db(db_path)
    row = dict(conn.execute(
        "SELECT streams_fetch_status FROM normalized_activities WHERE provider = ? AND external_id = 's1'",
        (PROVIDER,),
    ).fetchone())
    conn.close()
    assert row["streams_fetch_status"] == "ok"


def test_main_authenticate_failure_prints_message_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "trainiq.db"
    _seed_activity(db_path)
    monkeypatch.setattr(script, "StravaUnofficialConnector", lambda credential_store: _FakeConnector(authenticated=False))
    monkeypatch.setattr(sys, "argv", ["backfill_strava_streams.py", "--db-path", str(db_path)])

    exit_code = script.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Could not authenticate" in captured.out


def test_main_transient_error_during_enrichment_prints_message_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    from trainiq.sync.engine import TransientError

    class _RaisingConnector(_FakeConnector):
        def fetch_activity_streams(self, activity_id):
            raise TransientError("strava_unofficial: rate limited", retry_after_s=60)

    db_path = tmp_path / "trainiq.db"
    _seed_activity(db_path)
    monkeypatch.setattr(script, "StravaUnofficialConnector", lambda credential_store: _RaisingConnector())
    monkeypatch.setattr(sys, "argv", ["backfill_strava_streams.py", "--db-path", str(db_path)])

    exit_code = script.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Stopped:" in captured.out
