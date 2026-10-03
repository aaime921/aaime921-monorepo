# Architecture: PKCE OAuth + refresh-token auth path for PelotonConnector

Issue: #7
Requirements: [`docs/requirements/7-peloton-pkce-oauth-refresh.md`](../requirements/7-peloton-pkce-oauth-refresh.md)

## Approach

Add OAuth as a **third, independently-checked auth path** inside
`PelotonConnector.authenticate()`, tried before the existing two-path
dispatch (automated login / manual recovery), not folded into the
existing `ConnectorState`-gated branching those two already use. This is
deliberate, and is the design point the requirements doc left open:

- The existing dispatch (`RECOVERY_REQUIRED` → manual token, else →
  automated login) exists to implement the Soft Degradation policy:
  don't keep hammering a login endpoint once it's known broken for this
  account, only fall back to manual recovery after the state machine has
  actually escalated. That policy still matters for the automated
  username/password path, which stays exactly as it is (out of scope,
  confirmed dead per BL-008, kept only because removing it is explicitly
  out of scope).
- OAuth is different in kind: once a human has completed the one-time
  setup, there is no "hammering" risk analogous to repeated failed
  logins — a proactive refresh against a still-valid refresh token is the
  intended, expected steady-state call pattern, every sync, forever. It
  should not wait for `ConnectorState` to reach any particular value
  before being tried, and per AC5 its own failure must fall back to
  manual recovery **within the same `authenticate()` call**, not by
  waiting for a future state transition the way automated-login failures
  do today.

Concretely:

```python
def authenticate(self) -> bool:
    if self._credentials.exists(PROVIDER, CRED_OAUTH_REFRESH_TOKEN):
        if self._authenticate_with_oauth():
            return True
        # AC5: OAuth failed for any reason (expired/revoked/rejected
        # refresh token) — fall back to manual recovery in this same
        # cycle, regardless of ConnectorState. Never falls through to
        # the automated-login path, which stays dead-but-untouched.
        return self._authenticate_with_manual_token()
    if self.get_state() == ConnectorState.RECOVERY_REQUIRED:
        return self._authenticate_with_manual_token()
    return self._authenticate_with_automated_login()
```

The top branch is gated purely on "has this account ever completed OAuth
setup" (`CRED_OAUTH_REFRESH_TOKEN` exists), not on `ConnectorState`. This
means:

- An account that has never run the OAuth setup script sees **zero
  behavior change** — `exists(...)` is `False`, so control falls straight
  into the existing, untouched `if/else`. This is what satisfies AC6
  (existing automated-login and manual-recovery tests pass unmodified)
  without needing any test to change.
- An account that has completed OAuth setup tries OAuth on every single
  `authenticate()` call, silently succeeding from a cached, unexpired
  access token most of the time (AC1 — no network call), refreshing when
  needed (AC2), and only ever reaching manual recovery when OAuth itself
  is broken (AC5).
