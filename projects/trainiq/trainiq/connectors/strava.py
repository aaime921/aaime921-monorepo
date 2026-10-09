"""
trainiq.connectors.strava — Epic 1: Strava Connector

Feature 1.1 (Authentication):
  OAuth 2.0 against stravalib's Client, with automatic token refresh that
  always persists the LATEST refresh token — Strava issues a new refresh
  token on every refresh and invalidates the previous one (Milestone 1 §2).
  A naive implementation that reuses an old refresh token breaks silently
  after the first refresh cycle; this is the specific bug this module is
  built to avoid.

Feature 1.2 (Sync):
  `after`-timestamp incremental polling (ADR-006) via stravalib's
  `get_activities(after=...)`. Historical backfill and resume are NOT
  reimplemented here — they're handled generically by the Synchronization
  Engine (Epic 0.6), which already checkpoints per (provider, strategy) and
  resumes correctly across process restarts (see test_sync_engine.py).
  stravalib's `RateLimitExceeded` is translated into `trainiq.sync.engine
  .TransientError` so the Sync Engine's existing, already-tested
  exponential-backoff retry policy applies here without new retry logic.

Feature 1.3 (Normalization mapping — SCOPE NOTE):
  `normalize()` here does ONLY per-connector raw field extraction, matching
  the informal shape already established by Feature 0.7's `RawActivity`
  dataclass (provider, external_id, start_time, duration_s, discipline_raw,
  avg_hr, max_hr, avg_power, max_power, distance_m, calories, synced_at).
  It does NOT apply the canonical discipline taxonomy or compute TRIMP/TSS
  — per the Tier B roadmap's explicit dependency correction, that's Epic 6
  (Normalization Engine Core)'s job, and Epic 6 does not exist yet. This
  connector is usable end-to-end (auth + sync + raw persistence) without
  Epic 6; only the canonical-schema half of "normalization" is deferred.

  Per the Chief Architect's explicit decision after the Epic 0 self-review
  (Finding 1), the `normalize()` return shape is deliberately left as a
  plain dict, not a new shared formal type — that formalization is an
  Epic 6 prerequisite, not something to introduce here.

KNOWN LIMITATION (documented, not silently dropped): Strava's activity-list
endpoint (`get_activities`, `SummaryActivity`) does not include `calories`
— only the per-activity detail endpoint (`get_activity`, `DetailedActivity`)
does, at the cost of one additional API call per activity against the same
rate limit Milestone 1 already flagged as the binding constraint on
historical backfill duration. `calories` is therefore `None` for every
activity returned by this connector's `download()`+`normalize()` path in
this Epic — never fabricated, per Constitution Principle 1 — and fetching
it is left as an explicit, separately-scoped enhancement rather than
silently bundled into this feature at extra rate-limit cost that wasn't
budgeted for.

NOT VERIFIED IN THIS SANDBOX: this module's network calls cannot be
exercised against the real Strava API here (api.strava.com is not in this
environment's allowed egress list). All tests use a fake stravalib Client
injected via the constructor. A real end-to-end OAuth + sync run against a
live Strava account and a real Developer ID app registration has to happen
on your machine — flagged the same way Feature 0.1's packaging DoD was.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from stravalib import Client as StravalibClient
from stravalib import exc as stravalib_exc

from trainiq.connectors.base import CapabilityTier, Connector
from trainiq.credentials.store import CredentialStore
from trainiq.logging_setup import diagnostic_logger
from trainiq.sync.engine import AuthenticationError, TransientError

PROVIDER = "strava"

# credentials_metadata / Keychain credential_type keys, per Feature 0.2's
# "one item per provider per credential type" convention.
CRED_REFRESH_TOKEN = "refresh_token"
CRED_ACCESS_TOKEN = "access_token"
CRED_EXPIRES_AT = "expires_at"


def _client_id_and_secret() -> tuple[str, str]:
    """App-level OAuth credentials, loaded from environment rather than
    hardcoded — these are TrainIQ's own registered-app identifiers, not a
    per-user secret, but there's no reason to bake them into source either."""
    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set in the environment. "
            "Register an app at https://www.strava.com/settings/api to obtain these."
        )
    return client_id, client_secret


