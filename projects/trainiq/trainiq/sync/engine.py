"""
trainiq.sync.engine — Feature 0.6, extended by ADR-038

Checkpointed, resumable Synchronization Engine (ADR-008), orchestrating
connectors around the Provider State Machine (Feature 0.5 / ADR-010) with
Graceful Degradation (ADR-009): a connector the lifecycle policy says to
skip this run is skipped and logged, never allowed to halt the overall
sync cycle.

Retry policy (Milestone 4 §3, extended by ADR-037): exponential backoff for
transient errors within a single run; authentication failures are NOT
retried in a loop within a run — they transition the connector's state
instead.

ADR-038 (Connector Lifecycle Policy): whether a Degraded/RecoveryRequired
connector is even attempted THIS run — as opposed to ADR-037's within-run
retry timing — is decided by `trainiq.sync.lifecycle_policy.evaluate()`, a
pure function with zero I/O. This module's job is only to load the
persisted lifecycle state, call that function, and persist the outcome —
never to contain the temporal decision logic itself.

Athlete Profile (Epic 7) — explicit system behavior, stated here rather
than left to be inferred from test coverage: this engine is FORWARD-ONLY
with respect to `athlete_profile`. The profile supplied at construction
time is used for every activity normalized during that engine's lifetime;
activities normalized in PAST runs are never retroactively recomputed when
the profile changes later. Concretely — an activity synced before the
athlete's profile existed keeps `training_load_method="unknown"`
permanently, even after a profile is saved; only activities synced AFTER
that point see the new profile. This is a deliberate default for an
append-oriented synchronization pipeline (recomputing historical
`training_load` values on every profile edit would need its own explicit
design — batch size, which historical window to touch, whether to treat
that as a new "training_load_method" version — none of which is decided or
implemented here). If backfilling historical training load after a
late-arriving profile is ever wanted, that is a new, separate capability,
not an extension of this engine's existing behavior.

Scheduler-agnostic (ADR-014): this engine has no idea whether it was
triggered by app launch or a future LaunchAgent — `run_once()` is the only
entry point a caller needs.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, TypeVar

from trainiq.athlete.profile import AthleteProfile
from trainiq.connectors.base import Connector, ConnectorState, RecordKind
from trainiq.logging_setup import diagnostic_logger, summary_logger
from trainiq.normalization.engine import build_canonical_record
from trainiq.normalization.plausibility import DEFAULT_ROLLING_WINDOW_SIZE
from trainiq.sync.lifecycle_policy import LifecycleDecision, LifecycleState, evaluate


class TransientError(Exception):
    """Raised by a connector for a retryable failure (network blip, rate
    limit, timeout). Never raised for authentication failures — those go
    through connector.transition_state() instead, per Milestone 4 §3.

    `retry_after_s` (ADR-037): an optional, provider-supplied authoritative
    wait time in seconds (e.g. from stravalib's RateLimitExceeded.timeout).
    When present, the Sync Engine's retry policy honors it instead of its
    own generic exponential backoff. When absent, behavior is unchanged
    from before ADR-037 — the connector is simply saying "retryable," with
    no opinion on timing, and the generic backoff formula applies."""

    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AuthenticationError(Exception):
    """Raised by a connector when authenticate() cannot succeed. The engine
    does not retry this — it transitions the connector to Degraded and
    moves on, per Graceful Degradation (ADR-009)."""


@dataclass
class ConnectorSyncResult:
    provider: str
    state: ConnectorState
    records_upserted: int = 0  # unchanged meaning: total successfully persisted (inserted + updated)
    skipped_reason: Optional[str] = None
    error: Optional[str] = None
    duration_s: float = 0.0
    # Sync summary UX improvement — real counts, not estimates, distinguishing
    # what actually happened in the database. All default to 0 so every
    # existing caller/test constructing a ConnectorSyncResult without these
    # keyword arguments is entirely unaffected.
    records_inserted: int = 0
    records_updated: int = 0
    records_malformed: int = 0
    records_skipped: int = 0
    # ADR-039 / Issue #38: additive field, appended at the end per the
    # RC1-HF-006 precedent — never reorder/rename existing fields. Counts
    # WEIGH_IN records this run's plausibility check flagged; always 0 for
    # ACTIVITY-kind connectors.
    records_flagged_implausible: int = 0


@dataclass
class SyncRunResult:
    started_at: str
    finished_at: str
    connector_results: list[ConnectorSyncResult] = field(default_factory=list)

    @property
    def any_degraded(self) -> bool:
        return any(r.state != ConnectorState.HEALTHY for r in self.connector_results)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_normalized_activity(conn: sqlite3.Connection, record: dict) -> str:
    """Returns "inserted" or "updated" — a REAL outcome, not an estimate.
    Uses INSERT OR IGNORE first (cursor.rowcount is 1 only for a genuinely
    new row, 0 on conflict — verified directly, since a single
    INSERT...ON CONFLICT DO UPDATE statement's rowcount is always 1 in both
    cases and cannot distinguish them). Only when the row already existed
    does a second, explicit UPDATE statement run — meaning the common case
    (a new record) costs exactly one statement, same as before this change.

    Moved out of SynchronizationEngine (issue #36) so a one-off
    re-normalization pass (trainiq.normalization.renormalize) can reuse
    this exact idempotent persistence primitive instead of duplicating the
    INSERT OR IGNORE / conditional UPDATE pattern a second time. Does not
    commit — the caller owns the transaction boundary, same as before.

    Issue #46: the 6 new class-metadata columns (activity_title through
    sport_type_raw) use `.get(...)` with no KeyError on a missing key —
    every OTHER column above is still indexed directly, since the schema
    genuinely requires them. In the UPDATE branch ONLY, these 6 use
    `COALESCE(?, existing_column)` instead of unconditional overwrite —
    every pre-existing column keeps today's unconditional-overwrite
    behavior exactly as-is (correct for them: they're always fully
    re-derivable from the raw payload alone, with no "didn't attempt"
    case). This is load-bearing, not cosmetic: re-running
    renormalize_provider() for Peloton calls connector.normalize() against
    an already-stored raw payload with zero new network I/O, so a
    class-detail lookup is never retried there — without COALESCE, that
    re-run would silently overwrite every already-backfilled class title/
    instructor/type back to NULL. Safe for Strava (always supplies a real
    value or a deliberate None — COALESCE(NULL, existing_NULL) is still
    NULL) and for Peloton non-class workouts (recomputes the same sentinel
    every run — COALESCE(same_value, old_value) overwrites identically to
    before)."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence,
             activity_title, instructor_name, class_type, planned_duration_s,
             provider_class_id, sport_type_raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["provider"], record["external_id"], record["start_time"], record["duration_s"],
            record["discipline"], record["distance_m"], record["avg_hr"], record["max_hr"],
            record["avg_power"], record["max_power"], record["calories"],
            record["training_load"], record["training_load_method"], record["source_confidence"],
            record.get("activity_title"), record.get("instructor_name"), record.get("class_type"),
            record.get("planned_duration_s"), record.get("provider_class_id"), record.get("sport_type_raw"),
        ),
    )
    if cursor.rowcount == 1:
        return "inserted"

    conn.execute(
        """
        UPDATE normalized_activities SET
            start_time = ?, duration_s = ?, discipline = ?, distance_m = ?,
            avg_hr = ?, max_hr = ?, avg_power = ?, max_power = ?, calories = ?,
            training_load = ?, training_load_method = ?, source_confidence = ?,
            activity_title = COALESCE(?, activity_title),
            instructor_name = COALESCE(?, instructor_name),
            class_type = COALESCE(?, class_type),
            planned_duration_s = COALESCE(?, planned_duration_s),
            provider_class_id = COALESCE(?, provider_class_id),
            sport_type_raw = COALESCE(?, sport_type_raw)
        WHERE provider = ? AND external_id = ?
        """,
        (
            record["start_time"], record["duration_s"], record["discipline"], record["distance_m"],
            record["avg_hr"], record["max_hr"], record["avg_power"], record["max_power"],
            record["calories"], record["training_load"], record["training_load_method"],
            record["source_confidence"],
            record.get("activity_title"), record.get("instructor_name"), record.get("class_type"),
            record.get("planned_duration_s"), record.get("provider_class_id"), record.get("sport_type_raw"),
            record["provider"], record["external_id"],
        ),
    )
    return "updated"


