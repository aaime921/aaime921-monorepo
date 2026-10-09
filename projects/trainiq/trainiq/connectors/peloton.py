"""
trainiq.connectors.peloton — Epic 3: Peloton Connector

Feature 3.1 (Authentication — dual-path, per the Chief Architect's "Soft
Degradation" product decision from Milestone 2's review):
  Peloton has no official API. Milestone 2 found DATED, VERIFIED evidence
  (not theoretical) that the automated username/password login endpoint
  broke between October 2025 and January 2026, and that the only proven
  fallback across the community is a manually-obtained bearer token pasted
  in by the user. This connector therefore has two auth paths, not one:

    1. AUTOMATED (default, attempted whenever the connector is not already
       in RecoveryRequired): email + password against the session-based
       login endpoint. This is the "zero technical knowledge" path and is
       tried first every time, because Milestone 2 found no evidence this
       fails for every user, only that it CAN and HAS broken.
    2. MANUAL RECOVERY (fallback, only used once the connector has already
       reached RecoveryRequired): a bearer token the user extracted from
       their own authenticated browser session, supplied via
       `submit_manual_recovery()`. Per the Chief Architect's explicit
       ruling, this is a recovery path, not the default UX — TrainIQ does
       not ask for it on first failure, only after prolonged degradation.

Feature 3.2 (Recovery path): see `submit_manual_recovery()` and
  `request_manual_recovery()`. IMPORTANT, flagged prominently rather than
  quietly worked around: the specific trigger condition from the roadmap
  ("on repeated automated-login failure... only triggers after a defined
  consecutive-failure threshold, e.g. 7-14 days") turns out to depend on
  something more fundamental than duration-tracking. Verified empirically,
  not by inspection: once a connector reaches Degraded, the Synchronization
  Engine's Graceful Degradation logic (ADR-009) skips it entirely on every
  subsequent run — forever, with no periodic re-attempt at all. A
  simulated "6 consecutive daily syncs, all failing the same way" test
  shows exactly ONE real authentication attempt total, not six. This means
  the "wait N days, then request recovery" policy cannot be implemented
  today even with duration-tracking added, because nothing would ever
  re-attempt authentication during that window to discover the connector's
  actual current status. See BACKLOG.md, BL-007, which this module raises
  rather than resolves — this is Foundation-level Synchronization Engine
  behavior, not something one connector should decide unilaterally.

Feature 3.3 (Sync — REST only, per Milestone 2 §7 / R-PELOTON-05): GraphQL-
  only features (e.g. tags) are explicitly out of scope for v1.

Feature 3.4 (Normalization — extraction only, same scope limit as Strava's
  Feature 1.3 and Eufy's Feature 2.2): discipline branching so strength
  classes (modeled by Peloton as "rides," per Milestone 2/3's finding) are
  not mis-normalized, plus semantic-drift logging (R-PELOTON-07 / R-PELOTON-06)
  for any `fitness_discipline` value this connector doesn't recognize yet —
  logged, never silently dropped or guessed at.

BL-008 is closed (issue #5): the workout-list endpoint and response shape
are now live-verified, not best-guess. `download()` calls `GET /api/me`
for `user_id`, then walks `GET /api/user/{user_id}/workouts` across all
pages. `normalize()`'s field mapping matches the real response shape —
see `docs/verification/peloton-2026-09-28.md` and
`docs/architecture/5-peloton-endpoint-field-mapping.md`.

Feature 3.5 (issue #7 — OAuth+PKCE auth path, a third option alongside the
  two above): OAuth authorization-code+PKCE against Peloton's own Auth0
  tenant, backed by a stored, rotating refresh token, so an account that
  has completed the one-time setup (`scripts/setup_peloton_oauth.py`) can
  keep authenticating silently — no manual bearer-token re-paste every
  ~48h. `authenticate()` tries this path first, but only when OAuth
  credentials exist for this account; an account that hasn't run setup
  sees exactly today's automated-login/manual-recovery behavior,
  unchanged. If OAuth ever fails (refresh token revoked/rejected — this
  remains an unofficial, reverse-engineered `client_id` with no written
  permission from Peloton), it falls back to the existing manual-recovery
  path in the same `authenticate()` call, per the module's Soft
  Degradation pattern. See `docs/architecture/7-peloton-pkce-oauth-refresh.md`.

Feature 3.6 (issue #46 — class title/instructor/class type/planned length):
  `download()` fetches and attaches per-class metadata for every
  `workout_type == "class"` workout.

Feature 3.9 (issue #58 — resolves BL-011: two-step `peloton_id` ->
  `ride_id` resolution): #46's single-call lookup
  (`GET /api/ride/{peloton_id}/details`, treating a workout's `peloton_id`
  as if it were already a ride id) was disproved live against the BO's
  real account — 131/131 class workouts failed, because `peloton_id` is
  actually a class *session* id, and that endpoint 404s on it every time.
  The session id must first be resolved via `GET /api/peloton/{peloton_id}`
  (`fetch_class_session()`, new) to obtain the real `ride_id`, which is
  THEN passed to `GET /api/ride/{ride_id}/details`
  (`fetch_class_details()`, unchanged endpoint, now called correctly) to
  get the real ride/class object. See
  `docs/trainiq/architecture/58-peloton-class-lookup-ride-id-resolution.md`
  for the live-verified response shapes, the two-cache resolution in
  `download()`, and the `difficulty_estimate` column this issue adds.

Feature 3.7 (issue #47, AC1 only — HR-zone durations + effort points):
  `normalize()` now also reads `effort_zones.heart_rate_zone_durations`
  (z1-z5 seconds) and `effort_zones.total_effort_points` off the SAME
  list-endpoint record already fetched above (issue #5) — CONFIRMED,
  live-verified field names (docs/trainiq/verification/peloton-2026-09-28.md's
  second example record), unlike #46's constants above. `effort_zones:
  null` (also confirmed to occur, same verification file's first record)
  means every one of these fields stays NULL, never zero or fabricated.

  DEVIATION from docs/trainiq/architecture/47-peloton-heart-rate-capture.md's
  literal code sample, flagged here per docs/trainiq/roles/developer.md
  ("if you need to deviate, explain why"): the architecture doc has
  download() attach these as connector-internal `_hr_zone_*_s` keys (the
  #45/#46 pattern), with normalize() reading those. This implementation
  instead has normalize() read `effort_zones` straight off `raw`, with no
  download()-side step at all. Reason: `effort_zones` is already sitting
  on the raw payload verbatim with zero extra network cost — unlike every
  other connector-internal key this project uses the underscore-prefix
  pattern for (_class_title, _avg_hr, _distance_unit, ...), there is no
  "fetch result to smuggle through a dict" here, so the indirection buys
  nothing. It actively costs something: the architecture's own AC4 backfill
  script only re-parses already-stored raw_activities payloads for class
  metadata and the (future) performance endpoint, never for zone data — had
  normalize() depended on a download()-only key, the existing 136 workouts'
  zone data could never be backfilled by ANY mechanism (not the backfill
  script, and not trainiq.normalization.renormalize's existing
  renormalize_provider(), which calls connector.normalize() directly against
  stored raw payloads with no new network I/O — see that module's
  docstring). Reading `effort_zones` directly in normalize() instead means
  renormalize_provider() backfills all 136 existing Peloton workouts' zone
  data for free, with no new script needed for AC4's "fields in ACs 1-2"
  coverage of AC1 specifically.

  AC2 of #47 is implemented separately — see Feature 3.8 below. This
  paragraph is left as the historical record of why AC1 shipped on its own
  first (BL-012).

Feature 3.8 (issue #47, AC2 — avg/max HR, max power): `fetch_workout_performance()`
  calls the per-workout performance endpoint and `download()` attaches its
  result (gated on the same `is_new_since_checkpoint` check #46 already
  computes per workout, independent of class status — any discipline can
  have HR data) so `normalize()` can read `avg_hr`/`max_hr`/`max_power` for
  real instead of hardcoding them to None. CONFIRMED against the BO's real
  account, 2026-10-09 (`docs/trainiq/verification/peloton-2026-09-28.md`'s
  dated addendum) — this resolves BL-012, the only item in BACKLOG.md that
  was ever a "zero code, blocking" gap rather than a flagged-uncertain
  guess.

  DEVIATION from docs/trainiq/architecture/47-peloton-heart-rate-capture.md's
  literal naming: that doc calls the new status column `hr_fetch_status`.
  This implementation instead reuses `PERFORMANCE_FETCH_STATUS_OK`/
  `PERFORMANCE_FETCH_STATUS_FAILED` and a `performance_fetch_status` column,
  per issue #57's architecture doc
  (`docs/trainiq/architecture/57-peloton-distance-performance-graph-source.md`,
  "Approach" — written after #47's own doc, while #47 still had no PR/branch):
  #57's distance fix and this issue's HR/power both come from the exact same
  `fetch_workout_performance()` call, so a per-concern status column would
  always move in lockstep with this one — #57's doc explicitly directs #47
  to reuse its column rather than add a second one. As of this change, #57
  itself has no landed code yet (architecture doc only), so this is the
  first implementation of `fetch_workout_performance()`/
  `PERFORMANCE_ENDPOINT_TEMPLATE`/`_parse_performance_response()` — #57,
  when implemented, should extend `_parse_performance_response()` with its
  own `summaries`-based distance extraction from the same already-parsed
  response body, not add a second network call.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any

from trainiq.connectors.base import CapabilityTier, Connector, ConnectorState
from trainiq.credentials.store import CredentialStore
from trainiq.logging_setup import diagnostic_logger
from trainiq.sync.engine import AuthenticationError, TransientError, retry_with_backoff

PROVIDER = "peloton"

DEFAULT_BASE_URL = "https://api.onepeloton.com"  # ADR-007: configuration, not scattered literals

CRED_EMAIL = "email"
CRED_PASSWORD = "password"
CRED_SESSION_ID = "session_id"
CRED_SESSION_EXPIRES_AT = "session_expires_at"
CRED_MANUAL_BEARER_TOKEN = "manual_bearer_token"

# Feature 3.5 (issue #7): OAuth+PKCE path. Distinct credential types so
# this path's stored values can never be confused with the automated-login
# session or the manual-recovery bearer token above.
CRED_OAUTH_ACCESS_TOKEN = "oauth_access_token"
CRED_OAUTH_REFRESH_TOKEN = "oauth_refresh_token"
CRED_OAUTH_EXPIRES_AT = "oauth_expires_at"

# Milestone 2's research found no documented session lifetime for Peloton
# (unlike Strava's confirmed 6-hour figure). Conservative, unevidenced
# default, flagged as such rather than presented as a researched fact.
ASSUMED_SESSION_LIFETIME_S = 3600

# Feature 3.5: unofficial, reverse-engineered client_id (no written
# permission from Peloton) — live-verified end to end against the real
# account per issue #7, not independently corroborated elsewhere in this
# repo (see docs/architecture/7-peloton-pkce-oauth-refresh.md,
# "Risks/tradeoffs"). Not a constructor parameter, unlike base_url: there
# is no legitimate reason to override Peloton's own Auth0 tenant identifiers.
OAUTH_AUTHORIZE_URL = "https://auth.onepeloton.com/authorize"
OAUTH_TOKEN_URL = "https://auth.onepeloton.com/oauth/token"
OAUTH_CLIENT_ID = "WVoJxVDdPoFx4RNewvvg6ch2mZ7bwnsM"
OAUTH_REDIRECT_URI = "https://members.onepeloton.com/callback"
OAUTH_SCOPE = "offline_access openid peloton-api.members:default"
OAUTH_AUDIENCE = "https://api.onepeloton.com/"

# Reasoned default, not independently verified — same category of
# flagged-not-researched constant as ASSUMED_SESSION_LIFETIME_S above.
# Peloton's OAuth access token is live-verified at expires_in=172800
# (48h); this rounds a proportionally-scaled margin up to a flat,
# easy-to-reason-about value (see architecture doc's "Approach" section).
OAUTH_EXPIRY_SAFETY_MARGIN_S = 3600

# Canonical discipline taxonomy is Epic 6's job (Milestone A §5) — this is
# only the connector-level branching needed to avoid mis-normalizing
# strength classes as rides, per R-PELOTON-06. Anything not in this map is
# logged, never guessed (R-PELOTON-07).
_KNOWN_FITNESS_DISCIPLINES = {"cycling", "strength", "yoga", "running", "meditation", "stretching", "cardio"}

# Issue #45: issue #5's "distance is always km" conclusion (the constant
# this replaced, DISTANCE_KM_TO_M_MULTIPLIER) came from the CSV importer's
# own "Distance (km)" column and a plausibility check — not from the live
# API. Live evidence (BO's real account + Peloton<->Strava pairs, issue #45)
# proved the live API actually reports `distance` in the account's own
# display-unit setting (miles for this BO), not always km. The unit must
# therefore be read from the account, per workout-download, never assumed.

# UNCONFIRMED — issue #45's own suggestion, not yet live-verified against a
# real /api/me response body beyond its `id` field (the only field
# docs/verification/peloton-2026-09-28.md actually captured). Task 1 of
# docs/architecture/45-peloton-distance-unit-conversion.md calls for a
# live diagnostic (same disposable-script pattern as
# scripts/debug_peloton_manual_bearer.py from issue #5) against the real
# account's /api/me response, using the BO's manually-supplied bearer
# token, to confirm or correct this field name and the alias table below
# — this is live-account verification (Live-account testing is BO
# responsibility, not Dev/QA, per docs/roles/technical-architect.md
# "Testing scope boundaries"), so it could not be run from this sandbox,
# which has no stored Peloton credentials and no network path to
# api.onepeloton.com. Everything downstream of
# _resolve_account_distance_unit() is unaffected by what that
# verification finds — only this constant and the alias table below would
# need to change. See docs/trainiq/verification/peloton-2026-09-28.md and
# BACKLOG.md BL-010.
ACCOUNT_DISTANCE_UNIT_FIELD = "distance_unit"

# Recognized spellings/synonyms for the two units this project supports
# today. Extend this table (not the lookup logic in
# _resolve_account_distance_unit()) if live verification finds Peloton
# reports a different token set (e.g. "imperial"/"metric" instead of
# "mi"/"km").
_DISTANCE_UNIT_ALIASES: dict[str, str] = {
    "mi": "mi", "mile": "mi", "miles": "mi",
    "km": "km", "kilometer": "km", "kilometers": "km",
    "kilometre": "km", "kilometres": "km",
}

_DISTANCE_UNIT_MULTIPLIERS: dict[str, float] = {
    "mi": 1609.344,
    "km": 1000.0,
}


def _resolve_account_distance_unit(me: dict[str, Any]) -> str | None:
    """Reads ACCOUNT_DISTANCE_UNIT_FIELD off a /api/me response body and
    maps it through _DISTANCE_UNIT_ALIASES to a canonical "mi"/"km" token.
    Returns None for a missing field or an unrecognized value — never
    guesses, never raises. Pure function of its argument; does no logging
    itself (normalize() owns logging a missing/unknown unit, so it's
    reported exactly once per affected workout, not once per sync run plus
    once per workout)."""
    raw_unit = me.get(ACCOUNT_DISTANCE_UNIT_FIELD)
    if not isinstance(raw_unit, str):
        return None
    return _DISTANCE_UNIT_ALIASES.get(raw_unit.strip().lower())

WORKOUT_TYPE_FIELD = "workout_type"
RIDE_ID_FIELD = "peloton_id"

# Plain sentinel strings stored in the same free-text `class_type` column
# a real Peloton category value would occupy — collision with a real
# Peloton-assigned category is not a realistic concern (Peloton does not
# control this column's vocabulary; this project does).
CLASS_TYPE_NOT_A_CLASS = "not_a_class"      # AC2: just-ride/scenic/free mode
CLASS_TYPE_LOOKUP_FAILED = "lookup_failed"  # AC2/AC7: class, but either lookup step failed

# Issue #58, step 1: a workout's RIDE_ID_FIELD ("peloton_id") is actually a
# class SESSION id, not a ride id — live-verified against the BO's real
# account, 2026-10-09. This endpoint resolves it to the real ride_id.
SESSION_ENDPOINT_TEMPLATE = "/api/peloton/{peloton_id}"
SESSION_RIDE_ID_FIELD = "ride_id"

# Issue #58, step 2: unchanged endpoint from #46 — only the caller's
# argument changed (the RESOLVED ride_id from step 1, never peloton_id
# directly). Live-verified response shape (issue #58) supersedes #46's
# UNCONFIRMED flat-shape guess: `ride` is a nested object holding most
# fields, while `class_types` is a TOP-LEVEL sibling of `ride`, not nested
# under it.
RIDE_DETAIL_ENDPOINT_TEMPLATE = "/api/ride/{ride_id}/details"

RIDE_OBJECT_FIELD = "ride"
CLASS_TITLE_FIELD = "title"               # ride.title
INSTRUCTOR_OBJECT_FIELD = "instructor"    # ride.instructor
INSTRUCTOR_NAME_FIELD = "name"            # ride.instructor.name
PLANNED_DURATION_FIELD = "duration"       # ride.duration, seconds
DIFFICULTY_ESTIMATE_FIELD = "difficulty_estimate"  # ride.difficulty_estimate
CLASS_TYPES_FIELD = "class_types"         # top-level list of {name: str}
CLASS_TYPE_NAME_FIELD = "name"

# Issue #47, AC1 — CONFIRMED, live-verified (docs/trainiq/verification/
# peloton-2026-09-28.md's second example record). Unlike the #46 constants
# above, no Task 1 dependency: these sit on the same list-endpoint record
# download() already walks, with the exact field names this evidence shows.
EFFORT_ZONES_FIELD = "effort_zones"
HR_ZONE_DURATIONS_FIELD = "heart_rate_zone_durations"
TOTAL_EFFORT_POINTS_FIELD = "total_effort_points"
HR_ZONE_DURATION_FIELDS: dict[int, str] = {
    1: "heart_rate_z1_duration",
    2: "heart_rate_z2_duration",
    3: "heart_rate_z3_duration",
    4: "heart_rate_z4_duration",
    5: "heart_rate_z5_duration",
}

# Issue #47, AC2 — CONFIRMED against the BO's real account, 2026-10-09
# (docs/trainiq/verification/peloton-2026-09-28.md's dated addendum).
# Resolves BL-012. `every_n` is the literal param the BO's own capture
# used; the response's top-level "metrics"/"summaries" lists are present
# regardless of its value (only the per-interval "values" series' density
# depends on it), so this connector's use (summary stats only) is not
# sensitive to the exact number chosen here.
PERFORMANCE_ENDPOINT_TEMPLATE = "/api/workout/{workout_id}/performance_graph"
PERFORMANCE_ENDPOINT_PARAMS: dict[str, Any] = {"every_n": 60}

METRICS_FIELD = "metrics"
METRIC_SLUG_FIELD = "slug"
METRIC_AVERAGE_VALUE_FIELD = "average_value"
METRIC_MAX_VALUE_FIELD = "max_value"
METRIC_DISPLAY_UNIT_FIELD = "display_unit"

HEART_RATE_METRIC_SLUG = "heart_rate"
HEART_RATE_DISPLAY_UNIT = "bpm"
OUTPUT_METRIC_SLUG = "output"
OUTPUT_DISPLAY_UNIT = "watts"

# Shared with issue #57 (distance, same fetch_workout_performance() call) —
# see Feature 3.8's module docstring for why this is NOT named
# hr_fetch_status, the name #47's own architecture doc originally proposed.
# NULL (column default): never attempted this pass — COALESCE preserves
# prior state, same convention as #46's absent _class_* keys.
PERFORMANCE_FETCH_STATUS_OK = "ok"          # attempted, response parsed
                                             # (avg_hr/max_hr/max_power may
                                             # still individually be None —
                                             # a sparse but well-formed response)
PERFORMANCE_FETCH_STATUS_FAILED = "failed"  # attempted, fetch itself failed


def _coerce_int(value: Any) -> int | None:
    """metrics[].average_value/max_value can come back as a float (e.g.
    135.0) even though avg_hr/max_hr/max_power are INTEGER columns —
    rounds rather than truncates; never raises for a non-numeric value."""
    if value is None:
        return None
    try:
        return round(value)
    except TypeError:
        return None


def _parse_performance_response(body: dict[str, Any]) -> dict[str, Any]:
    """Issue #47, AC2 — CONFIRMED (see PERFORMANCE_ENDPOINT_TEMPLATE above):
    `body["metrics"]` is a list of per-metric summary dicts, each with a
    "slug" ("heart_rate", "output", "cadence", ...), "average_value",
    "max_value", and "display_unit". Looked up by slug, never by position
    — Peloton does not document a stable ordering. A workout with no HR
    monitor paired simply has no "heart_rate" entry at all -> avg_hr/max_hr
    stay None, never fabricated or derived from anything else. Never raises
    for a well-formed-but-data-sparse response — that is a legitimate set
    of Nones, not a fetch failure (see fetch_workout_performance())."""
    result: dict[str, Any] = {"avg_hr": None, "max_hr": None, "max_power": None}
    metrics = body.get(METRICS_FIELD)
    if not isinstance(metrics, list):
        return result
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        slug = metric.get(METRIC_SLUG_FIELD)
        if slug == HEART_RATE_METRIC_SLUG:
            if metric.get(METRIC_DISPLAY_UNIT_FIELD) == HEART_RATE_DISPLAY_UNIT:
                result["avg_hr"] = _coerce_int(metric.get(METRIC_AVERAGE_VALUE_FIELD))
                result["max_hr"] = _coerce_int(metric.get(METRIC_MAX_VALUE_FIELD))
            else:
                diagnostic_logger().warning(
                    f"{PROVIDER}: unexpected heart_rate display_unit "
                    f"{metric.get(METRIC_DISPLAY_UNIT_FIELD)!r} — avg_hr/max_hr stored as NULL, not guessed"
                )
        elif slug == OUTPUT_METRIC_SLUG:
            if metric.get(METRIC_DISPLAY_UNIT_FIELD) == OUTPUT_DISPLAY_UNIT:
                result["max_power"] = _coerce_int(metric.get(METRIC_MAX_VALUE_FIELD))
            else:
                diagnostic_logger().warning(
                    f"{PROVIDER}: unexpected output display_unit "
                    f"{metric.get(METRIC_DISPLAY_UNIT_FIELD)!r} — max_power stored as NULL, not guessed"
                )
    return result


def _is_class_workout(raw: dict[str, Any]) -> bool:
    """raw[WORKOUT_TYPE_FIELD] == "class" today. Falls back to "has a
    non-null RIDE_ID_FIELD" when WORKOUT_TYPE_FIELD is absent from the raw
    record entirely, since some session-id-shaped field must exist for the
    two-step lookup to be callable at all."""
    if WORKOUT_TYPE_FIELD in raw:
        return raw.get(WORKOUT_TYPE_FIELD) == "class"
    return raw.get(RIDE_ID_FIELD) is not None


def _extract_ride_metadata(details: dict[str, Any], ride_id: str) -> dict[str, Any]:
    """Pure mapping from a successful `GET /api/ride/{ride_id}/details`
    body to the 6 canonical fields (issue #58). Shared by download() and
    the backfill script so this field-mapping logic exists in exactly one
    place — caching and retry policy stay call-site-specific.

    class_type is `class_types[].name`, comma-joined (an Architect
    decision — see the architecture doc's "Field mapping"): `""` (never
    `None`) when the list is empty, since `None` is reserved for "not
    attempted this pass" under the COALESCE-based upsert (see
    "The None-means-not-attempted contract" in that doc) — conflating the
    two would silently make a genuinely successful lookup with no tags
    indistinguishable from a lookup that was never attempted."""
    ride = details.get(RIDE_OBJECT_FIELD) or {}
    instructor = ride.get(INSTRUCTOR_OBJECT_FIELD)
    instructor_name = instructor.get(INSTRUCTOR_NAME_FIELD) if isinstance(instructor, dict) else None
    class_types = details.get(CLASS_TYPES_FIELD) or []
    class_type_names = [
        entry.get(CLASS_TYPE_NAME_FIELD) for entry in class_types
        if isinstance(entry, dict) and entry.get(CLASS_TYPE_NAME_FIELD)
    ]
    return {
        "activity_title": ride.get(CLASS_TITLE_FIELD),
        "instructor_name": instructor_name,
        "class_type": ", ".join(class_type_names),
        "planned_duration_s": ride.get(PLANNED_DURATION_FIELD),
        "provider_class_id": ride_id,
        "difficulty_estimate": ride.get(DIFFICULTY_ESTIMATE_FIELD),
    }


def apply_class_metadata_update(conn: sqlite3.Connection, external_id: str, fields: dict[str, Any]) -> None:
    """Updates ONLY the 6 Peloton-enrichment columns on an existing
    normalized_activities row. Does not call build_canonical_record() and
    is not, and must not become, a parallel path for anything
    discipline/confidence/training_load/start_time computes — this is
    deliberately as narrow as ADR-039's bo_confirmed_valid/bo_confirmed_at
    columns (schema.py's own comment already documents that precedent for
    the same reason: a value some other process legitimately owns,
    supplementary to the row's canonical identity). Caller commits; this
    function does not. Used by scripts/backfill_peloton_workout_details.py.

    Issue #58 adds difficulty_estimate as a 6th column here (the backfill
    script never goes through upsert_normalized_activity()'s COALESCE
    path, so this is the only write path that must carry it for a
    `--retry-failed` run to actually persist it)."""
    conn.execute(
        """
        UPDATE normalized_activities
        SET activity_title = ?, instructor_name = ?, class_type = ?,
            planned_duration_s = ?, provider_class_id = ?, difficulty_estimate = ?
        WHERE provider = 'peloton' AND external_id = ?
        """,
        (
            fields.get("activity_title"), fields.get("instructor_name"), fields.get("class_type"),
            fields.get("planned_duration_s"), fields.get("provider_class_id"),
            fields.get("difficulty_estimate"), external_id,
        ),
    )


def apply_hr_performance_update(conn: sqlite3.Connection, external_id: str, fields: dict[str, Any]) -> None:
    """Issue #47, AC2 (BL-012). Updates ONLY avg_hr, max_hr, max_power, and
    performance_fetch_status on an existing normalized_activities row. Same
    narrow single-writer-invariant exception as apply_class_metadata_update()
    above, same reasoning (schema.py's bo_confirmed_valid/bo_confirmed_at
    precedent). Caller commits; this function does not. Used by
    scripts/backfill_peloton_workout_details.py."""
    conn.execute(
        """
        UPDATE normalized_activities
        SET avg_hr = ?, max_hr = ?, max_power = ?, performance_fetch_status = ?
        WHERE provider = 'peloton' AND external_id = ?
        """,
        (
            fields.get("avg_hr"), fields.get("max_hr"), fields.get("max_power"),
            fields.get("performance_fetch_status"), external_id,
        ),
    )


class PelotonHTTPError(Exception):
    """Non-2xx response not otherwise classified as transient or an auth failure."""


class PelotonOAuthRejected(Exception):
    """The OAuth token endpoint rejected a request with a 4xx status other
    than 429 (rate limit) — e.g. an expired/revoked refresh token, or an
    invalid authorization code. Distinct from PelotonHTTPError so callers
    can react differently: a refresh rejection triggers the AC5 fallback
    to manual recovery; an initial setup-exchange rejection surfaces
    directly to the human running the setup script."""


def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge), RFC 7636, S256 method.

    Only used by the one-time human setup flow — a grant_type=refresh_token
    request has no code_verifier, so this is never called from
    authenticate()'s regular runtime path."""
    verifier = secrets.token_urlsafe(96)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class PelotonConnector(Connector):
    capability_tier = CapabilityTier.TIER_2_UNOFFICIAL

    def __init__(
        self,
        credential_store: CredentialStore,
        session: Any = None,
        base_url: str = DEFAULT_BASE_URL,
    ):
        super().__init__(PROVIDER)
        self._credentials = credential_store
        self._base_url = base_url.rstrip("/")
        # Injectable for testing — this sandbox cannot reach
        # api.onepeloton.com, so every test substitutes a fake session.
        self._session = session if session is not None else _RequestsSession()
        self._active_auth_header: dict[str, str] | None = None

    # --- Feature 3.1: Authentication (dual-path) --------------------------

    def authenticate(self) -> bool:
        # Feature 3.5 (issue #7): OAuth is tried first, but only when this
        # account has actually completed OAuth setup — gated on credential
        # presence, not ConnectorState, so an account that's never run
        # setup sees zero behavior change (AC6), and a successful OAuth
        # refresh can pull an account back to Healthy even from
        # RecoveryRequired (self-healing is the point of this feature).
        if self._credentials.exists(PROVIDER, CRED_OAUTH_REFRESH_TOKEN):
            if self._authenticate_with_oauth():
                return True
            # AC5: OAuth failed for any reason (expired/revoked/rejected
            # refresh token) — fall back to manual recovery within this
            # same call, regardless of ConnectorState. Never falls through
            # to the automated-login path, which stays dead-but-untouched.
            return self._authenticate_with_manual_token()
        if self.get_state() == ConnectorState.RECOVERY_REQUIRED:
            # Per the Soft Degradation policy: once recovery has been
            # requested, do NOT keep hammering the automated login endpoint
            # — Milestone 2 §7 flagged that as its own ToS/behavioral risk.
            # Use the manually-supplied token if one has been provided.
            return self._authenticate_with_manual_token()
        return self._authenticate_with_automated_login()

    # --- Feature 3.5: OAuth+PKCE auth path (issue #7) ----------------------

    def _authenticate_with_oauth(self) -> bool:
        """AC1/AC2: returns True immediately from a cached, unexpired
        access token with no network call; otherwise refreshes if a
        refresh token is stored. Returns False (never raises) on any
        refresh rejection, a missing refresh token, or a missing/expired
        access token with no refresh token available — the caller
        (authenticate()) decides whether to fall back to manual recovery."""
        access_token = self._credentials.get(PROVIDER, CRED_OAUTH_ACCESS_TOKEN)
        expires_at_raw = self._credentials.get(PROVIDER, CRED_OAUTH_EXPIRES_AT)
        expires_at = int(expires_at_raw) if expires_at_raw else 0
        now = int(datetime.now(timezone.utc).timestamp())

        if access_token and expires_at > now + OAUTH_EXPIRY_SAFETY_MARGIN_S:
            self._active_auth_header = {"Authorization": f"Bearer {access_token}"}
            return True

        refresh_token = self._credentials.get(PROVIDER, CRED_OAUTH_REFRESH_TOKEN)
        if not refresh_token:
            return False
        return self._refresh_oauth_token(refresh_token)

    def _refresh_oauth_token(self, refresh_token: str) -> bool:
        """AC2/AC3: exchanges `refresh_token` via grant_type=refresh_token.
        On success, rotates ALL THREE OAuth credential values — the
        refresh token included, since Peloton invalidates the previous one
        on every use (live-confirmed by the BO), the same rotation hazard
        StravaConnector._refresh() already guards against. Returns False on
        PelotonOAuthRejected (logged, not re-raised) so the caller can fall
        back per AC5; lets TransientError propagate unchanged (429/5xx mean
        "try again later," not "this refresh token is bad")."""
        try:
            body = self._exchange_oauth_token({
                "grant_type": "refresh_token",
                "client_id": OAUTH_CLIENT_ID,
                "refresh_token": refresh_token,
            })
        except PelotonOAuthRejected as exc:
            diagnostic_logger().warning(f"{PROVIDER}: OAuth refresh rejected: {exc}")
            return False
        self._persist_oauth_tokens(body)
        return True

    def complete_oauth_setup(self, authorization_code: str, code_verifier: str) -> None:
        """Exchanges a human-obtained authorization code for the first
        access_token/refresh_token pair and persists them. Called once per
        setup (or re-setup) by scripts/setup_peloton_oauth.py — never from
        authenticate()'s regular runtime path. Lets PelotonOAuthRejected/
        TransientError propagate directly to the caller, which is expected
        to show the error and let the human restart the flow (a code is
        single-use, so there is no automatic retry to build here)."""
        body = self._exchange_oauth_token({
            "grant_type": "authorization_code",
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "code": authorization_code,
            "code_verifier": code_verifier,
        })
        self._persist_oauth_tokens(body)

    def _exchange_oauth_token(self, payload: dict[str, str]) -> dict[str, Any]:
        """POSTs `payload` to OAUTH_TOKEN_URL. Returns the parsed JSON body
        on 200. Raises TransientError for 429 (honoring Retry-After,
        ADR-037) and 5xx; raises PelotonOAuthRejected for any other
        non-200 status — Auth0 commonly uses 400 with an invalid_grant body
        for an expired/revoked/reused refresh token, not only 401/403, so
        the whole non-429 4xx range is treated as "rejected.\""""
        try:
            response = self._session.post(OAUTH_TOKEN_URL, json=payload)
        except _TransientHTTPCondition as exc:
            raise TransientError(f"{PROVIDER}: transient error during OAuth token exchange: {exc}", retry_after_s=exc.retry_after_s) from exc

        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            raise TransientError(f"{PROVIDER}: rate limited during OAuth token exchange", retry_after_s=retry_after)
        if response.status_code >= 500:
            raise TransientError(f"{PROVIDER}: server error during OAuth token exchange (status {response.status_code})")
        if response.status_code != 200:
            raise PelotonOAuthRejected(
                f"{PROVIDER}: OAuth token endpoint rejected the request (status {response.status_code})"
            )
        return response.json()

    def _persist_oauth_tokens(self, body: dict[str, Any]) -> None:
        expires_at = int(datetime.now(timezone.utc).timestamp()) + int(body["expires_in"])
        self._credentials.rotate(PROVIDER, CRED_OAUTH_ACCESS_TOKEN, body["access_token"])
        self._credentials.rotate(PROVIDER, CRED_OAUTH_REFRESH_TOKEN, body["refresh_token"])
        self._credentials.rotate(PROVIDER, CRED_OAUTH_EXPIRES_AT, str(expires_at))
        self._active_auth_header = {"Authorization": f"Bearer {body['access_token']}"}

    def _authenticate_with_manual_token(self) -> bool:
        token = self._credentials.get(PROVIDER, CRED_MANUAL_BEARER_TOKEN)
        if not token:
            diagnostic_logger().warning(
                f"{PROVIDER}: in RecoveryRequired with no manual token supplied yet"
            )
            return False
        self._active_auth_header = {"Authorization": f"Bearer {token}"}
        return True

    def _authenticate_with_automated_login(self) -> bool:
        session_id = self._credentials.get(PROVIDER, CRED_SESSION_ID)
        expires_at_raw = self._credentials.get(PROVIDER, CRED_SESSION_EXPIRES_AT)
        expires_at = int(expires_at_raw) if expires_at_raw else 0
        now = int(datetime.now(timezone.utc).timestamp())

        if session_id and expires_at > now + 60:
            self._active_auth_header = {"Cookie": f"peloton_session_id={session_id}"}
            return True

        email = self._credentials.get(PROVIDER, CRED_EMAIL)
        password = self._credentials.get(PROVIDER, CRED_PASSWORD)
        if not email or not password:
            diagnostic_logger().warning(
                f"{PROVIDER}: no stored email/password — user has not connected this account yet"
            )
            return False
        return self._login(email, password)

    def _login(self, email: str, password: str) -> bool:
        try:
            response = self._session.post(
                f"{self._base_url}/auth/login",
                json={"username_or_email": email, "password": password},
                headers={"peloton-platform": "web"},
            )
        except _TransientHTTPCondition as exc:
            raise TransientError(f"{PROVIDER}: transient error during login: {exc}", retry_after_s=exc.retry_after_s) from exc

        if response.status_code in (401, 403):
            # Deliberately not distinguishing "wrong password" from the
            # documented Oct 2025-Jan 2026 "Endpoint no longer accepting
            # requests" 403 pattern here — both are, from TrainIQ's
            # perspective, "automated login isn't working right now," and
            # both correctly route to the same Degraded -> (eventually)
            # RecoveryRequired path. See module docstring, BL-007.
            diagnostic_logger().warning(
                f"{PROVIDER}: automated login rejected (status {response.status_code}) — "
                f"this is the documented failure mode Milestone 2 found evidence of"
            )
            return False
        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            raise TransientError(f"{PROVIDER}: rate limited during login", retry_after_s=retry_after)
        if response.status_code >= 500:
            raise TransientError(f"{PROVIDER}: server error during login (status {response.status_code})")
        if response.status_code != 200:
            raise PelotonHTTPError(f"{PROVIDER}: unexpected login status {response.status_code}")

        body = response.json()
        session_id = body["session_id"]
        expires_at = int(datetime.now(timezone.utc).timestamp()) + ASSUMED_SESSION_LIFETIME_S

        self._credentials.rotate(PROVIDER, CRED_SESSION_ID, session_id)
        self._credentials.rotate(PROVIDER, CRED_SESSION_EXPIRES_AT, str(expires_at))
        self._active_auth_header = {"Cookie": f"peloton_session_id={session_id}"}
        return True

    # --- Feature 3.2: Recovery path ----------------------------------------

    def request_manual_recovery(self) -> str:
        return (
            "TrainIQ could not automatically reconnect to Peloton. This is a known, "
            "documented limitation (Peloton's unofficial API occasionally rejects "
            "automated login). To restore your Peloton data, extract a Bearer Token "
            "from an authenticated browser session and supply it via "
            "submit_manual_recovery()."
        )

    def submit_manual_recovery(self, bearer_token: str) -> None:
        """User-initiated: stores the manually-obtained token and clears the
        way for the next authenticate() call to use it. Does NOT immediately
        force a state transition — the next successful authenticate()/
        download() cycle is what actually moves the connector back toward
        Healthy (ADR-010), consistent with every other connector's pattern
        of only transitioning state based on a real outcome, not an intent."""
        if not bearer_token:
            raise ValueError("Refusing to store an empty manual recovery token")
        self._credentials.rotate(PROVIDER, CRED_MANUAL_BEARER_TOKEN, bearer_token)

    # --- Feature 3.3: Sync (REST only, per R-PELOTON-05) --------------------

    def _authenticated_get(
        self, path: str, params: dict[str, Any] | None = None, not_found_returns_none: bool = False
    ) -> dict[str, Any] | None:
        try:
            response = self._session.get(
                path,
                headers={**self._active_auth_header, "peloton-platform": "web"},
                params=params or {},
            )
        except _TransientHTTPCondition as exc:
            raise TransientError(f"{PROVIDER}: transient error during download: {exc}", retry_after_s=exc.retry_after_s) from exc

        if response.status_code in (401, 403):
            raise AuthenticationError(f"{PROVIDER}: session rejected during download (status {response.status_code})")
        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            raise TransientError(f"{PROVIDER}: rate limited during download", retry_after_s=retry_after)
        if response.status_code >= 500:
            raise TransientError(f"{PROVIDER}: server error during download (status {response.status_code})")
        # Issue #46: only fetch_class_details() opts into this — every
        # other existing call site keeps today's exact behavior (a 404
        # there raises PelotonHTTPError, unchanged).
        if not_found_returns_none and response.status_code == 404:
            return None
        if response.status_code != 200:
            raise PelotonHTTPError(f"{PROVIDER}: unexpected download status {response.status_code}")

        return response.json()

    def fetch_class_session(self, peloton_id: str) -> dict[str, Any] | None:
        """Step 1 of the two-step class-detail lookup (issue #58, resolves
        BL-011). `GET /api/peloton/{peloton_id}` resolves a workout's
        session id to the real ride_id the class repeats under. Returns
        None on a confirmed 404 (never raised as PelotonHTTPError for that
        case) — same contract fetch_class_details() already uses. Raises
        TransientError for 429/5xx (ADR-037, honors Retry-After) and
        AuthenticationError for 401/403, exactly like every other
        authenticated_get call in this connector."""
        if self._active_auth_header is None:
            raise AuthenticationError(f"{PROVIDER}: fetch_class_session() called before a successful authenticate()")
        return self._authenticated_get(
            f"{self._base_url}{SESSION_ENDPOINT_TEMPLATE.format(peloton_id=peloton_id)}",
            not_found_returns_none=True,
        )

    def fetch_class_details(self, ride_id: str) -> dict[str, Any] | None:
        """Public (not `_`-prefixed): also called directly by
        scripts/backfill_peloton_workout_details.py, so the HTTP/retry/
        error-shape logic exists in exactly one place. Returns None for a
        confirmed "this class no longer exists" response (404) — logged by
        the caller, never raised as PelotonHTTPError for that specific
        case. Raises TransientError for 429/5xx (ADR-037, honors
        Retry-After) and AuthenticationError for 401/403 — both let the
        caller's existing retry/degradation handling apply unchanged, same
        as every other authenticated_get call in this connector.

        Must be called with a RESOLVED ride_id (fetch_class_session()'s
        output) — never with a workout's own peloton_id directly, which is
        a session id and 404s on this endpoint every time (issue #58,
        resolves BL-011)."""
        if self._active_auth_header is None:
            raise AuthenticationError(f"{PROVIDER}: fetch_class_details() called before a successful authenticate()")
        return self._authenticated_get(
            f"{self._base_url}{RIDE_DETAIL_ENDPOINT_TEMPLATE.format(ride_id=ride_id)}",
            not_found_returns_none=True,
        )

    def fetch_workout_performance(self, workout_id: str) -> dict[str, Any] | None:
        """Issue #47, AC2. Public (not `_`-prefixed): also called directly
        by scripts/backfill_peloton_workout_details.py, so the HTTP/retry/
        parsing logic exists in exactly one place (#46's reasoning for
        fetch_class_details(), reused). Returns None for ANY failure short
        of AuthenticationError — AC5 explicitly wants every
        performance-fetch failure logged and degraded to NULL, for both
        the live sync path and the backfill tool, never aborting the run
        over one workout's missing HR data. This is a deliberately WIDER
        safety net than fetch_class_details(), which only treats a
        confirmed 404 this way and lets TransientError/429/5xx propagate
        to the Sync Engine's own retry policy for the whole sync attempt —
        correct for supplementary enrichment data, not for a class lookup.

        AuthenticationError (401/403) still propagates unchanged — an
        invalid session is a connector-wide concern (ADR-009's degradation
        path), not a per-workout data gap, and must not be silently
        swallowed here.

        CONFIRMED endpoint/response shape — see PERFORMANCE_ENDPOINT_TEMPLATE
        above."""
        if self._active_auth_header is None:
            raise AuthenticationError(f"{PROVIDER}: fetch_workout_performance() called before a successful authenticate()")
        try:
            body = retry_with_backoff(
                lambda: self._authenticated_get(
                    f"{self._base_url}{PERFORMANCE_ENDPOINT_TEMPLATE.format(workout_id=workout_id)}",
                    params=PERFORMANCE_ENDPOINT_PARAMS,
                ),
                PROVIDER,
            )
        except AuthenticationError:
            raise
        except (TransientError, PelotonHTTPError) as exc:
            diagnostic_logger().warning(
                f"{PROVIDER}: performance fetch failed for workout_id={workout_id!r}: {exc}"
            )
            return None
        return _parse_performance_response(body)

    def download(self, since: str | None = None) -> list[dict[str, Any]]:
        if self._active_auth_header is None:
            raise AuthenticationError(f"{PROVIDER}: download() called before a successful authenticate()")

        # `since` is required by the Connector interface but, per issue #5 /
        # docs/architecture/5-peloton-endpoint-field-mapping.md, is no
        # longer forwarded as a request param: the live-verified evidence
        # only documents this endpoint's *response* pagination fields, not
        # any verified request-side filter param. Same full-history-walk-
        # plus-dedup tradeoff already documented for Eufy (BL-006). Issue
        # #46 gives `since` a second, independent use below: deciding
        # whether a given class workout's detail fetch is worth attempting
        # at all, so a live sync doesn't re-fetch ride details for the
        # entire history on every run.
        me = self._authenticated_get(f"{self._base_url}/api/me")
        user_id = me["id"]
        # Issue #45: resolved once per download() call (not per workout) —
        # this is an account-level setting, not a per-workout field (see
        # architecture doc's "Why account-level, not per-workout"). Attached
        # to every workout dict below so normalize() stays a pure function
        # of its one argument and never itself calls /api/me.
        distance_unit = _resolve_account_distance_unit(me)

        workouts: list[dict[str, Any]] = []
        page = 0
        while True:
            body = self._authenticated_get(
                f"{self._base_url}/api/user/{user_id}/workouts",
                params={"page": page},
            )
            for workout in body.get("data", []):
                # Always set, even when None — never omitted — so
                # normalize() can use .get() without needing to distinguish
                # "key absent" from "key present but None".
                workout["_distance_unit"] = distance_unit
                workouts.append(workout)
            if not body.get("show_next"):
                break
            page += 1

        # Issue #58 (resolves BL-011): two-step resolution, attaching class
        # metadata (title/instructor/class type/planned length/difficulty)
        # to each class workout dict, in place, before returning —
        # connector-internal keys, same pattern as issue #45's
        # _distance_unit. Two independent caches, scoped to this one
        # download() call: session_ride_id_cache (peloton_id -> resolved
        # ride_id) bounds step 1 to one call per distinct session id;
        # ride_details_cache (resolved ride_id -> details) bounds step 2 to
        # one call per distinct ride id, so a BO who retakes the same class
        # twice (two different peloton_ids, same ride_id) still costs only
        # one ride-details call.
        since_epoch = int(since) if since is not None else None
        session_ride_id_cache: dict[Any, str | None] = {}
        ride_details_cache: dict[Any, dict[str, Any] | None] = {}
        for workout in workouts:
            # Computed once per workout, shared by the class-detail lookup
            # (#46) below and the performance fetch (#47 AC2) further down
            # — not recomputed twice for the same workout.
            start_time = workout.get("start_time")
            is_new_since_checkpoint = since_epoch is None or (start_time is not None and start_time > since_epoch)
            # Issue #47, AC2: the performance fetch is independent of class
            # status — any discipline (strength, running, ...) can have a
            # paired HR monitor, not just rides — so it's gated on the
            # checkpoint alone, reusing is_new_since_checkpoint computed
            # above rather than a second, class-only condition.
            if is_new_since_checkpoint:
                performance = self.fetch_workout_performance(workout.get("id"))
                if performance is None:
                    workout["_performance_fetch_status"] = PERFORMANCE_FETCH_STATUS_FAILED
                else:
                    workout["_performance_fetch_status"] = PERFORMANCE_FETCH_STATUS_OK
                    workout["_avg_hr"] = performance.get("avg_hr")
                    workout["_max_hr"] = performance.get("max_hr")
                    workout["_max_power"] = performance.get("max_power")
            # else: not new since checkpoint — every _avg_hr/_max_hr/
            # _max_power/_performance_fetch_status key stays ABSENT, same
            # "not attempted this pass" semantics as the class-metadata
            # keys above — COALESCE preserves whatever is already stored.

            if not _is_class_workout(workout):
                workout["_class_type"] = CLASS_TYPE_NOT_A_CLASS  # cheap, no network, every run
                continue

            if not is_new_since_checkpoint:
                # Deliberately leaves every _class_* key ABSENT — see
                # normalize()'s handling and the COALESCE-based upsert in
                # sync/engine.py. "Absent" here means "not attempted this
                # run," which must stay distinguishable from "attempted
                # and failed."
                continue

            peloton_id = workout.get(RIDE_ID_FIELD)
            if peloton_id not in session_ride_id_cache:
                session = self.fetch_class_session(peloton_id)
                session_ride_id_cache[peloton_id] = session.get(SESSION_RIDE_ID_FIELD) if session is not None else None
            ride_id = session_ride_id_cache[peloton_id]
            if ride_id is None:
                workout["_class_type"] = CLASS_TYPE_LOOKUP_FAILED
                diagnostic_logger().warning(f"{PROVIDER}: class session lookup failed for peloton_id={peloton_id!r}")
                continue

            if ride_id not in ride_details_cache:
                ride_details_cache[ride_id] = self.fetch_class_details(ride_id)
            details = ride_details_cache[ride_id]
            if details is None:
                workout["_class_type"] = CLASS_TYPE_LOOKUP_FAILED
                diagnostic_logger().warning(
                    f"{PROVIDER}: ride-details lookup failed for ride_id={ride_id!r} (peloton_id={peloton_id!r})"
                )
                continue

            fields = _extract_ride_metadata(details, ride_id)
            workout["_class_title"] = fields["activity_title"]
            workout["_instructor_name"] = fields["instructor_name"]
            workout["_class_type"] = fields["class_type"]
            workout["_planned_duration_s"] = fields["planned_duration_s"]
            workout["_provider_class_id"] = fields["provider_class_id"]
            workout["_difficulty_estimate"] = fields["difficulty_estimate"]
        return workouts

    # --- Feature 3.4 (extraction-only, see module docstring) --------------

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        discipline = raw.get("fitness_discipline")
        if discipline is not None and discipline not in _KNOWN_FITNESS_DISCIPLINES:
            # R-PELOTON-07 (semantic drift): log, never silently drop or
            # guess — the same principle already applied to missing load
            # data (ADR-016), now applied to an unrecognized category value.
            diagnostic_logger().warning(
                f"{PROVIDER}: unrecognized fitness_discipline {discipline!r} "
                f"(external_id={raw.get('id')}) — logged, not guessed"
            )

        start_time = raw["start_time"]
        end_time = raw.get("end_time")
        duration_s = end_time - start_time if end_time is not None else None

        total_work = raw.get("total_work")
        avg_power = total_work / duration_s if total_work is not None and duration_s else None

        # Issue #45: distance_m depends on the account's own distance unit
        # (attached to `raw` by download(), see _distance_unit above) — never
        # a hard-coded unit. A workout with no distance at all (e.g.
        # meditation) is not a unit problem, so it's not warned about; only
        # a real distance value with an unresolved unit is.
        raw_distance = raw.get("distance")
        unit_token = raw.get("_distance_unit")
        multiplier = _DISTANCE_UNIT_MULTIPLIERS.get(unit_token)
        if raw_distance is not None and multiplier is None:
            diagnostic_logger().warning(
                f"{PROVIDER}: unresolved distance unit {unit_token!r} "
                f"(external_id={raw.get('id')}) — distance_m stored as NULL, not guessed"
            )
            distance_m = None
        elif raw_distance is not None:
            distance_m = raw_distance * multiplier
        else:
            distance_m = None

        # Issue #47, AC1: read straight off `raw` (not a download()-attached
        # underscore key — see Feature 3.7's module docstring for why).
        # `effort_zones: null` (confirmed to occur) -> every one of these 6
        # fields is None, never zero.
        effort_zones = raw.get(EFFORT_ZONES_FIELD)
        if effort_zones is not None:
            zone_durations = effort_zones.get(HR_ZONE_DURATIONS_FIELD) or {}
            hr_zone_seconds = {
                zone_n: zone_durations.get(zone_key) for zone_n, zone_key in HR_ZONE_DURATION_FIELDS.items()
            }
            effort_points = effort_zones.get(TOTAL_EFFORT_POINTS_FIELD)
        else:
            hr_zone_seconds = {zone_n: None for zone_n in HR_ZONE_DURATION_FIELDS}
            effort_points = None

        return {
            "provider": PROVIDER,
            "external_id": str(raw["id"]),
            "start_time": start_time,
            "duration_s": duration_s,
            # Raw, pre-taxonomy-mapping value — Epic 6 owns the actual
            # strength-classes-modeled-as-rides correction (R-PELOTON-06);
            # this connector only avoids making that worse by preserving
            # the real discipline value distinctly from `ride_type`-style
            # metadata Peloton also reports.
            "discipline_raw": discipline,
            # Issue #47, AC2 (BL-012, resolved): read-only, never derived
            # here — download() is the only place that decides these (the
            # performance-endpoint network fetch, skip-if-already-synced,
            # the PERFORMANCE_FETCH_STATUS_* sentinel). raw.get(...) returns
            # None in every case that must be treated identically by the
            # upsert's COALESCE logic: this workout predates issue #47 AC2
            # entirely, it was skipped as older than the sync checkpoint, or
            # the fetch was attempted but the workout genuinely has no HR
            # monitor paired (effort_zones gives only per-zone durations and
            # an effort-points score, never a plain avg/max bpm — see AC1
            # above, a different, already-confirmed data source).
            "avg_hr": raw.get("_avg_hr"),
            "max_hr": raw.get("_max_hr"),
            # Derived, not directly reported: total_work (joules) / duration_s
            # (seconds) = watts. A legitimate physical derivation, not an
            # approximation — see docs/architecture/5-peloton-endpoint-field-mapping.md.
            "avg_power": avg_power,
            # Issue #47, AC2: same read-only, never-derived treatment as
            # avg_hr/max_hr above — never fabricated from total_work/avg_power.
            "max_power": raw.get("_max_power"),
            "distance_m": distance_m,
            "calories": raw.get("calories"),
            "synced_at": datetime.now(timezone.utc).isoformat(),
            # Issue #46: read-only, never derived here — download() is the
            # only place that decides these (network fetch, skip-if-
            # already-synced, sentinels). raw.get(...) returns None in all
            # three cases that must be treated identically by the upsert's
            # COALESCE logic: this workout predates issue #46's download()
            # entirely, it was skipped as older than the sync checkpoint,
            # or (for the first four) a lookup simply wasn't attempted.
            "activity_title": raw.get("_class_title"),
            "instructor_name": raw.get("_instructor_name"),
            "class_type": raw.get("_class_type"),
            "planned_duration_s": raw.get("_planned_duration_s"),
            "provider_class_id": raw.get("_provider_class_id"),
            "hr_zone_1_s": hr_zone_seconds[1],
            "hr_zone_2_s": hr_zone_seconds[2],
            "hr_zone_3_s": hr_zone_seconds[3],
            "hr_zone_4_s": hr_zone_seconds[4],
            "hr_zone_5_s": hr_zone_seconds[5],
            "effort_points": effort_points,
            # Issue #47, AC2 / issue #57: shared "attempted this pass or
            # not" marker for the one fetch_workout_performance() call —
            # see Feature 3.8's module docstring for why this is not a
            # Peloton-only hr_fetch_status column.
            "performance_fetch_status": raw.get("_performance_fetch_status"),
            # Issue #58: same absence-is-meaningful contract as the 5
            # fields above — None for "not attempted this pass", a real
            # (possibly fractional) value for an attempted, successful
            # lookup.
            "difficulty_estimate": raw.get("_difficulty_estimate"),
        }

    def extract_resume_cursor(self, normalized: dict[str, Any]) -> str | None:
        # Peloton's `start_time` is a raw epoch int (unlike Strava's, which
        # is already an ISO 8601 string), but `sync_checkpoints.last_cursor`
        # round-trips through SQLite's TEXT column affinity as a str. Coerce
        # here so extraction, persistence, and reload all agree on str and
        # the Sync Engine's `candidate_cursor > resume_cursor` comparison
        # never mixes int and str (issue #33).
        start_time = normalized.get("start_time")
        return str(start_time) if start_time is not None else None


def _parse_retry_after(response: Any) -> float | None:
    header = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    if header is None:
        return None
    try:
        return float(header)
    except (TypeError, ValueError):
        return None


class _TransientHTTPCondition(Exception):
    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class _RequestsSession:
    """Real network calls — used in production. Every test substitutes a
    fake with the same .post()/.get() shape, since this sandbox cannot
    reach api.onepeloton.com."""

    def __init__(self):
        import requests
        self._session = requests.Session()

    def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        import requests as _requests
        try:
            return self._session.post(url, json=json, headers=headers, timeout=30)
        except _requests.exceptions.RequestException as exc:
            raise _TransientHTTPCondition(str(exc)) from exc

    def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        import requests as _requests
        try:
            return self._session.get(url, params=params, headers=headers, timeout=30)
        except _requests.exceptions.RequestException as exc:
            raise _TransientHTTPCondition(str(exc)) from exc
