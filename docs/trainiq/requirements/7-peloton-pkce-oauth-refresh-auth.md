# Requirements: Add PKCE OAuth + refresh-token auth path to PelotonConnector

Issue: #7

## Summary

`PelotonConnector` currently has two authentication paths: automated
email/password login (`POST /auth/login`, confirmed broken with a 403 as
of 2026-09-28, see `BACKLOG.md` BL-008 and unlikely to come back), and a
manual-recovery bearer token the user extracts from an authenticated
browser session and supplies via `submit_manual_recovery()`. The manual
path works but has no auto-refresh — it needs fresh human attention roughly
every 48 hours indefinitely, which the BO wants reduced. The BO has
live-verified, end-to-end, against the real account, that Peloton's own
Auth0 tenant supports an OAuth authorization-code+PKCE flow (using an
unofficial, reverse-engineered client ID from a third-party client whose
source was read directly, not just its docs) whose resulting refresh token
can silently mint new access tokens indefinitely, with the refresh token
itself rotating on every use. The BO wants this wired into
`PelotonConnector` as a third, additional authentication path that
coexists with the other two — not a replacement for the manual-recovery
fallback, which must still work if this new path ever stops working (e.g.
the unofficial client ID is revoked).

## Scope

- A new OAuth authorization-code+PKCE authentication path for
  `PelotonConnector`, distinct from both the existing automated
  email/password path and the existing manual-bearer-token recovery path.
  All three paths coexist; none is removed.
- New, distinctly-named credential storage for this path — the issue
  itself names `oauth_access_token`, `oauth_refresh_token`, and
  `oauth_expires_at` — kept clearly separate from the existing
  `manual_bearer_token`/`email`/`password` entries so it's never ambiguous
  which path's credentials are "active."
- Using a stored, non-expired access token directly when available,
  without a network round trip (the same pattern `StravaConnector`
  already uses for its own access-token/expiry check).
- Refreshing the access token via a refresh-token grant when the stored
  access token is absent or expired, using the stored refresh token.
- On every successful refresh, persisting (rotating) **both** the new
  access token and the new refresh token — Peloton's refresh tokens rotate
  on every use (live-confirmed by the BO), so continuing to reuse an old
  stored refresh token after a successful refresh would break the next
  refresh. This is the same rotate-everything discipline
  `StravaConnector._refresh()` already follows for its own refresh token.
- Graceful fallback: if the refresh-token grant fails for any reason (the
  unofficial client ID is revoked or throttled, the refresh token is
  rejected, etc.), the connector must not error out — it must behave the
  same way every other connector's "expected auth failure" already does
  (return `False` from `authenticate()`, no raise), so the existing
  manual-bearer-token recovery path remains usable as the fallback of last
  resort, per the module's existing Soft Degradation pattern.
  `submit_manual_recovery()` / `request_manual_recovery()` are unchanged.
- Written documentation (a setup script, a doc page, or both) for the
  one-time, human-performed initial login step needed to obtain the first
  authorization code — including the JavaScript-blocking workaround the BO
  found necessary (see `BACKLOG.md` BL-008) — so a person other than the
  BO could actually do this without re-deriving it from scratch. This
  ticket does not require the initial code-capture step itself to be
  scripted (see "Out of scope" below); it requires that a human can follow
  written instructions to seed the credentials this new path then takes
  over maintaining.
- Test coverage (using a fake HTTP session/client, per this connector's
  existing testing convention — this sandbox cannot reach
  `auth.onepeloton.com` or `api.onepeloton.com`) for: using a valid
  stored access token with no network call; refreshing an expired/absent
  access token and persisting both rotated values; a failed refresh
  falling back to `False`/existing manual-recovery behavior rather than
  raising; and the existing automated email/password and manual-recovery
  paths continuing to work unmodified.

### Out of scope

- Scripting or otherwise automating the one-time human browser login /
  authorization-code-capture step. The BO was explicit that this step
  should stay manual — this ticket documents it, it does not eliminate it.
