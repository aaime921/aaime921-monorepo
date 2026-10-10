"""
Tests for issue #73's cloud-sync deployment: `projects/trainiq/deploy/trainiq-data/`.
Fixtures only, no live GitHub/Actions calls (CONVENTIONS.md) — this exercises
the pure functions behind `sync.yml`'s steps and `sync.yml` itself as data
(parsed YAML), not an actual workflow run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

DEPLOY_DIR = Path(__file__).resolve().parent.parent / "deploy" / "trainiq-data"
SYNC_YML_PATH = DEPLOY_DIR / "sync.yml"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


render_status = _load_module("render_status", DEPLOY_DIR / "scripts" / "render_status.py")
refresh_filter = _load_module("refresh_filter", DEPLOY_DIR / "scripts" / "refresh_filter.py")
merge_credentials = _load_module("merge_credentials", DEPLOY_DIR / "scripts" / "merge_credentials.py")


# --- sync.yml itself (AC1, AC3, AC5, AC11) -----------------------------------


@pytest.fixture(scope="module")
def sync_yml() -> dict:
    with SYNC_YML_PATH.open() as f:
        return yaml.safe_load(f)


def test_sync_yml_parses_as_valid_yaml(sync_yml):
    assert sync_yml["name"]


def test_sync_yml_has_the_three_triggers(sync_yml):
    triggers = sync_yml["on"]
    assert "schedule" in triggers
    assert triggers["schedule"][0]["cron"]
    assert "workflow_dispatch" in triggers
    assert triggers["issues"]["types"] == ["opened"]


def test_sync_yml_has_a_single_run_concurrency_guard(sync_yml):
    concurrency = sync_yml["concurrency"]
    assert concurrency["group"] == "trainiq-sync"
    assert concurrency["cancel-in-progress"] is False


def test_sync_yml_has_contents_and_issues_write_permissions(sync_yml):
    permissions = sync_yml["permissions"]
    assert permissions["contents"] == "write"
    assert permissions["issues"] == "write"


def test_sync_yml_has_no_literal_secret_values(sync_yml):
    """AC1/AC10: the workflow references secrets only through
    `${{ secrets.* }}`/`${{ github.token }}` — never a bare value that
    looks like a credential or token sitting in the monorepo."""
    raw = SYNC_YML_PATH.read_text()
    assert "secrets." in raw
    # Every env line pulling in a provider credential must be a `secrets.`
    # (or `github.token`) expression, never a literal.
    for name in merge_credentials.CREDENTIAL_SECRET_NAMES:
        for line in raw.splitlines():
            if line.strip().startswith(f"{name}:"):
                assert "secrets." in line, f"{name} is not sourced from secrets.*: {line!r}"


# --- render_status.render() (AC7, AC12) --------------------------------------


def _status(providers: dict, warnings: list[str] | None = None) -> dict:
    return {
        "version": 1,
        "started_at": "2026-10-10T05:17:00+00:00",
        "finished_at": "2026-10-10T05:21:00+00:00",
        "exit_code": 0,
        "outcome": "ok",
        "providers": providers,
        "warnings": warnings or [],
    }


NOW = datetime(2026, 10, 10, 5, 21, 0, tzinfo=timezone.utc)


def test_render_ok_run_lists_every_provider_and_no_warning():
    status = _status({
        "strava_unofficial": {"status": "ok", "records_synced": 4, "last_activity_time": "2026-10-09T12:00:00+00:00"},
        "peloton": {"status": "ok", "records_synced": 1, "last_activity_time": "2026-10-09T06:00:00+00:00"},
    })
    state: dict = {}
    content = render_status.render(status, state, NOW, render_status.EXIT_OK)

    assert "strava_unofficial" in content
    assert "peloton" in content
    assert "⚠️" not in content
    assert state["strava_last_ok"] == "2026-10-10"


def test_render_partial_with_strava_expired_and_known_last_ok_date():
    status = _status({
        "strava_unofficial": {
            "status": "auth_expired", "records_synced": 0, "last_activity_time": None,
            "warning": "strava_unofficial: authentication expired or was rejected; re-authenticate ...",
        },
        "peloton": {"status": "ok", "records_synced": 2, "last_activity_time": "2026-10-09T06:00:00+00:00"},
    }, warnings=["strava_unofficial: authentication expired or was rejected; re-authenticate ..."])
    state = {"strava_last_ok": "2026-09-28"}

    content = render_status.render(status, state, NOW, render_status.EXIT_PARTIAL)

    assert (
        "⚠️ Strava cookie expired: Strava data not refreshed since 2026-09-28. "
        f"Renew: {render_status.RENEW_URL}"
    ) in content
    # A failing provider must never advance strava_last_ok.
    assert state["strava_last_ok"] == "2026-09-28"


def test_render_partial_with_strava_expired_and_unknown_last_ok_date():
    status = _status({
        "strava_unofficial": {"status": "auth_expired", "records_synced": 0, "last_activity_time": None},
    })
    state: dict = {}

    content = render_status.render(status, state, NOW, render_status.EXIT_PARTIAL)

    assert "Strava data not refreshed since unknown." in content
    assert "strava_last_ok" not in state


def test_render_failure_with_no_status_json():
    content = render_status.render(None, {}, NOW, render_status.EXIT_TOTAL_FAILURE)

    assert "Result: failed" in content
    assert "No status report was produced" in content


def test_render_never_contains_a_credential_looking_value():
    """AC10: nothing render() produces should be a secret value — it only
    ever sees names, counts and already-secret-free strings."""
    status = _status({
        "strava_unofficial": {
            "status": "failed", "records_synced": 0, "last_activity_time": None,
            "warning": "strava_unofficial: transient HTTP status 503",
        },
    })
    content = render_status.render(status, {}, NOW, render_status.EXIT_PARTIAL)
    assert "TRAINIQ_" not in content
    assert "Bearer " not in content


# --- refresh_filter.should_run() (AC3, AC9) ----------------------------------


def test_should_run_true_for_schedule_and_dispatch_with_no_payload():
    assert refresh_filter.should_run("schedule", {}) is True
    assert refresh_filter.should_run("workflow_dispatch", {}) is True


def test_should_run_true_for_refresh_titled_issue_from_a_collaborator():
    payload = {"issue": {"title": "refresh", "labels": [], "author_association": "OWNER"}}
    assert refresh_filter.should_run("issues", payload) is True


def test_should_run_true_for_refresh_labeled_issue_from_a_collaborator():
    payload = {
        "issue": {
            "title": "Please sync now",
            "labels": [{"name": "refresh"}],
            "author_association": "MEMBER",
        }
    }
    assert refresh_filter.should_run("issues", payload) is True


def test_should_run_false_for_an_unrelated_issue():
    payload = {"issue": {"title": "Peloton export looks wrong", "labels": [], "author_association": "OWNER"}}
    assert refresh_filter.should_run("issues", payload) is False


def test_should_run_false_for_a_refresh_issue_from_a_non_collaborator():
    payload = {"issue": {"title": "refresh", "labels": [], "author_association": "NONE"}}
    assert refresh_filter.should_run("issues", payload) is False


def test_should_run_false_for_other_event_names():
    assert refresh_filter.should_run("pull_request", {}) is False


# --- should_run() vs. sync.yml's own `if:` expression (AC3) -----------------


def _evaluate_github_actions_if_expression(event_name: str, payload: dict) -> bool:
    """Hand-written, independent re-expression of sync.yml's job-level
    `if:` in Python (GitHub Actions expression syntax can't be executed
    outside Actions). This is deliberately NOT a call into
    refresh_filter.should_run() — the point of this test is to catch a
    future edit to one without the other, so the two must be written
    independently and compared, not share an implementation."""
    if event_name in ("schedule", "workflow_dispatch"):
        return True
    if event_name != "issues":
        return False
    issue = payload.get("issue", {})
    title_matches = issue.get("title") == "refresh"
    label_matches = any(label.get("name") == "refresh" for label in issue.get("labels", []))
    association_ok = issue.get("author_association") in ("OWNER", "MEMBER", "COLLABORATOR")
    return (title_matches or label_matches) and association_ok


@pytest.mark.parametrize(
    "event_name, payload",
    [
        ("schedule", {}),
        ("workflow_dispatch", {}),
        ("issues", {"issue": {"title": "refresh", "labels": [], "author_association": "OWNER"}}),
        ("issues", {"issue": {"title": "refresh", "labels": [], "author_association": "NONE"}}),
        ("issues", {"issue": {"title": "x", "labels": [{"name": "refresh"}], "author_association": "COLLABORATOR"}}),
        ("issues", {"issue": {"title": "x", "labels": [], "author_association": "OWNER"}}),
        ("pull_request", {}),
    ],
)
def test_should_run_agrees_with_the_workflows_if_expression(event_name, payload):
    assert refresh_filter.should_run(event_name, payload) == _evaluate_github_actions_if_expression(event_name, payload)


# --- merge_credentials.merge() / build_stored() (precedence + changed-secret) --


def test_merge_prefers_stored_rotated_value_when_secret_is_unchanged():
    env = {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "seed-value"}
    stored = {
        "TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": {
            "value": "rotated-value",
            "seed_sha256": merge_credentials._sha256("seed-value"),
        }
    }
    assert merge_credentials.merge(env, stored) == {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "rotated-value"}


def test_merge_prefers_the_secret_when_the_bo_changed_it_since_seeding():
    env = {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "bo-updated-value"}
    stored = {
        "TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": {
            "value": "rotated-value",
            "seed_sha256": merge_credentials._sha256("old-seed-value"),
        }
    }
    assert merge_credentials.merge(env, stored) == {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "bo-updated-value"}


def test_merge_with_no_stored_state_passes_env_through():
    env = {"TRAINIQ_EUFY_EMAIL": "a@example.com", "TRAINIQ_EUFY_PASSWORD": "pw"}
    assert merge_credentials.merge(env, None) == env


def test_merge_keeps_a_stored_credential_whose_secret_was_removed():
    env: dict = {}
    stored = {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": {"value": "rotated-value", "seed_sha256": "whatever"}}
    assert merge_credentials.merge(env, stored) == {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "rotated-value"}


def test_build_stored_seeds_against_this_runs_starting_secret():
    env = {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "seed-value"}
    rotated = {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "new-rotated-value"}

    stored = merge_credentials.build_stored(env, rotated)

    assert stored["TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN"]["value"] == "new-rotated-value"
    assert stored["TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN"]["seed_sha256"] == merge_credentials._sha256("seed-value")


def test_round_trip_seal_then_merge_prefers_rotation_until_secret_changes():
    """A rotation this run must be exactly what next run's merge() picks,
    as long as the BO hasn't touched the underlying secret."""
    env_this_run = {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "seed-value"}
    rotated_this_run = {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "new-rotated-value"}

    stored_for_next_run = merge_credentials.build_stored(env_this_run, rotated_this_run)

    next_run_env_unchanged = {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "seed-value"}
    assert merge_credentials.merge(next_run_env_unchanged, stored_for_next_run) == rotated_this_run

    next_run_env_changed = {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "bo-reconnected-value"}
    assert merge_credentials.merge(next_run_env_changed, stored_for_next_run) == next_run_env_changed


