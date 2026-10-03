# Requirements: Eufy weight values are 10x too high (unit conversion bug)

Issue: #1

## Summary

`EufyConnector.normalize()` (`trainiq/connectors/eufy.py:288`) currently
passes the raw Eufy API `scale_data.weight` value straight through into
`weight_kg` with no unit conversion. Live-account evidence captured on
2026-09-27 shows real `scale_data.weight` values like `829.5` and `883.5`,
which are not physically possible as kilograms for a human weigh-in.
Dividing these by 10 produces `82.95` and `88.35`, which are plausible adult
body weights — indicating the raw field is actually in deci-kilograms
(0.1 kg units), not kilograms. Every existing unit test for this field uses
already-plausible fixture values (e.g. `75.4`, `80.0`), so this scaling
error has never been exercised against real Eufy payload magnitudes and is
currently corrupting every weigh-in the Coach engine sees via Eufy. This
directly undermines the project's evidence-based-recommendation principle,
since downstream consumers (normalization, sync engine, Coach) have no way
to know the value is wrong.

## Scope

- Convert the raw `scale_data.weight` value from deci-kilograms to
  kilograms (divide by 10) inside `EufyConnector.normalize()` before it is
  assigned to `weight_kg`.
- Preserve `None`/missing-value handling: if `scale_data.weight` is absent,
  `weight_kg` must remain `None` (or whatever the existing "field absent"
  behavior is), not `None / 10` or a crash.
- Audit the other `scale_data` fields already mapped in `normalize()` —
  `body_fat` → `body_fat_pct` and `muscle_mass` → `muscle_mass_pct` — to
  confirm whether they need the same deci-unit correction. Per the reporter's
  own observation, these looked correctly scaled already (plausible
  percentage/BMI-range values) in the captured evidence, but that should be
  verified against the same captured payloads rather than assumed, and
  documented either way (corrected, or confirmed-and-left-alone with a
  one-line note of why).
- Note (do not act on, unless trivially in scope) for a future epic: the
  currently-unmapped `scale_data` fields `standard_weight`, `water_weight`,
  `fat_free_weight`, and `body_fat_mass` may share the same deci-kilogram
  scaling. Flag this for whoever maps them later; mapping those fields is
  out of scope for this issue.
- Add a regression test in `tests/test_eufy_connector.py` using the exact
  real captured values from the issue (`scale_data.weight = 829.5` →
  `weight_kg == 82.95`, and `scale_data.weight = 883.5` → `weight_kg ==
  88.35`), so this cannot silently regress.

### Out of scope

- Any correction to already-synced/stored historical `weigh_ins` rows in a
  live database — no live account or database access is available to this
  pipeline, and no such rows exist in this codebase's test/dev environment.
  If a backfill/migration of previously-synced-and-corrupted data is needed
  in a real deployment, that is a separate, human-directed operational task,
  not part of this fix.
- Mapping the additional unused `scale_data` fields (`standard_weight`,
  `water_weight`, `fat_free_weight`, `body_fat_mass`) — flagged above for a
  future epic only.
- Any change to `body_fat_pct` or `muscle_mass_pct` beyond the verification
  audit described above, unless that audit finds an actual scaling bug in
  them.

## Acceptance criteria

1. Given a raw Eufy record with `scale_data.weight = 829.5`,
   `EufyConnector.normalize()` returns `weight_kg == 82.95` (within
   floating-point tolerance).
2. Given a raw Eufy record with `scale_data.weight = 883.5`,
   `EufyConnector.normalize()` returns `weight_kg == 88.35` (within
   floating-point tolerance).
3. Given a raw Eufy record where `scale_data` has no `weight` key (or
   `scale_data` itself is absent), `EufyConnector.normalize()` returns
   `weight_kg is None` — no exception, no `None`-divided-by-10 error.
4. All existing `weight_kg`-related assertions in
   `tests/test_eufy_connector.py` (and any other test asserting on
   `weight_kg` from an Eufy-sourced record) are updated to fixture values
   that are consistent with the corrected deci-kg-to-kg conversion, and the
   full existing suite for this connector still passes.
5. A new regression test exists in `tests/test_eufy_connector.py` that
   asserts on the exact real captured values from this issue (829.5 → 82.95
   and 883.5 → 88.35), so a future accidental removal of the conversion is
   caught by CI.
6. The requirements/handoff comment (or a code comment near the fix)
   documents the outcome of the `body_fat_pct`/`muscle_mass_pct` scaling
   audit — either "confirmed already correctly scaled, no change needed" or
   the correction applied, with the reasoning.
7. No other field mapped in `normalize()` (`external_id`, `timestamp`) is
   altered by this change.

## Open questions

None that block the Architect/Developer. The reporter explicitly
acknowledged that no live Eufy account access is available to the automated
pipeline, and provided enough captured real-payload evidence (exact
before/after values) to implement and test the fix without it. The
`body_fat_pct`/`muscle_mass_pct` scaling audit and the four currently-unused
`scale_data` fields are flagged above as things to verify/note, not open
questions that block starting this work.
