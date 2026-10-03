
# Architecture: Update CLI help text for timestamp feature

Issue: #13
Requirements: [`docs/agent-pipeline/requirements/13-update-cli-help-text.md`](../requirements/13-update-cli-help-text.md)

## Approach

As of this writing, `main`'s `src/hello.py` builds its parser as:

```python
parser = argparse.ArgumentParser(description="Print a greeting.")
parser.add_argument("name", nargs="?", default=None, help="Name to greet")
parser.add_argument("--shout", action="store_true", help="Print the greeting in all caps")
```

No `epilog`/`formatter_class` customization exists on `main` yet (issue #5's
architecture doc proposed one, but that PR has not merged), and there is no
`--version` flag either (issue #2, also unmerged). This design targets
`main`'s actual current state and does not assume either of those lands
first — see "Affected components/files" for what to do if they do.

Add the required phrase by appending a second sentence to the parser's
`description=` string: `"Print a greeting. Timestamps are shown with
greeting."` `description` is always rendered near the top of argparse's
default help output, before the options listing, so this satisfies the
requirement with a one-line change and no new argparse mechanism
(no `epilog`, no `formatter_class`, no manual `-h`/`--help` handling).

**Why `description` and not `epilog`:** the requirements doc explicitly
leaves placement up to this design. `epilog` is the right place for worked
*examples* of literal output (that's what issue #5's design doc uses it
for), but this phrase is a general statement about behavior, not an
example — it reads naturally as a second sentence of the description, and
it does not need `RawDescriptionHelpFormatter` or any example-string
maintenance. Critically, it also keeps this change decoupled from the exact
timestamp *format*: the description sentence just says a timestamp is
shown, without committing to `[YYYY-MM-DD HH:MM:SS]` or any other literal
example string that would need to stay in sync with issue #8's output
format. If issue #5's epilog-with-examples design lands later, the
Developer implementing it will need to make its example lines match
whatever `greet()` actually outputs at that time (that's already called out
in issue #5's own architecture doc) — this design doesn't add a second
place that needs the same upkeep.

**Dependency on issue #8 (not blocking this design, but blocking for the
Developer):** PR #11, which implements the timestamp feature itself, is
still open against `main`. Per the requirements doc's "Open questions" and
the cross-issue dependency protocol in
[`docs/agent-pipeline/PIPELINE.md`](../PIPELINE.md), the Developer
implementing this issue must confirm PR #11 has merged before implementing
this change on top of it. If PR #11 is still unmerged when this issue
reaches `stage:dev`, the Developer should add `blocked:dependency` and name
PR #11, rather than landing help text that advertises a timestamp
`hello.py` doesn't yet produce. This design's approach does not depend on
PR #11's exact diff (it only touches the `description=` string, not
`greet()`), so once PR #11 has merged there is no additional rebasing
concern beyond the normal one of building on current `main`.

## Affected components/files

- `src/hello.py` (existing) — modify only the `description=` argument of
  the existing `ArgumentParser(...)` call in `main()`. No change to
  `greet()`, to argument definitions, to `--shout`, or to the non-help exit
  path.
- `tests/test_hello.py` (existing) — add one new `TestCli` case; no
  existing test changes needed.
- `README.md` — add a one-line mention that `--help` documents the
  timestamp.
- No new files, modules, or dependencies.

