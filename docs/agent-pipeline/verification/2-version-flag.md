# Verification: `--version` flag for the CLI

Issue: #2
Requirements: [`docs/agent-pipeline/requirements/2-version-flag.md`](../requirements/2-version-flag.md)
Architecture: [`docs/agent-pipeline/architecture/2-version-flag.md`](../architecture/2-version-flag.md)
PR: #3 (`issue-2-version-flag` → `main`, commit `fb05f8a`)

## Test suite

Ran the full suite from the repo root against the PR branch:

```sh
python3 -m pytest projects/agent-pipeline/tests/ -v
```

Result: **11 passed** (7 pre-existing + 4 new: `test_cli_version`,
`test_cli_version_takes_precedence_over_name`, `test_cli_help_documents_version`,
plus the pre-existing `TestGreet`/`TestCli` cases).

Also re-ran via the project's own documented command
(`python3 -m unittest discover -s tests -v` from `projects/agent-pipeline/`)
to confirm AC7's "single documented command" claim independently of pytest:
same 11 tests, all pass.

## Acceptance criteria — verified individually

Beyond re-running the Developer's own tests, each criterion was independently
exercised by invoking `src/hello.py` directly (not just asserted via the
existing test file), including an extra precedence check in the opposite
argument order that the Developer's tests didn't cover.

| # | Criterion | Verification | Result |
|---|-----------|---------------|--------|
| 1 | `--version` alone prints semver (`1.0.0`) to stdout | `python3 src/hello.py --version` → `1.0.0` | ✅ Pass |
| 2 | Exits 0 on `--version` | Same command, exit code `0` | ✅ Pass |
| 3 | `--version` doesn't require `name`, doesn't print a greeting; takes precedence regardless of order | `--version Bob` → `1.0.0` (exit 0); `Bob --version` → `1.0.0` (exit 0) — checked **both** orders | ✅ Pass |
| 4 | `--help` lists `--version` with a description | `python3 src/hello.py --help` → lists `--version  Print the version number and exit` | ✅ Pass |
| 5 | Existing behavior (greeting, `--shout`) unchanged | `hello.py` (no args) → `Hello, world!`; `hello.py Alice` → `Hello, Alice!`; `--shout` and `--shout Alice` → `HELLO, WORLD!` / `HELLO, ALICE!` — all match issues #1/#3 behavior; all 4 pre-existing `TestCli`/`TestGreet` tests pass unmodified | ✅ Pass |
| 6 | At least one automated test verifies `--version` (criteria 1-3) | 3 new tests added, covering AC1-3 and AC4 | ✅ Pass |
| 7 | All tests runnable via a single documented command, passing | Both `python3 -m unittest discover -s tests -v` (README/architecture-documented) and `pytest projects/agent-pipeline/tests/` pass with 11/11 | ✅ Pass |

## Code review notes

- `greet()` is untouched; `--version` is wired entirely through argparse's
  built-in `action="version"`, matching the architecture doc.
- `README.md` documents the new flag with an example invocation, consistent
  with the requirements' AC4.
- No changes outside `projects/agent-pipeline/` (scope correctly ringfenced
  per `CLAUDE.md`).

## Verdict

**All 7 acceptance criteria pass.** No regressions found. PR #3 approved.
