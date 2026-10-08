# Requirements: Stop Treating Eufy's "Not Measured" Body-Fat Sentinel as Implausible

**Issue:** #42
**Related:** #38 (original plausibility rule), PR #41 (shipped it), ADR-039 (the rule this issue corrects)

## Summary

After #38/PR #41 went live on the BO's database and the first sync ran, the
`body_fat_pct <= 3.0` floor rule flagged 130 of 568 stored Eufy weigh-ins as
implausible — but only 7 of those are genuinely bad readings (the same 7
from #38's original evidence). The other 123 are valid 81–88 kg weight
readings that Eufy recorded with `body_fat_pct = 0.0` because the scale
didn't get an impedance reading that time (e.g. weighed with socks on), not
because the athlete's body fat is actually ~0%. The rule has no way today to
distinguish "measured as 0" from "not measured," so it treats the sentinel
as a genuine physiological-floor violation and flags the **entire record**
— hiding a perfectly good weight reading along with the meaningless
body-fat value. About 22% of the BO's weight history is currently excluded
from analytics as a result.

This is a correction to the rule ADR-039 documented, not a new rule: the
BO's own evidence (plus #38's original evidence) shows the floor axis and
the weight-deviation axis were meant to be independent signals, but today a
trip on the body-fat axis suppresses the weight value too, which was never
the intent.

## Scope

- Normalizing Eufy's `body_fat_pct = 0.0` to `NULL` ("not measured") instead
  of storing and evaluating it as a literal 0% reading. Per the BO's issue,
  check whether `muscle_mass_pct = 0.0` follows the same "not measured"
  sentinel pattern — see Open Questions; no live-verified evidence for
  `muscle_mass_pct` specifically is given in this issue, only a stated
  hypothesis that it "may follow the same pattern."
- The fix applies wherever this normalization happens for any WEIGH_IN-shaped
  connector (today: `EufyConnector.normalize()`), consistent with the
  provider-agnostic framing #38 already established for the plausibility
  rule itself.
- Decoupling body-composition implausibility from weight implausibility in
  `evaluate_weigh_in_plausibility()` / `_build_weigh_in_record()`: a
  missing (`NULL`) body-fat value must never cause the **weight** to be
  excluded from analytics. Weight plausibility is judged solely by the
  existing rolling-median weight-deviation axis.
- If a body-fat floor check is kept at all (the BO's criteria allow for
  keeping it), it must apply only to present, non-zero, non-null
  `body_fat_pct` values, and a trip on it must flag only the body-fat
  aspect of the record — it must never also exclude that record's weight
  from analytics.
- A corrective one-time pass that re-evaluates the BO's already-stored
  `weigh_ins` rows against the corrected rule, so the 123 incorrectly
  flagged readings become unflagged and only the original 7 sub-40 kg
  readings (from #38) remain flagged. This follows the same precedent as
  #38's own backfill (`backfill_weigh_in_plausibility()`, run automatically
  on a schema migration transition) — not a live reconnection to the BO's
  Eufy account, and testable with fixture data reproducing the BO's
  evidence (the 82.2 kg / 82.65 kg rows with `body_fat_pct = 0.0`, etc.).
- `bo_confirmed_valid` / `bo_confirmed_at` values on any row are never
  touched by this corrective pass — only the rule's own
  `is_flagged_implausible` / `plausibility_reason` columns.
- Updating ADR-039 to record this correction (the floor's scope, the
  missing-vs-zero distinction, and the decoupling from weight).
