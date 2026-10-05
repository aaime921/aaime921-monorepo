# Architecture: Fix `StravaUnofficialConnector`'s unreachable `DEFAULT_BASE_URL`

Issue: #26
Requirements: [`docs/trainiq/requirements/26-strava-unofficial-base-url-fix.md`](../requirements/26-strava-unofficial-base-url-fix.md), [`docs/trainiq/requirements/26-strava-unofficial-base-url-unreachable.md`](../requirements/26-strava-unofficial-base-url-unreachable.md) (two requirements docs exist for this issue from two separate BA passes; both agree on scope and acceptance criteria, so this design satisfies both)
Prior design this touches: [`docs/trainiq/architecture/18-strava-session-cookie-connector.md`](18-strava-session-cookie-connector.md) ("Risks/tradeoffs" already flagged this exact risk before the connector shipped)

## Independent verification performed before designing

Per this role's Evidence-based principle, I did not design against the issue's or the BO's comment's claims alone. I ran a direct DNS resolution check from this sandbox (`getent hosts`, system resolver — independent of and prior to reading any HTTP response) before writing this doc:

```
$ getent hosts api.strava.com    -> (no output — no DNS record at all)
$ getent hosts www.strava.com    -> resolves, CNAME to a CloudFront distribution (d3u3hkafyj3iak.cloudfront.net)
$ getent hosts strava.com        -> resolves directly (4 A records)
```

This independently confirms, without relying on the issue body or any comment as the sole source: **`api.strava.com` has no DNS record whatsoever** (not merely unreachable from a restricted network — the name itself does not exist), while **`www.strava.com` is a live, resolvable host**. This corroborates the BO's 2026-10-05 comment and the original issue report. A direct HTTPS request to either host from this sandbox is blocked by this environment's own egress policy (confirmed separately — `connect_rejected` at the proxy layer, not a DNS failure), consistent with this repo's existing documented sandbox-egress constraint (`strava.py`, `strava_unofficial.py` module comments) — that constraint is real and independent of the DNS finding above.

## Approach

Change `DEFAULT_BASE_URL` in `trainiq/connectors/strava_unofficial.py` (currently line 51) from `https://api.strava.com` to `https://www.strava.com` — the one hostname independently confirmed resolvable above that plausibly serves Strava's REST API (it is Strava's primary web/API host; `api.strava.com` is not a Strava host at all per the DNS evidence, regardless of what ADR-007/BACKLOG.md BL-001 currently say — see "Documentation discrepancy" below).

**Paths are unchanged**: `/api/v3/athlete` and `/api/v3/athlete/activities` stay exactly as they are. This is a deliberate, minimal-scope choice, not an oversight:

- Both requirements docs' acceptance criteria are written against these exact paths, and scope this as "only the host portion of the constant changes" (26-strava-unofficial-base-url-fix.md, Scope).
- The one open technical question neither requirements doc resolves — whether Strava's versioned REST API (`/api/v3/*`) actually honors `_strava4_session` cookie-only auth, or whether cookie auth only works against `www.strava.com`'s separate, internal (non-`/api/v3`) website endpoints — **cannot be settled from this sandbox**. It requires a real request with a real session cookie, which needs both live network egress and a real BO cookie, neither available here. This is the exact same unresolved question #18's own architecture doc flagged before this connector ever shipped, and it remains unresolved today; I have no new evidence that changes the answer either way.
- Guessing at specific internal endpoint paths (e.g. ones used by third-party scraping tools) without a live-captured response to confirm them would violate this role's Evidence-based principle — it would replace one unverified hostname assumption with an equally unverified path assumption. The requirements docs explicitly reserve this as the Architect's technical call, not a mandate to pick a specific unverified alternative.
- Keeping the path unchanged means this fix stays a true one-constant change (satisfies AC3 of the `-fix.md` requirements doc exactly), and if BO live-verification (see below) shows `/api/v3` rejects cookie auth, swapping to a different path prefix is a contained, obvious follow-up to the same constant — not a redesign.

## Documentation discrepancy (flagged, not corrected here)

`docs/trainiq/adr/ADR-007-no-hardcoded-endpoints.md` and `projects/trainiq/BACKLOG.md` (BL-001) both state that `api.strava.com` is Strava's current, valid API hostname, migrating to `api-v3.strava.com` on 2027-01-04. The independent DNS evidence above directly contradicts the premise that `api.strava.com` is currently valid — it has no DNS record at all, which is inconsistent with "currently valid, migrating later." ADR-007's underlying architectural principle (no hardcoded remote endpoints) is unaffected by this and remains sound regardless of which specific hostname is correct.

The BO asked, in their 2026-10-05 comment, for ADR-007 and BACKLOG.md to be corrected as part of this issue. **This design doc does not make that correction** — this routine's mandated work location for this issue is `docs/trainiq/architecture/*` only, and `docs/trainiq/adr/*` / `projects/trainiq/BACKLOG.md` are outside it. Flagging this explicitly here and in the handoff comment rather than silently skipping it or silently overstepping scope.

## Affected components/files

