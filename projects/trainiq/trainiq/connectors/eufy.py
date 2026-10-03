"""
trainiq.connectors.eufy — Epic 2: Eufy Life Connector, corrected in
RC1-HF-003 against live-account evidence

Feature 2.1 (Cloud API authentication & sync — primary strategy, Milestone 3 §3.1):
  Hand-rolled HTTP client against `home-api.eufylife.com`, per the Milestone 3
  recommendation to NOT depend on the small, single/few-maintainer reverse-
  engineered wrapper libraries — the same "own the client, don't depend on a
  side project" logic already applied to Peloton's risk assessment.

  RC1-HF-003 CORRECTION, verified against a real Eufy account, not inferred:
  the original login request (email+password only, no headers) was
  INCOMPLETE. A live test confirmed the login endpoint requires an
  additional `client_id`/`client_secret` pair in the JSON body and a
  `category: Health` header — both present in every working public example
  (`github.com/robbalmbra/eufy-api`) but never included in this connector's
  first implementation. Without them, the login endpoint returned HTTP 200
  with an application-level `{"res_code":500,"message":"Service is
  temporarily unavailable..."}` — a generic, misleading error rather than a
  specific "missing field" error, which is why this went undetected until
  live verification. Fixed here; see BACKLOG.md, RC1-HF-003.

  Auth uses a non-standard `token:` header (not `Authorization: Bearer`) on
  subsequent calls, per Milestone 3 §3.1 — unchanged, already correct.

  RC1-HF-003 SECOND FINDING, also verified live: the login response itself
  includes a `devices` array — device discovery does NOT require the
  separate `GET /device/` call Milestone 3 left as an unconfirmed open
  question (BACKLOG.md, BL-006). `device_id` is now discovered automatically
  from the login response; manual entry is no longer required. `device_id`
  remains an optional constructor parameter (for a caller that already
  knows it, or a future non-interactive use), but is no longer mandatory.

  HONEST LIMITATION, still standing: Milestone 3's Open Question 1 about
  the exact shape of the `/v1/device/.../data` history endpoint's pagination
  and date-range parameters remains unconfirmed — RC1-HF-003 verified login
  and device discovery, not the full sync/history contract. See BL-006's
  remaining scope.

  Also honest: Milestone 3 documented that the login response includes a
  `refresh_token`, but never confirmed a dedicated refresh-token endpoint
  exists for EufyLife (unlike Strava, where Milestone 1 explicitly verified
  one). Rather than guess at an unconfirmed endpoint, this connector
  re-authenticates via full email+password login whenever the cached access
  token is missing or expired. The `refresh_token` is still persisted
  (harmless, future-proofing) but is not currently used for anything.

Feature 2.2 (Normalization mapping — extraction only, same scope limit as
  Strava's Feature 1.3): maps into the `WeighIn`-shaped dict (Feature 0.7),
  treating every body-composition field as optional per Milestone 3 §4 —
  the metric set is genuinely model-dependent and sparse, not a fixed
  schema. Canonical taxonomy/normalization semantics remain Epic 6's job.

  ISSUE #1 CORRECTION, verified against a real Eufy account (2026-09-27):
  `scale_data.weight` is in deci-kilograms (0.1 kg units), not kilograms —
  raw values like `829.5`/`883.5` only become plausible human body weights
  (`82.95`/`88.35`) once divided by 10. `normalize()` now applies this
  conversion. The accompanying `body_fat`/`muscle_mass` values were
  audited against the same captured payloads and are already correctly
  scaled (normal percentage/BMI ranges) — no change made to those two.

Feature 2.3 (Acquisition strategy plumbing — forward-looking, not BLE
  itself, per the Tier A roadmap's explicit scope limit): reports Cloud as
  the only implemented strategy (ADR-011/012). The interface is shaped to
  accept a BLE strategy later without a connector rewrite, but BLE itself
  is out of scope for this epic.

ADR-037 (Provider Directed Retry Policy): a standard HTTP `Retry-After`
header on a 429 response is honored as `retry_after_s` when present,
exactly the same contract Strava's connector already established — Eufy
inherits this automatically, per the Chief Architect's stated reason for
sequencing Epic 2 after ADR-037 rather than before it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from trainiq.connectors.base import AcquisitionStrategy, CapabilityTier, Connector, RecordKind
from trainiq.credentials.store import CredentialStore
from trainiq.logging_setup import diagnostic_logger
from trainiq.sync.engine import AuthenticationError, TransientError

PROVIDER = "eufy"

# ADR-007: base URL is configuration, not a literal scattered through the
# codebase. Unlike Strava (constrained by stravalib's own internal hardcoding
# — see BL-001), Eufy's connector owns its full HTTP client, so this can be
# fully honored here, not just satisfied at TrainIQ's own layer.
DEFAULT_BASE_URL = "https://home-api.eufylife.com/v1"

# RC1-HF-003: verified against a live account (github.com/robbalmbra/eufy-api's
# example, confirmed to work). This is TrainIQ's own registered-app identity
# with Eufy's backend, not a per-user secret — the same category of constant
# as Strava's STRAVA_CLIENT_ID, just embedded here rather than sourced from
# the environment, since it's a fixed, publicly-known value for the EufyLife
# app specifically (a DIFFERENT pair, "eufyhome-app", belongs to the older,
# unrelated EufyHome plugs/vacuum app — not to be confused with this one).
EUFY_APP_CLIENT_ID = "eufy-app"
EUFY_APP_CLIENT_SECRET = "8FHf22gaTKu7MZXqz5zytw"

CRED_EMAIL = "email"
CRED_PASSWORD = "password"
CRED_ACCESS_TOKEN = "access_token"
CRED_REFRESH_TOKEN = "refresh_token"  # stored, currently unused — see module docstring
CRED_EXPIRES_AT = "expires_at"

# Eufy's login response doesn't document a token lifetime the way Strava's
# does (Milestone 1's 6-hour figure has no Eufy equivalent in Milestone 3's
# research). Conservative default; not evidenced, flagged as such.
ASSUMED_TOKEN_LIFETIME_S = 3600

# Issue #1: verified against a real Eufy account (2026-09-27) — raw
# scale_data.weight values (e.g. 829.5, 883.5) are only plausible as human
# body weights once divided by 10, indicating the field is in
# deci-kilograms (0.1 kg units), not kilograms. body_fat/muscle_mass were
# separately observed to already be correctly scaled in the same captured
# payloads — this divisor applies to weight only.
WEIGHT_DECI_KG_TO_KG_DIVISOR = 10


class EufyHTTPError(Exception):
    """Non-2xx response not otherwise classified as transient or an auth failure."""


class EufyConnector(Connector):
    capability_tier = CapabilityTier.TIER_3_MULTI_STRATEGY
    record_kind = RecordKind.WEIGH_IN  # Epic 6 discovery, Q2
    # BL-006: verified live via a real network experiment
    # (scripts/debug_eufy.py --since), not inferred — GET
    # /device/{id}/data returns byte-for-byte identical responses
    # regardless of the start_time value sent (absent, current checkpoint,
    # or a value in the year 2286). The mechanism behind this is NOT
    # established (wrong parameter name vs. wrong endpoint vs. genuinely
    # unsupported are all still consistent with the evidence) — only the
    # observable consequence is: this connector cannot filter, full stop.
    supports_incremental_sync = False

    def __init__(
        self,
        credential_store: CredentialStore,
        device_id: Optional[str] = None,
        session: Any = None,
        base_url: str = DEFAULT_BASE_URL,
    ):
        super().__init__(PROVIDER)
        self._credentials = credential_store
        # RC1-HF-003: device_id is now OPTIONAL — verified live that the
        # login response itself includes a `devices` array, so device
        # discovery no longer requires the user to supply this manually
        # (Milestone 3's original, unconfirmed assumption). A caller that
        # already knows the device_id (e.g. loaded from config.json on a
        # subsequent run) can still pass it directly.
        self._device_id = device_id
        self._base_url = base_url.rstrip("/")
        # Injectable for testing — this sandbox cannot reach
        # home-api.eufylife.com, so every test substitutes a fake session.
        self._session = session if session is not None else _RequestsSession()
        self._access_token: str | None = None
        # RC1-HF-003: populated from the login response's `devices` field —
        # the setup wizard reads this to auto-select (or offer a choice of)
        # the account's scale(s) without asking the user to know a device_id.
        self._discovered_devices: list[dict[str, Any]] = []

    def discovered_devices(self) -> list[dict[str, Any]]:
        """Whatever `devices` the most recent successful login returned —
        empty before any login, or if the response had no such field.
        RC1-HF-003: verified live to be populated directly by the login
        response itself, no separate `GET /device/` call required."""
        return list(self._discovered_devices)

    def select_device(self, device_id: str) -> None:
        """Finalizes which discovered device this connector should sync —
        called by the setup wizard after presenting a choice (or
        auto-selecting when exactly one device was discovered)."""
        self._device_id = device_id

    # --- Feature 2.1a: Authentication (email+password login) --------------

    def authenticate(self) -> bool:
        cached_token = self._credentials.get(PROVIDER, CRED_ACCESS_TOKEN)
        expires_at_raw = self._credentials.get(PROVIDER, CRED_EXPIRES_AT)
        expires_at = int(expires_at_raw) if expires_at_raw else 0
        now = int(datetime.now(timezone.utc).timestamp())

        if cached_token and expires_at > now + 300:
            self._access_token = cached_token
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
                f"{self._base_url}/user/v2/email/login",
                json={
                    "client_id": EUFY_APP_CLIENT_ID,
                    "client_secret": EUFY_APP_CLIENT_SECRET,
                    "email": email,
                    "password": password,
                },
                headers={"category": "Health"},
            )
        except _TransientHTTPCondition as exc:
            raise TransientError(f"{PROVIDER}: transient error during login: {exc}", retry_after_s=exc.retry_after_s) from exc

        if response.status_code == 401 or response.status_code == 403:
            diagnostic_logger().warning(f"{PROVIDER}: login rejected (status {response.status_code})")
            return False
        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            raise TransientError(f"{PROVIDER}: rate limited during login", retry_after_s=retry_after)
        if response.status_code >= 500:
            raise TransientError(f"{PROVIDER}: server error during login (status {response.status_code})")
        if response.status_code != 200:
            raise EufyHTTPError(f"{PROVIDER}: unexpected login status {response.status_code}")

        body = response.json()
        res_code = body.get("res_code")
        if res_code != 1 or "access_token" not in body:
            diagnostic_logger().warning(
                f"{PROVIDER}: login rejected at application level "
                f"(res_code={res_code!r}, message={body.get('message')!r})"
            )
            return False

        access_token = body["access_token"]
        refresh_token = body.get("refresh_token")
        expires_at = now_plus_assumed_lifetime()

        self._credentials.rotate(PROVIDER, CRED_ACCESS_TOKEN, access_token)
        if refresh_token:
            self._credentials.rotate(PROVIDER, CRED_REFRESH_TOKEN, refresh_token)
        self._credentials.rotate(PROVIDER, CRED_EXPIRES_AT, str(expires_at))
        self._access_token = access_token
        # RC1-HF-003: capture whatever `devices` the login response
        # included — verified live to be present, not assumed.
        self._discovered_devices = list(body.get("devices", []))
        return True

    # --- Feature 2.1b: Sync (device history) — see module docstring's -----
    # --- honest limitation about endpoint-shape confidence ----------------

    def download(self, since: str | None = None) -> list[dict[str, Any]]:
        if not self._access_token:
            raise AuthenticationError(f"{PROVIDER}: download() called before a successful authenticate()")

        params: dict[str, Any] = {}
        if since:
            params["start_time"] = since

        try:
            response = self._session.get(
                f"{self._base_url}/device/{self._device_id}/data",
                headers={"token": self._access_token},
                params=params,
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
            raise EufyHTTPError(f"{PROVIDER}: unexpected download status {response.status_code}")

        body = response.json()
        return list(body.get("data", []))

    # --- Feature 2.2 (extraction-only, see module docstring) --------------

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Maps a single raw device-data record into the WeighIn-shaped
        dict (Feature 0.7). RC1-HF-003 CORRECTION: the real response
        nests measurements under `scale_data`, not at the top level —
        `download()`'s top-level fields are `id`/`device_id`/`create_time`/
        `update_time`; the actual weight/body-composition values live in
        `raw["scale_data"]`. Field NAMES within scale_data (`weight`,
        `body_fat`, `muscle_mass`) are carried over unchanged from the
        prior (top-level) assumption — this is the existing evidence, not
        a new guess; if these specific names are wrong, that's a separate,
        still-open question the exact scale_data payload doesn't resolve
        without seeing one populated example. Every field stays optional
        per Milestone 3 §4 — the metric set is genuinely sparse and
        model-dependent, never padded with a fabricated value.

        ISSUE #1: `weight` is deci-kilograms, divided here by
        WEIGHT_DECI_KG_TO_KG_DIVISOR before assignment — see module
        docstring."""
        scale_data = raw.get("scale_data") or {}
        raw_weight = scale_data.get("weight")
        return {
            "provider": PROVIDER,
            "external_id": str(raw["id"]),
            "timestamp": raw["create_time"],
            "weight_kg": (
                raw_weight / WEIGHT_DECI_KG_TO_KG_DIVISOR if raw_weight is not None else None
            ),
            "body_fat_pct": scale_data.get("body_fat"),  # confirmed correctly scaled, see module docstring
            "muscle_mass_pct": scale_data.get("muscle_mass"),  # confirmed correctly scaled, see module docstring
        }

    def extract_resume_cursor(self, normalized: dict[str, Any]) -> str | None:
        """Resolves BL-005: WeighIn-shaped output uses `timestamp`, not
        `start_time` (Connector base class's default) — the base default
        would silently return None for every Eufy record, which is exactly
        the bug that surfaced as a failing end-to-end test before this
        override existed.

        CORRECTED live-account finding: `timestamp` is NOT an ISO 8601
        string as originally (never-live-verified) assumed — it's a Unix
        epoch integer, confirmed directly from a real sync_checkpoints row
        (`1781160591`). The base class's contract (`base.py`, ADR-013
        refinement) requires the returned value to be "safely orderable
        via a plain string `>` comparison" — an unconverted int violates
        that contract the moment it round-trips through the `TEXT`-affinity
        `sync_checkpoints.last_cursor` column and comes back as a `str` on
        the next run, producing `int > str` -> TypeError. Explicit `str()`
        conversion here is what the contract has always required; this is
        Eufy coming into compliance with an existing rule, not a new one.
        """
        value = normalized.get("timestamp")
        return str(value) if value is not None else None

    # --- Feature 2.3: Acquisition strategy plumbing ------------------------

    def list_acquisition_strategies(self) -> list[AcquisitionStrategy]:
        # Cloud only, in this epic. BLE is deferred (Tier A roadmap,
        # Feature 2.3) — the interface accepts it later without a rewrite,
        # but it is not implemented here.
        return [AcquisitionStrategy.CLOUD]


def now_plus_assumed_lifetime() -> int:
    return int(datetime.now(timezone.utc).timestamp()) + ASSUMED_TOKEN_LIFETIME_S


def _parse_retry_after(response: Any) -> float | None:
    """Standard HTTP Retry-After header (RFC 9110 §10.2.3) — a value in
    seconds. ADR-037: honored here exactly as Strava's connector honors
    stravalib's typed .timeout field, for the same underlying reason."""
    header = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    if header is None:
        return None
    try:
        return float(header)
    except (TypeError, ValueError):
        return None


class _TransientHTTPCondition(Exception):
    """Internal-only: raised by _RequestsSession when the underlying
    `requests` call itself fails (connection error, timeout) — distinct
    from a non-2xx HTTP response, which is handled by status code above."""

    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class _RequestsSession:
    """Thin wrapper around `requests` — real network calls, used in
    production. Every test substitutes a fake with the same .post()/.get()
    shape instead, since this sandbox cannot reach home-api.eufylife.com."""

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
