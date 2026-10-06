# Architecture: Cross-Provider Activity Deduplication (Peloton ↔ Strava)

**Issue:** #37
**Requirements:** `docs/trainiq/requirements/37-cross-provider-activity-dedup.md`

## Approach

### Shape of the solution

A new, self-contained module, `trainiq/dedup/detector.py`, that:

1. Reads every `normalized_activities` row for `provider IN ('peloton',
   'strava', 'strava_unofficial')` (the exact three providers in scope —
   `peloton_csv` is deliberately excluded, per the requirements doc's
   "Out of scope").
2. Normalizes each row's `start_time` to a timezone-aware `datetime` by
   **dispatching on the row's own `provider` column**, not by sniffing the
   stored string's shape. `peloton` rows are a text-encoded epoch int
   (confirmed in `docs/trainiq/architecture/33-peloton-resume-cursor-type-mismatch.md`
   §"Risks/tradeoffs": SQLite TEXT affinity round-trips a bound Python
   `int` to its string form on storage); `strava`/`strava_unofficial` rows
   are already ISO 8601 strings (`StravaConnector.normalize()` /
   `_extract_start_time_iso()`). Provider is always known for every row
   being compared, so there is no case where format has to be guessed from
   the value alone — this is simpler and strictly more reliable than a
   generic "does this look like an int or an ISO string" detector, and it
   keeps working even if a future change starts writing a different raw
   shape for some other reason, as long as it's provider-specific.
3. Finds candidate cross-provider pairs using a **sort-and-sweep** over all
   in-scope rows ordered by normalized start time: walk the sorted list
   once, and for each row only compare forward against rows whose
   normalized start time is within the proximity window — this turns what
   would otherwise be an O(n²) pairwise scan into an O(n log n) one for the
   dataset sizes this project ever has (single BO, a few thousand
   activities at most across all providers combined). Same-provider pairs
   are skipped (not in scope — this issue is about cross-provider
   duplication only).
4. Scores every candidate pair that passes the primary gate (see
   "Interfaces/contracts" below) and writes exactly one row per such pair
   to `dedup_links`, with `resolution` distinguishing an auto-link from a
   flagged-ambiguous pair, and (for an auto-link) naming the primary side.
5. Is idempotent across repeated runs: before inserting, it checks whether
   a `dedup_links` row already exists for that pair (see "Risks/tradeoffs"
   — there is no schema change, so this check is done in Python, not via a
   `UNIQUE` constraint) and skips re-inserting it.
6. Never touches `raw_activities` or `normalized_activities` — it only
   `SELECT`s from `normalized_activities` and `INSERT`s into `dedup_links`.

A thin CLI script, `scripts/run_dedup_backfill.py`, is the invocation
mechanism (see "How it's invoked" below), following the exact pattern
`scripts/import_peloton_csv.py` already establishes for "logic lives in
`trainiq/`, the script is just an `open_db()` + call + print wrapper."

### Why a new module, not wired into the Sync Engine

The requirements doc explicitly scopes "wiring into the live sync path" and
"building a new analytics/totals/summary layer" out of this issue — the
deliverable is a **runnable, correct one-time pass** over data that already
exists, not a live-dedup-on-every-sync feature. A new module under
`trainiq/dedup/` keeps this cleanly separable from `trainiq/sync/engine.py`
(the Sync Engine's job is ingesting and normalizing single records from a
single connector at a time; cross-record, cross-provider comparison is a
different kind of operation with a different invocation lifecycle) and
from `trainiq/normalization/engine.py` (which builds one canonical record
from one raw record — it has no concept of comparing two already-persisted
records against each other).

This also keeps `tests/test_architecture_invariants.py`'s existing
single-writer guarantee intact with **zero changes to that test file**: the
invariant governs writes to `normalized_activities`/`weigh_ins` and calls to
`build_canonical_record()` specifically. The dedup detector never does
either — it only reads `normalized_activities` and writes `dedup_links`, a
table that invariant doesn't (and shouldn't) govern. No change to
`_AUTHORIZED_INGESTION_ENTRY_POINTS` is needed or appropriate.

