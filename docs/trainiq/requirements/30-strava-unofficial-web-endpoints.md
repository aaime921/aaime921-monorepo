# Requirements: Switch StravaUnofficialConnector to Strava's Web Endpoints

**Issue:** #30
**Related:** #26 (closed, host fix), PR #28 (merged, `a2c0662`), #18 (original
connector design), #25 (setup wizard; re-test after this), #29 (ADR
correction, docs-only, does not block this)

## Summary

`StravaUnofficialConnector` (`trainiq/connectors/strava_unofficial.py`) was
fixed on #26/PR #28 to point `DEFAULT_BASE_URL` at `https://www.strava.com`
(the only resolvable Strava host), but it still builds its requests against
the `/api/v3/*` paths. The BO has now live-verified, with a real logged-in
`_strava4_session` cookie and no OAuth token, that `/api/v3/*` rejects
cookie-only auth outright (401), while Strava's own **web** JSON endpoints —
specifically `/athlete/training_activities` — accept the same cookie and
return real activity data (200, JSON). This is exactly the risk #18's
original architecture doc flagged as unresolved before this connector ever
shipped ("whether `api.strava.com` accepts cookie-only auth at all," with
`www.strava.com`'s internal endpoints as the likely real target) — it is now
confirmed, with evidence, rather than theoretical. Until this is fixed, the
unofficial Strava connector cannot authenticate or download any data at all,
regardless of cookie validity.

## Scope

- `download()` fetches activity history from
  `GET /athlete/training_activities` instead of
  `GET /api/v3/athlete/activities`, driven by the live-verified response
  shape: `{models: [...], page, perPage, total}`. Pagination must be driven
  by `page` and `total` — the BO's testing showed `per_page` is **ignored**
  by this endpoint (requesting `per_page=2` still returned 20 items), so the
  existing per-page-count-based loop termination (`if not batch: break`) no
  longer reliably signals "no more pages" and must be replaced with a
  `page`/`total`-aware check (keep requesting while
  `page * <items-returned-per-page> < total`, or equivalent — described
  here as *what* the loop must track, not the exact code).
- The request must set `X-Requested-With: XMLHttpRequest`, `Accept:
  application/json`, and a browser-like `User-Agent` header — the BO's
  testing shows these are required for the endpoint to return JSON rather
  than an HTML page.
- `submit_manual_recovery()`'s cookie-validation call must use an endpoint
  that actually accepts the cookie. The BO's evidence shows two viable
  options: `GET /dashboard` (200 for a valid cookie, redirect-to-login for an
  invalid one) or `GET /athlete/training_activities` page 1 (200 JSON for
  valid, non-JSON/redirect for invalid). Either is acceptable; `/api/v3/*` is
  not, since it now returns 401 even for a valid cookie.
- Normalization must map the web endpoint's `*_raw` fields (e.g.
  `distance_raw`, `moving_time_raw`, `elapsed_time_raw`, `elevation_gain_raw`,
  plus `id`, `name`, `display_type`/`activity_type_display_name`, start
  time/date fields, `commute`, `private`, `has_latlng`, `description`) onto
  the same internal activity shape `normalize()` already produces today
  (`provider`, `external_id`, `start_time`, `duration_s`, `discipline_raw`,
  `avg_hr`, `max_hr`, `avg_power`, `max_power`, `distance_m`, `calories`,
  `synced_at`). Per this project's evidence-based principle, never fabricate
  a field: if the web payload doesn't carry an equivalent for something the
  current mapping sets (e.g. heart rate, power — not shown in the BO's
  sample fields), it stays `None`, exactly like today's documented "never
  reported by this endpoint" fields. Units for each `*_raw` field used must
  be documented in the normalization code (e.g. a comment) or the handoff
  doc, since the BO's evidence doesn't state them explicitly.
