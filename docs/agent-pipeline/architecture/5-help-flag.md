# Architecture: `--help` flag for the CLI

Issue: #5
Requirements: [`docs/requirements/5-help-flag.md`](../requirements/5-help-flag.md)

## Approach

`src/hello.py` already builds its parser with `argparse.ArgumentParser`,
which registers `-h`/`--help` automatically (`add_help` defaults to `True`)
and already satisfies most of this requirement for free: invoking `-h` or
`--help` prints a usage line plus each registered argument's `help=` text,
then exits 0 without running the greeting path — covering acceptance
criteria 1, 2, and 4 with no code change at all.

The one gap is criterion 3 (a concrete usage example): argparse's default
help output never includes worked examples, only the auto-generated
usage/options listing. Close that gap with an `epilog` string passed to
`ArgumentParser(...)`, combined with `formatter_class=
argparse.RawDescriptionHelpFormatter` so argparse prints the epilog exactly
as written instead of re-wrapping/collapsing its newlines. `description`
and the per-argument `help=` strings are left on the default formatter
behavior (only `description` and `epilog` are affected by
`RawDescriptionHelpFormatter`), so the existing options listing is
unaffected.

This is the minimal change: no new flag, no new argument-parsing branch, no
change to `greet()` or to the non-`--help` code path. It reuses the exact
mechanism (`argparse`) the CLI already depends on, rather than hand-rolling
a custom help string or intercepting `-h`/`--help` before argparse sees it.

## Affected components/files

- `src/hello.py` (existing) — modify only the `ArgumentParser(...)`
  constructor call in `main()`: add `formatter_class` and `epilog`. No
  change to `greet()`, to argument definitions, or to the function's return
  value/exit-code behavior on the non-help path.
- `tests/test_hello.py` (existing) — add new `TestCli` cases; no existing
  test changes needed.
- `README.md` — extend the existing "Hello World CLI" section to mention
  `--help`/`-h`.
- No new files, modules, or dependencies.

**Forward-looking note:** as of this writing `src/hello.py` on `main` only
accepts `name` and `--shout` (issue #2's `--version` flag, per its
architecture doc, is not yet present). Per the requirements doc, if
`--version` (or any other argument) has landed by the time this is
implemented, it is already covered by criteria 1-2 automatically — argparse
includes every registered argument in the auto-generated listing with no
extra work — but the Developer must add a corresponding example line to the
new `epilog` text below if that changes the set of arguments worth
demonstrating, and QA should re-check the help output against whatever
arguments actually exist at that point rather than against this doc's
literal epilog text.

## Interfaces/contracts

`src/hello.py`, inside `main()`:

```python
parser = argparse.ArgumentParser(
    description="Print a greeting.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=(
        "examples:\n"
        "  python3 hello.py Alice           Hello, Alice!\n"
        "  python3 hello.py --shout Alice   HELLO, ALICE!\n"
        "  python3 hello.py                 Hello, world!\n"
    ),
)
parser.add_argument("name", nargs="?", default=None, help="Name to greet")
parser.add_argument(
    "--shout",
    action="store_true",
    help="Print the greeting in all caps",
)
args = parser.parse_args(argv)
```

No other change to `main()`. `parser.add_argument` calls, their ordering,
and their existing `help=` strings are unchanged.

Behavioral contract (unchanged from argparse's own default, stated
explicitly since it's load-bearing for the acceptance criteria):

- `-h` and `--help` both trigger the same help output.
- Help output goes to stdout.
- The process exits with status 0 after printing help.
- `greet()` is never called when `-h`/`--help` is present on the command
  line, regardless of other arguments — argparse intercepts and exits
  before `parser.parse_args` returns.
- Any other invocation (no `-h`/`--help`) is parsed and handled exactly as
  before; `-h`/`--help` and `epilog`/`formatter_class` do not change
  argument parsing for non-help invocations.

## Task breakdown

1. In `src/hello.py`, add `formatter_class=argparse.RawDescriptionHelpFormatter`
   and the `epilog=` string above to the existing `ArgumentParser(...)` call
   in `main()`. Do not add an explicit `add_help=` argument (leave the
   default `True`) and do not add a manual `--help`/`-h` handler — argparse
   already owns this.
2. In `tests/test_hello.py`, add to `TestCli`:
   - `test_cli_help`: run `[sys.executable, SCRIPT, "--help"]`, assert
     `returncode == 0`, assert `"--shout"` appears in stdout (the options
     listing), assert `"Alice"` appears in stdout (the epilog example), and
     assert `"Hello"` does **not** appear in stdout (confirms the greeting
     path did not run).
   - `test_cli_help_short_flag`: same assertions, invoked with `"-h"`
     instead of `"--help"`, to cover both spellings named in acceptance
     criterion 1.
3. Update the "Hello World CLI" section of `README.md` to mention
   `--help`/`-h` and that it prints usage + examples instead of a greeting.
4. Run `python3 -m unittest discover -s tests -v` and confirm all tests
   (existing + new) pass.

## Test strategy notes

- CLI/integration-level only, consistent with how the existing suite covers
  argument-parsing behavior (`TestCli` via `subprocess.run`): there is no
  pure-function equivalent to test here, since the behavior under test is
  argparse's own `-h`/`--help` interception inside `main()`, not a new
  function `TestGreet` could exercise directly. Attempting to unit-test
  `main()` by calling it in-process would require catching the `SystemExit`
  argparse raises on `--help` — subprocess already gives a real exit code
  for free, so there's no reason to add that complexity.
- Assert presence of specific substrings (`--shout`, the epilog's example
  text) rather than the full exact stdout string used by the other `TestCli`
  tests — argparse's usage-line wrapping can vary slightly with terminal
  width/Python version, so pinning the entire output would make the test
  brittle for reasons unrelated to this feature. Covers acceptance criteria
  1-3.
- Negative assertion (`"Hello"` absent from `--help` output) directly covers
  "exits ... without printing a greeting" in acceptance criterion 1.
- Regression: all existing `TestGreet`/`TestCli` cases (unshouted and
  shouted, named and unnamed) must keep passing unmodified — covers
  acceptance criterion 4.
- No new test infrastructure, fixtures, or dependencies — same `unittest` +
  `subprocess` approach already used by the suite.

## Risks/tradeoffs

- **Relying on argparse's built-in `-h`/`--help` rather than a hand-rolled
  flag**: less control over exact wording/layout of the options listing,
  but zero new parsing logic, no risk of `-h`/`--help` and a hand-rolled
  flag disagreeing, and it's the idiomatic argparse pattern. The only
  custom piece is the `epilog`, which is exactly the one thing argparse
  doesn't provide out of the box (worked examples).
- **`RawDescriptionHelpFormatter` vs. the default formatter**: switching
  formatter classes could in principle change how the existing
  `description="Print a greeting."` line wraps, but a single short sentence
  has no wrapping to preserve either way, so there's no observable change
  to today's output beyond adding the epilog.
- **Example text hard-codes `hello.py` as the invocation**, matching how the
  existing README already documents running the CLI
  (`python3 src/hello.py ...`); the epilog uses the bare script name since
  that's conventional for argparse epilogs and keeps the example lines
  short, same convention `argparse`'s own auto-generated usage line follows.
- **No error-path change**: invalid arguments (e.g. an unknown flag) still
  go through argparse's existing error handling (usage message to stderr,
  exit code 2) — out of scope per the requirements doc, and this design
  doesn't touch that path.
