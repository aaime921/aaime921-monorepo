"""
render_status.py — issue #73

Renders `coach/status.md` for the TrainIQ cloud sync workflow (`sync.yml`,
run on `aaime921/trainiq-data`) from `trainiq --headless`'s `status.json`,
and keeps `coach/state.json`'s `strava_last_ok` date up to date so a
Strava-expiry warning can always say *since when* without ever guessing.

No secret ever flows through this script: `status.json` only carries
provider names, record counts, timestamps and `str(exception)` messages
(trainiq.headless's own guarantee — see projects/trainiq/trainiq/headless.py),
never a credential value (AC10).

Exit codes are trainiq.headless's (0 ok / 1 total failure / 2 usage error /
3 partial), duplicated here rather than imported: this script runs in
`trainiq-data`'s workflow from a monorepo checkout at `.mono`, and must
keep working even for an alert/report-only re-run where `trainiq` itself
was never installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

EXIT_OK = 0
EXIT_TOTAL_FAILURE = 1
EXIT_USAGE_ERROR = 2
EXIT_PARTIAL = 3

_EXIT_LABELS = {
    EXIT_OK: "ok",
    EXIT_TOTAL_FAILURE: "failed",
    EXIT_USAGE_ERROR: "usage error",
    EXIT_PARTIAL: "partial",
}

STRAVA_UNOFFICIAL_PROVIDER = "strava_unofficial"
STATUS_OK = "ok"
STATUS_AUTH_EXPIRED = "auth_expired"

# Renewal steps live once, in the install README, not duplicated here —
# status.md just links to them (architecture doc, "Strava expiry date").
RENEW_URL = (
    "https://github.com/aaime921/aaime921-monorepo/blob/main/"
    "projects/trainiq/deploy/trainiq-data/README.md#renewing-the-strava-session-cookie"
)


def render(status: Optional[dict], state: dict, now: datetime, exit_code: int) -> str:
    """Builds `coach/status.md`'s content.

    `state` (`coach/state.json`'s parsed contents) is mutated in place:
    when this run's `status.json` reports `strava_unofficial` as `ok`,
    `state["strava_last_ok"]` is set to today's UTC date. The caller is
    responsible for persisting `state` back to `coach/state.json` — this
    function only renders and updates the in-memory value, matching
    `coach/state.json` holding exactly one non-secret fact (AC7).
    """
    lines = ["# TrainIQ sync status", ""]
    lines.append(f"Last run: {now.strftime('%Y-%m-%dT%H:%M:%SZ')} (UTC)")
    lines.append(f"Result: {_EXIT_LABELS.get(exit_code, str(exit_code))}")

    if status is None:
        lines.append("")
        lines.append(
            "No status report was produced — the run crashed before "
            "`trainiq --headless` could write `status.json`."
        )
        return "\n".join(lines) + "\n"

    providers: dict = status.get("providers") or {}
    lines.append("")
    lines.append("## Providers")
    lines.append("")
    if not providers:
        lines.append("- (none configured)")
    for name in sorted(providers):
        info = providers[name]
        lines.append(f"- **{name}**: {info.get('status', 'unknown')} ({info.get('records_synced', 0)} records)")
        if info.get("warning"):
            lines.append(f"  - {info['warning']}")

    strava = providers.get(STRAVA_UNOFFICIAL_PROVIDER)
    if strava is not None and strava.get("status") == STATUS_OK:
        state["strava_last_ok"] = now.strftime("%Y-%m-%d")

    warnings = list(status.get("warnings") or [])
    if warnings:
        lines.append("")
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")

    if strava is not None and strava.get("status") == STATUS_AUTH_EXPIRED:
        last_ok = state.get("strava_last_ok") or "unknown"
        lines.append("")
        lines.append(
            f"⚠️ Strava cookie expired: Strava data not refreshed since {last_ok}. Renew: {RENEW_URL}"
        )

    return "\n".join(lines) + "\n"


def load_json(path: Optional[Path]) -> Optional[dict]:
    if path is None or not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def parse_exit_code(value: str) -> int:
    """Coerces `steps.sync.outputs.exit_code` to an int.

    It's empty when the job failed before the "Run headless sync" step ever
    ran (checkout, install, or the "Restore prior DB and credentials" abort) —
    `trainiq --headless` never started, so there is no exit code to report.
    That's still a failure, not something to crash `render_status.py` over:
    this step runs with `if: always()` precisely so `status.md` keeps
    reflecting the latest run even then (AC7).
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return EXIT_TOTAL_FAILURE


def _parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-json", type=Path, default=None, help="trainiq --headless's status.json, if produced.")
    parser.add_argument("--state-json", type=Path, default=None, help="coach/state.json from the prior run, if any.")
    parser.add_argument("--exit-code", type=str, required=True, help="steps.sync.outputs.exit_code; may be empty.")
    parser.add_argument("--out", type=Path, required=True, help="Where to write coach/status.md.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    status = load_json(args.status_json)
    state = load_json(args.state_json) or {}
    now = datetime.now(timezone.utc)
    exit_code = parse_exit_code(args.exit_code)

    content = render(status, state, now, exit_code)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content)

    if args.state_json is not None:
        args.state_json.parent.mkdir(parents=True, exist_ok=True)
        args.state_json.write_text(json.dumps(state, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
