# #70 Headless TrainIQ: env-var credentials, non-interactive run, status.json

## Summary
The BO wants TrainIQ to run unattended on a GitHub Actions runner (repo `aaime921/trainiq-data`) with no Mac,
Keychain or prompts, as part of the Phase 2 fit-coach plan. A spike (2026-10-10) showed Strava cookie,
Peloton OAuth and Eufy tokens all return 200 from a hosted runner. Credentials already exist as Actions
secrets named `TRAINIQ_<PROVIDER>_<CREDENTIAL_TYPE>` (e.g. `TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN`,
`TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE`) plus `TRAINIQ_CONFIG_JSON`, created by
`scripts/push_secrets_to_github.py`. Today `CredentialStore` is Keychain-only.

## Scope
In:
- Env-var credential backend, selected by `TRAINIQ_CREDENTIAL_BACKEND=env`; Keychain stays default.
- Write-back of credentials rotated during a run to a persistable file (`--credentials-out <path>`).
- Configurable DB, config and log directories (flags or env); no `~/Library/...` assumption on Linux.
- `--headless` mode: no prompts, exit codes, machine-readable `status.json`.
- Strava cookie-expiry handled as a partial failure, not a crash.

Out: the GitHub Actions sync workflow (separate ticket), pushing secrets back to GitHub, scripting login forms,
real-account testing (ops, BO), schema migrations.

Touches credential rotation and the auth-recovery path (Peloton refresh token, Eufy re-login, Strava
RecoveryRequired): Architect, please review against ADR-038 lifecycle. Cursor/checkpoint handling must not change.

## Acceptance criteria
1. With `TRAINIQ_CREDENTIAL_BACKEND=env`, `CredentialStore` get/exists read `TRAINIQ_<PROVIDER>_<TYPE>` (upper-cased)
   and return the same values the secret names carry; a missing var behaves like a missing Keychain item.
2. Without that variable, behaviour is Keychain exactly as today; the existing test suite passes unchanged.
3. Any credential rotated/set during a run (Peloton OAuth refresh, Eufy re-login) is written to the file given by
   `--credentials-out`, keyed so the workflow can map it back to the secret names. A rotated Peloton refresh token
   is written before the run can exit, including on partial failure; it is never lost.
4. The credentials-out file is created with owner-only permissions and no secret values appear in logs or `status.json`.
5. DB, config and log locations are settable by flag or env var; on Linux with none set and no `~/Library`, the run
   works using a sensible non-macOS default.
6. `trainiq --headless` never prompts or opens UI; any condition that would prompt becomes a recorded failure.
7. Exit code: 0 = all providers ok; a distinct non-zero "partial" code = at least one provider ok and one
   failed/auth_expired; a different non-zero code = total failure/crash. Codes are documented.
8. `status.json` is written on every headless run (including failures), with per provider: `status`
   (`ok` | `failed` | `auth_expired`), records synced, last activity time (null if none, never fabricated), plus a
   human-readable warning for any non-ok provider.
9. Strava cookie expired (401/403 or equivalent) → `strava_unofficial: auth_expired` with a human-readable warning
   (cookie must be refreshed), other providers still sync, exit code is "partial", no traceback/crash.
10. Mac/interactive behaviour is unchanged.
11. Tests (fixtures/mocks only, no live calls) cover: env backend get/exists/missing, rotation write-back file
    (content and permissions), auth-expired path with other providers ok, and exit codes.

## Open questions (non-blocking; Architect to decide)
- Exact "partial" exit code value and `status.json` schema/location (default path when not given).
- Whether `TRAINIQ_CONFIG_JSON` is loaded by the same env mechanism as config dir.
- Strava session-cookie use is ToS-gray and fragile (existing decision); expiry is expected to recur, hence AC 9.
