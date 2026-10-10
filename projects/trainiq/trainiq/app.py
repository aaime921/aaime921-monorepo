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

Issue #70 (headless/cloud run): `--headless` (also `TRAINIQ_HEADLESS=1`)
skips the setup wizard entirely (never prompts), always writes
`status.json` (trainiq.headless), and exits with one of
trainiq.headless's documented codes instead of always 0/1. DB/config/log
paths are resolved via trainiq.paths — flag > env var > today's exact
macOS defaults (`APP_SUPPORT_DIR`/`CONFIG_PATH`/`LOG_DIR` below, still the
module-level constants tests redirect, now themselves computed from
`trainiq.paths.platform_defaults()` so an unpatched Linux run gets XDG
paths instead of a nonsensical `~/Library` one). Interactive/Mac behavior
is unaffected when no flag/env override is given (AC10) — see
`main()`'s `headless` branch, taken only when asked for.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from trainiq.athlete.store import load_athlete_profile
from trainiq.config import get_athlete_timezone, get_eufy_device_id
from trainiq.connectors.base import Connector
from trainiq.connectors.eufy import EufyConnector
from trainiq.connectors.peloton import PelotonConnector
from trainiq.connectors.strava import StravaConnector
from trainiq.connectors.strava_streams import NEW_PER_SYNC_CAP, enrich_strava_streams
from trainiq.connectors.strava_unofficial import CRED_STRAVA_SESSION_COOKIE as STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE
from trainiq.connectors.strava_unofficial import PROVIDER as STRAVA_UNOFFICIAL_PROVIDER
from trainiq.connectors.strava_unofficial import StravaUnofficialConnector
from trainiq.credentials.store import ENV_CREDENTIALS_OUT, CredentialStore
from trainiq.dedup.detector import run_backfill as run_dedup_backfill
from trainiq import headless as headless_mod
from trainiq.export import run_export
from trainiq.logging_setup import DEFAULT_LOG_DIR, configure, diagnostic_logger, summary_logger
from trainiq.paths import Paths, platform_defaults, resolve_paths
from trainiq.safety import RunningFromTrashError, assert_not_running_from_trash
from trainiq.setup_wizard import run_configure, run_first_time_setup
from trainiq.storage.schema import open_db
from trainiq.sync.engine import AuthenticationError, SynchronizationEngine, TransientError

_DEFAULT_PATHS = platform_defaults()
APP_SUPPORT_DIR = _DEFAULT_PATHS.db_path.parent
LOG_DIR = _DEFAULT_PATHS.log_dir
CONFIG_PATH = _DEFAULT_PATHS.config_path


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

    def echo(self, msg: str) -> None:
        """Append to self.lines for console printing only. Used when the
        message was already logged elsewhere (the Sync Engine's own
        summary_logger call) so it isn't written to summary.log twice."""
        self.lines.append(msg)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="trainiq")
    parser.add_argument(
        "--configure", action="store_true",
        help="Add or reconfigure a connector, even if one or more are already configured.",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run non-interactively: never prompt, always write status.json, "
             "exit with a status-specific code (also settable via TRAINIQ_HEADLESS=1).",
    )
    parser.add_argument(
        "--credentials-out", default=None, metavar="PATH",
        help="Where to write credentials rotated during this run (env backend only); "
             "also settable via TRAINIQ_CREDENTIALS_OUT.",
    )
    parser.add_argument(
        "--status-json", default=None, metavar="PATH",
        help="Where to write the machine-readable status report (headless mode); "
             "also settable via TRAINIQ_STATUS_JSON. Default: <log-dir>/status.json.",
    )
    parser.add_argument(
        "--db-path", default=None, metavar="PATH",
        help="Database file location; also settable via TRAINIQ_DB_PATH.",
    )
    parser.add_argument(
        "--config-path", default=None, metavar="PATH",
        help="Config file location; also settable via TRAINIQ_CONFIG_PATH.",
    )
    parser.add_argument(
        "--log-dir", default=None, metavar="PATH",
        help="Log directory; also settable via TRAINIQ_LOG_DIR.",
    )
    # Issue #71: a subcommand, added alongside (not instead of) the flat
    # bare-`trainiq`/`--configure` shape above — `dest="command"` defaults
    # to None with no subcommand given, so existing `trainiq` and
    # `trainiq --configure` invocations are completely unaffected.
    subparsers = parser.add_subparsers(dest="command")
    export_parser = subparsers.add_parser(
        "export", help="Write Markdown/JSON summaries for the coach to --out."
    )
    export_parser.add_argument("--out", type=Path, required=True)
    export_parser.add_argument("--db-path", type=Path, default=None)
    export_parser.add_argument("--config-path", type=Path, default=None)
    export_parser.add_argument(
        "--as-of", type=str, default=None,
        help="YYYY-MM-DD; defaults to the latest data date (never wall-clock \"now\").",
    )
    export_parser.add_argument(
        "--no-classes", action="store_true",
        help="Skip the Peloton class-candidates lookup (issue #72); "
             "peloton_classes.md/.json are still written, marked unavailable.",
    )
    return parser.parse_args(argv)


