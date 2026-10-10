# Requirements: Add timestamp to hello output

Issue: #8

## Summary

The BO wants the CLI's greeting output to include the current timestamp,
appended to the existing greeting text, formatted as `YYYY-MM-DD HH:MM:SS`.
For example, `hello Alice` should print something like
`Hello, Alice! [2026-10-03 13:30:00]` using the actual current time at the
moment the command runs. This builds directly on the existing hello-world CLI
(issue #1) and its `--shout` flag (issue #3); scope is kept to adding the
timestamp only.

## Scope

- Append the current timestamp to the greeting the CLI already prints,
  formatted as `YYYY-MM-DD HH:MM:SS` and wrapped in square brackets (e.g.
  `[2026-10-03 13:30:00]`), separated from the greeting by a single space.
- The existing greeting text and punctuation (`Hello, <name>!` / `Hello,
  world!`) are unchanged — the timestamp is appended, not a replacement for
  any part of the existing format. (The issue's own example shows `Hello
  Alice!` without the comma used by the existing CLI; see "Open questions"
  below — this doc assumes the existing comma-formatted greeting is kept
  as-is and only the timestamp is new.)
- The timestamp reflects the actual current date/time when the command is
  run (not a fixed/mocked value) in both normal and `--shout` mode.
- Works whether a name argument is supplied or omitted (defaulting to
  "world"), and combines correctly with the existing `--shout` flag from
  issue #3 (the timestamp's digits/punctuation are unaffected by
  upper-casing, so no special-casing is needed there).
- At least one automated test covering the timestamp being present and
  correctly formatted, in addition to existing tests for the greeting text
  itself.

### Out of scope

- Any new flags, options, or configuration for the timestamp (e.g. a flag to
  disable it, or to choose a different format).
- Changing the wording of the greeting itself beyond appending the
  timestamp.
- Timezone configuration/selection beyond whatever the system's local time
  is (see "Open questions").
- Localization of the date/time format.

## Acceptance criteria

1. Running the CLI with a name argument (e.g. `<tool> Alice`) prints the
   existing greeting for that name, followed by a space and the current
   timestamp in `[YYYY-MM-DD HH:MM:SS]` format, e.g.
   `Hello, Alice! [2026-10-03 13:30:00]`.
2. Running the CLI with no name argument prints the existing default
   greeting ("world"), followed by a space and the current timestamp in the
   same `[YYYY-MM-DD HH:MM:SS]` format.
3. Running the CLI with `--shout` produces the same greeting-plus-timestamp
   behavior, with the greeting text upper-cased exactly as issue #3 already
   specifies; the timestamp's format is unaffected by `--shout`.
4. The timestamp reflects the current date/time at the moment the command
   runs, accurate to the second (i.e. not a hard-coded or stale value).
5. The tool exits with a success (zero) exit code in all of the above cases.
6. At least one automated test verifies the output contains a correctly
   formatted `[YYYY-MM-DD HH:MM:SS]` timestamp (e.g. via pattern/regex
   matching rather than an exact string match, since the value changes every
   run) for both the named and default-name cases.
7. All automated tests (existing and new) can be run via a single documented
   command and pass in this environment.

## Open questions

Non-blocking — defaults are assumed below so the Architect/Developer can
proceed; flag back to the BO only if either assumption turns out to matter:

- **Comma in the greeting text**: the issue's example (`Hello Alice!
  [2026-10-03 13:30:00]`) omits the comma that the existing CLI's greeting
  uses (`Hello, Alice!`). This doc assumes that's incidental phrasing in the
  issue, not a request to drop the comma, since the issue only asks to add a
  timestamp and doesn't mention changing the greeting wording. If the BO
  actually wants the comma removed, that's a one-line follow-up.
- **Timezone**: the issue doesn't specify local time vs. UTC. This doc
  assumes the system's local time (whatever `now()` returns in the runtime
  environment) is acceptable, since no timezone requirement was given and
  nothing in the request suggests the output is consumed across timezones.
