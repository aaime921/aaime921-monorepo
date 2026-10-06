# Requirements: Fix Peloton Resume Cursor int/str TypeError

**Issue:** #33
**Regression of:** `aaime921/trainiq#12` (closed; fix PRs `trainiq` #17/#18/#19 never merged into this monorepo)

## Summary

Peloton sync crashes on every run after the first with `TypeError: '>' not supported between instances of 'int' and 'str'`. The BO's live diagnostic log shows the in-memory resume cursor built during a sync (`candidate_cursor`, an `int`) being compared against the cursor reloaded from the persisted checkpoint (`resume_cursor`, a `str`). The connector transitions to `Degraded` and is skipped on subsequent runs, so Peloton data stops syncing entirely until this is fixed. This same defect was previously identified and fixed in `aaime921/trainiq#12`, but that fix lived in unmerged PRs and never reached this monorepo, so the regression is really "the original fix is still missing here," not a new bug.

## Scope

- The Peloton connector's resume cursor must use one consistent type across extraction, persistence, and reload, so the comparison in the Synchronization Engine never mixes `int` and `str`.
- Must handle cursors already persisted as `str` in an existing checkpoints table (like the BO's current database) without requiring a manual database fix — the fix must work against data as it exists today, not assume a clean slate.
- The temporary `DEBUG CURSORS` diagnostic log line (logged at ERROR level for every single record) must be removed or lowered to DEBUG level — it was added only to confirm the type mismatch and is not meant to ship at ERROR severity.
- Must include a test covering two consecutive syncs with a persisted cursor reload in between, for Peloton specifically — this is the path that was broken and the log evidence shows failing.
- Document, for the BO, how the connector recovers from its current `Degraded` state locally once the fix lands (whether it self-recovers on the next eligible sync attempt, or needs a manual state reset).

### Out of scope

- Any fix applied only to the standalone `aaime921/trainiq` repo or its unmerged PRs (#17/#18/#19) — those never reached the monorepo and are not the delivery target; the fix must land in `aaime921-monorepo`.
- Backfilling or repairing any historical Peloton data in the BO's production database — that is an ops task, not a pipeline deliverable.
- Changes to cursor handling for any connector other than Peloton. If the Architect determines other connectors share this same defect pattern, that is a separate issue, not an expansion of this one.
- Live-account verification as part of the pipeline deliverable — the CI pipeline has no access to the BO's real Peloton account; this must be verified via unit tests / fixture data reproducing the logged shapes. The BO may additionally verify live themselves, outside the pipeline.

## Acceptance criteria

1. `PelotonConnector.extract_resume_cursor()`, the checkpoint persistence path, and the checkpoint reload path (`SynchronizationEngine.get_checkpoint()` / `_set_checkpoint()`) all agree on a single cursor type for Peloton, so `SynchronizationEngine`'s `candidate_cursor > resume_cursor` comparison never compares an `int` to a `str`.
2. Given a `sync_checkpoints` row for Peloton with `last_cursor` stored as a `str` (reproducing the BO's current database state, e.g. `'1790883770'`), a sync run does not raise `TypeError` and completes successfully.
3. Test: two consecutive Peloton syncs, with a checkpoint persisted and reloaded in between (reproducing the reload path from the log), both complete without error. The exact logged values may be used as fixture data: candidate `1791134056`, persisted resume `'1790883770'`.
4. The `DEBUG CURSORS` log line at `sync/engine.py:503` is removed, or its level is lowered from `ERROR` to `DEBUG`, so it no longer logs at ERROR severity on every record.
5. The handoff comment (or an accompanying note in the requirements/design doc) documents for the BO how the currently-`Degraded` local Peloton connector recovers after this fix ships: either it becomes eligible for sync automatically on its normal retry schedule, or it requires a manual state reset — whichever is actually true per the connector/lifecycle design.
6. All existing Peloton connector and sync engine unit tests continue to pass.

## Open questions

None blocking. The BO's live diagnostic log already provides definitive evidence of both the mismatched types and the exact values involved (per this project's evidence-based principle), and the original, correct fix shape was already worked out in `aaime921/trainiq#12`'s PRs — the Architect can treat "pick one consistent type end-to-end" as settled intent and decide the specific type (likely `str`, since checkpoints persist as `str` and that's what existing persisted data already is) and the exact code path, including whether to re-derive the approach from the unmerged PRs (#17/#18/#19) or design fresh against current `main`, since those PRs were never merged and may be stale against this monorepo's current code.
