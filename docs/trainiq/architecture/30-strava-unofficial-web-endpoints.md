# Architecture: Switch `StravaUnofficialConnector` to Strava's web endpoints

Issue: #30
Requirements: [`docs/trainiq/requirements/30-strava-unofficial-web-endpoints.md`](../requirements/30-strava-unofficial-web-endpoints.md)
Prior designs this supersedes in part: [`docs/trainiq/architecture/18-strava-session-cookie-connector.md`](18-strava-session-cookie-connector.md) (original connector), [`docs/trainiq/architecture/26-strava-unofficial-base-url-fix.md`](26-strava-unofficial-base-url-fix.md) (host-only fix; both of that doc's own risk notes — "whether `www.strava.com/api/v3/*` accepts cookie-only auth at all" — are the risk this issue resolves with live evidence)

## Approach

`/api/v3/*` now confirmed-401s for cookie-only auth (BO's live evidence,
issue body). `/athlete/training_activities` confirmed-200s with real JSON.
This design moves both call sites currently hitting `/api/v3/*`
(`download()` and `submit_manual_recovery()`'s validation call) onto the
single web endpoint `GET /athlete/training_activities`, reusing one
internal helper for both so the "is this response actually valid JSON from
an authenticated session, or an HTML/redirect bounce" classification logic
exists in exactly one place.

**Single endpoint, single helper, two callers.** Rather than give
`submit_manual_recovery()` and `download()` two different validation
endpoints (`/dashboard` vs. `/athlete/training_activities`, both allowed
per the requirements doc), this design picks
`/athlete/training_activities?page=1` for both. Reasoning: `/dashboard`
returns HTML either way (200 for valid, redirect-then-HTML for invalid),
which means detecting validity there requires either disabling redirect
-following (to see a bare 3xx) or sniffing HTML content for a login-form
marker — a second, independent "is this actually a logged-in page"
heuristic alongside the one `download()` already needs for the JSON
endpoint. Reusing `training_activities` page 1 for validation means
`submit_manual_recovery()` exercises the *exact* code path `download()`
uses in production, which is stronger validation (if page 1 parses as a
real activity page, pagination will work too) and is zero extra
implementation surface.

**Four things change** from the current `#18`/`#26` implementation:

1. **Endpoint + pagination model.** `GET /athlete/training_activities` replaces
   `GET /api/v3/athlete/activities` (`download()`) and `GET /api/v3/athlete`
   (`submit_manual_recovery()`'s validation call). The response shape is
   `{models: [...], page, perPage, total}`, not a bare list. `per_page` is
   confirmed ignored by the server, so the loop must track **count of items
   seen so far vs. `total`**, not "stop on an empty page" (the old
   termination condition, which is no longer reliable since pages aren't
   guaranteed to shrink to empty — see requirements doc Scope).

2. **Required headers.** `X-Requested-With: XMLHttpRequest`, `Accept:
   application/json`, and a browser-like `User-Agent` are added to every
   request this connector makes (both call sites use the same
   `_authenticated_get`-equivalent helper, so this is one change, not two).

3. **No server-side incremental filter.** The BO's evidence doesn't confirm
   `/athlete/training_activities` honors an `after`/epoch equivalent to the
   old REST endpoint's `after` param (the issue's evidence only exercises
   plain `page`/`per_page`). Per the Evidence-based principle, this design
   does **not** invent an unverified query parameter. Incremental sync is
   instead done **client-side**: pagination stops early once an item at or
   older than the checkpoint is seen, relying on the endpoint's observed
   (and overwhelmingly likely, given it backs a reverse-chronological
   "training log" page) newest-first ordering. See "Risks/tradeoffs" — this
   is a real, flagged assumption, not a confirmed fact.

4. **Invalid-session classification now has two new cases to catch**, not
   just 401/403: an HTTP redirect (3xx) to Strava's login page, and a 200
   response whose body is HTML, not JSON (both explicitly called out in the
   requirements doc and AC5). Both must raise `AuthenticationError` and
   clear the three stored credentials, exactly like today's 401/403 path —
   **not** fall through to `TransientError` or `StravaUnofficialHTTPError`.
   This requires two code changes beyond today's status-code `if`-chain:
   `_RequestsSession` must request with redirect-following disabled (so a
   3xx surfaces as a 3xx instead of silently resolving to a 200 HTML login
   page), and the JSON-parse step must be wrapped so a parse failure is
   reclassified as `AuthenticationError`, not left to raise an uncaught
   `ValueError`/`JSONDecodeError` out of `download()`.

## Affected components/files

- **Changed:** `trainiq/connectors/strava_unofficial.py` — `download()`,
  `submit_manual_recovery()`, `_authenticated_get()` (status/response
  classification and header construction), `normalize()`, module-level
  constants (new endpoint path, headers, `*_raw` field names), and
  `_RequestsSession` (disable redirect-following).
- **Changed:** `tests/test_strava_unofficial_connector.py` — existing
  tests asserting the old `/api/v3/*` paths, bare-list response bodies, and
  REST-shaped fixture fields must be updated to the new endpoint/response
  shape; new tests added per "Test strategy notes" below.
- **New:** `docs/trainiq/verification/strava-unofficial-<date>.md` (BO
  deliverable, template-copied from `docs/trainiq/verification/PROTOCOL.md`,
  per AC7 — not written by this pipeline, since it requires a real account).
- **Not touched:** `trainiq/connectors/strava.py` (official OAuth
  connector), `trainiq/connectors/base.py`, `trainiq/sync/engine.py`,
  `trainiq/sync/lifecycle_policy.py`, `trainiq/credentials/store.py`,
  `trainiq/app.py`, `trainiq/setup_wizard.py` — none of this issue's scope
  touches wiring, lifecycle escalation, or credential storage mechanics
  (requirements doc's explicit Out of scope).
- **`PROVIDER`, `CRED_*` constants, `AcquisitionStrategy`,
  `capability_tier`, `authenticate()`'s cached-expiry check,
  `request_manual_recovery()`'s text, `extract_resume_cursor()`
  (inherited):** unchanged — none are endpoint-specific.

## Interfaces/contracts

```python
# --- Endpoint + headers (new) ------------------------------------------------

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

# --- submit_manual_recovery() (validation call site changes) -----------------

def submit_manual_recovery(self, cookie_value: str) -> bool:
    """Validates via GET /athlete/training_activities?page=1 BEFORE
    persisting anything (same AC2 requirement as before — only the
    endpoint changed, since /api/v3/athlete now 401s even for a valid
    cookie). Returns False (never raises) on a rejected/expired cookie
    (401/403, 3xx-to-login, or HTML-not-JSON — all three now classified
    identically, see _authenticated_get); lets TransientError propagate
    unchanged."""
    if not cookie_value:
        raise ValueError("Refusing to store an empty session cookie value")
    try:
        self._authenticated_get(
            TRAINING_ACTIVITIES_PATH, params={"page": 1}, cookie_override=cookie_value
        )
    except (AuthenticationError, StravaUnofficialHTTPError):
        return False
    # ... persist three credentials, unchanged from today.

# --- _authenticated_get() (classification changes) ---------------------------

def _authenticated_get(self, path, params=None, cookie_override=None) -> dict[str, Any]:
    cookie = cookie_override if cookie_override is not None else self._active_cookie
    try:
        response = self._session.get(
            f"{self._base_url}{path}",
            params=params or {},
            headers={"Cookie": f"_strava4_session={cookie}", **WEB_ENDPOINT_HEADERS},
        )
    except _TransientHTTPCondition as exc:
        raise TransientError(..., retry_after_s=exc.retry_after_s) from exc

    # NEW: a 3xx (redirect to login) is an invalid-session signal, not an
    # "unexpected status." _RequestsSession must NOT auto-follow redirects
    # (allow_redirects=False) for this to be observable at all — requests'
    # default behavior would silently resolve this to a 200 HTML page,
    # losing the signal entirely before it reaches this method.
    if 300 <= response.status_code < 400:
        self._clear_session_credentials()
        raise AuthenticationError(
            f"{PROVIDER}: session rejected (redirect to {response.headers.get('Location', '?')}) — cookie cleared"
        )
    if response.status_code in (401, 403):
        self._clear_session_credentials()
        raise AuthenticationError(f"{PROVIDER}: session rejected (status {response.status_code}) — cookie cleared")
    if response.status_code == 429:
        retry_after = _parse_retry_after(response) or DEFAULT_RETRY_AFTER_S
        raise TransientError(f"{PROVIDER}: rate limited", retry_after_s=retry_after)
    if response.status_code == 404 or response.status_code >= 500:
        raise TransientError(f"{PROVIDER}: transient HTTP status {response.status_code}")
    if response.status_code != 200:
        raise StravaUnofficialHTTPError(f"{PROVIDER}: unexpected status {response.status_code}")

    # NEW: a 200 with an HTML body (not JSON) is ALSO an invalid-session
    # signal per AC5 — Strava can serve a login/interstitial page with a
    # 200 rather than a redirect in some flows. Must be reclassified here,
    # not left to raise an uncaught JSON-decode error out of this method.
    try:
        return response.json()
    except ValueError as exc:
        self._clear_session_credentials()
        raise AuthenticationError(
            f"{PROVIDER}: session rejected (non-JSON response body — likely an HTML login page) — cookie cleared"
        ) from exc


def _clear_session_credentials(self) -> None:
    """Extracted from the inline triple-delete already in
    _authenticated_get — now called from three sites (401/403, 3xx, bad
    JSON) instead of one, so it's a named helper rather than copy-pasted
    three times."""
    self._credentials.delete(PROVIDER, CRED_STRAVA_SESSION_COOKIE)
    self._credentials.delete(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT)
    self._credentials.delete(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT)

# --- download() (pagination + field-name rewrite) -----------------------------

# Candidate field names for each *_raw value, tried in order, per item.
# The BO's issue body lists "start_time/date fields" without pinning an
# exact key — the only name in this connector's new surface the evidence
# doesn't nail down (every other field — distance_raw, moving_time_raw,
# elapsed_time_raw, elevation_gain_raw, id, name, display_type,
# activity_type_display_name, commute, private, has_latlng, description —
# is given verbatim in the issue body). Per the Evidence-based principle,
# this is not guessed as a single hardcoded key: the Developer tries these
# in order and takes the first present, so a wrong guess fails loudly
# (KeyError-equivalent, see below) instead of silently mis-normalizing
# every activity's start_time (which would also corrupt the incremental
# checkpoint, since extract_resume_cursor() keys off start_time).
_START_FIELD_CANDIDATES = ("start_date_local_raw", "start_date_raw", "start_date", "start_day")

def _extract_start_time_iso(raw: dict[str, Any]) -> str:
    for key in _START_FIELD_CANDIDATES:
        if key in raw and raw[key] is not None:
            value = raw[key]
            if key.endswith("_raw") and isinstance(value, (int, float)):
                # Assumed Unix epoch seconds, consistent with the other
                # *_raw fields being machine/numeric values rather than
                # display strings. Timezone of "local_raw" is unconfirmed
                # (local vs. UTC) — see Risks/tradeoffs.
                return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
            return str(value)  # assume already a parseable date/ISO string
    raise StravaUnofficialHTTPError(
        f"{PROVIDER}: no recognized start-time field in training_activities "
        f"item (tried {_START_FIELD_CANDIDATES}) — payload shape has "
        f"changed since issue #30's evidence; Developer/BO must re-capture "
        f"a live sample and update _START_FIELD_CANDIDATES"
    )

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
                    break  # assumes newest-first order — see Risks/tradeoffs
                activities.append(item)
            if stopped_early:
                break
        else:
            activities.extend(batch)
        page += 1
    return activities

# --- normalize() (field-name rewrite) -----------------------------------------

def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
    """Maps the web endpoint's *_raw fields onto the same internal shape
    this connector has always produced. Units, per the BO's evidence +
    REST-API-naming-convention inference (flagged, not confirmed —
    see Risks/tradeoffs):
      - distance_raw: meters (float), same unit REST's `distance` used.
      - elapsed_time_raw / moving_time_raw: seconds (int). `duration_s`
        uses elapsed_time_raw specifically, for parity with the REST
        connector's `elapsed_time` (total elapsed, not moving-only).
      - elevation_gain_raw: meters (float) — has no home in the current
        canonical shape, so it is read nowhere (not fabricated into an
        existing field, not silently dropped as an error either — just
        genuinely out of this issue's scope; see Affected components).
    """
    return {
        "provider": PROVIDER,
        "external_id": str(raw["id"]),
        "start_time": _extract_start_time_iso(raw),
        "duration_s": raw["elapsed_time_raw"],
        "discipline_raw": raw.get("activity_type_display_name") or raw.get("display_type"),
        # Per requirements AC4 / open question 2: no HR, power, or calories
        # field appears anywhere in the BO's captured field list for this
        # endpoint. Never fabricated — stays None, exactly like today's
        # "never reported by this endpoint" fields.
        "avg_hr": None,
        "max_hr": None,
        "avg_power": None,
        "max_power": None,
        "distance_m": raw.get("distance_raw"),
        "calories": None,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
```

`_TransientHTTPCondition`, `_parse_retry_after()` are unchanged.
`_RequestsSession.get()` gains `allow_redirects=False` on its underlying
`requests.Session.get()` call — the one production-code behavior change
needed to make 3xx responses observable at all (today's default,
redirect-following `requests` call would silently turn a login-redirect
into a 200 HTML page before this connector ever sees a status code).

## Task breakdown

1. Add `TRAINING_ACTIVITIES_PATH`, `WEB_ENDPOINT_HEADERS`,
   `_START_FIELD_CANDIDATES` module-level constants.
2. Update `_RequestsSession.get()` to pass `allow_redirects=False`.
3. Rewrite `_authenticated_get()`: merge `WEB_ENDPOINT_HEADERS` into every
   request's headers; add the 3xx branch; wrap the `response.json()` call
   to reclassify a parse failure as `AuthenticationError`; extract the
   three-credential-delete into `_clear_session_credentials()` and call it
   from all three now-auth-failure branches (401/403, 3xx, bad JSON).
4. Update `submit_manual_recovery()`'s validation call to
   `TRAINING_ACTIVITIES_PATH` with `params={"page": 1}` (drop the old
   `/api/v3/athlete` call entirely).
5. Add `_extract_start_time_iso()` as specified, with its documented
   candidate-field fallback and loud failure if none match.
6. Rewrite `download()`'s pagination loop to the `{models, page, total}`
   shape and the `fetched_count < total` termination condition, plus the
   client-side `since_epoch` early-stop filter (assumes newest-first
   order — see Risks/tradeoffs). Drop the old `after` request param
   entirely; it is not confirmed supported by this endpoint.
7. Rewrite `normalize()` to the field mapping specified above.
8. Update `tests/test_strava_unofficial_connector.py`:
   - Every test currently asserting `.../api/v3/athlete` or
     `.../api/v3/athlete/activities` URLs, or scripting a bare-list
     `FakeResponse` body for `download()`, needs its URL/body updated to
     `.../athlete/training_activities` and the `{models, page, perPage,
     total}` shape.
   - `FakeResponse` needs two additions: (a) a way to script a 3xx status
     with a `Location` header, and (b) a way to make `.json()` raise
     `ValueError` (simulating an HTML body) independent of `status_code`
     (today's `FakeResponse.json()` always returns the constructor's
     `json_body`, which can't currently represent "200 status but not
     actually JSON").
   - `FakeStravaUnofficialSession` needs a way to assert `allow_redirects`
     was not silently re-enabled — not required to literally pass
     `allow_redirects` through the fake (it has no real HTTP layer to
     disable redirects on), but at minimum keep this constraint visible in
     a code comment so it isn't silently dropped in a future refactor.
   - `_REAL_ACTIVITY_RECORD` and the two `test_normalize_*` variants that
     build on it must be replaced with a fixture shaped like the issue's
     evidence (`id`, `name`, `display_type`,
     `activity_type_display_name`, `distance_raw`, `moving_time_raw`,
     `elapsed_time_raw`, `elevation_gain_raw`, a start-time field, etc.),
     not the old REST-shaped one.
   - Add the new tests listed in "Test strategy notes" below.
9. Run the full test suite (`pytest`) — confirm nothing outside
   `strava_unofficial.py` and its test file regresses; no other module
   imports from it.
10. Copy `docs/trainiq/verification/PROTOCOL.md` to
    `docs/trainiq/verification/strava-unofficial-<date>.md` as a template
    for the BO (per AC7) — leave it for the BO to actually fill in against
    a real account; this pipeline has no live egress or real cookie.

## Test strategy notes

Same fake-session infrastructure already in the test file — no new
pattern needed, only new fixture shapes and new scripted responses.

- **Pagination (`download()`):**
  - Multi-page sequence where `total` requires 3 pages and the server
    returns a full, non-empty final page (e.g. `total=45`, 20 items/page
    ×2 + 5) — confirms the loop stops via `fetched_count >= total`, not
    via an empty page, and that it would have looped forever under the
    old "stop on empty batch" logic (this is the regression this issue
    exists to fix — a comment in the test should say so).
  - `total=0` / empty `models` on page 1 -> returns `[]`, one request only.
  - A page's `models` length doesn't evenly divide `total` (e.g. `total=41`,
    20/page) -> still terminates correctly, doesn't under- or over-fetch.
- **Client-side incremental stop:**
  - A `since` checkpoint mid-page: a batch containing a mix of items newer
    and older than `since` -> only the newer-than-checkpoint items are
    returned, and **no second page is requested** (confirms early-stop,
    not just correct filtering) — this is the test the requirements doc's
    "Incremental Sync" verification-protocol concern maps to directly.
  - `since` older than every returned item (nothing to filter) -> full
    pagination proceeds as the no-checkpoint case.
- **Headers:** assert `X-Requested-With`, `Accept`, and `User-Agent` are
  present on the request for both `download()` and
  `submit_manual_recovery()`.
- **Invalid-session classification (the core new behavior, AC5):**
  - 3xx status (with and without a `Location` header) during `download()`
    -> `AuthenticationError`, and all three credentials confirmed deleted
    (`exists()` assertions, not just "an exception was raised" — same
    rigor the existing 401/403 tests already use).
  - 200 status with a non-JSON body (`FakeResponse.json()` raises
    `ValueError`) during `download()` -> `AuthenticationError`, credentials
    deleted.
  - Same two cases during `submit_manual_recovery()` -> returns `False`,
    nothing persisted (parallel to the existing rejected-cookie tests).
  - 401/403 during `download()` and `submit_manual_recovery()` -> same
    behavior as before, now via the new endpoint/headers (update, don't
    remove, the existing parametrized tests).
  - 429/404/5xx -> unchanged `TransientError` behavior (update URLs only).
- **`_extract_start_time_iso()` / `normalize()`:**
  - New fixture using the issue body's exact field list (with your chosen
    primary candidate field, e.g. `start_date_local_raw` as an int epoch)
    -> asserts the full mapping (`external_id`, `start_time`,
    `duration_s` from `elapsed_time_raw`, `discipline_raw` from
    `activity_type_display_name`, `distance_m` from `distance_raw`,
    `avg_hr`/`max_hr`/`avg_power`/`max_power`/`calories` all `None`).
  - `activity_type_display_name` absent, `display_type` present ->
    `discipline_raw` falls back correctly (mirrors the old
    `sport_type`/`type` fallback test).
  - None of `_START_FIELD_CANDIDATES` present in the raw item ->
    `normalize()` (via `_extract_start_time_iso`) raises
    `StravaUnofficialHTTPError` with a message naming the missing field —
    confirms the "fail loud, don't silently corrupt the checkpoint" design
    choice, since this is exactly the scenario if the field-name guess
    above turns out wrong in production.
- **Unchanged, keep as-is (update URLs only where present):**
  `authenticate()`'s three tests, `list_acquisition_strategies()`,
  `request_manual_recovery()`'s text assertion, `extract_resume_cursor()`'s
  inherited-behavior test, the network-level-failure test.
- **What NOT to test here:** `Degraded -> RecoveryRequired` escalation
  timing (unchanged, already covered by `lifecycle_policy.py`'s own suite,
  per #18's design) and anything about `/dashboard` (not used by this
  design).

## Risks/tradeoffs

- **The exact start-time field name is not confirmed by the BO's
  evidence** — the issue body says "start_time/date fields" without
  pinning one key, unlike every other field it lists verbatim. This design
  handles that gap structurally (an ordered candidate list, failing loudly
  if none match) rather than hardcoding a single guessed key, per the
  Evidence-based principle — but the candidate list itself (led by
  `start_date_local_raw`, inferred from the `*_raw` naming convention the
  issue's other fields establish) is still an inference, not a
  live-captured fact. **This is the single most important thing for the
  BO's `docs/trainiq/verification/strava-unofficial-<date>.md` to pin
  down** (alongside `distance_raw`'s and `elevation_gain_raw`'s units, also
  inferred by convention, not confirmed). If the real field name isn't in
  `_START_FIELD_CANDIDATES`, every `normalize()` call fails loudly
  (`StravaUnofficialHTTPError`, not silent corruption) until the Developer
  adds the real key — a one-line fix once known, same "cheap to fix if the
  live capture proves it wrong" category of risk #18's design doc already
  accepted for `ASSUMED_SESSION_LIFETIME_S`.
- **Newest-first ordering for the client-side incremental-stop
  optimization is assumed, not confirmed.** If `/athlete/training_activities`
  is ever paginated oldest-first instead, the early-stop logic in
  `download()` would incorrectly truncate a backfill or incremental sync
  partway through (returning too few activities, not too many — a
  silent-undercount failure mode, not a crash). The requirements doc's
  own evidence doesn't state sort order explicitly; this is inferred from
  the endpoint backing a "training log" page, which is conventionally
  reverse-chronological. The verification protocol's existing "Incremental
  Sync" section (§3) already requires the BO to confirm new activities
  appear without a full re-fetch — a passing result there also happens to
  validate this ordering assumption; call this out explicitly in the
  live-verification doc rather than leaving it implicit.
- **No `after`-equivalent filter param means every full or partial sync
  still walks pages from page 1**, relying on the early-stop optimization
  above to bound the work for incremental syncs. This is strictly worse
  than the old REST endpoint's server-side `after` filter *if* one exists
  on this endpoint too and this design simply didn't find it — but
  inventing an unverified parameter name and silently trusting the server
  honors it would be worse: a wrong-but-silently-accepted param could make
  incremental sync appear to work while actually doing nothing, which is a
  harder bug to ever notice than "incremental sync is a bit slower than it
  could be." If the BO's live-verification finds a working filter param,
  swapping it in is a contained, obvious follow-up to this same loop, not
  a redesign.
- **The JSON-vs-HTML reclassification only covers a `ValueError` from
  `.json()`.** If Strava's actual invalid-session response is, say, a 200
  with an empty body or a body that happens to parse as valid-but-wrong
  JSON (e.g. `{}`), this design's `AuthenticationError` reclassification
  won't trigger, and the response falls through to whatever
  `body.get("models", [])` / `body.get("total", 0)` do with an empty dict
  (`[]` / `0` — `download()` would just see "zero total," not an explicit
  auth failure, and silently return no activities instead of entering the
  recovery-required path). This is a real residual gap the BO's live
  verification should specifically probe (what does an actually-expired
  cookie return today, not just the two shapes the issue body already
  names) — flagged rather than silently assumed covered.
- **Disabling redirect-following (`allow_redirects=False`) is a real
  behavior change to the only networking primitive this connector uses.**
  No other call site in this connector relies on following a redirect
  (there isn't one), so this is believed safe, but it's worth stating
  explicitly since it's a global change to `_RequestsSession`, not scoped
  to one request.
