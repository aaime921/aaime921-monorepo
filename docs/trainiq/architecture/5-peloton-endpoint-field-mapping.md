# Architecture: Peloton connector uses wrong endpoint and wrong field mapping (live-verified)

Issue: #5
Requirements: [`docs/requirements/5-peloton-endpoint-field-mapping.md`](../requirements/5-peloton-endpoint-field-mapping.md)

## Approach

`PelotonConnector.download()` (`trainiq/connectors/peloton.py`) calls
`GET /api/me/workouts`, which live-verified evidence (issue #5,
`docs/verification/peloton-2026-09-28.md`) confirms 404s. The real
sequence is: `GET /api/me` (to obtain the account's `id`, needed as
`user_id`), then `GET /api/user/{user_id}/workouts`, which is genuinely
paginated (`limit`, `page`, `total`, `count`, `page_count`,
`show_previous`, `show_next`, `next` in the response body).

**Endpoint fix.** Introduce a small private helper, `_authenticated_get()`,
that performs a GET with the connector's active auth header plus the
existing `peloton-platform: web` header, and applies the exact same
status-code classification `download()` already uses (401/403 →
`AuthenticationError`, 429 → `TransientError` honoring `Retry-After`
per ADR-037, 5xx → `TransientError`, anything else non-200 →
`PelotonHTTPError`). `download()` calls it once for `GET /api/me` to get
`user_id`, then loops calling it for `GET /api/user/{user_id}/workouts`
with an incrementing `page` param, collecting `data` from each page,
stopping when the response's `show_next` is falsy. This removes the
duplicated status-handling block that would otherwise exist twice
(once for `/api/me`, once for the paginated call).

**No incremental (`since`) filtering is added for the new endpoint.** The
live-captured evidence documents the *response* pagination fields, not any
verified request-side filter parameter (nothing analogous to `after` was
tested against `/api/user/{user_id}/workouts`). Sending an unverified
filter param would repeat exactly the mistake this issue exists to fix —
assuming an API shape instead of using the live-verified one. Per the
BL-006 (Eufy) precedent already established in this codebase: when
incremental filtering isn't verified to exist, do a full-history walk
every sync and rely on the existing `INSERT OR IGNORE` + conditional
`UPDATE` persistence (RC1-HF-006) to make repeated syncs idempotent, not
wasteful of correctness. `download()` keeps its existing `since: str |
None` parameter (required by the `Connector` interface / call site in
`SynchronizationEngine`) but no longer forwards it as a query parameter;
a comment records why, citing this doc and BL-006.

**Field-mapping fixes in `normalize()`:**
- `duration_s = end_time - start_time` (both fields are present and
  required in the real response). Guard: if `end_time` is absent,
  `duration_s` is `None` (never a `TypeError`).