def test_credential_secret_names_matches_every_credential_type_used_by_a_connector():
    """Guards the README's secrets table and the merge/mask allow-list
    against drifting from the connectors that actually read these env
    vars (trainiq.credentials.env_backend's naming convention)."""
    expected = {
        "TRAINIQ_STRAVA_REFRESH_TOKEN", "TRAINIQ_STRAVA_ACCESS_TOKEN", "TRAINIQ_STRAVA_EXPIRES_AT",
        "TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE", "TRAINIQ_STRAVA_UNOFFICIAL_SESSION_OBTAINED_AT",
        "TRAINIQ_STRAVA_UNOFFICIAL_SESSION_EXPIRES_AT",
        "TRAINIQ_PELOTON_EMAIL", "TRAINIQ_PELOTON_PASSWORD", "TRAINIQ_PELOTON_SESSION_ID",
        "TRAINIQ_PELOTON_SESSION_EXPIRES_AT", "TRAINIQ_PELOTON_MANUAL_BEARER_TOKEN",
        "TRAINIQ_PELOTON_OAUTH_ACCESS_TOKEN", "TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN",
        "TRAINIQ_PELOTON_OAUTH_EXPIRES_AT",
        "TRAINIQ_EUFY_EMAIL", "TRAINIQ_EUFY_PASSWORD", "TRAINIQ_EUFY_ACCESS_TOKEN",
        "TRAINIQ_EUFY_REFRESH_TOKEN", "TRAINIQ_EUFY_EXPIRES_AT",
    }
    assert merge_credentials.CREDENTIAL_SECRET_NAMES == frozenset(expected)