### How it's invoked

`scripts/run_dedup_backfill.py path/to/trainiq.db` (defaulting to the real
`APP_SUPPORT_DIR / "trainiq.db"` exactly like `import_peloton_csv.py` and
`peloton_smart_sync.py` already do). It opens the DB via the normal
`open_db()` (so schema migration runs first, same as every other entry
point), calls `trainiq.dedup.detector.run_backfill(conn)` once, prints a
summary (`scanned`, `linked`, `flagged`, `skipped_existing`), and exits 0.
Running it again (e.g. after a new sync pulls in more activities) re-scans
everything and only adds rows for pairs not already recorded — safe to run
repeatedly, including as a cron/manual step after every sync, without
re-wiring anything into `app.py`. Not wiring it into `app.py`/`main()`
itself is deliberate, per the requirements doc's scope boundary — that
remains a separate, future decision if the BO wants it automatic.

### Winner rule (generalized from the BO's stated rule)

The BO's rule as stated in the issue covers Peloton vs. Strava: "Peloton is
primary for rides because it carries power/HR; Strava (official or
unofficial) contributes GPS/distance." The requirements doc's scope,
though, is **every pairing** among `peloton`, `strava`, `strava_unofficial`
— which includes `strava` vs. `strava_unofficial` pairs, a case the BO's
rule doesn't directly name (neither side is Peloton). Rather than invent an
unrelated new concept for that one case, this design extends the existing
rule using a property the codebase already has: `Connector.capability_tier`
(`CapabilityTier.TIER_1_OFFICIAL` for `StravaConnector`,
`CapabilityTier.TIER_2_UNOFFICIAL` for `StravaUnofficialConnector`,
`trainiq/connectors/base.py`). `strava_unofficial`'s own `normalize()`
never reports HR/power (`avg_hr`/`avg_power`/`max_power` are always
`None` — see its module docstring), so the official connector is
never a strictly-worse data source for the same ride. The generalized
rule, in priority order:

1. If one side's provider is `peloton`, it is primary (power/HR), the other
   is secondary (GPS/distance) — the BO's rule, verbatim.
2. Otherwise (the `strava` vs. `strava_unofficial` case), `strava`
   (official, Tier 1) is primary; `strava_unofficial` (Tier 2) is
   secondary.

This is documented in the module docstring of `trainiq/dedup/detector.py`
(requirement: "documented in the code, not only in this doc"), with the
exact reasoning above.

## Affected components/files

**New:**
- `projects/trainiq/trainiq/dedup/__init__.py` — empty, package marker.
- `projects/trainiq/trainiq/dedup/detector.py` — all matching/scoring/
  persistence logic (see "Interfaces/contracts").
- `projects/trainiq/scripts/run_dedup_backfill.py` — CLI entry point.
- `projects/trainiq/tests/test_dedup_detector.py` — new tests (Developer/QA).

**Unchanged (verified, not assumed):**
- `trainiq/storage/schema.py` — `dedup_links`'s existing columns
  (`activity_id_a`, `activity_id_b`, `confidence_score`, `resolution`) are
  exactly what this design needs. No migration.
- `trainiq/sync/engine.py`, `trainiq/normalization/engine.py`,
  `trainiq/connectors/*.py` — none of this issue's logic lives here or
  needs to. `PelotonConnector.normalize()`'s unconverted epoch-int
  `start_time` is read, not fixed — issue #33's architecture doc already
  flagged converting it as a separate, out-of-scope cleanup, and this
  design works correctly with the current mixed-format reality by design
  (see "Approach" above), so there's no new pressure to fix it as part of
  this issue either.
- `tests/test_architecture_invariants.py` — no change; see "Approach"
  above for why this stays true.

## Interfaces/contracts

