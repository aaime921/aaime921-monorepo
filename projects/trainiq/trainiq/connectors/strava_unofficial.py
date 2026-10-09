"""
trainiq.connectors.strava_unofficial — Strava session-cookie connector
(issue #18)

Requirements: docs/trainiq/requirements/20-strava-session-cookie-connector.md
Architecture: docs/trainiq/architecture/18-strava-session-cookie-connector.md
Discovery: docs/trainiq/discovery/20-strava-zero-cost-paths.md

**Note on numbering:** the requirements/discovery docs header themselves
"Issue: #20," but the GitHub issue this connector is filed against is #18.
There is exactly one requirements doc matching this topic in the repo, so
this is a stale internal reference from an earlier pass, not an ambiguity.

This connector authenticates against Strava using the `_strava4_session`
browser cookie instead of OAuth — an unofficial, cookie-based access method,
not a documented or officially supported Strava API. It parallels the
open-source `strava-offline` project's approach. The BO has explicitly
accepted the ToS gray-area risk, cookie fragility, and bot-detection/rate-
limiting exposure described in the requirements and discovery docs, as a
free-tier workaround for Strava's June 2026 free-API subscription gate. The
cookie is obtained manually by the BO from their own, already-authenticated
browser session (DevTools -> Application -> Cookies) and pasted in via
`submit_manual_recovery()` — this module does not automate cookie
extraction, browser login, or any interaction with another account.

This connector is independent of `StravaConnector` (OAuth): it does not
import from or modify `trainiq/connectors/strava.py`, uses a distinct
`PROVIDER = "strava_unofficial"` (see architecture doc's "Approach" for why
this is the one load-bearing decision in this design — `connector_state`
is keyed by provider alone, so sharing `"strava"` would conflate the two
connectors' independent lifecycle tracking), and is not wired into
`trainiq/app.py` or `trainiq/setup_wizard.py` in this issue's scope.

**Issue #30 update:** `/api/v3/*` was confirmed (BO's live evidence) to
reject cookie-only auth outright (401) — it requires an OAuth bearer token
Strava never issues for cookie sessions. Both call sites that used to hit
`/api/v3/*` (`download()` and `submit_manual_recovery()`'s validation call)
now use Strava's own web endpoint, `GET /athlete/training_activities`,
which does accept the cookie. See
docs/trainiq/architecture/30-strava-unofficial-web-endpoints.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trainiq.connectors.base import AcquisitionStrategy, CapabilityTier, Connector
from trainiq.credentials.store import CredentialStore
from trainiq.logging_setup import diagnostic_logger
from trainiq.sync.engine import AuthenticationError, TransientError

PROVIDER = "strava_unofficial"

CRED_STRAVA_SESSION_COOKIE = "session_cookie"
CRED_STRAVA_SESSION_OBTAINED_AT = "session_obtained_at"
CRED_STRAVA_SESSION_EXPIRES_AT = "session_expires_at"

DEFAULT_BASE_URL = "https://www.strava.com"  # ADR-007: one named constant, constructor-overridable for tests

# Conservative, unevidenced estimate (per requirements doc) — same
# flagged-not-researched category as PelotonConnector's
# ASSUMED_SESSION_LIFETIME_S / OAUTH_EXPIRY_SAFETY_MARGIN_S.
ASSUMED_SESSION_LIFETIME_S = 7 * 24 * 3600
SESSION_EXPIRY_SAFETY_MARGIN_S = 1 * 24 * 3600

DEFAULT_RETRY_AFTER_S = 3600  # used when a 429 has no Retry-After header

# --- Issue #30: web endpoint + headers ---------------------------------------

TRAINING_ACTIVITIES_PATH = "/athlete/training_activities"

# Required per the BO's live evidence — without these the endpoint returns
# an HTML page instead of JSON (requirements doc Scope).
WEB_ENDPOINT_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
    # Plain, unremarkable desktop-browser UA string. Not trying to mimic a
    # *specific* browser/version closely — this just has to not look like
    # a bare script's default UA (the behavior the BO's evidence is
    # actually gating on), and a version-pinned string would silently go
    # stale. No evidence exists that Strava is more specific than that.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Candidate field names for each *_raw value, tried in order, per item.
# The BO's issue body lists "start_time/date fields" without pinning an
# exact key — the only name in this connector's new surface the evidence
# doesn't nail down (every other field — distance_raw, moving_time_raw,
# elapsed_time_raw, elevation_gain_raw, id, name, display_type,
# activity_type_display_name, commute, private, has_latlng, description —
# is given verbatim in the issue body). Per the Evidence-based principle,
# this is not guessed as a single hardcoded key: the Developer tries these
# in order and takes the first present, so a wrong guess fails loudly
# instead of silently mis-normalizing every activity's start_time (which
# would also corrupt the incremental checkpoint, since
# extract_resume_cursor() keys off start_time).
_START_FIELD_CANDIDATES = ("start_date_local_raw", "start_date_raw", "start_date", "start_day")


class StravaUnofficialHTTPError(Exception):
    """Non-2xx response not otherwise classified as transient or an auth
    failure. Mirrors PelotonHTTPError."""


class StravaUnofficialConnector(Connector):
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
        # Injectable for testing — same reasoning as PelotonConnector:
        # this sandbox cannot reach api.strava.com.
        self._session = session if session is not None else _RequestsSession()
        self._active_cookie: str | None = None

    def list_acquisition_strategies(self) -> list[AcquisitionStrategy]:
        return [AcquisitionStrategy.UNOFFICIAL_SESSION]

    # extract_resume_cursor(): NOT overridden. The Connector base class's
    # default (`normalized.get("start_time")`) already satisfies AC5 —
    # this connector's normalize() output is RawActivity-shaped with an
    # ISO 8601 `start_time`, identical to StravaConnector/PelotonConnector.

    def authenticate(self) -> bool:
        """No network call — checks the stored cookie's conservative
        expiry estimate only. If the estimate is wrong in either direction,
        the first real download() call will surface a 401/403 (clearing
        credentials) or succeed, same trade-off every other connector's
        cached credential check already makes."""
        cookie = self._credentials.get(PROVIDER, CRED_STRAVA_SESSION_COOKIE)
        if not cookie:
            diagnostic_logger().warning(
                f"{PROVIDER}: no session cookie stored — recovery required"
            )
            return False

        expires_at_raw = self._credentials.get(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT)
        expires_at = int(expires_at_raw) if expires_at_raw else 0
        now = int(datetime.now(timezone.utc).timestamp())
        if expires_at <= now + SESSION_EXPIRY_SAFETY_MARGIN_S:
            diagnostic_logger().warning(
                f"{PROVIDER}: stored session cookie is at/past its conservative expiry estimate"
            )
            return False

        self._active_cookie = cookie
        return True

    def request_manual_recovery(self) -> str:
        return (
            "TrainIQ could not authenticate with Strava's unofficial "
            "session-cookie method. Log into Strava in your browser, open "
            "DevTools (Cmd+Opt+I) -> Application -> Cookies, find "
            "`_strava4_session`, copy its value, and supply it via "
            "submit_manual_recovery()."
        )

    def submit_manual_recovery(self, cookie_value: str) -> bool:
        """Validates via GET /athlete/training_activities?page=1 BEFORE
        persisting anything (same AC2 requirement as before — only the
        endpoint changed, since /api/v3/athlete now 401s even for a valid
        cookie). Returns False (never raises) on a rejected/expired cookie
        (401/403, 3xx-to-login, or HTML-not-JSON — all three now classified
        identically, see _authenticated_get); lets TransientError propagate
        unchanged (a 429/5xx during validation isn't evidence the cookie is
        bad, same distinction every other connector's error handling
        already makes)."""
        if not cookie_value:
            raise ValueError("Refusing to store an empty session cookie value")
        try:
            self._authenticated_get(
                TRAINING_ACTIVITIES_PATH, params={"page": 1}, cookie_override=cookie_value
            )
        except (AuthenticationError, StravaUnofficialHTTPError):
            return False

        now = int(datetime.now(timezone.utc).timestamp())
        self._credentials.rotate(PROVIDER, CRED_STRAVA_SESSION_COOKIE, cookie_value)
        self._credentials.rotate(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT, str(now))
        self._credentials.rotate(
            PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT, str(now + ASSUMED_SESSION_LIFETIME_S)
        )
        return True

    def _authenticated_get(
        self, path: str, params: dict[str, Any] | None = None, cookie_override: str | None = None
    ) -> dict[str, Any]:
        """GET against `path` with the Cookie and web-endpoint headers,
        applying one uniform response classification (mirrors
        PelotonConnector's _authenticated_get)."""
        cookie = cookie_override if cookie_override is not None else self._active_cookie
        try:
            response = self._session.get(
                f"{self._base_url}{path}",
                params=params or {},
                headers={"Cookie": f"_strava4_session={cookie}", **WEB_ENDPOINT_HEADERS},
            )
        except _TransientHTTPCondition as exc:
            raise TransientError(
                f"{PROVIDER}: transient error during request: {exc}", retry_after_s=exc.retry_after_s
            ) from exc

        # A 3xx (redirect to login) is an invalid-session signal, not an
        # "unexpected status." _RequestsSession does not follow redirects
        # (allow_redirects=False) for this to be observable at all — the
        # default, redirect-following behavior would silently resolve this
        # to a 200 HTML page, losing the signal entirely before it reaches
        # this method.
        if 300 <= response.status_code < 400:
            self._clear_session_credentials()
            raise AuthenticationError(
                f"{PROVIDER}: session rejected (redirect to {response.headers.get('Location', '?')}) — cookie cleared"
            )
        if response.status_code in (401, 403):
            self._clear_session_credentials()
            raise AuthenticationError(
                f"{PROVIDER}: session rejected (status {response.status_code}) — cookie cleared"
            )
        if response.status_code == 429:
            retry_after = _parse_retry_after(response) or DEFAULT_RETRY_AFTER_S
            raise TransientError(f"{PROVIDER}: rate limited", retry_after_s=retry_after)
        if response.status_code == 404 or response.status_code >= 500:
            raise TransientError(f"{PROVIDER}: transient HTTP status {response.status_code}")
        if response.status_code != 200:
            raise StravaUnofficialHTTPError(f"{PROVIDER}: unexpected status {response.status_code}")

        # A 200 with an HTML body (not JSON) is ALSO an invalid-session
        # signal per AC5 — Strava can serve a login/interstitial page with
        # a 200 rather than a redirect in some flows. Must be reclassified
        # here, not left to raise an uncaught JSON-decode error out of this
        # method.
        try:
            return response.json()
        except ValueError as exc:
            self._clear_session_credentials()
            raise AuthenticationError(
                f"{PROVIDER}: session rejected (non-JSON response body — likely an HTML login page) — cookie cleared"
            ) from exc

    def _clear_session_credentials(self) -> None:
        """Extracted from the inline triple-delete — now called from three
        sites (401/403, 3xx, bad JSON) instead of one, so it's a named
        helper rather than copy-pasted three times."""
        self._credentials.delete(PROVIDER, CRED_STRAVA_SESSION_COOKIE)
        self._credentials.delete(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT)
        self._credentials.delete(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT)

    def download(self, since: str | None = None) -> list[dict[str, Any]]:
        if self._active_cookie is None:
            raise AuthenticationError(f"{PROVIDER}: download() called before a successful authenticate()")

        since_epoch = int(datetime.fromisoformat(since).timestamp()) if since else None

        activities: list[dict[str, Any]] = []
        page = 1
        fetched_count = 0
        total: int | None = None
        while total is None or fetched_count < total:
            body = self._authenticated_get(TRAINING_ACTIVITIES_PATH, params={"page": page})
            batch = body.get("models", [])
            total = body.get("total", 0)
            if not batch:
                break  # defensive fallback — total/fetched_count should already have ended the loop
            fetched_count += len(batch)

            if since_epoch is not None:
                stopped_early = False
                for item in batch:
                    item_epoch = int(
                        datetime.fromisoformat(_extract_start_time_iso(item)).timestamp()
                    )
                    if item_epoch <= since_epoch:
                        stopped_early = True
                        break  # assumes newest-first order — see architecture doc Risks/tradeoffs
                    activities.append(item)
                if stopped_early:
                    break
            else:
                activities.extend(batch)
            page += 1
        return activities

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Maps the web endpoint's *_raw fields onto the same internal
        shape this connector has always produced. Units, per the BO's
        evidence + REST-API-naming-convention inference (flagged, not
        confirmed — see architecture doc Risks/tradeoffs):
          - distance_raw: meters (float), same unit REST's `distance` used.
          - elapsed_time_raw / moving_time_raw: seconds (int). `duration_s`
            uses elapsed_time_raw specifically, for parity with the REST
            connector's `elapsed_time` (total elapsed, not moving-only).
          - elevation_gain_raw: meters (float) — has no home in the current
            canonical shape, so it is read nowhere (not fabricated into an
            existing field, not silently dropped as an error either — just
            genuinely out of this issue's scope).
        """
        return {
            "provider": PROVIDER,
            "external_id": str(raw["id"]),
            "start_time": _extract_start_time_iso(raw),
            "duration_s": raw["elapsed_time_raw"],
            "discipline_raw": raw.get("activity_type_display_name") or raw.get("display_type"),
            # Per requirements AC4 / open question 2: no HR, power, or
            # calories field appears anywhere in the BO's captured field
            # list for this endpoint. Never fabricated — stays None,
            # exactly like today's "never reported by this endpoint" fields.
            "avg_hr": None,
            "max_hr": None,
            "avg_power": None,
            "max_power": None,
            "distance_m": raw.get("distance_raw"),
            "calories": None,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            # Issue #46 (AC3): raw `name` and the same discipline_raw
            # fallback chain (activity_type_display_name, else
            # display_type), persisted under their own names. No
            # download() change needed — `name` is already present in the
            # stored web-endpoint payload.
            "activity_title": raw.get("name"),
            "sport_type_raw": raw.get("activity_type_display_name") or raw.get("display_type"),
        }


def _extract_start_time_iso(raw: dict[str, Any]) -> str:
    for key in _START_FIELD_CANDIDATES:
        if key in raw and raw[key] is not None:
            value = raw[key]
            if key.endswith("_raw") and isinstance(value, (int, float)):
                # Assumed Unix epoch seconds, consistent with the other
                # *_raw fields being machine/numeric values rather than
                # display strings. Timezone of "local_raw" is unconfirmed
                # (local vs. UTC) — see architecture doc Risks/tradeoffs.
                return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
            return str(value)  # assume already a parseable date/ISO string
    raise StravaUnofficialHTTPError(
        f"{PROVIDER}: no recognized start-time field in training_activities "
        f"item (tried {_START_FIELD_CANDIDATES}) — payload shape has "
        f"changed since issue #30's evidence; Developer/BO must re-capture "
        f"a live sample and update _START_FIELD_CANDIDATES"
    )


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
    fake with the same .get() shape, since this sandbox cannot reach
    api.strava.com."""

    def __init__(self):
        import requests
        self._session = requests.Session()

    def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        import requests as _requests
        try:
            # allow_redirects=False: a 3xx (redirect to Strava's login
            # page) must surface as a 3xx to _authenticated_get, not be
            # silently followed to a 200 HTML page (issue #30).
            return self._session.get(
                url, params=params, headers=headers, timeout=30, allow_redirects=False
            )
        except _requests.exceptions.RequestException as exc:
            raise _TransientHTTPCondition(str(exc)) from exc
