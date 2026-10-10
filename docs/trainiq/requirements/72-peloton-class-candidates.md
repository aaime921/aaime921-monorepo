# 72 – Peloton class candidates in the coach export

Depends on #71 (design on `main`: `docs/trainiq/architecture/71-coach-export.md`; code may not be merged yet, so the Developer checks merge status first). Extend the #71 export module, no separate one.

## Summary
The BO wants the coach (an LLM reading the export) to recommend a real Peloton **bike** class for the time available, preferring classes not done before or not done recently. The export gains a class-candidates file listing recent real classes per duration and class type, each marked done-before or new.

## Scope
- New export output `peloton_classes.md` + JSON, produced by the same export run as #71.
- Durations: **20, 30, 45, 60 min**. Class types: only those the BO actually uses, derived from history (not all 14).
- Per duration x class type: newest ~8 classes with title, instructor name, difficulty, air date, class id, and **done before (date)** or **new**.
- Bike (cycling) only (BO decision).
- Data comes from the existing authenticated Peloton connector and its rate limiting: one metadata call plus bounded search calls per export.
- Live evidence (BO account, 2026-10-10, read-only):
  - `GET /api/ride/metadata_mappings` returns `class_types` (14 active cycling types: Warm Up, Cool Down, Beginner, Low Impact, Music, Intervals, Progression, Climb, Power Zone, Groove, Theme, Live DJ, Heart Rate Zone, Studio Original) and `instructors` (id to name).
  - `GET /api/v2/ride/archived?browse_category=cycling&class_type_id=<id>&duration=<seconds>&sort_by=original_air_time&desc=true&limit=N&page=0` returns `data[]` (`title`, `instructor_id`, `duration`, `difficulty_estimate`, `original_air_time`, `id`) and `total` (e.g. 391 x 45-min Power Zone).
- Out: running/walking/strength classes, scheduling or booking, real-account verification (ops task).
- Touches no checkpoint or cursor persistence; nothing is stored in the DB unless the Architect decides otherwise.

## Acceptance criteria
1. Export writes `peloton_classes.md` and `peloton_classes.json` alongside the #71 files.
2. Sections cover durations 20, 30, 45, 60 min; within each, one table per class type the BO has used in history. Class types never used are omitted.
3. Each class row shows title, instructor name (resolved from `instructors`), difficulty, air date, class id; at most ~8 rows per duration x type, newest first (`original_air_time` desc).
4. Each row is marked `done before (YYYY-MM-DD)` (date of the BO's most recent ride of that class id) or `new`.
5. Only cycling classes are requested (`browse_category=cycling`).
6. Calls: exactly one metadata call plus at most one search call per (duration x used class type) per export, through the existing authenticated client and its rate limiting.
7. If metadata or any search fails or returns no usable data, the export still succeeds and every other file is written. The affected section (or the whole file if metadata fails) states "class catalog unavailable"; nothing is fabricated.
8. Output is deterministic for identical inputs (no wall-clock timestamps), consistent with #71.
9. Tests use fixtures shaped like the live evidence above (metadata mapping with `class_types`/`instructors`; archived response with `data[]` and `total`) and cover: done-before vs new marking, unused types omitted, instructor-id resolution, empty `data[]`, and API failure.

## Open questions (non-blocking, for the Architect)
- Which stored history field links a past ride to a class id and class type (see #46, #58)? If history lacks class type for some rides, define how they are treated; do not guess.
- "Newest ~8" is approximate; the Architect picks the exact limit.
- File-size cap vs the #71 50 KB target when many types are used.
