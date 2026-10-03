# Architecture: Wire StravaUnofficialConnector into app.py and setup_wizard.py

**Issue:** #22
**Requirements:** `docs/trainiq/requirements/22-wire-strava-unofficial-connector.md`
**Depends on:** #18 — `StravaUnofficialConnector` (closed, complete, not modified here)

---

## Approach

This is pure composition-root wiring, not new connector logic. Both target
functions (`_build_configured_connectors()` in `app.py`,
`run_first_time_setup()` in `setup_wizard.py`) already have an established,
repeated shape for adding a provider — Strava/Peloton/Eufy in `app.py`,
`_setup_strava()` / `_setup_email_password_provider()` in `setup_wizard.py`.
The design is simply: add one more branch/step following that exact shape,
using `StravaUnofficialConnector`'s own existing public surface
(`PROVIDER`, `CRED_STRAVA_SESSION_COOKIE`, `request_manual_recovery()`,
`submit_manual_recovery()`) as the contract. No new abstraction is
introduced anywhere — a fourth case of an existing pattern does not justify
generalizing the pattern into a table-driven loop; `app.py`'s four branches
and `setup_wizard.py`'s four steps already read as a deliberate, explicit
sequence (RC1's own stated style), and a fifth explicit case is more
readable and more easily reviewed/tested in isolation than a parametrized
version would be.

The one real design decision — called out as an open question in the
requirements doc — is resolved here: **the unofficial-Strava step is
offered unconditionally**, immediately after `_setup_strava()` and before
`_setup_peloton()`, regardless of whether the official step was accepted,
declined, or failed. This matches both the issue body ("optional, offered
after/alongside official Strava OAuth") and #18's own stated design
("both connectors can coexist") — there is no dependency between the two
steps' outcomes, so no conditional branching is needed in
`run_first_time_setup()` beyond adding one more call in sequence.

## Affected components/files

- **`trainiq/app.py`** — `_build_configured_connectors()`: one new
  try/except branch. New imports: `StravaUnofficialConnector`,
  `PROVIDER as STRAVA_UNOFFICIAL_PROVIDER`,
  `CRED_STRAVA_SESSION_COOKIE as STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE`
  from `trainiq.connectors.strava_unofficial`.
- **`trainiq/setup_wizard.py`** — one new function
  `_setup_strava_unofficial()`, plus one new call in
  `run_first_time_setup()`. New imports from
  `trainiq.connectors.strava_unofficial`: `PROVIDER`,
  `CRED_STRAVA_SESSION_COOKIE`, `StravaUnofficialConnector`.
- **`tests/test_app.py`** — new tests for the new branch (configured,
  skipped, construction-failure, coexistence-with-official-Strava).
- **`tests/test_setup_wizard.py`** — new tests for the new step (accept,
  decline, reject, exception, cancellation).
- **Nothing else.** `strava_unofficial.py`, `strava.py`, `peloton.py`,
  `eufy.py`, `sync/engine.py`, `credentials/store.py` are all unchanged —
  this connector's contract already supports everything this issue needs.

## Interfaces/contracts

### `app.py` — new branch in `_build_configured_connectors()`

Inserted after the existing Strava branch (official), before Peloton —
matching the requirements doc's "additive" framing and keeping the two
Strava branches visually adjacent for anyone reading the function
top-to-bottom:

```python
# --- Strava (unofficial, session-cookie) ---
try:
    if credential_store.get(STRAVA_UNOFFICIAL_PROVIDER, STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE):
        connectors.append(StravaUnofficialConnector(credential_store))
        log.info("Strava (unofficial): configured")
    else:
        log.info("Strava (unofficial): skipped (not connected)")
except Exception as exc:  # noqa: BLE001 - composition-time isolation is the point
    log.warning(f"Strava (unofficial): skipped (construction failed: {exc})")
```

This is a structural copy of the existing Strava branch: same presence
check shape (one stored credential as the configured/not-configured
signal), same try/except/log-and-continue Graceful Degradation pattern, no
new helper function needed. `StravaUnofficialConnector(credential_store)`
is the connector's full constructor signature for this use — `session` and
`base_url` are test/override-only parameters that `app.py` never needs to
pass, exactly like `StravaConnector(credential_store)` today.

No change to the function's return type, its other branches, its
docstring's Graceful Degradation claim, or its signature.

### `setup_wizard.py` — new step function

```python
def _setup_strava_unofficial(credential_store: CredentialStore, connector=None) -> bool:
    """Returns True if the unofficial (session-cookie) Strava connector
    ended up connected. Independent of _setup_strava() — this step is
    offered unconditionally, not gated on the official step's outcome, per
    #18's 'both connectors can coexist' design and the Architect's
    resolution of the requirements doc's open question. Unlike Peloton/
    Eufy, there is no separate 'store then roll back' case: the
    connector's own submit_manual_recovery() validates via a live request
    BEFORE persisting anything and is also the only write path — same
    relationship _setup_strava() already has with exchange_code_for_token().
    """
    print("\n--- Strava (unofficial, session cookie) ---")
    if not _prompt_yes_no("Connect Strava via session cookie now?"):
        print("Strava (unofficial): skipped")
        return False

    conn = connector if connector is not None else StravaUnofficialConnector(credential_store)
    print(conn.request_manual_recovery())
    cookie_value = _prompt_text("Paste the _strava4_session cookie value here")

    try:
        ok = conn.submit_manual_recovery(cookie_value)
    except Exception as exc:  # noqa: BLE001 — deliberate: roll back on ANYTHING unexpected, matching Peloton/Eufy's rule
        print(f"Strava (unofficial): connection failed unexpectedly ({type(exc).__name__}) — nothing saved")
        return False

    if not ok:
        print("Strava (unofficial): cookie rejected. Nothing saved.")
        return False

    print("Strava (unofficial): connected")
    return True
```

Design notes on this shape, to pre-empt Developer questions:

- **No manual `credential_store.set()`/`delete()` calls in this function,
  unlike `_setup_peloton()`/`_setup_eufy()`.** `submit_manual_recovery()`
  already does its own validate-then-persist (via `self._credentials
  .rotate(...)`, three keys) and never persists on a rejected cookie
  (`StravaUnofficialHTTPError`/`AuthenticationError` caught internally,
  returns `False` before any `rotate()` call — see `strava_unofficial.py`).
  There is therefore nothing for the wizard step to roll back on an
  expected rejection: "nothing saved" is already true by construction
  before this function is called at all. This matches AC8 exactly, and
  matches `_setup_strava()`'s existing comment making the same observation
  about OAuth. Do not add defensive `credential_store.delete(...)` calls
  here — there is nothing to delete, and doing so would misrepresent the
  actual write path to a future reader.
- **The one case this function *does* need to guard is AC9: an
  *unexpected* exception during `submit_manual_recovery()`** (e.g. the
  underlying HTTP call raising `TransientError`, or any exception type
  `strava_unofficial.py` doesn't itself catch). `submit_manual_recovery()`
  only catches `AuthenticationError`/`StravaUnofficialHTTPError`
  internally and returns `False` for those; anything else propagates. The
  wizard's `except Exception` catches that remaining case. Because
  `submit_manual_recovery()` validates *before* persisting regardless of
  which exception path is taken, "nothing saved" still holds here too —
  the `except` branch's job is solely to produce the correct user-facing
  message and return `False`, not to undo a write that never happened.
- **`conn.request_manual_recovery()` is called, not re-authored.** The
  requirements doc is explicit that the wizard must present the
  connector's own instruction string rather than writing new prompt copy —
  this keeps the DevTools instructions in exactly one place
  (`strava_unofficial.py`) so they can't drift out of sync between the two
  modules.
- **`connector` parameter mirrors `_setup_peloton(..., session=None)` /
  `_setup_eufy(..., session=None)`:** test injection point, defaults to
  constructing the real connector. Passing a fake connector directly
  (rather than a fake HTTP `session` like the other two steps) is the
  natural seam here, since `StravaUnofficialConnector` is a thin wrapper
  and the thing under test is really "did the wizard call
  `request_manual_recovery()`/`submit_manual_recovery()` correctly and
  react to their return value/exceptions correctly" — not any HTTP detail.

### `setup_wizard.py` — call site

```python
def run_first_time_setup(credential_store: CredentialStore, config_path: Path) -> None:
    print("No providers are configured yet. Let's connect at least one.\n")
    try:
        _setup_strava(credential_store)
        _setup_strava_unofficial(credential_store)
        _setup_peloton(credential_store)
        _setup_eufy(credential_store, config_path)
    except SetupCancelled:
        print("\nSetup cancelled.")
        return
    print("\nSetup complete.")
```

Only change: one new line, unconditional, between the existing
`_setup_strava(...)` and `_setup_peloton(...)` calls. `SetupCancelled`
propagates through this call exactly like every other step — no new
cancellation handling needed; it's already caught once at the top of
`run_first_time_setup()`.

## Data flow

1. **App launch (`app.py:main()`):** `_build_configured_connectors()` is
   called before and after `run_first_time_setup()`, as today. The new
   branch participates in both calls identically to the other three —
   first call likely finds nothing configured (fresh install), second call
   (post-setup) finds whatever the user just connected.
2. **Setup wizard (`setup_wizard.py:run_first_time_setup()`):**
   `_setup_strava_unofficial()` runs as one step in the fixed sequence.
   On accept + valid cookie, `submit_manual_recovery()` writes
   `session_cookie` / `session_obtained_at` / `session_expires_at` under
   provider `strava_unofficial` via `CredentialStore.rotate()` — the same
   Keychain write path every other connector's credentials use, with the
   same non-secret `credentials_metadata` bookkeeping side effect
   (`_mark_connected`) that `CredentialStore.set()`/`rotate()` already
   perform unconditionally.
3. **Next sync cycle:** the connector is picked up by
   `_build_configured_connectors()` → `SynchronizationEngine.run_once()`
   exactly like any other connector; no change to the Sync Engine or its
   checkpointing is in scope or needed.

## Error handling

| Scenario | Where | Behavior |
|---|---|---|
| No session cookie stored | `app.py` branch | Skipped, logged "not connected" — not an error. |
| `StravaUnofficialConnector(...)` construction raises | `app.py` branch | Caught, logged as skip, other connectors unaffected (Graceful Degradation, ADR-009). In practice this connector's `__init__` has no failure mode today (it never raises), but the `try/except` is kept for structural consistency with the other three branches and as a forward-compatible guard, matching the existing branches' own reasoning. |
| User declines the wizard step | `setup_wizard.py` step | Returns `False`, nothing stored, proceeds to next step. |
| `submit_manual_recovery()` returns `False` (cookie rejected via live `/api/v3/athlete` check) | `setup_wizard.py` step | Nothing stored (already true — see "Interfaces/contracts" above), "cookie rejected" message, returns `False`. |
| `submit_manual_recovery()` raises an unanticipated exception (e.g. `TransientError` from a 429/5xx during validation) | `setup_wizard.py` step | Caught by the step's `except Exception`, nothing stored, "failed unexpectedly" message, returns `False`. Note: a transient failure during setup-time validation is reported as a failure to the user in this step (same as Peloton/Eufy's existing behavior for any exception) — there is no retry-during-setup; the user can simply re-run the step by re-launching setup. |
| `Ctrl+C`/EOF during this step (`_prompt_yes_no`/`_prompt_text`) | `setup_wizard.py` step → `run_first_time_setup()` | `SetupCancelled` propagates up through the step (it is not caught locally, same as every other step), stopping all remaining setup steps; anything already configured earlier in the run (e.g. official Strava, if run before this step) is untouched — existing cancellation semantics, unchanged. |
| Both official and unofficial Strava connected simultaneously | `app.py` | Independent branches, independent `provider` keys (`"strava"` vs `"strava_unofficial"`), independent `ProviderStateMachine` instances (per #18's design) — no interaction, no precedence logic, as scoped. |

## Task breakdown

1. In `trainiq/app.py`: add imports for `StravaUnofficialConnector`,
   `PROVIDER`, `CRED_STRAVA_SESSION_COOKIE` from
   `trainiq.connectors.strava_unofficial` (aliased to avoid name
   collision with the official Strava `PROVIDER` import, e.g.
   `PROVIDER as STRAVA_UNOFFICIAL_PROVIDER`).
2. In `trainiq/app.py`: add the new branch to
   `_build_configured_connectors()` immediately after the existing Strava
   branch, per "Interfaces/contracts" above.
3. In `trainiq/setup_wizard.py`: add the equivalent aliased imports.
4. In `trainiq/setup_wizard.py`: add `_setup_strava_unofficial()`, per
   "Interfaces/contracts" above.
5. In `trainiq/setup_wizard.py`: add the one new call in
   `run_first_time_setup()`, between `_setup_strava(...)` and
   `_setup_peloton(...)`.
6. Add `tests/test_app.py` cases (AC1–AC4): configured-when-present,
   skipped-when-absent, construction-failure-skips-others,
   coexists-with-official-Strava. Follow the existing Strava test cases'
   structure (mocked `CredentialStore`).
7. Add `tests/test_setup_wizard.py` cases (AC5–AC10): step is offered
   after official Strava regardless of its outcome; decline stores
   nothing; accept+valid stores all three credentials and reports
   success; accept+rejected stores nothing; accept+unexpected-exception
   stores nothing; `Ctrl+C`/EOF at this step halts remaining steps without
   undoing earlier ones. Inject a fake connector exposing
   `request_manual_recovery()`/`submit_manual_recovery()` (per the
   `connector=` parameter), following the existing fake-session injection
   pattern used for Peloton/Eufy tests.
8. Run full `tests/test_app.py` + `tests/test_setup_wizard.py` suite to
   confirm AC11 (no regression on existing Strava/Peloton/Eufy cases).

## Test strategy notes

- All new tests are unit-level with a fake `StravaUnofficialConnector` (or
  a fake with the same two-method shape) injected via the `connector=`
  parameter in wizard tests, and a mocked `CredentialStore` in `app.py`
  tests — no real HTTP, matching AC12 and the project's live-verification
  constraint (`docs/trainiq/roles/business-analyst.md`).
- `app.py` tests should assert on the *returned connector list's*
  presence/absence and type, plus (where the existing Strava tests already
  do this) on the log call, rather than on internal connector state.
- `setup_wizard.py` tests should assert on `CredentialStore` contents
  (via a real or fake store) after the step runs, not on print() output —
  matching how the existing Peloton/Eufy tests verify "nothing saved"
  (checking `credential_store.get(...)` returns `None`), since that's the
  actual acceptance-criteria-relevant behavior for AC6/AC8/AC9.
- A fake connector for wizard tests needs only two methods:
  `request_manual_recovery() -> str` and
  `submit_manual_recovery(cookie_value: str) -> bool` (or raises, for the
  AC9 case) — no need to fake the real connector's HTTP internals, since
  the wizard step never calls `authenticate()`/`download()`.
- Real-account testing against api.strava.com remains explicitly out of
  scope for the pipeline (BO-driven, optional, per the issue body) —
  nothing in this design requires it to be considered complete.

## Risks/tradeoffs

- **Naming collision risk on import.** Both `strava.py` and
  `strava_unofficial.py` export a module-level `PROVIDER` and (separately)
  a credential-type constant that could visually collide
  (`CRED_REFRESH_TOKEN` vs `CRED_STRAVA_SESSION_COOKIE` don't collide by
  name, but `PROVIDER` does). Both `app.py` and `setup_wizard.py` must
  import the unofficial connector's `PROVIDER` under an alias
  (`STRAVA_UNOFFICIAL_PROVIDER` or similar) to avoid shadowing the
  official Strava `PROVIDER` already imported in both files. Flagging
  explicitly so the Developer doesn't hit a silent-shadowing bug where the
  second `import ... as PROVIDER` quietly overwrites the first.
- **Ordering choice is a judgment call, not a hard requirement.** The
  requirements doc flagged this as non-blocking but explicit: this design
  places the new step between official Strava and Peloton. If the BO
  later wants it offered only as a fallback (official declined/failed),
  that's a small, localized change — wrap the new call in
  `if not _setup_strava(credential_store): _setup_strava_unofficial(...)`
  — but implementing that now would be inventing a business rule the
  requirements doc explicitly says isn't given; this design follows the
  "offered unconditionally" reading the requirements doc already settled
  on.
- **No behavior change to the connector's own ToS/fragility risk** — that
  risk was already accepted at #18's design stage (per
  `docs/trainiq/architecture/18-strava-session-cookie-connector.md`) and is
  unchanged by merely wiring it in; this issue doesn't re-open that
  decision.
- **Two independent Strava connectors can now both run every sync cycle**
  if a user connects both. This roughly doubles Strava-directed API calls
  for such a user (official REST calls + unofficial REST calls to the same
  underlying activities data), which is a real, if small, operational cost
  — but is explicitly the already-accepted design from #18 ("both
  connectors can coexist"), not a new tradeoff introduced by this issue.

## Open questions

None. The one question the requirements doc flagged (ordering/
unconditional vs. fallback-only) is resolved above under "Approach" and
"Risks/tradeoffs."