- **Changed:** `trainiq/connectors/strava_unofficial.py` — `DEFAULT_BASE_URL` constant only (line 51 as of this writing; the Developer should re-confirm the line number against current `main`, since this file has shifted before per the requirements docs' own note).
- **Not changed:** `submit_manual_recovery()`, `download()`, `_authenticated_get()`, `authenticate()`, `normalize()`, pagination, error classification, credential handling — none of this is implicated by a wrong hostname (requirements docs' explicit Out of scope, both docs agree).
- **Not changed:** `trainiq/connectors/strava.py` (official OAuth connector, delegates to `stravalib`, unaffected).
- **Descriptive comments only, not changed:** the "this sandbox cannot reach api.strava.com" comments in `strava.py` and `strava_unofficial.py` — these describe this execution environment's own egress restriction (confirmed still accurate above), not a request-construction bug; both requirements docs explicitly exclude these from scope.
- **Changed:** `tests/test_strava_unofficial_connector.py` — see Test strategy notes.

## Interfaces/contracts

```python
DEFAULT_BASE_URL = "https://www.strava.com"  # ADR-007: one named constant, constructor-overridable for tests
```

No other signature, constant, or return shape changes. `self._base_url` continues to be used exactly as today: `f"{self._base_url}{path}"` in `_authenticated_get()`, where `path` is still `/api/v3/athlete` or `/api/v3/athlete/activities`.

## Task breakdown

1. Confirm `DEFAULT_BASE_URL`'s current line number in `trainiq/connectors/strava_unofficial.py` on current `main` (requirements docs flag this may have shifted).
2. Change the constant's value from `"https://api.strava.com"` to `"https://www.strava.com"`. Leave the inline `# ADR-007: ...` comment as-is (still accurate — the value is still a single named, overridable constant).
3. Grep the codebase (`projects/trainiq/`) for any other literal `api.strava.com` in a request-constructing code path (not a descriptive comment) — confirmed during this design that none exists today (only the one constant plus descriptive sandbox-egress comments in `strava.py`/`strava_unofficial.py`), but re-verify after this change since the Developer's diff could introduce or reveal one.
4. In `tests/test_strava_unofficial_connector.py`, add or extend a test asserting the fake session's recorded request URL(s) are built from `https://www.strava.com` (e.g. assert on the fake's captured `url` argument for both `submit_manual_recovery()`'s validation call and a `download()` call) — today's tests use a fake session and don't appear to hardcode the old URL string (confirmed by inspection: no literal `api.strava.com` found in the current test file), so this is new coverage, not a find-and-replace.
5. Run the full `StravaUnofficialConnector` test file and the full suite — confirm no regression, consistent with AC4/AC5 (`-fix.md`) and AC3/AC5 (`-unreachable.md`).

## Test strategy notes

- Mocked HTTP only (fake session), per this project's live-verification constraint — no live network in CI, consistent with every other connector's test suite.
- New/extended assertion: the request URL passed to the fake session's `.get()` starts with `https://www.strava.com` for both call sites (`submit_manual_recovery()` and `download()`), not just that *a* request was made.
- No new test infrastructure needed — reuse the existing fake-session pattern already in this test file.
- Out of scope for this fix's tests (per both requirements docs): anything exercising auth caching, pagination, 401/403/429/404/5xx classification, or `normalize()` — those are unchanged and already covered.

## Risks/tradeoffs

- **The central risk from #18's original design remains genuinely unresolved by this fix, not newly introduced by it:** whether `www.strava.com/api/v3/*` accepts `_strava4_session` cookie-only auth at all, versus requiring `www.strava.com`'s separate internal/website endpoints (the open question both this issue's requirements docs and #18's architecture doc flag, citing the `strava-offline` reference project and community reports as suggesting the latter). This fix corrects a hostname that cannot possibly work (no DNS record) to one that is confirmed reachable and is plausibly correct, but does **not** itself prove cookie auth succeeds there. **This is the one thing the BO must live-verify** — ideally producing `docs/trainiq/verification/strava-unofficial-<date>.md` (matching the Eufy/Peloton convention) from a real request with a real `_strava4_session` cookie against `https://www.strava.com/api/v3/athlete`. If that request returns 401/403 despite a known-good cookie, the correct follow-up is almost certainly swapping the path prefix to an internal endpoint (not `/api/v3`) — a separate, small, contained change, not a reason to hold this fix.
- **`api.strava.com` has zero DNS records**, not merely "unreachable from this sandbox" — this is a stronger and more final finding than the original issue assumed (the issue's framing left open whether it might be a transient or environment-specific resolution problem). Worth stating plainly so nobody downstream re-litigates whether `api.strava.com` might start working again.
- **ADR-007 and BACKLOG.md BL-001 are now known to contain an incorrect factual premise** about `api.strava.com` (see "Documentation discrepancy" above) — flagged, not corrected, since it's outside this issue's mandated work location (`docs/trainiq/architecture/*`). This should not block handoff to the Developer, whose scope is the code fix, but it does need a human or a routine with write access to `docs/trainiq/adr/*` / `projects/trainiq/BACKLOG.md` to actually make the correction — raising this explicitly in the handoff comment rather than letting it quietly drop.
- **No behavioral change beyond the host** — this design makes no changes to auth, normalization, pagination, or error handling, consistent with both requirements docs' explicit scope boundary.
