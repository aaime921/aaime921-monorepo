# Requirements: `--shout` flag for the CLI

Issue: #3

## Summary

The BO wants an optional `--shout` flag added to the existing hello-world CLI
(from issue #1). When the flag is passed, the greeting is printed in all
caps (e.g. `HELLO, ALICE!`) instead of the normal mixed-case greeting.
Without the flag, the CLI's existing behavior is unchanged. This is
explicitly the second smoke test of the automated pipeline, so scope is kept
small and builds directly on the existing greeting behavior.

## Scope

- An optional `--shout` flag on the existing CLI.
- When `--shout` is passed, the tool prints the greeting entirely in
  uppercase, including the name (whatever case the name was supplied in) and
  surrounding text/punctuation.
- When `--shout` is not passed, the CLI's existing behavior (as defined in
  `docs/requirements/1-hello-world-cli.md`) is unchanged.
- `--shout` works both when a name argument is supplied and when it is
  omitted (defaulting to "world").
- At least one automated test covering the shouted behavior, in addition to
  the existing tests for the unshouted behavior.

### Out of scope

- Any new flags, options, or greeting formats beyond `--shout`.
- Changing the default (non-`--shout`) greeting text or format.
- Partial/mixed capitalization, or any "shout level" other than fully
  upper-case.
- Internationalization/localization of the shouted greeting.
- Input validation, sanitization, or error handling beyond what issue #1
  already covers.

## Acceptance criteria

1. Running the CLI with `--shout` and a name argument (e.g.
   `<tool> --shout Alice`) prints exactly `HELLO, ALICE!` to standard output.
2. Running the CLI with `--shout` and no name argument prints exactly
   `HELLO, WORLD!` to standard output.
3. Running the CLI without `--shout` behaves exactly as specified in issue #1
   (e.g. `Hello, Alice!` / `Hello, world!`), unchanged by this feature.
4. The tool exits with a success (zero) exit code in all of the above cases.
5. At least one automated test verifies the `--shout` behavior with a name
   argument (criterion 1), and at least one automated test verifies the
   `--shout` behavior with no name argument (criterion 2).
6. All automated tests (existing and new) can be run via a single documented
   command and pass in this environment.

## Open questions

None blocking. The request is small and unambiguous; the flag's exact
placement relative to the name argument (before/after) and its parsing
mechanics are left to the Architect/Developer as implementation details, not
requirements.