def _authenticate_class_catalog(conn, config_path: Path) -> "PelotonConnector | None":
    """Issue #72: reuses the existing, already-configured PelotonConnector
    — no new auth path. A missing credential or a failed authenticate()
    call both return None here without raising (same as every path through
    `authenticate()` itself), which `trainiq.export.classes.build` turns
    into "class catalog unavailable" (AC 7) rather than failing the whole
    export."""
    credential_store = CredentialStore(conn=conn)
    try:
        connector = PelotonConnector(credential_store)
        if connector.authenticate():
            return connector
    except Exception:  # noqa: BLE001 - never fail the export over the class catalog
        pass
    return None


def _run_export_command(args: argparse.Namespace) -> int:
    configure(LOG_DIR)
    db_path = args.db_path or (APP_SUPPORT_DIR / "trainiq.db")
    config_path = args.config_path or CONFIG_PATH
    as_of = date.fromisoformat(args.as_of) if args.as_of else None

    conn = open_db(db_path)
    try:
        catalog = None if args.no_classes else _authenticate_class_catalog(conn, config_path)
        written = run_export(conn, args.out, config_path, as_of=as_of, catalog=catalog)
    finally:
        conn.close()

    for path in written:
        print(f"wrote {path}")
    return 0


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
            report.echo(r.summary_line)


def _run_strava_streams_enrichment(conn, connectors: list[Connector], reporter: "_Reporter") -> None:
    """Issue #50 post-sync hook: runs dedup (idempotent, so Peloton-linked
    activities are correctly excluded) then enriches up to
    NEW_PER_SYNC_CAP never-attempted strava_unofficial activities with
    per-activity streams. No-op if the unofficial Strava connector isn't
    configured this run. An auth/transient failure here is reported and
    swallowed, not fatal to the sync as a whole (ADR-009) — the connector's
    own cookie-clearing (on AuthenticationError) already surfaces as a
    recovery need on the NEXT regular sync's authenticate() call."""
    strava_unofficial = next(
        (c for c in connectors if isinstance(c, StravaUnofficialConnector)), None
    )
    if strava_unofficial is None:
        return

    try:
        run_dedup_backfill(conn)
        if not strava_unofficial.authenticate():
            return
        result = enrich_strava_streams(conn, strava_unofficial, limit=NEW_PER_SYNC_CAP)
        reporter.info(
            f"{STRAVA_UNOFFICIAL_PROVIDER} streams: processed {result.processed} "
            f"(ok {result.ok}, no_streams {result.no_streams}, unavailable {result.unavailable})"
        )
    except (AuthenticationError, TransientError) as exc:
        reporter.warning(f"{STRAVA_UNOFFICIAL_PROVIDER} streams enrichment stopped: {exc}")


def _print_log_locations(log_dir: Path) -> None:
    print(f"\nFull logs: {log_dir / 'summary.log'}, {log_dir / 'diagnostic.log'}")


def _seed_config_from_env(config_path: Path) -> str | None:
    """TRAINIQ_CONFIG_JSON (issue #70): if set and `config_path` doesn't
    already exist, seeds it from that env var — never overwrites a real
    config file. Returns an error message if the env var is set but isn't
    valid JSON, else None. Headless-only (see `_run_headless`): an
    interactive Mac run has no reason to have this env var set, and
    gating it this way keeps AC10 (Mac behavior unchanged) trivially true
    rather than merely tested."""
    raw = os.environ.get("TRAINIQ_CONFIG_JSON")
    if not raw or config_path.exists():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"TRAINIQ_CONFIG_JSON is not valid JSON: {exc}"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(parsed, indent=2))
    return None


def _resolve_status_json_path(args: argparse.Namespace, log_dir: Path) -> Path:
    raw = args.status_json or os.environ.get(headless_mod.ENV_STATUS_JSON) or str(log_dir / "status.json")
    return Path(raw)


