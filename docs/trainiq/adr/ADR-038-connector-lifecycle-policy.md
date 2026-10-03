# ADR-038 — Connector Lifecycle Policy

**Status:** **Implemented** (Epic 3). Approved after two review rounds (see revision notes throughout). Implementation surfaced three additional findings beyond the ADR's original scope — a missing state-restoration mechanism (Connector state was never loaded from the database on startup, which would have made this entire ADR's premise moot across app restarts), an off-by-one in the initial backoff-cadence indexing (caught before shipping, by re-deriving the mapping from this document's own §4 rather than trusting the first implementation), and a latent illegal-state-transition bug in `RecoveryRequired` handling that had never been exercised before this ADR made `RecoveryRequired` connectors retry-eligible. All three are documented in detail in `BACKLOG.md`'s closed BL-007 entry. A fifth verification, requested after initial review — **Restart Transparency** (a restart doesn't just preserve state, it produces byte-for-byte identical `connector_state` evolution compared to an uninterrupted run) — is verified by `test_adr038_restart_transparency_connector_state_evolves_identically`. 142 tests passing project-wide at the time this ADR was closed (Epic 3) — see the project's current test count in `TrainIQ_Acceptance_Review.md` or by running the suite directly, since this number is a historical snapshot, not a live figure.
**Raised during:** Epic 3 implementation (Peloton connector), promoted from backlog (BL-007) to ADR per Chief Architect direction, following the same "stop before implementing" discipline established for ADR-037.
**Decision owner:** Chief Architect.
**Naming note:** deliberately titled "Connector Lifecycle Policy," not "Recovery Scheduling," per the Chief Architect's explicit direction — this should have room to grow into richer lifecycle states in the future without needing to be renamed.

---

## 1. Problem

`SynchronizationEngine.sync_connector()` currently treats any connector not in `Healthy` or `Warning` as permanently skipped, forever, with no re-attempt mechanism of any kind. This was verified directly, not inferred: a test simulating six consecutive daily sync runs against a connector stuck in `Degraded` produced exactly one real authentication attempt, ever (BL-007).

This is a gap in the framework, not in any connector. `Warning` connectors already get retried every run correctly (they were never affected by this gap — only `Degraded` and `RecoveryRequired` are). The gap specifically prevents:

- A `Degraded` connector (auth failure — wrong credentials, or a provider-side outage like Peloton's documented Oct 2025–Jan 2026 breakage) from ever discovering that the underlying problem cleared on its own.
- The roadmap's own Feature 3.2 DoD — "wait 7-14 days, then surface manual recovery" — from being implementable at all, since nothing would attempt re-authentication during that window to know whether escalation to `RecoveryRequired` is warranted.
- `RecoveryRequired` connectors from ever picking up a manually-supplied recovery token (e.g. Peloton's bearer token) without a full app restart forcing a fresh connector instance.

## 2. Ownership

**The Synchronization Engine owns lifecycle/retry-cadence decisions. Connectors remain completely unaware of scheduling.** This follows the same test already applied to `extract_resume_cursor()` (BL-005) and the retry-timing question that produced ADR-037: does answering this question require provider-specific knowledge? It doesn't — "how long has this connector been Degraded" and "how many attempts has it had" are facts about connector *state*, which the Sync Engine already tracks, not facts about any specific provider's API.

A second, independent reason specific to this decision: Milestone 2 §7 flagged that hammering a provider's auth endpoint with automated retries is itself a behavioral/ToS risk, separate from whether the retries succeed. Centralizing retry cadence in one place means one auditable policy applies uniformly to every connector, rather than each connector author independently choosing a cadence — which the Chief Architect specifically named as one of the Foundation's core benefits worth protecting.

**What stays connector-owned, unchanged:** whether a *given* re-attempt is cheap or expensive. Peloton's `authenticate()` already short-circuits with zero network calls when in `RecoveryRequired` with no manual token yet supplied (verified by `test_authenticate_in_recovery_required_with_no_token_yet_returns_false`). This observation motivates the *default value* chosen for `RecoveryRequired`'s retry-cadence parameter in Section 4 (see that section's revision note) — it is not treated here as a Foundation-level guarantee that every connector's `RecoveryRequired` check is free, since that would be exactly the provider-specific leak this ADR exists to prevent.

## 3. Required Persisted State

`connector_state` (currently: `provider`, `state`, `updated_at`, `detail`) needs to distinguish two different kinds of "when" that are currently conflated into one `updated_at` field:

| Field | Semantics | Written when |
|---|---|---|
| `state` | unchanged | unchanged |
| `state_entered_at` (NEW) | when the *current* state was actually entered | only on a real transition — a no-op reconfirmation (e.g. re-attempting and still failing) does NOT reset this |
| `last_attempt_at` (NEW) | when the connector was last actually tried, regardless of outcome | every attempt, success or failure |
| `attempt_count_in_state` (NEW) | how many attempts have occurred since `state_entered_at` | incremented immediately before each attempt executes (see Section 4's precision note) — reset to 0 on any real transition |
| `next_eligible_retry_at` (NEW) | when the Sync Engine may next attempt this connector | **always derived**, `last_attempt_at + cadence(attempt_count_in_state)`, computed immediately after every attempt — never manually set (see Section 4's precision note) |
| `detail` | unchanged | unchanged |

This directly answers the Chief Architect's requirement that the ADR define "quali timestamp esistono; quando vengono aggiornati; cosa succede dopo un tentativo fallito; cosa succede dopo un tentativo riuscito; se il contatore tentativi viene azzerato; se il timer riparte" — each is a specific field/rule above, not left implicit.

## 4. Retry Policy Semantics

**Two genuinely different mechanisms already exist or are being proposed here — worth being explicit they don't merge:**
- **ADR-037 (existing):** intra-run retry timing for a `TransientError` during a single `sync_connector()` call — seconds-to-minutes scale, governed by `retry_after_s` or the generic exponential backoff.
- **ADR-038 (this ADR):** inter-run retry *eligibility* — hours-to-days scale, governs whether the Sync Engine attempts a `Degraded`/`RecoveryRequired` connector *at all* on a given `run_once()` call.

**Proposed policy (defaults, explicitly flagged as calibratable — same discipline already applied to every literature-derived threshold in this project, e.g. Milestone C's monotony/ACWR bands):**

**Revision note (Chief Architect review, round 2):** the previous revision left an internal contradiction — Section 2 states the policy is centralized, while the original Section 4 said a future connector "is configured with a different cadence value," implying per-connector policy variation the Non-Goals section then contradicted by ruling out. Resolved per the Chief Architect's explicit model:

```
Sync Engine
    ↓
LifecyclePolicy (one, centrally owned)
    ↓
Policy parameters (one set, applied uniformly)
```

**Not:**

```
Connector
    ↓
decides its own retry cadence
```

**v1 ships exactly one `LifecyclePolicy`, with exactly one set of parameters (below), applied identically to every connector — Strava, Eufy, and Peloton all use the same numbers.** There is no per-connector override mechanism in this ADR. If a future connector genuinely needs different cadence values (the Chief Architect's mailbox/webhook example), that requires an explicit extension to this ADR — a new decision, reviewed the same way this one was — not a silent per-connector parameter a connector author could set unilaterally. This is now stated as a hard Non-Goal (Section 7), not left ambiguous.

- **`attempt_count_in_state` increment timing, made explicit:** incremented immediately before an authentication/synchronization attempt is executed — not after, and not only on failure. This ensures the counter reflects "how many times has the Sync Engine tried," independent of outcome, and can never be off by one relative to what `last_attempt_at` records.
- **`next_eligible_retry_at`, made explicit:** this value is **always derived** — computed as `last_attempt_at + cadence(attempt_count_in_state)` immediately after each attempt — **and is never manually set or edited outside that computation.** Stated explicitly so nothing (a future migration, a manual DB fix, a well-intentioned debugging session) writes to this column directly six months from now and silently breaks the policy's determinism.
- **Escalation timing, made explicit (Chief Architect's preferred, more conservative option):** when the elapsed-time threshold is reached, the Sync Engine does **not** transition to `RecoveryRequired` purely from elapsed time — it performs one final authentication attempt first. Only if that attempt also fails does the escalation transition occur. This means escalation is always preceded by a real, failed attempt, never a transition based on the clock alone with no corresponding attempt — more conservative, and consistent with every other state transition in this system already being outcome-driven (ADR-010), not time-driven.

- **`Warning`:** unchanged — attempted every run. Not affected by this ADR.
- **`Degraded`:** re-attempted on a backoff cadence across runs (not every run, per the Milestone 2 §7 hammering concern) — default: eligible again after 1 day, then 2, then 4, capped at 7 days between attempts. After a total elapsed time in `Degraded` of **10 days** (splitting the Chief Architect's own "7 or 14 days" range from Milestone 2's review), the Sync Engine performs one final authentication attempt; only if that attempt also fails does it call `connector.transition_state(RECOVERY_REQUIRED, ...)` — generically, for any connector, not connector-specific logic, and never purely from elapsed time with no corresponding attempt. This is the literal mechanism the roadmap's Feature 3.2 DoD was describing but never had infrastructure to run on.
- **`RecoveryRequired`:** cadence is scheduler-controlled, per the single uniform policy — default "every run." The reasoning for that default (a manual recovery action could have been supplied at any time; a well-behaved connector's `RecoveryRequired` check should be cheap) is documented as *why this default was chosen*, not as a Foundation-level guarantee any connector can rely on being true for itself. Per this section's revision note, no per-connector variation exists in this ADR — a connector whose recovery check genuinely isn't free is a future problem requiring an explicit ADR extension, not something addressed here.
- **`Healthy` → `Degraded`/`Warning` (any first transition):** `attempt_count_in_state` resets to 0, `state_entered_at` resets to now, per Section 3.

## 5. Interaction With the Existing State Machine (ADR-010)

No changes to `ConnectorState` or `_ALLOWED_TRANSITIONS` — `Degraded → RecoveryRequired` is already a legal transition (used today only for the eventual future Peloton-triggered case; this ADR is what actually drives it automatically). This ADR only changes *who calls* `transition_state()` and *when* — the Sync Engine, based on elapsed time/attempts, rather than never.

## 6. Quality Gate Implications

Extends **Gate 1 (Connector Correctness)** and **Gate 5-equivalent adversarial thinking** (per the Tier B roadmap's precedent that lifecycle-adjacent behavior needs failure-mode testing, not just happy-path testing): two new gate criteria are needed, not one — per the Chief Architect's explicit addition, since timer-based bugs are almost always off-by-one and a single "escalation eventually happens" test wouldn't catch that class of bug:

1. **Escalates exactly once, at the configured threshold** — a connector stuck in `Degraded` for a simulated multi-week period must transition to `RecoveryRequired` exactly once, not zero times and not repeatedly.
2. **Never escalates early** — a connector stuck in `Degraded` for any duration *shorter* than the configured threshold must NOT have escalated yet, tested at a boundary (e.g. one cycle short of the threshold), not just at a comfortably-past-threshold point.
3. **Recovers correctly if a re-attempt succeeds before escalation** — a connector that becomes `Degraded`, is re-attempted per the backoff cadence, and succeeds at any point before reaching the escalation threshold must transition back to `Healthy`, with `attempt_count_in_state` and `state_entered_at` reset per Section 3 — not continue counting toward escalation using stale state from the prior degradation.
4. **Persists correctly across an application restart** — this ADR's entire premise is that lifecycle state is persisted (`connector_state`), not held in memory, so this must be tested directly rather than assumed: a connector enters `Degraded`, the application is closed (a fresh `SynchronizationEngine` instance is constructed against the same on-disk database, mirroring exactly how `test_resume_after_mid_sync_kill_no_duplicates_no_lost_progress` already proves resume-cursor persistence across restarts), and the elapsed-time/attempt-count computation continues correctly from what was persisted — not reset, and not lost.
5. **Restart Transparency** (added after initial review, a stronger property than #4): a restart doesn't just preserve state — it must not change the state machine's *behavior* at all. The same scripted event sequence, run once in a single continuous process and once with a simulated restart inserted partway through, must produce byte-for-byte identical `connector_state` — every column (`state`, `state_entered_at`, `last_attempt_at`, `attempt_count_in_state`, `next_eligible_retry_at`), not just the final state name. This is the property most likely to silently break as future work adds schema migrations, new schedulers, or new synchronization strategies, which is exactly why it's tested as its own explicit criterion rather than assumed to follow from #4.

Both are directly testable the same way `test_degraded_connector_is_never_automatically_re_attempted_across_many_runs` already demonstrated the *absence* of retry behavior — the fix gets verified by tests in the same shape, now asserting both the presence of correct escalation and the absence of premature escalation.

## 7. Explicit Non-Goals

- **This ADR does not specify UI/UX** for surfacing "Peloton will be retried in N days" to the athlete — that's a future Coach/UI-layer concern, not Foundation.
- **This ADR does not change `TransientError`/ADR-037** — the two mechanisms are independent, per Section 4.
- **This ADR does not implement per-connector custom cadences.** Resolved unambiguously per Section 4's round-2 revision: v1 has exactly one `LifecyclePolicy` with exactly one set of parameters, applied identically to every connector. A connector never chooses, overrides, or is individually configured with its own cadence — that would require an explicit future ADR extension, not a mechanism this ADR provides.
- **This ADR does not attempt to solve BL-006/BL-008** (unverified live-endpoint shapes) — unrelated concern.
- **This ADR does not implement the fix.** Per the Chief Architect's explicit instruction, no code is written until this document is reviewed and approved.

## 8. Alternatives Considered

- **Leave retry cadence connector-owned:** rejected — reintroduces exactly the inconsistency risk Section 2 argues against, and every connector would need to reimplement the same kind of scheduling logic `extract_resume_cursor()` already showed doesn't scale as a per-connector pattern.
- **A separate, dedicated "Lifecycle Scheduler" component outside the Sync Engine:** considered, rejected for now as unnecessary indirection — the Sync Engine already owns orchestration (ADR-008) and the state machine's transition calls (ADR-010's implementation); splitting scheduling into a new component would be a bigger structural change than the problem currently warrants. Worth revisiting only if lifecycle policy grows significantly more complex than this ADR's scope.
- **No automatic escalation to `RecoveryRequired` at all — leave it purely manual:** rejected — this is exactly the roadmap's own Feature 3.2 DoD, already agreed to during design (Milestone 2's Soft Degradation review), not a new goal being invented here.

## 9. Consequences

- **Positive:** every current and future connector automatically gains correct lifecycle behavior — no connector-specific work required, the same payoff already observed from ADR-037 and BL-005's resolution.
- **Positive:** the roadmap's Feature 3.2 DoD becomes implementable for the first time.
- **Positive:** a single, auditable, uniform retry cadence across all providers — directly protects against the Milestone 2 §7 hammering risk.
- **Neutral:** `connector_state` gains four new columns — a schema migration (v1 → v2), the first this project will have actually exercised outside of `test_migrate_is_idempotent`'s synthetic case.
- **Positive:** existing connectors (Strava, Eufy, Peloton) require no interface changes beyond consuming the lifecycle policy passively — `authenticate()`, `download()`, `normalize()`, and `extract_resume_cursor()` are all untouched by this ADR. This is Foundation orchestration logic gaining a new capability, not a connector contract change, which is why no connector code needs to be revisited to adopt it.
- **Risk carried forward, not eliminated:** the proposed default cadence numbers (1/2/4/7-day backoff, 10-day escalation) are engineering judgment informed by Milestone 2's "7 or 14 days" range, not independently re-derived evidence — flagged as calibratable per the same standard applied throughout this project, not presented as a researched fact the way, say, TRIMP's formula was.
