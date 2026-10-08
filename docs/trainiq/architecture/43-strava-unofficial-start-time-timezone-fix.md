# Architecture: Strava Unofficial Start-Time Timezone Fix

**Issue:** #43
**Requirements:** `docs/trainiq/requirements/43-strava-unofficial-start-time-timezone-fix.md`
**Related:** #30 (web endpoints), #36 (discipline re-normalization — this
issue reuses its re-normalization mechanism verbatim), #37 (dedup backfill),
#33 (a different resume-cursor bug on Peloton — unrelated root cause, same
family of "checkpoint must stay consistent with what `normalize()`
produces" concern)

## Approach

### Root cause (verified against current `main`)

`StravaUnofficialConnector._extract_start_time_iso()`
(`trainiq/connectors/strava_unofficial.py`) tries
`_START_FIELD_CANDIDATES = ("start_date_local_raw", "start_date_raw",
"start_date", "start_day")` in order and, for any `*_raw` field that is an
`int`/`float`, converts it with `datetime.fromtimestamp(value,
tz=timezone.utc)`. `start_date_local_raw` is an epoch number whose
wall-clock digits are the athlete's **local** time, not UTC — the BO's
live evidence (issue body) shows `start_date_local_raw` formatted as UTC
gives `19:57:34` while the real UTC instant (confirmed via the payload's
own `start_time` field and the matching Peloton row) is `18:57:34`, a
60-minute gap that exactly matches BST's UTC+1 offset. In GMT (winter),
local time equals UTC, so the bug is invisible — which is why it was never
caught by the existing fixtures (all of which happen to use round UTC
times with no actual local/UTC distinction encoded).

