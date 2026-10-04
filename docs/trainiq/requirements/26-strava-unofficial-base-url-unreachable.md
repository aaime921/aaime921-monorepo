# Requirements: Fix unreachable BASE_URL in StravaUnofficialConnector

**Issue:** #26
**Related:** #18 (StravaUnofficialConnector implementation, closed), #20
(duplicate-numbered requirements/discovery docs for the same #18 topic),
#22 (wiring into `app.py`/`setup_wizard.py`, closed/merged), #25 (setup
wizard prompts not displayed — the BO hit this bug while testing #25's
workaround/fix)

## Summary

`StravaUnofficialConnector` (`trainiq/connectors/strava_unofficial.py`,
issue #18) cannot reach Strava at all: its `DEFAULT_BASE_URL` is
`https://api.strava.com`, but the BO's live DNS checks (`nslookup`, `dig`,
`host`) all return NXDOMAIN for that hostname, while `strava.com` itself
resolves fine. Every HTTP call the connector makes (`/api/v3/athlete`,
`/api/v3/athlete/activities`) targets this unreachable host, so the
connector fails with `TransientError` any time it's actually exercised —
which is how the BO found this, while testing the fix for issue #25. The
BO wants the correct endpoint identified and the connector pointed at it
so the Strava-unofficial integration can actually run.

## Scope

### In scope

- Investigate and confirm the correct HTTP endpoint for cookie-based
  (`_strava4_session`) access to Strava's activities API — `api.strava.com`
  is confirmed unreachable per the BO's live evidence.
- Update `DEFAULT_BASE_URL` (and any other hardcoded references to the old
  host) in `StravaUnofficialConnector` to the corrected, live-verified
  endpoint.
- Update/add test coverage (mocked HTTP, per this project's existing
  pattern) so tests assert against the corrected endpoint rather than the
  broken one.
- Re-verify connectivity: a live request against the corrected endpoint,
  using a real `_strava4_session` cookie, succeeds — BO-driven, since
  neither CI nor this pipeline's sandbox has access to real BO accounts or
  to arbitrary external hosts (see "Live-verification constraint" in
  `docs/trainiq/roles/business-analyst.md`).

### Out of scope

- Any other change to `StravaUnofficialConnector`'s logic — authentication
  caching, 401/403 credential-clearing, 429/404/5xx classification,
  pagination, field normalization. Issue #18 already implemented all of
  this and nothing here reports it broken.
- Any change to the official, OAuth-based `StravaConnector`
  (`trainiq/connectors/strava.py`) — unaffected by this bug.
- Automating cookie extraction or login — unchanged, out of bounds per
  project ground rules.
- Live-account integration testing in CI — not available to the pipeline;
  the BO performs this manually (see "Live-verification constraint").
- Re-litigating issue #25 (setup wizard prompts) — that is a separate,
  already-filed issue. This issue addresses only the connectivity bug that
  surfaced while testing #25, not #25 itself.

## Acceptance criteria

1. **Root cause accepted as given, not re-litigated.** `DEFAULT_BASE_URL =
   "https://api.strava.com"` (`trainiq/connectors/strava_unofficial.py`,
   currently line 51) is confirmed as the value used for every
   `StravaUnofficialConnector` HTTP call. The BO's DNS evidence (NXDOMAIN
   on `api.strava.com` via `nslookup`/`dig`/`host`, while `strava.com`
   itself resolves) is accepted as ground truth per this project's
   evidence-based principle — downstream roles do not need to independently
   re-verify the DNS failure itself, only identify and verify the
   replacement.
2. **Endpoint corrected and live-verified.** `DEFAULT_BASE_URL` is updated
   to an endpoint that is confirmed, by an actual successful live request
   (BO-driven — see Out of scope), to accept `_strava4_session` cookie
   authentication for both `/api/v3/athlete` and
   `/api/v3/athlete/activities`. "Live-verified" means a real request that
   succeeded, not an inference from documentation or a community report
   alone.
3. **No behavior change beyond connectivity.** All other
   `StravaUnofficialConnector` behavior (auth caching, credential-clearing
   on 401/403, 429/404/5xx classification, pagination, `normalize()`
   output) is unchanged by this fix.
4. **Tests reflect the corrected endpoint.** Any test asserting the literal
   string `https://api.strava.com`, or constructing expected request URLs
   from it, is updated to the corrected value — no test is left passing
   against the now-known-broken host.
5. **No regression.** All other existing tests in
   `tests/test_strava_unofficial_connector.py`, `tests/test_app.py`, and
   `tests/test_setup_wizard.py` continue to pass unmodified.
6. **Code citation re-verified against current `main`.** The issue's cited
   location (`trainiq/connectors/strava_unofficial.py` line 51) is
   confirmed accurate before scoping the fix further, since this file has
   been touched since #18 by #22's wiring work and line numbers may have
   shifted.

## Open questions

- **The correct replacement endpoint is not resolved by this doc** — this
  is a known risk surfacing, not a new discovery. The architecture doc for
  issue #18 (`docs/trainiq/architecture/18-strava-session-cookie-connector.md`,
  "Risks/tradeoffs") already flagged, before this connector shipped,
  that "`api.strava.com` actually accepts cookie-only auth at all" was an
  open question the original design never resolved with live evidence,
  noting that "the `strava-offline` reference project and some community
  reports describe hitting `www.strava.com`'s internal endpoints instead."
  That doc recommended a live capture during/after implementation, which
  was never done. Determining the actual correct host/path (which may not
  be REST-shaped the same way as the official OAuth API — cookie-based
  internal endpoints often aren't) is a technical investigation for the
  Architect, the same framing #18's own architecture doc already used —
  not a business decision requiring the BO's judgment. This does not block
  handing off to the Architect (same precedent as #18, where this exact
  question was flagged but didn't block implementation).
- **Verifying the fix works still needs the BO.** Per the live-verification
  constraint, confirming the corrected endpoint actually works requires a
  request from an environment with real internet access and a real BO
  session cookie — this pipeline's sandbox has neither. The Architect/
  Developer should design the fix so it's verifiable via mocked tests in
  CI, and flag in their handoff that a BO live-verification pass — ideally
  producing a `docs/trainiq/verification/strava-unofficial-<date>.md`
  capture matching the existing Peloton/Eufy convention — is the only way
  to fully close this out. This was already recommended once in #18's
  architecture doc and never done; worth not letting it slip a second
  time.

## References

- Root cause: `trainiq/connectors/strava_unofficial.py` (`DEFAULT_BASE_URL`,
  currently line 51)
- Prior flag of this exact risk: `docs/trainiq/architecture/18-strava-session-cookie-connector.md`
  ("Risks/tradeoffs")
- Connector requirements: `docs/trainiq/requirements/18-strava-session-cookie-connector.md`
- Wiring (unaffected by this issue, already merged): `docs/trainiq/requirements/22-wire-strava-unofficial-connector.md`
- Related, separate issue (not reopened or re-scoped by this doc): #25 —
  setup wizard prompts not displayed, during whose testing the BO
  encountered this connectivity bug
