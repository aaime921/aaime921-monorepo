# #73 Cloud sync on trainiq-data: daily + on-demand refresh, coach export, Strava-expiry warning

## Summary
The BO wants the TrainIQ sync to run in GitHub Actions on the private repo `aaime921/trainiq-data`, so the Grok coach
always has fresh data without the BO's Mac. It runs once every 24 h and on demand: Grok's GitHub connector opens a
`refresh` issue (the BO taps to submit) and that issue triggers a run. Health data stays in the private data repo, never
in the monorepo. An expired Strava cookie must be visible to the coach (in the data) and to the BO (GitHub notification).

## Dependencies (not blockers for BA/Architect)
- #70 merged: `trainiq --headless`, env credentials, `--credentials-out`, `status.json`, exit codes 0/3/1/2.
- #71 command fixed as `trainiq export --out <dir> [--db-path ...]`; its PR (#77) may still be open. Developer must check
  merge status first, else `blocked:dependency`.
- The workflow must also supply `TRAINIQ_STRAVA_UNOFFICIAL_SESSION_EXPIRES_AT` (secret exists); a missing expiry counts as expired (#70 QA note).

## Scope
In:
- Workflow file + install docs kept in the monorepo at `projects/trainiq/deploy/trainiq-data/` (`sync.yml`); the BO installs it in `trainiq-data`.
- Persistence of the DB and `coach/` between runs; persistence of rotated credentials.
- Status/warning file for the coach; issue-based reporting for `refresh` runs.

Out: editing `trainiq-data` directly (pipeline only edits the monorepo), changing #70/#71 behaviour, scripting Strava login,
live-account testing (BO ops), any health data in the monorepo.

## Acceptance criteria
1. `projects/trainiq/deploy/trainiq-data/sync.yml` exists, is valid workflow YAML (lint/parse check in a test), and no data or secret values are in the monorepo.
2. Install docs (same folder) list the steps for the BO, including creating a **read-only fine-grained token** secret to check out the private monorepo, and every other secret/variable the workflow needs, by name.
3. Triggers: `schedule` once per 24 h, `workflow_dispatch`, and `issues: opened` that runs only when the issue title or label is `refresh`; other issues start no job. A concurrency group allows one run at a time (no run lost for a `refresh` issue; it is queued or handled).
4. Each run: restores the DB, runs `trainiq --headless` plus the Strava streams step, runs `trainiq export --out coach/`, then persists the DB and `coach/`.
5. The DB never accumulates in git history: stored as a single force-updated `data` branch or a release asset (Architect picks); `coach/` is committed on `main`. Repo size does not grow by one DB copy per run.
6. Credentials rotated during the run (`--credentials-out`) are persisted so the next run uses them (to secrets or an encrypted file; mechanism needs BO approval, flagged in docs). A rotated Peloton refresh token is persisted even when the run ends partial/failed.
7. `coach/status.md` is written on every run, including failures: last run time (UTC), result per provider (from `status.json`), and any warning. Strava cookie expired gives exactly: "⚠️ Strava cookie expired: Strava data not refreshed since <date>. Renew: <steps>", where <date> is the last successful Strava sync (stated as unknown if none, never invented).
8. Partial exit (code 3, e.g. Strava expired) still commits DB and `coach/` for providers that succeeded, and the run is surfaced to the BO as a GitHub notification (e.g. failing run or opened issue, Architect decides; must notify without BO action beyond normal GitHub notifications).
9. For a `refresh` issue: on success or partial, the workflow comments a result summary (including warnings) and closes the issue; on failure, it comments the error and leaves the issue open.
10. No secret value appears in logs, comments, `status.md` or committed files.
11. Free tier: a run is expected to take under 10 minutes, so 30 scheduled runs/month plus on-demand stay well under 2,000 free minutes/month for private repos; Architect states the estimate in the design.
12. Tests (fixtures only, no live calls) cover any scripts the workflow uses: status.md rendering (ok, partial with Strava-expired warning, failure) and `refresh` trigger filtering logic.

## Open questions (non-blocking; Architect decides, BO approves where noted)
- DB storage: `data` branch vs release asset.
- Where rotated credentials are persisted (updating secrets needs a write-scoped token; encrypted file alternative) — BO approval required.
- How the BO is notified of expiry on scheduled runs (failed run vs issue).
