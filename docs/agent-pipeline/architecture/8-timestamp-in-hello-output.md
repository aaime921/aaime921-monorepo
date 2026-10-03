# Architecture: Timestamp in hello output

Issue: #8
Requirements: [`docs/agent-pipeline/requirements/8-timestamp-in-hello-output.md`](../requirements/8-timestamp-in-hello-output.md)

## Approach

Add the current timestamp to the string `greet()` already builds in
`src/hello.py`, by:

1. Dropping the comma from the existing `f"Hello, {name or 'world'}!"`
   format, per the requirements doc's resolution of the BO's literal
   example (`Hello Alice!`, no comma).
2. Appending `" [YYYY-MM-DD HH:MM:SS]"` — the current local time, read at
   call time — to that greeting.
3. Accepting an optional `now: datetime | None = None` parameter that
   defaults to `datetime.now()` when not supplied.

`greet()` stays the single place that owns the full greeting string,
consistent with how `--shout` was added (issue #3): `shout` still
upper-cases the *entire* formatted string, including the timestamp. This is
a no-op on the timestamp's characters (digits, `-`, `:`, space, brackets all
have no case), so shout + timestamp compose with no special-casing needed.

The `now` parameter is the one addition beyond "just call `datetime.now()`
inline": it keeps `greet()` a deterministic, directly-testable pure function
when a caller supplies a fixed time, while `main()` (and any caller that
omits `now`) still gets real current time with no extra wiring. Without it,
every test of the timestamped output would have to parse-and-compare a
moving target with a regex instead of being able to assert an exact string
when a fixed instant is useful. The requirements doc doesn't mandate this,
but it's a small, local change that makes the acceptance-criteria tests
easier to write precisely — see "Test strategy notes" below for how both
styles (regex on real time, exact match on injected time) are expected to
be used together.

## Affected components/files

- `src/hello.py` (existing, on `main` as of this design — currently has
  `greet()`/`main()` with the `--shout` flag from issue #3 merged;
  `--version`/`--help` from issues #2/#5 are designed but not yet
  implemented on `main`) — modify `greet()` only. No change to `main()`'s
  argument parsing.
- `tests/test_hello.py` (existing) — the four existing tests assert the
  old `"Hello, Alice!"` / `"Hello, world!"` / `"HELLO, ALICE!"` /
  `"HELLO, WORLD!"` strings; all four must be updated for the dropped comma
  and the new bracketed timestamp, per the requirements doc's acceptance
  criteria. This is an intentional behavior change to existing output, not
  a regression — flagged explicitly here since it touches tests the
  Developer didn't write.
- `README.md` — update the "Hello World CLI" example output to match the
  new format.
- No new files, modules, or dependencies (`datetime` is stdlib).

## Interfaces/contracts

`src/hello.py`:

```python
from datetime import datetime


def greet(name: str | None, shout: bool = False, now: datetime | None = None) -> str:
    """Return the greeting string for `name` (or the "world" default),
    with the current timestamp appended in brackets.

    `now` defaults to the real current time (`datetime.now()`) when not
    supplied; pass a fixed `datetime` to get a deterministic, exactly
    assertable result (used by tests).

    If `shout` is True, the returned string is upper-cased.
    """
    moment = now if now is not None else datetime.now()
    timestamp = moment.strftime("%Y-%m-%d %H:%M:%S")
    greeting = f"Hello {name or 'world'}! [{timestamp}]"
    return greeting.upper() if shout else greeting
```

`main()` is unchanged: it still calls `greet(args.name, shout=args.shout)`
with no `now` argument, so the CLI always gets the real current time.

Output shape (acceptance criteria 1–3):

```
Hello Alice! [2026-10-03 13:30:00]
Hello world! [2026-10-03 13:30:00]
```

## Task breakdown

1. In `src/hello.py`, add `from datetime import datetime` and the `now`
   parameter to `greet()`; change the format string to drop the comma and
   append `" [{timestamp}]"` using `strftime("%Y-%m-%d %H:%M:%S")`, per the
   contract above.
