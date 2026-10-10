# Architecture: Setup Wizard Prompts Not Displayed (Issue #25)

**Status: BLOCKED — root cause not confirmed, BO input needed.** This is not
a design doc; it's the investigation record required by the requirements
doc's acceptance criterion 1 ("root cause confirmed, not assumed"), kept
here so the next person (BO, or whoever re-opens this issue) doesn't have
to redo it.

## What was tested

The requirements doc flagged the issue's own explanation — stdout
redirection/buffering — as an unconfirmed hypothesis, and a read of
`setup_wizard.py` / `logging_setup.py` doesn't support it: loguru's
handlers attach only to `summary.log`/`diagnostic.log` file sinks, and
every prompt uses plain `print()` / `input()` / `getpass.getpass()`
against the real stdio streams.

I ran the exact reproduction command from the issue
(`python -m trainiq.app`, zero connectors configured, fresh
`CredentialStore`/db) two ways on a live install of the current `main`:

1. **Piped/redirected stdout** (`... | python -m trainiq.app > out.log`,
   stdin fed via a pipe) — the classic "non-tty, block-buffered" case the
   hypothesis describes.
2. **Genuine pty** (forked a child attached to a real pseudo-terminal via
   `pty.openpty()`, answered each `[y/N]` prompt as it appeared) — the
   actual "interactive terminal" case the issue and acceptance criteria
   describe.

**Result: in both cases, every prompt appeared in full, in order, exactly
as the requirements doc's acceptance criteria 2–5 expect** — the opening
message, all four provider prompts, and "Setup complete." None of this
reproduces the bug as filed. This rules out stdout buffering as the
mechanism, at least for a literal `python -m trainiq.app` invocation.

## The one remaining lead: `trainiq.spec`

`projects/trainiq/trainiq.spec` builds the distributable macOS artifact
(`pyinstaller trainiq.spec`) with `console=False` — i.e. `--windowed`, no
console at all. A windowed PyInstaller/py2app-style macOS app has no
attached terminal: `sys.stdout`/`sys.stderr` are not connected to anything
a user can see, and `sys.stdin` has no data source. `print()` and
`input()`/`getpass.getpass()` either write to nothing or raise — and
`_prompt_yes_no()`/`_prompt_text()`/`_prompt_password()` already catch
`OSError` from exactly this situation and convert it to `SetupCancelled`
(see the comment at `setup_wizard.py:68-74`, which already anticipates "a
genuinely non-interactive launch ... no attached terminal at all"). That
produces precisely the reported symptom: the db is created (unrelated to
stdio), setup silently no-ops, the process exits 0, nothing is ever shown
on screen.

This is the only mechanism found in this repo that would produce the
exact reported symptom with zero further hypothesizing required. It is
**not verified** — `trainiq.spec`'s own header says it "cannot be run or
verified in this development sandbox" (no macOS, no PyInstaller macOS
toolchain here), so building and double-clicking the actual `.app` bundle
to confirm this is something only the BO can do.

## The open question blocking a design

The issue's reproduction steps say `python -m trainiq.app`, run directly —
that's the raw module invocation I tested above, and it does **not**
reproduce on current `main`. If the BO actually ran that exact command in
a real Terminal session and still saw silence, the bug is something this
investigation hasn't found yet, and designing a fix now would be guessing
against the wrong mechanism (the requirements doc's own standard to avoid).
If, in practice, the BO is launching the built `TrainIQ.app` bundle
(double-click from Finder, not a Terminal command), `console=False` in
`trainiq.spec` is almost certainly the mechanism, and the fix would look
very different — e.g. detecting the no-console case and either (a)
building a console-attached variant for the first-run setup step, (b)
failing loudly to a log/dialog instead of silently no-op'ing when stdio
isn't usable, or (c) documenting that first-time setup must be run from
Terminal (`trainiq` console entry point in `pyproject.toml`) before the
`.app` is used day-to-day — exact approach needs the Developer/a follow-up
design once the mechanism is confirmed.

Per the requirements doc's own open question #2 and the Architect role's
instruction not to guess when a material question is unresolved, this
needs the BO to confirm **how they were actually running the app when they
saw the silent failure** (raw `python -m trainiq.app` in Terminal, vs. the
packaged `.app` bundle, vs. something else) before a design can be written
against the confirmed mechanism rather than an assumed one.

## Not touched

No code changes were made. `tests/test_setup_wizard.py` / `tests/test_app.py`
were not modified.
