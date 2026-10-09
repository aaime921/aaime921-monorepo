# Architecture: Peloton Distance Unit Conversion (account unit, not hard-coded km)

**Issue:** #45
**Requirements:** [`docs/trainiq/requirements/45-peloton-distance-unit-conversion.md`](../requirements/45-peloton-distance-unit-conversion.md)
**Related:** #5 (original, now-wrong, km assumption), #36 (`renormalize_provider()` pattern this design reuses), #37 (dedup pairs used as the correctness check)

## Approach

Two independent pieces, same split as #36: a connector fix for future syncs, and a
one-off re-normalization of the rows already stored under the wrong assumption.

**1. Connector fix.** `download()` already calls `GET /api/me` once per sync (to get
`user_id` — `peloton.py:~230`); this design adds exactly one more read from that same
already-fetched response: the account's distance unit. No new network call.

The resolved unit is attached to **each raw workout dict** as a connector-internal key
(`_distance_unit`, underscore-prefixed so it's visually unmistakable as something this
connector added, not a field Peloton's API returned) before `download()` returns the
list. `normalize()` then reads `raw.get("_distance_unit")` — a single, already-resolved
token (`"mi"`, `"km"`, or `None`) — and never itself talks to `/api/me` or does any
alias/string parsing. This keeps `normalize()` a pure function of its one argument
(the Connector architecture invariant: Download fetches, Normalize maps), and it
naturally makes the resolved unit part of what gets written to `raw_activities`
(`sync/engine.py`'s `_upsert_raw_activity()` persists exactly the dict `download()`
returned, per workout) — **but only for workouts synced after this fix ships.**
Existing stored rows were fetched by the old `download()` and have no `_distance_unit`
key; the one-off correction (below) supplies it a different way.

**Why account-level, not per-workout:** the live-captured evidence (`docs/trainiq/
verification/peloton-2026-09-28.md`) shows one full real workout record — `id`,
`start_time`, `end_time`, `fitness_discipline`, `total_work`, `distance`, `calories`,
`effort_zones` — with no unit field on it. There is no evidence a per-workout unit
field exists. Designing against one anyway would repeat exactly the mistake this issue
is fixing (issue #5 assumed a unit from a different code path's behavior, not a
live-verified fact). The requirements doc's "verified per-record where possible" is
satisfied differently here: *correctness* is verified per-record, by the BO's
Peloton↔Strava pairs (AC1/AC5) — not the *unit source* itself, which this design
treats as account-level because that's what the evidence supports.

**Why the exact `/api/me` field name is a flagged, unverified placeholder — and what
closes that gap:** the requirements doc explicitly leaves the exact field to whoever
implements this, "since it requires inspecting what Peloton's real API responses
actually contain" (open questions section). This Architect routine has no live Peloton
access (sandboxed, no credentials, no network to `onepeloton.com` — same constraint
"Testing scope boundaries" in the role doc describes for Dev/QA). Per the project's
evidence-based principle ("if not \[live-verified\], flag as a verification gap... rather
than assume"), this design does **not** pretend a field name is confirmed. It:

- Names the constant (`ACCOUNT_DISTANCE_UNIT_FIELD`), gives it the issue's own
  suggested value (`"distance_unit"`) as a starting guess, and comments it exactly
  like this file's existing flagged-not-researched constants
  (`ASSUMED_SESSION_LIFETIME_S`, `OAUTH_CLIENT_ID`'s comment) — i.e., visibly marked
  as unconfirmed, not quietly treated as fact.
- Makes **Task 1** of the breakdown below a mandatory live-verification step (same
  pattern as the `debug_peloton_manual_bearer.py` diagnostics that resolved issue #5's
  own endpoint-shape unknowns), to be run by whoever has the BO's manual bearer token,
  **before** the rest of this design is implemented against a possibly-wrong field
  name. This is a verification task, not a design decision deferred — the rest of this
  doc (multiplier table, never-guess fallback, test shapes) does not change regardless
  of what that step finds; only the one constant and its alias table might.
- If that step finds no such field exists anywhere reachable without re-fetching every
  workout individually (not just `/api/me`), the fallback is: `_resolve_account_distance_
  unit()` returns `None`, every live-synced `distance_m` is `NULL` + logged (per AC3),
  and this becomes a new BACKLOG item, not a silent wrong guess. **Never fall back to
  assuming km (or mi) to avoid a `NULL`** — that is the exact mistake #45 exists to fix.

**2. One-off correction of already-stored rows.** Cannot use `renormalize_provider()`
(issue #36) completely unchanged: it calls `connector.normalize(raw)` directly against
whatever is already in `raw_activities.payload_json`, and existing Peloton rows have no
`_distance_unit` key to read (see above — that key only exists on workouts fetched by
the *new* `download()`). The unit for this account is, however, already known — the BO
independently confirmed it as `mi` via the issue's own Peloton↔Strava comparison. So
the one-off path supplies that confirmed value explicitly, rather than trying to infer
it from data that was never captured with it:

- `renormalize_provider()` (`trainiq/normalization/renormalize.py`) gains one new,
  optional, backward-compatible parameter: `raw_transform: Callable[[dict], dict] |
  None = None`. When given, it's applied to each row's parsed raw dict — `raw =
  raw_transform(raw) if raw_transform else raw` — immediately after `json.loads(...)`
  and before `connector.normalize(raw)`. Default `None` means zero behavior change for
  the existing `scripts/renormalize_strava_unofficial.py` call site (issue #36)
  — confirmed by reading its one call, which passes no such argument.
  Operates purely in-memory on the dict passed to `normalize()`; `raw_activities` is
  still never written to, matching this module's existing "never touches raw_activities
  — only reads it" contract.
- A new script, `scripts/renormalize_peloton_distance.py`, follows
  `renormalize_strava_unofficial.py`'s exact shape (`open_db()` / `CredentialStore` /
  argparse `--db-path`), with one required additional argument: `--unit {mi,km}` (no
  default — an operator must state the confirmed unit; this is the project's
  never-guess standard applied to script ergonomics, not just code). It calls:
  ```python
  renormalize_provider(
      conn, PROVIDER, connector,
      raw_transform=lambda raw: {**raw, "_distance_unit": args.unit},
  )
  ```
  i.e., it injects the operator-confirmed unit into a *copy* of each raw dict, the same
  key `normalize()` already knows how to read for live syncs — one code path, two ways
  of populating the key it depends on.
- `PelotonConnector.normalize()` makes no network call and touches no credentials
  (confirmed by reading the method: it only reads from its `raw` argument and calls
  `diagnostic_logger()`), so constructing the connector for this script is safe with no
  live session, same justification `renormalize_strava_unofficial.py` already gives for
  its own connector.
- Run once against the BO's real database with `--unit mi` (the account's confirmed
  unit from this issue's evidence); AC5 (Peloton↔Strava pairs agree within 1%) is the
  BO's own verification of the result, same as the requirements doc specifies.

## Affected components/files

- `trainiq/connectors/peloton.py` — `download()` (resolve + attach `_distance_unit`
  per workout), `normalize()` (read it, convert, never-guess fallback), new module-
  level constants/helpers (below). `DISTANCE_KM_TO_M_MULTIPLIER` is removed — it's the
  bug, not a value anything else still needs (confirmed: `trainiq/csv_import/
  peloton_csv.py` does its own inline `* 1000` on a CSV column and never imports this
  constant — grep-confirmed, zero cross-module dependents).
- `trainiq/normalization/renormalize.py` — add the optional `raw_transform` parameter
  to `renormalize_provider()`. No change to its existing behavior when omitted.
- `scripts/renormalize_peloton_distance.py` — new, modeled on
  `scripts/renormalize_strava_unofficial.py`.
- `tests/test_peloton_connector.py` — new tests for `normalize()`'s unit handling and
  `download()`'s per-workout attachment (see Test strategy notes).
- `tests/test_renormalize.py` — new test(s) for the `raw_transform` parameter.
- `docs/trainiq/verification/peloton-2026-09-28.md` — dated addendum recording the
  live-verified (or live-refuted) `/api/me` unit field finding from Task 1 below.
- `projects/trainiq/BACKLOG.md` — new `BL-010` entry (see Risks/tradeoffs) recording
  this finding for future reference, same convention as BL-008's "closed as verified."
- Not touched: `trainiq/csv_import/peloton_csv.py` (explicitly out of scope, confirmed
  self-contained above), `authenticate()`/OAuth/manual-recovery paths, resume-cursor
  handling, discipline taxonomy.

## Interfaces/contracts

```python
# trainiq/connectors/peloton.py

# UNCONFIRMED — issue #45's own suggestion, not yet live-verified against a real
# /api/me response body beyond its `id` field (docs/trainiq/verification/
# peloton-2026-09-28.md only captured `id`). Task 1 (below) must confirm or correct
# this before the rest of this fix is implemented against it.
ACCOUNT_DISTANCE_UNIT_FIELD = "distance_unit"

# Recognized spellings/synonyms for the two units this project supports today.
# Extend this table (not the lookup logic) if Task 1 finds Peloton reports a
# different token set (e.g. "imperial"/"metric" instead of "mi"/"km").
_DISTANCE_UNIT_ALIASES: dict[str, str] = {
    "mi": "mi", "mile": "mi", "miles": "mi",
    "km": "km", "kilometer": "km", "kilometers": "km",
    "kilometre": "km", "kilometres": "km",
}

_DISTANCE_UNIT_MULTIPLIERS: dict[str, float] = {
    "mi": 1609.344,
    "km": 1000.0,
}


def _resolve_account_distance_unit(me: dict[str, Any]) -> str | None:
    """Reads ACCOUNT_DISTANCE_UNIT_FIELD off a /api/me response body and maps it
    through _DISTANCE_UNIT_ALIASES to a canonical "mi"/"km" token. Returns None
    for a missing field or an unrecognized value — never guesses, never raises.
    Pure function of its argument; no logging here (normalize() owns logging,
    see below, so a missing/unknown unit is reported exactly once per affected
    workout, not once per sync run plus once per workout)."""


class PelotonConnector(Connector):
    def download(self, since: str | None = None) -> list[dict[str, Any]]:
        """Unchanged pagination/user_id logic. New: after fetching `me`, calls
        _resolve_account_distance_unit(me) once, then sets
        raw_workout["_distance_unit"] = <that result> on every workout dict
        before appending it to the returned list — including when the result
        is None. `_distance_unit` is therefore always present as a key (never
        omitted) on every dict this method returns, so normalize() can use
        .get() without needing to distinguish "key absent" from "key present
        but None."""

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Unchanged except for `distance_m`'s derivation:

            raw_distance = raw.get("distance")
            unit_token = raw.get("_distance_unit")
            multiplier = _DISTANCE_UNIT_MULTIPLIERS.get(unit_token)
            if raw_distance is not None and multiplier is None:
                diagnostic_logger().warning(...)  # AC3: logged, never guessed
                distance_m = None
            elif raw_distance is not None:
                distance_m = raw_distance * multiplier
            else:
                distance_m = None  # no distance on this workout at all — not a
                                    # unit problem, nothing to warn about

        Only warns when a real distance value exists but its unit doesn't — a
        meditation/strength session with no `distance` field at all is not an
        error condition and must not be logged as one.
        """
```

```python
# trainiq/normalization/renormalize.py

def renormalize_provider(
    conn: sqlite3.Connection,
    provider: str,
    connector: Connector,
    athlete_profile: Optional[AthleteProfile] = None,
    raw_transform: Optional[Callable[[dict], dict]] = None,  # NEW
) -> RenormalizeResult:
    """Unchanged except: when raw_transform is given, each row's parsed raw
    dict is passed through it (raw = raw_transform(raw)) before
    connector.normalize(raw) — in-memory only, never written back to
    raw_activities. Omitted/None preserves today's exact behavior."""
```

```python
# scripts/renormalize_peloton_distance.py — new, modeled on
# scripts/renormalize_strava_unofficial.py

parser.add_argument(
    "--unit", required=True, choices=["mi", "km"],
    help="Confirmed Peloton account distance unit for the rows being corrected "
         "(e.g. 'mi' for this BO's account, per issue #45's evidence). Required — "
         "this script never guesses a unit.",
)
...
result = renormalize_provider(
    conn, PROVIDER, connector,
    raw_transform=lambda raw: {**raw, "_distance_unit": args.unit},
)
```

## Task breakdown

1. **Verification (blocking, do first):** using the BO's manually-supplied bearer
   token, run a temporary one-off diagnostic (same throwaway pattern as
   `scripts/debug_peloton_manual_bearer.py` — isolated, no production code imported,
   deleted after use) against `GET /api/me` for the real account. Capture the **full**
   response body (not just `id`, which is all the prior verification pass recorded).
   Confirm: (a) does a distance-unit field exist at all; (b) its real key name; (c) its
   real value format (e.g. `"mi"` vs `"miles"` vs `"imperial"` vs something else
   entirely). Update `ACCOUNT_DISTANCE_UNIT_FIELD` and `_DISTANCE_UNIT_ALIASES` to
   match. Record the finding as a dated addendum in `docs/trainiq/verification/
   peloton-2026-09-28.md`, same correction-note convention that file already uses. If
   no such field exists anywhere on `/api/me`, record that as the finding instead —
   it doesn't block the rest of this fix, it just means `_resolve_account_distance_
   unit()` always returns `None` for now (see Approach's fallback) and the BACKLOG
   item below stays open rather than closed.
2. Add `ACCOUNT_DISTANCE_UNIT_FIELD`, `_DISTANCE_UNIT_ALIASES`,
   `_DISTANCE_UNIT_MULTIPLIERS`, and `_resolve_account_distance_unit()` to
   `trainiq/connectors/peloton.py`; remove `DISTANCE_KM_TO_M_MULTIPLIER`.
3. Update `download()` to resolve the unit once per call (right after fetching `me`)
   and attach `_distance_unit` to every workout dict before it's appended to the
   returned list.
4. Update `normalize()`'s `distance_m` derivation per the Interfaces section above.
5. Add the `raw_transform` parameter to `renormalize_provider()`.
6. Write `scripts/renormalize_peloton_distance.py`.
7. Tests (see below).
8. Add the dated verification addendum (step 1) and a new `BL-010` entry to
   `projects/trainiq/BACKLOG.md` recording the live-verified (or live-refuted) finding,
   independent of whatever Task 1 found — this is the documentation AC8 requires.
9. Handoff. The BO then runs `scripts/renormalize_peloton_distance.py --unit mi`
   against the real database and checks AC5 (Peloton↔Strava pairs within 1%) — that
   step is explicitly BO-run per the requirements doc, not part of this pipeline.

## Test strategy notes

All in fixtures/mocks — no live Peloton access from CI (Testing scope boundaries).

`tests/test_peloton_connector.py`, new cases for `normalize()` (AC7 — the three
required accounts, tested directly against raw dicts, independent of `download()`):
- `_distance_unit="mi"`: `distance = 13.1816` → `distance_m` within float tolerance of
  `13.1816 * 1609.344` (≈ 21,213.9 — matches the issue's own Strava-confirmed figure of
  21213.7 to within 1%, which is the acceptance bar AC1 actually asks for).
- `_distance_unit="km"`: `distance = 21.2137` → `distance_m == 21213.7` (no regression,
  AC2).
- `_distance_unit` missing (key absent) and `_distance_unit=None` (key present, value
  None) and `_distance_unit="furlongs"` (present, unrecognized): all three →
  `distance_m is None` and a warning logged (AC3/AC7) — caplog or a monkeypatched
  logger, matching how the existing unrecognized-`fitness_discipline` test (if any)
  asserts a warning today.
- A record with no `distance` key at all (e.g. a meditation session) and an
  unresolved unit: `distance_m is None` and **no** warning logged — proves the
  "don't warn when there's nothing to convert" branch.

New case for `download()`: a fake `/api/me` response carrying a recognized unit value
→ assert every returned workout dict has `_distance_unit` set to the resolved token;
a response with the field missing → every workout dict still has the key, set to
`None` (never omitted).

`tests/test_renormalize.py`, new case: `raw_transform` supplied → each row's `raw`
dict passed to a fake `connector.normalize()` reflects the transform's changes;
`raw_transform=None` (or omitted) → byte-identical behavior to today, proving the
strava_unofficial call site is unaffected.

`scripts/renormalize_peloton_distance.py`: a focused test (or reuse of
`test_renormalize.py`'s coverage via a thin integration test) that `--unit` is
required and rejects any value outside `{mi, km}` — argparse's `choices` should
already enforce this; one test confirms it rather than assuming argparse's behavior.

Manual/BO-run verification (not CI): AC1, AC5 — the Peloton↔Strava pairs from #37,
run against the real database after the one-off script executes.

## Risks/tradeoffs

- **The core risk this whole design is built around:** the exact `/api/me` field name
  and value format are unconfirmed. This is called out explicitly (Approach, Task 1)
  rather than quietly coded against a guess. The mitigation is structural: every piece
  of this design downstream of `_resolve_account_distance_unit()` is unaffected by
  what Task 1 finds — only that one function's internals (the constant + alias table)
  need to change, not `normalize()`, not the renormalize plumbing, not the tests'
  shape (only their literal input values).
- **If `/api/me` turns out not to carry any usable unit field at all:** every future
  live sync logs + stores `NULL` for `distance_m` (never a silent wrong guess, per
  AC3) until a different signal is found. That's a real degradation vs. "always
  wrong but non-NULL," but it is the behavior this issue's never-fabricate principle
  explicitly requires over guessing again. Tracked as `BL-010` either way (open if
  unresolved, closed-as-verified if Task 1 finds a working field — same convention as
  BL-008).
- **The one-off script trusts an operator-supplied `--unit`, not a re-verified one.**
  This is a deliberate, narrow exception to "never guess": the value isn't guessed, it's
  the same fact the BO already independently proved via six real Peloton↔Strava pairs
  (this issue's own evidence) — the script just needs a place to receive that already-
  established fact, since it cannot re-derive it from data that predates this fix. A
  wrong `--unit` value would be an operator error, not a connector bug; AC5's
  BO-run pairwise check is exactly the safeguard that catches that before/after
  trusting the result.
- **Unit could change mid-account-lifetime** (BO changes their Peloton display
  preference). `download()` re-resolves the unit fresh every sync call, so newly
  synced workouts after such a change are correct automatically. Workouts already
  stored under the old unit would need another run of the one-off script (with the
  new `--unit`) if this ever happens — not handled automatically, and not in scope
  for this issue (no evidence this has happened or is expected to).
- **`renormalize_provider()`'s new parameter is additive and defaulted** — verified
  by inspection that the one existing call site (`renormalize_strava_unofficial.py`)
  passes no such argument, so this is not a silent behavior change for issue #36's
  already-shipped fix.