```python
# trainiq/dedup/detector.py

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

PROVIDERS_IN_SCOPE = ("peloton", "strava", "strava_unofficial")

# BO's live evidence (issue #37): real duplicate pairs in production start
# within ±5 minutes of each other. Primary signal's gating window.
TIME_WINDOW_S = 300

# Secondary-signal gate: duration must agree within this many seconds to
# count as a duration match. Flat, not relative — the fixture's real clock-
# skew-only case (1800s vs 1790s, a 10s gap from rounding/stop-lag, not a
# different ride) and the BO's production data both involve single-ride
# duration gaps far smaller than this; a flat 2-minute tolerance is
# generous enough to absorb that without being so wide it starts matching
# genuinely different rides.
DURATION_TOLERANCE_S = 120

# Confidence weights (sum to 1.0) and auto-link threshold. No BO-specified
# value exists for either (requirements doc: "picking and documenting one
# is Architect/Developer work") — these are the stated, documented values
# this design commits to. A pair that clears the primary gate below but
# scores under AUTO_LINK_THRESHOLD is flagged, never silently auto-linked.
TIME_WEIGHT = 0.5
DURATION_WEIGHT = 0.25
DISCIPLINE_WEIGHT = 0.25
AUTO_LINK_THRESHOLD = 0.6

RESOLUTION_PREFIX_LINKED = "linked:primary="
RESOLUTION_FLAGGED = "flagged:needs_review"


@dataclass(frozen=True)
class _ScoredPair:
    activity_id_a: int
    activity_id_b: int
    confidence_score: float
    resolution: str


def run_backfill(conn: sqlite3.Connection) -> "BackfillResult":
    """Entry point. Reads every in-scope normalized_activities row, finds
    candidate cross-provider pairs via sort-and-sweep on normalized start
    time, scores each candidate that passes the primary gate, and writes
    any not already present in dedup_links. Commits once at the end (all-
    or-nothing for a single invocation — matches this table's role as a
    derived, re-run-safe annotation layer, not a per-record transactional
    log). Returns a summary; never raises for "no activities found" or
    "no pairs found" (both are valid, reportable outcomes, not errors)."""


@dataclass(frozen=True)
class BackfillResult:
    scanned: int           # in-scope normalized_activities rows read
    candidate_pairs: int    # pairs that passed the primary gate
    linked: int             # of those, auto-linked (confidence >= threshold)
    flagged: int            # of those, flagged as ambiguous
    skipped_existing: int   # candidate pairs already present in dedup_links


def normalize_start_time(provider: str, raw_start_time: str) -> datetime:
    """Dispatches on `provider`, never on sniffing `raw_start_time`'s shape
    (see Approach). Raises ValueError for a provider outside
    PROVIDERS_IN_SCOPE — this function is only ever called with one of the
    three in-scope providers, so that's a genuine programming-error guard,
    not a normal-path outcome."""


def primary_provider(provider_a: str, provider_b: str) -> str:
    """The generalized winner rule (see Approach, "Winner rule"). Returns
    whichever of provider_a/provider_b is primary. Raises ValueError if
    neither side is one of PROVIDERS_IN_SCOPE, or if both sides are the
    same provider (same-provider pairs are never scored — see
    find_candidate_pairs)."""
```

`dedup_links` is written with exactly its four existing columns:

```sql
INSERT INTO dedup_links (activity_id_a, activity_id_b, confidence_score, resolution)
VALUES (?, ?, ?, ?)
```

