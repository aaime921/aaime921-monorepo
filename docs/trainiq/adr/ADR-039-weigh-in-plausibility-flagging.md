# ADR-039 — Weigh-In Plausibility Flagging

**Status:** Implemented.
**Raised during:** Issue #38 — 7 of 568 Eufy weigh-ins (18.9-35.8 kg, against the BO's real 80-88.3 kg baseline) were stored and treated as normal readings, distorting weight and body-composition trends. All 568 records share the BO's own `customer_id`/`user_id`, so these aren't a second profile to filter out — most likely another person (a child), a pet, or an object briefly left on the scale.
**Decision owner:** Architect, per the requirements doc's explicit delegation of exact numbers to design (`docs/trainiq/requirements/38-eufy-implausible-weigh-in-flagging.md`, "Open questions").

## Context

`EufyConnector.normalize()` and `_build_weigh_in_record()` passed every record through with no plausibility check at all. The BO's evidence shows every bad record carrying both an implausible weight AND an implausible `body_fat_pct` (0.0 or 5.0), consistent with the scale not recognizing a valid adult measurement.

A fixed absolute kg range (e.g. "reject anything under 40 kg") was explicitly rejected by the requirements — athletes' baseline weights differ, so the rule must be relative to the individual athlete's own history.

## Decision

A reading is flagged as implausible when either of two independent axes trips:

1. **Body-fat floor:** `body_fat_pct <= 3.0`. This is an essential-fat physiological floor, not sex-adjusted. `AthleteProfile.sex` exists and a sex-aware floor (e.g. ~10% for female athletes) would catch more bad readings on this axis alone, but risks false positives for a genuinely very lean athlete. Kept conservative because axis 2 below already catches all 7 of the BO's known bad records independently — this floor is a second signal, not the primary one.

2. **Weight-deviation from rolling median:** given the athlete's prior unflagged (or BO-confirmed-valid) readings, strictly before this one's timestamp, limited to a rolling window of **5**, compute `baseline = median(recent_weights_kg)`. If there are at least **3** prior readings and `abs(weight_kg - baseline) / baseline * 100 > 25.0`, the reading is flagged. Below 3 prior readings, this axis is skipped entirely rather than assumed-plausible against a fabricated baseline (no default weight is invented).

Boundary is strict `>` — a reading exactly at the 25.0% threshold is **not** flagged; this is deterministic and tested explicitly.

Both axes are evaluated by one pure function, `evaluate_weigh_in_plausibility()` (`trainiq/normalization/plausibility.py`), independent of provider — any current or future connector with `record_kind == RecordKind.WEIGH_IN` gets this for free through `_build_weigh_in_record()`, with no Eufy-specific code.

### Why these specific numbers

±25% deviation, a window of 5, a minimum history of 3, and a body-fat floor of 3.0% are a judgment call, not derived from a larger dataset — issue #38 only gives us 568 points from one athlete. They're chosen to comfortably separate the BO's ~80-88 kg baseline from the ~19-36 kg outliers (a 75%+ gap) while staying loose enough not to flag ordinary week-to-week weight fluctuation or a genuine multi-month trend (the median adapts as real change accumulates one reading at a time). These are calibratable constants, not load-bearing architecture — a future issue can retune them with more data without touching the rule's shape.

### Persistence: flag, never delete

A flagged reading's raw values (`weight_kg`, `body_fat_pct`, `muscle_mass_pct`, `external_id`, `timestamp`) are persisted exactly as received — raw data stays intact and auditable, consistent with how `raw_activities` already preserves unmodified payloads. Schema v4 adds four columns to `weigh_ins`:

| Column | Owner | Written by |
|---|---|---|
| `is_flagged_implausible` | the rule | sync + backfill; **overwritten on every re-evaluation** (e.g. a full Eufy resync, BL-006), since the rolling median can shift as history grows |
| `plausibility_reason` | the rule | same as above |
| `bo_confirmed_valid` | the BO | only `scripts/confirm_weigh_in.py` |
| `bo_confirmed_at` | the BO | only `scripts/confirm_weigh_in.py` |

Analytics excludes a row only when it's flagged **and** not BO-confirmed:

```sql
WHERE is_flagged_implausible = 0 OR bo_confirmed_valid = 1
```

