# TrainIQ cloud sync — install steps (`aaime921/trainiq-data`)

Issue #73. The pipeline only ever edits this folder in the monorepo — the
BO installs/updates things in `aaime921/trainiq-data` (private) by hand,
following the steps below.

## 1. One-time repo setup

1. `aaime921/trainiq-data` exists, is private, and has Actions enabled
   (Settings → Actions → General → "Allow all actions").
2. Copy `sync.yml` from this folder into `trainiq-data`'s
   `.github/workflows/sync.yml`. Everything else this workflow calls
   (`scripts/`, `trainiq` itself) is read from a fresh monorepo checkout
   made at the start of every run, so only `sync.yml` itself needs
   re-copying — and only when its own content changes.
3. Create the secrets below (Settings → Secrets and variables → Actions →
   New repository secret).

## 2. Secrets to create

### Checkout

- **`MONOREPO_READ_TOKEN`** — a GitHub **fine-grained personal access
  token**, scoped to the single repository `aaime921/aaime921-monorepo`,
  with **Contents: Read-only** and no other permissions. Used only to
  check out the monorepo (never to push to it). Everything else the
  workflow writes back (the DB, credentials, `coach/`, issue comments) uses
  the repo's own built-in `GITHUB_TOKEN` (`contents: write`,
  `issues: write` — already granted in `sync.yml`), so no write-scoped PAT
  is needed anywhere.

### Credential state (⚠️ needs BO approval — see "Why an encrypted file")

- **`TRAINIQ_STATE_PASSPHRASE`** — any long random passphrase (e.g.
  `openssl rand -base64 32`). Encrypts/decrypts `creds.enc`, the release
  asset holding credentials TrainIQ rotated during a run (a new Peloton
  session, a refreshed Strava OAuth token, ...). Keep a copy somewhere
  safe outside GitHub: losing it means the next run falls back to
  whatever's in the secrets below, nothing more.

### TrainIQ config

- **`TRAINIQ_CONFIG_JSON`** — the full contents of your local
  `config.json` (athlete profile, Eufy `device_id`, weight goal, ...), as
  one JSON blob. Seeds `trainiq`'s config file on a run where it doesn't
  already exist (never overwrites one that does). **Re-run
  `scripts/push_secrets_to_github.py`** after this issue's work lands, so
  this secret picks up the `athlete` weight-goal field the coach export
  now reads.
- **`TRAINIQ_STRAVA_UNOFFICIAL_SESSION_EXPIRES_AT`** — already required by
  issue #70; a missing value is treated as an expired session, not a
  configured one.

### Provider credentials

One secret per `(provider, credential type)` pair, named
`TRAINIQ_<PROVIDER>_<CREDENTIAL_TYPE>` exactly as
`scripts/push_secrets_to_github.py` already names them. The full,
exact list (also enforced in code — `CREDENTIAL_SECRET_NAMES` in
`scripts/merge_credentials.py`):

| Provider | Secrets |
|---|---|
| Strava (OAuth) | `TRAINIQ_STRAVA_REFRESH_TOKEN`, `TRAINIQ_STRAVA_ACCESS_TOKEN`, `TRAINIQ_STRAVA_EXPIRES_AT` |
| Strava (unofficial, session cookie) | `TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE`, `TRAINIQ_STRAVA_UNOFFICIAL_SESSION_OBTAINED_AT`, `TRAINIQ_STRAVA_UNOFFICIAL_SESSION_EXPIRES_AT` |
| Peloton | `TRAINIQ_PELOTON_EMAIL`, `TRAINIQ_PELOTON_PASSWORD`, `TRAINIQ_PELOTON_SESSION_ID`, `TRAINIQ_PELOTON_SESSION_EXPIRES_AT`, `TRAINIQ_PELOTON_MANUAL_BEARER_TOKEN`, `TRAINIQ_PELOTON_OAUTH_ACCESS_TOKEN`, `TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN`, `TRAINIQ_PELOTON_OAUTH_EXPIRES_AT` |
| Eufy | `TRAINIQ_EUFY_EMAIL`, `TRAINIQ_EUFY_PASSWORD`, `TRAINIQ_EUFY_ACCESS_TOKEN`, `TRAINIQ_EUFY_REFRESH_TOKEN`, `TRAINIQ_EUFY_EXPIRES_AT` |

Only create the ones for providers you've actually connected — an
unconfigured provider is skipped, same as a local run (AC1 in #70).

## 3. First run

Trigger **Actions → TrainIQ sync → Run workflow** once by hand
(`workflow_dispatch`). There's no release yet, so the run starts from an
empty DB — check `coach/status.md` afterward for which providers came up
`ok`. After that, the schedule (`17 5 * * *` UTC, once per day) and any
`refresh` issue take over.

## 4. How it works, briefly

- **DB & credentials**: stored as release assets (`trainiq.db`,
  `creds.enc`) on a single release tagged `data` in `trainiq-data`,
  overwritten (`--clobber`) every run — no git history growth. `coach/` is
  committed straight to `main` (small text, one commit per run, only when
  it changed).
- **On-demand refresh**: open an issue titled (or labelled) `refresh` in
  `trainiq-data`; the workflow comments a result summary and closes it on
  success/partial, or comments the error and leaves it open on failure.
- **BO notification**: a failed or partial scheduled/dispatch run opens or
  updates one issue titled `trainiq sync needs attention` (label `alert`),
  which the next clean run closes — a normal GitHub notification, no extra
  setup needed.
- **`coach/status.md`**: always shows the last run time (UTC) and the
  result per provider, plus any warning.

### Renewing the Strava session cookie

`strava_unofficial`'s session cookie expires periodically (it's a
browser-session cookie, not an OAuth token — see ADR for #18); this is
expected, not a bug. When `coach/status.md` shows:

> ⚠️ Strava cookie expired: Strava data not refreshed since \<date\>.

1. Log into Strava in a browser.
2. Re-run the local capture flow documented in
   `projects/trainiq/trainiq/connectors/strava_unofficial.py`'s module
   docstring to obtain a fresh session cookie and its expiry.
3. Update the `TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE` and
   `TRAINIQ_STRAVA_UNOFFICIAL_SESSION_EXPIRES_AT` secrets in
   `trainiq-data` (or re-run `scripts/push_secrets_to_github.py`).
4. The next run (scheduled, or `workflow_dispatch`) picks it up — the
   changed-secret always wins over whatever TrainIQ last rotated (see
   `scripts/merge_credentials.py`'s module docstring).

## 5. Why an encrypted file, not a secrets-write token

Persisting a rotated credential (e.g. a new Peloton refresh token) back to
GitHub *secrets* would need a PAT with `secrets:write` on `trainiq-data` —
a token that can rewrite any secret in the repo, including
`MONOREPO_READ_TOKEN` and `TRAINIQ_STATE_PASSPHRASE` themselves. Instead,
rotated credentials are encrypted (`openssl enc -aes-256-cbc -pbkdf2`,
keyed by `TRAINIQ_STATE_PASSPHRASE`) and uploaded as the `creds.enc`
release asset using the run's own `GITHUB_TOKEN` (`contents: write` only).
**This mechanism needs BO approval** (per the architecture doc) — until
approved, a rotated credential still gets picked up next run via the
decrypted `creds.enc` written during this same run, but the whole approach
(rather than e.g. a secrets-write PAT) is the thing to sign off on.