class StravaConnector(Connector):
    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def __init__(
        self,
        credential_store: CredentialStore,
        stravalib_client: StravalibClient | None = None,
    ):
        super().__init__(PROVIDER)
        self._credentials = credential_store
        # Injectable for testing — this sandbox cannot reach api.strava.com,
        # so every test constructs this with a fake client instead.
        self._client = stravalib_client if stravalib_client is not None else StravalibClient()

    # --- Feature 1.1: Authentication ------------------------------------

    def authenticate(self) -> bool:
        refresh_token = self._credentials.get(PROVIDER, CRED_REFRESH_TOKEN)
        if not refresh_token:
            diagnostic_logger().warning(
                f"{PROVIDER}: no refresh token stored — user has not connected this account yet"
            )
            return False

        access_token = self._credentials.get(PROVIDER, CRED_ACCESS_TOKEN)
        expires_at_raw = self._credentials.get(PROVIDER, CRED_EXPIRES_AT)
        expires_at = int(expires_at_raw) if expires_at_raw else 0
        now = int(datetime.now(timezone.utc).timestamp())

        # A 5-minute safety margin before actual expiry avoids a race where
        # the token expires mid-request rather than being refreshed just before.
        if access_token and expires_at > now + 300:
            self._client.access_token = access_token
            return True

        return self._refresh(refresh_token)

    def _refresh(self, refresh_token: str) -> bool:
        client_id, client_secret = _client_id_and_secret()
        try:
            token = self._client.refresh_access_token(
                client_id=client_id, client_secret=client_secret, refresh_token=refresh_token
            )
        except stravalib_exc.RateLimitExceeded as exc:
            # Refreshing a token still counts against the rate limit — this
            # is a transient condition, not an auth failure, so it should be
            # retried by the Sync Engine, not treated as Degraded.
            # ADR-037: pass through stravalib's authoritative wait time
            # rather than letting the generic exponential backoff guess.
            raise TransientError(
                f"{PROVIDER}: rate limited while refreshing token: {exc}",
                retry_after_s=exc.timeout,
            ) from exc
        except stravalib_exc.AuthError as exc:
            diagnostic_logger().warning(f"{PROVIDER}: refresh_access_token failed: {exc}")
            return False

        # THE bug this module exists to avoid: persist the NEW refresh
        # token every time, never assume the old one is still valid
        # (Milestone 1 §2 — Strava rotates and invalidates on every refresh).
        self._credentials.rotate(PROVIDER, CRED_REFRESH_TOKEN, token.refresh_token)
        self._credentials.rotate(PROVIDER, CRED_ACCESS_TOKEN, token.access_token)
        self._credentials.rotate(PROVIDER, CRED_EXPIRES_AT, str(token.expires_at))
        self._client.access_token = token.access_token
        return True

    # --- Feature 1.2: Sync ------------------------------------------------

    def download(self, since: str | None = None) -> list[dict[str, Any]]:
        after = datetime.fromisoformat(since) if since else None
        try:
            activities = list(self._client.get_activities(after=after))
        except stravalib_exc.RateLimitExceeded as exc:
            # ADR-037: pass through stravalib's authoritative wait time.
            raise TransientError(
                f"{PROVIDER}: rate limited during get_activities: {exc}",
                retry_after_s=exc.timeout,
            ) from exc
        except stravalib_exc.LoginRequired as exc:
            # Session went bad mid-run (e.g. token revoked externally) —
            # this is an auth failure, not a transient one.
            raise AuthenticationError(f"{PROVIDER}: session invalid during get_activities: {exc}") from exc

        return [self._activity_to_raw_dict(a) for a in activities]

    @staticmethod
    def _activity_to_raw_dict(activity) -> dict[str, Any]:
        """Converts a stravalib SummaryActivity into a plain, JSON-serializable
        dict — this is what gets persisted verbatim to raw_activities."""
        return {
            "id": activity.id,
            "start_date": activity.start_date.isoformat() if activity.start_date else None,
            "elapsed_time": int(activity.elapsed_time) if activity.elapsed_time is not None else None,
            "sport_type": str(activity.sport_type) if activity.sport_type else None,
            "type": str(activity.type) if activity.type else None,
            "average_heartrate": activity.average_heartrate,
            "max_heartrate": activity.max_heartrate,
            "average_watts": activity.average_watts,
            "max_watts": activity.max_watts,
            "distance": float(activity.distance) if activity.distance is not None else None,
            # Issue #46: for a Peloton-synced ride, Strava's own `name`
            # already carries the class title and instructor (e.g. "45 min
            # Power Zone Max Ride with Matt Wilpers") — previously read
            # nowhere in this connector. Never fabricated: None when
            # Strava itself reports no name.
            "name": activity.name if activity.name else None,
        }

    # --- Feature 1.3 (scope-limited, see module docstring): raw extraction only ---

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Extracts Strava's raw fields into the RawActivity-shaped dict
        (Feature 0.7). Does NOT apply discipline taxonomy or compute
        TRIMP/TSS — deferred to Epic 6, per this module's docstring."""
        discipline_raw = raw["sport_type"] or raw["type"]
        return {
            "provider": PROVIDER,
            "external_id": str(raw["id"]),
            "start_time": raw["start_date"],
            "duration_s": raw["elapsed_time"],
            # Raw, pre-taxonomy-mapping vocabulary — sport_type is Strava's
            # newer, more granular field; fall back to the older `type` field
            # if a given activity somehow lacks it.
            "discipline_raw": discipline_raw,
            "avg_hr": int(raw["average_heartrate"]) if raw["average_heartrate"] is not None else None,
            "max_hr": raw["max_heartrate"],
            "avg_power": int(raw["average_watts"]) if raw["average_watts"] is not None else None,
            "max_power": raw["max_watts"],
            "distance_m": raw["distance"],
            # Never fabricated — the summary endpoint genuinely doesn't
            # report this (see module docstring's KNOWN LIMITATION).
            "calories": None,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            # Issue #46 (AC3): raw `name` persisted independent of any
            # Peloton link — the linked-pair precedence (AC4) is enforced
            # entirely by the schema (these columns are NULL on every
            # Peloton row's own counterpart query), not by anything here.
            "activity_title": raw.get("name"),
            "sport_type_raw": discipline_raw,
        }
