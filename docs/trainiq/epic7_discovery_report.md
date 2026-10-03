# TrainIQ — Epic 7 Discovery Report
## Athlete Knowledge Model: Current State, Roadmap Reality Check, and Design Questions

**Purpose:** per the Chief Architect's explicit request, this inspects the actual current code and backlog — not the roadmap or memory — before any Epic 7 implementation begins. No production code has been written against this report.

---

## 1. Current production state

**`AthleteProfile` (`trainiq/athlete/profile.py`), inspected directly:** exactly five fields, all optional — `sex`, `date_of_birth`, `resting_hr`, `max_hr`, `ftp_watts`. No hidden attributes, no methods, no persistence. Confirmed clean per the Chief Architect's explicit "keep it a pure model" instruction after Epic 6.

**Every consumption point, found by grepping the entire `trainiq/` tree — three, no more:**
- `trainiq/normalization/engine.py` — `build_canonical_record()`'s signature accepts `Optional[AthleteProfile]`.
- `trainiq/normalization/load.py` — `compute_training_load()` and `_compute_trimp()` read `profile.sex`, `profile.resting_hr`, `profile.max_hr`, `profile.ftp_watts`.
- `trainiq/sync/engine.py` — `SynchronizationEngine.__init__` accepts `athlete_profile: Optional[AthleteProfile] = None` and passes it straight through.

**No connector references `AthleteProfile` at all** — confirmed by its absence from the grep results across `trainiq/connectors/`.

