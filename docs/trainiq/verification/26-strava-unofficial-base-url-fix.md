
# Verification: Fix `StravaUnofficialConnector`'s unreachable `DEFAULT_BASE_URL`

Issue: #26
PR: [#28](https://github.com/aaime921/aaime921-monorepo/pull/28) (`claude/exciting-knuth-gvr12h` → `main`)
Requirements: [`docs/trainiq/requirements/26-strava-unofficial-base-url-fix.md`](../requirements/26-strava-unofficial-base-url-fix.md), [`docs/trainiq/requirements/26-strava-unofficial-base-url-unreachable.md`](../requirements/26-strava-unofficial-base-url-unreachable.md)
Architecture: [`docs/trainiq/architecture/26-strava-unofficial-base-url-fix.md`](../architecture/26-strava-unofficial-base-url-fix.md)

## Test suite

Checked out PR branch `claude/exciting-knuth-gvr12h` (sha `4e86e89`) and ran
from `projects/trainiq/`:

```
pytest tests/ -q
```

Result: 362 passed, 2 failed. The 2 failures are in
`tests/test_peloton_csv_import.py` (`FileNotFoundError` on a hardcoded path
to a real CSV not present in this sandbox) — confirmed unrelated: `git diff
origin/main...claude/exciting-knuth-gvr12h --stat` shows this PR touches only
`trainiq/connectors/strava_unofficial.py` and
`tests/test_strava_unofficial_connector.py`, and these 2 tests fail
identically on `main` per the Developer's own handoff comment (not
independently re-run against `main` in this pass, but the diff scope makes
causation impossible).

Scoped re-run, all passing:
```
pytest tests/test_strava_unofficial_connector.py tests/test_app.py tests/test_setup_wizard.py -q
```
→ 65 passed (the three files the requirements docs name for regression).

## Independent AC verification

Beyond re-running the Developer's own tests: read the diff line-by-line,
re-read the changed file's constructor/`_authenticated_get()` to confirm the
new tests exercise the real `f"{self._base_url}{path}"` code path (not a
tautological assertion against a hardcoded string), and independently grepped
the codebase for stray references to the broken host.

| AC (source) | Description | Check performed | Result |
|---|---|---|---|
| unreachable.md #1 | Root cause (`DEFAULT_BASE_URL = "https://api.strava.com"`, line 51) accepted as given | Confirmed via `git diff` against `main`: old value was exactly `https://api.strava.com` | ✅ Pass |
| unreachable.md #2 | Endpoint corrected and **live-verified** to accept `_strava4_session` cookie auth on both endpoints | `DEFAULT_BASE_URL` → `https://www.strava.com`, confirmed resolvable (BO's own `nslookup`/`dig`/`curl` from an unrestricted network, issue comment 2026-10-05: `curl` got HTTP 401, i.e. host reachable). **Cookie-auth success itself is not yet live-verified** — no real `_strava4_session` request has succeeded against `/api/v3/athlete` yet, and no `docs/trainiq/verification/strava-unofficial-<date>.md` capture exists. Both requirements docs and the architecture doc explicitly designed this as a separate, BO-driven step that does **not** block this fix (no live egress or real cookie available to this pipeline) | ⚠️ Partially pass — host-reachability confirmed; cookie-auth success still pending a BO-driven live capture (pre-flagged by BA/Architect/Developer, not a new gap) |
| unreachable.md #3 / fix.md #6 | No behavior change beyond connectivity (auth, normalize, pagination, error handling unchanged) | Diff is exactly 2 files: the one-line constant change + 2 new tests. No other function touched | ✅ Pass |
| unreachable.md #4 / fix.md #4 | Tests reflect the corrected endpoint; no test left asserting the old broken URL | Grepped `tests/test_strava_unofficial_connector.py` for `api.strava.com` — zero hits outside a docstring explaining the sandbox-egress comment (descriptive, not an assertion) | ✅ Pass |
| unreachable.md #5 | No regression: `test_strava_unofficial_connector.py`, `test_app.py`, `test_setup_wizard.py` pass unmodified | Ran all three — 65/65 pass | ✅ Pass |
| unreachable.md #6 | Code citation (line 51) re-verified against current `main` | Read the file on the PR branch: `DEFAULT_BASE_URL` is still at line 51 | ✅ Pass |
| fix.md #1 | `DEFAULT_BASE_URL` is a hostname that resolves, combined with existing paths forms the same endpoints | `https://www.strava.com` + `/api/v3/athlete`, `/api/v3/athlete/activities` — paths unchanged in diff; host independently confirmed resolvable (BO's live DNS/curl evidence) | ✅ Pass |
| fix.md #2 | No other request-constructing code path still points at `api.strava.com` | Grepped `projects/trainiq/` for `api.strava.com`: hits in `strava_unofficial.py` (one descriptive comment, "this sandbox cannot reach..."), `strava.py` (descriptive docstring/comment), `test_strava_connector.py` (descriptive docstring), `BACKLOG.md` (BL-001 text). None are in a request-constructing path — all are comments/docs, matching both requirements docs' explicit carve-out | ✅ Pass |
| fix.md #3 | `submit_manual_recovery()` and `download()` both build from the single corrected constant | Read `_authenticated_get()`: both call sites route through it, which uses `f"{self._base_url}{path}"` — one place, confirmed by code reading, not just trusting the test names | ✅ Pass |
| fix.md #5 | New/updated test confirms outgoing request URL uses corrected host | `test_submit_manual_recovery_validates_against_corrected_base_url` and `test_download_requests_against_corrected_base_url` both assert `fake.get_calls[0]["url"]` against the full corrected URL, exercising the real connector method (not reading the constant directly) | ✅ Pass |

## Verdict

All acceptance criteria that this pipeline can mechanically verify pass. One
item — live cookie-auth success against the corrected host — remains open,
exactly as the requirements docs and architecture doc designed it: a
BO-driven step needing real network egress and a real `_strava4_session`
cookie, neither available here. This was flagged consistently through BA →
Architect → Developer and is not a new finding. PR #28 is approved and ready
to merge (merge itself is reserved for the BO).

**Recommended before closing out fully:** the BO performs the live-cookie
check and records it as
`docs/trainiq/verification/strava-unofficial-<date>.md` (Eufy/Peloton
convention) — this repo's own `docs/trainiq/verification/PROTOCOL.md`,
section 8, already has a Strava item for exactly this.

## Note on an unresolved BO request (flagged, not actioned by QA)

The BO's 2026-10-05 issue comment explicitly asked: *"ADR-007 and
BACKLOG.md BL-001 are wrong about `api.strava.com` being the current
hostname. Correct them as part of this issue."* Neither the Architect (doc:
"this routine's mandated work location for this issue is
`docs/trainiq/architecture/* only`") nor the Developer (handoff comment:
"outside this issue's work location") made that correction, each deferring
to "a human or a routine with write access to those paths." This QA routine's
own mandated work location is `docs/trainiq/verification/*` only, so it is
not corrected here either. Flagging this explicitly so it does not silently
drop once this issue reaches `stage:done`: `docs/trainiq/adr/ADR-007-no-hardcoded-endpoints.md`
and `projects/trainiq/BACKLOG.md` (BL-001) still state `api.strava.com` is
Strava's current, valid hostname — which this issue's own evidence (BO's
live DNS checks, zero DNS records) directly contradicts.