- Regression tests reproducing the BO's evidence: a reading like `82.2 kg,
  body_fat_pct 0.0` is not flagged and `body_fat_pct` is stored as `NULL`.

### Out of scope

- Redesigning the weight-deviation axis itself (rolling window size,
  minimum history, 25% threshold) — unaffected by this issue, already
  correctly leaves the 123 readings unflagged.
- Re-litigating whether a body-fat floor should exist at all — the BO's
  criteria explicitly allow keeping it, just scoped correctly.
- Live-account verification as part of the pipeline deliverable — the CI
  pipeline has no access to the BO's real Eufy account (same constraint as
  #38). The BO's evidence table in this issue is already live-verified
  ground truth for `body_fat_pct`; it is sufficient fixture data.
- Actually running the corrective pass against the BO's live production
  database — that's the BO's own follow-up action once this ships (same
  precedent as #38's migration-triggered backfill). This issue's deliverable
  is code that produces the correct result when that migration/backfill
  runs, verified against fixture data, not a live operations task.
- Any schema migration unrelated to this issue's own correction. A schema
  change is in scope only if the Architect's design for decoupling
  body-fat from weight flagging requires one (e.g. splitting
  `is_flagged_implausible` into field-level flags) — exact shape is the
  Architect's call, same precedent as ADR-039's own v3→v4 migration.
- `muscle_mass_pct` normalization, if the Architect/BO determine there's no
  evidence it needs the same fix (see Open Questions) — don't apply an
  unverified fix speculatively.

## Acceptance criteria

1. `EufyConnector.normalize()` normalizes `body_fat_pct = 0.0` to `NULL`
   before it reaches persistence or the plausibility rule — it is never
   stored or evaluated as a literal 0% reading.
2. A `NULL` (missing) body-composition value never causes a weigh-in's
   **weight** to be flagged or excluded from analytics. Weight plausibility
   is determined solely by the rolling-median weight-deviation axis.
3. If the body-fat floor check is retained, it is evaluated only against
   present, non-null, non-zero `body_fat_pct` values, and a trip on it
   flags only the body-fat aspect of the record, never the weight.
4. Re-running the corrective pass against fixture data reproducing the BO's
   full evidence table (568 rows: 7 genuinely-implausible sub-40 kg
   readings from #38, 123 readings of 81–88 kg with `body_fat_pct = 0.0`,
   and the remaining normal readings) results in exactly the same 7 rows
   flagged as #38 originally required, and 561 rows unflagged — the 123
   are no longer excluded from analytics.
5. The corrective pass does not modify any row's `bo_confirmed_valid` or
   `bo_confirmed_at` value.
6. Regression test: a reading of `82.2 kg` with `body_fat_pct = 0.0` is not
   flagged, and its persisted `body_fat_pct` is `NULL`.
7. Regression test: #38's original 7-row evidence (sub-40 kg readings,
   `body_fat_pct` 0.0 or 5.0) are still flagged — this fix must not weaken
   the original rule's detection of genuinely implausible readings.
8. ADR-039 is updated to describe the corrected rule: the missing-vs-zero
   distinction for `body_fat_pct`, and that a body-composition flag never
   suppresses the weight value.
9. All existing Eufy connector, normalization, plausibility, and backfill
   tests continue to pass (adjusted only where they asserted the old,
   incorrect behavior).

## Open questions

- Does `muscle_mass_pct = 0.0` from Eufy actually mean "not measured," the
  same way `body_fat_pct = 0.0` does? The issue states this only as an
  unconfirmed hypothesis ("if it follows the same pattern"), unlike
  `body_fat_pct`, for which the BO has live-verified evidence (the 123-row
  evidence table). Per this project's evidence-based principle, this needs
  either the BO confirming it from their real data, or an explicit decision
  to apply the same normalization defensively without live confirmation.
  Not blocking handoff — Architect/BO can decide either way, but should not
  be silently assumed.
- Exact mechanism for decoupling body-fat flagging from weight flagging
  (e.g., splitting `is_flagged_implausible`/`plausibility_reason` into
  per-field columns, vs. some other representation) — delegated to the
  Architect, same precedent as #38's "Open questions" delegating exact
  schema/threshold decisions.
- Whether the corrective pass is a new schema migration step (e.g. v4→v5)
  or a re-run of the existing backfill function with the corrected rule —
  Architect's call; either satisfies AC4 as long as it's testable against
  fixture data and automatically applied (not a manual script the BO must
  remember to run), consistent with #38's precedent.
