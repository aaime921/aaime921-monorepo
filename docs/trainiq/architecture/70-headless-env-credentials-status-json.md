# #70 Design: headless run, env credentials, status.json

Requirements: `docs/trainiq/requirements/70-headless-env-credentials-status-json.md` (AC numbers below refer to it).
Code root: `projects/trainiq/`.

## Approach
Keep `CredentialStore` as the only credential API (connectors untouched) and swap its backend. Add a thin
headless layer in `app.py`. Engine/lifecycle/checkpoint logic stays as is (ADR-008, ADR-038): auth failure
still goes Degraded/RecoveryRequired through the engine; headless only *reports* it. Env-backed credentials
are held in memory for the run, so Peloton refresh rotation works inside the run and is persisted to the
credentials-out file (the workflow, a separate ticket, pushes it to secrets).

## Affected files
- `trainiq/credentials/store.py` (edit): backend selection.
- `trainiq/credentials/env_backend.py` (new): `EnvBackend`.
- `trainiq/paths.py` (new): path resolution.
- `trainiq/headless.py` (new): `StatusReport`, exit codes, status.json writer.
- `trainiq/app.py` (edit): flags, use paths, headless branch; `_parse_args`, `main`.
- `trainiq/logging_setup.py` (edit): `DEFAULT_LOG_DIR` stays for Mac, resolved via `paths`.
- `trainiq/sync/engine.py` (edit, additive): `ConnectorSyncResult.auth_failed: bool = False`, set True in the
  `AuthenticationError` branch (~l.829) and the `authenticate() False` path. Append at the end of the dataclass
  (RC1-HF-006 precedent). No other engine change.
- Tests: `tests/test_env_credentials.py`, `tests/test_headless.py` (new).

## Interfaces
**Credential backend** (`store.py`): `CredentialStore.__init__(username, conn=None, backend=None)`.
`backend` defaults to `_default_backend()`: `EnvBackend` iff `os.environ.get("TRAINIQ_CREDENTIAL_BACKEND") == "env"`,
else `KeyringBackend` (wraps current `keyring` calls verbatim, so AC2/AC10 and the existing mocked-keyring
tests hold; keep `import keyring` in `store.py` so existing monkeypatches still work).
Protocol: `get(provider, type) -> str|None`, `set(provider, type, value)`, `delete(provider, type)`.

`EnvBackend`:
- name: `f"TRAINIQ_{provider}_{type}".upper()` (e.g. `TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE`, matches secrets).
- `get`: in-memory overlay first (values set this run), then `os.environ`; empty string = missing.
- `set`: writes overlay only (never mutates `os.environ`), and records `(provider,type)->value` in `rotated`.
- `delete`: removes from overlay and marks as deleted for the run.
- `CredentialStore.set/rotate` unchanged: they call `backend.set` then `_mark_connected` (SQLite metadata fine).

**Credentials-out** (`--credentials-out PATH` / env `TRAINIQ_CREDENTIALS_OUT`): `EnvBackend` takes an optional
`out_path`. On every `set` it rewrites the whole file atomically (write temp in same dir with
`os.open(..., 0o600)`, `fsync`, `os.replace`) with JSON
`{"version":1,"credentials":{"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN":"..."}}` — keys are the secret names.
Writing on each `set` (not at exit) satisfies AC3 "never lost, even on partial failure/crash". Strava/Peloton
rotate access+refresh+expires together; the file holds all of them. File contains secrets by design (0600);
the workflow must treat it as secret and delete it after upload. Only rotated values are written, not unchanged ones.
If `out_path` is unset under env backend, rotation stays in memory and a warning goes in status.json.

**Paths** (`paths.py`): `resolve_paths(args, env) -> Paths(db, config, log_dir)`. Precedence: flag > env
(`TRAINIQ_DB_PATH`, `TRAINIQ_CONFIG_PATH`, `TRAINIQ_LOG_DIR`) > default. Default: macOS (`sys.platform=="darwin"`)
= today's `~/Library/...` values exactly; else XDG: `$XDG_DATA_HOME|~/.local/share/trainiq/trainiq.db`,
`$XDG_CONFIG_HOME|~/.config/trainiq/config.json`, `$XDG_STATE_HOME|~/.local/state/trainiq/logs`.
Flags: `--db-path`, `--config-path`, `--log-dir`. Module-level `APP_SUPPORT_DIR/LOG_DIR/CONFIG_PATH` constants
in `app.py` remain (tests may import them); `main` resolves paths at call time.

**TRAINIQ_CONFIG_JSON** (decision): if set and the config file does not exist, `main` writes its content to the
config path before use (validate JSON; invalid → recorded failure). It is non-secret config (Eufy device_id,
timezone). Not a different loading mechanism; `config.py` untouched.

