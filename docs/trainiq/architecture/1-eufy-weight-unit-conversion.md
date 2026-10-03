# Architecture: Eufy weight values are 10x too high (unit conversion bug)

Issue: #1
Requirements: [`docs/requirements/1-eufy-weight-unit-conversion.md`](../requirements/1-eufy-weight-unit-conversion.md)

## Approach

`EufyConnector.normalize()` (`trainiq/connectors/eufy.py`) currently assigns
`scale_data.get("weight")` straight to `weight_kg` with no conversion.
Live-account evidence (issue #1) shows raw values of `829.5` / `883.5`,
which are only plausible as human body weights once divided by 10
(`82.95` / `88.35`) — i.e. the raw field is in deci-kilograms, not kg.

Fix: divide the raw weight by a named constant `10` (deci-kg → kg) inside
`normalize()`, guarding for `None` so a missing/absent weight still yields
`weight_kg is None` rather than a `TypeError` from `None / 10`. This is a
pure, local arithmetic fix at the single point of extraction — no schema,
interface, or downstream (`normalization/`, `sync/engine.py`) change is
needed, since everything downstream already consumes `weight_kg` assuming
it is correctly-scaled kg (see `tests/test_confidence.py` and
`tests/test_normalization_engine.py`, which construct already-normalized
dicts directly and never re-derive from raw Eufy payloads).

**`body_fat_pct` / `muscle_mass_pct` audit (requirements §Scope, AC6):**
the issue's reporter directly observed, from the same live-captured
payloads that exposed the weight bug, that the accompanying `body_fat`,
`bmi`, and `muscle_mass` values were already in normal percentage/BMI
ranges — i.e. plausible without any scaling correction. No live account
access is available to this pipeline to re-verify beyond that reported
observation, and no raw payload dump exists in-repo (`scripts/debug_eufy.py`
writes to a local, gitignored `debug/` path, not committed). Given that
evidence, and that the requirements doc treats it as sufficient to decide
without blocking on `needs:human`, the conclusion is: **`body_fat_pct` and
`muscle_mass_pct` are confirmed already correctly scaled — no change.**
The Developer should add a one-line code comment recording this
(the "documented either way" requirement in AC6) rather than re-opening
the question; do not add a scaling divisor for these two fields.

## Affected components/files

- `trainiq/connectors/eufy.py` — `normalize()`, plus a new module-level
  constant. This is the only production code change.
- `tests/test_eufy_connector.py` — existing fixtures that pass a
  scale_data `weight` value and assert on the resulting `weight_kg` need
  their raw input updated (see below), and one new regression test using
  the issue's exact real values, plus one new test for the missing-weight
  case.
- No other file changes. `tests/test_confidence.py` and
  `tests/test_normalization_engine.py` construct already-normalized
  `weight_kg` dicts directly (never call `EufyConnector.normalize()`), so
  they are unaffected — confirmed by inspection, not assumed.
  `tests/test_sync_engine.py`'s `MockAdversarialWeighInConnector` is an
  unrelated mock connector (provider `"adversarial"`) with its own
  `normalize()` that maps `raw["weight"]` directly — it is not
  `EufyConnector` and must not be touched.

## Interfaces/contracts

No public interface changes. `normalize()`'s signature and return-dict
shape (keys: `provider`, `external_id`, `timestamp`, `weight_kg`,
`body_fat_pct`, `muscle_mass_pct`) are unchanged; only the *value* computed
for `weight_kg` changes.

Add, near the other module-level constants in `trainiq/connectors/eufy.py`
(alongside `EUFY_APP_CLIENT_ID` etc.):

```python
# Issue #1: verified against a real Eufy account (2026-09-27) — raw
# scale_data.weight values (e.g. 829.5, 883.5) are only plausible as human
# body weights once divided by 10, indicating the field is in
# deci-kilograms (0.1 kg units), not kilograms. body_fat/muscle_mass were
# separately observed to already be correctly scaled in the same captured
# payloads — this divisor applies to weight only.
WEIGHT_DECI_KG_TO_KG_DIVISOR = 10
```

`normalize()` becomes (only the `weight_kg` line changes):

```python
def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
    scale_data = raw.get("scale_data") or {}
    raw_weight = scale_data.get("weight")
    return {
        "provider": PROVIDER,
        "external_id": str(raw["id"]),
        "timestamp": raw["create_time"],
        "weight_kg": (
            raw_weight / WEIGHT_DECI_KG_TO_KG_DIVISOR if raw_weight is not None else None
        ),
        "body_fat_pct": scale_data.get("body_fat"),  # confirmed correctly scaled, see module docstring
        "muscle_mass_pct": scale_data.get("muscle_mass"),  # confirmed correctly scaled, see module docstring
    }
```

## Task breakdown

1. Add the `WEIGHT_DECI_KG_TO_KG_DIVISOR` constant to
   `trainiq/connectors/eufy.py`, with a comment citing issue #1's evidence
   (as above).
2. Update `normalize()` to divide `scale_data.get("weight")` by that
   constant, guarding `None` so a missing/absent weight stays `None`
   (never `None / 10`).
3. Add a one-line comment next to `body_fat_pct`/`muscle_mass_pct` in
   `normalize()` recording the audit outcome ("confirmed already
   correctly scaled, no change" — see Approach above). Also add/update
   the module docstring's Feature 2.2 paragraph to mention this fix and
   the audit outcome, consistent with how prior corrections (e.g.
   RC1-HF-003) are documented there.
4. Update existing fixtures in `tests/test_eufy_connector.py` so raw
   `scale_data["weight"]` inputs are 10x the intended (already-correct)
   `weight_kg` expectation — the expected `weight_kg` values themselves
   are unchanged, only the raw input each test scripts:
   - `test_normalize_maps_full_record`: raw `weight` `75.4` → `754`
     (expected `weight_kg` stays `75.4`).
   - `test_normalize_never_fabricates_missing_body_composition_fields`:
     raw `weight` `80.0` → `800` (expected `weight_kg` stays `80.0`).
   - `test_sync_no_longer_reports_zero_records_against_the_real_payload_shape`:
     raw weights `82.3, 81.9, 81.8` → `823, 819, 818` (expected
     `weigh_ins.weight_kg` stays `82.3, 81.9, 81.8`).
   - `test_end_to_end_sync_via_synchronization_engine`: raw weights
     `75.0, 74.9` → `750, 749` (expected `weigh_ins.weight_kg` stays
     `75.0, 74.9`).
   - `test_extract_resume_cursor_always_returns_a_str` asserts only on the
     cursor, not on `weight_kg` — no change strictly required by the
     requirements' AC4, but update its raw `weight` `80.0` → `800` anyway
     for consistency with the other fixtures in the file (low priority,
     doesn't affect what the test verifies).
5. Add a new test asserting `weight_kg is None` when `scale_data` has no
   `weight` key, and a second when `scale_data` (or the whole key) is
   absent from `raw` entirely — covers requirements AC3. Confirm no
   exception is raised.
6. Add the regression test named in the requirements doc (AC1/AC2/AC5),
   using the issue's exact real captured values:
   `scale_data.weight = 829.5` → `weight_kg == pytest.approx(82.95)`, and
   `scale_data.weight = 883.5` → `weight_kg == pytest.approx(88.35)`.
   Use `pytest.approx` (already imported via `pytest`) given floating-point
   division, matching the requirements doc's "within floating-point
   tolerance" wording.
7. Run the full `tests/test_eufy_connector.py` suite (and
   `test_confidence.py` / `test_normalization_engine.py`, to confirm they
   are genuinely untouched by this change) and confirm everything passes.

## Test strategy notes

- Unit-level: `EufyConnector.normalize()` in isolation, covering the
  conversion, the `None`-guard, and the exact real-value regression case
  — all directly achievable with the existing fixture style in
  `tests/test_eufy_connector.py` (no live account needed).
- Integration-level: the two existing end-to-end tests that already run
  raw payloads through `SynchronizationEngine.run_once()` and assert on
  the `weigh_ins` table's `weight_kg` column continue to exercise the fix
  through the real storage path once their fixtures are updated (step 4
  above) — no new integration test is needed, updating the existing ones
  is sufficient.
- No manual/live-account verification is possible or required here — the
  issue's captured real-payload evidence stands in for it, per the BA's
  explicit note that this doesn't block the Architect/Developer.

## Risks/tradeoffs

- **Silent unit assumption.** The `10` divisor is inferred from two data
  points (829.5→82.95, 883.5→88.35) reported by the account owner, not from
  Eufy API documentation (none is known to exist for this undocumented
  endpoint). If a future live sync surfaces a value where dividing by 10
  produces an implausible result (e.g. consistently under 20 kg or over
  300 kg), that would falsify this assumption and should be raised as a
  new issue rather than silently re-adjusted — this design doesn't add
  runtime plausibility-range validation, since the requirements doc scopes
  this issue to the conversion fix itself, not general input validation.
- **No backfill.** Any weigh-ins already synced into a real deployment's
  `weigh_ins` table under the old, unconverted logic stay wrong until a
  human runs a separate corrective migration — explicitly out of scope
  per the requirements doc (no live DB access from this pipeline).
- **`body_fat_pct`/`muscle_mass_pct` audit relies on a secondhand
  observation**, not a payload this pipeline can inspect directly. This is
  the best evidence available without live account access; if it later
  proves wrong, that's a new, separate bug, not a defect in this fix.
