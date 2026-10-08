"""
trainiq.app — Feature 0.1, wired as the composition root (Release Candidate Preparation)

Epic 0 deliberately stopped at "the app launches and Foundation is up, no
connectors registered." That gap — no code path from a fresh launch to a
real synchronization — was found during live-verification-readiness review
to be a genuine, unintentional release blocker, not a design choice ever
revisited after Epic 0. RC1-HF-001 wired existing components together.
RC1-HF-002 (this revision) closes the second half of the gap: there was
still no way for a user to actually ACQUIRE credentials in the first
place. A single entry point auto-triggers first-time setup when zero
connectors are configured — no separate `configure` command, per the
Chief Architect's explicit Design Review decision (Option A).

"Configured," not "enabled" (naming correction from review): a connector
is configured when enough information exists in CredentialStore (and, for
Eufy, config.json's device_id) to construct it — a factual state, not a
toggle.

Graceful Degradation applies at composition time, not just inside the
Sync Engine: a connector that fails to construct is logged and skipped;
it never prevents the other connectors from running.

Eufy's device_id: non-secret configuration, not a credential — per the
Chief Architect's explicit RC1 decision, it lives in a plain JSON file
(trainiq.config), not Keychain and not a new database migration. The
EUFY_DEVICE_ID environment variable introduced in RC1-HF-001 is retired
entirely as of this revision.

Runtime safety check (added after an observed real incident): main()'s
first action after configuring logging is to refuse to run if the
executing package is located inside macOS Trash — see trainiq.safety for
why and how. Every startup also logs the resolved package path, so any
future "which copy of TrainIQ is actually running" question is answered
directly by diagnostic.log rather than debugged from scratch.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trainiq.athlete.store import load_athlete_profile
from trainiq.config import get_athlete_timezone, get_eufy_device_id
from trainiq.connectors.base import Connector
from trainiq.connectors.eufy import EufyConnector
from trainiq.connectors.peloton import PelotonConnector
from trainiq.connectors.strava import StravaConnector
from trainiq.connectors.strava_unofficial import CRED_STRAVA_SESSION_COOKIE as STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE
from trainiq.connectors.strava_unofficial import PROVIDER as STRAVA_UNOFFICIAL_PROVIDER
from trainiq.connectors.strava_unofficial import StravaUnofficialConnector
from trainiq.credentials.store import CredentialStore
from trainiq.logging_setup import configure, diagnostic_logger, summary_logger
from trainiq.safety import RunningFromTrashError, assert_not_running_from_trash
from trainiq.setup_wizard import run_configure, run_first_time_setup
from trainiq.storage.schema import open_db
from trainiq.sync.engine import SynchronizationEngine

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
LOG_DIR = Path.home() / "Library" / "Logs" / "TrainIQ"
CONFIG_PATH = APP_SUPPORT_DIR / "config.json"


class _Reporter:
    """Wraps a loguru-bound logger. Every call both logs exactly as before
    AND appends the same message to self.lines, in order — giving callers
    (main()) a way to also print to stdout what's already being logged to
    the file sinks, without changing any existing log content."""

    def __init__(self, log) -> None:
        self._log = log
        self.lines: list[str] = []

    def info(self, msg: str) -> None:
        self._log.info(msg)
        self.lines.append(msg)

    def warning(self, msg: str) -> None:
        self._log.warning(msg)
        self.lines.append(msg)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="trainiq")
    parser.add_argument(
        "--configure", action="store_true",
        help="Add or reconfigure a connector, even if one or more are already configured.",
    )
    return parser.parse_args(argv)


def _build_configured_connectors(
    credential_store: CredentialStore,
    config_path: Path,
    reporter: "_Reporter | None" = None,
) -> list[Connector]:
    """Attempts to construct each known connector from whatever is
    currently in CredentialStore (and, for Eufy, config_path). A connector
    that isn't configured (no stored credentials yet — the expected state
    for a fresh install) or that fails to construct for any other reason
    is logged and skipped, never fatal — Graceful Degradation (ADR-009)
    applied at composition time, before any connector ever reaches the
    Sync Engine."""
    report = reporter if reporter is not None else _Reporter(summary_logger())
    connectors: list[Connector] = []

    # --- Strava ---
    try:
        if credential_store.get("strava", "refresh_token"):
            connectors.append(StravaConnector(credential_store))
            report.info("Strava: configured")
        else:
            report.info("Strava: skipped (not connected)")
    except Exception as exc:  # noqa: BLE001 - composition-time isolation is the point
        report.warning(f"Strava: skipped (construction failed: {exc})")

    # --- Strava (unofficial, session-cookie) ---
    try:
        if credential_store.get(STRAVA_UNOFFICIAL_PROVIDER, STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE):
            athlete_timezone = get_athlete_timezone(config_path)
            connectors.append(
                StravaUnofficialConnector(credential_store, local_timezone=athlete_timezone)
            )
            report.info("Strava (unofficial): configured")
        else:
            report.info("Strava (unofficial): skipped (not connected)")
    except Exception as exc:  # noqa: BLE001 - composition-time isolation is the point
        report.warning(f"Strava (unofficial): skipped (construction failed: {exc})")

    # --- Peloton ---
    try:
        has_peloton_creds = bool(
            credential_store.get("peloton", "email") and credential_store.get("peloton", "password")
        )
        if has_peloton_creds:
            connectors.append(PelotonConnector(credential_store))
            report.info("Peloton: configured")
        else:
            report.info("Peloton: skipped (not connected)")
    except Exception as exc:  # noqa: BLE001
        report.warning(f"Peloton: skipped (construction failed: {exc})")

    # --- Eufy ---
    try:
        has_eufy_creds = bool(
            credential_store.get("eufy", "email") and credential_store.get("eufy", "password")
        )
        eufy_device_id = get_eufy_device_id(config_path)
        if has_eufy_creds and eufy_device_id:
            connectors.append(EufyConnector(credential_store, device_id=eufy_device_id))
            report.info("Eufy: configured")
        elif has_eufy_creds and not eufy_device_id:
            report.info("Eufy: skipped (missing device_id in config.json)")
        else:
            report.info("Eufy: skipped (not connected)")
    except Exception as exc:  # noqa: BLE001
        report.warning(f"Eufy: skipped (construction failed: {exc})")

    return connectors


def _log_sync_summary(result, reporter: "_Reporter | None" = None) -> None:
    report = reporter if reporter is not None else _Reporter(summary_logger())
    for r in result.connector_results:
        if r.error:
            report.warning(f"{r.provider}: {r.state.value} — {r.error}")
        elif r.skipped_reason:
            report.info(f"{r.provider}: skipped this run — {r.skipped_reason}")
        else:
            report.info(
                f"{r.provider}: downloaded "
                f"{r.records_inserted + r.records_updated + r.records_malformed + r.records_skipped}, "
                f"inserted {r.records_inserted}, updated {r.records_updated}, "
                f"malformed {r.records_malformed}, skipped {r.records_skipped}"
            )


def _print_log_locations() -> None:
    print(f"\nFull logs: {LOG_DIR / 'summary.log'}, {LOG_DIR / 'diagnostic.log'}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    configure(LOG_DIR)
    log = summary_logger()
    log.info("Starting TrainIQ")

    try:
        package_path = assert_not_running_from_trash()
    except RunningFromTrashError as exc:
        diagnostic_logger().critical(str(exc))
        print(f"FATAL:\n{exc}", file=sys.stderr)
        return 1
    log.info(f"Executing package: {package_path}")

    db_path = APP_SUPPORT_DIR / "trainiq.db"
    conn = open_db(db_path)

    credential_store = CredentialStore(conn=conn)
    reporter = _Reporter(log)
    connectors = _build_configured_connectors(credential_store, CONFIG_PATH, reporter=reporter)

    if args.configure:
        run_configure(credential_store, CONFIG_PATH, status_lines=reporter.lines)
        reporter = _Reporter(log)
        connectors = _build_configured_connectors(credential_store, CONFIG_PATH, reporter=reporter)
    elif not connectors:
        run_first_time_setup(credential_store, CONFIG_PATH)
        reporter = _Reporter(log)
        connectors = _build_configured_connectors(credential_store, CONFIG_PATH, reporter=reporter)

    for line in reporter.lines:
        print(line)

    if not connectors:
        log.info("Nothing to synchronize.")
        print("Nothing to synchronize.")
        _print_log_locations()
        conn.close()
        return 0

    athlete_profile = load_athlete_profile(conn)  # loaded once, per ADR-038-era discipline
    log.info(f"Running synchronization ({len(connectors)} connector(s))")
    engine = SynchronizationEngine(conn, athlete_profile=athlete_profile)
    result = engine.run_once(connectors)
    sync_reporter = _Reporter(log)
    _log_sync_summary(result, reporter=sync_reporter)
    for line in sync_reporter.lines:
        print(line)
    _print_log_locations()

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
