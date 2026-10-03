# Requirements: `--help` flag for the CLI

Issue: #5

## Summary

The BO wants the hello-world CLI (issue #1, extended by issue #3's `--shout`
flag) to have a working `--help` flag that lists all available
commands/options and includes usage examples. This issue is explicitly
framed as a fresh smoke test of the BA→QA pipeline flow, so scope is kept
small and builds directly on the CLI's existing behavior.

## Scope

- `--help` (and the conventional short form `-h`) prints a help message to
  standard output and exits successfully, instead of performing the normal
  greeting behavior.
- The help message documents every argument the CLI currently accepts: the
  optional `name` positional argument and the `--shout` flag (the only
  arguments present in `projects/agent-pipeline/src/hello.py` as of this
  writing). If additional arguments exist by the time this is implemented,
  the help message must document those too.
- The help message includes at least one concrete usage example showing how
  to invoke the CLI (e.g. with a name, and with `--shout`).
- Invoking the CLI without `--help` is unaffected — existing greeting
  behavior (from issues #1 and #3) is unchanged.
- At least one automated test covering the `--help` behavior.

### Out of scope

- Adding any new flags beyond what already exist on the CLI (e.g. a
  `--version` flag is tracked separately in issue #2 and its own
  requirements doc; not in scope here).
- Changing the existing greeting or `--shout` behavior.
- Localization/internationalization of the help text.

## Acceptance criteria

1. Running the CLI with `--help` (or `-h`) and no other arguments prints a
   help message to standard output and exits with a success (zero) exit
   code, without printing a greeting.
2. The help message lists every argument the CLI currently accepts (`name`,
   `--shout`) along with a brief description of what each does.
3. The help message includes at least one concrete usage example
   demonstrating how to invoke the CLI.
4. Running the CLI without `--help` behaves exactly as specified in issues
   #1 and #3, unchanged by this feature.
5. At least one automated test verifies criteria 1-3.
6. All automated tests (existing and new) can be run via a single documented
   command and pass in this environment.

## Open questions

None blocking. Note for the Architect/Developer: the CLI already uses
Python's `argparse`, which auto-generates a basic `-h`/`--help` output
listing registered arguments — so part of this requirement (criteria 1-2)
may already be satisfied by the existing parser setup. The main gap is
likely the usage-examples requirement (criterion 3), since `argparse` does
not include worked examples by default. How that gap is closed (e.g. an
epilog, a custom formatter, etc.) is an implementation detail left to the
Architect/Developer, not specified here.
