# Live-shape fixtures (real API responses)

Captured **2026-10-10 from the BO's real accounts** (read-only), so code that reads
Peloton, Strava or Eufy data is tested against what those services actually send,
not a guessed shape. Credentials, tokens, cookies and emails are removed
(`<redacted>`); long lists and time series are trimmed. Field names, nesting,
list-vs-dict and value formats are untouched. Each file says what it is in `_note`.

**Rule:** any change that reads or parses one of these responses (connectors,
normalization, export, dedup) must have at least one test that loads the matching
file here and runs the real code path on it. Don't hand-write a different shape.
If a service changes its format, re-capture the file on the BO's machine.

| File | Endpoint | Shape gotchas (each caused a real bug) |
|---|---|---|
| `peloton_api_me.json` | `GET /api/me` | **No** `distance_unit` field (#45→#57) |
| `peloton_user_workouts_page.json` | `GET /api/user/{id}/workouts` | `start_time` epoch int; `distance` in **miles** |
| `peloton_session.json` | `GET /api/peloton/{peloton_id}` | `peloton_id` is a **session**; `ride_id` is the class (#46→#58) |
| `peloton_ride_details.json` | `GET /api/ride/{ride_id}/details` | `ride` nested; `class_types` top-level list |
| `peloton_performance_graph.json` | `GET /api/workout/{id}/performance_graph` | metrics/summaries by `slug`; distance with `display_unit` |
| `peloton_metadata_mappings.json` | `GET /api/ride/metadata_mappings` | `class_types`, `instructors` are **lists**, all disciplines (#72) |
| `peloton_archived_rides.json` | `GET /api/v2/ride/archived` | `data[]` of classes, `instructor_id` not name |
| `strava_training_activities_page.json` | `GET www.strava.com/athlete/training_activities` | `start_time` ISO; `start_date_local_raw` is **local** epoch (#43) |
| `strava_activity_streams.json` | `GET www.strava.com/activities/{id}/streams` | dict of equal-length lists; missing sensors = missing keys |
| `eufy_device_data_item.json` | one item of `GET …/device/{id}/data` | `scale_data.weight` deci-kg; `body_fat` 0 = not measured (#42) |
| `db_samples.json` | real DB rows (schema v11) | timestamps TEXT: Peloton/Eufy **epoch**, Strava **ISO** (#71) |
