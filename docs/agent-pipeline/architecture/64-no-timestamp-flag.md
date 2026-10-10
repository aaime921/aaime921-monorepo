# 64: Design, `--no-timestamp` flag

Requirements: `docs/agent-pipeline/requirements/64-no-timestamp-flag.md`.

## Approach
Add a keyword-only-style boolean `timestamp: bool = True` to `greet()` and build the
greeting from the base text, appending ` [ts]` only when enabled. Upper-casing is applied
after, so `--shout` composes with either setting. Argparse `store_true` flag
`--no-timestamp` (dest `no_timestamp`) is passed as `timestamp=not args.no_timestamp`.
Default call signature behavior is unchanged (criteria 1, 4).

## Affected files
- `projects/agent-pipeline/src/hello.py`: modify `greet()` and `main()`.
- `projects/agent-pipeline/tests/test_hello.py`: add tests.
- `projects/agent-pipeline/README.md`: add flag to usage if flags are listed there.

## Interfaces
```python
def greet(name: str | None, shout: bool = False, now: datetime | None = None,
          timestamp: bool = True) -> str
```
- `timestamp=False` returns `Hello <name|world>!` (no trailing space; `now` ignored).
- Docstring updated to mention `timestamp`.
- CLI: `--no-timestamp`, help: "Omit the trailing timestamp from the greeting".

## Tasks
- [ ] Add `timestamp` param to `greet()`; build `greeting = f"Hello {name or 'world'}!"`, append `f" [{ts}]"` only if `timestamp`.
- [ ] Add `--no-timestamp` argument in `main()` and pass through.
- [ ] Add tests (below); run `python -m unittest` in the tests dir as existing tests do.
- [ ] Update README usage if it lists flags.

## Test strategy
Follow existing `test_hello.py` style (unit tests on `greet`, subprocess tests for CLI):
- `greet` with `timestamp=False`: with name, without name, with `shout=True` -> exact strings (`Hello Ann!`, `HELLO WORLD!`).
- CLI: `--no-timestamp` with/without name, `--no-timestamp --shout` and `--shout --no-timestamp`: exact stdout equality, exit code 0.
- Regression: default and `--shout`-only outputs still match the timestamp regex (criteria 1, 4).
- Help: `--help` stdout contains `--no-timestamp` (criterion 6).

## Risks
Negative flag name vs. positive param: keep the mapping in one place (`main()`) to avoid
double negatives inside `greet()`. Otherwise low risk.