2. In `tests/test_hello.py`, update `TestGreet`:
   - `test_greet_with_name`: pass a fixed `now=datetime(2026, 1, 2, 3, 4, 5)`
     and assert the exact string `"Hello Alice! [2026-01-02 03:04:05]"`.
   - `test_greet_without_name`: same pattern, asserting
     `"Hello world! [2026-01-02 03:04:05]"`.
   - `test_greet_with_name_shout` / `test_greet_without_name_shout`: same,
     asserting the upper-cased form, e.g.
     `"HELLO, ALICE!".upper()` → confirm it's actually
     `"HELLO ALICE! [2026-01-02 03:04:05]"` (no comma).
   - Add one new test, e.g. `test_greet_default_now_matches_pattern`, that
     calls `greet("Alice")` with no `now` argument and asserts the result
     matches `r"^Hello Alice! \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]$"` via
     `re.match` — this is what actually exercises the "real system clock,
     not hardcoded" part of acceptance criterion 3.
3. In `tests/test_hello.py`, update `TestCli`'s four existing subprocess
   tests: since the CLI always uses real time (no way to inject `now`
   through argv), change their assertions from exact-string equality to a
   regex match against
   `r"^Hello Alice! \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\n$"` (and the
   `world` / `HELLO` variants), covering acceptance criteria 1, 2, 4, and 5.
4. Update the "Hello World CLI" section of `README.md`: change the example
   output lines to show the bracketed timestamp and the dropped comma.
5. Run `python3 -m unittest discover -s tests -v` and confirm all tests
   (existing, updated, and new) pass.

## Test strategy notes

- Unit-level (`TestGreet`): inject a fixed `now` and assert exact strings —
  this is strictly stronger than a regex and catches format mistakes a
  pattern match would miss (e.g. wrong separator, swapped comma/bracket
  order). Covers acceptance criteria 1–3 precisely.
- One unit test (`greet()` called with no `now`) is required to assert
  against the regex pattern, not an exact value, since it genuinely reads
  the system clock — this is the test that actually proves criterion 3
  ("reflects the real current date/time ... not hardcoded").
- CLI/integration-level (`TestCli`): must use the regex approach throughout,
  since the subprocess always uses real time and there's no argv-level way
  to inject `now` (and the requirements explicitly rule out adding one —
  see "Out of scope": no new flags/configurability). Covers acceptance
  criteria 1, 2, 4, 5.
- Regression: no previously-passing *behavior* is preserved byte-for-byte
  (the comma removal is an intentional, requirements-mandated change to
  existing output), so the four existing tests are updated, not left alone
  — this is expected and should not be treated as a regression by QA.
- No new test infrastructure, fixtures, or dependencies; `datetime` and
  `re` are both stdlib.

## Risks/tradeoffs

- **Comma removal changes previously-shipped output.** Issues #1 and #3
  shipped `"Hello, Alice!"` (with comma); this issue's requirements doc
  takes the BO's literal example as authoritative and drops it. If that
  was actually a typo rather than an intentional change (flagged as a
  non-blocking open question in the requirements doc), the fix is a
  one-line revert of the format string — isolated entirely to the f-string
  in `greet()`, no structural impact on this design.
- **`now` parameter is additive, not required by the requirements doc.**
  It's included because it makes exact-match unit tests possible instead
  of relying on regex everywhere, which is easier to get subtly wrong
  (e.g. a pattern that's too loose). If a reviewer prefers the simpler
  `datetime.now()`-inline version with no injection point, that's a valid
  one-line simplification at the cost of unit tests only being able to
  regex-match, same as the CLI tests already must.
- **No timezone handling**, per requirements' explicit "out of scope" —
  `datetime.now()` uses local system time with no `tzinfo`. If the CI
  environment's clock/timezone is misconfigured, tests would still pass
  (they check format, not absolute value) but the BO-facing output could
  show an unexpected local time. Not a design concern for this issue.
- **Pre-existing gap, not introduced by this design**: `--version` and
  `--help` (issues #2, #5) have architecture docs but aren't yet
  implemented on `main`. This design only touches `greet()`'s formatting
  and is independent of those flags; the Developer doesn't need them
  merged first, but should be aware `main.py`'s current flag set is just
  `name` + `--shout`.