**Forward-looking note (parallels issue #5's own note for `--version`):** if
issue #5's epilog/`--version` support, or issue #2's `--version` flag, have
already landed on `main` by the time this is implemented, they are
unaffected by this change — `description` and `epilog` are independent
argparse fields, and adding a sentence to `description` does not alter an
existing `epilog` or any argument listing. The Developer should still read
`main`'s actual `hello.py` at implementation time rather than assume the
snippet below is the literal diff base.

## Interfaces/contracts

`src/hello.py`, inside `main()`:

```python
parser = argparse.ArgumentParser(
    description="Print a greeting. Timestamps are shown with greeting.",
)
parser.add_argument("name", nargs="?", default=None, help="Name to greet")
parser.add_argument(
    "--shout",
    action="store_true",
    help="Print the greeting in all caps",
)
args = parser.parse_args(argv)
```

No other change to `main()`. Argument definitions, their ordering, and
their existing `help=` strings are unchanged.

Behavioral contract (unchanged from today, stated explicitly since it's
load-bearing for the acceptance criteria):

- `-h`/`--help` print the description (now including the timestamp
  sentence) followed by the usage/options listing, then exit 0.
- `greet()` is never called when `-h`/`--help` is present on the command
  line — argparse intercepts before `parse_args` returns, same as today.
- Any other invocation (no `-h`/`--help`) is parsed and handled exactly as
  before; this change does not touch argument parsing for non-help
  invocations, the greeting, or `--shout`.

## Task breakdown

1. In `src/hello.py`, change the `description=` value passed to
   `ArgumentParser(...)` in `main()` from `"Print a greeting."` to
   `"Print a greeting. Timestamps are shown with greeting."`. No other
   line in `main()` or `greet()` changes.
2. In `tests/test_hello.py`, add to `TestCli`:
   - `test_cli_help_mentions_timestamp`: run
     `[sys.executable, SCRIPT, "--help"]`, assert `returncode == 0`, assert
     the exact substring `"Timestamps are shown with greeting"` appears in
     `result.stdout`, and assert `"--shout"` also appears (confirms the
     existing options listing is still present and unmodified). Do **not**
     assert the full stdout string — same rationale as issue #5's
     `test_cli_help`: argparse's usage-line wrapping can vary with terminal
     width/Python version, so pin substrings, not the whole output.
3. Update `README.md`'s "Hello World CLI" section with a one-line mention
   that `--help` now also notes that timestamps are shown with the
   greeting.
4. Run `python3 -m unittest discover -s tests -v` from
   `projects/agent-pipeline/` and confirm all tests (existing + new) pass.
5. Manually confirm, before handing off to QA, that PR #11 (issue #8) has
   merged to `main`; if not, stop and add `blocked:dependency` per the
   cross-issue dependency protocol instead of proceeding to step 6.
6. Follow the standard handoff protocol: commit, comment, remove
   `stage:dev`, add `stage:qa`.

## Test strategy notes

- CLI-level only, matching how the existing suite covers `--help`-adjacent
  behavior: there's no pure-function equivalent, since the only change is
  text passed to `argparse.ArgumentParser`, not a new function `TestGreet`
  could exercise directly.
- Substring assertions, not full-stdout equality, for the same reason
  issue #5's design gives: avoid coupling the test to incidental
  line-wrapping. Covers acceptance criterion 1.
- Regression: all existing `TestGreet`/`TestCli` cases (named/unnamed,
  shout/no-shout) must keep passing unmodified — covers acceptance
  criteria 2-4. If PR #11 has merged by implementation time, those cases
  will already reflect the new bracketed-timestamp format per issue #8's
  own test changes; this issue does not add or change assertions about the
  greeting's literal format.
- No new test infrastructure, fixtures, or dependencies — same `unittest` +
  `subprocess` approach already used by the suite.

## Risks/tradeoffs

- **`description` vs. `epilog` placement**: `description` was chosen
  because it needs no new formatter machinery and keeps this change
  independent of the literal timestamp example text an `epilog` would need
  to show. The tradeoff is that the phrase appears as a second sentence in
  a paragraph rather than as a highlighted worked example; acceptable since
  the requirement is only that the phrase appear in `--help` output
  verbatim, not where.
- **Merge-order dependency on issue #8/PR #11**: this design's own change
  is independent of PR #11's diff (different part of `main()`), but the
  *meaning* of the help text (truthfully describing current behavior) is
  not — landing this issue's text before PR #11 merges would make `--help`
  describe a feature `hello.py` doesn't yet exhibit. This is a sequencing
  risk for the Developer to manage via `blocked:dependency`, not something
  this design can eliminate by itself, since the two issues' code changes
  don't overlap.
- **No error-path change**: invalid arguments still go through argparse's
  existing error handling (usage message to stderr, exit code 2) —
  unaffected by a `description=` string change.