# --- CLI smoke tests ----------------------------------------------------------


def test_render_status_cli_writes_status_md_and_state_json(tmp_path):
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(_status({"peloton": {"status": "ok", "records_synced": 1}})))
    state_path = tmp_path / "state.json"
    out_path = tmp_path / "status.md"

    rc = render_status.main([
        "--status-json", str(status_path),
        "--state-json", str(state_path),
        "--exit-code", "0",
        "--out", str(out_path),
    ])

    assert rc == 0
    assert out_path.exists()
    assert "strava_last_ok" not in json.loads(state_path.read_text())


def test_refresh_filter_cli_exit_code_reflects_should_run(tmp_path):
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({"issue": {"title": "refresh", "labels": [], "author_association": "OWNER"}}))

    rc = refresh_filter.main(["--event-name", "issues", "--payload-json", str(payload_path)])

    assert rc == 0


def test_merge_credentials_cli_seal_then_merge_round_trip(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    for name in merge_credentials.CREDENTIAL_SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN", "seed-value")

    rotated_path = tmp_path / "creds_out.json"
    rotated_path.write_text(json.dumps({"version": 1, "credentials": {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "new-value"}}))
    sealed_path = tmp_path / "creds_sealed.json"

    rc = merge_credentials.main(["--mode", "seal", "--rotated", str(rotated_path), "--out", str(sealed_path)])
    assert rc == 0

    github_env_path = tmp_path / "github_env"
    github_env_path.write_text("")
    rc = merge_credentials.main([
        "--mode", "merge", "--stored", str(sealed_path), "--github-env", str(github_env_path),
    ])
    assert rc == 0
    assert "TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN=new-value" in github_env_path.read_text()
