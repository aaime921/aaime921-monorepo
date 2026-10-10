# Architecture: Console Feedback on Every Run + Add/Reconfigure a Connector

**Issue:** #25
**Requirements:** `docs/trainiq/requirements/25-setup-wizard-prompts-not-displayed.md`

## Approach

Root cause (confirmed by the BO, not the Architect's earlier disproven
buffering hypothesis): `app.main()` only calls `run_first_time_setup()` when
`_build_configured_connectors()` returns an empty list, and every other
status/result line the app produces is written exclusively to
`summary_logger()`'s file sinks (`summary.log` / `diagnostic.log`), never to
stdout. Once one connector exists, a completely normal run prints nothing.
Two independent, additive gaps follow directly from this, matching the two
items in the requirements doc's Scope:

1. There is no way to add or reconfigure a connector once at least one is
   already configured — `run_first_time_setup()` is reachable only through
   the all-or-nothing "zero connectors" gate.
2. Nothing in a normal run reaches the terminal, by construction — every
   status line already exists as a `summary_logger()` call; it just has no
   second destination.

Both are solved by **adding a second destination for lines that are already
being produced**, not by inventing new status computation, and by **adding
one explicit, discoverable entry point** for (re)configuring a connector,
reusing the four existing per-provider setup functions unchanged. No
connector/auth logic, lifecycle state machine, or Sync Engine behavior
changes.

### Triggering "add/reconfigure a connector": an explicit `--configure` flag

