
# Architecture: `--version` flag for the CLI

Issue: #2
Requirements: [`docs/agent-pipeline/requirements/2-version-flag.md`](../requirements/2-version-flag.md)

## Approach

Use argparse's built-in `action="version"` to implement `--version`, rather
than a hand-rolled `if args.version:` branch. Add a module-level `VERSION`
constant in `src/hello.py` and register it as the `version` string for a new
`--version` argument with `action="version"`.

This is the simplest option that satisfies every acceptance criterion for
free, because of how argparse's version action behaves:

- It prints the given `version` string to stdout and calls `parser.exit()`
  (exit code 0) **the moment the token is encountered during parsing** —
  before the rest of argv is processed. That means `--version` together with
  a name argument (AC2) still prints only the version, regardless of
  argument order, with no extra code to enforce precedence.
- It requires no name argument to be present (AC1) since it short-circuits
  parsing entirely.
- It's automatically listed in `--help` with the description we give it
  (AC4) — argparse adds it to the options list like any other flag.
- It needs no changes to `greet()` or the existing greeting code path, so
  AC5 (unchanged behavior when `--version` is absent) holds by construction.

The alternative (a manual `if args.version: print(...); return 0` check
before calling `greet()`) would require explicit ordering logic to guarantee
precedence over the name argument and would duplicate exit-code handling
that argparse already does correctly. It's not chosen.

## Affected components/files

- `src/hello.py` (existing, from issue #1, extended by issue #3) — add a
  `VERSION` constant and a `--version` argument in `main()`. No change to
  `greet()`.
- `tests/test_hello.py` (existing) — add CLI-level tests for `--version`.
- `README.md` — extend the existing "Hello World CLI" section to document
  `--version`.
- No new files, no dependency changes.

No cross-issue dependency: `src/hello.py` and `--shout` are already merged
to `main`, so this design is written directly against `main`'s current
content.

## Interfaces/contracts

`src/hello.py`:

```python
VERSION = "1.0.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a greeting.")
    parser.add_argument("name", nargs="?", default=None, help="Name to greet")
    parser.add_argument(
        "--shout",
        action="store_true",
        help="Print the greeting in all caps",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=VERSION,
        help="Print the version number and exit",
    )
    args = parser.parse_args(argv)
    print(greet(args.name, shout=args.shout))
    return 0
```

- `VERSION` is a plain module-level string constant (`"1.0.0"`), not derived
  from packaging metadata — there is no package manifest for this script
  today, and requirements explicitly put packaging/distribution metadata out
  of scope.
- `version=VERSION` (not `version="%(prog)s " + VERSION`) so the printed
  output is exactly the semantic version string with no program-name
  prefix, matching the requirements' example (`1.0.0`) literally.
- No change to `greet()`'s signature or behavior.

## Task breakdown

1. In `src/hello.py`, add `VERSION = "1.0.0"` as a module-level constant,
   near the top of the file.
2. In `main()`, add the `--version` argument as shown above, using
   `action="version"` and `version=VERSION`.
3. In `tests/test_hello.py`, add a new `TestCli` test:
   - running `[sys.executable, SCRIPT, "--version"]` asserts exit code 0
     and stdout `"1.0.0\n"`.
4. Add a second `TestCli` test covering precedence:
   - running `[sys.executable, SCRIPT, "--version", "Alice"]` (name present)
     asserts the same exit code 0 and stdout `"1.0.0\n"`, not a greeting.
5. Update the "Hello World CLI" section in `README.md` to mention
   `--version` and give one example invocation and its output.
6. Run `python3 -m unittest discover -s tests -v` and confirm all tests
   (existing + new) pass.

## Test strategy notes

- CLI/integration-level only, consistent with how `--shout` was tested:
  subprocess-run the script with `--version` (alone, and with a name
  argument) and assert exact stdout (`"1.0.0\n"`) and a zero exit code.
  This directly covers AC1, AC2, and AC3.
- No unit-level test is needed for a `greet()`-style pure function here,
  since `--version` deliberately bypasses `greet()` entirely — the
  behavior under test is argparse's own exit action wired to our constant,
  which is only observable at the CLI/subprocess level.
- `--help` coverage (AC4) can be checked with a quick subprocess run of
  `[sys.executable, SCRIPT, "--help"]` asserting `"--version"` appears in
  stdout; add this as a third new `TestCli` test if not already covered.
- Regression: all existing tests (greeting and `--shout`, unit and CLI)
  must keep passing unmodified, covering AC5.

## Risks/tradeoffs

- **Hardcoded version string vs. packaging metadata**: `VERSION` lives as a
  plain constant in `src/hello.py` rather than being read from a
  `pyproject.toml`/`importlib.metadata` source. This matches the
  requirements' explicit exclusion of packaging/distribution metadata and
  keeps the change minimal; if this project later adds real package
  metadata, `VERSION` should be sourced from it instead to avoid two
  diverging version strings; that's a future issue, not this one.
- **argparse's version action exits immediately**: this is the desired
  behavior (AC2/AC3), but it does mean `--version` silently ignores
  malformed values for other arguments that would otherwise raise a parse
  error — e.g. `--version --bogus-flag` still just prints the version and
  exits 0, because the version action fires before `--bogus-flag` would be
  rejected. This is standard argparse behavior for `--version`/`--help` and
  not a new risk introduced by this design; not worth guarding against for
  this smoke-test CLI.
- **No prog-name prefix**: choosing `version=VERSION` over
  `version="%(prog)s " + VERSION` means the output won't say which program
  the version belongs to. Acceptable here since the CLI has one entry
  point and the requirements' example output is the bare version string.
