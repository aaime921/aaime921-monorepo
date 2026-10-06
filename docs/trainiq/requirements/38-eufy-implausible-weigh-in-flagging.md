# Requirements: Flag Implausible Eufy Weigh-Ins

**Issue:** #38
**Related:** #1 (deci-kg unit conversion, already fixed; this issue's evidence table assumes that fix is in place)

## Summary

7 of the BO's 568 stored Eufy weigh-ins are physically implausible (18.9–35.8 kg, against every other reading's 80–88.3 kg) and are currently stored and treated as normal readings, distorting weight and body-composition trends. All 568 records share the BO's own `customer_id`/`user_id`, so these aren't a second profile to filter out — most likely another person, a pet, or an object briefly on the scale. `EufyConnector.normalize()` and the normalization pipeline's `_build_weigh_in_record()` currently pass every record through with no plausibility check at all. The BO wants implausible readings flagged and excluded from analytics (never deleted), the 7 known bad records corrected retroactively, and a way to undo a flag that turns out to be wrong.

## Scope

- A plausibility rule for weigh-in records, applied wherever weigh-in records are built for persistence (today: `_build_weigh_in_record()` in `trainiq/normalization/engine.py`, fed by `EufyConnector.normalize()` — the rule itself is about weight/body-composition values, not Eufy-specific, so it should apply to any provider using the same WeighIn-shaped pipeline, not be hardcoded into the Eufy connector).
- The rule must be relative to the individual athlete, not a fixed absolute kg range — e.g. deviation from the athlete's own rolling median weight, and/or an implausible `body_fat_pct` (0, or below a stated physiological floor). The BO's evidence shows both symptoms co-occurring on every bad record (`body_fat_pct` of 0.0 or 5.0 alongside the low weight).
- Persisting flagged status without deleting or altering the underlying raw/weight values — raw data stays intact and auditable, consistent with how `raw_activities` already preserves unmodified payloads.
- Excluding flagged readings from analytics/trend computation (wherever `weigh_ins` is currently read for that purpose).
- A one-time pass that applies the new rule to the BO's already-stored weigh-ins, so the 7 known bad records (listed below) end up flagged without the BO needing to re-sync or manually edit rows.
- The sync run summary reporting a flagged-reading count for the run (e.g., `eufy: … flagged 1 implausible`), consistent with the existing per-provider summary counts (`records_inserted`/`records_updated`/`records_malformed`/`records_skipped` in `ConnectorSyncResult`).
- A documented way for the BO to un-flag a specific reading that was actually valid (a false positive), without deleting/re-syncing the row.
- Tests: one normal reading (not flagged), one obvious outlier matching the BO's evidence (flagged), and one borderline reading near the chosen threshold (behaves deterministically per the documented rule).

### Out of scope

- Re-deriving or changing the deci-kg unit conversion — already fixed and verified in #1; this issue's evidence values are already in kg.
- Deleting any existing or future raw weigh-in record. The acceptance criteria explicitly require flagging, not deletion.
- Live-account verification as part of the pipeline deliverable — the CI pipeline has no access to the BO's real Eufy account. The BO's evidence table in the issue is already live-verified ground truth and is sufficient fixture data; everything here must be testable against it without a live sync.
- Any schema migration unrelated to this issue's own flagging need. A migration to persist flag state on `weigh_ins` is in scope *for this issue* (the acceptance criteria require persisting and later clearing a flag — there is nowhere to store that today), the same way Epic 7 added `athlete_profile` and ADR-038 extended `connector_state` within normal issue scope; it should not be used as an opportunity to also land the unrelated, already-tracked `BL-009`/`source_confidence` migration from issue #24.
- Designing the exact threshold percentage, rolling-window size, or body-fat floor value — see Open Questions.
- Designing the exact un-flag interface (CLI flag, script, config edit, etc.) — see Open Questions.

## Acceptance criteria

1. An explicit, documented plausibility rule exists for weigh-in records: a reading is flagged when it falls outside ±X% of the athlete's own rolling median weight (computed from the athlete's prior unflagged readings), and/or `body_fat_pct` is 0 or below a documented physiological floor. The rule must not rely solely on a fixed absolute weight range, since athletes' baseline weights differ.
2. A flagged weigh-in's raw values (`weight_kg`, `body_fat_pct`, `muscle_mass_pct`, `external_id`, `timestamp`) are persisted unchanged; the record additionally carries persisted flag state (flagged yes/no, at minimum).
3. Any analytics/trend computation that reads `weigh_ins` excludes rows currently flagged.
4. Applying the documented rule to the BO's existing stored data flags all 7 records from the issue's evidence table, without flagging any of the normal 80–88.3 kg readings:

   | timestamp (UTC) | weight_kg | body_fat_pct |
   |---|---|---|
   | 2026-09-05 19:30 | 35.8 | 0.0 |
   | 2026-07-06 18:43 | 20.7 | 5.0 |
   | 2026-07-05 10:53 | 20.55 | 0.0 |
   | 2025-09-14 18:53 | 19.15 | 5.0 |
   | 2025-09-14 18:52 | 19.15 | 0.0 |
   | (+2 more, each < 40 kg, per the BO's DB) | | |

   This must be verifiable with fixture data reproducing these exact values alongside normal 80–88.3 kg readings — not a live re-sync — and must apply to rows already sitting in the BO's local database today, not only to newly-synced rows going forward.
5. The Eufy connector's sync run summary reports a count of readings flagged during that run (e.g. `eufy: … flagged 1 implausible`), following the existing summary-count pattern already used for inserted/updated/malformed/skipped records.
6. The BO has a documented, working way to clear a flag on a specific reading they've confirmed was actually valid, without deleting or re-syncing that row, and without losing the fact that it was once flagged (auditable, not a silent revert).
7. Tests cover at minimum: (a) a normal reading within the athlete's established range is not flagged; (b) an obvious outlier reproducing the BO's evidence (e.g. 20.7 kg against an 80+ kg baseline, `body_fat_pct` 5.0) is flagged; (c) a reading near the chosen threshold is handled deterministically per the documented rule, whichever way that rule resolves it.
8. All existing Eufy connector, normalization, and sync engine tests continue to pass.

## Open questions

Not blocking — the issue explicitly delegates the exact numbers to design, it only constrains the *shape* of the rule (relative, not a fixed absolute range) and requires it be explicit and documented. Architect should settle:

- The specific threshold (±X%) and the rolling-median window (how many/which prior readings feed the median), and the exact `body_fat_pct` floor value.
- Where flag state is persisted — this needs a schema change to `weigh_ins` (no existing column covers it); exact column(s)/migration numbering are the Architect's call, same precedent as the `connector_state` (ADR-038) and `athlete_profile` (Epic 7) migrations.
- The exact un-flag mechanism's interface (script, CLI, config-driven override list, etc.) — the BO only needs the capability, not a specific UX.
- Whether the one-time backfill pass (AC 4) is a standalone script/migration step the BO runs once, or runs automatically the next time the app opens the BO's database — either satisfies the acceptance criterion as long as it's testable against fixture data reproducing the evidence table.
