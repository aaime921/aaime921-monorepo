# Requirements: `--help` flag for the CLI

Issue: #5

## Summary

The BO wants a `--help` flag added to the existing hello-world CLI (from
issue #1, extended by issue #3's `--shout` flag) that shows all of the
CLI's available command-line options and includes usage examples. This is
a pipeline smoke test (per the issue's stated purpose: "Fresh test of
BA→QA pipeline flow"), so scope is kept small and builds directly on the
existing CLI.

## Scope

- A `--help` flag on the existing CLI that, when passed, prints help text
  to standard output and exits successfully, instead of performing the
  tool's normal greeting behavior.
- The help text lists every command-line argument/flag the CLI currently
  supports, each with a short description of what it does. As of this
  writing that is the `name` positional argument and the `--shout` flag
  (see `docs/agent-pipeline/requirements/1-hello-world-cli.md` and
  `docs/agent-pipeline/requirements/3-shout-flag.md`); if any further flag
  (e.g. `--version` per issue #2) has landed on the CLI by the time this is
  implemented, it must be listed too.
- The help text includes at least one concrete usage example showing how
  to invoke the CLI (e.g. with a name, and with `--shout`).
- `--help` works on its own, with no other arguments present (i.e. the
  `name` positional argument is not required when `--help` is used).
- When `--help` is not passed, the CLI's existing behavior (greeting, with
  or without `--shout`, and any other flags already present) is unchanged.
- At least one automated test covering the `--help` behavior.

### Out of scope

- Any new functional flags or options beyond `--help` itself.
- Changing the default greeting behavior or any existing flag's behavior.
- The specific mechanism used to produce the help text (e.g. relying on
  the argument-parsing library's built-in help vs. a custom implementation)
  -- that's an implementation detail for the Architect/Developer, not a
  requirement.
- Localization/translation of help text.

## Acceptance criteria

1. Running the CLI with `--help` and no other arguments prints help text
   to standard output and exits with a success (zero) exit code, without
   printing a greeting.
2. The help text lists every argument/flag the CLI currently supports (at
   minimum the `name` positional argument and `--shout`), each with a
   short description of what it does.
3. The help text includes at least one usage example showing a concrete
   invocation of the CLI.
4. `--help` does not require the `name` positional argument.
5. Running the CLI without `--help` behaves exactly as specified in issues
   #1 and #3 (and any other already-landed flag requirements), unchanged
   by this feature.
6. At least one automated test verifies the `--help` behavior (criteria
   1-3).
7. All automated tests (existing and new) can be run via a single
   documented command and pass in this environment.

## Open questions

None blocking. One note for the Architect/Developer: issue #2 (`--version`)
is currently labeled `stage:done` in this repo, but the `--version` flag is
not present in `projects/agent-pipeline/src/hello.py` on `main` as of this
writing -- only `name` and `--shout` exist. Acceptance criterion 2 is
written against the CLI's actual flags at implementation time rather than
assuming `--version` will be present, so this doesn't block requirements
here, but the Developer should verify the current flag set before writing
the help text and tests rather than assuming this doc's examples are
exhaustive.
