# 64: `--no-timestamp` flag for hello CLI

## Summary
The BO wants a `--no-timestamp` option on the hello CLI (`projects/agent-pipeline/src/hello.py`)
so the greeting can be printed without the trailing `[YYYY-MM-DD HH:MM:SS]`. This is also a
smoke test of the slimmed pipeline routines (AND-6); the change itself is deliberately small.

## Scope
- In: new `--no-timestamp` command-line flag; works alone and combined with `--shout`.
- In: tests and `--help` text covering the new flag, consistent with existing flags.
- Out: any change to default output, the timestamp format, or other flags.

## Acceptance criteria
1. With no flags, output is unchanged: `Hello <name|world>! [YYYY-MM-DD HH:MM:SS]`.
2. With `--no-timestamp`, output is `Hello <name|world>!` with no bracketed timestamp and no trailing space.
3. With `--no-timestamp --shout` (either order), output is the same greeting upper-cased, with no timestamp: `HELLO WORLD!`.
4. `--shout` alone still upper-cases the greeting and keeps the timestamp.
5. `--no-timestamp` works with and without a positional name.
6. `--help` lists `--no-timestamp` with a short description.
7. Exit code is 0 in all the cases above.
8. Automated tests cover criteria 1-5 and pass.

## Open questions
None.

## Notes for next stage
- Pipeline smoke test: each of Architect, Developer and QA should run exactly once; no other routine should fire.
