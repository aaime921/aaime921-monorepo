"""
trainiq.headless — Issue #70

`--headless`/`TRAINIQ_HEADLESS=1` support: a non-interactive run that
never prompts, always writes a machine-readable `status.json`, and exits
with one of a small, documented set of codes so a calling workflow can
tell "all ok" from "partial" from "total failure" without parsing logs.

Exit codes:
    EXIT_OK            = 0   every configured provider synced ok.
    EXIT_TOTAL_FAILURE = 1   nothing configured, every provider
                             failed/auth_expired, or the run crashed
                             before it could finish.
    EXIT_USAGE_ERROR   = 2   bad CLI arguments (argparse's own code,
                             reused rather than shadowed; `--headless`
                             combined with `--configure` also lands here).
    EXIT_PARTIAL       = 3   at least one provider ok AND at least one
                             failed/auth_expired. Deliberately not 1 or 2,
                             so a workflow can `continue-on-error` on 3
                             alone.

`status.json` (schema version 1) is written on every headless run,
success or failure (trainiq/app.py's `_run_headless` wraps the whole run
in `try/finally`). It never contains a secret value (AC4): only provider
names, the *names* of expected secrets in human-readable warning text,
and `str(exception)` messages, which every connector already keeps
secret-free (existing AuthenticationError call sites never interpolate a
credential value).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_AUTH_EXPIRED = "auth_expired"

OUTCOME_OK = "ok"
OUTCOME_PARTIAL = "partial"
OUTCOME_FAILED = "failed"

EXIT_OK = 0
EXIT_TOTAL_FAILURE = 1
EXIT_USAGE_ERROR = 2
EXIT_PARTIAL = 3

SCHEMA_VERSION = 1

ENV_HEADLESS = "TRAINIQ_HEADLESS"
ENV_STATUS_JSON = "TRAINIQ_STATUS_JSON"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProviderStatus:
    status: str
    records_synced: int = 0
    last_activity_time: Optional[str] = None
    warning: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {
            "status": self.status,
            "records_synced": self.records_synced,
            "last_activity_time": self.last_activity_time,
        }
        if self.warning:
            d["warning"] = self.warning
        return d


@dataclass
class StatusReport:
    started_at: str
    providers: dict[str, ProviderStatus] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    finished_at: Optional[str] = None

    def outcome(self) -> str:
        if not self.providers:
            return OUTCOME_FAILED
        statuses = [p.status for p in self.providers.values()]
        if all(s == STATUS_OK for s in statuses):
            return OUTCOME_OK
        if any(s == STATUS_OK for s in statuses):
            return OUTCOME_PARTIAL
        return OUTCOME_FAILED

    def exit_code(self) -> int:
        outcome = self.outcome()
        if outcome == OUTCOME_OK:
            return EXIT_OK
        if outcome == OUTCOME_PARTIAL:
            return EXIT_PARTIAL
        return EXIT_TOTAL_FAILURE

    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "started_at": self.started_at,
            "finished_at": self.finished_at or iso_now(),
            "exit_code": self.exit_code(),
            "outcome": self.outcome(),
            "providers": {name: p.to_dict() for name, p in self.providers.items()},
            "warnings": list(self.warnings),
        }


def _provider_status_from_result(result) -> str:
    if getattr(result, "auth_failed", False):
        return STATUS_AUTH_EXPIRED
    if result.error or result.skipped_reason:
        return STATUS_FAILED
    return STATUS_OK


def _provider_warning(provider: str, status: str, error: Optional[str]) -> Optional[str]:
    if status == STATUS_OK:
        return None
    if status == STATUS_AUTH_EXPIRED:
        detail = f" ({error})" if error else ""
        return (
            f"{provider}: authentication expired or was rejected{detail}; "
            f"re-authenticate and refresh its TRAINIQ_{provider.upper()}_* credential(s)"
        )
    if not error:
        return f"{provider}: sync failed"
    # Connector/engine error strings are already provider-prefixed
    # ("strava: rate limited") in every existing call site — avoid
    # doubling it up into "strava: strava: rate limited".
    return error if error.startswith(f"{provider}:") else f"{provider}: {error}"


def last_activity_time(conn, provider: str, record_kind) -> Optional[str]:
    """`SELECT MAX(...)` only — never fabricated; `None` if this provider
    has no rows yet. ACTIVITY-kind providers (default) read
    `normalized_activities.start_time`; WEIGH_IN-kind (Eufy) read
    `weigh_ins.timestamp` instead."""
    from trainiq.connectors.base import RecordKind

    if record_kind == RecordKind.WEIGH_IN:
        row = conn.execute("SELECT MAX(timestamp) AS v FROM weigh_ins WHERE provider = ?", (provider,)).fetchone()
    else:
        row = conn.execute(
            "SELECT MAX(start_time) AS v FROM normalized_activities WHERE provider = ?", (provider,)
        ).fetchone()
    return row["v"] if row else None


def build_status_report(
    started_at: str,
    conn,
    connectors: Iterable,
    sync_results: Iterable,
    extra_warnings: Optional[list[str]] = None,
) -> StatusReport:
    """`connectors` and `sync_results` are matched by `provider` name, as
    `SynchronizationEngine.run_once()` returns one `ConnectorSyncResult`
    per input connector."""
    report = StatusReport(started_at=started_at, warnings=list(extra_warnings or []))
    by_provider = {c.provider: c for c in connectors}
    for result in sync_results:
        status = _provider_status_from_result(result)
        connector = by_provider.get(result.provider)
        record_kind = getattr(connector, "record_kind", None)
        report.providers[result.provider] = ProviderStatus(
            status=status,
            records_synced=result.records_upserted,
            last_activity_time=last_activity_time(conn, result.provider, record_kind),
            warning=_provider_warning(result.provider, status, result.error),
        )
    return report


def write_status_report(report: StatusReport, path: Path) -> None:
    """Atomic, owner-only-permissions write — same pattern as
    `EnvBackend`'s credentials-out writer, so a crash mid-write never
    leaves a half-written `status.json` behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.to_dict(), indent=2)
    tmp_path = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
