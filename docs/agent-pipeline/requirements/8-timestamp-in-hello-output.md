# Requirements: Timestamp in hello output

Issue: #8

## Summary

The BO wants the existing hello-world CLI (from issue #1) to include the
current date and time alongside the greeting, so the output shows when the
greeting was produced. This is a small addition to existing, already-shipped
behavior (issues #1, #2, #3, #5), not a new feature area.

## Scope

- The greeting output includes the current timestamp at the moment the
  command runs, appended after the greeting text.
- Timestamp format: `YYYY-MM-DD HH:MM:SS` (4-digit year; 2-digit month, day,
  hour, minute, second; 24-hour clock; zero-padded), wrapped in square
  brackets, matching the BO's example: `Hello Alice! [2026-10-03 13:30:00]`.
- Applies whether a name argument is supplied or the default ("world") is
  used.
- The timestamp reflects the actual current date/time read at run time (the
  system clock) — it is not a fixed or hardcoded value.
- Existing CLI behavior from issues #1, #2, #3, and #5 (default greeting,
  `--version`, `--shout`, `--help`) continues to work; this issue only adds
  the timestamp to the greeting output.
- At least one automated test covering the timestamped output.
- All existing automated tests continue to pass.

### Out of scope

- Any way to disable, configure, or reformat the timestamp (e.g. a flag to
  turn it off, or an alternate format/timezone option) — the BO specified
  one fixed format with no mention of configurability.
- Timezone handling — the BO gave no timezone requirement; using the local
  system time is sufficient.
- Changes to `--version` or `--help` output beyond what's naturally required
  to keep them passing (the BO's request is about the greeting, not these
  flags).

## Acceptance criteria

1. Running the CLI with a name argument (e.g. `<tool> Alice`) prints output
   of the exact form `Hello Alice! [YYYY-MM-DD HH:MM:SS]`, where the
   bracketed value is the current timestamp at the moment the command ran.
2. Running the CLI with no name argument prints output of the exact form
   `Hello world! [YYYY-MM-DD HH:MM:SS]`, where the bracketed value is the
   current timestamp at the moment the command ran.
3. The timestamp string strictly matches the pattern `YYYY-MM-DD HH:MM:SS`
   (zero-padded, 24-hour clock, e.g. `2026-10-03 13:30:00`), and reflects the
   real current date/time rather than a fixed or hardcoded value.
4. The tool exits with a success (zero) exit code in both of the above
   cases.
5. At least one automated test verifies the output format for criterion 1 or
   2 (checking the greeting text and validating the timestamp against the
   `YYYY-MM-DD HH:MM:SS` pattern, rather than asserting an exact hardcoded
   timestamp value, since the timestamp changes on every run).
6. All automated tests — existing (issues #1, #2, #3, #5) and new — can be
   run via a single documented command and pass in this environment.

## Open questions

None blocking. One note for the Architect/Developer: the BO's example in the
issue (`Hello Alice! [2026-10-03 13:30:00]`) drops the comma used in the
existing greeting format (`Hello, Alice!`, per issue #1). This requirements
doc takes the BO's literal example as authoritative — the comma is dropped —
since it was given as an explicit "Output should be" sample, not something
left to inference. If this was actually a typo and the BO meant to keep the
comma, that's a one-line fix; it doesn't block design or implementation
either way.
