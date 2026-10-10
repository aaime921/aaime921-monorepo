# #83 Regression tests on live-shape fixtures

## Summary
Real API samples now live in `projects/trainiq/tests/fixtures/live/` (see its
README). Four real-data bugs (#45, #46, #71, #72) passed QA on hand-written
fixtures. The BO wants tests that run the **existing** code paths on these real
files so a guessed shape can never pass again.

## Scope
- In: a test module (e.g. `tests/test_live_fixtures.py`) covering every fixture
  file; fixing existing code if it fails on a fixture.
- Out: new features, changing/re-capturing fixtures, network or real accounts.
- No checkpoint/cursor handling touched.

## Acceptance criteria
1. At least one test per fixture file, loading the file and running the real
   code on its `response`.
2. Peloton: workouts-page items through `PelotonConnector.normalize()`: distance
   miles -> metres when the performance summary is present; `start_time` epoch
   handled.
3. Peloton: session fixture resolves to the class `ride_id` (not `peloton_id`).
4. Peloton: ride-details fixture yields class metadata (nested `ride`,
   top-level `class_types` list).
5. Peloton: performance_graph yields avg/max HR, max power and distance, read
   by `slug`.
6. Peloton: metadata_mappings + archived rides through the class-catalog
   builder (#72) yield at least one candidate.
7. Strava: training_activities models through
   `StravaUnofficialConnector.normalize()`; UTC start comes from `start_time`,
   not `start_date_local_raw`.
8. Strava: streams fixture yields derived avg HR, moving time and pace.
9. Eufy: device-data item through `EufyConnector.normalize()`: deci-kg -> kg;
   `body_fat` 0 -> NULL.
10. DB: `db_samples.json` rows (mixed epoch and ISO timestamps) go through the
    export's timestamp parsing and renderers without error.
11. Tests only read fixtures (never modify them); no network.
12. If existing code fails on a fixture, fix the code (not the fixture) in the
    same PR and state so in the PR description.

## Open questions
None blocking. Expected values should be derived from the fixture contents and
the README gotchas, not invented.