- Fixing, removing, or otherwise changing the existing (confirmed broken)
  automated email/password login path. The BO did not ask for it to be
  removed, only for a better alternative to exist alongside it; whether
  and in what order `authenticate()` should still attempt it versus this
  new path is a design decision, not a requirement — see "Open questions."
- Any change to `submit_manual_recovery()` / `request_manual_recovery()`
  themselves, or to the manual-bearer-token path's behavior.
- Any change to `download()` / `normalize()` (issue #5's endpoint/field-
  mapping fix, already closed, is unrelated to authentication).
- Seeking or obtaining Peloton's written permission for the unofficial
  client ID — the BO has already accepted that risk (module docstring/BL-
  008 already document this connector as Tier 2 Unofficial); this ticket
  is scoped to the connector code and its fallback behavior only, not to
  resolving that legal/ToS question.
- GraphQL-only Peloton features (already out of scope per the module's
  existing Feature 3.3 / R-PELOTON-05 scope note, unrelated to this issue).

## Acceptance criteria

1. `PelotonConnector` supports authenticating via a stored OAuth access
   token, a stored OAuth refresh token, and the existing manual bearer
   token, as three distinguishable credential entries — never conflated
   with each other or with `email`/`password`.
2. Given a stored OAuth access token whose stored expiry is still in the
   future (beyond a safety margin), `authenticate()` uses it directly and
   makes no network call to the token endpoint.
3. Given no stored OAuth access token (or an expired one) but a stored
   OAuth refresh token, `authenticate()` performs a refresh-token grant
   and, on a successful response, persists both the new access token and
   the new refresh token returned by that response — not just the access
   token.
4. After a successful refresh, the previously-stored refresh token is no
   longer the one used for the next refresh attempt — the newly-rotated
   one is used instead (i.e., a test simulating two consecutive refreshes
   must show the second refresh request using the token returned by the
   first, not the original stored one).
5. Given a refresh-token grant that fails (non-2xx or a rejected token),
   `authenticate()` returns `False` rather than raising, consistent with
   the "must never raise for expected auth failures" contract every other
   connector's `authenticate()` already follows — and the existing
   manual-bearer-token path remains usable afterward (a subsequent
   `submit_manual_recovery()` + `authenticate()` call still succeeds).
6. The existing automated email/password path and the existing manual-
   recovery path both continue to pass their current tests unmodified by
   this change.
7. Written setup instructions exist (in the repo) describing how a human
   obtains the initial authorization code for this provider, including the
   JavaScript-blocking workaround, sufficient for the initial
   `oauth_access_token`/`oauth_refresh_token` credentials to be seeded
   without needing to rediscover that workaround from scratch.
8. `download()` and `normalize()` behavior is unchanged by this ticket —
   verified by the existing test suite for those methods continuing to
   pass with no modification.

## Open questions

None that block the Architect. Two items are noted here for the
Architect's design (not the BO's judgment), since Triage already flagged
"auth path coexistence" and "fallback behavior" as the architectural
decisions this issue needs:

- **Attempt ordering**: today `authenticate()` tries automated
  email/password first, then (once `RecoveryRequired`) the manual token.
  The issue doesn't say whether the new OAuth path should be tried before,
  after, or instead of the (confirmed-broken, but not explicitly
  deprecated) email/password attempt on a normal, non-`RecoveryRequired`
  sync. Left to the Architect to decide; whatever is chosen must still
  satisfy acceptance criteria 5–6 above (graceful fallback to manual
  recovery; no regression to the existing paths).
- **First-time onboarding vs. ongoing refresh**: the issue is framed
  around reducing *ongoing* manual-token churn for an already-connected
  account. It doesn't say whether a brand-new user connecting Peloton for
  the first time should be steered toward this OAuth path from the start,
  or still land on the manual-bearer-token path initially and adopt OAuth
  later. Left to the Architect/Developer; acceptance criterion 7's setup
  documentation covers the mechanics either way.