`activity_id_a`/`activity_id_b` are always stored with the numerically
smaller `normalized_activities.id` first (`activity_id_a < activity_id_b`)
— a fixed, documented ordering convention so the "does this pair already
exist" idempotency check (`SELECT 1 FROM dedup_links WHERE activity_id_a = ?
AND activity_id_b = ?`) only ever has to check one orientation, not both.

`resolution` values, exactly two shapes, both prefix-queryable:
- Auto-linked: `"linked:primary=peloton"` or `"linked:primary=strava"` (the
  literal provider string of whichever side `primary_provider()` returned —
  never `strava_unofficial`, since the generalized rule above never makes
  it primary against either other in-scope provider).
- Flagged: the literal constant `"flagged:needs_review"`.

A caller can therefore distinguish the two classes with
`resolution LIKE 'linked:%'` vs. `resolution = 'flagged:needs_review'`, and
extract the primary provider from a linked row with
`resolution.removeprefix("linked:primary=")` — this is the "queryable to
resolve a pair to its primary record" surface the requirements doc asks
for (not a view or new table, per its explicit scope boundary: "actually
building totals/analytics that consume it is future work"). A small helper
is still worth adding for convenience, used by future analytics but not by
this issue itself:

```python
def primary_activity_ids(conn: sqlite3.Connection) -> set[int]:
    """Every normalized_activities.id that is NOT the secondary side of an
    auto-linked pair — i.e. the set a future summary/totals layer should
    sum over to count each linked pair once. Flagged (ambiguous) pairs
    contribute BOTH sides to this set unchanged, since they are explicitly
    not merged (requirement: "never silently merge"); only an auto-linked
    pair's secondary side is excluded."""
