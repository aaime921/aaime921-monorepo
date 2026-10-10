# 83 – Live-fixture regression tests: design

Requirements: `docs/trainiq/requirements/83-live-fixture-regression-tests.md` (12 ACs).
Fixtures + gotchas: `projects/trainiq/tests/fixtures/live/README.md`. Tests only; no new runtime code unless a fixture exposes a bug (AC 12).

## Dependency (read first)
AC 6 (class-catalog builder) needs `trainiq/export/classes.py` and the two `PelotonConnector.fetch_*` methods from **PR #82 (#72), still open and unmerged**. Developer: confirm #82 is merged to `main` before writing the catalog test. If not, implement everything else and add `blocked:dependency` for AC 6 only (don't re-implement #72 code).

## Layout
One module `projects/trainiq/tests/test_live_fixtures.py`, grouped by classes `TestPeloton`, `TestStrava`, `TestEufy`, `TestDbExport`. Module-level helper:
```python
LIVE = Path(__file__).parent / "fixtures" / "live"
def live(name: str):  # json.load(open(LIVE/name)); return ["response"] (db_samples: whole dict)
```
Read-only (`open(..., "r")`), no network: connectors are built the way `tests/test_peloton_connector.py`, `test_strava_unofficial_connector.py`, `test_eufy_connector.py` build them (follow their constructor/fake-session patterns); call only pure methods/functions. Add an autouse guard that fails on `socket.socket.connect` to enforce AC 11. Add a test that every `*.json` in `live/` is referenced by name in the module (coverage guard for future fixtures, AC 1).

## Cases (expected values derived from the file contents, not invented)
| Fixture | Code under test | Assert |
|---|---|---|
| `peloton_user_workouts_page` | `PelotonConnector.normalize(item)` per `data[]` item | no raise; `start_time` from epoch int → ISO UTC; `distance_m` = miles × 1609.344 when the performance summary is attached (build `raw` the way `download()` does – read lines ~937–1060 – merging `_parse_performance_response(peloton_performance_graph)`); `None` when absent, never guessed |
| `peloton_session` | `SESSION_RIDE_ID_FIELD` lookup as in `fetch_class_session`/`fetch_class_details` (fake `_authenticated_get` returning the fixture) | resolved id == `response["ride_id"]` and != `response["id"]` |
| `peloton_ride_details` | `_extract_ride_metadata(response, ride_id)` | class_type/title/instructor populated from nested `ride` + top-level `class_types` |
| `peloton_performance_graph` | `_parse_performance_response(response)` | avg/max HR, max power, distance populated via `slug`; distance unit via `display_unit` |
| `peloton_metadata_mappings` + `peloton_archived_rides` | `classes.build(conn, primary_ids, fake_catalog)` where the fake returns the two fixtures; in-memory DB seeded via `storage.schema` with one Peloton cycling row whose `class_type` is a tag present in the mappings | `status == "ok"`, ≥1 class candidate (AC 6). Pick the seeded tag from the fixture at test time (first `class_types` name) |
| `peloton_api_me` | existing user-id/profile read in `PelotonConnector` | no `distance_unit` field handled (#45): explicit-absence test via `_resolve_distance_unit_token(None)` |
| `strava_training_activities_page` | `StravaUnofficialConnector.normalize(model)` per `models[]` item | `start_time` (UTC ISO) derives from `start_time`, differs from the local conversion of `start_date_local_raw`; sport mapped |
| `strava_activity_streams` | `strava_streams.derive_stream_metrics(response)` | avg HR, moving_time_s, pace_s_per_km non-None and within plausible bounds; missing-sensor keys give `None` not 0 |
| `eufy_device_data_item` | `EufyConnector.normalize(item)` | weight_kg == `scale_data.weight`/10; `body_fat` 0 → `None` |
| `db_samples` | `export.data.parse_timestamp` on every row's TEXT timestamp, then load rows into an in-memory DB (schema v11 via `storage.schema`) and run `export.data.load_activities`/`load_weigh_ins` plus each renderer (`recent`, `load`, `performance`, `last_done`, `weight`, `profile`) | no raise; epoch and ISO rows both parse to tz-aware datetimes; mixed set sorts without TypeError |

If a column set in `db_samples.json` doesn't match `storage.schema`, insert by the fixture's own column names (do not edit the fixture); a schema mismatch is itself a finding (fix code, per AC 12).

## Bug handling (AC 12)
If any case fails, fix production code minimally in the same PR; list each fix in the PR description. Never alter fixtures.

## Verification scope
Fixtures were captured by the BO from real accounts, so no further live check is needed; CI cannot reach accounts (unchanged).