- `avg_power = total_work / duration_s` when both `total_work` is not
  `None` and `duration_s` is truthy (i.e. present and non-zero — guards
  both a missing `total_work` and a division by zero for a
  zero-length/malformed record); otherwise `None`. This is a legitimate
  physical derivation (joules ÷ seconds = watts), not an approximation,
  consistent with how Eufy's weight divisor (`docs/architecture/1-eufy-
  weight-unit-conversion.md`) was reasoned about and documented.
- `avg_hr`, `max_hr`, `max_power` are now hardcoded to `None`, always —
  not read from any key. This is deliberate and is what makes the
  `effort_zones: null` case (AC5) trivially safe: the code never touches
  `effort_zones` at all, so there is nothing to guard against it being
  `null`. Per the project's never-fabricate standard, `effort_zones`'s
  real data (`heart_rate_zone_durations`, `total_effort_points`) is not a
  plain avg/max bpm and must not be mapped to `avg_hr`/`max_hr` — the
  requirements doc explicitly scopes surfacing that data under new field
  names as future-epic work, not this fix.

**Distance-unit verification (AC9) — resolved, correction needed.**
`normalize()` today assigns `raw.get("distance")` directly to
`distance_m`, with **no unit conversion at all**. `trainiq/csv_import/
peloton_csv.py` — which imports the same underlying Peloton account data
via CSV export — reads a column literally named `"Distance (km)"` and
converts it with `distance_m = distance_km * 1000` before writing the same
`distance_m` field. Since both paths ultimately describe the same
provider's workout distance, and the CSV path's own column header is
direct evidence Peloton reports distance in kilometers, the live API's
`distance` field is correctly treated as kilometers too. A plausibility
check against the issue's real captured record corroborates this: `distance
= 1.2163`, `duration_s = 299` (≈5 min) → `1.2163 km / (299 s / 3600) ≈
14.6 km/h`, a normal indoor cycling-class pace; treated as meters instead
(`1.2163 m` in 5 minutes) it would be nonsensical. **Conclusion: `distance`
is in kilometers, and the current code has an existing, previously-
unflagged unit bug** — it must multiply by `1000`, mirroring the CSV
importer's own conversion, guarded for `None`.

This conclusion and its evidence must be recorded in a code comment at
the point of conversion (AC9) — not just this doc — the same convention
`WEIGHT_DECI_KG_TO_KG_DIVISOR` already established for Eufy.

## Affected components/files

- `trainiq/connectors/peloton.py` — `download()` (endpoint + pagination),
  `normalize()` (field mapping + distance conversion), one new private
  helper (`_authenticated_get()`), one new module-level constant
  (`DISTANCE_KM_TO_M_MULTIPLIER`), and an updated module docstring
  (Feature 3.3/3.4 paragraphs, and removal of the now-stale "HONEST
  LIMITATION... see BACKLOG.md, BL-008" paragraph, replaced with a short
  note that BL-008 is closed and the real shape is implemented — mirroring
  how RC1-HF fixes update their owning module's docstring elsewhere in
  this codebase).
- `tests/test_peloton_connector.py` — `FakePelotonSession` needs to support
  scripting more than one GET response in sequence (see below); every
  existing test that calls `connector.download()` needs its scripted
  response(s) updated to the new two-call (then paginated) shape; several
  new tests are needed (pagination, distance conversion, avg_power
  derivation, effort_zones null-safety, always-None fields).
- `BACKLOG.md` (BL-008) and `docs/verification/peloton-2026-09-28.md` —
  **already updated** on `main` (commit `2f94216`, 2026-09-29) to record
  this finding and cite issue #5. AC11 is already satisfied; no further
  doc changes are required by the Developer for this AC. If the Developer
  wants to note in `BACKLOG.md`'s BL-008 entry that the *code fix* has
  since landed (not just filed), that's a welcome, optional one-line
  addition when closing out the issue, not a blocking task.
- No schema/migration changes. No other connector or shared module
  (`sync/engine.py`, `normalization/engine.py`) is touched — this is
  entirely local to `PelotonConnector`, same shape as the Eufy fix
  (issue #1).

## Interfaces/contracts

`download()`'s public signature is unchanged:
`download(self, since: str | None = None) -> list[dict[str, Any]]`.
`since` is accepted (required by the base `Connector` interface) but, per
Approach above, is no longer forwarded to the API as an unverified filter
— a comment states this and cites BL-006.

`normalize()`'s public signature and returned-dict *keys* are unchanged.
Only the values computed for `duration_s`, `avg_power`, `avg_hr`,
`max_hr`, `max_power`, and `distance_m` change.

New module-level constant, alongside the existing ones:

```python
# Issue #5: PelotonConnector.normalize() previously assigned raw
# `distance` straight to `distance_m` with no conversion. Verified
# against trainiq/csv_import/peloton_csv.py, which imports the same
# provider's data from a CSV column literally named "Distance (km)" and
# performs this exact conversion — corroborated by a plausibility check
# on the issue's real captured record (1.2163 km over 299s ≈ 14.6 km/h,
# a normal indoor-cycling pace; nonsensical read as meters). `distance`
# is kilometers, not meters.
DISTANCE_KM_TO_M_MULTIPLIER = 1000
```

New private helper (illustrative signature — the Developer may adjust the
exact parameter shape, this is not a public contract):

```python
def _authenticated_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET against `path` with the active auth header, applying the same
    status-code classification download() already used for a single call.
    Returns the parsed JSON body. Raises AuthenticationError/TransientError/
    PelotonHTTPError exactly as download() did before this change."""
```

`download()` becomes (illustrative, exact code is the Developer's call):

```python
def download(self, since: str | None = None) -> list[dict[str, Any]]:
    if self._active_auth_header is None:
        raise AuthenticationError(f"{PROVIDER}: download() called before a successful authenticate()")

    me = self._authenticated_get(f"{self._base_url}/api/me")
    user_id = me["id"]

    workouts: list[dict[str, Any]] = []
    page = 0
    while True:
        body = self._authenticated_get(
            f"{self._base_url}/api/user/{user_id}/workouts",
            params={"page": page},
        )
        workouts.extend(body.get("data", []))
        if not body.get("show_next"):
            break
        page += 1
    return workouts
```

`normalize()`'s changed portion (illustrative):

```python
start_time = raw["start_time"]
end_time = raw.get("end_time")
duration_s = end_time - start_time if end_time is not None else None

total_work = raw.get("total_work")
avg_power = total_work / duration_s if total_work is not None and duration_s else None

raw_distance = raw.get("distance")
distance_m = raw_distance * DISTANCE_KM_TO_M_MULTIPLIER if raw_distance is not None else None

return {
    "provider": PROVIDER,
    "external_id": str(raw["id"]),
    "start_time": start_time,
    "duration_s": duration_s,
    "discipline_raw": discipline,
    "avg_hr": None,     # genuinely unavailable in the real API shape — see module docstring, never fabricated from effort_zones
    "max_hr": None,     # ditto
    "avg_power": avg_power,
    "max_power": None,  # genuinely unavailable from this endpoint — never fabricated
    "distance_m": distance_m,
    "calories": raw.get("calories"),
    "synced_at": datetime.now(timezone.utc).isoformat(),
}
```

`extract_resume_cursor()` is unchanged — it already reads
`normalized.get("start_time")`, which continues to be populated.

## Task breakdown

1. Add `DISTANCE_KM_TO_M_MULTIPLIER` constant with the comment above.
2. Add `_authenticated_get()` (or equivalently factor the shared
   status-handling), reusing the existing `_TransientHTTPCondition`
   catch/`TransientError`/`AuthenticationError`/`PelotonHTTPError`
   pattern already used by `_login()`/`download()`.
3. Rewrite `download()`: fetch `user_id` via `GET /api/me`, then loop
   `GET /api/user/{user_id}/workouts` incrementing `page`, collecting
   `data` from each page, stopping when `show_next` is falsy. Keep the
   `since` parameter in the signature; add a short comment explaining why
   it's accepted but not forwarded (cites this doc + BL-006).
4. Rewrite `normalize()`'s field mapping exactly as in Interfaces/
   contracts above.
5. Update the module docstring: replace the stale "HONEST LIMITATION...
   BL-008" paragraph (BL-008 is now closed) with a short note that the
   real endpoint/field shape is implemented per issue #5, and update the
   Feature 3.3 paragraph if it mentions the old single-page assumption.
6. In `tests/test_peloton_connector.py`, change `FakePelotonSession` so
   `script_get_response()` appends to a queue (`list[FakeResponse]`)
   instead of overwriting a single slot, and `get()` pops from the front
   of that queue in call order. This is what lets a single test script
   both the `/api/me` response and one-or-more `/api/user/.../workouts`
   page responses, in the order the connector will request them, without
   changing `script_get_response()`'s call-site signature (existing
   single-response tests keep working — they just script one response as
   before, now via the queue's only entry).
7. Update every existing test that calls `connector.download()` and
   scripts a GET response to script two responses instead (an `/api/me`
   success, e.g. `FakeResponse(200, {"id": "u1"})`, then the workouts
   response) — this includes (at minimum, verify no others were missed):
   `test_download_before_authenticate_raises_authentication_error` (no
   change needed — it never reaches a GET), `test_download_passes_since_
   as_after_param` (rename/rework: since `since` is no longer forwarded
   as a query param, this test's assertion changes to confirming `since`
   does *not* appear in the workouts-page request params — or, if the
   Developer judges the test no longer meaningful, replace it with a test
   asserting the `page` param behavior instead), `test_download_rate_
   limited_honors_retry_after`, `test_download_session_rejected_raises_
   authentication_error`, and `test_end_to_end_automated_login_sync`.
8. Add new tests:
   - `/api/me` → `user_id`, then a single-page `/api/user/{user_id}/
     workouts` response (`show_next: false`) → `download()` returns
     exactly that page's `data`, and the second GET call's URL contains
     the right `user_id`.
   - A two-page sequence (`show_next: true` then `show_next: false`,
     `page` incrementing 0→1) → `download()` returns the concatenation
     of both pages' `data`, in order.
   - `normalize()` on the issue's exact `effort_zones: null` record →
     `duration_s == 299`, `avg_power == pytest.approx(23646.98 / 299)`,
     `avg_hr is None`, `max_hr is None`, `max_power is None`,
     `distance_m == pytest.approx(1216.3)`.
   - `normalize()` on the issue's exact `effort_zones`-populated record →
     same always-`None` assertions for `avg_hr`/`max_hr`/`max_power`,
     confirming a populated `effort_zones` doesn't change that outcome.
   - A record missing `end_time` → `duration_s is None`, no exception.
   - A record with `total_work` present but `duration_s` `None` (missing
     `end_time`) → `avg_power is None`, no `ZeroDivisionError`/`TypeError`.
9. Update the four existing `normalize()`-focused tests
   (`test_normalize_maps_cycling_class_with_power`,
   `test_normalize_strength_class_has_no_power_never_fabricated`,
   `test_normalize_logs_unrecognized_discipline_without_dropping_the_
   record`) and their fixtures to use the real field names (`end_time`,
   `total_work`, no `avg_heart_rate`/`max_heart_rate`/`avg_power` keys in
   the raw input) and updated expectations.
10. Run the full `tests/test_peloton_connector.py` suite plus the whole
    project suite (`pytest`), confirming nothing outside this file
    regressed — no other file references `PelotonConnector.download()`/
    `normalize()`'s internals directly (verify by grep, don't assume, same
    diligence as the Eufy fix's Affected-components section).

## Test strategy notes

- Unit-level: `normalize()` in isolation against the issue's two exact
  real records (the primary regression protection this issue asks for),
  plus the missing-`end_time`/missing-`total_work` edge cases.
- Unit-level: `download()` against a fake session scripted with 1-page and
  2-page response sequences, asserting both the returned data and the
  actual request sequence (`/api/me` called first, `user_id` correctly
  threaded into the second URL, `page` incrementing correctly, looping
  stops on `show_next: false`).
- Integration-level: `test_end_to_end_automated_login_sync` continues to
  exercise the full path (login → `/api/me` → paginated workouts →
  `normalize()` → `normalized_activities`) through
  `SynchronizationEngine`, now scripted against the real shape — this is
  what proves the fix works end-to-end, not just at the unit level.
- No live-account verification is possible or required from this
  pipeline — the issue's captured real-payload evidence, now also
  recorded in `docs/verification/peloton-2026-09-28.md`, stands in for it.

## Risks/tradeoffs

- **No incremental filtering, by design, not oversight.** Every sync walks
  the account's full workout history (all pages), same cost profile as
  Eufy's BL-006-documented limitation. This is the honest choice given the
  evidence — sending an unverified `since`/`after`-style param on the new
  endpoint would repeat this issue's own root cause. If Peloton is ever
  found to support real incremental filtering on this endpoint, only
  `download()` needs to change (existing checkpoint/dedup infrastructure
  already supports it, same forward-compatible shape BL-006 documents for
  Eufy).
- **`avg_power` is a derived value, not Peloton's own reported figure.**
  `total_work / duration_s` may differ slightly from whatever "avg watts"
  Peloton's own UI shows if that excludes rest periods within the class —
  already flagged in the issue and requirements doc; not resolvable
  without a documented Peloton definition, which does not exist.
- **Distance-unit correction changes previously-persisted values' meaning
  if any real sync has ever run this code path.** Per `docs/verification/
  peloton-2026-09-28.md` §1, the real `connector_state` table has no row
  for `peloton` at all — meaning no real sync has ever completed through
  this path yet — so there is no existing-data backfill concern here,
  unlike a connector already in production use. Worth a one-line note in
  the handoff comment regardless, so this isn't silently assumed.
- **Extra network round-trip per sync.** `GET /api/me` is now called once
  per `download()` invocation to obtain `user_id`, on top of the
  paginated workout calls. Not cached across syncs (each `download()` call
  fetches fresh) — deliberately simple, matching this connector's existing
  "no cross-call caching" style, and cheap relative to walking 7+ pages of
  workout data in the same call.