`start_time` — an ISO 8601 string carrying its own correct UTC offset
(e.g. `"2026-10-07T18:57:34+0000"`) — already exists in every payload this
connector has seen (per #30's and this issue's live evidence) but is not
in `_START_FIELD_CANDIDATES` at all, so it's never tried.

Downstream effect: `normalized_activities.start_time` is wrong by exactly
the local UTC offset for every BST-era `strava_unofficial` row. Cross-
provider dedup (#37) compares `strava_unofficial`'s `start_time` directly
against Peloton's (true UTC) `start_time` within a 5-minute window
(`dedup/detector.py`'s `TIME_WINDOW_S`), so every real BST-era duplicate
pair falls ~55 minutes outside that window and is never even scored as a
candidate.

### Fix, field selection

1. Prepend `"start_time"` to `_START_FIELD_CANDIDATES`:
   ```python
   _START_FIELD_CANDIDATES = ("start_time", "start_date_local_raw", "start_date_raw", "start_date", "start_day")
   ```
2. When the selected key is `"start_time"`: parse it with
   `datetime.fromisoformat(value)` (Python 3.11's `fromisoformat` accepts
   the `+0000`-style offset Strava sends — verified directly against this
   project's Python 3.11 runtime, not assumed) and convert to UTC via
   `.astimezone(timezone.utc)`. If the parsed value has no offset at all
   (`tzinfo is None`) — not seen in any evidence, but the one input this
   field could plausibly arrive as that this fix must not silently
   mishandle — raise `StravaUnofficialHTTPError`, the same fail-loud
   convention `_extract_start_time_iso` already uses for "no recognized
   field at all." Guessing a naive datetime is already UTC is exactly
   today's bug, just moved to a different field; this fix must not
   reintroduce it anywhere.
3. When the selected key is `"start_date_local_raw"` (now a demoted
   fallback, only reached if `start_time` is absent — not observed in any
   evidence so far, but the requirements doc treats "no change to
   `_START_FIELD_CANDIDATES`'s remaining order" as in scope for this key's
   *handling*, not its *position after `start_time`*): convert using an
   explicit local-timezone rule, never `tz=timezone.utc` (today's bug).
   See "Local timezone resolution" below for where the zone name comes
   from.
4. `start_date_raw`, `start_date`, `start_day` keep their current
   handling, unchanged, per the requirements doc's explicit "out of
   scope." (`start_date_raw` is a genuine UTC epoch per #30's evidence —
   the bug is specific to the `_local_` field, not to `*_raw` numeric
   fields in general.)

### Local timezone resolution (for the `start_date_local_raw` fallback only)

The BO confirmed the account's timezone is `Europe/London`, but explicitly
said not to hard-code it — a future BO/athlete could be in a different
zone, and `start_date_local_raw` is Strava's representation of whatever
timezone the *athlete's Strava account* is set to, which this codebase has
no way to verify independently. Resolution order, cheapest/most-specific
first:

1. **Explicit config value**, read from `config.json` via a new
   `trainiq.config.get_athlete_timezone(config_path)` — mirrors the
   existing `get_eufy_device_id()` precedent exactly (non-secret,
   per-install configuration, not a credential, not a schema migration).
2. **System timezone**, detected from `/etc/localtime`'s symlink target
   (`/usr/share/zoneinfo/<Area>/<Location>` on macOS, TrainIQ's only
   supported platform — see `trainiq.spec`/`APP_SUPPORT_DIR`'s
   `Library/Application Support` convention). This is a correct,
   zero-config default for the overwhelmingly common case where the
   machine running TrainIQ is in the athlete's own timezone.
3. **Neither resolves** (not a symlink, or the symlink target isn't under
   a `zoneinfo` directory — e.g. a non-macOS dev/CI environment with no
   explicit config set): `None`. This is only a problem if
   `start_date_local_raw` actually has to be used, which requires
   `start_time` to be absent from a live payload — not something any
   current evidence suggests happens. Resolution therefore happens
   **lazily**, only inside the fallback branch itself, and raises
   `StravaUnofficialHTTPError` there (not at connector construction) if
   still unresolved — consistent with Constitution Principle 1 (never
   fabricate a value that can't be honestly known) applied to this one
   non-secret setting, and with this function's existing "fail loud
   rather than silently mis-normalize" convention. It must never default
   to a fixed zone like `Europe/London` as a silent fallback — that would
   just be today's bug with extra steps for every future install that
   isn't the current BO.

`StravaUnofficialConnector.__init__` gains an optional
`local_timezone: str | None = None` parameter (an already-resolved IANA
zone name, resolved by the *caller* — `app.py`'s composition root and
`scripts/renormalize_strava_unofficial.py`, both of which already read
`config.json` for Eufy's `device_id` via the identical pattern). If the
caller passes `None` (the default — every existing call site, including
`app.py`'s current `StravaUnofficialConnector(credential_store)`, is
unaffected unless explicitly updated), the connector resolves step 2
itself at construction time (cheap — one symlink read, never a network
call, never raises). The connector never reads `config.json` directly
itself — matching the existing division of responsibility where
`config.py` I/O happens at the composition root / script entry point, not
inside a connector (see `EufyConnector(credential_store, device_id=...)`
precedent; no existing connector opens `config.json` on its own).

### Local-epoch-to-UTC conversion algorithm

`start_date_local_raw`'s wall-clock digits, read as if UTC, equal the
athlete's real local wall-clock time. To recover the true instant: strip
the (wrong) UTC label, re-label with the resolved local zone, then convert
to UTC:

```python
naive_wallclock = datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
aware_local = naive_wallclock.replace(tzinfo=ZoneInfo(local_timezone))
return aware_local.astimezone(timezone.utc).isoformat()
```

Verified directly against the BO's evidence table (not just reasoned
about): `start_date_local_raw`'s epoch, run through this algorithm with
`local_timezone="Europe/London"`, produces `2026-10-07T18:57:34+00:00` —
exactly the confirmed-correct UTC instant — for the BST case, and leaves
GMT-era values unchanged (local offset is zero in winter), matching both
rows of the BO's evidence table.

`ZoneInfo(local_timezone)` can raise `zoneinfo.ZoneInfoNotFoundError` if a
*configured* (step 1) zone name is invalid (e.g. a typo in `config.json`);
catch this and re-raise as `StravaUnofficialHTTPError` for a consistent
error type out of this module, with the bad name in the message.

### Why not normalize `start_time`'s output format further

`parsed.astimezone(timezone.utc).isoformat()` always yields a `+00:00`
suffix, regardless of what offset the input carried (`+0000`, `+01:00`,
etc.) — this is what makes every value `_extract_start_time_iso` can
produce (via `start_time` or via the `start_date_local_raw` fallback)
uniformly comparable as a plain string, satisfying
`Connector.extract_resume_cursor()`'s documented CONTRACT
(`connectors/base.py`) that a resume cursor must be "safely orderable via
a plain string `>` comparison." A passthrough of the raw string (today's
`return str(value)` for any non-`_raw` field) would NOT guarantee this —
Strava could in principle send a non-`+0000` offset on some future payload
and silently break ordering. Converting explicitly, every time, removes
that latent risk rather than leaving it for a future issue to rediscover.

## Affected components/files

- `projects/trainiq/trainiq/connectors/strava_unofficial.py` —
  `_START_FIELD_CANDIDATES`, `_extract_start_time_iso()` (signature gains
  a `local_timezone` parameter), `StravaUnofficialConnector.__init__()`
  (new `local_timezone` param + `_detect_system_timezone()` call),
  `normalize()` and `download()` (both call sites pass
  `self._local_timezone` through). New: `_detect_system_timezone()`,
  `_parse_offset_aware_start_time()`, `_convert_local_epoch_to_utc()`
  (names indicative — Developer may consolidate/rename, see Task
  breakdown).
- `projects/trainiq/trainiq/config.py` — new `get_athlete_timezone()` /
  `set_athlete_timezone()`, mirroring `get_eufy_device_id()` /
  `set_eufy_device_id()` exactly (new `"athlete": {"timezone": ...}` key,
  no schema/migration involved — this file is explicitly for non-secret
  JSON config already).
- `projects/trainiq/trainiq/app.py` — `_build_configured_connectors()`:
  resolve `get_athlete_timezone(config_path)` and pass it as
  `StravaUnofficialConnector(credential_store,
  local_timezone=athlete_timezone)`, same shape as the existing Eufy
  `device_id` wiring immediately below it in the same function.
- `projects/trainiq/trainiq/sync/engine.py` — new module-level function
  `recompute_checkpoint_from_normalized()` (see "Resume-cursor
  remediation" below). Reuses the existing `_iso_now()` module helper.
- `projects/trainiq/scripts/renormalize_strava_unofficial.py` — construct
  the connector with a resolved `local_timezone` (new `--config-path`
  CLI arg, defaulting to `APP_SUPPORT_DIR / "config.json"`, matching the
  existing `--db-path` convention exactly), and call
  `recompute_checkpoint_from_normalized()` after `renormalize_provider()`,
  before `conn.commit()`.
- `projects/trainiq/scripts/run_dedup_backfill.py` — **no code change**;
  simply re-run after the above, per requirements scope. Listed here only
  because the Developer's task breakdown includes actually re-running it
  as part of this issue's verification (against fixture data, per the
  live-DB-access constraint below).
- `projects/trainiq/tests/test_strava_unofficial_connector.py` — fixtures
  listed in the requirements doc (`start_date_local_raw`-as-UTC-epoch at
  the checkpoint tests and the `normalize()` tests) need review; see Task
  breakdown and Risks/tradeoffs.
- **Not touched:** `trainiq/connectors/strava.py`, `trainiq/connectors/
  peloton.py`, `trainiq/dedup/detector.py`, `trainiq/storage/schema.py`
  (no schema change — `sync_checkpoints.last_cursor` is already `TEXT`,
  already holds ISO 8601 strings for this provider).

## Interfaces/contracts

```python
# trainiq/config.py — new, same shape as get/set_eufy_device_id
def get_athlete_timezone(config_path: Path) -> Optional[str]: ...
def set_athlete_timezone(config_path: Path, timezone_name: str) -> None: ...
```

```python
# trainiq/connectors/strava_unofficial.py
_START_FIELD_CANDIDATES = ("start_time", "start_date_local_raw", "start_date_raw", "start_date", "start_day")

class StravaUnofficialConnector(Connector):
    def __init__(
        self,
        credential_store: CredentialStore,
        session: Any = None,
        base_url: str = DEFAULT_BASE_URL,
        local_timezone: str | None = None,   # NEW — IANA zone name, already resolved by the caller
    ):
        ...
        self._local_timezone = local_timezone if local_timezone is not None else _detect_system_timezone()

    # normalize() and download()'s early-stop check both now call:
    #     _extract_start_time_iso(item, self._local_timezone)

def _detect_system_timezone() -> str | None:
    """Best-effort IANA zone name from /etc/localtime's symlink target.
    None if undeterminable — callers must never substitute a fixed zone."""

def _extract_start_time_iso(raw: dict[str, Any], local_timezone: str | None) -> str:
    """Same fail-loud contract as today (raises StravaUnofficialHTTPError
    if no candidate field is present). NEW: also raises
    StravaUnofficialHTTPError if the start_time field is present but
    offset-naive, or if the start_date_local_raw fallback is reached with
    local_timezone is None, or if local_timezone names an unknown zone."""
```

```python
# trainiq/sync/engine.py — new, module-level (alongside upsert_normalized_activity)
def recompute_checkpoint_from_normalized(
    conn: sqlite3.Connection, provider: str, strategy: str = "default"
) -> Optional[str]:
    """Sets sync_checkpoints.last_cursor for (provider, strategy) to
    MAX(normalized_activities.start_time) for that provider, bypassing
    extract_resume_cursor()/a live connector entirely — a direct
    re-derivation from already-corrected data, for use after a
    renormalization pass changes historical start_time values out from
    under an already-persisted checkpoint. Returns the new cursor value,
    or None if the provider has no normalized_activities rows at all (an
    valid, reportable state, not an error — mirrors
    RenormalizeResult's own "0 rows" being unexceptional). Does not
    commit — caller owns the transaction boundary, matching every other
    function in this module and in trainiq.normalization.renormalize."""
```

Call-site addition in `scripts/renormalize_strava_unofficial.py`, directly
after `renormalize_provider()` and before `conn.commit()`:

```python
strategy_obj = connector.active_strategy()
strategy = strategy_obj.value if strategy_obj is not None else "default"
new_cursor = recompute_checkpoint_from_normalized(conn, PROVIDER, strategy=strategy)
```

(`strategy` resolves to `"unofficial_session"` for this connector — see
`AcquisitionStrategy.UNOFFICIAL_SESSION` in `connectors/base.py` — exactly
matching the `(provider, strategy)` key `SynchronizationEngine` itself
reads/writes during a live sync, via the identical `active_strategy()`
derivation already used in `sync/engine.py`'s `sync_connector()`.)

## Task breakdown

1. `config.py`: add `get_athlete_timezone()` / `set_athlete_timezone()`.
2. `strava_unofficial.py`:
   a. Add `_detect_system_timezone()`.
   b. Add `local_timezone` param to `__init__`; resolve the default.
   c. Reorder `_START_FIELD_CANDIDATES`, prepending `"start_time"`.
   d. Rewrite `_extract_start_time_iso()` to take `local_timezone` and
      branch on `start_time` / `start_date_local_raw` / everything else,
      per "Approach" above. Update both call sites (`normalize()`,
      `download()`'s early-stop loop) to pass `self._local_timezone`.
3. `app.py`: wire `get_athlete_timezone(config_path)` into
   `_build_configured_connectors()`'s `StravaUnofficialConnector(...)`
   construction, alongside the existing Eufy `device_id` resolution.
4. `sync/engine.py`: add `recompute_checkpoint_from_normalized()`.
5. `scripts/renormalize_strava_unofficial.py`: add `--config-path`,
   resolve `local_timezone` via `get_athlete_timezone()`, pass it into the
   connector constructor, and call
   `recompute_checkpoint_from_normalized()` after `renormalize_provider()`
   (print the new cursor value in the existing results block).
6. Review the two fixture locations the requirements doc flags
   (`test_strava_unofficial_connector.py`'s checkpoint-ordering tests and
   `normalize()` tests, both currently constructing
   `start_date_local_raw` as a bare UTC epoch): decide per-test whether
   the fixture should move to `start_time` (the now-primary field — most
   of these tests are not actually testing the fallback path, so this is
   likely the right move for most of them) or stay on
   `start_date_local_raw` with an explicit `local_timezone` fixture value
   (for the handful that should become the fallback-path regression
   tests described in AC3 below). Do not change what each test is
   asserting about *behavior* — only the fixture shape needed to reach
   that behavior under the new field priority.
7. New tests (see Test strategy notes).
8. Documentation-only (no code, this issue's scope is the connector +
   checkpoint fix): none further — `scripts/run_dedup_backfill.py` and
   `scripts/renormalize_strava_unofficial.py`'s own docstrings/CLI help
   don't need edits beyond the `--config-path` addition in step 5.

## Test strategy notes

- **Core AC3 (summer/winter fixtures → correct UTC, matching Peloton):**
  two `normalize()` tests using the BO's real evidence values —
  `start_time="2026-10-07T18:57:34+0000"` (BST case) and a winter
  equivalent — asserting the output equals the Peloton-epoch-equivalent
  ISO string for the same instant. This directly exercises the new
  primary path, not the fallback.
- **Fallback path (`start_date_local_raw`), explicitly regression-tested**
  now that it has real logic instead of a passthrough: a fixture with
  `start_time` absent, `start_date_local_raw` set to the BST epoch,
  `local_timezone="Europe/London"` passed into the connector — asserts
  the same corrected UTC result as the primary-path test. A second case
  with `local_timezone=None` passed explicitly asserts
  `StravaUnofficialHTTPError`, not a silent UTC-as-local guess.
- **`_detect_system_timezone()`:** unit-testable in isolation by
  monkeypatching the `/etc/localtime` path it reads (inject a `Path` or
  patch `Path("/etc/localtime")` resolution) — assert a
  `/usr/share/zoneinfo/Europe/London` target yields `"Europe/London"`,
  and a non-symlink or non-zoneinfo target yields `None`. Not
  live-environment-dependent if the path is injectable; if the Developer
  finds injecting it awkward, a `None`-detecting test is still required
  at minimum per the Testing scope boundaries note below.
- **Bad configured zone name:** `local_timezone="Not/AZone"` on the
  fallback path raises `StravaUnofficialHTTPError`, not an uncaught
  `ZoneInfoNotFoundError`.
- **`config.py`:** `get_athlete_timezone()`/`set_athlete_timezone()` —
  trivial round-trip tests mirroring `test_config.py`'s existing Eufy
  device_id tests exactly.
- **AC4 (renormalization corrects stored rows):** extend
  `test_renormalize.py`'s existing `strava_unofficial` coverage (if any —
  Developer to confirm) or add a fixture DB with a `raw_activities` row
  whose payload has the BST `start_time`, plus a `normalized_activities`
  row pre-seeded with the OLD (wrong, +1h) value, matching the real-world
  state this script will run against. Assert the corrected value after
  `renormalize_provider()` and that `raw_activities` is untouched
  (already covered conceptually by `renormalize.py`'s design — just
  needs this provider's specific before/after values).
- **AC5 (dedup backfill links the previously-missed pairs without
  duplicating existing ones):** extend `test_dedup_detector.py` with a
  fixture reproducing the BO's shape at small scale — a few winter pairs
  already in `dedup_links` (simulating "already linked from a prior
  run") plus a few summer pairs only correctly within `TIME_WINDOW_S`
  *after* the fix's corrected `start_time` — assert `run_backfill()`
  adds only the new pairs (`skipped_existing` matches the pre-seeded
  count, `linked` increases by exactly the new pairs). This test
  exercises `dedup/detector.py` unchanged — it's proving the *input data*
  this issue produces is now correct, not changing dedup logic itself.
- **AC6 (resume-cursor consistency), the part most likely to be
  under-tested if not called out explicitly:**
  - Unit test `recompute_checkpoint_from_normalized()` directly: seed
    `normalized_activities` rows for `strava_unofficial` with known
    `start_time` values (including one higher than any pre-existing
    `sync_checkpoints` row for that provider — simulating the exact
    "old wrong cursor was higher than the corrected max" scenario this
    fix must handle), call it, assert `sync_checkpoints.last_cursor`
    becomes the new `MAX(start_time)` — including the "lowers an
    existing, too-high cursor" case, not just "sets one from nothing."
  - A regression test proving *why* this matters:
    `SynchronizationEngine.get_checkpoint()` after a simulated
    renormalization returns the corrected (lower) cursor, and a
    subsequent `connector.download(since=<that corrected cursor>)` call
    (against a fake session scripting a BST-era activity whose corrected
    `start_time` is between the old wrong cursor and the new corrected
    one) does NOT stop early / skip it — i.e. reproduce the exact failure
    mode AC6 exists to prevent, then show the fix prevents it.
  - `recompute_checkpoint_from_normalized()` returning `None` for a
    provider with zero rows (fresh/test DB) — not an error.
- **Full existing suite stays green (AC7):** after step 6's fixture
  review, `pytest` run across the whole `tests/` directory, not just the
  Strava-unofficial file — `test_dedup_detector.py` and `test_sync_engine.py`
  both touch checkpoint/cursor behavior tangentially and should be
  re-checked even though neither is this issue's primary target.
- **Testing scope boundaries (per project convention):** everything above
  is fixture/mocked-data testing, consistent with this sandbox having no
  live Strava/Peloton account access. `_detect_system_timezone()` reading
  the *real* `/etc/localtime` on the BO's actual Mac, and running the two
  scripts against the BO's real database, are explicitly BO/ops
  responsibilities post-merge (per requirements doc's "Out of scope"), not
  something CI can verify.

## Risks/tradeoffs

- **`start_date_local_raw`'s fallback path is now exercised by zero
  known live payloads.** Every payload in this issue's and #30's evidence
  includes `start_time`. This is correct per the requirements doc (demote,
  don't delete — some future payload shape change could still omit
  `start_time`), but it means the new local-timezone-conversion code path
  is validated only by fixtures, never by a real Strava response, until/
  unless it's ever actually hit in production. Flagged, not treated as a
  blocker — consistent with how `start_day`/`start_date` fallbacks already
  work today (candidates tried in order specifically so an unused one
  fails loud rather than silently, if it's ever actually reached).
- **System-timezone detection is macOS-specific** (`/etc/localtime`
  symlink convention). This matches the project's only supported platform
  today (`trainiq.spec`, `Library/Application Support` paths throughout),
  so it's not a new platform assumption, only a new *use* of one. If
  TrainIQ is ever ported off macOS, this specific detection — not the
  rest of this fix — would need a platform-specific equivalent.
- **`zoneinfo.ZoneInfo` depends on the OS's IANA tz database being
  present.** macOS ships one system-wide (`/usr/share/zoneinfo`), so no
  new dependency (e.g. the `tzdata` PyPI package) is needed for this
  project's actual deployment target. Not re-verified against the
  PyInstaller-packaged binary specifically (`trainiq.spec`) — if the
  packaged app somehow can't see the system zoneinfo database (unusual,
  but not re-confirmed here), `ZoneInfo()` raises
  `ZoneInfoNotFoundError`, which this design already converts to a loud,
  diagnosable `StravaUnofficialHTTPError` rather than a silent wrong
  answer — so the failure mode is safe even if this risk materializes.
- **Existing tests encoding the old buggy assumption must change** (see
  Task breakdown item 6) — this is a known, called-out consequence of the
  fix, not a regression discovered later. The Developer should treat a
  fixture using `start_date_local_raw` as a UTC epoch, post-fix, as
  "testing the wrong thing now," not as "the fix broke this test."
- **Checkpoint recomputation is a blunt instrument.** `MAX(start_time)`
  across ALL of a provider's `normalized_activities` rows, every time it's
  called, is correct for this one-off post-renormalization use (matching
  exactly what a from-scratch resync would have arrived at), but it's
  intentionally not wired into the live `SynchronizationEngine` path —
  a normal sync still only advances the checkpoint forward via
  `extract_resume_cursor()`, per-record, as today. This function is scoped
  to the one-off remediation script only, not a general-purpose checkpoint
  repair tool.
