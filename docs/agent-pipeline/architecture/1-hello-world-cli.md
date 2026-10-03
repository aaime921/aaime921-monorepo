# Architecture: Hello World CLI Greeting

Issue: #1
Requirements: [`docs/requirements/1-hello-world-cli.md`](../requirements/1-hello-world-cli.md)

## Approach

Implement as a single-file Python 3 script using only the standard library
(`sys` for argv, `argparse` for argument parsing). Python is preinstalled in
essentially every environment this pipeline runs in, needs no build step or
package manager, and its built-in `unittest` module covers the testing
requirement with zero external dependencies. Given the trivial, smoke-test
scope described in the requirements, there is no reason to introduce a
framework, packaging metadata, or a compiled language — the simplest thing
that satisfies the acceptance criteria is correct here.

The script exposes a small `greet(name: str | None) -> str` function separate
from the CLI entry point, so tests can call the pure function directly
instead of shelling out to a subprocess. The entry point wires argv parsing
to `greet()` and prints the result.

## Affected components/files

This is a new feature in an otherwise empty repo (only `README.md` and
`docs/` exist so far). New files:

- `src/hello.py` — the CLI script (new).
- `tests/test_hello.py` — automated tests (new).
- `README.md` — add a short "Hello World CLI" usage section documenting the
  run and test commands (update).

No existing components are affected.

## Interfaces/contracts

`src/hello.py`:

```python
def greet(name: str | None) -> str:
    """Return the greeting string for `name` (or the "world" default)."""
    ...

def main(argv: list[str] | None = None) -> int:
    """Parse argv, print the greeting, return process exit code (0)."""
    ...

if __name__ == "__main__":
    sys.exit(main())
```

- `greet(None)` returns `"Hello, world!"`.
- `greet("Alice")` returns `"Hello, Alice!"`.
- CLI usage: `python3 src/hello.py [NAME]`.
  - Zero or one positional argument. Argparse handles the "too many
    arguments" case by erroring out (not specified by requirements, but
    argparse's default behavior is acceptable since multi-argument handling
    is explicitly out of scope).
- `main()` prints `greet(name)` followed by a newline to stdout via `print()`
  and returns `0` on the two in-scope paths (acceptance criterion 3).

## Task breakdown

1. Create `src/hello.py` with `greet()`, `main()`, and the `if __name__ ==
   "__main__"` guard, using `argparse` with one optional positional `name`
   argument (default `None`).
2. Create `tests/test_hello.py` using `unittest`, with at least:
   - a test asserting `greet("Alice") == "Hello, Alice!"` (acceptance
     criterion 1),
   - a test asserting `greet(None) == "Hello, world!"` (acceptance
     criterion 2).
3. Add a short section to `README.md` documenting:
   - Run command: `python3 src/hello.py [NAME]`.
   - Test command: `python3 -m unittest discover -s tests` (single
     documented command per acceptance criterion 5).
4. Manually run both commands locally to confirm the exact output strings
   and zero exit codes match acceptance criteria 1–3, and that the test
   command passes.

## Test strategy notes

- Unit tests (in `tests/test_hello.py`) should call `greet()` directly for
  the two acceptance-criteria cases (with name, without name) — this is
  sufficient to cover criteria 1, 2, and 4 without the overhead of spawning
  a subprocess.
- A manual/CLI-level check (or an additional test using `subprocess.run`, at
  the Developer's discretion) should confirm the process exit code is 0 and
  that stdout matches exactly, satisfying criterion 3 end-to-end — this can
  be a manual step noted in the PR rather than a required automated test,
  since the pure-function tests already cover the logic.
- No edge cases beyond "name given" / "no name given" are in scope (per
  requirements' "Out of scope" section), so no tests for flags, multiple
  arguments, or special characters are needed.

## Risks/tradeoffs

- Using `argparse` pulls in slightly more ceremony than manually inspecting
  `sys.argv`, but it correctly handles the zero-or-one-argument contract and
  gives sane `--help` behavior for free, at negligible complexity cost.
- No packaging (`pyproject.toml`/`setup.py`) is included since distribution
  is explicitly out of scope; the script is run directly with `python3`.
- Python was chosen over other "simple" options (e.g. a shell script or Go
  binary) because it minimizes environment setup risk (no compilation step)
  while keeping the test story trivial (stdlib `unittest`, no test runner
  install).
