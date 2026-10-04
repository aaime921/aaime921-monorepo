# Requirements: Fix StravaUnofficialConnector's Unreachable BASE_URL

**Issue:** #26
**Related:** #25 (Setup wizard prompts not displayed) — this bug is why the
Strava-unofficial step failed with `TransientError` during #25's testing,
not a wizard/prompting defect itself.
**Depends on:** #18 (connector implementation), #22 (wiring into app.py /
setup_wizard.py) — both closed/complete. No change to either's scope.

## Summary

`StravaUnofficialConnector` (`trainiq/connectors/strava_unofficial.py`,
line 51) sets `DEFAULT_BASE_URL = "https://api.strava.com"`. The BO has
live-verified (`nslookup`, `dig`, and `host` all returning NXDOMAIN for
`api.strava.com`, while plain `strava.com` resolves) that this hostname does
not exist. Every request the connector makes — the validation call inside
`submit_manual_recovery()` and the activity fetch inside `download()`, both
built as `f"{self._base_url}{path}"` — therefore fails at the DNS layer
before it can even reach Strava, regardless of whether the supplied session
cookie is valid. This is a pure connectivity defect, not an auth or
cookie-validity problem, and it blocks the unofficial Strava connector from
working at all.

## Scope

- Correct `DEFAULT_BASE_URL` in `trainiq/connectors/strava_unofficial.py`
  to a real, resolvable Strava REST API hostname that serves the
  `/api/v3/athlete` and `/api/v3/athlete/activities` paths this connector
  already constructs — i.e. only the host portion of the constant changes;
  the path strings built in `submit_manual_recovery()` and `download()` are
  untouched unless the corrected host requires a different path prefix to
  reach the same two endpoints.
- Grep the codebase for any other literal reference to the broken
  `api.strava.com` hostname (comments mentioning it as a sandbox-egress
  constraint, e.g. in `strava.py`'s docstring and `strava_unofficial.py`'s
  own `_RequestsSession` comment, are descriptive/historical and are **not**
  in scope to edit — only a hostname used in an actual outgoing request is a
  bug here).
- Update any existing unit test that hardcodes or asserts against the old
  `https://api.strava.com` string so it reflects the corrected constant.
- Add/extend unit test coverage (mocked HTTP session, per this project's
  live-verification constraint — no live network in CI) asserting that
  `submit_manual_recovery()` and `download()` issue their requests against
  the corrected base URL.

### Out of scope

- Any change to `StravaConnector` (official OAuth connector) — it goes
  through `stravalib`, which abstracts its own base URL; this issue is
  about the unofficial, cookie-based connector only.
- Any change to `authenticate()`'s expiry-estimate logic, `normalize()`'s
  field mapping, `download()`'s pagination loop, or error classification in
  `_authenticated_get()` — none of that is implicated by a wrong hostname.
- Re-opening #22's wiring work (`app.py` / `setup_wizard.py`) — unaffected
  by this fix; the wiring calls into the connector the same way regardless
  of which host it targets internally.
- Live-account verification against the real Strava API in CI — not
  available to this pipeline (per the project's live-verification
  constraint). A final live-account check, if the BO wants one, is their
  own optional, separate step, not a pipeline deliverable.
- Deciding whether the unofficial connector should instead reuse
  `stravalib`'s own (working) base-URL handling instead of raw HTTP — that
  would be a design change beyond a one-constant fix; flagged as an open
  question below rather than assumed.

## Acceptance criteria

1. `DEFAULT_BASE_URL` in `trainiq/connectors/strava_unofficial.py` is a
   hostname that actually resolves (not `api.strava.com`), combined with
   the existing `/api/v3/athlete` and `/api/v3/athlete/activities` paths to
   form the same REST endpoints the connector already targets.
2. No other request-constructing code path in the codebase still points at
   the broken `api.strava.com` hostname after this fix (verified by
   search — descriptive comments about sandbox egress are not required to
   change).
3. `submit_manual_recovery()`'s validation request and `download()`'s
   activity-fetch request are both built from the single corrected
   `DEFAULT_BASE_URL` / `self._base_url` constant — i.e. the fix is made in
   one place, not independently patched per call site.
4. All existing `StravaUnofficialConnector` unit tests pass unmodified,
   except any that literally assert the old broken URL string, which are
   updated to assert the corrected one.
5. A new or updated unit test (mocked HTTP session/fake, no live network)
   confirms the connector's outgoing request URL uses the corrected host.
6. No behavioral change to authentication, normalization, pagination, or
   error handling — only the base URL differs from before the fix.

## Open questions

**Does not block the Architect — both are investigation/design calls for
them, not BO decisions:**

1. **What is the correct hostname?** The issue body's root-cause analysis
   already points at `trainiq/connectors/strava_unofficial.py:51`, but
   identifying *which* real hostname replaces `api.strava.com` for
   session-cookie–authenticated REST calls is a technical investigation,
   not a requirement — intentionally left for the Architect rather than
   decided here, per this role's scope (describe *what*, not *how*).
   `StravaConnector` (official OAuth, `strava.py`) can't be used as a
   direct before/after comparison for this, since it delegates all HTTP to
   `stravalib` and never constructs a base URL itself.
2. **Should this stay raw HTTP, or reuse `stravalib`'s transport?** Out of
   scope here (see above) — noted so the Architect can make a deliberate
   call rather than it being silently assumed either way.
