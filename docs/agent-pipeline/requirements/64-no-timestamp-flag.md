# 64: `--no-timestamp` flag for hello CLI

## Summary
The BO wants a `--no-timestamp` flag on the hello CLI (`projects/agent-pipeline/src/hello.py`)
so the greeting can be printed without the trailing `[YYYY-MM-DD HH:MM:SS]`. This issue is also
a routine smoke test (AND-6) and may be discarded.

## Scope
- In: new `--no-timestamp` option on the hello CLI; works together with `--shout`.
- Unchanged: default behavior (timestamp shown) and all existing flags.
- Out: changing the timestamp format; any other flag.

## Acceptance criteria
1. Without `--no-timestamp`, output is identical to today (`Hello <name>! [YYYY-MM-DD HH:MM:SS]`).
2. With `--no-timestamp`, output is the greeting only, with no bracketed timestamp
   and no trailing space (e.g. `Hello world!`, `Hello Ann!`).
3. `--no-timestamp --shout` (either order) prints the greeting upper-cased with no timestamp
   (e.g. `HELLO WORLD!`).
4. `--shout` alone still prints the upper-cased greeting with timestamp.
5. Exit code is 0 in all cases above.
6. The new flag appears in `--help` output, if the CLI's help lists flags.
7. Existing tests keep passing; new tests cover criteria 2 and 3.

## Open questions
None blocking.
