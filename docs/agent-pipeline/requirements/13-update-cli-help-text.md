# Requirements: Update CLI help text for timestamp feature

Issue: #13

## Summary

The BO wants the existing hello-world CLI's `--help` output updated to
mention that the greeting now includes a timestamp. This is a documentation
change to the help text only — it does not change the CLI's runtime
behavior (greeting, `--shout`, or the timestamp itself).

## Scope

- The CLI's `--help` output includes text communicating that a timestamp is
  shown alongside the greeting. Per the BO's literal wording in the issue,
  the help output must include the phrase **"Timestamps are shown with
  greeting"** verbatim (consistent with how issue #8's requirements doc
  treated the BO's literal example text as authoritative rather than
  something to paraphrase).
- Exact placement of this text within the help output (parser
  `description`, an `epilog`, or elsewhere) is an implementation detail for
  the Architect/Developer.
- `--help` continues to list every other command-line argument/flag the CLI
  currently supports on `main` at implementation time (at minimum the
  `name` positional argument and `--shout`, per issues #1 and #3), each
  with its existing short description.
- `--help` continues to work on its own, with no other arguments required,
  and continues to exit with a success (zero) exit code without printing a
  greeting (unchanged from issue #5).
- When `--help` is not passed, existing CLI behavior (greeting, `--shout`)
  is unchanged by this issue.

### Out of scope

- Any change to the greeting's runtime behavior, the timestamp's format, or
  any flag's behavior — this issue only changes `--help` output text.
- Implementing or modifying the timestamp feature itself (that's issue #8).
- Any new flags beyond what already exists.

## Acceptance criteria

1. Running the CLI with `--help` prints help text containing the exact
   phrase "Timestamps are shown with greeting", and exits with a success
   (zero) exit code.
2. The `--help` output still lists every other argument/flag present on
   `main` at implementation time (at minimum `name` and `--shout`), each
   with a short description, unchanged from issue #5's requirement.
3. `--help` still requires no other arguments and still does not print a
   greeting.
4. Running the CLI without `--help` (default greeting, `--shout`) behaves
   exactly as before this issue.
5. At least one automated test verifies the `--help` output contains the
   required timestamp-related text.
6. All automated tests (existing and new) can be run via the single
   documented command (`python3 -m unittest discover -s tests`, per the
   project README) and pass.

## Open questions

None blocking, but one dependency the Architect/Developer must account for:
as of this writing, the actual timestamp feature (issue #8) has **not**
merged into `main` — its implementation sits in still-open PR #11
(`issue-8-timestamp-in-hello-output`). This issue's help-text change would
therefore describe a feature (`[YYYY-MM-DD HH:MM:SS]` appended to the
greeting) that isn't present in `hello.py` on `main` yet.

This doesn't block writing requirements here — the BO explicitly asked for
this help-text wording regardless. But the Developer implementing this
issue should either implement after PR #11 merges, or otherwise confirm
with the Team Lead/BO that landing the help text ahead of the feature it
describes is acceptable, so the CLI never advertises a timestamp that its
current `--help` run won't actually produce. If PR #11 still hasn't merged
when this issue reaches `stage:dev`, the Developer should add
`blocked:dependency` and name PR #11, per the cross-issue dependency
protocol in `docs/agent-pipeline/PIPELINE.md`.