**A real finding: `date_of_birth` is populated everywhere `AthleteProfile` is constructed (including in `synthetic_dataset.py`'s scenarios) but is never read by `compute_training_load()` or anywhere else.** It's a dead field in the current pipeline, not a bug — but worth a decision, not silence. Milestone B's original discussion mentioned age/date-of-birth as a route to *estimating* max-HR when it isn't directly known (a common practical formula, e.g. 220-minus-age). Whether Epic 7 should implement that estimation fallback, or whether `date_of_birth` stays reserved for something else (future Coach-layer age-adjusted reasoning, out of Epic 7's scope) is Open Question A below.

## 2. Roadmap alignment — and a backlog hygiene finding

Comparing against the original Feature 7.1 scope: persistent-facts storage, a one-time initial-setup collection flow, missing values reducing confidence rather than blocking functionality. None of this exists in code yet — confirmed, not assumed, by the absence of any `athlete_profile`-related table, migration, or setup-flow code anywhere in the repository.

**What Epic 6 already did that changes Epic 7's remaining work:** the consumption side is entirely done. `compute_training_load()` already implements method selection (TSS-opportunistic, TRIMP-fallback, Unknown-otherwise), already accepts the exact `AthleteProfile` shape, and is already tested against both a populated and an absent profile. **Epic 7's actual remaining scope is narrower than the original Feature 7.1 description implies: persistence and a way to get a profile into the `SynchronizationEngine` constructor. No computation logic, no schema for the load engine itself — that's built.**

**Backlog hygiene finding, worth fixing before Epic 7 starts, not after:** `BACKLOG.md` currently lists both BL-002 and BL-003 as `Open`, but checking each against what Epic 6 actually shipped:
- **BL-003** ("`sync_connector()` will need editing... for Epic 6") is **fully resolved** — Epic 6 slice 5 did exactly this. It should be moved to `CLOSED`, referencing slice 5. Leaving it open going into Epic 7 misrepresents the project's actual state.
- **BL-002** ("formalize `normalize()`'s output contract... when Epic 6 is built") is **only partially resolved.** Epic 6 introduced `RecordKind` — an explicit contract for *which shape* a connector produces — but did not introduce a typed contract (e.g. a `TypedDict` or dataclass) for the *exact required keys within* each shape. `build_canonical_record()` still indexes `normalized["external_id"]`, `normalized["start_time"]`, `normalized["duration_s"]` etc. against a plain `dict`, relying on convention, exactly as BL-002 originally described. This should stay open, but its text should be updated to reflect what's actually still missing, not restate the pre-Epic-6 description.

Recommend fixing both entries as part of Epic 7's opening housekeeping (a documentation-only change, not a scope addition).

**A second process finding, outside the backlog but worth naming:** the actual design documents this project has produced — the Engineering Constitution, the Phase 0/0B milestone reports, the Tier A/B roadmaps — exist only in this conversation's history, not as files in the repository. `docs/` currently contains only the two ADRs and the two Epic 6 reports. This means anyone (or any future Claude instance) picking up this repository without this conversation's context cannot actually check Epic 7's work against the original Feature 7.1 description the way this report just did — they'd have no source to check against. **Recommend committing at minimum the Constitution and the relevant roadmap sections into `docs/` at some point** — not blocking Epic 7 on this, but flagging it now since Epic 7 is the first epic where this discovery report had to reconstruct roadmap intent from conversation memory rather than a repository file.

## 3. Persistence design

**Schema addition required:** one new table, `athlete_profile`, mirroring the dataclass exactly — `sex TEXT`, `date_of_birth TEXT`, `resting_hr INTEGER`, `max_hr INTEGER`, `ftp_watts INTEGER`, all nullable (every field is optional per the dataclass and per Milestone B's "missing values reduce confidence, never block"). TrainIQ is single-user by design throughout this project — recommend the standard SQLite singleton-table pattern (`id INTEGER PRIMARY KEY CHECK (id = 1)`), which makes "exactly one profile, ever" a database-enforced invariant rather than an application-level convention to remember. `credentials_metadata` (existing table) is not a usable precedent here — it's keyed per-provider, correctly, since there are multiple providers; `athlete_profile` has no equivalent natural key, which is exactly why the singleton pattern fits.

**BL-009, checked against this new migration:** BL-009 (`weigh_ins` needs a `source_confidence` column) and Epic 7's `athlete_profile` table are unrelated changes touching different tables for different reasons. **Recommend two separate migrations (v2→v3 and v3→v4), not one bundled migration** — bundling them would mean a single migration's changelog entry has to explain two unrelated epics' worth of reasoning, which works against the traceability this project has maintained ADR-by-ADR and backlog-item-by-backlog-item throughout. This is a recommendation, not a decision made here.

**Confirmed clean: no persistence responsibilities have leaked into normalization or connector code.** Grepped `trainiq/normalization/` and `trainiq/connectors/` for any SQL execution, `sqlite3` import, or `.commit()` call — none found. Both layers remain exactly as thin/pure as the Constitution requires; Epic 7's persistence work belongs entirely in `trainiq/storage/` and wherever the profile is loaded for `SynchronizationEngine`.

## 4. Integration points

**Profile flow, as it exists today:** `SynchronizationEngine.__init__(athlete_profile: Optional[AthleteProfile] = None)` already accepts exactly what's needed. Epic 7's job is to build whatever loads a real `AthleteProfile` from the new table and passes it into that constructor argument — likely a small `load_athlete_profile(conn) -> Optional[AthleteProfile]` function in `trainiq/athlete/`, called once by whatever wires up the `SynchronizationEngine` at app-start (`trainiq/app.py` today, which currently constructs no `SynchronizationEngine` at all — Epic 0's `app.py` only proves the Foundation boots, per its own DoD).

**Can `compute_training_load()` remain completely unchanged?** Yes — confirmed by inspection, not assumption: its signature already takes `Optional[AthleteProfile]`, and its entire body operates on whatever object it's handed, with zero knowledge of where that object came from. Epic 7 supplies a populated instance instead of `None`; nothing inside `load.py` needs to know or care about the distinction.

**Do any connectors need modification?** No — confirmed by the grep in Section 1: zero connector files reference `AthleteProfile` today, and nothing about persisting a profile requires them to.

## 5. Quality Gate impact

**New evidence Epic 7 will need, not yet existing:**
- Standard CRUD/migration tests for the new table, in the same shape as `test_migrate_is_idempotent` and `test_migration_backs_up_existing_database` (both already established patterns in `test_storage.py`).
- An end-to-end test proving the *actual* payoff: a real connector's activity, synced through `SynchronizationEngine` with a real (non-`None`) `AthleteProfile` supplied, produces a real `training_load` value with `training_load_method` of `"trimp"` or `"tss"` — not `"unknown"`. This is the test that finally exercises the branch every Epic 6 test deliberately avoided (since no profile existed to supply).
- A singleton-constraint test confirming the table genuinely cannot hold a second row.

**Do the existing 195 tests remain unaffected until the first implementation slice begins?** Yes by construction — this report has made no code changes. They remain the baseline Epic 7's first slice will be measured against.

---

## Open questions requiring a decision before implementation (none decided here)

**Question A:** Should Epic 7 implement age-based max-HR estimation using `date_of_birth` (closing the dead-field finding in Section 1), or leave that field reserved/unused for now and only wire up direct persistence of whatever the athlete enters? Affects whether Epic 7 has one slice or two around the "estimation vs. direct entry" question Milestone B originally left open.

**Question B:** Confirm the recommended singleton-table pattern and the two-separate-migrations approach to BL-009 (Section 3) before either is implemented.

**Question C:** Confirm whether fixing the BL-002/BL-003 backlog entries (Section 2) should happen now, as Epic 7's first small housekeeping step, or separately.

---

## Recommendation

Epic 7's actual remaining scope, now that Epic 6's consumption side is fully built, is smaller than the original roadmap description: persistence, a loader, and wiring into `SynchronizationEngine` — not any load-computation logic, which already exists and is tested. Once Questions A-C are answered, the first slice would naturally be the `athlete_profile` schema migration itself, tested in isolation before anything reads from it — the same incremental pattern Epic 6 used successfully across all five of its slices.