- An expired or invalid cookie — a redirect to a login page, a non-2xx
  status, or an HTML response where JSON was expected — must map to the
  existing recovery-required error path (clearing stored credentials and
  raising `AuthenticationError`, same as today's 401/403 handling), not
  `TransientError`. This is the same classification bug class #26 was
  scoped around for the old endpoint; the new endpoint needs the same
  correctness guarantee.
- Unit test coverage (mocked HTTP only, per this project's
  live-verification constraint — no live network or real cookie in CI) using
  fixtures shaped like the BO's live-captured payload above for: successful
  pagination across multiple pages ending via `page`/`total`, cookie
  validation success/failure against the new validation endpoint, and the
  "HTML/redirect instead of JSON" expired-cookie case mapping to the
  recovery-required path.
- A live-verification step for the BO, documented at
  `docs/trainiq/verification/strava-unofficial-<date>.md` (matching the
  Eufy/Peloton convention this project already uses) — this pipeline cannot
  reach Strava or use a real cookie itself, so a human-run confirmation that
  the switched endpoints work end-to-end against a real account is a
  deliverable of this issue, not optional follow-up.

### Out of scope

- Any change to `StravaConnector` (official OAuth connector, `strava.py`) —
  unaffected; it never used `/api/v3/*` cookie auth and delegates HTTP to
  `stravalib`.
- Re-litigating the base hostname (`https://www.strava.com`) — already
  fixed on #26/PR #28 and confirmed reachable. Only the path and headers
  change here.
- The `authenticate()` cached-cookie-expiry check, `request_manual_recovery()`'s
  instructional text, and the 429/5xx/network-level transient-error
  classification logic — none of that is implicated by switching endpoints,
  except where this doc explicitly calls out the HTML/redirect-on-expiry
  case above.
- #29's ADR-007/BACKLOG.md documentation correction — tracked separately,
  does not block or depend on this issue.
- Wiring this connector into `app.py`/`setup_wizard.py` — already done on
  #22, unaffected by an internal endpoint change.
- Deciding the exact arithmetic for the `page`/`total` loop boundary (e.g.
  whether to also track items-returned-this-page in case Strava's `models`
  array length ever varies) — that's an implementation detail for the
  Architect/Developer, described here only as the behavior the loop must
  achieve (stop once all `total` items have been fetched, not once an empty
  page is seen, since pages are no longer guaranteed to shrink to empty).

## Acceptance criteria

1. `download()` fetches activity history from `/athlete/training_activities`,
   paginated via `page` and the response's `total` field, instead of
   `/api/v3/athlete/activities`.
2. The request includes `X-Requested-With: XMLHttpRequest`, `Accept:
   application/json`, and a browser-like `User-Agent` header.
3. `submit_manual_recovery()`'s cookie-validation call targets an endpoint
   that accepts the cookie (`/dashboard` or `/athlete/training_activities`
   page 1) — not `/api/v3/athlete`, which now 401s even for a valid cookie.
4. `normalize()` maps the web payload's `*_raw` fields to the same internal
   activity shape `normalize()` currently produces, with each mapped
   field's unit documented. Any current field with no equivalent in the web
   payload stays `None` — not fabricated.
5. An expired/invalid cookie (redirect to login, non-2xx, or HTML instead of
   JSON) raises the existing recovery-required (`AuthenticationError`) path
   and clears stored credentials, exactly as today's 401/403 handling does —
   never classified as `TransientError`.
6. Unit tests (mocked HTTP, no live network/cookie) cover: multi-page
   pagination terminating correctly via `page`/`total`; successful cookie
   validation against the new endpoint; and the expired/invalid-cookie case
   mapping to the recovery-required path. All use fixtures shaped like the
   BO's live-captured payload.
7. A live-verification doc at
   `docs/trainiq/verification/strava-unofficial-<date>.md` is created,
   documenting the BO's end-to-end confirmation against a real account.

## Open questions

**Neither blocks the Architect — both are technical investigation/design
calls, not BO decisions, since the BO's evidence already answers the
business-level question (this works, use it):**

1. **Exact pagination boundary condition.** The BO's evidence confirms
   `per_page` is ignored and `total` is reliable, but doesn't specify
   exactly how many items come back per page in practice (20 was observed
   once). The Architect should design the loop against `total` rather than
   assume a fixed page size.
2. **Exact mapping for fields with no obvious `*_raw` equivalent** (e.g.
   heart rate, power — present in `/api/v3`'s payload, not mentioned in the
   BO's sample of `/athlete/training_activities` fields). Per the
   evidence-based principle, these stay `None` unless the Architect/Developer
   find a real equivalent field in the actual response — this doc does not
   assume one exists.

## Handoff

Commit this requirements doc, comment on the issue with the summary and a
link, remove `stage:ba`, add `stage:architect`.
