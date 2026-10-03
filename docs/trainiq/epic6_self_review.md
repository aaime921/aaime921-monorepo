# TrainIQ — Epic 6 Internal Architectural Review

**Author role:** self-review, acting as a senior software architect auditing already-written code — not the implementer. Per the working rules set at Epic 6's start: a genuine self-review before declaring the epic complete.
**Scope:** all five slices of Epic 6 (record_kind, taxonomy, confidence, training load, routing) plus the AthleteProfile relocation.

---

## The headline finding: a malformed record could crash the entire batch

**Classification: Major, bug — found and fixed in this review, not just flagged.**

`build_canonical_record()` correctly indexes required fields directly (`normalized["external_id"]`, `normalized["start_time"]`, `normalized["duration_s"]` for ACTIVITY records) rather than using `.get()` — correct, because `normalized_activities` genuinely declares these `NOT NULL`. But nothing in `sync_connector()`'s per-record loop caught the `KeyError` this raises for a malformed record. I reproduced it before writing anything down: a connector returning a record missing `start_time` raised an uncaught `KeyError` that propagated through `run_once()`'s list comprehension (`[self.sync_connector(c) for c in connectors]`) and **crashed every other connector in the same batch** — a healthy connector positioned after the broken one in the list never even ran.

This is a direct regression against ADR-009 (Graceful Degradation), introduced by this epic, not inherited from before it — before slice 5, nothing in the per-record loop could raise an exception the way `build_canonical_record()`'s required-field indexing now can.

**Fixed in this review:** the per-record canonical-building step is now wrapped in a targeted `except (KeyError, TypeError, ValueError)`, logging the malformed record and continuing — the raw payload is still preserved in `raw_activities` (per Milestone 4 §5's reprocessing rationale), only the canonical record is skipped. Verified by a new reproduction-based regression test, `test_malformed_record_does_not_crash_the_run_or_other_connectors`, which is the direct executable form of the bug I found manually first.

**Why I fixed this immediately rather than only flagging it:** this isn't a new capability or a policy question requiring a Chief Architect decision — it's a correctness bug restoring a guarantee (ADR-009) the project already approved five months ago. That's the same category Epic 0's Hardening treated as fix-immediately, not stop-and-raise.

---

## Standard review questions

**1. Constitution consistency:** Held throughout — Principle 1 (never fabricate) is the connecting thread across all five slices (Unknown discipline → OTHER, Unknown load → null+reason, Unknown confidence never happens because it's always computable). The malformed-record bug didn't violate a Constitution principle directly, but it would have undermined Principle 7 (quality gates before features) if shipped — a feature that crashes under real-world malformed input isn't "done" just because its happy path is tested.

**2. Unapproved assumptions:** None found beyond what's already logged in BACKLOG.md (BL-009). The AthleteProfile relocation was checked against "model must stay pure" — confirmed: it's a bare dataclass, five optional fields, no methods, no persistence.

**3. Breaking changes for future epics:** None for Epic 7 specifically — `compute_training_load()` takes `AthleteProfile | None` already; Epic 7 supplying a real one requires zero changes to Epic 6 code, exactly as designed. BL-009 remains the one open item (a schema migration, not a breaking change).

**4. Is the single-writer invariant (Chief Architect's explicit post-slice-5 rule) actually enforced, not just true today?** Now yes, structurally: `test_only_synchronization_engine_writes_to_normalized_activities_or_weigh_ins` and `test_only_synchronization_engine_calls_build_canonical_record` scan the actual repository source for any second write path or call site. Written proactively in this review, before being asked, because a rule that's "true today" and a rule that's "enforced" are different guarantees — the Chief Architect's phrasing described the former; I upgraded it to the latter.

**Caveat, added after Chief Architect review of this document — worth stating precisely rather than letting the claim above stand overstated:** these tests are a static-analysis guardrail, not a mathematical proof of single-writer uniqueness. They catch a second `INSERT INTO` or a second direct call to `build_canonical_record()` appearing anywhere in the source — the accidental-shortcut failure mode this rule exists to prevent. They do not catch a future abstraction layer or helper that reaches the same tables through indirection the static check doesn't follow. That's a structural limit of this class of check, not a defect in it: the tests raise the bar well above relying on developer discipline alone, but a human reviewer noticing a new abstraction touching these tables remains the actual backstop, not something this automated check can replace.

**5. Does Epic 6 make Epic 7 straightforward?** Yes, more concretely than before this review: the malformed-record fix means Epic 7's eventual `AthleteProfile` loader can be wired in via the existing `athlete_profile` constructor parameter without touching any per-record error handling — that path is now robust independent of whether a profile is supplied.

**6. Technical debt intentionally accepted:**

| # | Item | Classification | Status |
|---|---|---|---|
| 1 | `weigh_ins` has no `source_confidence` column | Minor | Open (BL-009), correctly scoped out of slice 5 |
| 2 | Malformed record crashing the batch | Major | **Fixed in this review** |

**7. Is there anything else resembling finding #2 — other unguarded direct-indexing introduced by this epic?** Checked: `_build_weigh_in_record()` also indexes `normalized["external_id"]` and `normalized["timestamp"]` directly. Same NOT NULL justification applies (`weigh_ins.timestamp TEXT NOT NULL`), and it's covered by the same try/except at the call site in `sync_connector()`, since that wraps `build_canonical_record()` generically regardless of which internal branch it takes. No second instance of the bug found.

---

## Recommendation

Close Epic 6. The one remaining item (BL-009) is correctly scoped as a future schema migration, not a defect in what this epic claims to do. The malformed-record finding was serious enough that I would not have recommended closing the epic with it unfixed — it's fixed, tested, and the fix is now a permanent regression test rather than a one-time patch.
