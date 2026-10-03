# Architecture: `--shout` flag for the CLI

Issue: #3
Requirements: [`docs/requirements/3-shout-flag.md`](../requirements/3-shout-flag.md)

## Approach

Add a boolean `shout` parameter to the existing `greet()` pure function in
`src/hello.py`, defaulting to `False`, and expose it via a new `--shout`
argparse flag in `main()`. When `shout` is `True`, `greet()` returns the
normal greeting string upper-cased.

This keeps the shouting behavior inside the same pure function that already
owns greeting construction, rather than upper-casing the string in `main()`
or duplicating the format string. It keeps `greet()` trivially unit-testable
(as the existing tests already do) without needing a subprocess, and it
requires no change to argument parsing beyond one extra flag — no new
modules, classes, or config.

## Affected components/files

- `src/hello.py` (existing, from issue #1 / PR #2) — modify `greet()` and
  `main()`.
- `tests/test_hello.py` (existing) — add new test cases; no existing test
  changes needed.
- `README.md` — extend the existing "Hello World CLI" section to document
  `--shout`.
- No new files.

**Dependency note:** as of this writing, `src/hello.py` exists only on the
open `issue-1-hello-world-cli` branch (PR #2) and has not been merged to
`main`. This design is written against that branch's current content
(confirmed via GitHub). The Developer should confirm PR #2 has merged to
`main` before starting; if it hasn't, either wait for the merge or branch
from PR #2's branch and rebase once it lands. This is a sequencing note, not
an open design question — the code the Developer needs to change is fixed
and already known.

## Interfaces/contracts

`src/hello.py`:

```python
def greet(name: str | None, shout: bool = False) -> str:
    """Return the greeting string for `name` (or the "world" default).

    If `shout` is True, the returned string is upper-cased.
    """
    greeting = f"Hello, {name or 'world'}!"
    return greeting.upper() if shout else greeting


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a greeting.")
    parser.add_argument("name", nargs="?", default=None, help="Name to greet")
    parser.add_argument(
        "--shout",
        action="store_true",
        help="Print the greeting in all caps",
    )
    args = parser.parse_args(argv)
    print(greet(args.name, shout=args.shout))
    return 0
```

No changes to the CLI's exit-code behavior, output stream (stdout), or the
non-`--shout` code path's output.

## Task breakdown

1. In `src/hello.py`, add the `shout: bool = False` parameter to `greet()`
   and upper-case the result when `shout` is true.
2. In `src/hello.py`, add the `--shout` flag to the `argparse` parser in
   `main()` (`action="store_true"`, default `False`) and pass
   `shout=args.shout` into the `greet()` call.
3. In `tests/test_hello.py`, add two unit tests on `TestGreet`:
   - `greet("Alice", shout=True) == "HELLO, ALICE!"`
   - `greet(None, shout=True) == "HELLO, WORLD!"`
4. In `tests/test_hello.py`, add two subprocess-level tests on `TestCli`,
   mirroring the existing `test_cli_with_name` / `test_cli_without_name`
   style:
   - running `[sys.executable, SCRIPT, "--shout", "Alice"]` asserts exit
     code 0 and stdout `"HELLO, ALICE!\n"`.
   - running `[sys.executable, SCRIPT, "--shout"]` asserts exit code 0 and
     stdout `"HELLO, WORLD!\n"`.
5. Update the "Hello World CLI" section in `README.md` to mention the
   `--shout` flag and give one example invocation.
6. Run `python3 -m unittest discover -s tests -v` and confirm all tests
   (existing + new) pass.

## Test strategy notes

- Unit-level: call `greet()` directly with `shout=True`, both with and
  without a name, to cover acceptance criteria 1 and 2 at the fastest
  layer, consistent with the existing `TestGreet` tests.
- CLI/integration-level: subprocess-run the script with `--shout` (with and
  without a name) and assert exact stdout and a zero exit code, consistent
  with the existing `TestCli` tests and covering acceptance criteria 1, 2,
  and 4.
- Regression: the existing four tests (unshouted unit + CLI cases) must
  keep passing unmodified, covering acceptance criterion 3.
- No new test infrastructure, fixtures, or dependencies are needed — same
  `unittest` + `subprocess` approach as issue #1.

## Risks/tradeoffs

- **Upstream dependency on PR #2**: this design targets `src/hello.py` as
  it exists on the `issue-1-hello-world-cli` branch, which is not yet on
  `main`. If that PR's implementation changes materially before merging,
  this design's line-level details (though not its approach) may need a
  small update.
- **Upper-casing approach**: `.upper()` on the fully formatted string (vs.
  upper-casing just the name before interpolation) is chosen because it's
  simpler and the requirements explicitly want the whole greeting shouted.
  No tradeoff in practice for this ASCII-only, unaccented use case.
- **No new CLI validation**: `--shout` combined with any input is always
  valid; there's no error state to add, consistent with the requirements'
  explicit exclusion of new input validation.
