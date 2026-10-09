# Verification: Peloton distance stored ~38% too short — connector assumes km, API returns account's display unit

**Issue:** #45
**PR:** #54 (`issue-45-peloton-distance-unit` → `main`)
**Requirements:** [`docs/trainiq/requirements/45-peloton-distance-unit-conversion.md`](../requirements/45-peloton-distance-unit-conversion.md)
**Architecture:** [`docs/trainiq/architecture/45-peloton-distance-unit-conversion.md`](../architecture/45-peloton-distance-unit-conversion.md)

## Method

Checked out PR branch `issue-45-peloton-distance-unit` at `1ad21a9` (base
`dc84000`). Read every changed file in full against the architecture doc's
"Affected components/files" table, installed the project into a clean venv
(`python3.11 -m venv` + `pip install -e ".[dev]"`), ran the full
`projects/trainiq` test suite, ran every test file touched by this PR
individually, and confirmed the two pre-existing CSV-fixture failures
reproduce identically on `origin/main`. Also test-merged the PR branch
against current `origin/main` (which has advanced since the branch was cut)
to confirm no merge conflict.

## Scope check

```
git diff --stat origin/main...HEAD
```

Touches exactly the files the architecture doc scopes this issue to:
`trainiq/connectors/peloton.py`, `trainiq/normalization/renormalize.py`,
`scripts/renormalize_peloton_distance.py` (new),
`docs/trainiq/verification/peloton-2026-09-28.md` (addendum),
`projects/trainiq/BACKLOG.md` (new BL-010), plus the corresponding test
files (`test_peloton_connector.py`, `test_renormalize.py`,
`test_renormalize_peloton_distance_script.py`, new). `trainiq/csv_import/
peloton_csv.py` has zero diff — confirmed untouched (AC6).

## Acceptance criteria

| AC | Result | Evidence |
|----|--------|----------|
| 1. mi-unit account: `distance_m` within 1% of true metric distance | **PASS** | `connectors/peloton.py::normalize()` converts via `_DISTANCE_UNIT_MULTIPLIERS["mi"] = 1609.344`. `test_peloton_connector.py::test_normalize_mi_account_converts_distance_correctly` feeds the issue's own raw value (`distance=13.1816`) and asserts `distance_m == pytest.approx(21213.7, rel=0.01)` — the Strava-confirmed ground truth. Real-data BO-run verification (Peloton↔Strava pairs) is explicitly out of scope for CI per the requirements doc — not re-attempted here. |
| 2. km-unit account: no regression | **PASS** | `_DISTANCE_UNIT_MULTIPLIERS["km"] = 1000.0`, unchanged value. `test_normalize_km_account_converts_distance_correctly` (`distance=21.2137` → `distance_m == pytest.approx(21213.7)`) and the updated `test_normalize_maps_cycling_class_with_power` (now explicitly supplies `_distance_unit="km"`) both pass. |
| 3. Unknown/missing unit → logged, `distance_m = None`, never guessed | **PASS** | `normalize()`: `if raw_distance is not None and multiplier is None: diagnostic_logger().warning(...); distance_m = None`. Verified independently (not just re-running the Developer's tests) by writing a standalone script that calls `normalize()` directly with `_distance_unit` set to `None`, an absent key, and an unrecognized token (`"furlongs"`) — all three produced `distance_m is None` and a logged warning containing `"distance"` and the external_id, confirmed via a `loguru` sink. A workout with a real `distance_unit="xyz"` and no plausible alias never falls back to either `mi` or `km`. |
| 4. One-off script re-derives `distance_m` from stored `raw_activities`, no re-fetch, no raw-payload mutation, idempotent | **PASS** | `scripts/renormalize_peloton_distance.py` calls `renormalize_provider(conn, PROVIDER, connector, raw_transform=lambda raw: {**raw, "_distance_unit": args.unit})` — only reads `raw_activities.payload_json`, never writes to it (confirmed by reading `renormalize_provider()`: its UPDATE targets `normalized_activities` only). `test_renormalize.py::test_renormalize_never_modifies_raw_activities` and `::test_renormalize_is_idempotent_on_second_run` both pass; `test_renormalize_applies_raw_transform_before_normalize` confirms the injected unit reaches `normalize()`. Independently confirmed by running the script's core call twice in sequence against a scratch SQLite DB seeded with 3 fake Peloton raw rows (`distance` values with no `_distance_unit` key) — first run produced correct `distance_m` for all 3, second run reported identical `updated` counts and unchanged `distance_m` values (no drift). |
| 5. BO's real-DB Peloton↔Strava pairs agree within 1% after running the script | **⏳ Not run (BO-run, by design)** | Explicitly scoped as BO-run against the real database in both the requirements doc ("the BO's own verification check against real data") and `docs/trainiq/roles/qa.md` ("Live-account testing boundaries" — CI/QA has no real Peloton/Strava account access). Nothing in this PR blocks that check; `--unit mi` is ready to run. |
| 6. `peloton_csv` unchanged, its tests still pass | **PASS** | Zero diff on `trainiq/csv_import/peloton_csv.py` (scope check above). `test_peloton_csv_import.py`: 8 of 10 tests pass; the other 2 fail identically on `origin/main` (see "Full test suite" below) — pre-existing, unrelated to this PR. |
| 7. Tests for mi/km/missing-unit accounts in `test_peloton_connector.py` | **PASS** | `test_normalize_mi_account_converts_distance_correctly`, `test_normalize_km_account_converts_distance_correctly`, `test_normalize_unresolved_unit_with_real_distance_yields_none_and_warns` (parametrized: `None` and `"furlongs"`), `test_normalize_missing_distance_unit_key_entirely_yields_none_and_warns`, plus `test_normalize_no_distance_at_all_yields_none_without_warning` (proves the "don't warn when there's nothing to convert" branch) and two `download()`-level tests confirming `_distance_unit` is attached to every workout dict, never omitted. All pass. |
| 8. Live-verified finding documented in `docs/trainiq/verification/` and `BACKLOG.md` | **PASS** | `docs/trainiq/verification/peloton-2026-09-28.md` has a dated 2026-10-09 addendum explaining the finding and the outstanding field-name verification gap; `projects/trainiq/BACKLOG.md` has a new `BL-010` entry, open, describing exactly what closes it. |

**6 of 8 acceptance criteria verified pass in CI/sandbox. AC1 and AC5 require the BO's real database and real Peloton↔Strava data and are explicitly scoped as BO-run, not CI/QA-run, by both the requirements doc and `docs/trainiq/roles/qa.md`.** The connector-level unit conversion AC1 depends on (`mi` → `distance_m` within 1% of Strava's figure) is independently verified above against the issue's own captured numbers; what remains is the BO re-running the real pairwise check, which is unaffected by anything in this PR.

