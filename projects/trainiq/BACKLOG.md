# TrainIQ — Engineering Backlog

Tracked items that are neither bugs to fix immediately nor forgotten. Each
entry states which epic it was raised in, which epic (if any) is expected to
resolve it, and its current status. Per the Constitution, nothing here gets
silently reinterpreted — an item is closed only when the epic that owns it
explicitly closes it.

---

## DOCUMENTED BEHAVIORS

*(Not backlog items — no action required. Recorded here so intentional
system behavior is discoverable in one place rather than left implicit in
test coverage, per the Chief Architect's explicit request after Epic 7.)*

### DB-001 — Training load computation is forward-only with respect to `athlete_profile`
**Raised:** Epic 7 final slice, Chief Architect review.
**Behavior:** `SynchronizationEngine` uses whatever `AthleteProfile` it was
constructed with for every activity normalized during its lifetime.
Activities normalized in past runs are never retroactively recomputed when
the profile is later added or changed — an activity synced before a
profile existed keeps `training_load_method="unknown"` permanently.
**Why this is intentional, not a gap:** recomputing historical
`training_load` on every profile edit is a genuinely separate capability
(it would need its own design: batch scope, which historical window to
touch, how to represent "recomputed under profile version N" if ever
needed) — not a natural extension of the existing forward-only pipeline.
**Documented in:** `trainiq/sync/engine.py`'s module docstring (authoritative)
and `tests/test_athlete_profile_integration.py::test_updating_stored_profile_affects_subsequent_syncs_only`
(behavioral proof).
**Status:** Informational. Revisit only if backfilling historical training
load is ever actually requested as a feature — at which point it should be
scoped as its own slice/ADR, not assumed to already be covered here.

---

## OPEN

### BL-010 — Peloton's real `/api/me` distance-unit field is unconfirmed
**Raised:** Issue #45 implementation. Issue #45 fixed `PelotonConnector`'s
distance-unit bug (it always assumed km; the live API actually reports
the account's own display unit, miles for this BO) by resolving the unit
from `GET /api/me` per account, rather than hard-coding either unit. The
field it reads, `ACCOUNT_DISTANCE_UNIT_FIELD = "distance_unit"`, is the
issue's own suggested value — the Architect flagged it explicitly as
**unconfirmed**, since confirming it requires a live diagnostic against
the BO's own account (same category as BL-008's endpoint-shape question).
**Why still open:** that live diagnostic (architecture doc's Task 1:
capture the **full** `/api/me` response body with the BO's manually-
supplied bearer token — the only prior pass, 2026-09-28, recorded just
`id` from that endpoint) requires live-account access this sandbox
doesn't have. Per `docs/trainiq/roles/technical-architect.md`'s "Testing
scope boundaries," live-account testing is BO responsibility, not Dev/QA
— so the Developer routine implemented the rest of the fix (connector
logic, one-off correction script, tests) against the flagged placeholder
and left this item open rather than guessing a confirmation that didn't
happen. See `docs/trainiq/verification/peloton-2026-09-28.md`'s
2026-10-09 addendum.
**What closes this:** whoever has the BO's manual bearer token runs the
Task 1 diagnostic, confirms or corrects `ACCOUNT_DISTANCE_UNIT_FIELD` and
`_DISTANCE_UNIT_ALIASES` in `trainiq/connectors/peloton.py`, and records
the finding as a further dated addendum — same convention BL-008 used.
Everything downstream of `_resolve_account_distance_unit()` is unaffected
either way (by design — see the architecture doc's "Risks/tradeoffs").
**Status:** Open.

### BL-011 — Peloton class/ride detail response shape (and `workout_type`/`peloton_id`'s own existence) is unverified against a live account
**Raised:** Issue #46 implementation (class title/instructor/class
type/planned length).
**Detail:** same category as BL-008 (the workout-list endpoint, closed by
issue #5) and BL-010 (issue #45, account distance unit) — this
implementation rests on constants that are the issue's own claim, not
independently confirmed against this project's own live evidence. The one
real record on file (`docs/trainiq/verification/peloton-2026-09-28.md`)
does not show a `workout_type` or `peloton_id` field at all, and no
capture exists anywhere in this repo for the ride/class detail endpoint
(`GET /api/ride/{id}/details`) or the `joins=ride,ride.instructor`
parameter's response shape. Flagged constants, all in
`trainiq/connectors/peloton.py`: `WORKOUT_TYPE_FIELD`, `RIDE_ID_FIELD`,
`RIDE_DETAIL_ENDPOINT_TEMPLATE`, `RIDE_DETAIL_JOINS_PARAM`,
`CLASS_TITLE_FIELD`, `INSTRUCTOR_OBJECT_FIELD`, `INSTRUCTOR_NAME_FIELD`,
`CLASS_TYPE_RAW_FIELD`, `PLANNED_DURATION_FIELD`.
**Why not resolved here:** confirming these requires a live diagnostic
against the BO's real account using their manually-supplied bearer token
(`docs/trainiq/architecture/46-peloton-strava-class-metadata.md`'s Task
1) — this sandboxed Developer routine has no stored Peloton credentials
and no network path to `api.onepeloton.com`, so it cannot run that
verification itself. This implementation currently uses the per-ride-id
details-endpoint plan (plan (b) in the architecture doc) with the
flagged-UNCONFIRMED field names above; Task 1 may find the cheaper
joins-on-list approach (plan (a)) works instead, or that the field names
differ.
**What is NOT affected by this gap:** the schema (6 nullable columns on
`normalized_activities`), the skip-if-already-synced-since-checkpoint
optimization, the COALESCE-based upsert safety (so a future
`renormalize_provider()` re-run can never silently wipe backfilled class
metadata back to NULL), the backfill tool's resumability, and every
test's shape — only the flagged constants' literal values, and (if
`WORKOUT_TYPE_FIELD` turns out not to exist) `_is_class_workout()`'s
already-coded fallback branch, would need to change.
**Status:** Open — needs the BO (or whoever holds the manual bearer
token) to run Task 1's live diagnostic and update the flagged constants
accordingly, then close this item the way BL-008 was closed for issue #5.

### BL-009 — `weigh_ins` table has no `source_confidence` column
**Raised:** Epic 6 implementation (Normalization Engine, slice 5), discovered
while building `_build_weigh_in_record()` — checked against the actual
schema, not assumed. `normalized_activities` has `source_confidence`;
`weigh_ins` never got the equivalent column in schema v1.
**Why this matters:** weigh-in data is genuinely sparse and model-dependent
(Milestone 3 §4 — not every scale reports body fat or muscle mass), which
is exactly the situation `source_confidence` exists to describe.
`compute_source_confidence()` (Epic 6 slice 3) already supports `WEIGH_IN`
as a record kind and would compute a meaningful value — there's simply
nowhere to persist it today.
**Status:** Open — requires a schema migration (v2→v3) adding
`source_confidence REAL` to `weigh_ins`. Not fixed in slice 5, to avoid
silently expanding this slice's approved scope into an unplanned
migration. `_build_weigh_in_record()` does not call
`compute_source_confidence()` at all right now, rather than compute a
value with nowhere to go.
**Related, not resolved by:** Issue #38 / ADR-039 (schema v4) added
`is_flagged_implausible`, `plausibility_reason`, `bo_confirmed_valid`,
`bo_confirmed_at` to `weigh_ins` — a different, already-approved migration
for a different purpose (plausibility flagging, not confidence scoring).
`source_confidence` remains unaddressed; don't assume v4 covers it.
Issue #42 (schema v5) renamed two of those v4 columns and added two more
(splitting weight-plausibility from body-fat-plausibility) — same
category of unrelated, already-approved migration; `source_confidence`
remains unaddressed by v5 too.



### BL-008 — Peloton workout-list endpoint shape is unverified against a live account
**Raised:** Epic 3 implementation, same category as BL-006 (Eufy).
**Detail:** Milestone 2's research established the auth-breakage evidence
and general data-availability shape (workout metadata, discipline types,
HR/power presence patterns) with high confidence, but — unlike Milestone 1
for Strava — did not pin down a fully verified endpoint path and response
schema for the workout list itself. `download()` is built to the
best-documented shape found in that research (`GET /api/me/workouts`), not
a live-verified one.
**Status:** Closed as verified (2026-09-28) — see "Live bearer-token test"
below. The unknown this item was raised to resolve (what's the real
endpoint/shape?) now has a definitive answer. The code fix itself is
tracked separately as `aaime921/trainiq#5`, per this repo's convention of
GitHub Issues for pipeline-processed work vs. `BACKLOG.md` for standing
findings. **Verification protocol:** `docs/verification/PROTOCOL.md`.

**Live reconfirmation (2026-09-28):** ran a single, isolated, one-off
`POST /auth/login` against the real account (`scripts/debug_peloton_login_403.py`,
deleted after use per its own instructions — not `PelotonConnector._login()`
itself, to avoid any credential rotation from a call expected to fail).
Result: `403`, body `{"status": 403, "message": "Access forbidden. Endpoint
no longer accepting requests."}` — byte-for-byte the same failure mode
Milestone 2 documented. **The Oct 2025–Jan 2026 automated-login breakage is
confirmed UNCHANGED, not resolved, as of this date.** The automated auth
path being blocked means `GET /api/me/workouts` cannot be reached via it —
but see "Live bearer-token test" below, which reached it a different way.
See `docs/verification/peloton-2026-09-28.md`.

**Live bearer-token test (2026-09-28, same day, after the login test
above):** the BO supplied a token extracted from an authenticated browser
session. Three temporary, isolated, one-off diagnostics (deleted after use,
never touched `PelotonConnector`, credentials, or any persisted state)
found:
1. `GET /api/me/workouts` (what `download()` calls today) → **404**,
   `Not found: '/me/workouts'`. Not an auth failure — the token was valid.
2. `GET /api/me` → **200**. Bearer-token auth works fine; returns the
   account's real `id`.
3. `GET /api/user/{user_id}/workouts` → **200**, real paginated data (129
   workouts, 7 pages at `limit=20`).

**This is the real endpoint** — `download()`'s assumed path was simply
wrong, not just unreachable. The real records also don't have `duration`,
`avg_heart_rate`, `max_heart_rate`, `avg_power`, or `max_power` — fields
`normalize()` currently reads — only `end_time` (duration derivable),
`effort_zones.heart_rate_zone_durations` (no plain avg/max bpm at all), and
`total_work` in joules (avg watts derivable as `total_work/duration_s`; max
watts not available from this endpoint at all). Full record dumps and the
complete comparison table are in `docs/verification/peloton-2026-09-28.md`.
Filed as `aaime921/trainiq#5` for the actual `download()`/`normalize()`
fix, which will go through the normal pipeline (BA/Architect/Dev/QA) like
issue #1's Eufy fix. Real production use will still need a periodically
refreshed manual token (~48h lifetime, no auto-refresh) regardless of the
code fix, since the automated login itself remains broken.
**Update (issue #5 code fix landed):** `download()`/`normalize()` now
implement the real endpoint/pagination/field-mapping shape described
above — see the issue for the PR. Pending QA sign-off.

Two paths now exist for real Peloton data pending that fix: the
manual-recovery bearer-token path above (auth works, connector code
doesn't match the real shape yet), and the CSV-export importer below,
which **is** already fully working today.

**CSV-import path now operational (2026-09-28):** `trainiq/csv_import/peloton_csv.py`
(added, untested end-to-end, in commit `f16f37f`) had no CLI ever calling
it outside the test suite. Added `scripts/import_peloton_csv.py` as that
missing entry point and ran it for real against the live `trainiq.db`
(backed up first to `trainiq.before-peloton-csv-import.db`, matching the
existing `trainiq.before-strava.db` precedent) with a real, previously-
audited 120-row export (`data/peloton_analysis/aimea75_workouts.csv`,
byte-identical to the newest of several re-downloads in `~/Downloads`).
Result: 120 seen, 120 inserted, 0 skipped, 0 collisions. Discipline
breakdown: 118 `cycling`, 2 `other` (raw value `Stretching`, which
`_PELOTON_MAP` intentionally routes to `OTHER` — not a gap, a prior,
deliberate taxonomy decision, verified by inspection, not assumed).
`source_confidence` range 0.25–1.0, avg 0.96, consistent with real,
variably-complete workout data. **This is the current, practical answer to
"how do we get Peloton data into TrainIQ"** while the live API
(automated-login side) remains blocked per the finding above.

**PKCE OAuth flow investigated, initially misjudged as abandoned, then
confirmed working (2026-09-29):** an unofficial third-party client
(`@dofek/peloton`, github.com/Asherlc/dofek) claims Peloton's own web app
uses a standard Auth0 authorization-code+PKCE flow
(`client_id=WVoJxVDdPoFx4RNewvvg6ch2mZ7bwnsM`, `redirect_uri=
https://members.onepeloton.com/callback`, `scope` includes
`offline_access`) that would issue a real refresh token — verified by
reading that client's actual source directly, not just its README summary.

First two live attempts (`scripts/debug_peloton_oauth_pkce.py`) failed with
`invalid_grant`/"Invalid authorization code": `members.onepeloton.com`'s
own frontend JS consumes the authorization code itself as part of its
normal login flow (observed landing on `/profile/overview`, confirming its
own exchange completed) before a human can copy it out of the address bar.
A third attempt hung indefinitely on the Auth0 login page, which at the
time looked like bot-detection reacting to repeated attempts — this was
recorded here as a decision to abandon the approach entirely.

**That call was premature.** A follow-up attempt, using a different
technique to stop the race — blocking JavaScript entirely for
`members.onepeloton.com` via `chrome://settings/content/javascript` (so
the site's own callback page can't auto-redirect/auto-consume the code
before it's copied) — worked cleanly end to end: `200` on the code
exchange, real `access_token` and `refresh_token` returned
(`expires_in=172800`, i.e. 48h, matching the known session lifetime),
`GET /api/me` succeeded with the access token, and — the critical
question — a live `grant_type=refresh_token` exchange also returned `200`
with a new `access_token` and a rotated `refresh_token`. The earlier hang
was most likely a transient/session-state issue, not a hard block; no
further repeated attempts were needed to reach this clean result.

**Implication:** a one-time human browser login (via this PKCE flow, using
the JS-blocking technique to capture the code) plus a stored, rotating
refresh token could let `PelotonConnector` refresh its own access token
silently going forward — the same shape as the existing Strava connector's
`rotate()`-based refresh token — instead of needing a fresh manual bearer
token extracted every ~48h. Not yet built into `PelotonConnector` itself;
this was a POC only (per this repo's own convention of proving an external
contract outside the governed codebase before committing to it), and it
still carries the same standing caveat as the rest of this item: unofficial
API, no written permission from Peloton, contract can change without
notice. The manual bearer-token path (`scripts/peloton_smart_sync.py`)
remains the working fallback in the meantime.

### BL-001 — Review `stravalib` releases before January 2027
**Raised:** Epic 1 self-review, carried forward from Milestone 1 (R-STRAVA-02).
**Corrected (2026-10, #29):** this item originally stated Strava's API base URL migrates from `api.strava.com` to `api-v3.strava.com`. `api.strava.com` has no DNS record and was never a valid Strava host (confirmed on #26). The real migration, per Strava's own developer changelog / community-hub announcement, is from `https://www.strava.com/api/v3` — the actual current host, also independently confirmed as `stravalib`'s own hardcoded `ApiV3.server` value — to `https://api-v3.strava.com`, available 2027-01-04, final deadline 2027-06-01. See ADR-007 for the same correction. The naming error doesn't change the underlying action item: `stravalib` still owns this host internally, and TrainIQ still depends on the maintainers migrating it before the deadline.
**Why it's here, not just in a comment:** Strava's API base URL migrates from
`www.strava.com/api/v3` to `api-v3.strava.com` on **January 4, 2027** (final
cutover deadline June 1, 2027). `stravalib` v2.5.0 hardcodes the current URL
internally (`ApiV3.server = "www.strava.com"`), not exposed as a constructor
override. TrainIQ's own code never hardcodes it (ADR-007 is satisfied at our
layer), but the actual mitigation depends entirely on the `stravalib`
maintainers updating their library before that date.
**Action required:** check `stravalib`'s changelog/releases in Q4 2026; if no
fix has shipped by then, this becomes a real blocker requiring either a
patched fork, a monkey-patch of `ApiV3.server`, or a move away from
`stravalib` entirely for the base-URL portion of the client.
**Status:** Open, not urgent for the base-URL migration itself (roughly three months of runway as of this writing, 2026-10) but still required before RC1 per the Product Owner's live-verification priority. **Verification protocol:** `docs/verification/PROTOCOL.md`.

### BL-002 — Normalization mapping contract: partially resolved by Epic 6, typed-key contract still missing
**Raised:** Epic 0 self-review, Finding 1. **Rewritten:** Epic 7 Discovery Report, per Chief Architect direction — the original wording described the pre-Epic-6 situation and no longer reflects reality.
**Original concern:** `Connector.normalize()`'s required output keys were an informal convention, not an enforced contract.
**What Epic 6 actually resolved:** `RecordKind` (slice 1) gives an explicit, connector-declared contract for *which shape* a connector produces (ACTIVITY vs. WEIGH_IN) — this closes the "which table, which pipeline" half of the original concern.
**What remains open:** there is still no typed contract (e.g. a `TypedDict` or dataclass) for the *exact keys required within* each declared shape. `build_canonical_record()` (`trainiq/normalization/engine.py`) still indexes `normalized["external_id"]`, `normalized["start_time"]`, `normalized["duration_s"]`, etc. against a plain `dict`, relying on convention matching `RawActivity`/`WeighIn`'s informal shape from Feature 0.7. A future connector could still drift on field names within a correctly-declared `record_kind` without any static check catching it.
**Status:** Open — narrower in scope than originally described, not resolved.

---

## CLOSED

### BL-006 — Eufy Cloud API: `start_time` has no observable effect on `/device/{id}/data`
**Raised:** Epic 2 implementation, carrying forward Milestone 3's Open
Question 1. Narrowed by RC1-HF-003 (device discovery and login fully
verified live). This entry resolves the remaining, narrower question:
whether the endpoint's `start_time` parameter provides incremental
filtering.
**Finding, from a real network experiment (`scripts/debug_eufy.py --since`),
stated precisely — proven vs. still open:**
- **Proven:** `GET /v1/device/{device_id}/data` returns byte-for-byte
  identical responses (357540 bytes, 522 records, same oldest/newest
  timestamps) regardless of whether `start_time` is omitted, set to the
  most recent record's own timestamp, or set to a value far in the future
  (`9999999999`). The parameter, as currently sent, has no observable
  effect on this endpoint's output.
- **Not proven, and not claimed:** *why* it has no effect. Three
  explanations remain equally consistent with the evidence and are not
  distinguished by it — the parameter name may be wrong, the correct
  filtering mechanism may live on a different endpoint or use different
  parameters entirely (pagination, device type, firmware version), or the
  endpoint may genuinely not support incremental filtering at all. The
  practical consequence is identical under all three, which is why this
  is closed as a finding rather than left open pending further
  disambiguation — but the specific mechanism was deliberately not
  overclaimed.
**Decision:** keep the checkpoint infrastructure (`sync_checkpoints`,
`extract_resume_cursor()`) unchanged — it costs nothing to maintain and
becomes immediately useful if a future Eufy API change, or a
correctly-identified alternative parameter, makes filtering possible.
`EufyConnector` continues performing a full-history download on every
sync. `INSERT OR IGNORE` + conditional `UPDATE` (RC1-HF-006) already
makes this idempotent — every sync after the first correctly reports
"522 updated, 0 inserted," not duplicate rows.
**Consequence, stated plainly:** this is O(full history) network and
processing cost on every sync, not O(new records since last sync) — a
real, permanent limitation, not a defect, and one that grows as history
grows. Not a regression in TrainIQ; a limitation of the external API as
currently understood.
**Future work:** if Eufy ever documents or exposes a working incremental
mechanism, only `EufyConnector.download()` needs to change — the
checkpoint infrastructure, `SynchronizationEngine`, and the routing logic
all already support it without modification.
**Closed:** during Release Candidate Preparation, backed by a live
network experiment, not source inspection alone. All temporary print()-based
RCA instrumentation added across this investigation (`STEP N` markers in
`app.py`; `SYNC_CONNECTOR START`/`CALLING CONNECTOR`/`CONNECTOR RETURNED`/
`RUN_ONCE START`/etc. in `sync/engine.py`) has been removed now that the
finding is confirmed — only the permanent production summary log
(`downloaded/inserted/updated/malformed/skipped`, plus the generic
`supports_incremental_sync` note) remains. 284 tests passing, confirming
the removal was purely diagnostic scaffolding with no test ever depending
on it — the correct sign that none of it belonged in production.

### RC1-HF-006 — RCA: `normalized_activities` empty was correct behavior; sync summary UX overhauled with real counts
**Raised:** 522 records synced from Eufy, `raw_activities` populated,
`normalized_activities` empty, no exceptions — appeared to be a defect.
**Root cause analysis, confirmed by direct code inspection, not
speculation:** `EufyConnector.record_kind = RecordKind.WEIGH_IN`.
`_persist_canonical_record()` routes strictly by `record_kind` (Epic 6,
BL-005) — `WEIGH_IN` records go to `weigh_ins`, never
`normalized_activities`. This is correct, designed behavior, not a bug —
`normalized_activities` is Eufy's wrong table by design, since a scale
produces weigh-ins, not activities. The actual gap was diagnostic, not
functional: nothing showed *which* table received the data, and the old
summary line (`"synced N record(s)"`) didn't distinguish new records from
already-existing ones or from records that failed to persist.
**Resolved by:** a real sync-summary overhaul, not a database fix.
`_upsert_normalized_activity()` and `_upsert_weigh_in()` now use
`INSERT OR IGNORE` (checking `cursor.rowcount`, verified directly:
1 = genuinely new row, 0 = conflict) followed by an explicit `UPDATE` only
when needed — giving a **real, counted, non-estimated** distinction
between inserted and updated rows, at the cost of exactly one extra
statement only for records that already existed (the common case, a new
record, costs the same single statement as before). `ConnectorSyncResult`
gained four new fields (`records_inserted`, `records_updated`,
`records_malformed`, `records_skipped`), purely additive with `0`
defaults — `records_upserted`'s existing meaning (total successfully
persisted) is unchanged, so no existing test needed modification. The
summary log line now reads `"downloaded N, inserted N, updated N,
malformed N, skipped N"` instead of the misleading single number.
**Verified by:** 5 new tests — first-sync-all-inserted, second-sync-
same-records-all-updated (using a connector that ignores the resume
cursor specifically to exercise the update path, since normal incremental
sync correctly filters already-synced records out), a mixed insert/update
batch, the exact Eufy WEIGH_IN scenario from the RCA itself, and
missing-external-id counted separately from malformed-record failures.
279 tests passing project-wide, all 274 pre-existing tests unchanged.
**Closed:** during Release Candidate Preparation.

### RC1-HF-005 — Runtime safety check against executing from macOS Trash
**Raised:** Live-verification session — a stale copy of the project
sitting in macOS Trash was executed instead of the intended copy,
producing confusing behavior differences that took real debugging time
to trace back to "which copy of TrainIQ is actually running" rather than
any bug in the code itself. A static audit for hardcoded `/Users/`,
`/.Trash/`, `/Downloads/` references found nothing (correctly — the
problem was never hardcoded paths), which is what motivated a runtime
check instead of a static one.
**Resolved by:** `trainiq/safety.py` — `assert_not_running_from_trash()`
resolves the executing package's real filesystem location via
`Path(__file__).resolve()`, never `os.getcwd()`/`$PWD` (both reflect
shell state, which is exactly what disagreed with the actual loaded
package in the incident this closes). Symlinks are followed to their
real target before the check runs, so a symlink that merely *points*
into Trash is caught, not just a literal `/.Trash/` path typed directly.
Checks both `.Trash` (per-user) and `.Trashes` (volume-level, external/
network drives) as exact path segments — broader than the originally
specified literal string, since `.Trashes` is the same category of risk.
Wired into `trainiq/app.py`'s `main()` as the first action after logging
is configured: aborts with a clear FATAL message (printed to stderr and
logged to `diagnostic.log`) before any database is opened or any
connector is touched if the check fails; on every normal startup, logs
`Executing package: <resolved path>` unconditionally, so any future
version-mismatch question is answered directly by the log rather than
re-debugged from scratch.
**Verified by:** 7 tests in `test_safety.py` (normal path, `.Trash`,
`.Trashes`, a symlink resolving into Trash, a symlink resolving to a
normal path, the real no-argument code path, and the error message's
content) plus 2 integration tests in `test_app.py` confirming `main()`
actually calls this check and logs the package path on every run, not
just in isolation. 274 tests passing project-wide.
**Closed:** during Release Candidate Preparation.

### RC1-HF-004 — Diagnostic log now captures full tracebacks for unexpected exceptions
**Raised:** During RC1-HF-003 follow-up, after a claimed fix and an
observed persistent failure could not be reconciled from a one-line
error message alone. Rather than continue debating which side's file
state was correct, the more durable fix was to make the log itself
produce unambiguous evidence going forward.
**Resolved by:** `sync_connector()`'s existing catch-all exception
handler (RC1-HF-001) now logs via `diagnostic_logger().opt(exception=True).error(...)`
instead of a plain `.error(...)` call — loguru captures the real,
current traceback via `sys.exc_info()` at the point the handler runs,
the same underlying mechanism as `traceback.format_exc()`, integrated
into the existing logging setup rather than a separate print statement.
Made **permanent**, not a temporary debugging hack — a bare exception
type and message is not enough to know which file/line/call-stack an
exception actually originated from once more than one code path could
plausibly produce the same exception type, which is exactly the
ambiguity this closes for every future unexpected-exception case, not
just this one.
**Verified by:** `test_rc1_hf_004_diagnostic_log_captures_full_traceback_not_just_message`
— uses a real loguru handler writing to a real temp file (not a mock),
and asserts the log contains `"Traceback (most recent call last)"`, the
exception type, and a reference to the actual source file the exception
was raised from. 265 tests passing project-wide.
**Closed:** during Release Candidate Preparation.

### RC1-HF-003 — Eufy login was incomplete; device discovery is automatic, not manual
**Raised:** Live-account testing during Release Candidate Preparation,
following a research-then-verify sequence: public-source research
identified a plausible cause (missing fields), a curl test against a real
account confirmed it directly, then the fix was implemented against that
confirmed evidence — not against the research alone.
**Root cause, verified live, not inferred:** `EufyConnector._login()`
sent only `{"email": ..., "password": ...}` with no headers. The correct
request — confirmed working against a real account — requires
`client_id: "eufy-app"` and `client_secret: "8FHf22gaTKu7MZXqz5zytw"` in
the JSON body (TrainIQ's own registered-app identity with Eufy's backend,
not a per-user secret) and a `category: Health` header. Without these,
the endpoint returned HTTP 200 with an application-level
`{"res_code":500,"message":"Service is temporarily unavailable..."}` — a
generic, misleading error that gave no indication of a missing field,
which is why this was never caught by any test against a mock (every mock
necessarily assumed the request shape was already correct).
**Second finding, also verified live, better than the original design
question anticipated:** the login response itself includes a `devices`
array (`[{"id": "eufyt9150cfe90116a933", "name": "Smart Scale P3"}]`).
Milestone 3's original open question — whether a "list devices" endpoint
exists at all — is resolved not by finding a separate endpoint, but by
discovering one was never needed: login already returns it.
**Resolved by:**
- `EufyConnector._login()` corrected to the verified request shape.
- `device_id` is now an optional constructor parameter, not mandatory —
  `EufyConnector.discovered_devices()` exposes what the login response
  returned, and `select_device()` finalizes the choice.
- `setup_wizard.py`'s Eufy step no longer prompts for a device ID at all.
  It authenticates, reads `discovered_devices()`, auto-selects when
  exactly one device is found (or one matches a "scale"-like name, as a
  soft heuristic, not a hard requirement), and only prompts with a short
  numbered choice when genuinely ambiguous.
- The separate `GET /device/` endpoint remains unimplemented — correctly:
  it's unnecessary for RC1 now that login supplies the same information,
  and remains a reasonable future fallback if a login response is ever
  missing `devices` for some accounts.
**Verified by:** 5 new tests in `test_eufy_connector.py` (login payload
shape, `discovered_devices()` population and defensive empty-list
handling, `device_id`'s new optionality, `select_device()`), and updated/
new tests in `test_setup_wizard.py` covering single-device auto-selection,
multiple-device choice, and zero-devices rollback. 257 tests passing
project-wide.
**BL-006 narrowed, not fully closed** — see BL-006's updated entry: the
`/device/{id}/data` history payload's exact schema remains unverified,
separately from login/discovery, which this fix did not touch.
**Closed:** during Release Candidate Preparation, following the full
Evidence → Analysis → Assessment → Recommended Action → Review →
Implementation → Verification sequence, with live-account confirmation
at the Evidence step before any code was written.

### RC1-HF-002 — First-Time Provider Configuration
**Raised:** Live-verification-readiness review. Confirmed by repository
audit (Discovery Report): no code path existed anywhere for a user to
actually acquire credentials — `CredentialStore`, every connector, and
`stravalib`'s OAuth methods were all built and tested in isolation, but
nothing assembled them into something a person could walk through. Same
root cause as RC1-HF-001 (Epic 0 deferred real UI/operational flow; no
later epic's scope ever picked it back up), a different half of the gap.
**Resolved by:** a first-time setup wizard (`trainiq/setup_wizard.py`),
auto-triggered from `trainiq.app.main()` only when zero connectors are
configured — no separate `configure` entry point, per the Chief
Architect's explicit Design Review decision (single entry point, Option
A). Strava uses manual authorization-code paste (no local HTTP listener,
no new networking dependency); Peloton/Eufy prompt for email+`getpass`-
masked password. Every provider follows store → validate against the real
connector → keep on success, roll back (delete) on ANY failure including
an unexpected exception, not just a clean rejection.
**Eufy's `device_id`:** classified as non-secret configuration, not a
credential, per explicit Chief Architect distinction — persisted in a new
plain JSON file (`trainiq/config.py`, `~/Library/Application
Support/TrainIQ/config.json`), not Keychain and not a new database
migration (a schema migration for one string was judged disproportionate
cost during RC1 specifically — revisit only if genuinely richer
configuration needs emerge from real usage). The `EUFY_DEVICE_ID`
environment variable introduced in RC1-HF-001 is fully retired.
**A real, useful finding during implementation, not just a test
artifact:** pytest's captured stdin raises `OSError`, not `EOFError`, when
`input()` is called — surfaced by a test, but the underlying scenario
(no interactive terminal available at all) is real, not sandbox-specific,
so `setup_wizard.py`'s cancellation handling now catches `OSError`
alongside `KeyboardInterrupt`/`EOFError`, not just the two originally
anticipated.
**Verified by:** 12 new tests in `test_setup_wizard.py` (every provider's
happy path, rejection-triggers-rollback, unexpected-exception-also-
triggers-rollback, and cancellation preserving already-configured
providers), 9 new tests in `test_config.py`, and `test_app.py` updated to
reflect wizard auto-triggering and config.json. 250 tests passing
project-wide.
**Closed:** during Release Candidate Preparation, per explicit,
staged authorization (Discovery → Review → Design → Review → Implementation).

### RC1-HF-001 — Unexpected connector exceptions crashed the entire sync batch
**Raised:** Discovered while writing regression tests for the composition-root
wiring (`trainiq/app.py`), authorized as Release Engineering per the PO/Reviewer
workflow. Not a defect in the wiring itself — the wiring surfaced it.
**Evidence:** `SynchronizationEngine.sync_connector()`'s exception handling
caught only `AuthenticationError` and `TransientError`. Reproduced directly:
a real `StravaConnector`, correctly configured with a stored refresh token
but with no `STRAVA_CLIENT_ID`/`STRAVA_CLIENT_SECRET` in the environment,
raises a bare `RuntimeError` on `authenticate()` — which propagated
uncaught through `run_once()`'s list comprehension and crashed every other
connector in the same batch, confirmed by a minimal reproduction script
before any fix was written.
**Why this was correctly scoped as a boundary fix, not a Strava fix:** the
gap was in the Sync Engine's outer resilience boundary, not in Strava's
connector — the identical failure mode would apply to an unexpected
exception from `requests`, `sqlite3`, `keyring`, Eufy, or Peloton, none of
which were specific to this one reproduction.
**Resolved by:** a new catch-all `except Exception` boundary in
`sync_connector()`, applying the same conservative state-transition logic
already used for `AuthenticationError` (including respecting ADR-038's
escalation rule and the `RecoveryRequired`-can-only-go-to-`Healthy`
legality constraint) — logged at `ERROR` level specifically, since an
unexpected exception type may indicate a real bug, not just an
already-designed-for degradation cause.
**Verified by:** `test_rc1_hf_001_unexpected_exception_does_not_crash_the_batch`
and `test_rc1_hf_001_unexpected_exception_respects_recovery_required_legality`
(`tests/test_sync_engine.py`), plus a direct re-run of the original
reproduction using the real `StravaConnector` — confirmed the connector now
degrades cleanly to `Degraded` and the other connector in the batch
completes normally. 228 tests passing project-wide.
**Process note, worth preserving:** this defect was found by writing a
test for a change explicitly authorized to be wiring-only, not by
searching for bugs — the test surfaced unexpected behavior, the behavior
was confirmed not to be a test artifact, and the finding was reported
through Evidence → Analysis → Assessment → Recommended Action and held
for explicit authorization before any fix was written, per the workflow
established for this phase of the project.
**Closed:** during Release Candidate Preparation, per explicit,
narrowly-scoped authorization (`SynchronizationEngine` only).

### BL-003 — `sync_connector()` needed editing, not just extending, for Epic 6
**Raised:** Epic 0 self-review, Finding 3.
**Resolved by:** Epic 6, slice 5 (routing). `SynchronizationEngine.sync_connector()`
was edited exactly as predicted — it now calls `build_canonical_record()` and
routes the result to `normalized_activities` or `weigh_ins` via `record_kind`,
rather than discarding `normalize()`'s output beyond cursor extraction.
**Closed:** Epic 7 Discovery Report — found still marked Open despite being
fully resolved by Epic 6; corrected as opening housekeeping before Epic 7
implementation began, per Chief Architect direction.

### BL-007 — Degraded connectors were never automatically re-attempted; the Soft Degradation policy couldn't function as designed
**Raised:** Epic 3 implementation (Peloton connector). The initial
hypothesis about the mechanism was itself wrong, worth recording rather
than quietly correcting without a trace: the prediction going in was "the
Sync Engine loses track of *when* a connector first became Degraded."
What was actually verified was more fundamental — the Sync Engine never
even attempted to re-authenticate a Degraded connector again, at all, on
any subsequent run.
**Resolved by:** **ADR-038 — Connector Lifecycle Policy**
(`docs/adr/ADR-038-connector-lifecycle-policy.md`), reviewed and revised
twice before implementation, per the Chief Architect's explicit "draft
before code" mandate. New module `trainiq.sync.lifecycle_policy` — a pure,
deterministic `evaluate()` function taking persisted state and returning
SKIP/RETRY/ESCALATE, with zero I/O and zero side effects, per the Chief
Architect's implementation discipline requirement. `connector_state`
gained four columns (schema v1→v2): `state_entered_at`, `last_attempt_at`,
`attempt_count_in_state`, `next_eligible_retry_at`.
**A second, independently significant gap found and fixed during this
implementation, not anticipated by the ADR itself:** connector state was
never being restored from the database on a fresh `Connector` instance —
every restart silently reset every connector to `Healthy` in memory,
regardless of what was persisted. This meant Graceful Degradation only
ever worked within a single continuous process, never across an app
restart, which would have made ADR-038's entire premise moot. Fixed via a
new `ProviderStateMachine.restore()` / `Connector.restore_state()` method
that loads persisted state without going through transition-legality
checks (a restore is not a real transition). Directly verified by
`test_adr038_gate_4_lifecycle_persists_across_a_simulated_application_restart`.
**A third bug caught during implementation, before it shipped:** the
initial backoff-cadence indexing was off by one relative to ADR-038 §4's
literal stated sequence ("1, then 2, then 4 days") — the first
implementation would have skipped the 1-day step entirely. Caught by
re-deriving the mapping from the ADR's own text rather than trusting the
first implementation, before any test was written against the wrong
version. Documented in `lifecycle_policy._backoff_cadence()`'s own
docstring so the correction isn't silently lost.
**A fourth, latent bug surfaced (not introduced) by this work:**
`RecoveryRequired`'s only legal outgoing transition is `Healthy`
(ADR-010). Before ADR-038, `RecoveryRequired` connectors were never
re-attempted, so a second consecutive failure while already
`RecoveryRequired` was a code path that had never executed. Now that it
runs every attempt (the default policy), a naive "always transition to
Degraded on failure" would have raised `InvalidStateTransition`. Fixed by
targeting `pre_attempt_state` (not unconditionally `Degraded`) when not
escalating. Verified by `test_adr038_recovery_required_repeated_failure_does_not_raise_illegal_transition`.
**Verified by:** 14 pure-function tests (`test_lifecycle_policy.py`), 5
Quality Gate tests (including Restart Transparency, added after initial
Chief Architect review — a stronger property than plain persistence:
proves a restart produces byte-for-byte identical `connector_state`
evolution, not just a preserved final state) plus the illegal-transition
regression (`test_sync_engine.py`), and 2 end-to-end tests through the
real Peloton connector proving both periodic re-attempt and 10-day
escalation actually happen (`test_peloton_connector.py`) — 142 tests
passing project-wide at the time this item was closed (Epic 3); this is a
historical snapshot, not a claim about the current test count.
**Closed:** during Epic 3, per ADR-038.

### BL-005 — Checkpoint cursor extraction was activity-shaped only, didn't generalize to weigh-in data
**Raised:** Epic 2 implementation (Eufy connector), discovered via a failing
end-to-end test, verified empirically before being written down.
**Resolved by:** a refinement of ADR-013, NOT a new ADR — per the Chief
Architect's diagnosis: "L'architettura dice già: il Connector conosce il
provider. Ma l'implementazione lascia ancora al Sync Engine un pezzetto di
conoscenza del provider. ... Non stiamo estendendo l'architettura. La
stiamo rendendo più coerente." This was Epic 0's Sync Engine not fully
honoring an abstraction the Connector Framework had already committed to —
`RawActivity` was simply the only shape it had ever been tested against
until Eufy's `WeighIn` shape existed.
**What changed:** `Connector` gained a new method, `extract_resume_cursor(normalized) -> str | None`,
with a default implementation (`normalized.get("start_time")`) that exactly
preserves existing behavior for `RawActivity`-shaped connectors (Strava —
zero changes needed). `SynchronizationEngine.sync_connector()` no longer
contains any field-name knowledge at all — verified directly by `grep`
returning zero matches for `start_time`/`timestamp` in `engine.py`.
`EufyConnector` overrides the method to read `timestamp` instead.
**Naming:** called "resume cursor," not "checkpoint cursor" or "cursor
field," per the Chief Architect's explicit generalization — today every
connector's cursor happens to be a timestamp, but the contract makes no
such assumption; an opaque token or sequence number would work identically
provided it satisfies the documented ordering precondition.
**Verified by:** `test_sync_engine_has_no_knowledge_of_any_specific_cursor_field_name`
— a connector using a deliberately made-up field name (`sequence_marker`,
neither `start_time` nor `timestamp`) checkpoints and resumes correctly,
proving the engine works purely through the connector-owned method with no
remaining knowledge of its own. Plus `test_end_to_end_sync_via_synchronization_engine`
(Eufy), now passing.
**Closed:** during Epic 2, as an implementation refinement — no new ADR,
per the Chief Architect's explicit ruling that this doesn't extend the
architecture, it makes the implementation match architecture that already existed.

### BL-004 — Generic transient-error retry policy is not tuned to Strava's actual rate-limit semantics
**Raised:** Epic 1 self-review, Finding S1.
**Resolved by:** **ADR-037 — Provider Directed Retry Policy** (`docs/adr/ADR-037-provider-directed-retry-policy.md`).
**Why this became an ADR, not a backlog fix:** the Chief Architect's explicit reasoning — this changes a shared Connector↔Sync Engine contract (`TransientError`'s shape and the Sync Engine's retry behavior), which the project's governance model treats as ADR material, not something any single epic resolves unilaterally.
**What changed:** `TransientError` gained an optional `retry_after_s` field. The Sync Engine's retry logic honors it when present, falls back to the pre-existing exponential backoff when absent. `StravaConnector` now passes through `stravalib.exc.RateLimitExceeded.timeout` instead of discarding it. `max_retries` still bounds total attempts regardless.
**Verified by:** `test_provider_directed_retry_honors_retry_after_s_not_generic_backoff`, `test_provider_directed_retry_still_respects_max_retries`, `test_transient_error_without_retry_after_s_still_uses_generic_backoff` (Sync Engine level); `test_download_rate_limited_passes_through_stravalib_timeout_per_adr_037`, `test_refresh_rate_limited_passes_through_stravalib_timeout_per_adr_037` (Strava connector level, using the real installed `stravalib` exception class).
**Closed:** during Epic 1, before Epic 2 began — per the Chief Architect's explicit sequencing decision.
