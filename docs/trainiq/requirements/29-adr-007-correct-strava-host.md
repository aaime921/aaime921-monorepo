# Requirements: Correct ADR-007 and BACKLOG.md BL-001's Strava Hostname Claim

**Issue:** #29
**Related:** #26 (closed, host fix), PR #28 (merged, `a2c0662`), #27 (closed as duplicate)

## Summary

`docs/trainiq/adr/ADR-007-no-hardcoded-endpoints.md` and `projects/trainiq/BACKLOG.md`
(BL-001) both state that `api.strava.com` is Strava's current, valid API
hostname, due to migrate to `api-v3.strava.com` on 2027-01-04. That premise is
false: `api.strava.com` has no DNS record at all (confirmed independently by
the BO and, separately, by the Architect on #26, via `dig`/`nslookup`/`host`/
`getent hosts` from unrestricted networks — not a sandbox-egress artifact).
Strava's real API host is `https://www.strava.com/api/v3` (confirmed
reachable: `curl` returns 401, i.e. auth-required, not DNS/connection
failure). The stale premise already cost real pipeline time once: on #26 the
Architect initially rejected the BO's correct DNS evidence and blocked on
`needs:human` because it contradicted ADR-007/BL-001, and had to be talked
through it before the fix could proceed. On #26, QA, Developer, and Architect
each separately declined to make this correction themselves, citing their
role's mandated work location as not covering `docs/trainiq/adr/*` or
`projects/trainiq/BACKLOG.md` — QA ultimately opened this issue to track it
rather than leave it silently unfixed.

## Scope

- Correct `docs/trainiq/adr/ADR-007-no-hardcoded-endpoints.md` so it no
  longer calls `api.strava.com` Strava's current or valid hostname. It
  should name `https://www.strava.com/api/v3` as the real base and cite #26
  as the evidence source (DNS lookup + `curl` 401, from both the BO's and
  the Architect's independent checks).
- Resolve the "`api.strava.com` → `api-v3.strava.com` on 2027-01-04" migration
  claim one way: keep it only if backed by a real, citable source (e.g. a
  Strava developer changelog/forum post), otherwise remove it rather than
  restate it unverified. ADR-007's actual architectural decision — no
  hardcoded remote endpoints, defined as configuration in one place — does
  not depend on this claim either way and is unaffected by the correction.
- Correct `projects/trainiq/BACKLOG.md` BL-001 to match the same facts. BL-001's
  own premise (review `stravalib` ahead of a January 2027 migration away
  from `api.strava.com`) may no longer describe a real, actionable item once
  the hostname claim is corrected — close it if so, or rewrite it to
  whatever actionable item (if any) survives the correction.
- Update any comment or docstring in `projects/trainiq/` that currently asserts
  `api.strava.com` as a presently-valid or current Strava hostname (as
  opposed to describing this pipeline sandbox's own egress restriction,
  which is a separate, accurate, unrelated statement — see "Out of scope").
  As of this writing, no such comment exists outside the two documents
  above; this item exists to guard against one being missed, not because a
  known instance has been found.

### Out of scope

- The "this sandbox cannot reach api.strava.com" (and equivalent
  Peloton/Eufy) comments in `strava.py`, `strava_unofficial.py`, and other
  connector files. Those describe this pipeline's own network-egress
  restriction, not a claim about which hostname is Strava's real one — the
  Architect on #26 already distinguished this and both of #26's requirements
  docs excluded it from scope for the same reason. It stays true regardless
  of this correction.
- Historical, point-in-time docs that recorded `api.strava.com` as part of a
  past design pass (`docs/trainiq/requirements/18-*`,
  `docs/trainiq/requirements/20-*`, `docs/trainiq/discovery/18-*`,
  `docs/trainiq/discovery/20-*`, `docs/trainiq/architecture/18-*`,
  `docs/trainiq/architecture/22-*`, `docs/trainiq/architecture/26-*`,
  `docs/trainiq/requirements/26-*`). These are historical records of what was
  believed/designed at the time, not standing claims about Strava's current
  API — rewriting them would misrepresent the pipeline's own history. Only
  ADR-007 and BACKLOG.md are standing, current-truth documents.
- Any change to `StravaUnofficialConnector` or `StravaConnector` logic,
  tests, or behavior. This issue is a documentation correction only; the
  connector's base URL was already fixed on #26 (PR #28, merged).
- Deciding whether `www.strava.com/api/v3/*` actually accepts
  `_strava4_session` cookie-only auth — that question is being tracked by
  #30, not here.

## Acceptance criteria

1. ADR-007 no longer states or implies `api.strava.com` is Strava's current
   or valid hostname. It names `https://www.strava.com/api/v3` as the real
   base and cites #26 as the evidence.
2. The `api-v3.strava.com` / 2027-01-04 migration claim in ADR-007 either
   carries a real, checkable source, or is removed. It is not left as an
   unverified assertion.
3. BACKLOG.md BL-001 is corrected to the same facts as ADR-007, or closed
   with a comment explaining why it no longer applies once corrected.
4. No comment or docstring in `projects/trainiq/` asserts `api.strava.com` as
   a presently-valid Strava hostname after this change (verified by search).
   Comments describing this pipeline's own sandbox-egress restriction are
   unaffected and are not required to change.
5. No change to connector code, tests, or runtime behavior — this issue is
   docs-only.
6. Historical design docs (listed under "Out of scope" above) are left
   unchanged.

## Open questions

**Neither blocks the Architect — both are investigation/judgment calls for
them, not BO decisions:**

1. **Does the `api-v3.strava.com` / January 2027 migration claim have a real
   source?** Nothing in this repo's history cites one — ADR-007 states it as
   fact with no link, and BACKLOG.md BL-001 repeats it without one either.
   Finding (or failing to find) a source is a technical investigation, not a
   requirement; AC2 above covers either outcome.
2. **Who should own ADR edits going forward?** On #26, the Architect,
   Developer, and QA each separately declined to touch
   `docs/trainiq/adr/*` / `BACKLOG.md`, citing their own role's mandated work
   location — which is why this correction is still outstanding instead of
   having been made directly on #26. The issue body suggests the Architect as
   the natural owner. This BA routine has no authority to change any role's
   mandated work location (that is pipeline configuration, not a
   requirements decision), so it is flagged here for the Architect/BO to
   settle, not resolved in this doc.

## Handoff

Commit this requirements doc, comment on the issue with the summary and a
link, remove `stage:ba`, add `stage:architect`.
