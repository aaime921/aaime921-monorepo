# #73 Design: cloud sync workflow for trainiq-data

Requirements: `docs/trainiq/requirements/73-cloud-sync-trainiq-data.md` (AC numbers refer to it).
Builds on `docs/trainiq/architecture/70-headless-env-credentials-status-json.md` (headless, exit codes 0/3/1/2,
`status.json`, `--credentials-out`) and `71-coach-export.md` (`trainiq export --out DIR [--db-path P]`).
**Dependency:** confirm PR #77 (#71) is merged before implementing; else `blocked:dependency`.
Strava streams enrichment already runs inside `trainiq --headless` (`app.py` `_run_strava_streams_enrichment`), so no separate step.

## Decisions
- **DB storage (AC5): a GitHub release asset** `trainiq.db` on tag `data` in `trainiq-data`, overwritten with
  `gh release upload --clobber`. No git history involved. DB is `sqlite3 .backup`-ed first (consistent copy). `coach/` is
  committed on `main` (small text; one commit per run, only if changed). First run (no release) starts from an empty DB;
  if the release exists but download fails, abort without uploading (never overwrite good data with an empty DB).
- **Credentials (AC6): encrypted file, not secrets-write token. Needs BO approval** (flagged in install docs and handoff).
  `--credentials-out` JSON is encrypted (`openssl enc -aes-256-cbc -pbkdf2`, key = secret `TRAINIQ_STATE_PASSPHRASE`)
  and uploaded as release asset `creds.enc`. Avoids a PAT with `secrets:write`. Each run: decrypt prior `creds.enc`,
  merge over secrets via `merge_credentials.py`, export as env. Rule: a secret the BO changed after the stored value
  was seeded wins (store `seed_sha256` of the secret beside each value). Upload step is `if: always()` so a rotated
  Peloton refresh token survives partial/failed runs. Decrypted values get `::add-mask::`; the plaintext file is deleted.
- **Notification (AC8):** scheduled/dispatch runs with exit 3 or 1 open (or comment on) one deduplicated issue
  `trainiq sync needs attention`, label `alert`; the BO gets a normal GitHub notification. It is closed by the next clean
  run. Exit 1/2 also fail the job. Exit 3 does not (data was committed).
- **`refresh` issues (AC3, 9):** `on: issues: types: [opened]`; job `if` accepts only title `refresh` or label `refresh`, and
  `author_association` OWNER/MEMBER/COLLABORATOR. Concurrency `group: trainiq-sync`, `cancel-in-progress: false`.
  GitHub keeps only one *pending* run per group, so a queued refresh run can be superseded. Fix: the final step handles
  **all open `refresh` issues** (comment summary, close; on exit 1 comment error and leave open), not just the triggering one.
- **Strava expiry date (AC7):** `coach/state.json` (committed, no secrets) holds `strava_last_ok` (UTC date), updated when
  `providers.strava_unofficial.status == "ok"`. Missing means "unknown", never guessed.
- **Free tier (AC11):** ~4-6 min/run (pip cache, DB download, sync, export). 30 scheduled + ~20 on-demand runs is ~250 of
  2,000 free private-repo minutes/month. Concurrency prevents overlap. Schedule offset off :00 (e.g. `17 5 * * *`).

## Files (all new, under `projects/trainiq/deploy/trainiq-data/`)
- `sync.yml`, `README.md` (install steps).
- `scripts/render_status.py`: `render(status: dict|None, state: dict, now: datetime, exit_code: int) -> str` plus CLI
  (`--status-json --state-json --exit-code --out`); also updates `state.json`.
- `scripts/refresh_filter.py`: `should_run(event_name, payload: dict) -> bool` (used by a tiny CLI for local tests; the
  workflow `if:` duplicates the same expression, and a test asserts both agree on fixture payloads).
- `scripts/merge_credentials.py`: `merge(env: Mapping, stored: dict|None) -> dict`, writes `$GITHUB_ENV` lines via masked values.
- Tests: `projects/trainiq/tests/test_deploy_trainiq_data.py` (fixtures only).

## Workflow outline (`sync.yml`)
Permissions `contents: write`, `issues: write`. Steps: checkout `trainiq-data`; checkout the private monorepo to `.mono`
with secret `MONOREPO_READ_TOKEN` (fine-grained, read-only, Contents on that repo only); setup-python + pip cache;
`pip install .mono/projects/trainiq`; download `trainiq.db` and `creds.enc`; merge credentials; run
`trainiq --headless --db-path ... --credentials-out ... --status-json ...` capturing exit code (`set +e`);
`trainiq export --out coach` if the DB exists; `render_status.py`; `if: always()` upload creds then DB (DB only if the run
produced one); commit `coach/` to `main`; alert / refresh-issue reporting; delete temp files.
Env: `TRAINIQ_CREDENTIAL_BACKEND=env`, all `TRAINIQ_*` credential secrets (list names from `credentials/env_backend.py`
and the #70 README), **`TRAINIQ_STRAVA_UNOFFICIAL_SESSION_EXPIRES_AT`** (missing counts as expired), `TRAINIQ_CONFIG_JSON`.
Secret names must be listed in `README.md` (AC2). Renewal steps for the Strava message live in the README and are linked in
`status.md`.

## Tasks
1. Confirm #71 merged. 2. Write the three scripts. 3. Write `sync.yml`. 4. Write `README.md`: secrets, token scopes,
   enabling Actions, first-run bootstrap, Strava renewal, approval note on `creds.enc`. 5. Tests. 6. Check no data or secrets committed.

## Tests (AC1, 12)
`yaml.safe_load(sync.yml)` has the three triggers, concurrency and permissions; `render_status` for ok, partial with the exact
Strava warning (known date and unknown date), failure/no status.json; no secret values in output; `should_run` for
title `refresh`, label `refresh`, other issue, non-collaborator; `merge` precedence and changed-secret rule.
BO live verification: the real runner, token scopes, notifications.

## Risks
- Release-asset DB is overwritten without versions; keep the previous asset as `trainiq.db.prev` for one-step rollback.
- Encrypted creds tie recovery to `TRAINIQ_STATE_PASSPHRASE`; losing it means re-seeding from secrets (works by design).
- Strava cookie scraping is ToS-gray and fragile (ADR for #18); expiry is expected and surfaced, not hidden.
