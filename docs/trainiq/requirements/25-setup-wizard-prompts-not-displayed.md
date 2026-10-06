# Requirements: Setup Wizard Prompts Not Displayed in Interactive Terminal

**Issue:** #25

## Summary

Root cause confirmed by the BO (2026-10-06), superseding the original
stdout-redirection/buffering hypothesis the Architect's investigation had
already ruled out: the setup wizard is not being suppressed — it is simply
**never invoked** once any single connector is already configured.
`app.main()` only calls `run_first_time_setup()` when
`_build_configured_connectors()` returns an empty list (`app.py`). On the
BO's machine, Peloton was already configured from earlier use, so that
check returned a non-empty list, the wizard was skipped entirely, and every
other piece of run output (`_build_configured_connectors()`'s per-connector
status, `_log_sync_summary()`'s per-connector sync result) is written only
to `summary_logger()`'s file sinks (`summary.log` / `diagnostic.log` under
`~/Library/Logs/TrainIQ`, via `logging_setup.py`) — never to the terminal.
A completely normal run therefore prints nothing at all to the console,
which looked identical to a silent failure.

This replaces the prior version of this doc, written before the root
cause was confirmed.

## Scope

1. **Add or reconfigure a connector when at least one connector is already
   configured.** Today there is no path to this: `run_first_time_setup()`
   is reachable from `app.main()` only via the single all-or-nothing gate
   "`_build_configured_connectors()` returned zero connectors." A user who
   already has Peloton connected and wants to also connect
   Strava-unofficial has no way to do so short of manual database/config
   manipulation. Provide a working way to add/reconfigure a connector in
   this state. The triggering mechanism (a prompt on every run while
   coverage is incomplete, an explicit CLI flag/argument, an environment
   variable, something else) is the Architect's design decision — this doc
   states the need, not the mechanism.
2. **Console feedback on every run, not only first-time setup.** Every
   invocation of `python -m trainiq.app` — not just the first-time-setup
   path — should print a short, human-readable summary to the terminal:
   which connectors were configured / skipped (with reason) / degraded
   (with reason), and for connectors that ran, what synced (counts of
   downloaded/inserted/updated/malformed/skipped records), plus where the
   full logs live. This information already exists (it's exactly what
   `_build_configured_connectors()` and `_log_sync_summary()` log via
   `summary_logger()`) — it just never reaches stdout today.

### Out of scope

- The Strava API endpoint bug (#26) — already fixed and merged (PR #28).
  Not a dependency of this work going forward.
- The Peloton resume-cursor `int`/`str` type-mismatch bug causing a
  `Degraded` state (#33) — tracked and fixed separately. This issue's AC2
  below means a `Degraded` connector's reason is visible on the console,
  not that #33 itself gets fixed here.
- Any change to provider authentication logic, field mapping, or connector
  behavior once credentials are collected.
- Scripting/automating credential entry.
- Verifying this fix against a real account in production — must be
  testable via unit/integration tests or fixture data; the BO may
  additionally verify live themselves, but that's not a pipeline
  deliverable.
- The packaged macOS `.app` bundle (`console=False`) packaging concern
  raised earlier in this issue's history — still a separate, already-
  flagged concern, not part of this scope.

## Acceptance criteria

1. With at least one connector already configured (e.g. Peloton), the user
   has a documented, working way to add or reconfigure another connector
   (e.g. Strava-unofficial) without manually editing the database or
   `config.json`.
2. Every run of `python -m trainiq.app` prints a summary to the terminal
   (stdout), covering, per connector: configured / not configured / skipped
   (with reason) / degraded (with reason) — and for connectors that
   synced, counts of downloaded/inserted/updated/malformed/skipped records.
3. The printed summary states where `summary.log` and `diagnostic.log`
   live, so a user who wants more detail knows where to look.
4. First-time setup (zero connectors configured) continues to behave
   exactly as before — opening message, per-provider prompts, "Setup
   complete." This issue extends console output to every other run; it
   must not regress the existing first-time path.
5. No regression to existing tests in `tests/test_setup_wizard.py` and
   `tests/test_app.py`.
6. New test coverage exists for: (a) the add/reconfigure-a-connector path
   when at least one connector is already configured, and (b) the console
   summary appearing on stdout for a normal (non-first-time-setup) run —
   via fixtures/monkeypatching, not a real account, per the project's
   live-verification constraint.

## Open questions

None blocking handoff. The exact triggering mechanism for "add another
connector" is left to the Architect's design, per the BA/Architect division
of responsibility (describe *what*, not *how*).

## Dependencies

Builds on `app.py`, `setup_wizard.py`, and `logging_setup.py` as they exist
on `main` after #26 (merged via PR #28). Independent of #33 — a `Degraded`
connector's reason surfaces in the console output this issue adds (AC2),
but fixing #33's underlying cursor-type bug is not part of this issue.