def _derive_next_eligible_retry(
    state: ConnectorState, last_attempt_at: datetime, attempt_count_in_state: int
) -> Optional[datetime]:
    """ADR-038 §4: next_eligible_retry_at is ALWAYS derived from
    last_attempt_at + the policy's cadence for the given state/attempt
    count — never set independently. This is the one place that formula
    lives; lifecycle_policy.evaluate() re-derives the same eligibility
    check from persisted fields directly rather than trusting a
    potentially-stale stored value, so this column is informational
    (useful for a future UI showing "retrying in N days") rather than
    itself load-bearing for the policy decision."""
    from trainiq.sync.lifecycle_policy import _backoff_cadence

    if state == ConnectorState.DEGRADED:
        return last_attempt_at + _backoff_cadence(attempt_count_in_state)
    # Healthy/Warning/RecoveryRequired are all "eligible every run" today —
    # no meaningful future retry time to compute.
    return None


_T = TypeVar("_T")


def retry_with_backoff(
    fn: Callable[[], _T],
    provider: str,
    max_retries: int = 3,
    backoff_base_s: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> _T:
    """Extracted from SynchronizationEngine._with_retries (issue #46) so
    scripts/backfill_peloton_class_metadata.py can reuse the exact same
    ADR-037 retry policy instead of duplicating it. Behavior for the
    existing caller (SynchronizationEngine._with_retries, below) is
    unchanged. Exponential backoff for TransientError by default; honors a
    provider-directed `retry_after_s` hint when present (ADR-037) instead
    of the generic formula. AuthenticationError propagates immediately —
    no retry loop, per Milestone 4 §3."""
    attempt = 0
    while True:
        try:
            return fn()
        except AuthenticationError:
            raise
        except TransientError as exc:
            attempt += 1
            if attempt > max_retries:
                raise
            if exc.retry_after_s is not None:
                delay = exc.retry_after_s
                diagnostic_logger().warning(
                    f"{provider}: transient error (attempt {attempt}/{max_retries}), "
                    f"provider-directed retry in {delay:.1f}s (ADR-037): {exc}"
                )
            else:
                delay = backoff_base_s * (2 ** (attempt - 1))
                diagnostic_logger().warning(
                    f"{provider}: transient error (attempt {attempt}/{max_retries}), "
                    f"retrying in {delay:.1f}s: {exc}"
                )
            sleep_fn(delay)


class SynchronizationEngine:
    """One instance per app run. Owns checkpointing, retry policy, and
    Graceful Degradation across however many connectors are registered."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        max_retries: int = 3,
        backoff_base_s: float = 1.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        athlete_profile: Optional[AthleteProfile] = None,
    ):
        self._conn = conn
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep_fn
        # Injectable for testing ADR-038's multi-day lifecycle timing
        # deterministically — same precedent as sleep_fn above.
        self._now_fn = now_fn
        # Epic 6 Q1 resolution: None until Epic 7 (Feature 7.1) exists and
        # a real caller supplies a populated profile. training_load
        # resolves to Unknown for every activity until then — not a
        # placeholder, the correct ADR-016 behavior for a missing input.
        self._athlete_profile = athlete_profile

    # --- checkpointing -------------------------------------------------

    def get_checkpoint(self, provider: str, strategy: str = "default") -> Optional[str]:
        row = self._conn.execute(
            "SELECT last_cursor FROM sync_checkpoints WHERE provider = ? AND strategy = ?",
            (provider, strategy),
        ).fetchone()
        return row["last_cursor"] if row else None

    def _set_checkpoint(self, provider: str, cursor: str, strategy: str = "default") -> None:
        self._conn.execute(
            """
            INSERT INTO sync_checkpoints (provider, strategy, last_success_at, last_cursor)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider, strategy) DO UPDATE SET
                last_success_at = excluded.last_success_at,
                last_cursor = excluded.last_cursor
            """,
            (provider, strategy, _iso_now(), cursor),
        )
        self._conn.commit()

    # --- idempotent persistence -----------------------------------------

    def _upsert_raw_activity(self, provider: str, external_id: str, payload_json: str) -> None:
        self._conn.execute(
            """
            INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider, external_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                fetched_at = excluded.fetched_at
            """,
            (provider, external_id, payload_json, _iso_now()),
        )

    def _upsert_normalized_activity(self, record: dict) -> str:
        """Returns "inserted" or "updated" — a REAL outcome, not an
        estimate. Delegates to the module-level upsert_normalized_activity()
        (issue #36) so a one-off re-normalization pass
        (trainiq.normalization.renormalize) can reuse the exact idempotent
        persistence primitive instead of duplicating this SQL."""
        return upsert_normalized_activity(self._conn, record)

    def _upsert_weigh_in(self, record: dict) -> str:
        """Returns "inserted" or "updated" — same real, non-estimated
        pattern as _upsert_normalized_activity above, for the same reason.

        ADR-039 / Issue #38: column lists extended with
        is_flagged_implausible/plausibility_reason only. Deliberately
        NEVER bo_confirmed_valid/bo_confirmed_at — those are BO-owned, so a
        resync must never silently revert a BO confirmation."""
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO weigh_ins "
            "(provider, external_id, timestamp, weight_kg, body_fat_pct, muscle_mass_pct, "
            " is_flagged_implausible, plausibility_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["provider"], record["external_id"], record["timestamp"],
                record["weight_kg"], record["body_fat_pct"], record["muscle_mass_pct"],
                record["is_flagged_implausible"], record["plausibility_reason"],
            ),
        )
        if cursor.rowcount == 1:
            return "inserted"

        self._conn.execute(
            "UPDATE weigh_ins SET timestamp = ?, weight_kg = ?, body_fat_pct = ?, muscle_mass_pct = ?, "
            "is_flagged_implausible = ?, plausibility_reason = ? "
            "WHERE provider = ? AND external_id = ?",
            (
                record["timestamp"], record["weight_kg"], record["body_fat_pct"], record["muscle_mass_pct"],
                record["is_flagged_implausible"], record["plausibility_reason"],
                record["provider"], record["external_id"],
            ),
        )
        return "updated"

    def _recent_weights_before(self, timestamp: str, limit: int) -> list[float]:
        """ADR-039 / Issue #38: the athlete's prior unflagged (or
        BO-confirmed-valid) weigh-ins strictly before `timestamp`, newest
        first, limited to `limit` — exactly what
        evaluate_weigh_in_plausibility() expects as its rolling window.

        Deliberately NOT filtered by provider — the rolling median is of
        the athlete's weight, not of a single device's readings, per the
        requirements ("relative to the individual athlete"). If a second
        weigh-in provider is ever added, its readings feed and are checked
        against the same shared baseline."""
        rows = self._conn.execute(
            "SELECT weight_kg FROM weigh_ins "
            "WHERE weight_kg IS NOT NULL AND timestamp < ? "
            "AND (is_flagged_implausible = 0 OR bo_confirmed_valid = 1) "
            "ORDER BY timestamp DESC LIMIT ?",
            (timestamp, limit),
        ).fetchall()
        return [r[0] for r in rows]

    def _persist_canonical_record(self, connector: Connector, record: dict) -> str:
        """Routes purely by `connector.record_kind` — never by inspecting
        which keys `record` happens to contain. This is the specific
        property the Chief Architect asked to be tested explicitly,
        mirroring extract_resume_cursor()'s anti-shape-sniffing test.
        Returns "inserted" or "updated" — the real, non-estimated outcome,
        for the sync summary UX improvement."""
        if connector.record_kind == RecordKind.ACTIVITY:
            return self._upsert_normalized_activity(record)
        return self._upsert_weigh_in(record)

    def _load_lifecycle_state(self, provider: str) -> LifecycleState:
        """Reads persisted lifecycle state (ADR-038 §3). Absence of a row
        means this connector has never synced before — treated as a fresh
        Healthy connector, matching the Connector base class's own default."""
        row = self._conn.execute(
            "SELECT state, state_entered_at, last_attempt_at, attempt_count_in_state "
            "FROM connector_state WHERE provider = ?",
            (provider,),
        ).fetchone()
        if row is None:
            return LifecycleState(
                state=ConnectorState.HEALTHY, state_entered_at=None,
                last_attempt_at=None, attempt_count_in_state=0,
            )
        return LifecycleState(
            state=ConnectorState(row["state"]),
            state_entered_at=datetime.fromisoformat(row["state_entered_at"]) if row["state_entered_at"] else None,
            last_attempt_at=datetime.fromisoformat(row["last_attempt_at"]) if row["last_attempt_at"] else None,
            attempt_count_in_state=row["attempt_count_in_state"] or 0,
        )

    def _record_attempt_start(self, provider: str, lifecycle: LifecycleState, now: datetime) -> None:
        """ADR-038 §4 precision note: attempt_count_in_state is incremented
        IMMEDIATELY BEFORE the attempt executes, not after — so the counter
        always reflects "how many times has the Sync Engine tried,"
        independent of outcome. state_entered_at is untouched here (a
        no-op reconfirmation is not a real transition)."""
        new_count = lifecycle.attempt_count_in_state + 1
        state_entered_at = lifecycle.state_entered_at.isoformat() if lifecycle.state_entered_at else _iso_now()
        self._conn.execute(
            """
            INSERT INTO connector_state (provider, state, updated_at, state_entered_at, last_attempt_at, attempt_count_in_state)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                updated_at = excluded.updated_at,
                last_attempt_at = excluded.last_attempt_at,
                attempt_count_in_state = excluded.attempt_count_in_state
            """,
            (provider, lifecycle.state.value, _iso_now(), state_entered_at, now.isoformat(), new_count),
        )
        self._conn.commit()

    def _record_lifecycle_outcome(
        self, connector: Connector, is_new_state: bool, now: datetime, detail: str | None = None
    ) -> None:
        """Persists the outcome after an attempt. `is_new_state=True` means
        a real transition just occurred (success -> Healthy, first entry
        into Degraded, or escalation into RecoveryRequired) — counters
        reset per ADR-038 §4. `is_new_state=False` means the connector
        stayed in the same state (another failed Degraded retry, or a
        RecoveryRequired re-check with no token yet) — counters already
        incremented by _record_attempt_start are left as-is.

        next_eligible_retry_at is always derived, never set independently
        of last_attempt_at + the policy's cadence (ADR-038 §4)."""
        state = connector.get_state()
        if is_new_state:
            state_entered_at = now
            attempt_count = 1  # this attempt is the first in the new state
        else:
            row = self._conn.execute(
                "SELECT state_entered_at, attempt_count_in_state FROM connector_state WHERE provider = ?",
                (connector.provider,),
            ).fetchone()
            state_entered_at = datetime.fromisoformat(row["state_entered_at"]) if row and row["state_entered_at"] else now
            attempt_count = row["attempt_count_in_state"] if row else 1

        next_eligible = _derive_next_eligible_retry(state, now, attempt_count)

        self._conn.execute(
            """
            INSERT INTO connector_state
                (provider, state, updated_at, detail, state_entered_at, last_attempt_at, attempt_count_in_state, next_eligible_retry_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                state = excluded.state, updated_at = excluded.updated_at, detail = excluded.detail,
                state_entered_at = excluded.state_entered_at, last_attempt_at = excluded.last_attempt_at,
                attempt_count_in_state = excluded.attempt_count_in_state,
                next_eligible_retry_at = excluded.next_eligible_retry_at
            """,
            (
                connector.provider, state.value, _iso_now(), detail,
                state_entered_at.isoformat(), now.isoformat(), attempt_count,
                next_eligible.isoformat() if next_eligible else None,
            ),
        )
        self._conn.commit()

    # --- retry policy ----------------------------------------------------

    def _with_retries(self, fn: Callable[[], list[dict]], provider: str) -> list[dict]:
        """Exponential backoff for TransientError by default; honors a
        provider-directed `retry_after_s` hint when present (ADR-037)
        instead of the generic formula. AuthenticationError propagates
        immediately — no retry loop, per Milestone 4 §3.

        Delegates to the module-level retry_with_backoff() (issue #46),
        the same method -> module-function promotion issue #36 already
        made for upsert_normalized_activity — so
        scripts/backfill_peloton_class_metadata.py can reuse this exact
        ADR-037 policy. Behavior for this (and every existing) caller is
        unchanged."""
        return retry_with_backoff(fn, provider, self._max_retries, self._backoff_base_s, self._sleep)

    # --- orchestration -----------------------------------------------------

    def sync_connector(self, connector: Connector) -> ConnectorSyncResult:
        start = time.monotonic()
        provider = connector.provider
        now = self._now_fn()

        # Restore persisted state before doing anything else — a fresh
        # Connector instance otherwise always defaults to Healthy,
        # regardless of what's actually persisted (the gap discovered
        # while implementing ADR-038; see ProviderStateMachine.restore()).
        lifecycle = self._load_lifecycle_state(provider)
        connector.restore_state(lifecycle.state)

        decision = evaluate(lifecycle, now)

        if decision == LifecycleDecision.SKIP:
            summary_logger().info(
                f"{provider}: not yet eligible for retry — currently {lifecycle.state.value}"
            )
            return ConnectorSyncResult(
                provider=provider, state=lifecycle.state,
                skipped_reason=f"connector is {lifecycle.state.value}, not yet eligible per lifecycle policy",
                duration_s=time.monotonic() - start,
            )

        pre_attempt_state = connector.get_state()
        # ADR-038 §4 precision note: increment BEFORE the attempt executes.
        self._record_attempt_start(provider, lifecycle, now)

        # Epic 0 Hardening (Finding 4): checkpoints are per (provider, strategy),
        # not per provider alone — the schema was built for ADR-011 multi-strategy
        # connectors (Eufy's Cloud+BLE) but this wiring was previously missing,
        # meaning two strategies would have silently shared one checkpoint.
        strategy_obj = connector.active_strategy()
        strategy = strategy_obj.value if strategy_obj is not None else "default"

        try:
            ok = connector.authenticate()
            if not ok:
                raise AuthenticationError(f"{provider}: authenticate() returned False")

            since = self.get_checkpoint(provider, strategy=strategy)
            raw_records = self._with_retries(lambda: connector.download(since=since), provider)

            count = 0
            inserted_count = 0
            updated_count = 0
            skipped_malformed = 0
            skipped_no_external_id = 0
            flagged_implausible_count = 0
            resume_cursor = since

            # ADR-039 / Issue #38, Task breakdown item 5: Eufy's
            # supports_incremental_sync=False means every sync reprocesses
            # full history (BL-006), and download()'s return order isn't
            # guaranteed chronological. For WEIGH_IN connectors, pre-
            # normalize and sort by timestamp ascending so
            # _recent_weights_before()'s DB lookups below are evaluated in
            # the same chronological order the one-time backfill used —
            # otherwise a sync's flagging results could depend on download
            # order rather than solely on the data. Not needed for
            # ACTIVITY connectors, so left unchanged there.
            if connector.record_kind == RecordKind.WEIGH_IN:
                pairs = [(raw, connector.normalize(raw)) for raw in raw_records]
                pairs.sort(key=lambda pair: pair[1].get("timestamp") or "")
            else:
                pairs = [(raw, None) for raw in raw_records]

            for raw, pre_normalized in pairs:
                normalized = pre_normalized if pre_normalized is not None else connector.normalize(raw)
                external_id = normalized.get("external_id") or raw.get("external_id")
                if external_id is None:
                    skipped_no_external_id += 1
                    diagnostic_logger().warning(
                        f"{provider}: record with no external_id, skipping upsert"
                    )
                    continue

                self._upsert_raw_activity(provider, str(external_id), json.dumps(raw))
                count += 1

                # Epic 6 self-review finding (Major, discovered and fixed
                # in the same pass): build_canonical_record() indexes
                # required fields directly (external_id/start_time/
                # duration_s for ACTIVITY, external_id/timestamp for
                # WEIGH_IN) rather than using .get() — correctly, since
                # the schema genuinely requires them (NOT NULL). But
                # before this fix, a single malformed record raising
                # KeyError propagated uncaught through run_once()'s list
                # comprehension, crashing EVERY connector in the batch,
                # not just the one with bad data — a direct regression
                # against Graceful Degradation (ADR-009), verified by a
                # reproduction before this fix was written. The raw
                # payload is still preserved (raw_activities exists
                # specifically so Normalization can be re-run later
                # without re-fetching, per Milestone 4 §5) even when
                # canonical normalization fails — only the canonical
                # record is skipped, logged, never silently dropped.
                try:
                    recent_weights_kg: tuple[float, ...] = ()
                    if connector.record_kind == RecordKind.WEIGH_IN:
                        # ADR-039 / Issue #38: committed-history lookup, not
                        # in-memory batch state — later rows in this same
                        # run see earlier rows' upserts via this connection
                        # (same transaction, not yet committed but visible
                        # to it), matching the one-time backfill's semantics.
                        recent_weights_kg = tuple(
                            self._recent_weights_before(normalized["timestamp"], DEFAULT_ROLLING_WINDOW_SIZE)
                        )
                    canonical_record = build_canonical_record(
                        provider, connector.record_kind, normalized, self._athlete_profile, recent_weights_kg
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    skipped_malformed += 1
                    diagnostic_logger().warning(
                        f"{provider}: malformed record (external_id={external_id!r}) "
                        f"could not be normalized into a canonical record, raw payload "
                        f"preserved for later reprocessing: {exc}"
                    )
                else:
                    outcome = self._persist_canonical_record(connector, canonical_record)
                    if outcome == "inserted":
                        inserted_count += 1
                    else:
                        updated_count += 1
                    if canonical_record.get("is_flagged_implausible"):
                        flagged_implausible_count += 1

                # Connector-owned per the ADR-013 refinement (Epic 2) — the
                # Sync Engine never inspects `normalized`'s field names
                # itself, whatever shape a given connector's data has.
                candidate_cursor = connector.extract_resume_cursor(normalized)
                # DIAGNOSTIC ONLY — added at explicit Chief Architect request
                # to determine with certainty which side of the comparison
                # below is still an int, without changing the comparison's
                # logic, types, or behavior in any way.
                diagnostic_logger().debug(
                    "DEBUG CURSORS: candidate={!r} ({}) resume={!r} ({})",
                    candidate_cursor, type(candidate_cursor).__name__,
                    resume_cursor, type(resume_cursor).__name__,
                )
                if candidate_cursor and (resume_cursor is None or candidate_cursor > resume_cursor):
                    resume_cursor = candidate_cursor

            if skipped_malformed:
                summary_logger().warning(
                    f"{provider}: {skipped_malformed} malformed record(s) skipped this run"
                )

            self._conn.commit()
            if resume_cursor:
                self._set_checkpoint(provider, resume_cursor, strategy=strategy)

            connector.transition_state(ConnectorState.HEALTHY, detail="sync succeeded")
            # A successful attempt is always a "new state" for lifecycle-
            # counter purposes, whether it came from Healthy, Warning,
            # Degraded, or RecoveryRequired — counters reset either way.
            self._record_lifecycle_outcome(connector, is_new_state=True, now=now, detail="sync succeeded")
            # Sync summary UX improvement: "synced N record(s)" was
            # genuinely misleading — it reported records_upserted (raw
            # records successfully processed into raw_activities), which
            # says nothing about how many were actually new vs. already
            # existing in the canonical table, or how many were silently
            # malformed and skipped. Every number below is real, counted
            # during this exact run — none are estimated or derived after
            # the fact.
            # ADR-039 / Issue #38: always appended (not conditional like
            # the BL-006 clause below) — "flagged 0 implausible" is
            # informative for every connector, including ACTIVITY-kind
            # ones where it will always read 0.
            summary_logger().info(
                f"{provider}: downloaded {len(raw_records)}, "
                f"inserted {inserted_count}, updated {updated_count}, "
                f"malformed {skipped_malformed}, skipped {skipped_no_external_id}, "
                f"flagged {flagged_implausible_count} implausible"
            )
            if not connector.supports_incremental_sync:
                # BL-006 (Eufy, currently the only connector this applies
                # to): stated as a generic fact the Sync Engine reads from
                # the connector, never as a hardcoded provider name here —
                # the Sync Engine must never contain provider-specific
                # knowledge, the same principle BL-005 already established.
                summary_logger().info(
                    f"{provider}: incremental filtering unavailable for this connector's "
                    f"current endpoint; full history processed each sync (BL-006)"
                )
            return ConnectorSyncResult(
                provider=provider, state=connector.get_state(),
                records_upserted=count, duration_s=time.monotonic() - start,
                records_inserted=inserted_count, records_updated=updated_count,
                records_malformed=skipped_malformed, records_skipped=skipped_no_external_id,
                records_flagged_implausible=flagged_implausible_count,
            )

        except AuthenticationError as exc:
            if decision == LifecycleDecision.ESCALATE:
                # ADR-038 §4: escalation is always preceded by one real,
                # failed attempt — this is that attempt, and it just failed.
                target = ConnectorState.RECOVERY_REQUIRED
            elif pre_attempt_state in (ConnectorState.HEALTHY, ConnectorState.WARNING):
                target = ConnectorState.DEGRADED
            else:
                # Already Degraded (a normal backoff retry that failed
                # again), or already RecoveryRequired (re-checked, still no
                # manual token supplied) — stay put. Critically, this must
                # NOT unconditionally target DEGRADED: RECOVERY_REQUIRED's
                # only legal outgoing transition is to HEALTHY
                # (_ALLOWED_TRANSITIONS), so blindly transitioning to
                # DEGRADED here would raise InvalidStateTransition — a
                # latent bug that was dormant only because RecoveryRequired
                # connectors were never previously re-attempted at all.
                target = pre_attempt_state
            is_new_state = target != pre_attempt_state
            connector.transition_state(target, detail=str(exc))
            self._record_lifecycle_outcome(connector, is_new_state=is_new_state, now=now, detail=str(exc))
            summary_logger().warning(f"{provider}: authentication failed — {exc}")
            return ConnectorSyncResult(
                provider=provider, state=connector.get_state(),
                error=str(exc), duration_s=time.monotonic() - start,
            )
        except TransientError as exc:
            # Unaffected by ADR-038/escalation — a rate limit or timeout is
            # not evidence the credential itself is broken, so it always
            # routes to Warning, never RecoveryRequired, regardless of
            # `decision`.
            is_new_state = pre_attempt_state != ConnectorState.WARNING
            connector.transition_state(ConnectorState.WARNING, detail=str(exc))
            self._record_lifecycle_outcome(connector, is_new_state=is_new_state, now=now, detail=str(exc))
            summary_logger().warning(f"{provider}: sync failed after retries — {exc}")
            return ConnectorSyncResult(
                provider=provider, state=connector.get_state(),
                error=str(exc), duration_s=time.monotonic() - start,
            )
        except Exception as exc:  # noqa: BLE001 — RC1-HF-001, deliberate boundary
            # RC1-HF-001: this is the Sync Engine's outer resilience
            # boundary — the fix for a real, reproduced defect found while
            # wiring the composition root (trainiq/app.py). Only
            # AuthenticationError and TransientError were previously
            # handled here; any OTHER exception a connector's
            # authenticate()/download()/normalize() happened to raise
            # (confirmed reproducible: StravaConnector raises a bare
            # RuntimeError when STRAVA_CLIENT_ID/SECRET aren't set)
            # propagated uncaught through run_once()'s list comprehension,
            # crashing every other connector in the same batch — a direct
            # violation of Graceful Degradation (ADR-009), and the same
            # class of bug as the malformed-record crash fixed during
            # Epic 6, one layer further out. This is NOT a Strava-specific
            # fix — deliberately generic, since the same gap would apply
            # identically to an unexpected exception from `requests`,
            # `sqlite3`, `keyring`, or any future connector.
            #
            # Treated the same way an AuthenticationError is treated
            # (conservative: something is unexpectedly wrong, not
            # something that will resolve itself on the next retry a few
            # seconds from now) — including respecting ADR-038 escalation
            # and the RecoveryRequired-can-only-go-to-Healthy legality
            # rule already established for AuthenticationError above.
            if decision == LifecycleDecision.ESCALATE:
                target = ConnectorState.RECOVERY_REQUIRED
            elif pre_attempt_state in (ConnectorState.HEALTHY, ConnectorState.WARNING):
                target = ConnectorState.DEGRADED
            else:
                target = pre_attempt_state
            is_new_state = target != pre_attempt_state
            connector.transition_state(target, detail=f"{type(exc).__name__}: {exc}")
            self._record_lifecycle_outcome(
                connector, is_new_state=is_new_state, now=now, detail=f"{type(exc).__name__}: {exc}"
            )
            # Logged at ERROR, not WARNING — an unexpected exception type
            # is qualitatively different from an anticipated auth/transient
            # failure and deserves to stand out in the diagnostic log,
            # since it may indicate a real bug rather than a known,
            # already-designed-for degradation cause.
            # RC1-HF-004: the diagnostic log now includes the FULL
            # traceback for any unexpected exception, not just its type
            # and message. Made permanent rather than a temporary
            # debugging hack — a one-line message ("TypeError: ...") is
            # not enough to know which file/line an exception actually
            # originated from once more than one connector or code path
            # could plausibly produce the same exception type. loguru's
            # `exception=True` captures the real, current traceback via
            # sys.exc_info() at the point this handler runs — the same
            # mechanism `traceback.format_exc()` uses, integrated into
            # the existing logging setup rather than a separate print.
            diagnostic_logger().opt(exception=True).error(
                f"{provider}: unexpected exception during sync — {type(exc).__name__}: {exc}"
            )
            summary_logger().warning(f"{provider}: sync failed unexpectedly — see diagnostic log")
            return ConnectorSyncResult(
                provider=provider, state=connector.get_state(),
                error=f"{type(exc).__name__}: {exc}", duration_s=time.monotonic() - start,
            )

    def run_once(self, connectors: list[Connector]) -> SyncRunResult:
        """The single entry point a caller (app launch, or later a
        LaunchAgent) needs — scheduler-agnostic per ADR-014. A failure in
        one connector never prevents the others from running (ADR-009)."""
        started_at = _iso_now()
        results = []
        for c in connectors:
            r = self.sync_connector(c)
            results.append(r)
        result = SyncRunResult(started_at=started_at, finished_at=_iso_now(), connector_results=results)
        return result