A confirmed reading therefore reads as "flagged, but overridden" rather than silently un-flagged — the BO can see exactly what they overrode (AC6's auditability requirement). `_upsert_weigh_in()`'s INSERT/UPDATE column lists deliberately exclude `bo_confirmed_valid`/`bo_confirmed_at` — a resync can never revert a confirmation.

### Backfill (AC4)

The one-time pass (`trainiq/storage/backfill.py::backfill_weigh_in_plausibility()`) reuses the exact same pure function as live sync, run once over all existing `weigh_ins` rows in ascending timestamp order, triggered automatically on the v3->v4 migration — no separate script the BO has to remember to run.

### Order-independence on resync

Eufy's `supports_incremental_sync = False` means every sync reprocesses full history (BL-006), and `download()`'s return order isn't guaranteed chronological. `SynchronizationEngine.sync_connector()` sorts WEIGH_IN-kind raw records by normalized timestamp ascending before evaluating plausibility, so a sync's flagging results depend only on the data, never on download order — matching the backfill's semantics exactly.

### Un-flag mechanism (AC6)

`scripts/confirm_weigh_in.py --provider <p> --external-id <id>` looks up the row by its existing `(provider, external_id)` unique key, errors without writing if the row doesn't exist or isn't currently flagged, and otherwise sets `bo_confirmed_valid = 1` / `bo_confirmed_at = now`. No un-confirm path exists — reversing a confirmation isn't in scope for issue #38; it's a one-line follow-up if ever needed.

## Consequences

- The rolling baseline is shared across providers (not filtered by provider) — the median is of the athlete's weight, not of one device's readings. If a second weigh-in source is ever added, a systematic calibration difference between two scales (not just noise) could make one provider's otherwise-normal readings look like outliers relative to a blended median. Not a concern today (only Eufy exists), flagged here for whoever adds the next one.
- `weigh_ins` has no `source_confidence` column (BL-009, still open and unrelated to this migration — see `BACKLOG.md`).
- Threshold values are revisit-able with more data; this ADR is the record of what was chosen and why, so a future change is a deliberate revision, not an unexplained drift.

## Correction (Issue #42)

After this rule went live on the BO's database, the first sync flagged 130 of 568 stored Eufy weigh-ins as implausible — but only the original 7 sub-40 kg readings were genuinely bad. The other 123 were valid 81–88 kg readings that Eufy recorded with `body_fat_pct = 0.0` because the scale didn't get an impedance reading that time (e.g. weighed with socks on), not because the athlete's body fat is actually ~0%. Two independent defects caused this, fixed at the two layers where they were introduced:

1. **Missing-vs-zero, at the connector layer.** `EufyConnector.normalize()` had no way to distinguish "measured as 0" from "not measured" — it passed Eufy's `body_fat_pct`/`muscle_mass_pct` sentinel value of `0.0` straight through as a literal reading. BO-confirmed against live production data: a literal `0` on either field means "not measured," the same sentinel pattern Issue #1 already found and fixed for `weight`'s deci-kilogram scaling. `normalize()` now maps a literal `0` on `body_fat_pct`/`muscle_mass_pct` to `None` before either value reaches persistence or the plausibility rule.

2. **One combined verdict, at the rule layer.** Even with defect 1 fixed, `evaluate_weigh_in_plausibility()`'s original shape returned a single `is_plausible`/`reason` verdict that conflated two independent questions — "is this body-fat reading physiologically plausible?" and "is this weight reading plausible?" — so tripping the body-fat floor suppressed the weight-deviation axis's (already-correct) verdict on the weight itself. A future non-zero-but-below-floor body-fat reading (e.g. `1.5`) would still have wrongly excluded a perfectly good weight reading even with defect 1 fixed. Both fixes were required.

### What changed

- `evaluate_weigh_in_plausibility()` now returns `WeighInPlausibility` with two fully independent verdicts — `is_weight_plausible`/`weight_reason` and `is_body_fat_plausible`/`body_fat_reason` — instead of one combined `is_plausible`/`reason`. One axis can never flag the other.
- The body-fat floor check gained an explicit `> 0` guard (`body_fat_pct > 0 and body_fat_pct <= body_fat_floor_pct`), so it is evaluated only against present, non-null, **non-zero** values. This defends the rule itself even if some future connector forgets to normalize its own zero-sentinel the way Eufy now does — the connector-level normalization and this guard are deliberately redundant, not one-or-the-other.
- Schema v5 splits the single `is_flagged_implausible`/`plausibility_reason` pair into two independent pairs, one per axis:

| Column | Owner | Written by |
|---|---|---|
| `is_weight_flagged_implausible` (renamed from `is_flagged_implausible`) | the rule's weight axis | sync + backfill |
| `weight_plausibility_reason` (renamed from `plausibility_reason`) | the rule's weight axis | sync + backfill |
| `is_body_fat_flagged_implausible` (new) | the rule's body-fat axis | sync + backfill |
| `body_fat_plausibility_reason` (new) | the rule's body-fat axis | sync + backfill |
| `bo_confirmed_valid` / `bo_confirmed_at` | the BO | only `scripts/confirm_weigh_in.py` — unchanged, stays attached to the weight axis only, since that's the thing a BO override actually restores to analytics |

  A rename, not an additive-only change like v2/v4: the old columns' *meaning* changed (a single combined verdict → one specific axis), and keeping the old names while quietly narrowing what they mean would risk later code silently reading "is this weight bad" as "was anything about this record flagged" — exactly this bug, relocated. `ALTER TABLE ... RENAME COLUMN` is a single, lossless statement (SQLite ≥ 3.25), no new migration machinery required.

- A one-time corrective pass, `trainiq/storage/backfill.py::decouple_weigh_in_plausibility()`, runs automatically on the v4→v5 transition (same precedent as v3→v4's `backfill_weigh_in_plausibility()`): it first normalizes any already-stored `body_fat_pct`/`muscle_mass_pct` zeros to `NULL`, then re-evaluates every row in ascending timestamp order against the corrected rule. A row whose body-fat axis alone trips still contributes its weight to later rows' rolling baselines — the concrete mechanism that was silently suppressing all 123 readings (and anyone else's baseline built from them) before this fix.
- `sync/engine.py`'s `_upsert_weigh_in()` column lists, `_recent_weights_before()`'s predicate, and `scripts/confirm_weigh_in.py` were all updated for the renamed/added columns. `records_flagged_implausible` (the sync summary counter) now counts the weight axis only — a body-fat-only flag is real, persisted, queryable information, but isn't an analytics-exclusion event, which is what that counter has always meant.

### No confirm/override path for the body-fat axis

`confirm_weigh_in.py` stays scoped to the weight axis. A body-fat flag is visible in the database but doesn't exclude anything from analytics under this design, so there's nothing today for a BO override to *restore*. A future need to silence a body-fat false positive (e.g. a genuinely very lean athlete repeatedly tripping the floor) would be a new, separate capability, not a gap in this correction's scope.