def _run_headless(args: argparse.Namespace, db_path: Path, config_path: Path, log_dir: Path, log) -> int:
    """The `--headless` path: never prompts (no wizard/`--configure`
    branch is reachable here), always writes `status.json`, and returns
    one of `trainiq.headless`'s documented exit codes. The whole body is
    wrapped so an unexpected crash still writes a `failed` status.json
    instead of leaving a workflow with nothing to inspect (AC6/AC8)."""
    report = headless_mod.StatusReport(started_at=headless_mod.iso_now())
    status_path = _resolve_status_json_path(args, log_dir)
    conn = None
    try:
        try:
            package_path = assert_not_running_from_trash()
        except RunningFromTrashError as exc:
            diagnostic_logger().critical(str(exc))
            report.warnings.append(str(exc))
            return headless_mod.EXIT_TOTAL_FAILURE
        log.info(f"Executing package: {package_path}")

        seed_error = _seed_config_from_env(config_path)
        if seed_error:
            diagnostic_logger().error(seed_error)
            report.warnings.append(seed_error)
            return headless_mod.EXIT_TOTAL_FAILURE

        conn = open_db(db_path)
        credentials_out = args.credentials_out or os.environ.get(ENV_CREDENTIALS_OUT)
        credential_store = CredentialStore(conn=conn, credentials_out=credentials_out)
        reporter = _Reporter(log)
        connectors = _build_configured_connectors(credential_store, config_path, reporter=reporter)
        for line in reporter.lines:
            print(line)

        if not connectors:
            log.info("Nothing to synchronize.")
            report.warnings.append("no connectors configured")
            return headless_mod.EXIT_TOTAL_FAILURE

        athlete_profile = load_athlete_profile(conn)
        log.info(f"Running synchronization ({len(connectors)} connector(s))")
        engine = SynchronizationEngine(conn, athlete_profile=athlete_profile)
        result = engine.run_once(connectors)
        sync_reporter = _Reporter(log)
        _log_sync_summary(result, reporter=sync_reporter)
        _run_strava_streams_enrichment(conn, connectors, reporter=sync_reporter)
        for line in sync_reporter.lines:
            print(line)
        enrichment_warnings = [line for line in sync_reporter.lines if "streams enrichment stopped" in line]

        computed = headless_mod.build_status_report(
            report.started_at, conn, connectors, result.connector_results,
            extra_warnings=report.warnings + enrichment_warnings,
        )
        report.providers = computed.providers
        report.warnings = computed.warnings
        return report.exit_code()
    except Exception as exc:  # noqa: BLE001 — headless's own outer resilience boundary
        diagnostic_logger().opt(exception=True).error(f"headless run failed unexpectedly: {exc}")
        report.warnings.append(f"{type(exc).__name__}: {exc}")
        return headless_mod.EXIT_TOTAL_FAILURE
    finally:
        if conn is not None:
            conn.close()
        report.finished_at = headless_mod.iso_now()
        headless_mod.write_status_report(report, status_path)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    headless = args.headless or os.environ.get(headless_mod.ENV_HEADLESS) == "1"

    if args.command == "export":
        return _run_export_command(args)

    if headless and args.configure:
        print("FATAL: --headless cannot be combined with --configure", file=sys.stderr)
        return headless_mod.EXIT_USAGE_ERROR

    run_paths = resolve_paths(
        args,
        defaults=Paths(db_path=APP_SUPPORT_DIR / "trainiq.db", config_path=CONFIG_PATH, log_dir=LOG_DIR),
    )
    db_path, config_path, log_dir = run_paths.db_path, run_paths.config_path, run_paths.log_dir

    configure(log_dir)
    log = summary_logger()
    log.info("Starting TrainIQ")

    if headless:
        return _run_headless(args, db_path, config_path, log_dir, log)

    try:
        package_path = assert_not_running_from_trash()
    except RunningFromTrashError as exc:
        diagnostic_logger().critical(str(exc))
        print(f"FATAL:\n{exc}", file=sys.stderr)
        return 1
    log.info(f"Executing package: {package_path}")

    conn = open_db(db_path)

    credential_store = CredentialStore(conn=conn)
    reporter = _Reporter(log)
    connectors = _build_configured_connectors(credential_store, config_path, reporter=reporter)

    if args.configure:
        run_configure(credential_store, config_path, status_lines=reporter.lines)
        reporter = _Reporter(log)
        connectors = _build_configured_connectors(credential_store, config_path, reporter=reporter)
    elif not connectors:
        run_first_time_setup(credential_store, config_path)
        reporter = _Reporter(log)
        connectors = _build_configured_connectors(credential_store, config_path, reporter=reporter)

    for line in reporter.lines:
        print(line)

    if not connectors:
        log.info("Nothing to synchronize.")
        print("Nothing to synchronize.")
        _print_log_locations(log_dir)
        conn.close()
        return 0

    athlete_profile = load_athlete_profile(conn)  # loaded once, per ADR-038-era discipline
    log.info(f"Running synchronization ({len(connectors)} connector(s))")
    engine = SynchronizationEngine(conn, athlete_profile=athlete_profile)
    result = engine.run_once(connectors)
    sync_reporter = _Reporter(log)
    _log_sync_summary(result, reporter=sync_reporter)
    _run_strava_streams_enrichment(conn, connectors, reporter=sync_reporter)
    for line in sync_reporter.lines:
        print(line)
    _print_log_locations(log_dir)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
