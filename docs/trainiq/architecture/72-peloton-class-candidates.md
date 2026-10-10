# 72 – Peloton class candidates in the coach export: design

Requirements: `docs/trainiq/requirements/72-peloton-class-candidates.md` (9 ACs). Extends `docs/trainiq/architecture/71-coach-export.md` (module `trainiq/export/`, `run_export`, `fmt.py`, determinism and as-of rules). **The Developer must first confirm #71's code is merged to `main`**; otherwise `blocked:dependency`. Class-metadata columns come from #46/#58 (already on `main`).

## Answer to the BA's open questions
- **History link:** `normalized_activities.provider_class_id` = the *resolved, stable* `ride_id` (#58; per-session `peloton_id` is not stored there) and `normalized_activities.class_type` = `class_types[].name` joined by `", "` (#58). Use rows with `provider='peloton'` and `discipline='cycling'`, deduped through `primary_activity_ids()` (as #71).
- **Rides without class info:** `class_type` of `NULL`, `""`, `not_a_class` or `lookup_failed` contributes **no class type** (not guessed). Such a ride with a non-NULL `provider_class_id` still counts for done-before. Nothing else is inferred.
- **Limit:** `ROWS_PER_SECTION = 8`; `MAX_TYPES = 6` (most-ridden types over all history, ties by name asc). Worst case 6 × 4 × 8 = 192 rows ≈ 25 KB, so with #71 files the 50 KB target holds. Types dropped by the cap get one line `Not shown (cap): <names>`.

## Approach
Pure renderer + a small fetch layer, so everything but the HTTP calls is testable offline.
1. `used_types(conn, primary_ids)` splits stored `class_type` on `", "`, counts per tag (cycling Peloton only).
2. One `GET /api/ride/metadata_mappings` → `{class_types: name→id, instructors: id→name}`. Tags are matched to catalog names case-insensitively; a tag not in the catalog (e.g. a non-class-type tag) is listed once as `Unmatched history tags: …`, never searched.
3. For each used type × durations `(20,30,45,60)` min = `(1200,1800,2700,3600)` s: one `GET /api/v2/ride/archived` with `browse_category=cycling&class_type_id=…&duration=…&sort_by=original_air_time&desc=true&limit=8&page=0`. Budget: 1 + ≤ 24 calls.
4. `done_before` = `{provider_class_id: max(start_time date)}` from the same history rows. Row marked `done before (YYYY-MM-DD)` or `new`.

## Files / interfaces
Modified: `trainiq/connectors/peloton.py` (two public methods, reuse `_authenticated_get(..., not_found_returns_none=True)`, so auth header, `Retry-After`/429 → `TransientError`, 401/403 → `AuthenticationError` and the session's rate limiting are reused unchanged; no new HTTP code path):
```python
def fetch_ride_metadata_mappings(self) -> dict | None
def fetch_archived_classes(self, class_type_id: str, duration_s: int, limit: int = 8) -> dict | None
```
Both raise `AuthenticationError` if called before `authenticate()` (same guard as `fetch_class_details`).

New `trainiq/export/classes.py`:
```python
DURATIONS_S = {20: 1200, 30: 1800, 45: 2700, 60: 3600}
class ClassCatalog(Protocol):   # PelotonConnector satisfies it; tests pass a fake
    def fetch_ride_metadata_mappings(self): ...
    def fetch_archived_classes(self, class_type_id, duration_s, limit=8): ...
def build(conn, primary_ids, catalog: ClassCatalog | None) -> dict   # JSON-shaped, never raises on API errors
def render_md(data: dict) -> str
```
`run_export(..., catalog: ClassCatalog | None = None)` calls `build` last, after all #71 files are written, inside its own try/except for `Exception` (anything unexpected → `unavailable`). Files `peloton_classes.md` and `peloton_classes.json` (`sort_keys`, `indent=2`) are appended to the fixed file order. `app.py export`: authenticates the existing `PelotonConnector` (already configured credentials, no new auth path) and passes it; `--no-classes` passes `None`. Authentication failure or missing credentials → `catalog=None` → "class catalog unavailable".

JSON shape: `{"as_of", "status": "ok|unavailable", "reason"?, "sections": [{"duration_min", "class_type", "status": "ok|unavailable|empty", "total", "classes": [{"id","title","instructor","difficulty","air_date","done_before": "YYYY-MM-DD"|null}]}], "unmatched_tags": [], "not_shown": []}`. Header uses the #71 `As of:` line; `air_date` = UTC date of `original_air_time` (epoch s); no wall-clock anywhere (AC 8). Instructor unresolved → `-`; missing difficulty → `-` via `fmt`.

## Failure handling (AC 7)
- Metadata `None`/error/no `class_types` → whole file: `Class catalog unavailable`, status `unavailable`.
- A search `None`/error/malformed → that section states `class catalog unavailable`; empty `data[]` → `No classes found` (distinct from unavailable).
- `AuthenticationError` or `TransientError` on any call: **stop issuing further calls** (don't hammer a rate-limited API); remaining sections are `unavailable`. Other export files are already written.

## Task breakdown
1. Check #71 merged. 2. Connector methods + tests (mock session). 3. `used_types` / `done_before` from DB. 4. `build` + `render_md` + JSON. 5. Wire into `run_export` and `app.py export` (`--no-classes`); README line. 6. Tests below.

## Tests
Fixtures shaped like the live evidence (`class_types` list, `instructors` map, `data[]` + `total`). Fake catalog records calls. Cover: done-before vs new (latest date wins), unused types omitted, tag split from `"Power Zone, Tabata"`, sentinel/NULL class_type ignored, instructor resolution, empty `data[]`, metadata failure, one search failing, auth error stops further calls, call count ≤ 1 + types × 4, `cycling` in every query, byte-equal output on re-run, size < 50 KB with 6 types × 8 rows.

## Risks / verification gaps
- **UNVERIFIED:** that `archived data[].id` equals the stored `provider_class_id` (#58's `ride_id`). The live evidence lists `id` but never compares it with history. If they differ, every row shows `new` with no error. BO live check (ops task): one known past class must show `done before`. The Developer states this gap in the PR; do not add a title-based fallback.
- Class-type vocabulary mismatch (history tag vs catalog name) is surfaced via `unmatched_tags`, not hidden.
- Network in export makes it non-offline; mitigated by `--no-classes` and the never-breaks rule. Output is deterministic only for an unchanging catalog (new classes appear over time), which is inherent.
- Only the newest 8 per section can be marked done-before; older done classes are simply absent.
