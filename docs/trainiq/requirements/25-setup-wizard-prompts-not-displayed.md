# Requirements: Setup Wizard Prompts Not Displayed in Interactive Terminal

**Issue:** #25

## Summary

With zero connectors configured, running `python -m trainiq.app` should trigger `run_first_time_setup()` and walk the user through connecting at least one provider (Strava, Peloton, Eufy) via interactive prompts. Instead, per the BO's report, the app runs to completion silently: the database is created/updated but no prompts ever appear on screen, so the user has no way to configure a connector on first run and is left with manual database/credential manipulation as the only workaround. This blocks first-time setup entirely for any new install.

## Scope

- Make the first-time setup wizard's prompts (`run_first_time_setup()` and everything it calls: the Strava, Strava-unofficial, Peloton, and Eufy steps) reliably visible to the user in a real interactive terminal session when zero connectors are configured.
- Confirm, with live reproduction, what actually causes the prompts to be suppressed — the issue's own stdout-redirection/buffering explanation is explicitly labeled a hypothesis, not a confirmed cause (see "Open questions" below).
- Preserve all existing documented behavior of the wizard: rollback semantics on auth failure, no rollback of already-succeeded providers on later cancellation, Ctrl+C/EOF handling, Eufy auto device discovery.

### Out of scope

- Any change to provider authentication logic, field mapping, or connector behavior once credentials are collected — this is purely about prompts reaching the user, not what happens after a provider connects.
- Adding a separate `configure` command/entry point — the Chief Architect's existing decision (auto-trigger only when zero connectors are configured, documented in `setup_wizard.py`) is not being revisited by this issue.
- Scripting/automating credential entry — out of bounds per project ground rules.
- Fixing this via "test against a real account in production" — any fix must be verifiable via unit/integration tests or manual fixture data, since CI has no access to real BO accounts.

## Acceptance criteria

1. **Root cause confirmed, not assumed.** Before the fix is considered correct, the actual mechanism suppressing the prompts is identified and verified against a real repro (see "Open questions") — not just the stdout-buffering hypothesis taken at face value.
2. **Opening message visible.** On a fresh install (empty `CredentialStore`, no connectors configured), running the app in a genuine interactive terminal session displays "No providers are configured yet. Let's connect at least one." on screen before any prompt is shown.
3. **Each step's prompts visible.** The yes/no connect prompt, and any follow-up prompts (email, password, authorization code, cookie value, device choice) for Strava, Strava (unofficial), Peloton, and Eufy are all visible on screen at the point they are presented — not only discoverable afterward in `summary.log` / `diagnostic.log`.
4. **End-to-end setup completes.** A user who answers the prompts for at least one provider in a real interactive terminal session ends up with that provider configured (readable back from `CredentialStore`), sees "Setup complete.", and on the next run that connector is picked up by `_build_configured_connectors()` and synchronized.
5. **Cancellation behavior unchanged.** Ctrl+C/EOF during any prompt still prints "Setup cancelled." (via `SetupCancelled`) and still does not roll back providers already successfully configured earlier in the same run — no regression to the behavior documented in `setup_wizard.py`.
6. **No regression to existing tests.** All existing tests in `tests/test_setup_wizard.py` and `tests/test_app.py`, which exercise the wizard by monkeypatching `input`/`getpass` directly rather than a real terminal, continue to pass unmodified.
7. **New regression coverage.** A test (or tests) added that would have caught this specific failure mode — i.e., exercises the wizard the way the bug actually manifested (real/attached stdio, not just monkeypatched functions), so a future change can't silently reintroduce it. Exact test mechanics are the Architect/Developer's call.

## Open questions

- **Root cause is unverified.** The issue text itself calls the stdout-redirection/buffering explanation a "Hypothesis." A read of the current code doesn't obviously support it: `trainiq/logging_setup.py`'s loguru handlers write only to `summary.log` and `diagnostic.log` file sinks and never attach to `sys.stdout`/`sys.stderr`, and every prompt in `setup_wizard.py` uses plain `print()` / `input()` / `getpass.getpass()`, which write directly to the real stdio streams — nothing in the reviewed code appears to intercept or buffer them. Per this project's evidence-based principle, this needs an actual live reproduction, not a guess, before a fix is designed against the wrong mechanism. This is a technical investigation, not a business decision, so it's flagged here for the Architect to pursue rather than held for the BO — but if reproducing it requires something only the BO can observe (see next point), the Architect may still need to loop the BO in via `needs:human` at that stage.
- **How is the app actually being launched when this reproduces?** The reported repro command is `python -m trainiq.app` run directly, which should have a real attached tty on stdin/stdout. If the BO is actually launching it some other way in practice (e.g. a packaged macOS app bundle, a wrapper/launcher script, a terminal emulator with non-standard stdio handling), that materially changes where the bug lives and whether it's reproducible in an automated test at all. This detail isn't in the issue and may need the BO to confirm exactly how they launched it when they saw the silent failure.

## Dependencies

None — builds on existing `setup_wizard.py` / `app.py` (last touched by #22/#24, already merged to main).
