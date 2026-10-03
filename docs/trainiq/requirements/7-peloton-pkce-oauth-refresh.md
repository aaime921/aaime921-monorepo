# Requirements: PKCE OAuth + refresh-token auth path for PelotonConnector

Issue: #7

## Summary

The BO wants `PelotonConnector` to gain a third, better authentication path:
OAuth authorization-code+PKCE against Peloton's own Auth0 tenant, backed by a
stored, rotating refresh token, so the connector can silently mint fresh
access tokens on a normal scheduled sync — instead of requiring the BO to
manually extract and paste a new bearer token roughly every 48 hours (the
current `submit_manual_recovery()` fallback). The automated username/password
login path (`POST /auth/login`) is confirmed permanently broken (403,
BACKLOG.md BL-008) and is not expected to return, so this OAuth path is meant
to become the connector's practical default once a user completes a one-time
manual setup step, with the existing manual-bearer-token recovery path kept
as the fallback of last resort — not replaced. The flow (authorize endpoint,
PKCE parameters, token exchange, and refresh-token rotation) has already been
live-verified end to end against the real account by the BO, using a
throwaway POC script (deleted after use, per this repo's convention); no
token from that test was persisted and nothing is wired into the connector
yet.

## Scope

- Add a new authentication path to `PelotonConnector` that:
  - Uses OAuth 2.0 authorization-code flow with PKCE (S256) against Peloton's
    Auth0 tenant, using the live-verified parameters the BO captured:
    - Authorize endpoint: `https://auth.onepeloton.com/authorize`
    - `client_id=WVoJxVDdPoFx4RNewvvg6ch2mZ7bwnsM`
    - `redirect_uri=https://members.onepeloton.com/callback`
    - `scope=offline_access openid peloton-api.members:default`
    - `audience=https://api.onepeloton.com/`
    - Token exchange endpoint: `POST https://auth.onepeloton.com/oauth/token`
  - Once a valid access token + refresh token pair exists, silently
    refreshes the access token via the `grant_type=refresh_token` exchange
    on future syncs, with no manual step, for the life of the refresh token.
  - Persists a new access token **and** a new refresh token on every
    successful refresh — the real API rotates the refresh token on use and
    invalidates the previous one (live-confirmed by the BO), so reusing a
    stale refresh token after the first refresh would break the path
    silently. This is the same failure mode `StravaConnector` already
    guards against for its own rotating refresh token.
- Add new, distinct credential storage for this path (e.g. `oauth_access_token`,
  `oauth_refresh_token`, `oauth_expires_at`) via `CredentialStore`, kept
  separate from the existing `manual_bearer_token`/`email`/`password`
  entries so there is no ambiguity about which auth path is currently active
  for a given account.
- When the OAuth refresh path fails for any reason (refresh token revoked,
  expired, or rejected by Peloton), the connector must fall back cleanly to
  the existing manual-bearer-token recovery path rather than raising an
  unhandled error or leaving the account stuck — consistent with the BO's
  explicit instruction that this is an *additional* path, not a replacement,
  since it remains an unofficial, reverse-engineered `client_id` with no
  written permission from Peloton that could stop working without notice.
- Support the one-time, human-completed initial login/consent step that
  produces the first access_token/refresh_token pair. This step is
  necessarily manual (a real person logs into Peloton in a browser and
  the authorization code is captured) and must **not** be scripted or
  automated — the BO was explicit that automating the login form itself is
  out of scope and should not be attempted.
- Document the one-time setup step clearly enough for the BO to complete it
  without re-deriving the process from scratch, including the specific
  workaround the BO found necessary to capture the authorization code
  reliably: blocking JavaScript for `members.onepeloton.com` before logging
  in, so that site's own callback page cannot race the manual copy-paste of
  the code. This can be a setup script, written instructions, or both.
- Existing behavior must be preserved unchanged: the automated
  username/password login path and the manual-bearer-token recovery path
  (`submit_manual_recovery()` / `request_manual_recovery()`) continue to
  work exactly as they do today. This issue is additive.

### Out of scope

- Removing, replacing, or otherwise changing the automated username/password
  login path, even though it is confirmed broken — it stays as-is.
- Any change to `submit_manual_recovery()` / `request_manual_recovery()`
  behavior itself, beyond it remaining the fallback when OAuth refresh fails.
- Scripting or automating the human login/consent step. The one-time manual
  browser step is a deliberate, permanent part of this design, not a gap to
  close later.
- Seeking or obtaining written permission from Peloton for the reverse-
  engineered `client_id` — this connector must be built defensively (clean
  fallback on failure) precisely because that permission does not exist and
  the flow could be revoked or changed without notice.
- Any change to `download()`/`normalize()` or the workout-list endpoint
  handling — unrelated to authentication and already covered by issue #5.
- Deciding exactly how/where in `authenticate()`'s existing dispatch logic
  (automated login vs. manual recovery, gated by `ConnectorState`) the new
  OAuth path is checked, and the precise token-expiry safety margin used
  before triggering a refresh — these are implementation design calls for
  the Architect/Developer, not business requirements.

## Acceptance criteria

1. `PelotonConnector` can authenticate using a stored OAuth access token when
   one exists and has not expired, with no network call to the token
   endpoint required.
2. When the stored OAuth access token is expired (or expired enough to need
   proactive refresh), and a stored OAuth refresh token exists,
   `PelotonConnector` automatically exchanges it via
   `POST https://auth.onepeloton.com/oauth/token` (`grant_type=refresh_token`)
   for a new access token, without requiring any manual/human step.
3. Every successful OAuth refresh persists **both** the new access token and
   the new refresh token (rotation) — a second consecutive refresh using the
   token from a first refresh must succeed, proving the old refresh token
   was correctly overwritten and is not what's being reused.
4. The OAuth credential values (access token, refresh token, expiry) are
   stored under credential types distinct from `manual_bearer_token`,
   `email`, and `password`, so both the OAuth path and the manual-recovery
   path can have stored values simultaneously without either overwriting or
   being confused with the other.
5. If the OAuth refresh exchange fails (e.g., the refresh token is rejected
   or revoked), `PelotonConnector` falls back to attempting authentication
   via the existing manual-bearer-token recovery path in the same
   `authenticate()` call/cycle, rather than raising an unhandled error or
   leaving the connector with no working auth path when a manual token is
   available.
6. The existing automated username/password login path and the existing
   manual-bearer-token recovery path (`submit_manual_recovery()` /
   `request_manual_recovery()`) are unchanged and all their existing tests
   continue to pass unmodified.
7. There is no code path that submits Peloton login credentials (username,
   password, or the authorization-code login form) programmatically as part
   of the new OAuth path — the initial code/token exchange only ever
   consumes an authorization code that a human obtained by completing the
   login in a real browser.
8. Written setup documentation (and/or a setup script) exists describing how
   a user completes the one-time browser login/consent step for this OAuth
   path, including the JavaScript-blocking workaround needed to capture the
   authorization code before `members.onepeloton.com`'s own frontend
   consumes it.
9. `tests/test_peloton_connector.py` includes regression tests covering:
   token exchange success, refresh-token rotation (criterion 3), refresh
   failure triggering fallback to manual recovery (criterion 5), and PKCE
   parameter generation (code verifier/challenge pair, `S256` method).

## Open questions

None that block the Architect. The BO live-verified the full flow (authorize
parameters, token exchange, refresh rotation) against the real account today
and provided exact endpoints, parameters, and the credential-separation and
fallback-on-failure requirements needed to implement and test this without
further clarification.

One design point is intentionally left to the Architect rather than decided
here (see "Out of scope"): exactly where the OAuth path sits in
`authenticate()`'s existing state-based dispatch (relative to the
already-dead automated username/password attempt and the
`ConnectorState.RECOVERY_REQUIRED`-gated manual path), and the specific
expiry safety margin used before proactively refreshing. Neither choice
changes what this feature must accomplish (criteria 1–9 above), so it
doesn't block moving forward.
