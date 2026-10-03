# TrainIQ — Epic 6 Discovery Report
## Normalization Engine: Current State, Boundaries, and Open Questions

**Purpose:** per the Chief Architect's explicit request, this inspects the actual current code — not the design documents from memory — to establish exactly where `normalize()` ends, where the Normalization Engine begins, and what (if anything) needs deciding before any implementation code is written. No code has been written against this report.

---

## 1. Connector boundary audit — already correctly thin (no connector changes needed)

Inspected all three connectors' `normalize()` methods directly:

| Connector | Output shape | Fields |
|---|---|---|
| Strava | `RawActivity`-like | `provider, external_id, start_time, duration_s, discipline_raw, avg_hr, max_hr, avg_power, max_power, distance_m, calories(always None), synced_at` |
| Peloton | `RawActivity`-like | Same shape as Strava, plus a semantic-drift log (not a mapping) for unrecognized `fitness_discipline` values |
| Eufy | `WeighIn`-like | `provider, external_id, timestamp, weight_kg, body_fat_pct, muscle_mass_pct` |

**Finding:** every connector already stops at raw field extraction. None of them compute a canonical `discipline`, a training load, or a confidence score — they only preserve `discipline_raw` (or nothing, for Eufy, which has no discipline concept) and extract provider-native values. **This confirms the Chief Architect's constraint is already satisfied today — no connector code needs to change for Epic 6 to begin.** The boundary described in the design phase already exists in practice, not just in intent.

## 2. Schema audit — two tables, two shapes, confirming BL-003's prediction precisely

`normalized_activities` (built in schema v1) expects: `provider, external_id, start_time, duration_s, discipline, distance_m, avg_hr, max_hr, avg_power, max_power, calories, training_load, training_load_method, source_confidence`. This is the canonical target for `RawActivity`-shaped connectors (Strava, Peloton) — note `discipline` (canonical), not `discipline_raw`.

`weigh_ins` (also schema v1) expects: `provider, external_id, timestamp, weight_kg, body_fat_pct, muscle_mass_pct`. This is the canonical target for Eufy's `WeighIn`-shaped output — and notably, it's already nearly identical to what `EufyConnector.normalize()` produces today, since weigh-in data doesn't need a taxonomy mapping or a load computation the way activities do.

**Finding: Epic 6 is not one pipeline, it's two**, split by record shape, matching two tables that have existed in the schema since Epic 0 but have never been written to. This wasn't obvious from the roadmap's original framing (which described Epic 6 somewhat activity-centric) and is worth stating explicitly now that Eufy exists and confirms the split is real, not hypothetical.

## 3. Exactly where `normalize()` ends and Epic 6 begins

Checked directly in `trainiq/sync/engine.py`: `sync_connector()` calls `connector.normalize(raw)` at line 332, then uses the result for exactly two things — `external_id` extraction (line 333) and `extract_resume_cursor()` (line 344). **The normalized dict is never persisted anywhere.** `normalized_activities` and `weigh_ins` are both empty in every environment this project has run in. This is BL-003 confirmed with line numbers, not just a design-time prediction: Epic 6 will require editing `sync_connector()`'s body to route `normalize()`'s output through the new engine and write the result to the correct table — a real edit to existing orchestration code, not a purely additive change.

## 4. Open questions requiring a decision before implementation

### Q1 (blocking, most significant): training load computation has no data to compute from yet

TRIMP requires `sex`, `resting_hr`, `max_hr` (Milestone B §2); opportunistic TSS additionally requires `FTP`. **Checked directly: no `athlete_profile` table, or anything resembling one, exists anywhere in the schema.** This data was explicitly scoped to Epic 7 (Athlete Knowledge Model), Feature 7.1 — which has not been built. Epic 6 cannot compute a real TRIMP or TSS value today, for any activity, from any connector, because the physiological inputs the formulas require don't exist in persisted storage.

**Three ways to resolve this, not decided here:**
- **(a)** Epic 6 builds a minimal `athlete_profile` table itself as a prerequisite. Rejected as a recommendation (not a decision) — this reaches into Epic 7's explicitly scoped territory and risks exactly the kind of scope creep the working rules just reaffirmed.
- **(b), recommended:** Epic 6 implements the full TRIMP/TSS computation logic, but with `training_load` correctly resolving to `null`/`training_load_method="null"` for every activity until Epic 7 populates `athlete_profile` — this is not a workaround, it's ADR-016 applied exactly as designed: the data genuinely isn't known yet, so it's marked Unknown, never fabricated or estimated. Epic 6 ships fully functional and honestly incomplete in one specific respect, and Epic 7 turns the lights on for load computation without requiring any Epic 6 code to change.
- **(c)** Reorder the roadmap — build Epic 7's `athlete_profile` persistence first, or in parallel. Not recommended given the working rule "do not begin later epics during Epic 6," but named for completeness.

**Recommendation: (b).** It requires no scope violation, no reordering, and is a direct, mechanical application of a principle already established five months ago rather than a new judgment call.

### Q2: how does the Sync Engine route a normalized record to the correct table?

Given Section 2's two-shape finding, something needs to decide whether a given `normalize()` output belongs in `normalized_activities` or `weigh_ins`. Two options:
- **Implicit shape-sniffing** (check whether `start_time`/`duration_s` or `timestamp`/`weight_kg` keys are present) — rejected as a recommendation: this is exactly the anti-pattern BL-005 already taught this project to avoid (inferring meaning from field names rather than an explicit declaration), just at a different layer.
- **Explicit, connector-declared record kind** — a small addition to the `Connector` interface (e.g. a `record_kind` property returning `ACTIVITY` or `WEIGH_IN`), analogous in spirit to `capability_tier` and `list_acquisition_strategies()`. Recommended, but flagged here because it touches the shared `Connector` base class — per the working rules, this is exactly the kind of Foundation-interface question that should be confirmed rather than assumed, even though it looks more like the "implementation refinement" category (extending an existing abstraction the same way `extract_resume_cursor()` did) than a new architectural capability requiring an ADR.

**Recommendation:** treat this the same way `extract_resume_cursor()` was treated (BL-005) — an interface refinement, not an ADR — but confirming that classification explicitly before implementing, since the Chief Architect's working rules distinguish the two categories deliberately.

### Q3 (not blocking, stated for completeness): confidence computation has no existing implementation to build on

Unlike Q1, this isn't blocked on anything — `source_confidence` can be computed directly from what's already present in a given raw record (how many of the load-relevant fields are populated), which Epic 6 already receives. Named here only so it's clear this is new logic Epic 6 owns outright, not something inherited or blocked.

---

## 5. Proposed minimal first slice (not started — awaiting confirmation on Q1/Q2)

If Q1→(b) and Q2→(explicit record kind) are confirmed, the smallest coherent first slice would be:
1. Add `record_kind` to the `Connector` interface (default `ACTIVITY`, `EufyConnector` overrides to `WEIGH_IN`) — the Q2 resolution.
2. Build the canonical discipline taxonomy + per-provider mapping table (Milestone A §5), consuming `discipline_raw`.
3. Build `source_confidence` computation from raw field completeness.
4. Build the TRIMP/TSS computation path, resolving to `null`/logged-reason per ADR-016 given no `athlete_profile` exists yet (the Q1 resolution).
5. Edit `sync_connector()` to route normalized output through the above and persist to `normalized_activities` or `weigh_ins` per `record_kind`.

This is a proposal for ordering, not a commitment — offered so the Chief Architect can confirm or redirect before any of it is built, per the working rules.
