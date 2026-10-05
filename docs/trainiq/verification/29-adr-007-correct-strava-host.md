# Verification: Correct ADR-007 and BACKLOG.md BL-001's Strava Hostname Claim

**Issue:** #29
**PR:** #32 (`claude/exciting-knuth-e6qka3` → `main`)
**Requirements:** [`docs/trainiq/requirements/29-adr-007-correct-strava-host.md`](../requirements/29-adr-007-correct-strava-host.md)
**Architecture:** [`docs/trainiq/architecture/29-adr-007-correct-strava-host.md`](../architecture/29-adr-007-correct-strava-host.md)

## Method

Checked out PR branch `claude/exciting-knuth-e6qka3` at `c05127e`. Diffed it
against `origin/main` (`0b65e5b`) to confirm exact scope, read both corrected
files in full, re-ran the verification grep, and ran the full `trainiq` test
suite (docs-only change, so this is a regression check, not an AC check).

## Scope check

`git diff --stat origin/main origin/claude/exciting-knuth-e6qka3`:

```
docs/trainiq/adr/ADR-007-no-hardcoded-endpoints.md |  4 +++-
projects/trainiq/BACKLOG.md                        | 14 ++++++++------
2 files changed, 11 insertions(+), 7 deletions(-)
```

Exactly the two files the requirements/architecture docs scope this issue
to. No connector code, test, or other doc touched.

## Acceptance criteria

| AC | Result | Evidence |
|----|--------|----------|
| 1. ADR-007 no longer calls `api.strava.com` current/valid; names `https://www.strava.com/api/v3` as base, cites #26 | **PASS** | `Origin` line and new `## Context` paragraph both name `https://www.strava.com/api/v3` as the real, reachable host and explicitly state `api.strava.com` "has no DNS record at all and has never been a reachable Strava host," citing #26's `dig`/`getent hosts` findings. |
| 2. Migration claim either sourced or removed | **PASS** | `Origin` line cites "Strava developer changelog / community-hub 'An update to our developer program'" with dates (2027-01-04 available, 2027-06-01 final cutover). Not left unverified. (Source is web-search-derived per the Architect's doc, not a direct fetch — flagged there as a residual caveat for the BO to spot-check; this doesn't block AC2, which only requires a real, checkable source to be cited.) |
| 3. BACKLOG.md BL-001 corrected to match | **PASS** | BL-001 rewritten: "Corrected (2026-10, #29)" note added, `www.strava.com/api/v3` named as the real current host migrating to `api-v3.strava.com` on the same dates, `stravalib`'s hardcoded `ApiV3.server = "www.strava.com"` cited to corroborate. Underlying action item (watch `stravalib` pre-migration) preserved, not dropped. |
| 4. No comment/docstring in `projects/trainiq/` asserts `api.strava.com` as presently valid | **PASS** | `grep -rn "api.strava.com" --include=*.py projects/trainiq/` on the PR branch returns only the four pre-existing sandbox-egress comments (`strava.py:51`, `strava.py:105`, `strava_unofficial.py:122`, `strava_unofficial.py:365`, plus `test_strava_connector.py:5` referencing the same docstring) — all describing this sandbox's own network restriction, not a claim about Strava's real host. Identical to the Architect's predicted result; zero code changes were needed. |
| 5. Docs-only, no connector code/test/behavior change | **PASS** | Confirmed by the scope-check diff above: only `ADR-007-no-hardcoded-endpoints.md` and `BACKLOG.md` changed. |
| 6. Historical design docs left unchanged | **PASS** | Same scope-check diff — `docs/trainiq/requirements/18-*`, `20-*`, `26-*`, `docs/trainiq/discovery/18-*`, `20-*`, `docs/trainiq/architecture/18-*`, `22-*`, `26-*` do not appear in the diff at all. |

**All 6 acceptance criteria pass.**

## Regression check (full test suite)

```
cd projects/trainiq && pytest tests/ -q
```

Result on PR branch (`c05127e`): **2 failed, 371 passed**.

Both failures (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
`::test_real_csv_full_regression`) are `FileNotFoundError` on
`/home/claude/peloton_work/aimea75_workouts.csv` — a live-account CSV fixture
not present in this sandbox. Confirmed **pre-existing and unrelated**: the
identical two failures occur on `origin/main` (`0b65e5b`) before this PR's
changes are applied (`pytest tests/test_peloton_csv_import.py -q` → same
`2 failed, 8 passed`). This PR touches no code or tests, consistent with
AC5, so no new failures are expected or found.

## Verdict

✅ All 6 acceptance criteria verified pass. No regressions (pre-existing,
unrelated test failures only). PR #32 approved.