```

### Primary-gate / scoring algorithm (the core of `run_backfill`)

For every candidate pair `(x, y)` from different providers, both in
`PROVIDERS_IN_SCOPE`, whose normalized start times are within
`TIME_WINDOW_S` of each other (the sort-and-sweep's own filter):

1. `duration_match = abs(x.duration_s - y.duration_s) <= DURATION_TOLERANCE_S`
   if both sides have a non-null `duration_s` (always true today — every
   connector's `normalize()` sets `duration_s`), else `False`.
2. `discipline_match = x.discipline == y.discipline` (the already-canonical
   `discipline` column — `cycling`/`running`/`strength`/`yoga`/`other` —
   not each connector's raw pre-taxonomy value).
3. **Primary gate:** if neither `duration_match` nor `discipline_match` is
   `True`, the pair is **not scored at all** — no `dedup_links` row is
   written for it. This is requirement AC1's "and at least one other
   signal... also agrees" — a time-proximity match alone is never
   sufficient.
4. Otherwise, compute:
   ```
   time_score = max(0.0, 1.0 - abs(delta_seconds) / TIME_WINDOW_S)
   confidence = (
       TIME_WEIGHT * time_score
       + DURATION_WEIGHT * (1.0 if duration_match else 0.0)
       + DISCIPLINE_WEIGHT * (1.0 if discipline_match else 0.0)
   )
   ```
   rounded to 4 decimal places for a stable, diffable stored value.
5. If `confidence >= AUTO_LINK_THRESHOLD`: `resolution =
   f"linked:primary={primary_provider(x.provider, y.provider)}"`.
   Otherwise: `resolution = "flagged:needs_review"`.
6. Skip (don't re-insert) if a `dedup_links` row for this `(activity_id_a,
   activity_id_b)` pair already exists; otherwise insert.

Worked example against the production numbers in the issue: an exact-time
Peloton/Strava pair with matching duration and discipline scores
`0.5*1.0 + 0.25*1.0 + 0.25*1.0 = 1.0`. The fixture's 3-minute-skew pair
(`scenario_cross_provider_duplicates`, 180s apart, duration 1800 vs. 1790s,
both `cycling`) scores `0.5*(1 - 180/300) + 0.25*1 + 0.25*1 = 0.5*0.4 + 0.5 =
0.7` — clears `AUTO_LINK_THRESHOLD = 0.6`, auto-linked, satisfying
requirement #5's "near match within tolerance, produces a recorded link."
A pair 290s apart (just inside the window) whose duration disagrees but
discipline agrees scores `0.5*(1 - 290/300) + 0 + 0.25 ≈ 0.267` — passes the
primary gate (discipline agreed) but lands below threshold, so it's
recorded `flagged:needs_review`, not silently merged.

## Task breakdown

1. Create `trainiq/dedup/__init__.py` (empty) and `trainiq/dedup/detector.py`
   with the constants, dataclasses, and functions in "Interfaces/contracts"
   above: `normalize_start_time()`, `primary_provider()`,
   `primary_activity_ids()`, and the internal pair-finding/scoring/persist
   logic that `run_backfill()` composes.
2. Implement `normalize_start_time()`: `peloton` → `datetime.fromtimestamp(
   int(raw_start_time), tz=timezone.utc)`; `strava`/`strava_unofficial` →
   `datetime.fromisoformat(raw_start_time)` (already timezone-aware per
   both connectors' `isoformat()` output). Raise `ValueError` for any other
   provider.
3. Implement the sort-and-sweep candidate finder: `SELECT id, provider,
   external_id, start_time, duration_s, discipline FROM
   normalized_activities WHERE provider IN (?, ?, ?)`, normalize every
   row's start time once, sort by it, then sweep — for each row, compare
   forward only while the next row's normalized start time is within
   `TIME_WINDOW_S`, skipping same-provider comparisons.
4. Implement the primary-gate + confidence-scoring step exactly as
   specified above, including the stable pair ordering
   (`activity_id_a < activity_id_b`) before either the idempotency check
   or the insert.
5. Implement `primary_provider()` per the generalized winner rule, with
   the module docstring documenting both the BO's original rule and the
   `strava`-vs-`strava_unofficial` extension and why (see "Approach").
6. Implement the idempotency check (`SELECT 1 FROM dedup_links WHERE
   activity_id_a = ? AND activity_id_b = ?`) immediately before each
   insert, and accumulate `BackfillResult` counts (`scanned`,
   `candidate_pairs`, `linked`, `flagged`, `skipped_existing`).
7. Commit once at the end of `run_backfill()`.
8. Create `scripts/run_dedup_backfill.py` mirroring
   `scripts/import_peloton_csv.py`'s structure: `argparse` with an optional
   positional/`--db-path` (default `APP_SUPPORT_DIR / "trainiq.db"`),
   `open_db()`, call `run_backfill()`, print the `BackfillResult` fields,
   return 0.
9. Write tests per "Test strategy notes" below.
10. Run the full existing test suite (`pytest`) and confirm zero
    regressions, in particular `tests/test_architecture_invariants.py` and
    `tests/test_synthetic_dataset.py` (unchanged, but worth confirming
    nothing in this new module accidentally imports/triggers either in a
    way that breaks them).

## Test strategy notes

All at the unit/integration level, in-memory SQLite (`open_db` against a
temp path, or a `:memory:` connection with `schema.migrate()` run against
it — follow whichever existing pattern `tests/test_sync_engine.py` or
`tests/test_storage.py` already uses for test DB setup, for consistency).
No live-account access needed or possible, per this project's testing
scope boundary — everything here operates on already-normalized rows a
test seeds directly into `normalized_activities`, with no connector
involved.

Minimum required cases (requirement #7, the issue's own acceptance
criteria):

1. **Exact start-time match** — two rows, same normalized start time,
   matching duration and discipline, different providers (e.g. `peloton` +
   `strava`) → one `dedup_links` row, `resolution` starts with
   `"linked:primary="`, confidence `1.0`.
2. **Near match within tolerance** — reproduce
   `scenario_cross_provider_duplicates`'s own numbers (180s apart, 1800s
   vs. 1790s duration, both cycling) directly from
   `trainiq/synthetic_dataset.py` (seed it into `normalized_activities` via
   the normal `build_canonical_record()` → insert path, or construct the
   row shape it produces) → linked, confidence `0.7` per the worked
   example above.
3. **Non-match** — two activities on the same day but more than
   `TIME_WINDOW_S` apart (e.g. a morning ride and an evening run) → zero
   `dedup_links` rows for that pair; `run_backfill()` must not raise.
4. **Epoch-int vs. ISO-8601 format handling** — a `peloton` row with
   `start_time` stored as a text-encoded epoch int (e.g. `"1791134056"`,
   matching how `_upsert_normalized_activity` actually persists it via
   SQLite TEXT affinity — don't hand-construct an already-parsed value)
   paired with a `strava`/`strava_unofficial` row whose `start_time` is a
   real ISO 8601 string within the window → correctly computed as a
   proximate pair and linked. This is the test that would fail loudly if
   `normalize_start_time()`'s provider-dispatch were ever replaced with a
   generic sniffing approach that mishandled one format.
5. **Flagged, not merged** — a pair that passes the primary gate (one
   secondary signal agrees) but scores below `AUTO_LINK_THRESHOLD` (e.g.
   near the edge of `TIME_WINDOW_S` with only discipline matching, no
   duration match) → exactly one `dedup_links` row,
   `resolution == "flagged:needs_review"`, confidence `< 0.6`.
6. **Idempotency** — calling `run_backfill()` twice against the same DB
   state produces the same `dedup_links` rows, not duplicates; the second
   call's `BackfillResult.skipped_existing` accounts for every pair the
   first call already recorded, and its `linked`/`flagged` counts for
   those same pairs are `0`.
7. **Generalized winner rule** — a `strava` vs. `strava_unofficial`
   candidate pair auto-links with `resolution ==
   "linked:primary=strava"`, confirming `primary_provider()`'s extension
   beyond the BO's literal Peloton-vs-Strava wording.
8. **Scale/correctness against the full fixture scenario** — run
   `run_backfill()` against all 12 activities (6 days ×
   peloton+strava) `scenario_cross_provider_duplicates()` produces and
   assert exactly 6 linked pairs, 0 flagged, 0 skipped-as-non-matching —
   the smaller, deterministic stand-in for requirement #5's "54 production
   pairs" claim (which this repo cannot verify directly, since it has no
   access to the BO's real database — see requirements doc's "Out of
   scope").

`primary_activity_ids()` gets its own focused test or two (e.g. an
auto-linked pair excludes the secondary side; a flagged pair excludes
neither side) — not part of the required-by-the-issue list above, but
needed since the function is part of this design's public surface.

## Risks/tradeoffs

- **No `UNIQUE` constraint on `dedup_links(activity_id_a, activity_id_b)`.**
  The requirements doc is explicit that "no schema change should be
  needed," so idempotency is enforced in application code (a `SELECT`
  before every `INSERT`) rather than at the database level via `INSERT OR
  IGNORE`/`ON CONFLICT`, unlike `normalized_activities`/`raw_activities`
  (which do have real `UNIQUE` constraints the Sync Engine relies on).
  This means a hypothetical second, independent writer to `dedup_links`
  that doesn't go through this same check-then-insert sequence could still
  create a duplicate row — an accepted gap given the no-migration
  constraint, and consistent with this table having no other writer today
  or planned by this issue.
- **Confidence formula and threshold are this design's own choice, not a
  BO-supplied number.** Documented explicitly, with a worked example
  against the fixture's own numbers, specifically so a future reader (or
  the BO) can see exactly what `0.6`/`120s`/the three weights were chosen
  against and re-tune them with evidence if the real 54-pair backfill
  (an ops task, not this issue's deliverable) turns up pairs that should
  have linked but didn't, or vice versa.
- **O(n log n) sort-and-sweep, not a database-side query.** All matching
  happens in Python after one bulk `SELECT`, not via SQL self-joins or a
  spatial index. Reasonable for this project's actual scale (single BO,
  low thousands of activities total) — flagged so a future maintainer
  doesn't assume this was benchmarked against a much larger dataset size
  it was never designed for.
- **`strava` vs. `strava_unofficial` primary-rule extension is this
  design's own judgment call, not literally stated by the BO** (see
  "Approach" — "Winner rule"). Flagged prominently rather than silently
  folded into "the BO's rule," consistent with this project's
  evidence-based principle: the reasoning (capability tier, strava
  official's data never being strictly worse) is written out so it can be
  challenged or revised with better evidence later.
- **Backfill is a full re-scan every run, not incremental.** Acceptable
  for this issue's actual scale and its explicit "one-time pass" framing;
  if this is ever wired into a more frequent/automatic path (out of scope
  here), an incremental variant (e.g. only re-scanning activities newer
  than the last backfill's watermark) would be a reasonable follow-up, not
  something this issue needs to build speculatively now.
