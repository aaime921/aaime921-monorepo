# Requirements: Strava Unofficial Start-Time Timezone Fix

**Issue:** #43

## Summary

`StravaUnofficialConnector` stores an incorrect `start_time` for every
activity synced during UK summer time (BST): the value is shifted +1 hour,
because `_extract_start_time_iso()` (`trainiq/connectors/strava_unofficial.py`)
prefers `start_date_local_raw` — an epoch of the **local** wall-clock time —
and converts it as if it were UTC
(`datetime.fromtimestamp(value, tz=timezone.utc)`). Winter (GMT) activities
are unaffected because local time and UTC coincide then. The BO has
confirmed, from a live account, that the payload already carries the correct
UTC value under a different field, `start_time`, which today's
`_START_FIELD_CANDIDATES` tuple never tries. This bug is why cross-provider
dedup (#37) misses roughly 79 Peloton/Strava duplicate pairs: those rides'
stored start times look 60 minutes apart when they're really the same
instant. The BO wants the connector fixed, the already-stored rows
corrected, and the dedup backfill re-run so the missed pairs link.

## Scope

- In `StravaUnofficialConnector._extract_start_time_iso()`
  (`trainiq/connectors/strava_unofficial.py`), make `start_time` (an ISO
  8601 string with a UTC offset, e.g. `2026-10-07T18:57:34+0000`) the first
  field tried, parsed with its own offset and converted to UTC — not
  assumed to already be UTC if the offset says otherwise.
- `start_date_local_raw` is demoted to a fallback, used only when
  `start_time` is absent. When it is used, it must never be treated as a
  UTC epoch (today's bug) — it needs an explicit local-timezone conversion.
  The BO's evidence doesn't pin down a specific IANA timezone name for
  "local"; if resolving `start_date_local_raw` correctly requires knowing
  the account's timezone and that isn't available to the connector, flag
  this for the Architect rather than guessing a fixed offset like
  `Europe/London` is universally correct for this field.
- `start_date_raw` and `start_date` remain fallbacks after those two, in
  their current order, unchanged.
- Correct the already-stored rows: re-run the existing
  `scripts/renormalize_strava_unofficial.py` (it re-derives
  `normalized_activities` from the untouched `raw_activities.payload_json`
  without re-fetching from Strava — same mechanism issue #36 used). No
  changes to `raw_activities` or to the script's existing behavior are in
  scope beyond whatever `normalize()` now produces.
- Re-run the existing `scripts/run_dedup_backfill.py` after the
  renormalization. It's already idempotent (only adds rows for pairs not
  already in `dedup_links`), so this is expected to add the ~79 previously-
  missed BST pairs without duplicating the 45 pairs already linked from
  winter data.
- Resume-cursor consistency: `StravaUnofficialConnector` doesn't override
  `extract_resume_cursor()` — it uses the base class default
  (`normalized.get("start_time")`), stored in
  `sync_checkpoints.last_cursor`. Once `normalize()` starts producing a
  different (correct) `start_time` for the same activities, any
  **previously stored** cursor value for this provider was computed under
  the old (incorrect) logic. The Architect must determine whether that
  stored cursor also needs correcting as part of this fix (e.g. alongside
  the renormalization step) so that the next live sync's "after cursor X"
  comparison doesn't skip or re-fetch activities at the boundary. This is
  explicitly in scope per the issue's acceptance criteria — don't leave it
  as an unstated side effect.
- Existing tests in `tests/test_strava_unofficial_connector.py` that
  construct fixtures using `start_date_local_raw` as a UTC epoch (e.g. the
  checkpoint-ordering tests around lines 349–364, and the `normalize()`
  tests around lines 531–588) encode today's buggy assumption and will need
  to be reviewed/updated so they reflect the corrected behavior — call this
  out to the Developer, don't treat it as a hidden regression if those
  tests need to change.

### Out of scope

- Any change to `StravaConnector` (the official REST connector) or
  `PelotonConnector` — this bug is specific to `StravaUnofficialConnector`'s
  field selection.
- Building or changing the dedup detection logic itself (#37) — this issue
  only supplies correct input data for it to work on; re-running the
  existing backfill script is the extent of this issue's interaction with
  dedup.
- Running any script against the BO's live production database as part of
  this pipeline delivery. Per this project's live-verification constraint,
  the CI pipeline has no BO account/database access — the fix must be
  verified via unit tests / fixture data. The BO running
  `renormalize_strava_unofficial.py` and `run_dedup_backfill.py` against
  their own database once this ships is an ops task, same as any other
  real-account backfill.
- Changing `_START_FIELD_CANDIDATES`'s fallback chain for `start_date_raw`
  or `start_date` — the issue's evidence only implicates `start_date_local_raw`
  and establishes `start_time` as the correct primary; the other two
  fallbacks are untouched.

## Acceptance criteria

1. `normalize()` uses `start_time` (ISO 8601 with offset) as the first
   choice when present, parses it with its stated offset, and stores the
   resulting UTC instant in `normalized_activities.start_time`.
2. `start_date_local_raw` is never converted with `tz=timezone.utc`
   (today's bug). When it's used as a fallback (no `start_time` present),
   it's converted via an explicit local-timezone rule rather than assumed
   to be UTC.
3. A summer (BST) fixture payload and a winter (GMT) fixture payload both
   normalize to the correct UTC instant — each matching the corresponding
   Peloton epoch value for the same real-world ride (per the BO's evidence
   table in the issue).
4. Running `scripts/renormalize_strava_unofficial.py` against a fixture
   database containing the old (incorrectly-shifted) stored rows corrects
   their `start_time` to the right UTC instant, without modifying
   `raw_activities`.
5. Running `scripts/run_dedup_backfill.py` after the renormalization, on a
   fixture reproducing the BO's reported data, links the previously-missed
   BST pairs without duplicating the already-linked winter pairs — the
   total linked count only grows, existing `dedup_links` rows for the 45
   winter pairs are untouched.
6. The resume-cursor handling for this provider (`sync_checkpoints.last_cursor`,
   via `extract_resume_cursor()`) is addressed so that an activity isn't
   skipped or incorrectly re-downloaded at the sync boundary as a result of
   `start_time` values changing for already-synced activities. How exactly
   this is done (e.g. recomputing the stored cursor alongside the
   renormalization step) is Architect/Developer judgment; that it's handled
   at all, and verified by a test, is the requirement.
7. All existing tests continue to pass, with any fixtures that encoded the
   old buggy UTC-from-local-epoch assumption updated to reflect correct
   behavior (see Scope).

## Open questions

None blocking. The BO's issue supplies definitive, live-verified evidence
for the core fix: the correct field (`start_time`), proof that
`start_date_local_raw` is local rather than UTC, and a real example showing
both the raw payload and the resulting stored (wrong) value. The one
genuine unknown — which IANA timezone to use when `start_date_local_raw`
must be used as a fallback — isn't blocking because it only affects a
fallback path; the Architect can design around it (e.g. using it only when
no better signal exists, or documenting the limitation) without needing the
BO's input first. The resume-cursor remediation approach (AC6) is a design
decision within the stated constraint, not an open business question.
