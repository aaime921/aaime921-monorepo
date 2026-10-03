# TrainIQ — Architecture Debt Review

**Method:** audit only. No code was changed to produce this document. Every finding below was verified against the actual repository — line counts, method inventories, import graphs, and cyclomatic complexity were measured, not estimated.
**Guiding question, as posed:** if this project is maintained for another five years, where does it start to get difficult?

---

## Finding 1 (the most significant): `SynchronizationEngine` has accumulated too many responsibilities

`trainiq/sync/engine.py` is 523 lines — the largest module in the codebase by a wide margin (the next largest, `synthetic_dataset.py`, is a data-generation script, not orchestration logic). `SynchronizationEngine` alone owns:

- Checkpoint persistence (`get_checkpoint`, `_set_checkpoint`)
- Three separate persistence paths (`_upsert_raw_activity`, `_upsert_normalized_activity`, `_upsert_weigh_in`) plus a router (`_persist_canonical_record`)
- Lifecycle state load/record (`_load_lifecycle_state`, `_record_attempt_start`, `_record_lifecycle_outcome`)
- Retry policy execution (`_with_retries`)
- Top-level orchestration (`sync_connector`, `run_once`)

**Measured, not estimated:** `sync_connector()` itself is 154 lines with an approximate cyclomatic complexity of 16 (branch-counted via AST — `if`/`for`/`while`/`except`/boolean-operator nodes). A commonly cited rule of thumb treats 10+ as "worth reviewing" and 20+ as high-risk; this method is trending toward the latter, not comfortably under the former.

**Why this matters over five years, not today:** every new connector capability (a fourth `RecordKind`, a new persistence target, a change to retry semantics) has one obvious place to go — inside this already-large method — which is exactly how methods grow from 154 lines to 300. Nothing is broken today; the risk is compounding, not present.

**Not fixed here.** A plausible future direction (not a recommendation, since this review's scope is audit only) would be extracting the three concerns — persistence routing, lifecycle bookkeeping, retry policy — into collaborator objects `SynchronizationEngine` composes rather than implements directly, mirroring how `lifecycle_policy.py` was already successfully extracted as a pure function during ADR-038's implementation.

## Finding 2: the two-`RecordKind` assumption is hardcoded in three separate places

`RecordKind.ACTIVITY` vs. `RecordKind.WEIGH_IN` branching exists independently in `trainiq/normalization/confidence.py`, `trainiq/normalization/engine.py`, and `trainiq/sync/engine.py` — three `if/else` statements, not one dispatch point. This works cleanly for exactly two record kinds. It does not scale as a *pattern*: a third kind (sleep data, subjective wellness, anything Epic 8+ might add) requires finding and editing three call sites, each independently, with no compiler or test currently positioned to catch a missed one.

**Not a defect today** — both branches are correct and fully tested. This is a scalability observation about the *shape* of the abstraction, not a bug in its current instance.

## Finding 3 (downgraded per Chief Architect review — naming inconvenience, not debt): `engine.py` exists twice, in different packages

**Revised classification:** on reflection, this doesn't warrant the same weight as the other findings — two same-named files in different packages is a normal, unambiguous Python pattern, not technical debt. Recorded here only as a minor naming inconvenience, not a maintainability risk: `trainiq/sync/engine.py` and `trainiq/normalization/engine.py` mean "the sync engine" and "the normalization engine" cannot be shortened to "the engine" in conversation or documentation without ambiguity. Worth knowing, not worth ranking alongside Findings 1–2.

## Finding 4: a small number of tests couple to private implementation details

`test_strava_connector.py` calls `connector._activity_to_raw_dict()` directly (four call sites) and `scripts/benchmark.py` reaches into `connector._activities`. Both are underscore-prefixed, explicitly private. Testing through private methods isn't wrong when the alternative is significantly more test setup — but it means a future internal refactor of those private helpers (renaming, restructuring, splitting) breaks tests even when the connector's actual public contract (`authenticate`/`download`/`normalize`) is unchanged. This is the kind of thing that erodes confidence in "the tests are red, something's wrong" over time, once a few such couplings accumulate.

## Finding 5: one test asserts on exact wording, not behavior

`test_peloton_connector.py::test_request_manual_recovery_returns_real_instructions_not_the_default_stub` asserts `"Bearer Token" in message`. If that user-facing string is ever reworded for clarity (a copy change, not a behavior change), this test breaks despite nothing being wrong. Low-severity, single occurrence — but worth naming as the one place a purely cosmetic change has an outsized chance of triggering a false test failure.

## Finding 6: dynamic SQL construction via string formatting — a pattern to monitor, not a fragile one

**Revised framing per Chief Architect review:** the original wording ("pattern-fragile") risked reading as a near-vulnerability, which overstates the actual risk. `trainiq/athlete/store.py` builds its `INSERT OR REPLACE` statement by joining a hardcoded tuple of column names into an f-string. `_COLUMNS` is a fixed, code-defined constant, never derived from external or user input — there is no concrete risk today, full stop. This is recorded as a pattern worth *monitoring*, specifically: if this shape is ever copied to a context where the column/table list comes from something less static, that would be the moment to revisit it. Not a current weakness in this codebase.

## Positive findings, worth recording alongside the debt (a debt review that only lists problems overstates the debt)

- **No connector imports another connector.** Grepped all three (`strava.py`, `eufy.py`, `peloton.py`) — each imports only from `connectors.base`. The "thin, independent connector" property this project has repeatedly claimed is structurally true, not just asserted.
- **No credential value is ever passed to a logger.** Checked every `diagnostic_logger()`/`summary_logger()` call site in all three connectors and in `CredentialStore` itself — none references a token, password, or secret variable directly. (The one residual risk in this area — exception stringification — is covered in the companion Security Review, not repeated here.)
- **`normalization/` and `connectors/` have zero database access between them** (re-verified in this review, not just carried forward from Epic 6's self-review) — the layering this project has repeatedly claimed is, again, structurally enforced, not aspirational.
- **`lifecycle_policy.py`'s extraction as a pure function (ADR-038) is the one place this project already did what Finding 1 recommends elsewhere** — proof the pattern works when applied, not just a theoretical suggestion.

---

## Summary — where this project gets difficult in five years, ranked

1. `SynchronizationEngine`'s accumulated responsibilities (Finding 1) — the one item worth genuine attention before it grows further, not because anything is broken, but because every future connector/persistence/retry change has nowhere natural to go except into an already-large method.
2. The `RecordKind` dispatch pattern (Finding 2) — low urgency until a third kind is actually proposed, at which point it becomes the natural moment to address.
3. Everything else (Findings 3–6) — real, worth knowing, not worth interrupting release engineering for.

**No code was changed in the production of this document.**