`python -m trainiq.app --configure` (parsed with the stdlib `argparse` — no
new dependency, matching the project's recurring "zero new dependencies"
bar, e.g. Strava's manual-paste OAuth). Rejected alternatives and why:

- **Prompt every run while coverage is incomplete.** Rejected: having only
  2 of 4 providers connected is a normal, deliberate steady state (not
  every user owns a Peloton *and* a Eufy scale), consistent with this
  project's explicit "configured, not enabled — a factual state, not a
  toggle" framing (`app.py`'s module docstring). Prompting on every single
  run to "fix" a non-problem is nagging, not a feature.
- **Environment variable.** Rejected: requires the user to remember and
  unset a persistent variable for what is actually a one-off, interactive
  action; a flag is discoverable via `--help` and self-cleaning (present
  only on the invocation where it's needed).

A flag is not a new "separate `configure` command" in the sense the Chief
Architect's RC1-HF-002 decision foreclosed — that decision was about not
adding a second entry point/module for first-time setup specifically. This
is still the one existing entry point (`python -m trainiq.app`), with one
optional flag.

`--configure` works regardless of current connector state (zero or several)
and is additive to, not a replacement for, the existing zero-connector
auto-trigger — seeing it as a second, explicit path into the *same*
per-provider setup functions keeps both paths impossible to diverge, since
there is only one implementation of "ask about Strava/Strava-unofficial/
Peloton/Eufy" underneath both.

### Console feedback on every run: capture the lines already being logged

`_build_configured_connectors()` and `_log_sync_summary()` already compute
and log, for every one of the four providers, a one-line human-readable
status/result string. The fix is to have each of those calls **also**
append its message to an in-memory list, and have `main()` print that list
to stdout after the run — the existing `summary_logger()` call sites are
left untouched (same calls, same arguments, same log output), so none of
the file-logging behavior changes and no existing test needs to change
because of *that* part of the fix.

## Affected components/files

- `projects/trainiq/trainiq/app.py` — `main()` gains `--configure` parsing
  and stdout printing; `_build_configured_connectors()` and
  `_log_sync_summary()` gain an optional reporter parameter (additive,
  default-preserving signature).
- `projects/trainiq/trainiq/setup_wizard.py` — new `run_configure()` entry
  point; `run_first_time_setup()`'s body is factored so both share the same
  underlying per-provider steps.
- `projects/trainiq/tests/test_app.py` — new tests; existing `main()` call
  sites updated to pass `argv=[]` explicitly (see "Risks/tradeoffs" — this
  is required for existing tests to keep working under the new signature,
  not a behavior change).
- `projects/trainiq/tests/test_setup_wizard.py` — new tests for
  `run_configure()`.
- No change to `logging_setup.py`, any connector, the Sync Engine, or the
  lifecycle policy.

## Interfaces/contracts

### `app.py`

```python
def main(argv: list[str] | None = None) -> int:
    """argv defaults to None, which tells argparse to read sys.argv[1:] —
    correct for both the `trainiq` console-script entry point and
    `python -m trainiq.app`. Direct callers (tests) must pass argv=[]
    explicitly to avoid argparse parsing pytest's own command-line args."""

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """One flag: --configure (store_true). Nothing else — keep the surface
    minimal; this is the only CLI surface the app has ever had."""
```

```python
class _Reporter:
    """Wraps a loguru-bound logger. Every call both logs exactly as before
    AND appends the same message to self.lines, in order. Existing call
    sites change from `log.info(...)` to `report.info(...)` — same
    arguments, same log output, now also collectible for stdout."""
    def __init__(self, log) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
```

```python
def _build_configured_connectors(
    credential_store: CredentialStore,
    config_path: Path,
    reporter: "_Reporter | None" = None,
) -> list[Connector]:
    """Return type and existing behavior UNCHANGED — every current call
    site and assertion in test_app.py (`connectors = _build_configured_connectors(store, config_path)`)
    keeps working untouched. When reporter is omitted, an internal
    throw-away _Reporter wrapping summary_logger() is used, so logging
    behavior is identical either way. main() passes its own reporter in
    so it can read `reporter.lines` afterward."""

def _log_sync_summary(result, reporter: "_Reporter | None" = None) -> None:
    """Same pattern as above."""
```

`main()`'s flow (condensed; error-path/safety-check code before this point
is unchanged):

```python
def main(argv=None) -> int:
    args = _parse_args(argv)
    configure(LOG_DIR)
    log = summary_logger()
    ...  # unchanged: trash check, db open, credential_store construction

    reporter = _Reporter(log)
    connectors = _build_configured_connectors(credential_store, CONFIG_PATH, reporter=reporter)

    if args.configure:
        run_configure(credential_store, CONFIG_PATH, status_lines=reporter.lines)
        reporter = _Reporter(log)
        connectors = _build_configured_connectors(credential_store, CONFIG_PATH, reporter=reporter)
    elif not connectors:
        run_first_time_setup(credential_store, CONFIG_PATH)  # unchanged call
        reporter = _Reporter(log)
        connectors = _build_configured_connectors(credential_store, CONFIG_PATH, reporter=reporter)

    for line in reporter.lines:
        print(line)

    if not connectors:
        log.info("Nothing to synchronize.")
        print("Nothing to synchronize.")
        _print_log_locations()
        conn.close()
        return 0

    ...  # unchanged: load_athlete_profile, engine construction, run_once
    sync_reporter = _Reporter(log)
    _log_sync_summary(result, reporter=sync_reporter)
    for line in sync_reporter.lines:
        print(line)
    _print_log_locations()
    conn.close()
    return 0

def _print_log_locations() -> None:
    print(f"\nFull logs: {LOG_DIR / 'summary.log'}, {LOG_DIR / 'diagnostic.log'}")
```

### `setup_wizard.py`

```python
def _run_provider_setup_steps(credential_store: CredentialStore, config_path: Path) -> None:
    """Shared body extracted from the old run_first_time_setup(): the four
    _setup_* calls, in the same fixed order, under the same SetupCancelled
    handling. Both run_first_time_setup() and run_configure() call this and
    differ only in their own header/footer print() text, so there is
    exactly one implementation of 'ask about each provider' for both the
    first-time and reconfigure paths to diverge from."""
    _setup_strava(credential_store)
    _setup_strava_unofficial(credential_store)
    _setup_peloton(credential_store)
    _setup_eufy(credential_store, config_path)


def run_first_time_setup(credential_store: CredentialStore, config_path: Path) -> None:
    """Unchanged behavior/text — still exactly what AC4 requires to not regress."""
    print("No providers are configured yet. Let's connect at least one.\n")
    try:
        _run_provider_setup_steps(credential_store, config_path)
    except SetupCancelled:
        print("\nSetup cancelled.")
        return
    print("\nSetup complete.")


def run_configure(
    credential_store: CredentialStore,
    config_path: Path,
    status_lines: list[str],
) -> None:
    """New. Triggered only by --configure, independent of how many
    connectors are currently configured. Prints the already-computed
    per-provider status (status_lines, e.g. 'Strava: configured',
    'Eufy: skipped (missing device_id in config.json)') as a header, then
    runs the exact same four prompts first-time setup uses. Saying 'y' to
    an already-configured provider naturally reconfigures it —
    credential_store.set() overwrites the existing Keychain entry, and
    every _setup_* function already validates-then-stores (or rolls back on
    failure) regardless of whether a value previously existed, so no new
    rollback/overwrite logic is needed here. Saying 'n' leaves that
    provider's existing credentials untouched."""
    print("Current connectors:")
    for line in status_lines:
        print(f"  {line}")
    print()
    try:
        _run_provider_setup_steps(credential_store, config_path)
    except SetupCancelled:
        print("\nConfiguration cancelled.")
        return
    print("\nConfiguration complete.")
```

## Task breakdown

1. `setup_wizard.py`: extract `_run_provider_setup_steps()` from
   `run_first_time_setup()`'s body; confirm `run_first_time_setup()`'s
   printed output is byte-for-byte unchanged (AC4).
2. `setup_wizard.py`: add `run_configure()` as specified above.
3. `app.py`: add `_Reporter`; thread it through
   `_build_configured_connectors()` and `_log_sync_summary()` as an
   optional parameter; verify every existing `log.info`/`log.warning` call
   site becomes `report.info`/`report.warning` with identical arguments
   (no log-content change).
4. `app.py`: add `_parse_args()` (`argparse`, one `--configure` flag);
   change `main()`'s signature to `main(argv: list[str] | None = None)`.
5. `app.py`: wire `main()`'s flow as specified above — `--configure`
   branch, stdout printing of `reporter.lines` and `sync_reporter.lines`,
   `_print_log_locations()`.
6. `pyproject.toml`'s `trainiq = "trainiq.app:main"` entry point and the
   `if __name__ == "__main__": sys.exit(main())` guard both call `main()`
   with zero arguments — confirm both still work with the new signature
   (they do: `argv=None` is argparse's own signal to read `sys.argv[1:]`,
   which is exactly what both callers rely on; no change needed to either
   call site).
7. `tests/test_app.py`: update every existing direct `main()` call (e.g.
   `app_module.main()`, `main()`) to `main(argv=[])` — required so argparse
   parses an empty list instead of pytest's own process argv; this is a
   test-code-only change, not a behavior change. Confirm all pre-existing
   assertions still pass unmodified otherwise.
8. `tests/test_app.py`: add new tests (see Test strategy notes).
9. `tests/test_setup_wizard.py`: add new tests for `run_configure()` (see
   Test strategy notes).

## Test strategy notes

All via fixtures/monkeypatching (unit/integration only — AC6 explicitly
rules out live-account testing as a pipeline deliverable), following the
existing patterns already in both test files (`in_memory_keyring`,
`isolated_app_dirs`, monkeypatching `builtins.input`/`getpass.getpass`).

- **`_Reporter`**: a direct unit test that `.info()`/`.warning()` both log
  (assert via the same monkeypatch-the-logger technique
  `test_main_logs_executing_package_path_on_normal_startup` already uses)
  and collect into `.lines`, in call order.
- **Console summary, normal run (AC2/AC3)**: extend
  `test_main_continues_when_one_connector_cannot_be_constructed`-style
  setup (fake connectors, no network) with `capsys`; assert stdout contains
  one line per connector's configured/skipped status, the sync-result line
  per connector, and a line naming both `summary.log` and `diagnostic.log`
  paths under the test's `isolated_app_dirs` `LOG_DIR`.
- **First-time path unregressed (AC4)**: re-run the two existing
  `test_main_with_no_credentials_triggers_wizard_then_exits_cleanly` /
  `test_main_wizard_cancellation_via_eof_exits_cleanly_with_nothing_saved`
  tests unmodified (only their `main()` call gains `argv=[]`) — same
  assertions, same exit codes.
- **`--configure` with zero connectors configured**: `main(argv=["--configure"])`
  with `input()` monkeypatched to decline every provider; assert
  `run_configure()` (not `run_first_time_setup()`) is what ran — e.g. by
  monkeypatching `run_first_time_setup` to raise if called, proving only
  the `--configure` branch fired.
- **`--configure` with one connector already configured**: store a Strava
  refresh token first; `main(argv=["--configure"])` with `input()` declining
  every prompt; assert (a) the printed "Current connectors" header
  includes "Strava: configured", (b) Strava's existing credential is
  unchanged afterward (declining must not delete it — distinct from the
  first-time wizard's own rollback-on-failure case, which only deletes a
  credential it just stored in the same step), (c) `main()` does NOT also
  call `run_first_time_setup()` even though `--configure` ran.
- **`run_configure()` reconfigures an existing connector**: directly test
  `run_configure()` (mirroring `test_setup_wizard.py`'s existing
  per-provider tests) — pre-seed Peloton credentials, monkeypatch
  `input`/`getpass` to answer "y" + new email/password for Peloton only and
  "n" for the other three, assert Peloton's stored credential value changed
  to the new one and the other three providers' stored state (absent) is
  unaffected.
- **No regression (AC5)**: run the full existing `test_app.py` /
  `test_setup_wizard.py` suites after the `argv=[]` update; every prior
  assertion must still pass unmodified.

## Risks/tradeoffs

- **`main()`'s signature change is the one place this design touches code
  outside the two target files' internals.** Mitigated by `argv=None`
  defaulting to argparse's own `sys.argv[1:]` read, which is exactly what
  both real callers (the `trainiq` console script, and `python -m
  trainiq.app`'s `__main__` guard) already do implicitly — neither needs to
  change. Only direct test callers need the explicit `argv=[]` update
  (Task 7); this is called out explicitly rather than left for the
  Developer to discover as a surprise test failure.
- **Two trigger paths into the same setup machinery
  (`run_first_time_setup()` auto-triggered on zero connectors,
  `run_configure()` explicit via `--configure`).** Kept from diverging by
  routing both through one shared `_run_provider_setup_steps()` — a
  behavior change to one provider's prompt affects both paths identically,
  by construction, not by discipline.
- **Stdout duplication.** Every status/result line now appears in both
  `summary.log` and the terminal by design (AC2/AC3) — this is the fix, not
  a side effect to avoid.
- **`--configure` run unattended (no TTY).** Not a new risk: the existing
  `_prompt_yes_no`/`_prompt_text`/`_prompt_password` helpers already catch
  `OSError` (no stdin available at all) and raise `SetupCancelled`,
  already exercised by `test_main_wizard_cancellation_via_eof_exits_cleanly_with_nothing_saved`.
  An unattended `--configure` run simply cancels immediately on the first
  prompt and `main()` proceeds exactly as it does today when the
  zero-connector wizard cancels — no new failure mode.
- **Scope boundary.** This issue does not touch the Peloton `Degraded`
  lifecycle bug (#33) — its reason string (already computed by
  `ConnectorSyncResult`/`_log_sync_summary`) now simply becomes *visible*
  on the console via this fix, which is exactly AC2's intent, not a fix to
  #33 itself.
