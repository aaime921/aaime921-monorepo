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
PER_PAGE = 200


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
        """Validates via GET /api/v3/athlete BEFORE persisting anything —
        per AC2's explicit requirement. Returns False (never raises) on a
        rejected cookie; lets TransientError propagate unchanged (a 429/5xx
        during validation isn't evidence the cookie is bad, same
        distinction every other connector's error handling already makes)."""
        if not cookie_value:
            raise ValueError("Refusing to store an empty session cookie value")
        try:
            self._authenticated_get("/api/v3/athlete", cookie_override=cookie_value)
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
    ) -> Any:
        """GET against `path` with the Cookie header, applying one uniform
        status-code classification (mirrors PelotonConnector's
        _authenticated_get). Returns the parsed JSON body — a list for
        /athlete/activities, a dict for /athlete."""
        cookie = cookie_override if cookie_override is not None else self._active_cookie
        try:
            response = self._session.get(
                f"{self._base_url}{path}",
                params=params or {},
                headers={"Cookie": f"_strava4_session={cookie}"},
            )
        except _TransientHTTPCondition as exc:
            raise TransientError(
                f"{PROVIDER}: transient error during request: {exc}", retry_after_s=exc.retry_after_s
            ) from exc

        if response.status_code in (401, 403):
            self._credentials.delete(PROVIDER, CRED_STRAVA_SESSION_COOKIE)
            self._credentials.delete(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT)
            self._credentials.delete(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT)
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
        return response.json()

    def download(self, since: str | None = None) -> list[dict[str, Any]]:
        if self._active_cookie is None:
            raise AuthenticationError(f"{PROVIDER}: download() called before a successful authenticate()")

        # Checkpoint is an ISO 8601 string (base-class convention); the
        # REST API's `after` param is Unix epoch seconds, same conversion
        # StravaConnector.download() already does before handing it to
        # stravalib.
        after = int(datetime.fromisoformat(since).timestamp()) if since else None

        activities: list[dict[str, Any]] = []
        page = 1
        while True:
            params: dict[str, Any] = {"per_page": PER_PAGE, "page": page}
            if after is not None:
                params["after"] = after
            batch = self._authenticated_get("/api/v3/athlete/activities", params=params)
            if not batch:
                break
            activities.extend(batch)
            page += 1
        return activities

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Identical field mapping to StravaConnector.normalize() — the
        activities-list JSON shape is the same REST endpoint either way,
        cookie or OAuth."""
        return {
            "provider": PROVIDER,
            "external_id": str(raw["id"]),
            "start_time": raw["start_date"],
            "duration_s": raw["elapsed_time"],
            "discipline_raw": raw.get("sport_type") or raw.get("type"),
            "avg_hr": int(raw["average_heartrate"]) if raw.get("average_heartrate") is not None else None,
            "max_hr": raw.get("max_heartrate"),
            "avg_power": int(raw["average_watts"]) if raw.get("average_watts") is not None else None,
            "max_power": raw.get("max_watts"),
            "distance_m": raw.get("distance"),
            # Never fabricated — the activities-list endpoint doesn't
            # report this via cookie auth any more than it does via OAuth
            # (same KNOWN LIMITATION as StravaConnector).
            "calories": None,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }


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
            return self._session.get(url, params=params, headers=headers, timeout=30)
        except _requests.exceptions.RequestException as exc:
            raise _TransientHTTPCondition(str(exc)) from exc
