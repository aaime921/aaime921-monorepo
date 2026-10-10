# TrainIQ

Personal macOS training-data aggregation and evidence-based coaching system.

**Start here:** [`CONSTITUTION.md`](CONSTITUTION.md) — the eight founding principles, and why they exist.
Then [`docs/adr/INDEX.md`](docs/adr/INDEX.md) — the complete architecture decision record (38 ADRs, design-phase and implementation-phase).
Then [`BACKLOG.md`](BACKLOG.md) — what's open, what's closed, and what's intentionally deferred, with reasons.

Committed as of the Phase 0B acceptance review — previously these existed only in design-phase conversation history. The Phase 0/0B milestone reports and the Tier A/B Implementation Roadmaps remain outside the repository as of this writing (see `docs/adr/INDEX.md`'s closing note); the Constitution and ADR archive above are the durable, in-repository summary of the reasoning behind them.

## Setup

    python3 -m venv .venv && source .venv/bin/activate
    pip install -e ".[dev]"

## Debug: capture a real Eufy device-data response

    python3 scripts/debug_eufy.py

Requires Eufy already connected (run the app once first). Writes the
complete, unmodified JSON response to `debug/eufy_download_response.json`
— read-only, does not sync or persist anything. See the script's own
docstring for exactly what it does and does not do.

## Run tests

    pytest

## Headless / cloud run (issue #70)

For a GitHub Actions runner (no Mac, no Keychain, no prompts):

    TRAINIQ_CREDENTIAL_BACKEND=env \
    TRAINIQ_CREDENTIALS_OUT=/path/to/rotated-credentials.json \
        trainiq --headless

- **Credentials**: with `TRAINIQ_CREDENTIAL_BACKEND=env`, `CredentialStore` reads
  `TRAINIQ_<PROVIDER>_<CREDENTIAL_TYPE>` (e.g. `TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN`)
  instead of Keychain — matches the secret names `scripts/push_secrets_to_github.py`
  creates. Unset, behavior is Keychain exactly as on macOS.
- **Rotation**: any credential rotated during the run (Peloton OAuth refresh, Eufy
  re-login) is atomically rewritten to `--credentials-out PATH` /
  `TRAINIQ_CREDENTIALS_OUT` (mode `0600`) on every change, so it survives a crash or a
  later provider's failure. The file holds secret values by design; a workflow must
  treat it as secret and delete it after use.
- **Paths**: `--db-path`/`--config-path`/`--log-dir` or `TRAINIQ_DB_PATH`/
  `TRAINIQ_CONFIG_PATH`/`TRAINIQ_LOG_DIR` override the default — today's `~/Library/...`
  locations on macOS, XDG base directories (`$XDG_DATA_HOME`, `$XDG_CONFIG_HOME`,
  `$XDG_STATE_HOME`) everywhere else. `TRAINIQ_CONFIG_JSON` seeds `config.json` from a
  JSON string if that file doesn't already exist (headless only).
- **`--headless`** (or `TRAINIQ_HEADLESS=1`): never prompts; writes a machine-readable
  `status.json` to `--status-json PATH` / `TRAINIQ_STATUS_JSON` (default
  `<log-dir>/status.json`) on every run, success or failure. Per provider:
  `status` (`ok`/`failed`/`auth_expired`), `records_synced`, `last_activity_time`
  (`null` if none — never fabricated), and a `warning` for anything non-`ok`. Never
  contains a secret value.
- **Exit codes**: `0` all providers ok · `3` partial (≥1 ok and ≥1 failed/auth_expired)
  · `1` total failure (nothing configured, every provider failed, or an unexpected
  crash) · `2` usage error (bad arguments; `--headless` with `--configure`).

## Establish or re-check the performance baseline

    python3 scripts/benchmark.py --out docs/benchmarks/$(date +%Y-%m-%d).md

See `scripts/benchmark.py`'s module docstring for what these numbers do and do not represent — in short, compare against a PREVIOUS run on the SAME machine, never against a different machine's numbers.

## Coach export (issue #71)

    trainiq export --out <dir>

Writes `profile.md`, `recent.md`, `last_done.md`, `load.md`, `weight.md`,
`performance.md`, `peloton_classes.md` (plus JSON companions for
`recent`/`load`/`weight`/`peloton_classes`) into `<dir>`, creating it if
absent. Deterministic for a given database: `--as-of YYYY-MM-DD` overrides
the default anchor date (the latest activity or weigh-in date), never
wall-clock "now". `--db-path`/`--config-path` default the same way the
re-normalize scripts do.

The weight-loss goal (`weight.md`/`profile.md`) is not in the database —
set it once in `config.json` (same file as Eufy's `device_id`):

    {"athlete": {"start_weight_kg": 82.1, "goal_weight_kg": 72.0}}

Without it, those files print "goal: not configured" rather than
inventing a number.

### Peloton bike class candidates (issue #72)

`peloton_classes.md`/`.json` list real Peloton bike classes (20/30/45/60
min, the class types the BO's own history actually uses) for the coach to
recommend from, each marked `done before (date)` or `new`. Uses the
existing authenticated Peloton connector — no separate login. `--no-classes`
skips the live lookups; missing credentials, an auth failure, or any API
error during the run all degrade the same way, to "class catalog
unavailable" (the rest of the export is unaffected either way).

## Regenerate the synthetic dataset

    python3 -m trainiq.synthetic_dataset

## Build the macOS app (macOS only — see scripts/build.sh)

    TRAINIQ_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
        scripts/build.sh