**Headless** (`--headless`, also env `TRAINIQ_HEADLESS=1`; plus `--status-json PATH`, env `TRAINIQ_STATUS_JSON`,
default `<log_dir>/status.json`):
- Skip `run_first_time_setup`/`run_configure`; `--headless` with `--configure` is an error (exit 2). No connectors
  configured → recorded failure, status.json written, exit 1. Skip the "running from Trash" check? Keep it (no prompt).
- Guard: patch nothing; any prompt path is unreachable because wizard is skipped. Additionally set
  `builtins.input`-free; as belt-and-braces `sys.stdin` check is unnecessary.
- Whole `main` body in headless wraps in `try/except Exception` → status `failed` with message, still writes status.json.

**Exit codes** (constants in `headless.py`, documented in the module docstring and README): `0` all ok,
`3` partial (≥1 ok and ≥1 failed/auth_expired), `1` total failure/crash/nothing configured, `2` usage error.
Rationale: 3 avoids argparse's 2 and the generic 1 so a workflow can `continue-on-error` on 3 only.

**status.json** schema (version 1):
```json
{"version":1,"started_at":"ISO","finished_at":"ISO","exit_code":3,"outcome":"partial",
 "providers":{"strava_unofficial":{"status":"auth_expired","records_synced":0,
   "last_activity_time":null,"warning":"Strava session cookie expired; refresh TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE"}},
 "warnings":[]}
```
`status`: `ok|failed|auth_expired`. Mapping from `ConnectorSyncResult`: state Healthy/Warning with no error → `ok`;
`auth_failed` → `auth_expired`; any other error/Degraded/skip → `failed`. Applies to all providers (Peloton/Eufy
auth failures also report `auth_expired`; resolves the BA question; Peloton/Eufy hints may mention re-login).
`records_synced` = `records_upserted`. `last_activity_time` = `SELECT MAX(start_time) FROM normalized_activities
WHERE source/provider = ?` (check column name in `storage/schema.py`), null if none; weigh-ins (Eufy) use the
weigh-in table's max timestamp likewise. Never fabricate. Warnings contain no secret values (AC4): only
provider names and secret *names*, never values, and `error` strings are passed through `str(exc)` only after
the connectors' existing no-secret guarantee; the Developer must add a test asserting known secret values are
absent from status.json and logs.
Strava streams enrichment (`_run_strava_streams_enrichment`) runs as today; its `AuthenticationError` is already
caught/logged. If it hits auth after an ok sync, add a warning, not a status change.

## Tasks
1. `EnvBackend` + `KeyringBackend` + selection in `CredentialStore`; credentials-out atomic writer.
2. `paths.py`; wire `main` and `logging_setup` default.
3. Engine: additive `auth_failed`.
4. `headless.py`: status mapping, writer (atomic, always runs, `finally`), exit code.
5. `app.py`: flags, headless branch, `TRAINIQ_CONFIG_JSON`, README section for env vars/exit codes.
6. Tests; run full existing suite unchanged.

## Test strategy (fixtures/mocks only)
- Env backend: get/exists/missing/empty string; name upper-casing for `strava_unofficial`; set stays in memory
  and does not touch `os.environ`; keyring untouched when backend=env, and used when unset.
- Rotation: simulate Peloton refresh via `CredentialStore.rotate` → file content keys/values, mode `0o600`
  (`stat`), file exists after a later provider raises; second rotation rewrites, not appends.
- Headless: fake connectors (existing test doubles) — all ok → 0; strava_unofficial raises AuthenticationError +
  another ok → 3, status `auth_expired` with warning, no traceback; all fail → 1; crash mid-run still writes status.json.
- Paths: precedence and Linux default with `sys.platform` monkeypatched; Mac default unchanged.
- No-prompt: monkeypatch `builtins.input`/`getpass` to raise; headless run must not call them.
- Live-verification (BO, not Dev/QA): real runner behaviour; spike already showed 200s from datacenter IPs.

## Risks / tradeoffs
- Rotated refresh tokens only persist if the workflow uploads the credentials-out file; if it fails after
  Peloton rotated, the old secret is dead. Mitigation is workflow ticket's concern (upload with `if: always()`).
- Strava cookie scraping is ToS-gray and fragile (existing decision); expiry is expected, hence `auth_expired` partial.
- Auth failure in a fresh ephemeral DB: lifecycle state (Degraded 10-day → RecoveryRequired, ADR-038) restarts
  each run unless the DB is persisted by the workflow, so escalation timing is not meaningful headless; status.json
  reports per-run outcome only. DB/checkpoint persistence between runs is the workflow ticket's concern.
- `exit 3` for partial is our convention; documented, easy to change in one constant.
