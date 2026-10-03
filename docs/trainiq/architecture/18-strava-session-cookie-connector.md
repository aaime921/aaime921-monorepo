# Architecture: Strava session-cookie connector (`StravaUnofficialConnector`)

Issue: #18
Requirements: [`docs/trainiq/requirements/18-strava-session-cookie-connector.md`](../requirements/18-strava-session-cookie-connector.md)
Discovery: [`docs/trainiq/discovery/18-strava-zero-cost-paths.md`](../discovery/18-strava-zero-cost-paths.md)

**Re-review note (issue #18, re-triggered on `stage:architect`):** an earlier
pass of this doc linked to `docs/requirements/20-*` / `docs/discovery/20-*`,
which at the time were internally headered "Issue: #20" despite matching
this topic by content only. That was a real numbering mismatch, since
resolved: `docs/trainiq/requirements/18-strava-session-cookie-connector.md`
and `docs/trainiq/discovery/18-zero-cost-paths.md` now exist on `main`,
correctly headered "Issue: #18," independently confirmed against repo
history (not taken on a comment's claim) before writing this update. The
`20-*` originals are still present in the repo (unreferenced, apparently
leftover from the mislabeling) but are out of scope for this doc to clean
up. Content below is unchanged from the original design — this re-review
confirmed the requirements doc's substance matches what this design was
already built against, so only the stale links/citations here were
corrected.

## Approach

Add a new, independent connector — `StravaUnofficialConnector`, in a new
module `trainiq/connectors/strava_unofficial.py` — implementing the same
`Connector` ABC (ADR-013) as `StravaConnector`, `PelotonConnector`, and
`EufyConnector`, but authenticating via the `_strava4_session` browser
cookie instead of OAuth. It does not touch `trainiq/connectors/strava.py`
at all. (Class name deliberately follows the existing `XConnector`
convention — `StravaConnector`, `PelotonConnector`, `EufyConnector` —
rather than the requirements doc's literal `StravaUnofficial`, for
consistency; no acceptance criterion depends on the exact class name.)

**Provider identity is the one load-bearing decision in this design**,
and it's not arbitrary: this connector uses `PROVIDER = "strava_unofficial"`,
**not** `"strava"`. Reasoning:

- `CredentialStore` keys secrets by `(provider, credential_type)` — reusing
  `"strava"` would put this connector's session cookie in the same
  provider namespace as the official connector's OAuth tokens, with no
  functional benefit.
- More importantly: `connector_state` (ADR-038) is keyed by **provider
  alone**, with no strategy column (`_load_lifecycle_state`,
  `_record_attempt_start`, `_record_lifecycle_outcome` in
  `trainiq/sync/engine.py` all query `WHERE provider = ?`). If this
  connector shared `PROVIDER = "strava"` with `StravaConnector`, the two
  connectors — which the requirements doc explicitly says "operate in
  parallel" — would silently share one lifecycle row. A cookie failure
  marking the shared row `Degraded` would also skip the official
  connector's next sync attempt, and vice versa. This is a real
  correctness bug, not a style preference, and it only becomes visible
  once both connectors are registered and failing independently — exactly
  the scenario this feature exists to support.
- `raw_activities`/`normalized_activities` are keyed by `(provider,
  external_id)`. A distinct provider string means a real Strava activity
  synced by both connectors lands as two separate canonical rows (see
  Risks/tradeoffs — this is a real, separate consequence of the same
  choice, not eliminated by it).

The connector also declares `AcquisitionStrategy.UNOFFICIAL_SESSION` (the
value already exists in `ConnectorBase`'s enum, unused until now) via
`list_acquisition_strategies()`. This gives it its own
`sync_checkpoints` row (`strategy="unofficial_session"` vs.
`StravaConnector`'s implicit `"default"`) as defense-in-depth on top of
the distinct provider string — belt-and-suspenders, since checkpoints and
lifecycle state are tracked in different tables with different keys.

**`download()`/`normalize()` reuse `StravaConnector`'s field mapping by
construction, not by sharing code.** Strava's REST activities-list JSON
(`id`, `start_date`, `elapsed_time`, `sport_type`, `type`,
`average_heartrate`, `max_heartrate`, `average_watts`, `max_watts`,
`distance`) is exactly the same response shape `stravalib` wraps —
`StravaConnector._activity_to_raw_dict()` output uses these same key names
because stravalib's attributes mirror the API's own field names directly.
Since this connector hits the REST endpoint with plain HTTP instead of
through stravalib, `download()` can return the API's JSON activity objects
**as-is**, with no reshaping step, and `normalize()` becomes line-for-line
identical to `StravaConnector.normalize()` (same keys, same None-handling,
same `calories: None` limitation — the activities-list endpoint doesn't
return calories either way, cookie or OAuth). Not sharing a helper
function between the two modules is a deliberate choice consistent with
this codebase's existing precedent (Peloton and Strava don't share
`normalize()` logic despite both being `RawActivity`-shaped) — each
connector module stays self-contained.

**Error handling follows the existing `PelotonConnector` HTTP shape**
(hand-rolled session, no library), since this connector needs raw cookie
headers that `stravalib` has no support for:

- 401/403 → the stored cookie is stale. Clear all three session
  credentials and raise `AuthenticationError`. **This is a deliberate,
  explicit deviation from the requirements doc's AC7 wording** ("escalate
  to `RecoveryRequired` state") — see "On AC7, specifically" below.
- 429 → `TransientError` with `retry_after_s` from `Retry-After` if
  present, else a conservative default (3600s per the requirements doc).
- 404/5xx → `TransientError`, generic backoff (no authoritative
  `retry_after_s`).
- Anything else non-200 → a new `StravaUnofficialHTTPError`, mirroring
  `PelotonHTTPError`.
- A network-level failure (connection error/timeout) → `TransientError`,
  via the same `_TransientHTTPCondition` wrapper pattern
  `PelotonConnector`'s `_RequestsSession` already uses.

**`authenticate()` never makes a network call.** It only checks the
stored cookie's conservative expiry estimate (`obtained_at + 7 days`,
1-day safety margin — same shape as `StravaConnector`'s 5-minute margin on
`expires_at`, and `PelotonConnector`'s 1-hour OAuth margin). If the
estimate is wrong in either direction, the first real `download()` call
will surface a 401/403 (clearing credentials) or succeed — same accepted
trade-off this codebase already makes for every other connector's cached
credential check.

### On AC7, specifically

The requirements doc's AC7 says: "the connector clears all three session
credentials and escalates to `RecoveryRequired` state" on a 401/403. As
literally written, this is not just a style mismatch with the rest of the
codebase — **it cannot be implemented as stated without breaking the
state machine**:

- `ProviderStateMachine._ALLOWED_TRANSITIONS` (ADR-010,
  `trainiq/connectors/base.py`) only permits `DEGRADED -> RECOVERY_REQUIRED`.
  `HEALTHY -> RECOVERY_REQUIRED` and `WARNING -> RECOVERY_REQUIRED` are
  not legal transitions — calling `transition_state(RECOVERY_REQUIRED)`
  directly from a connector that was `Healthy` (e.g. its very first sync
  attempt ever) raises `InvalidStateTransition`.
  `sync/engine.py`'s own comments call this out explicitly as "a latent
  bug that was dormant only because `RecoveryRequired` connectors were
  never previously re-attempted at all" (see the `AuthenticationError`
  handler).
- ADR-038 §2 is explicit that connectors are "completely unaware of
  scheduling" — lifecycle/escalation timing is the Sync Engine's job
  alone, via `lifecycle_policy.evaluate()`, specifically so a hammering
  concern (Milestone 2 §7) and escalation logic aren't each
  connector-author's call to make independently.

The corrected, implementable behavior — and what this design actually
specifies — is: the connector clears the three credentials and raises
`AuthenticationError`, exactly like `StravaConnector._refresh()` and
`PelotonConnector._authenticated_get()` already do on their own
401/403-equivalent failures. The Sync Engine's existing, already-tested
machinery then does the rest, unchanged: first failure from `Healthy` ->
`Degraded`; after the clear (no cookie stored at all — see below)
reattempts on the `Degraded` backoff cadence; only after the ADR-038
10-day elapsed-time threshold does the engine perform one final
authentication attempt and, if that also fails, escalate to
`RecoveryRequired`. This is the same path every other connector's
auth-failure already takes — nothing about this connector changes
`sync/engine.py` or `lifecycle_policy.py`.

One real behavioral difference from Peloton's OAuth path is worth stating
explicitly: because the credential is deleted immediately on 401/403
(not left in place the way Peloton's permanently-rejected OAuth refresh
token is, per that design's own documented reasoning), every `Degraded`
retry during the backoff window short-circuits in `authenticate()` with
**zero network calls** — there's no cookie to even try. This generalizes
the "a well-behaved connector's cheap recheck" pattern ADR-038 §4
describes for `RecoveryRequired` to this connector's `Degraded` state too,
and is intentional: a stale Strava session cookie cannot self-heal (unlike
Peloton's OAuth rejection, which the Peloton design doc explicitly flags
as possibly a transient Auth0-side issue worth leaving in place to retry).
This isn't a blocking ambiguity — the behavior described satisfies the
*intent* of AC7 ("subsequent calls to `authenticate()` return `False` and
trigger recovery flow," which is literally true) through the mechanism
the rest of the codebase already uses, so this is handled here rather than
raised as `needs:human`.

## Affected components/files

- **New:** `trainiq/connectors/strava_unofficial.py` — the connector
  itself, plus `StravaUnofficialHTTPError`, a small private
  `_RequestsSession`/`_TransientHTTPCondition` adapter (same shape as
  `peloton.py`'s), and `_parse_retry_after()` (can be a local copy — no
  shared HTTP module exists yet in this codebase to import it from; see
  Risks/tradeoffs).
- **New:** `tests/test_strava_unofficial_connector.py`.
- **Not touched:** `trainiq/connectors/strava.py`,
  `trainiq/connectors/base.py`, `trainiq/sync/engine.py`,
  `trainiq/sync/lifecycle_policy.py`, `trainiq/credentials/store.py` — all
  existing contracts already support everything this connector needs
  (`CredentialStore`'s `(provider, credential_type)` surface is already
  generic; `AcquisitionStrategy.UNOFFICIAL_SESSION` already exists in the
  enum, unused until now).
- **Explicitly not in this issue's scope:** `trainiq/app.py`
  (`_build_configured_connectors`) and `trainiq/setup_wizard.py`. The
  requirements doc's Scope section never mentions wiring this connector
  into the live composition root or the first-run setup flow, only the
  connector class and its tests. Wiring it in is a separate decision with
  its own design question (see Risks/tradeoffs: it must NOT be an
  unconditional third `connectors.append(...)` the way Eufy/Peloton are
  today) — left for a follow-up issue or an explicit BO call, not silently
  added here.

## Interfaces/contracts

```python
PROVIDER = "strava_unofficial"

CRED_STRAVA_SESSION_COOKIE = "session_cookie"
CRED_STRAVA_SESSION_OBTAINED_AT = "session_obtained_at"
CRED_STRAVA_SESSION_EXPIRES_AT = "session_expires_at"

DEFAULT_BASE_URL = "https://api.strava.com"  # ADR-007: one named constant, constructor-overridable for tests

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
        expiry estimate only. See Approach."""
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
        """Identical field mapping to StravaConnector.normalize() — see
        Approach for why the raw shapes line up exactly."""
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
```

`_TransientHTTPCondition`, `_RequestsSession`, and `_parse_retry_after()`
are local, private copies of the exact shapes already in
`trainiq/connectors/peloton.py` (see Risks/tradeoffs on not sharing them).

## Task breakdown

1. Create `trainiq/connectors/strava_unofficial.py` with the module
   docstring (state: cookie-based, unofficial, ToS gray area accepted per
   issue #18's discovery/requirements docs, parallels `strava-offline`;
   cite both docs by path).
2. Add the module-level constants and `StravaUnofficialHTTPError` as
   specified above.
3. Add the private `_TransientHTTPCondition`/`_RequestsSession` adapter
   and `_parse_retry_after()`, copied from `peloton.py`'s shapes.
4. Implement `StravaUnofficialConnector.__init__`,
   `list_acquisition_strategies()`, `authenticate()` as specified.
5. Implement `_authenticated_get()` with the status-code classification
   above, including clearing all three credentials on 401/403.
6. Implement `request_manual_recovery()` and `submit_manual_recovery()`.
7. Implement `download()` (pagination + `since` -> `after` epoch
   conversion) and `normalize()`.
8. Do **not** modify `trainiq/app.py` or `trainiq/setup_wizard.py` in this
   issue (see Affected components/files) — if you believe wiring it in is
   actually needed now, comment on the issue rather than adding it
   unprompted, since the mutual-exclusivity question in Risks/tradeoffs
   below needs an explicit answer first.
9. Write `tests/test_strava_unofficial_connector.py` per Test strategy
   notes below.
10. Run the full test suite (`pytest`) — confirm nothing outside the new
    file and new test file is affected; no existing module imports
    anything from `strava_unofficial.py` yet.

## Test strategy notes

New file, `tests/test_strava_unofficial_connector.py`, using a fake
session with the same `script_get_response()`-queue shape
`tests/test_peloton_connector.py` already established (see that file's
queue-based fake, referenced in `docs/architecture/5-peloton-endpoint-
field-mapping.md`'s task breakdown) — no new test infrastructure pattern
needed.

- **`authenticate()`:**
  - Valid, unexpired cookie stored -> returns `True`, **zero session
    calls** (assert `fake.get_calls == []`) — this is the "no network
    validation on the cached path" property from Approach.
  - No cookie stored -> returns `False`, no exception.
  - Cookie stored but past `expires_at - SESSION_EXPIRY_SAFETY_MARGIN_S`
    -> returns `False`, no exception, no network call.
- **`submit_manual_recovery()`:**
  - Valid cookie (scripted `200` on `/api/v3/athlete`) -> returns `True`;
    all three credentials are stored with `expires_at = obtained_at +
    ASSUMED_SESSION_LIFETIME_S`.
  - Rejected cookie (scripted `401`/`403`) -> returns `False`; nothing is
    persisted (assert `credential_store.exists(...)` is still `False` for
    all three).
  - Empty string -> raises `ValueError`, nothing persisted.
  - A `429`/`5xx` during validation -> `TransientError` propagates (not
    swallowed into a `False` return) — confirms validation failures are
    distinguished from "the cookie itself is bad."
- **`download()`:**
  - No checkpoint -> full backfill: a multi-page sequence (e.g. 2 full
    pages of 200 then a shorter/empty final page) returns the
    concatenation of all pages, `page` incrementing 1, 2, 3; stops on the
    first page with zero results.
  - Checkpoint `since="2024-01-01T00:00:00+00:00"` -> the request's
    `after` param is the correct Unix epoch seconds integer.
  - Empty first page -> returns `[]`, no exception.
  - `download()` called before any successful `authenticate()` ->
    `AuthenticationError`, no network call.
- **Error handling:**
  - `429` with `Retry-After: 120` -> `TransientError(retry_after_s=120)`.
  - `429` with no `Retry-After` header -> `TransientError(retry_after_s=
    3600)` (`DEFAULT_RETRY_AFTER_S`).
  - `401` and, separately, `403` during `download()` -> both raise
    `AuthenticationError`, AND all three credentials are confirmed deleted
    from `CredentialStore` afterward (direct `exists()` assertions, not
    just "an exception was raised").
  - `404` and `500` during `download()` -> both raise `TransientError`
    (no `retry_after_s` asserted either way — generic backoff).
  - A network-level failure (fake raises the connection-error equivalent)
    -> `TransientError`.
- **`normalize()`:** reuse the exact same two fixture records
  `tests/test_strava_connector.py` already uses for
  `StravaConnector.normalize()` (same raw JSON shape, per Approach) and
  assert identical output — this is the most direct way to prove the
  "identical mapping" claim in AC4, rather than inventing new fixtures
  that could accidentally diverge.
- **`extract_resume_cursor()`:** one short test confirming the inherited
  base-class behavior (`{"start_time": "..."}  -> "..."`, `{}  -> None`)
  — not reimplementing `Connector`'s own test coverage, just confirming
  this connector didn't accidentally override it.
- **What NOT to test here:** the actual `Degraded -> RecoveryRequired`
  escalation timing. That's `lifecycle_policy.py`'s and
  `sync/engine.py`'s already-existing, already-passing ADR-038 test
  suite's job (see "On AC7, specifically") — this connector introduces no
  new engine-level behavior, so no new engine-level tests are needed for
  it specifically.

## Risks/tradeoffs

- **ToS/fragility risk is accepted, not mitigated by this design** — per
  the requirements doc's explicit framing ("accepted as a free-tier
  workaround... known tradeoffs... accepted by the BO") and the
  discovery doc's Option B analysis. This design only implements the
  already-approved approach; it doesn't re-litigate whether to build it.
- **No live-captured evidence exists for this exact endpoint/auth
  combination** (per the Evidence-based principle in
  `docs/roles/technical-architect.md`). `docs/verification/` has entries
  for Peloton and Eufy but none for Strava's cookie-auth path — this
  design's field mapping is justified by analogy (the REST API's
  documented/stravalib-mirrored schema, which is almost certainly stable
  across auth methods since it's the same endpoint), not by a captured
  real response. Recommend the BO do one live capture
  (`docs/verification/strava-unofficial-<date>.md`, same format as the
  other two) during or shortly after implementation — specifically to
  confirm (a) the JSON shape assumption above, (b) that `api.strava.com`
  actually accepts cookie-only auth at all (the `strava-offline` reference
  project and some community reports describe hitting `www.strava.com`'s
  internal endpoints instead — this is a real open question the
  requirements doc's chosen URL doesn't resolve), and (c) a real
  `_strava4_session` cookie's actual lifetime, since `7 days` is
  acknowledged in the requirements doc as "a conservative estimate," not
  a measured one. None of this blocks implementation — fixing it is a
  one-line constant/URL change if the capture proves either assumption
  wrong, same category of risk already accepted for
  `ASSUMED_SESSION_LIFETIME_S` (Peloton) and `OAUTH_EXPIRY_SAFETY_MARGIN_S`.
- **Duplicate canonical rows if both Strava connectors are ever
  registered and synced simultaneously.** Using a distinct `provider`
  string (this doc's main decision) correctly isolates credentials and
  lifecycle state, but it does NOT prevent `raw_activities`/
  `normalized_activities` from getting two rows — one keyed
  `("strava", "<id>")`, one `("strava_unofficial", "<id>")` — for the
  exact same real Strava activity, if both connectors are configured and
  synced at once. This is a direct, structural consequence of the
  provider-isolation decision, not a bug introduced elsewhere. **This is
  why app.py wiring is explicitly left out of this issue's scope**: when
  it is wired in, `_build_configured_connectors()` must treat the two
  Strava connectors as mutually exclusive (e.g. prefer `StravaConnector`
  if its `refresh_token` is present, only fall back to
  `StravaUnofficialConnector` otherwise) rather than the unconditional
  "append if configured" pattern Eufy/Peloton use today, since those two
  are genuinely independent data sources and Strava's two connectors are
  not. Flagging this now so whoever does that wiring (a future issue, or
  the Developer if they choose to tackle it as part of this one) doesn't
  default to the existing pattern by habit.
- **No shared HTTP-adapter module exists yet**, so
  `_TransientHTTPCondition`/`_RequestsSession`/`_parse_retry_after()` are
  duplicated a second time (Peloton has the first copy). This mirrors the
  existing `normalize()` non-sharing precedent and is consistent with
  this codebase's current per-connector-module style, but a third
  hand-rolled HTTP connector (if one is ever added) would be a reasonable
  trigger to extract a shared helper into `trainiq/connectors/base.py` or
  a new `trainiq/connectors/_http.py` — not needed now, not done here.
- **AC7's literal wording is not implementable as stated** — see "On AC7,
  specifically" above. Documented explicitly here and in the handoff
  comment so this isn't silently reinterpreted without anyone downstream
  knowing why the implementation doesn't match the requirements doc
  word-for-word.
