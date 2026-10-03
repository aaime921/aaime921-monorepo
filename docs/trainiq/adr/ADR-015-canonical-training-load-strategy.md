# ADR-015 — Canonical Training Load Strategy

**Status:** Accepted (Phase 0B, Milestone A — Normalization Engine)
**Origin:** Cross-provider comparability research found that TSS (power-based) offers the highest precision but the lowest data coverage across TrainIQ's actual providers, while TRIMP (heart-rate-based) offers lower precision but far higher coverage.

## Context

Coverage matters more than precision for a system that must produce a comparable number across Strava, Peloton, and (indirectly) Eufy data, much of which lacks power data entirely.

## Decision

TRIMP is the primary cross-provider training-load metric. TSS is computed opportunistically as an enhancement when power and FTP are both available. The internal model does not depend on TSS.

## Consequences

- `compute_training_load()` (Epic 6) implements exactly this selection order: TSS if power+FTP present, else TRIMP if HR+baseline present, else Unknown (ADR-016).
- Both methods are recorded via `training_load_method`, so a TSS-derived and a TRIMP-derived value are never silently treated as equivalent.
