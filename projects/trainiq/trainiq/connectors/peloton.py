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
  `download()` now also fetches and attaches per-class metadata for every
  `workout_type == "class"` workout, via `fetch_class_details()`. This
  rests on two facts that are NOT independently confirmed against this
  project's own live evidence (the one real record on file,
  `docs/trainiq/verification/peloton-2026-09-28.md`, shows neither a
  `workout_type` nor a `peloton_id` field) — see the flagged constants
  below (`WORKOUT_TYPE_FIELD` through `PLANNED_DURATION_FIELD`) and
  `docs/trainiq/architecture/46-peloton-strava-class-metadata.md`'s Task 1,
  a mandatory live-verification step requiring the BO's manually-supplied
  bearer token against a real account — something this sandboxed routine
  cannot run (no stored Peloton credentials, no network path to
  api.onepeloton.com). Everything downstream of those flagged constants
  (the schema, the skip-if-already-synced + COALESCE upsert safety, the
  backfill tool, the tests' shapes) is unaffected by what that
  verification eventually finds — only the constants themselves, and
  `_is_class_workout()`'s fallback branch, would need to change. See
  BACKLOG.md BL-011.

Feature 3.7 (issue #57 — distance from `performance_graph`, replacing the
  disproved `/api/me` account-unit source): issue #45's
  `ACCOUNT_DISTANCE_UNIT_FIELD = "distance_unit"` read off `GET /api/me`
  (BL-010) is DISPROVED, not merely unconfirmed — the BO's real `/api/me`
  response has no distance-unit field of any kind. `distance_m` is now
  derived per workout from `GET /api/workout/{id}/performance_graph`'s own
  self-describing distance summary (`fetch_workout_performance()` /
  `_parse_performance_response()`), gated only on the same
  `is_new_since_checkpoint` condition issue #46 already computes per
  workout in `download()`'s loop — unconditional across every discipline
  (not just class workouts, not just workouts with a raw `distance`),
  since issue #47 (not yet implemented) will extend the SAME fetch for
  avg/max HR and max power and needs it attempted for every discipline
  too. `performance_fetch_status` is one shared column for both concerns,
  since they come from the one HTTP call. `distance_m`'s upsert semantics
  moved from unconditional-overwrite to COALESCE (sync/engine.py) for the
  same reason issue #46 already needed it for the 6 class-metadata
  columns: a resync that skips an already-synced workout must not erase
  an already-correct value. See
  `docs/trainiq/architecture/57-peloton-distance-performance-graph-source.md`
  and BACKLOG.md BL-010 (closed as disproved).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable

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
# proved the live API actually reports `distance` in some unit, not always
# km — but issue #45's own replacement (an account-level unit read off
# `GET /api/me`) is itself now DISPROVED by issue #57: the BO's real
# `/api/me` response has no distance-unit field of any kind
# (`height_unit`/`weight_unit`/`locale` are present, nothing else). See
# BACKLOG.md BL-010 ("Closed as disproved") and
# docs/trainiq/architecture/57-peloton-distance-performance-graph-source.md.
# `ACCOUNT_DISTANCE_UNIT_FIELD`/`_resolve_account_distance_unit()` are
# removed, not merely unused — distance is now resolved per workout from
# `performance_graph`'s own self-describing `display_unit` (below), never
# from any account-level setting.

# Recognized spellings/synonyms for the two units this project supports
# today. Extend this table (not the lookup logic in
# _resolve_distance_unit_token()) if live verification finds Peloton
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


def _resolve_distance_unit_token(raw_unit: Any) -> str | None:
    """Maps a performance_graph `display_unit` value through
    _DISTANCE_UNIT_ALIASES to a canonical "mi"/"km" token. Returns None
    for a missing/non-string/unrecognized value — never guesses, never
    raises. Pure function of its argument; does no logging itself
    (normalize() owns logging a missing/unknown unit, so it's reported
    exactly once per affected workout, not once per sync run plus once per
    workout). Replaces issue #45's _resolve_account_distance_unit() (see
    above) — same alias table, now applied to a per-record value instead
    of an account-level /api/me field."""
    if not isinstance(raw_unit, str):
        return None
    return _DISTANCE_UNIT_ALIASES.get(raw_unit.strip().lower())


# Issue #57: CONFIRMED, live-evidenced (BO's real account, 2026-10-09) —
# GET this path returns a body with a top-level "summaries" list; the
# entry with slug == "distance" carries "value" (float) and its own
# "display_unit" (e.g. "km") for that workout, self-describing and
# independent of any account-level setting. No query params documented in
# the issue's own capture — PERFORMANCE_ENDPOINT_PARAMS reflects that, not
# a guess. #47 (HR/power, not yet implemented) extends
# _parse_performance_response()'s returned dict with the same fetch rather
# than calling this endpoint a second time.
PERFORMANCE_ENDPOINT_TEMPLATE = "/api/workout/{workout_id}/performance_graph"
PERFORMANCE_ENDPOINT_PARAMS: dict[str, Any] = {}

SUMMARIES_FIELD = "summaries"
SUMMARY_SLUG_FIELD = "slug"
SUMMARY_VALUE_FIELD = "value"
SUMMARY_DISPLAY_UNIT_FIELD = "display_unit"
DISTANCE_SUMMARY_SLUG = "distance"

# "Attempted this pass" outcome, shared by distance (this issue) and
# HR/power (#47, not yet implemented) since both are extracted from the
# one fetch_workout_performance() call. Absent (column default, NULL):
# never attempted this pass — COALESCE in upsert_normalized_activity()
# preserves whatever was already stored, same convention as #46's absent
# _class_* keys.
PERFORMANCE_FETCH_STATUS_OK = "ok"          # attempted, response parsed
                                             # (individual fields may still
                                             # be None/absent)
PERFORMANCE_FETCH_STATUS_FAILED = "failed"  # attempted, fetch itself failed


def _parse_performance_response(body: dict[str, Any]) -> dict[str, Any]:
    """Issue #57: extracts the distance summary from a performance_graph
    response body. Returns {"distance_value": float | None,
    "distance_unit_raw": str | None} — both None if `body` has no
    "summaries" list, or no entry with slug == DISTANCE_SUMMARY_SLUG.
    Never raises, even on a malformed/non-dict entry in `summaries`.

    Issue #47 (not yet implemented) extends this SAME function's returned
    dict with "avg_hr"/"max_hr"/"max_power" read from the same
    already-parsed `body` — same endpoint, same fetch, one response. Do
    not add a second call to PERFORMANCE_ENDPOINT_TEMPLATE for that;
    extend this dict instead."""
    summaries = body.get(SUMMARIES_FIELD)
    distance_value: float | None = None
    distance_unit_raw: str | None = None
    if isinstance(summaries, list):
        for entry in summaries:
            if not isinstance(entry, dict):
                continue
            if entry.get(SUMMARY_SLUG_FIELD) == DISTANCE_SUMMARY_SLUG:
                distance_value = entry.get(SUMMARY_VALUE_FIELD)
                distance_unit_raw = entry.get(SUMMARY_DISPLAY_UNIT_FIELD)
                break
    return {"distance_value": distance_value, "distance_unit_raw": distance_unit_raw}

# Issue #46 — UNCONFIRMED. The one real record on file
# (docs/trainiq/verification/peloton-2026-09-28.md) does not show either
# field; this is the issue's own claim, not yet independently verified.
# Task 1 of docs/trainiq/architecture/46-peloton-strava-class-metadata.md
# is a mandatory, blocking live-verification step against the BO's real
# account — this sandboxed routine cannot run it (no stored Peloton
# credentials, no network path to api.onepeloton.com). If Task 1 finds
# WORKOUT_TYPE_FIELD doesn't exist at all, _is_class_workout()'s fallback
# branch (below) is what's actually exercised; Task 1 must then remove the
# now-dead primary branch rather than leave both live indefinitely.
WORKOUT_TYPE_FIELD = "workout_type"
RIDE_ID_FIELD = "peloton_id"

# Plain sentinel strings stored in the same free-text `class_type` column
# a real Peloton category value would occupy — collision with a real
# Peloton-assigned category is not a realistic concern (Peloton does not
# control this column's vocabulary; this project does).
CLASS_TYPE_NOT_A_CLASS = "not_a_class"      # AC2: just-ride/scenic/free mode
CLASS_TYPE_LOOKUP_FAILED = "lookup_failed"  # AC2/AC7: class, but the detail fetch failed

# Issue #46 — UNCONFIRMED, same Task 1 as above. Peloton's ride/class
# detail response shape has never been captured in this repo. This
# implements the per-ride-id details endpoint (plan (b) in the
# architecture doc); RIDE_DETAIL_JOINS_PARAM documents the cheaper
# joins-on-list alternative (plan (a)) Task 1 should try first — if it
# works, fetch_class_details()/the ride_details_cache in download() below
# can be removed entirely in favor of reading the joined fields directly
# off each workout. That decision is deferred to Task 1, not made here.
RIDE_DETAIL_JOINS_PARAM = {"joins": "ride,ride.instructor"}
RIDE_DETAIL_ENDPOINT_TEMPLATE = "/api/ride/{ride_id}/details"

CLASS_TITLE_FIELD = "title"
INSTRUCTOR_OBJECT_FIELD = "instructor"  # UNCONFIRMED nesting — Task 1 may find instructor data sits elsewhere
INSTRUCTOR_NAME_FIELD = "name"
CLASS_TYPE_RAW_FIELD = "ride_type_id"   # or whatever Task 1 actually finds
PLANNED_DURATION_FIELD = "duration"     # seconds


def _is_class_workout(raw: dict[str, Any]) -> bool:
    """raw[WORKOUT_TYPE_FIELD] == "class" today — the issue's own claim,
    not yet independently confirmed (see WORKOUT_TYPE_FIELD above). Falls
    back to "has a non-null RIDE_ID_FIELD" when WORKOUT_TYPE_FIELD is
    absent from the raw record entirely, since some ride-id-shaped field
    must exist for a per-workout details lookup to be callable at all."""
    if WORKOUT_TYPE_FIELD in raw:
        return raw.get(WORKOUT_TYPE_FIELD) == "class"
    return raw.get(RIDE_ID_FIELD) is not None


def _extract_instructor_name(details: dict[str, Any]) -> str | None:
    instructor = details.get(INSTRUCTOR_OBJECT_FIELD)
    if isinstance(instructor, dict):
        return instructor.get(INSTRUCTOR_NAME_FIELD)
    return None


def apply_class_metadata_update(conn: sqlite3.Connection, external_id: str, fields: dict[str, Any]) -> None:
    """Updates ONLY the 5 Peloton-enrichment columns on an existing
    normalized_activities row. Does not call build_canonical_record() and
    is not, and must not become, a parallel path for anything
    discipline/confidence/training_load/start_time computes — this is
    deliberately as narrow as ADR-039's bo_confirmed_valid/bo_confirmed_at
    columns (schema.py's own comment already documents that precedent for
    the same reason: a value some other process legitimately owns,
    supplementary to the row's canonical identity). Caller commits; this
    function does not. Used by scripts/backfill_peloton_workout_details.py."""
    conn.execute(
        """
        UPDATE normalized_activities
        SET activity_title = ?, instructor_name = ?, class_type = ?,
            planned_duration_s = ?, provider_class_id = ?
        WHERE provider = 'peloton' AND external_id = ?
        """,
        (
            fields.get("activity_title"), fields.get("instructor_name"), fields.get("class_type"),
            fields.get("planned_duration_s"), fields.get("provider_class_id"), external_id,
        ),
    )


def apply_distance_update(conn: sqlite3.Connection, external_id: str, fields: dict[str, Any]) -> None:
    """Updates ONLY distance_m and performance_fetch_status on an existing
    normalized_activities row. Same narrow single-writer exception as
    apply_class_metadata_update() — see that function's docstring for why.
    Caller commits; this function does not. Used by
    scripts/backfill_peloton_workout_details.py."""
    conn.execute(
        """
        UPDATE normalized_activities
        SET distance_m = ?, performance_fetch_status = ?
        WHERE provider = 'peloton' AND external_id = ?
        """,
        (fields.get("distance_m"), fields.get("performance_fetch_status"), external_id),
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
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        super().__init__(PROVIDER)
        self._credentials = credential_store
        self._base_url = base_url.rstrip("/")
        # Injectable for testing — this sandbox cannot reach
        # api.onepeloton.com, so every test substitutes a fake session.
        self._session = session if session is not None else _RequestsSession()
        self._active_auth_header: dict[str, str] | None = None
        # Issue #57: fetch_workout_performance() retries internally (unlike
        # fetch_class_details(), which relies on the Synchronization
        # Engine's own outer retry) — injectable so tests simulating
        # exhausted retries don't actually sleep real backoff delays.
        self._sleep_fn = sleep_fn

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

        UNCONFIRMED endpoint/response shape — see the module-level comment
        above WORKOUT_TYPE_FIELD and RIDE_DETAIL_ENDPOINT_TEMPLATE."""
        if self._active_auth_header is None:
            raise AuthenticationError(f"{PROVIDER}: fetch_class_details() called before a successful authenticate()")
        return self._authenticated_get(
            f"{self._base_url}{RIDE_DETAIL_ENDPOINT_TEMPLATE.format(ride_id=ride_id)}",
            not_found_returns_none=True,
        )

    def fetch_workout_performance(self, workout_id: str) -> dict[str, Any] | None:
        """Public (not `_`-prefixed) — also called directly by
        scripts/backfill_peloton_workout_details.py, so the HTTP/retry/
        parsing logic exists in exactly one place. Wraps its own call in
        retry_with_backoff() and degrades ANY non-auth failure
        (TransientError after retries exhausted, PelotonHTTPError,
        including a 404) to None, logged once — AC3 requires a per-workout
        distance-resolution failure to never abort the whole sync. This
        deliberately differs from fetch_class_details(), which lets
        TransientError propagate to the Synchronization Engine's own outer
        retry — distance/HR data is supplementary enrichment, a class
        lookup failure is not. AuthenticationError still propagates
        unchanged (connector-wide concern, ADR-009)."""
        if self._active_auth_header is None:
            raise AuthenticationError(f"{PROVIDER}: fetch_workout_performance() called before a successful authenticate()")
        try:
            body = retry_with_backoff(
                lambda: self._authenticated_get(
                    f"{self._base_url}{PERFORMANCE_ENDPOINT_TEMPLATE.format(workout_id=workout_id)}",
                    params=PERFORMANCE_ENDPOINT_PARAMS,
                ),
                PROVIDER,
                sleep_fn=self._sleep_fn,
            )
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
        # whether a given workout's per-workout detail fetch(es) are worth
        # attempting at all, so a live sync doesn't re-fetch them for the
        # entire history on every run.
        me = self._authenticated_get(f"{self._base_url}/api/me")
        user_id = me["id"]

        workouts: list[dict[str, Any]] = []
        page = 0
        while True:
            body = self._authenticated_get(
                f"{self._base_url}/api/user/{user_id}/workouts",
                params={"page": page},
            )
            workouts.extend(body.get("data", []))
            if not body.get("show_next"):
                break
            page += 1

        # Issue #46: attach class metadata (title/instructor/class type/
        # planned length) to each class workout dict, in place, before
        # returning — connector-internal keys. ride_details_cache is keyed
        # by ride id for the duration of this one download() call, so a BO
        # who retakes the same on-demand class twice only triggers one
        # network call for it.
        #
        # Issue #57: distance is resolved the same way, from the same
        # checkpoint-gated per-workout performance_graph fetch
        # (fetch_workout_performance()) — unconditional per
        # is_new_since_checkpoint workout, NOT gated on whether the workout
        # is a class or has a raw `distance` value (a strength class has no
        # distance but can have real HR data for #47's own future use of
        # this same fetch). "Absent" for every _class_*/_distance_*/
        # _performance_fetch_status key below means "not attempted this
        # run," which must stay distinguishable from "attempted and
        # failed" — both normalize() and the COALESCE-based upsert in
        # sync/engine.py depend on that distinction.
        since_epoch = int(since) if since is not None else None
        ride_details_cache: dict[Any, dict[str, Any] | None] = {}
        for workout in workouts:
            start_time = workout.get("start_time")
            is_new_since_checkpoint = since_epoch is None or (start_time is not None and start_time > since_epoch)

            if not _is_class_workout(workout):
                workout["_class_type"] = CLASS_TYPE_NOT_A_CLASS  # cheap, no network, every run
            elif is_new_since_checkpoint:
                ride_id = workout.get(RIDE_ID_FIELD)
                if ride_id not in ride_details_cache:
                    ride_details_cache[ride_id] = self.fetch_class_details(ride_id)
                details = ride_details_cache[ride_id]
                if details is None:
                    workout["_class_type"] = CLASS_TYPE_LOOKUP_FAILED
                    diagnostic_logger().warning(f"{PROVIDER}: class lookup failed for ride_id={ride_id!r}")
                else:
                    workout["_class_title"] = details.get(CLASS_TITLE_FIELD)
                    workout["_instructor_name"] = _extract_instructor_name(details)
                    workout["_class_type"] = details.get(CLASS_TYPE_RAW_FIELD)
                    workout["_planned_duration_s"] = details.get(PLANNED_DURATION_FIELD)
                    workout["_provider_class_id"] = ride_id
            # else: not new since checkpoint — every _class_* key stays ABSENT.

            if is_new_since_checkpoint:
                performance = self.fetch_workout_performance(workout.get("id"))
                if performance is None:
                    workout["_performance_fetch_status"] = PERFORMANCE_FETCH_STATUS_FAILED
                else:
                    workout["_performance_fetch_status"] = PERFORMANCE_FETCH_STATUS_OK
                    workout["_distance_value"] = performance.get("distance_value")
                    workout["_distance_unit"] = _resolve_distance_unit_token(performance.get("distance_unit_raw"))
            # else: not new since checkpoint — _performance_fetch_status/
            # _distance_* keys stay ABSENT (see docstring block above).
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

        # Issue #57: distance_m is now derived from the per-workout
        # performance_graph fetch (attached to `raw` by download() as
        # _distance_value/_distance_unit/_performance_fetch_status), never
        # from any account-level setting (issue #45's ACCOUNT_DISTANCE_UNIT_FIELD
        # is disproved/removed — see BACKLOG.md BL-010). A workout with no
        # `distance` at all (e.g. meditation) is not a fetch/unit problem,
        # so it's never warned about.
        raw_distance = raw.get("distance")
        if raw_distance is None:
            distance_m = None
        elif raw.get("_performance_fetch_status") != PERFORMANCE_FETCH_STATUS_OK:
            # Either not attempted this pass (key absent — checkpoint-
            # gated, silent, same convention as #46's absent _class_*), or
            # the fetch itself failed (fetch_workout_performance() already
            # logged that failure once, at fetch time — do not log it
            # again here).
            distance_m = None
        else:
            distance_value = raw.get("_distance_value")
            unit_token = raw.get("_distance_unit")
            multiplier = _DISTANCE_UNIT_MULTIPLIERS.get(unit_token)
            if distance_value is not None and multiplier is not None:
                distance_m = distance_value * multiplier
            else:
                diagnostic_logger().warning(
                    f"{PROVIDER}: performance fetch succeeded but yielded no usable "
                    f"distance summary (external_id={raw.get('id')!r}) — distance_m "
                    f"stored as NULL, not guessed"
                )
                distance_m = None

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
            # Genuinely unavailable in the real API response (issue #5):
            # `effort_zones` gives only per-heart-rate-zone durations and a
            # total effort-points score, never a plain avg/max bpm — never
            # fabricated from it, per the project's never-fabricate standard.
            "avg_hr": None,
            "max_hr": None,
            # Derived, not directly reported: total_work (joules) / duration_s
            # (seconds) = watts. A legitimate physical derivation, not an
            # approximation — see docs/architecture/5-peloton-endpoint-field-mapping.md.
            "avg_power": avg_power,
            # Genuinely unavailable from this endpoint — never fabricated
            # from total_work or anything else.
            "max_power": None,
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
            # Issue #57: shared between distance (this issue) and HR/power
            # (#47, not yet implemented) — both come from the one
            # fetch_workout_performance() call, so one status column
            # suffices (see peloton.py's PERFORMANCE_FETCH_STATUS_OK docstring).
            "performance_fetch_status": raw.get("_performance_fetch_status"),
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
