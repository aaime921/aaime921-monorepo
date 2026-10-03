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
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from trainiq.connectors.base import CapabilityTier, Connector, ConnectorState
from trainiq.credentials.store import CredentialStore
from trainiq.logging_setup import diagnostic_logger
from trainiq.sync.engine import AuthenticationError, TransientError

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

# Issue #5: PelotonConnector.normalize() previously assigned raw `distance`
# straight to `distance_m` with no conversion. Verified against
# trainiq/csv_import/peloton_csv.py, which imports the same provider's data
# from a CSV column literally named "Distance (km)" and performs this exact
# conversion — corroborated by a plausibility check on issue #5's real
# captured record (1.2163 km over 299s ≈ 14.6 km/h, a normal indoor-cycling
# pace; nonsensical read as meters). `distance` is kilometers, not meters.
DISTANCE_KM_TO_M_MULTIPLIER = 1000


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

    def _authenticated_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
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
        if response.status_code != 200:
            raise PelotonHTTPError(f"{PROVIDER}: unexpected download status {response.status_code}")

        return response.json()

    def download(self, since: str | None = None) -> list[dict[str, Any]]:
        if self._active_auth_header is None:
            raise AuthenticationError(f"{PROVIDER}: download() called before a successful authenticate()")

        # `since` is required by the Connector interface but, per issue #5 /
        # docs/architecture/5-peloton-endpoint-field-mapping.md, is no
        # longer forwarded: the live-verified evidence only documents this
        # endpoint's *response* pagination fields, not any verified
        # request-side filter param. Same full-history-walk-plus-dedup
        # tradeoff already documented for Eufy (BL-006).
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

        raw_distance = raw.get("distance")
        distance_m = raw_distance * DISTANCE_KM_TO_M_MULTIPLIER if raw_distance is not None else None

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
        }

    def extract_resume_cursor(self, normalized: dict[str, Any]) -> str | None:
        # RawActivity-shaped output (start_time) — the Connector base
        # class's default already does exactly this; overridden here only
        # for explicitness/documentation, not because the default is wrong.
        return normalized.get("start_time")


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