## Known gap carried over from Dev (not a QA finding, flagged by the PR itself)

`ACCOUNT_DISTANCE_UNIT_FIELD = "distance_unit"` is the issue's own suggested
field name, not yet live-verified against a real `/api/me` response body
(the architecture doc's Task 1, a live diagnostic requiring the BO's bearer
token). This is visibly flagged in the code, the verification-doc addendum,
and `BACKLOG.md` BL-010 — it does not block this PR: by design, every piece
of this fix downstream of `_resolve_account_distance_unit()` is correct
regardless of what that future verification finds, and an unresolved unit
already fails safe (`NULL` + logged, per AC3) rather than silently guessing.
Leaving BL-010 open is the correct outcome here, not a defect to send back
to Dev.

## Full test suite (regression check)

```
cd projects/trainiq && PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring pytest -q
```

Result on PR branch (`1ad21a9`), in a clean venv built from `pyproject.toml`'s
`[dev]` extra: **445 passed, 2 failed**.

Both failures (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
`::test_real_csv_full_regression`) are `FileNotFoundError` on
`/home/claude/peloton_work/aimea75_workouts.csv` — a live-account CSV fixture
not present in this sandbox. Confirmed **pre-existing and unrelated**: running
`tests/test_peloton_csv_import.py` against `origin/main` (before this PR's
changes) reproduces the identical `2 failed, 8 passed`. This PR touches no
CSV-import code or test.

Targeted run of every file this PR touches or adds
(`test_peloton_connector.py test_renormalize.py
test_renormalize_peloton_distance_script.py`): **60 passed**.

## Mergeability

Test-merged `origin/issue-45-peloton-distance-unit` against current
`origin/main` (two commits ahead of this PR's base: #43 QA doc, #46
architecture doc, neither touching any file this PR changes) — clean
automatic merge, no conflicts.

## Verdict

✅ All CI-verifiable acceptance criteria (2, 3, 4, 6, 7, 8) pass, and AC1's
connector-level conversion logic is independently verified against the
issue's own captured numbers. AC1 and AC5's real-database pairwise check
remain correctly scoped as BO-run, per the requirements doc and QA role doc.
No regressions (pre-existing, unrelated test failures only, confirmed
present on `origin/main` too). PR #54 approved — ready for the BO to run
`scripts/renormalize_peloton_distance.py --unit mi` and verify AC5 against
the real database.
