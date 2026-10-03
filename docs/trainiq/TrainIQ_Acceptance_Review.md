# TrainIQ — Phase 0B Development Acceptance Review

**Status:** Development phase complete, submitted for acceptance.
**Scope:** Foundation (Epic 0) through Epic 7 (Athlete Profile integration). Feature development suspended for this review, per explicit instruction — no new production code below except one correctness defect found and fixed during the review itself (Section 5).
**Method:** every claim in this document was checked against the actual repository — source, tests, schema, and committed documents — not against memory of prior conversation summaries.

---

## 1. Architecture Review

**Layering, verified by inspection, not assumed:**

| Layer | Location | Confirmed responsibility |
|---|---|---|
| Connectors | `trainiq/connectors/` | Auth, download, raw-field extraction, resume-cursor extraction, record-kind declaration. Zero SQL, zero confidence/taxonomy/load logic (verified by grep and by dedicated containment tests in each connector's own test file). |
| Orchestration | `trainiq/sync/engine.py` | Checkpointing, retry (ADR-037), lifecycle policy (ADR-038), routing by `record_kind`, persistence calls. Zero provider-specific field-name knowledge (verified: `grep` for `start_time`/`timestamp` returns nothing in this file). |
| Normalization | `trainiq/normalization/` | Taxonomy, confidence, training load, canonical record assembly. Zero database access (verified by grep for `sqlite3`/`.execute(`/`.commit(` — none found). |
| Athlete data | `trainiq/athlete/` | `AthleteProfile` (pure dataclass, zero methods — verified by AST inspection) and `store.py` (load/save, zero Sync Engine dependency — verified by AST import check). |
| Storage | `trainiq/storage/schema.py` | Schema definition and migration only. |

**Architectural invariants that are enforced, not just documented:** `tests/test_architecture_invariants.py` scans the actual repository source for any write path to `normalized_activities`/`weigh_ins` outside `sync/engine.py`, and any call to `build_canonical_record()` outside the same file. Both pass. Per the Chief Architect's own caveat (recorded in the Epic 6 self-review and reproduced here for completeness): these are static-analysis guardrails, not a proof of impossibility — a future abstraction layer could in principle reach the same tables through indirection these checks don't follow. That limit is accepted and documented, not silently assumed away.

**Finding:** none. The architecture as built matches the architecture as described in every prior review of it.

---

## 2. Backlog Review

Read `BACKLOG.md` in full, checked every entry's status against the actual codebase rather than trusting the label:

| ID | Status as labeled | Verified accurate? |
|---|---|---|
| DB-001 | Documented behavior (forward-only training load) | Yes — confirmed in `sync/engine.py`'s docstring and proven by `test_updating_stored_profile_affects_subsequent_syncs_only` |
| BL-001 | Open (external verification) | Yes — `stravalib`'s base URL is still hardcoded internally; nothing has changed |
| BL-002 | Open (narrowed scope) | Yes, and independently re-verified in this review: `build_canonical_record()` still indexes plain dict keys with no typed contract |
| BL-006 | Open (external verification) | Yes — no live Eufy account has been exercised |
| BL-008 | Open (external verification) | Yes — no live Peloton account has been exercised |
| BL-009 | Open (schema enhancement) | Yes — `weigh_ins` still has no `source_confidence` column, confirmed directly against the current schema in this review |
| BL-003 | Closed (Epic 6 slice 5) | Yes — `sync_connector()` does route to `normalized_activities`/`weigh_ins` today |
| BL-007 | Closed (ADR-038) | Yes — the lifecycle policy tests pass and the fix is in place |
| BL-005 | Closed (ADR-013 refinement) | Yes — `extract_resume_cursor()` exists and is used |
| BL-004 | Closed (ADR-037) | Yes — `retry_after_s` exists on `TransientError` and is honored |

**Finding:** the backlog accurately reflects the codebase. No entry needed correction. Two minor staleness issues were found and fixed during this review (Section 6) — a test-count reference in ADR-038 and in BL-007 that could be misread as a live figure rather than a historical snapshot; both now say so explicitly.

---

## 3. ADR Consistency Review

**ADR-037 (Provider Directed Retry Policy):** `TransientError.retry_after_s` exists, is optional, defaults to `None`, and is honored by `_with_retries()` in preference to the generic backoff formula when present. Verified live in `test_provider_directed_retry_honors_retry_after_s_not_generic_backoff` and the Strava-specific pass-through tests. No contradiction found with any later ADR.

**ADR-038 (Connector Lifecycle Policy):** `lifecycle_policy.evaluate()` is a pure function (verified: no I/O imports, no database access). The single-uniform-policy requirement from the second review round — no per-connector cadence override — is verified: `DEGRADED_BACKOFF_SCHEDULE_DAYS` and `DEGRADED_ESCALATION_THRESHOLD_DAYS` are module-level constants with exactly one value each, not a per-provider lookup table. No contradiction found with ADR-037 — the two operate on different timescales (intra-run vs. inter-run) exactly as ADR-038 §4 specifies, and no code path conflates them.

**Cross-ADR consistency:** ADR-013 (Connector Interface v2, `get_state()`/`active_strategy()`/etc.) is referenced throughout the implementation ADRs and the code, but — a genuine gap worth naming — **ADR-013 itself, along with every other Phase 0B design-phase ADR (006–036), is not a file in this repository.** Only ADR-037 and ADR-038 (both created during implementation) exist under `docs/adr/`. This doesn't create an inconsistency in what the code does, but it means this Acceptance Review — and any future one — cannot mechanically verify implementation ADRs against design-phase ADRs the way it can verify code against ADR-037/038, because the earlier ADRs exist only in conversation history. **Recommendation, not a defect:** commit the design-phase ADR register (even a compact single-file version covering ADR-006 through ADR-036) before the v1.0 Release Candidate milestone, so acceptance of that milestone doesn't depend on this conversation being available.

---

## 4. Test Quality Review

**218 tests, all passing, verified by direct execution — not reported from memory.**

- Zero `@pytest.mark.skip` or `@pytest.mark.xfail` anywhere in the suite — every test that exists is required to pass.
- Zero bare `except:` clauses in production code that could silently swallow a failure the tests wouldn't catch.
- Every test file has at least as many assertions as test functions (a coarse but real signal against empty/placeholder tests).
- Architectural-invariant tests (single-writer, anti-shape-sniffing, anti-shape-sniffing-for-routing, no-connector-knows-confidence) exist as their own category, distinct from behavioral tests — verified present and passing.
- Regression tests exist for every bug found during development (the malformed-record crash, the backoff off-by-one, the illegal `RecoveryRequired` transition, the state-restoration gap) — none of these were fixed without a corresponding permanent test.

**Finding, addressed during this review:** no test previously verified that a database frozen at the *oldest* schema version (v1, pre-dating ADR-038 and Epic 7) migrates correctly to the *current* version in a single call — every existing migration test covered one step in isolation (fresh install, v1→v2, v2→v3). Added `test_v1_database_migrates_directly_to_current_version_in_one_call`, which passes. This is now part of the permanent suite (218 total, up from 217).

---

## 5. Migration Integrity Review

- `CURRENT_SCHEMA_VERSION` (3) matches the highest key in `_MIGRATIONS` exactly, and the migration keys are contiguous (`1, 2, 3`, no gaps) — verified programmatically, not by inspection alone.
- Fresh install → v3 directly: confirmed.
- v1 (frozen, pre-ADR-038) → v3 in one call: confirmed by this review's new test, preserving pre-existing data (`credentials_metadata`) through both intermediate steps.
- v2 → v3 with pre-existing data preserved and a backup produced: confirmed by the existing `test_v2_to_v3_migration_preserves_existing_data`.
- The `athlete_profile` singleton constraint (`CHECK (id = 1)`) is enforced by SQLite itself, not application code — confirmed by two dedicated tests forcing constraint violations.

**Finding:** none. Migration integrity holds across every realistic upgrade path this project has, including the one that wasn't previously tested (now fixed, per Section 4).

---

## 6. Production Readiness Review

**Confirmed clean:**
- No hardcoded secrets in production code. (`CRED_PASSWORD = "password"` in the Eufy/Peloton connectors is a *credential-type label* — the key under which a real password is stored via `CredentialStore`/Keychain — not a literal password; checked the surrounding code to confirm this before listing it as clean rather than assuming.)
- No `print()` statements in production code paths (the one found is in `synthetic_dataset.py`'s `if __name__ == "__main__":` CLI entry point, appropriate there).
- No TODO/FIXME/XXX markers anywhere in `trainiq/`.
- Logging: split summary/diagnostic streams, weekly rotation, 8-week retention — matches the Milestone 4 §8 design, confirmed against the actual `logging_setup.py` code.

**Two honest gaps, both already tracked, re-confirmed rather than newly discovered:**
- **Feature 0.1's packaging DoD** ("signed, empty `.app` double-click-launches on a clean macOS machine") remains unverified — this sandbox cannot run PyInstaller's macOS packaging or code-signing steps. Unchanged since Epic 0.
- **BL-001/BL-006/BL-008** — no live provider account has been exercised for any of the three connectors. Every test in this project uses a fake/mocked HTTP or SDK client. This is the single largest gap between "the code is correct against its own tests" and "the code works against reality," and it is exactly what the next phase (live-provider verification) exists to close.

**New observation from this review, not previously recorded:** `pyproject.toml` pins dependencies with lower bounds only (`>=`), no upper bounds and no lock file. This is a reasonable default for a young single-developer project, but worth naming as a real production-readiness consideration before a v1.0 Release Candidate: an unpinned `pip install` between now and then could pull a breaking newer `stravalib`/`keyring`/`requests` release with no warning. Not a defect — no evidence of an actual break exists — but worth deciding deliberately (a lock file, or explicit upper bounds) rather than leaving to chance, especially given BL-001 already tracks a *known* future breaking change in one of these dependencies.

---

## Defects found and fixed during this review

**One.** `ADR-038`'s and `BACKLOG.md`'s test-count references ("142 tests passing project-wide") were historical snapshots from when Epic 3 closed, stated without qualification — readable as a current claim rather than a point-in-time fact. Both corrected to say explicitly that the number is a historical snapshot. This is a documentation-accuracy fix, not a code defect, and required no production code change.

No correctness defect was found in production code during this review. The one prior correctness defect this project has recorded (the malformed-record crash) was found and fixed during the Epic 6 self-review, not this one — re-verified here as still fixed and still covered by its regression test.

---

## Acceptance Recommendation

**Accept.** Architecture, backlog, ADRs, tests, and migrations are internally consistent and match what they claim about themselves — verified in this review by direct inspection and execution, not by re-reading prior summaries. Production readiness has two known, already-tracked, correctly-unresolved gaps (live-provider verification, macOS packaging verification) that are explicitly the subject of the next phase, not defects in this one. One documentation-accuracy issue was found and corrected; no code defect was found.

**Recommended sequence following acceptance, per instruction:**
1. Execute the live-provider verification plan (BL-001, BL-006, BL-008) — the only work that can close the actual gap between "tests pass against mocks" and "this works."
2. Only then schedule BL-002 (typed normalization contract) and BL-009 (`weigh_ins.source_confidence` migration) — both are internal refinements that don't need to precede real-world verification and shouldn't be allowed to distract from it.
3. v1.0 Release Candidate milestone, once 1 and 2 are both closed.

**218 tests passing** at the time of this review's completion.
