# #73 Cloud sync on trainiq-data: QA verification

PR #85 (head 1d516ec). Verdict: **FAIL** (AC7). Fixtures only; live Actions run is the BO's.

Tests: `pytest tests/test_deploy_trainiq_data.py` 33 passed; full `pytest tests/` 842 passed, 2 failed
(`test_peloton_csv_import.py`, local-only CSV path; pre-existing, unrelated).
Fixture check: `render_status` tests use the real `status.json` shape from `trainiq.headless` (`providers.*.status/records_synced/last_activity_time/warning`, `warnings`). CLI flags in `sync.yml` all exist in `trainiq/app.py`.

| # | Criterion | Result | Reason |
|---|---|---|---|
| 1 | sync.yml valid, no secrets in repo | PASS | Parsed in test; only `${{ secrets.* }}` refs |
| 2 | Install docs, read-only token, all secrets named | PASS | README lists `MONOREPO_READ_TOKEN` (Contents read-only) and every secret |
| 3 | Triggers + refresh filter + concurrency | PASS | schedule/dispatch/issues:opened, `if:` agrees with `should_run`, group `trainiq-sync` |
| 4 | Restore, headless (+streams), export, persist | PASS | Streams run inside `--headless` (app.py); export + uploads present |
| 5 | DB not in git history | PASS | Release asset `data`, `--clobber`; `coach/` on main |
| 6 | Rotated creds persisted, even on partial/fail | PASS | Seal/encrypt/upload steps `if: always()`; mechanism flagged for BO approval |
| 7 | status.md written on every run incl. failures | **FAIL** | If any step before `trainiq --headless` fails (e.g. the Restore abort, install, monorepo checkout), `steps.sync.outputs.exit_code` is empty, so `render_status.py --exit-code ""` exits 2 (`invalid int value: ''`) and no status.md is written; the old file stays committed |
| 8 | Partial run commits, BO notified | PASS | exit 3 commits DB + coach/, alert issue opened/updated |
| 9 | refresh issue: comment+close / comment+leave open | PASS | Report step |
| 10 | No secret in logs/status/commits | PASS | Values masked in merge script; status only has names/counts |
| 11 | Free-tier estimate | PASS | Architecture doc: ~250 of 2,000 min/month |
| 12 | Script tests (fixtures) | PASS | status render ok/partial/failure, refresh filter |

## Repro for AC7
```
python projects/trainiq/deploy/trainiq-data/scripts/render_status.py \
  --status-json state/status.json --state-json coach/state.json --exit-code "" --out coach/status.md
# -> error: argument --exit-code: invalid int value: ''   (rc 2, no coach/status.md)
```
Fix direction: default the exit code in the workflow (e.g. `${{ steps.sync.outputs.exit_code || '1' }}`) or make the
script accept an empty value as failure, and add a test; render the "crashed before status.json" text.

Live-account verification (runner, token scopes, notifications) not done here.
