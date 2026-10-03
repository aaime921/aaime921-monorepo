# Requirements: Add source_confidence Column to weigh_ins Table

**Issue:** #24  
**Test Issue for BA Pilot (Phase 2)**

## Summary

The `normalized_activities` table has a `source_confidence` column (populated by `compute_source_confidence()` in Epic 6), but `weigh_ins` table does not. Weigh-in data is inherently sparse (body-fat and muscle-mass measurements depend on scale capabilities) and model-dependent. Add `source_confidence REAL` column to `weigh_ins` and wire `_build_weigh_in_record()` to compute and persist it. This brings weigh-in data quality tracking to parity with activity data.

Tracked in BACKLOG.md as BL-009; deferred from Epic 6 to avoid scope creep on that slice.

## Scope

- **Schema migration:** v1 → v2, add `source_confidence REAL` (nullable, like normalized_activities)
- **Connector integration:** `_build_weigh_in_record()` calls `compute_source_confidence(WEIGH_IN)` and includes result in persisted record
- **Testing:** Unit tests verify source_confidence is computed and retrieved correctly
- **Backfill handling:** Existing rows in v1 schema either backfilled or gracefully handled (e.g., NULL until next sync)

### Out of scope

- Changes to `compute_source_confidence()` itself (already supports WEIGH_IN record kind per Epic 6)
- New fields beyond source_confidence (e.g., confidence scores for individual fields)
- Data quality gates (e.g., "filter out low-confidence data") — that's a consumer decision, not this migration
- Rollback/downgrade path (v2 → v1 not required)

## Acceptance criteria

1. **Schema migration exists:** New migration file `trainiq/storage/migrations/v2_add_weigh_ins_source_confidence.sql` creates column
2. **Column definition:** `source_confidence REAL NULL` (matches normalized_activities pattern)
3. **_build_weigh_in_record() updated:** Calls `compute_source_confidence(record_kind=WEIGH_IN, record=...)` and includes result in dict/object before persistence
4. **Existing rows handled:** 
   - Option A: Backfill with NULL (simplest, data exists until next sync)
   - Option B: Backfill with computed confidence (requires reading raw scale_data again; more complex)
   - Choose one and document in code comment why
5. **Persistence verified:** Unit tests confirm source_confidence is written to DB and retrieved intact
6. **Type consistency:** If compute_source_confidence() returns float/int, store as REAL (no type mismatch)
7. **Existing tests pass:** All existing weigh-in tests continue to pass; new tests added for source_confidence persistence

## Open questions

**One design decision (for Architect):** Backfill strategy for existing weigh_ins rows post-migration.

- **Option A (simpler):** Set existing rows' source_confidence = NULL; they'll get computed values on next connector sync
  - Pro: No complex backfill logic
  - Con: Existing data lacks confidence until next sync
  
- **Option B (more complete):** Backfill by re-running compute_source_confidence() on each existing row's raw data (if available)
  - Pro: Instant confidence for all rows
  - Con: Requires raw scale_data to still be accessible; more complex migration

**To unblock Architect:** BO should decide which approach fits the use case. For now, assume Option A (NULL backfill, compute on next sync) unless the BO prefers otherwise.
