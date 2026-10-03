# Requirements: Wire StravaUnofficialConnector into app.py and setup_wizard.py

**Issue:** #22
**Depends on:** #18 — `StravaUnofficialConnector` implementation (CLOSED, complete)

---

## Summary

`StravaUnofficialConnector` (`trainiq/connectors/strava_unofficial.py`, issue
#18) is fully implemented but, by its own documented scope, was deliberately
**not** wired into the app's composition root or the first-time setup flow.
This issue closes that gap: make the connector reachable by a real user —
registered in `app.py`'s connector-construction step so it actually
participates in sync, and offered as a setup option in `setup_wizard.py` so
a user can supply their `_strava4_session` cookie without hand-editing the
credential store. No change to the connector itself, the sync engine, or any
other connector.

## Scope

### In scope

1. **`trainiq/app.py` — `_build_configured_connectors()`:**
   - Add a branch for `StravaUnofficialConnector`, following the exact same
     "configured" pattern already used for Strava/Peloton/Eufy: construct
     the connector only if its required credential is present in
     `CredentialStore`, log `"configured"` or `"skipped (not connected)"`
     accordingly, and catch/log/skip (never raise) on construction failure —
     consistent with the Graceful Degradation rule already documented for
     this function.
   - The presence check is whether a session cookie is stored for provider
     `"strava_unofficial"` (`CRED_STRAVA_SESSION_COOKIE` /
     `"session_cookie"`) — mirroring how the existing Strava branch checks
     for `refresh_token`. This does not re-validate cookie expiry at
     composition time; that is `authenticate()`'s job inside the sync
     engine, same as every other connector.
   - This is **additive**: the official `StravaConnector` branch is
     unchanged, and both connectors can be configured and run
     simultaneously (per #18's requirements doc, "both connectors can
     coexist").

2. **`trainiq/setup_wizard.py` — new setup step for the unofficial
   connector:**
   - A new `_setup_strava_unofficial()` function, following the same shape
     as the existing `_setup_strava()` / `_setup_email_password_provider()`
     steps: ask whether to connect, collect input, validate against the
     real connector, keep on success, roll back (delete anything just
     stored) on any failure — including an unexpected exception, not just
     an expected rejection, per the module's documented rollback rule.
   - Prompt text must give the user concrete instructions for obtaining the
     cookie: log into Strava in a browser, open DevTools, go to
     Application → Cookies, find `_strava4_session`, copy its value — this
     is the same instruction text `StravaUnofficialConnector.request_manual_recovery()`
     already returns; the wizard should present that guidance before
     prompting for the value; it may reuse that connector-provided string
     rather than it being re-authored.
   - Call the connector's `submit_manual_recovery(cookie_value)` to both
     validate (it makes a live request to `/api/v3/athlete` before storing
     anything) and persist the three session credentials. No separate
     manual write to `CredentialStore` is needed or correct — unlike
     Peloton/Eufy, this connector's own validation call is also the one
     and only write path, the same relationship `_setup_strava()` already
     has with `exchange_code_for_token()`.
   - Call site in `run_first_time_setup()`: offer this step for every
     first-time setup run, after the existing `_setup_strava()` (official
     OAuth) step and before `_setup_peloton()` — i.e. unconditionally
     alongside the official flow, not gated on whether the user connected
     or skipped official Strava. The two are independent, coexisting
     connectors (per #18), not an either/or choice.
   - Respects the module's existing cancellation semantics: `Ctrl+C`/EOF at
     this step stops asking about remaining providers without rolling back
     anything already configured earlier in the run (existing
     `SetupCancelled` behavior — unchanged).

3. **Integration testing:**
   - Unit/integration tests for the new `app.py` branch (mocked
     `CredentialStore`, asserting the connector is included/excluded from
     the returned list based on stored-cookie presence, and that a
     construction failure is caught and skipped, matching the existing
     tests for the other three connectors in `tests/test_app.py`).
   - Unit/integration tests for the new `setup_wizard.py` step (mocked
     connector / injected fake session, following the existing pattern in
     `tests/test_setup_wizard.py`: accept → stores and keeps; decline →
     nothing stored; validation rejects → nothing stored; unexpected
     exception during validation → nothing stored).
   - All tests use mocked HTTP — this sandbox/CI has no access to
     api.strava.com, same constraint as every existing connector test.
   - A real-account manual test is optional and BO-driven, not a pipeline
     deliverable (per issue body and the project's live-verification
     constraint — see "Live-verification constraint" in
     `docs/trainiq/roles/business-analyst.md`).

### Out of scope

- Any change to `StravaUnofficialConnector` itself
  (`trainiq/connectors/strava_unofficial.py`) — issue #18 is complete.
- Any change to `StravaConnector` (official OAuth), `PelotonConnector`,
  `EufyConnector`, or `SynchronizationEngine`.
- Automating cookie extraction (browser automation, scripted login) — the
  user pastes the cookie manually, same constraint #18 established.
- Deciding which Strava connector "wins" if a user connects both — both
  run independently and simultaneously; no precedence/dedup logic between
  them is requested or implied by the issue body.
- Real-account integration testing in CI (not available to the pipeline).

## Acceptance criteria

1. **`app.py` registers the connector when configured:** given a
   `CredentialStore` containing a non-empty `session_cookie` value under
   provider `strava_unofficial`, `_build_configured_connectors()` includes
   a `StravaUnofficialConnector` instance in its returned list.
2. **`app.py` skips it when not configured:** given a `CredentialStore`
   with no `session_cookie` stored for `strava_unofficial`,
   `_build_configured_connectors()` does not include the connector, and
   logs it as skipped ("not connected"), matching the existing log
   convention for the other three connectors.
3. **`app.py` degrades gracefully on construction failure:** if
   constructing `StravaUnofficialConnector` raises for any reason, the
   exception is caught, logged as a skip (not raised), and does not
   prevent the other configured connectors from being returned —
   consistent with the function's existing Graceful Degradation behavior.
4. **`app.py` coexistence:** given valid stored credentials for both
   `strava` (official) and `strava_unofficial`, both connectors are
   present in the returned list; neither construction affects the other.
5. **Setup wizard offers the step:** running `run_first_time_setup()`
   prompts the user, after the official Strava step, whether to connect
   Strava via session cookie — regardless of whether the official Strava
   step was accepted, declined, or failed.
6. **Setup wizard: decline:** answering "no" to the unofficial-Strava
   prompt stores nothing and proceeds to the next provider.
7. **Setup wizard: accept + valid cookie:** answering "yes" and supplying
   a cookie value that the connector's validation accepts results in all
   three session credentials (`session_cookie`, `session_obtained_at`,
   `session_expires_at`) being stored, and the step reports success.
8. **Setup wizard: accept + rejected cookie:** answering "yes" and
   supplying a cookie value that validation rejects results in nothing
   being stored (no partial credentials left behind) and a clear
   "nothing saved" message, matching the rollback behavior of the other
   setup steps.
9. **Setup wizard: accept + unexpected exception during validation:**
   if the validation call raises an exception other than an expected
   rejection, nothing is stored — rollback is unconditional, not limited
   to the expected-failure case, matching the module's documented rule
   for the other providers.
10. **Setup wizard: cancellation mid-step:** `Ctrl+C`/EOF during this step
    stops prompting for any remaining providers but does not undo any
    provider already successfully configured earlier in the same run.
11. **No regression:** all existing `app.py` and `setup_wizard.py` tests
    (Strava official, Peloton, Eufy) continue to pass unmodified.
12. **All new tests use mocked HTTP/mocked connectors** — no network
    access to Strava is required to run the test suite.

## Open questions

None that block the Architect. The ordering choice in AC5 (unofficial
Strava offered unconditionally after the official step, not only as a
fallback when official is skipped/fails) is inferred directly from the
issue body's "optional, offered after/alongside official Strava OAuth" and
from #18's explicit "both connectors can coexist" design — not an invented
business rule. If the BO intends the unofficial flow to be offered *only*
when the official flow was skipped or failed (a fallback-only UX rather
than an independent one), that's a one-line change for the Architect/
Developer to make against AC5 and AC10; flagging here so it's a visible
decision rather than silently assumed.

## References

- **Connector implementation:** `trainiq/connectors/strava_unofficial.py`
  (issue #18, closed)
- **Connector requirements:** `docs/trainiq/requirements/18-strava-session-cookie-connector.md`,
  `docs/trainiq/requirements/20-strava-session-cookie-connector.md`
- **Existing wiring pattern to follow:** `trainiq/app.py`
  `_build_configured_connectors()`, `trainiq/setup_wizard.py`
  `_setup_strava()` / `_setup_email_password_provider()`