- If OAuth keeps failing indefinitely (e.g. the refresh token was revoked
  and the human hasn't rerun setup yet), every sync pays one extra,
  failed POST to the token endpoint before falling back to whatever the
  manual-recovery path already does. This is a bounded, one-call cost per
  sync, not a retry loop — see Risks/tradeoffs.

**Why not gate OAuth on `ConnectorState` too?** It was considered and
rejected: `ConnectorState` reflects whether the *last full sync cycle*
succeeded, not which auth mechanism is configured. Gating OAuth behind,
say, "not `RECOVERY_REQUIRED`" would create exactly the bug class
Milestone 1's Strava module and this connector's own manual-recovery path
already guard against elsewhere in this codebase (state and credential
availability are orthogonal concerns) — and would make it impossible for
a successful OAuth refresh to ever pull a `RECOVERY_REQUIRED` connector
back to `HEALTHY`, which is precisely the self-healing behavior this
issue exists to deliver. `SynchronizationEngine.sync_connector()` already
transitions state generically based on overall outcome (`authenticate()`
+ `download()` succeeding or not) — nothing here needs to change for that
to keep working exactly as it does for the other two paths today.

**Proactive-refresh safety margin.** `PelotonConnector` already has one
precedent for this shape (`ASSUMED_SESSION_LIFETIME_S`'s session check
uses a 60s margin) and so does `StravaConnector` (5-minute/300s margin
against a documented ~6h access-token life). Peloton's OAuth access token
is live-verified at `expires_in=172800` (48h) — two orders of magnitude
longer-lived than Strava's. A proportionally-scaled margin would be
roughly 30–40 minutes; this design rounds up to a flat, easy-to-reason
about **`OAUTH_EXPIRY_SAFETY_MARGIN_S = 3600`** (1 hour). Same caveat as
`ASSUMED_SESSION_LIFETIME_S` already carries in this file: this is a
reasoned default, not a value independently verified against Peloton's
actual clock-skew or grace-period behavior (no such data exists — only
the happy-path expiry and one live refresh were verified). Flagged as
such in-code, not presented as researched fact.

**PKCE and the initial code/token exchange live in `PelotonConnector`,
not only in the setup script.** AC9 requires unit tests in
`tests/test_peloton_connector.py` for PKCE parameter generation and for
"token exchange success" — both need to be real, importable, testable
connector code, not logic embedded only in an interactive CLI script.
The setup script (see below) becomes a thin interactive wrapper around
connector methods, matching how `setup_wizard.py` already wraps
connector calls for the other two providers rather than reimplementing
auth logic itself.

## Affected components/files

- `trainiq/connectors/peloton.py` — new constants, three new credential
  types, one new small exception class, and three new methods on
  `PelotonConnector`. `authenticate()`'s dispatch changes as shown above.
  No changes to `download()`, `normalize()`, `_login()`,
  `_authenticate_with_automated_login()`, `_authenticate_with_manual_token()`,
  `request_manual_recovery()`, or `submit_manual_recovery()` — all
  unchanged, per the requirements doc's explicit scope limits.
- `trainiq/credentials/store.py` — **no changes.** `CredentialStore`'s
  existing `get`/`set`/`exists`/`rotate` surface already supports
  arbitrary `(provider, credential_type)` pairs; the three new OAuth
  credential types are just new `credential_type` string values under the
  existing `"peloton"` provider key, exactly like `manual_bearer_token`
  already is. `credentials_metadata`'s connected/last-refreshed bookkeeping
  is per-provider, not per-credential-type, so it already reflects OAuth
  activity the same way it reflects any other Peloton credential write —
  nothing to add there either.
- `scripts/setup_peloton_oauth.py` — **new**, permanent CLI entry point
  (same category as `scripts/import_peloton_csv.py`, not a
  delete-after-use POC like `scripts/debug_peloton.py`). Prints the
  JS-blocking instructions and the authorize URL, prompts for the pasted
  authorization code, and calls
  `PelotonConnector.complete_oauth_setup(...)`.
- `docs/setup/peloton-oauth.md` — **new**, the written setup
  documentation AC8 requires: what the one-time flow is, why the
  JavaScript-blocking step is necessary (cites `BACKLOG.md` BL-008's
  documented race), and exactly how to run the script.
- `tests/test_peloton_connector.py` — new tests only; nothing existing
  changes (see Test strategy notes).
- `BACKLOG.md` — no changes required. BL-008 is already closed; this
  issue doesn't reopen or amend it, since it not resolve/relate to the
  endpoint-shape question BL-008 tracked.

## Interfaces/contracts

New credential type constants, alongside the existing ones:

```python
CRED_OAUTH_ACCESS_TOKEN = "oauth_access_token"
CRED_OAUTH_REFRESH_TOKEN = "oauth_refresh_token"
CRED_OAUTH_EXPIRES_AT = "oauth_expires_at"
```

New OAuth-tenant constants (fixed, live-verified values from the issue —
not constructor parameters, unlike `base_url`: there is no legitimate
reason for a caller to override Peloton's own Auth0 tenant identifiers,
unlike `base_url`, which exists purely for test injection against the
REST API):

```python
OAUTH_AUTHORIZE_URL = "https://auth.onepeloton.com/authorize"
OAUTH_TOKEN_URL = "https://auth.onepeloton.com/oauth/token"
OAUTH_CLIENT_ID = "WVoJxVDdPoFx4RNewvvg6ch2mZ7bwnsM"
OAUTH_REDIRECT_URI = "https://members.onepeloton.com/callback"
OAUTH_SCOPE = "offline_access openid peloton-api.members:default"
OAUTH_AUDIENCE = "https://api.onepeloton.com/"

# Reasoned default, not independently verified (see Approach) — same
# category of flagged-not-researched constant as ASSUMED_SESSION_LIFETIME_S
# above in this file.
OAUTH_EXPIRY_SAFETY_MARGIN_S = 3600
```

New exception, alongside `PelotonHTTPError`:

```python
class PelotonOAuthRejected(Exception):
    """The OAuth token endpoint rejected a request with a 4xx status other
    than 429 (rate limit) — e.g. an expired/revoked refresh token, or an
    invalid authorization code. Distinct from PelotonHTTPError so callers
    can tell "the grant itself was rejected" apart from "something
    unexpected/malformed happened", and react differently: a refresh
    rejection triggers the AC5 fallback to manual recovery; an initial
    setup-exchange rejection is meant to surface directly to the human
    running the setup script (who can simply restart the flow — codes
    are single-use and short-lived, so there is nothing to gracefully
    degrade here)."""
```

New module-level, side-effect-free function (used by the setup script and
directly unit-tested per AC9):

```python
def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge), RFC 7636, S256 method.
    Only used by the one-time human setup flow — refreshing an existing
    refresh_token does not involve PKCE at all (the OAuth spec's
    grant_type=refresh_token request has no code_verifier), so this is
    never called from authenticate()'s regular runtime path."""
```

New private helper, shared by setup and refresh (keeps the
success/rejected/transient status-code classification in exactly one
place, same reasoning as `_authenticated_get()` from issue #5):

```python
def _exchange_oauth_token(self, payload: dict[str, str]) -> dict[str, Any]:
    """POSTs `payload` (grant_type=authorization_code or
    grant_type=refresh_token, plus that grant's required fields) to
    OAUTH_TOKEN_URL. Returns the parsed JSON body on 200. Raises
    TransientError for 429 (honoring Retry-After, ADR-037) and for 5xx;
    raises PelotonOAuthRejected for any other non-200 status (400/401/403
    — Auth0 commonly uses 400 with an invalid_grant body for an
    expired/revoked/reused refresh token, not only 401/403, so this
    treats the whole non-429 4xx range as "rejected," not just 401/403)."""
```

New public method — the initial, human-triggered exchange:

```python
def complete_oauth_setup(self, authorization_code: str, code_verifier: str) -> None:
    """Exchanges a human-obtained authorization code for the first
    access_token/refresh_token pair and persists them. Called exactly
    once per setup (or re-setup, e.g. after a revoked refresh token) —
    never from authenticate()'s regular runtime path. Lets
    PelotonOAuthRejected/TransientError propagate directly to the caller
    (the setup script), which is expected to show the error and let the
    human restart the flow, per AC7's "no scripted login" constraint —
    there is no automatic retry to build here, a code is single-use."""
```

New private methods — the silent runtime path:

```python
def _authenticate_with_oauth(self) -> bool:
    """AC1/AC2: returns True immediately from a cached, unexpired access
    token with no network call; otherwise refreshes if a refresh token is
    stored. Returns False (never raises) on any refresh rejection, a
    missing refresh token, or a missing/expired access token with no
    refresh token available — the caller (authenticate()) is what decides
    to fall back to manual recovery, this method only reports outcome."""

def _refresh_oauth_token(self, refresh_token: str) -> bool:
    """AC2/AC3: exchanges `refresh_token` via grant_type=refresh_token.
    On success, rotates ALL THREE OAuth credential values — access token,
    refresh token (the API always returns a new one; the old one is
    invalidated on use, live-confirmed by the BO, same rotation hazard
    StravaConnector._refresh() already guards against), and expiry.
    Returns False on PelotonOAuthRejected (logged as a warning, not
    re-raised) so the caller can fall back per AC5. Lets TransientError
    propagate unchanged (429/5xx are not "this refresh token is bad,"
    they're "try again later" — same distinction _login() already makes)."""
```

`authenticate()`'s new top branch is shown in Approach above.

## Task breakdown

1. Add the three `CRED_OAUTH_*` constants, the six `OAUTH_*` constants,
   and `OAUTH_EXPIRY_SAFETY_MARGIN_S`, each with a short comment (mirror
   the existing `ASSUMED_SESSION_LIFETIME_S`/`DISTANCE_KM_TO_M_MULTIPLIER`
   comment style — state what's live-verified vs. reasoned-default).
2. Add `PelotonOAuthRejected` next to `PelotonHTTPError`.
3. Add `generate_pkce_pair()` (module-level function, `secrets` +
   `hashlib.sha256` + base64url, no padding, per RFC 7636).
4. Add `_exchange_oauth_token()`, reusing the existing
   `_TransientHTTPCondition` → `TransientError` translation and
   `_parse_retry_after()` helper already in this file.
5. Add `complete_oauth_setup()`, building the
   `grant_type=authorization_code` payload (`code`, `code_verifier`,
   `client_id=OAUTH_CLIENT_ID`, `redirect_uri=OAUTH_REDIRECT_URI`,
   `grant_type=authorization_code`) and persisting all three credential
   values via `self._credentials.rotate(...)` on success.
6. Add `_authenticate_with_oauth()` and `_refresh_oauth_token()` as
   specified above, building the refresh payload
   (`grant_type=refresh_token`, `refresh_token`, `client_id=OAUTH_CLIENT_ID`).
7. Change `authenticate()`'s body to the three-branch dispatch shown in
   Approach. This is the only change to existing code in this file
   besides the module docstring update in step 8.
8. Update the module docstring's Feature 3.1 section to describe the
   OAuth path as a third option, cross-referencing this doc and issue #7,
   without rewriting the existing Feature 3.1/3.2 text describing the
   automated-login/manual-recovery pair (both unchanged).
9. Add `scripts/setup_peloton_oauth.py`: prints the JS-blocking
   instructions (block JavaScript for `members.onepeloton.com` via
   `chrome://settings/content/javascript` before logging in — cites
   `BACKLOG.md` BL-008), calls `generate_pkce_pair()`, builds and prints
   the authorize URL (`OAUTH_AUTHORIZE_URL` plus
   `client_id`/`redirect_uri`/`scope`/`audience`/`response_type=code`/
   `code_challenge`/`code_challenge_method=S256`, via `urlencode`),
   prompts for the pasted authorization code, and calls
   `PelotonConnector(CredentialStore()).complete_oauth_setup(code, verifier)`.
   Catch `PelotonOAuthRejected` and print a clear "the code was rejected,
   restart this script and try again" message rather than a raw
   traceback — this is the one place a rejection is meant to reach a
   human directly, per `complete_oauth_setup()`'s contract above.
10. Add `docs/setup/peloton-oauth.md` describing the one-time flow in
    prose (why it's manual, the JS-blocking step and why it's needed, how
    to run the script), for a human who hasn't just read this design doc.
11. Run the full test suite (`pytest`) to confirm nothing outside
    `tests/test_peloton_connector.py` is affected — no other module
    references `PelotonConnector`'s internals beyond what `setup_wizard.py`
    already imports (email/password constants, `PROVIDER`,
    `PelotonConnector` itself), none of which this issue touches.

## Test strategy notes

All new tests go in `tests/test_peloton_connector.py`, using the existing
`FakePelotonSession`/`FakeResponse` fixtures already there (no new test
infrastructure needed) — `_exchange_oauth_token()`/`_refresh_oauth_token()`/
`complete_oauth_setup()` all go through `self._session.post(...)`, same as
`_login()` already does.

- **PKCE generation (AC9):** `generate_pkce_pair()` returns a
  `code_verifier` matching RFC 7636's charset/length constraints, and a
  `code_challenge` that is exactly the S256 transform of that verifier
  (recompute it independently in the test via `hashlib`/`base64` and
  assert equality — not just "is a non-empty string"). Also assert two
  calls produce two different verifiers (it must be random per call, not
  memoized).
- **Initial token exchange success (AC9):** `complete_oauth_setup()`
  against a scripted 200 response persists all three credential values
  correctly under the new `CRED_OAUTH_*` types, and does **not** touch
  `manual_bearer_token`/`email`/`password` (AC4 — direct assertion that
  those remain unset/unchanged).
- **Refresh-token rotation (AC3, the specific regression this issue calls
  out):** script two consecutive successful refresh responses with
  *different* `refresh_token` values; call `_refresh_oauth_token()` (or
  go through `authenticate()` with a pre-expired access token) twice;
  assert the second call's outgoing request body's `refresh_token` field
  is the *new* one from the first response, not the original — mirroring
  `StravaConnector`'s own rotation regression-test shape.
- **Refresh failure → fallback (AC5):** store an OAuth refresh token and
  a manual bearer token both; script a 401/403/400 response from the
  token endpoint; call `authenticate()`; assert it returns `True` (via
  the manual path) and that `self._active_auth_header` ends up as the
  `Bearer <manual token>` header, not an OAuth one — proving the fallback
  actually happened within the same call, not just that `False` wasn't
  returned.
- **Refresh failure, no manual token available:** same as above but
  without a stored manual token — `authenticate()` returns `False`
  (matching `_authenticate_with_manual_token()`'s existing no-token
  behavior), never raises.
- **Cached, unexpired access token needs no network call (AC1):** store a
  future-expiry OAuth access token (past the 1-hour margin); call
  `authenticate()`; assert it returns `True` and `fake.post_calls == []`.
- **Expired/near-expiry access token triggers refresh (AC2):** store an
  access token expiring inside the margin (or already expired) plus a
  valid refresh token; assert `authenticate()` calls the token endpoint
  and succeeds.
- **Existing behavior unchanged (AC6):** no new test needed here — this
  is what the *unmodified* existing test suite already proves, since
  none of those tests ever store an OAuth credential and the new
  top-level branch is a no-op when one isn't present.
- **Never scripts a login form (AC7):** implicit in every test above —
  no new test path ever calls `POST /auth/login` or constructs a
  Peloton login payload; `complete_oauth_setup()`'s only network call is
  to `OAUTH_TOKEN_URL`.
- **Manual, not automated:** no test should attempt to exercise the
  authorize-URL redirect or the human login step itself — those are
  explicitly out of scope/unautomatable per the requirements doc.

## Risks/tradeoffs

- **Refresh-rejection status codes are inferred, not exhaustively
  live-verified.** Only a successful refresh was tested live (per the
  issue); no live test exercised what an actually-revoked or expired
  refresh token returns. This design treats the whole non-429 4xx range
  as "rejected" (`PelotonOAuthRejected`) specifically because Auth0
  commonly returns `400 invalid_grant` for this case, not only
  `401`/`403` — but if Peloton's Auth0 tenant is configured to return
  something else in that family (e.g. a 5xx it shouldn't), this
  connector would (correctly, per ADR-037/existing convention) treat
  that as `TransientError` and retry-by-backoff rather than fall back to
  manual recovery immediately. Worth re-verifying with a real revoked
  token if/when one is available, but not blocking for this design.
- **A permanently-broken OAuth refresh token is retried forever, once
  per sync, rather than ever being cleared automatically.** Considered
  auto-deleting the stored refresh token on a confirmed rejection, but
  rejected: nothing in the requirements asks for it, and doing so
  unprompted would silently discard state the human might reasonably
  want to inspect/debug, or that might start working again without
  their intervention (e.g. a transient Auth0-side issue misclassified as
  a rejection). The cost is bounded — one extra failed POST per sync,
  not a loop — so leaving the stored (bad) token in place until the
  human reruns the setup script is the simpler, safer default. Revisit
  only if this proves to add meaningful, observed cost in practice.
- **The 1-hour proactive-refresh margin is a reasoned default, not a
  measured one** (see Approach) — same category of risk this file
  already carries for `ASSUMED_SESSION_LIFETIME_S`, now flagged for a
  second constant rather than silently assumed correct.
- **This remains an unofficial, reverse-engineered `client_id` with no
  written permission from Peloton** (per the requirements doc's explicit
  scope) — this design's entire fallback structure exists because of
  that risk, not despite it. Nothing here reduces that underlying risk;
  it only ensures the connector degrades gracefully, to the existing
  manual path, if/when it materializes.
- **`scripts/setup_peloton_oauth.py` opens a real browser-facing URL and
  asks for a pasted code, unlike `setup_wizard.py`'s existing Strava step
  (which uses the same shape but a different provider).** No new pattern
  is introduced — this mirrors Strava's existing manual
  authorization-code paste in `setup_wizard.py` closely enough that a
  future consolidation (folding this into `setup_wizard.py` itself
  instead of a standalone script) is a reasonable follow-up, but is not
  required by any acceptance criterion here and is left as a
  possible future simplification rather than done now.
