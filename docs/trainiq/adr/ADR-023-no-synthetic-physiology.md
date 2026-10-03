# ADR-023 — No Synthetic Physiology

**Status:** Accepted (Phase 0B, Milestone C — Analytics Engine)
**Origin:** Direct extension of ADR-016's "never fabricate" principle to the question of which metrics to build at all, not just how to handle missing data within a metric already chosen.

## Context

VO2max, HRV readiness, and sleep readiness are all real, well-evidenced concepts in sports science generally — but TrainIQ has no data source (no lab test, no HRV sensor, no sleep tracker) that could compute them honestly.

## Decision

TrainIQ does not construct physiological indicators when the necessary underlying data does not exist. Showing less information is preferable to producing apparent precision the data cannot support.

## Consequences

- VO2max, HRV readiness, and sleep readiness are permanently excluded from V1's Analytics Engine — not because they are uninteresting, but because no honest computation path exists.
- If a future connector ever supplies HRV, sleep, or lab-grade fitness data, this ADR would need to be revisited explicitly, not silently overridden.
