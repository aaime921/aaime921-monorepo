import argparse
import sys
from datetime import datetime


def greet(
    name: str | None,
    shout: bool = False,
    now: datetime | None = None,
    timestamp: bool = True,
) -> str:
    """Return the greeting string for `name` (or the "world" default),
    with the current timestamp appended in brackets.

    `now` defaults to the real current time (`datetime.now()`) when not
    supplied; pass a fixed `datetime` to get a deterministic, exactly
    assertable result (used by tests).

    If `shout` is True, the returned string is upper-cased. If `timestamp`
    is False, the bracketed timestamp is omitted entirely (and `now` is
    ignored).
    """
    greeting = f"Hello {name or 'world'}!"
    if timestamp:
        moment = now if now is not None else datetime.now()
        greeting += f" [{moment.strftime('%Y-%m-%d %H:%M:%S')}]"
    return greeting.upper() if shout else greeting


def main(argv: list[str] | None = None) -> int:
    """Parse argv, print the greeting, return process exit code (0)."""
    parser = argparse.ArgumentParser(description="Print a greeting.")
    parser.add_argument("name", nargs="?", default=None, help="Name to greet")
    parser.add_argument(
        "--shout",
        action="store_true",
        help="Print the greeting in all caps",
    )
    parser.add_argument(
        "--no-timestamp",
        action="store_true",
        help="Omit the trailing timestamp from the greeting",
    )
    args = parser.parse_args(argv)
    print(greet(args.name, shout=args.shout, timestamp=not args.no_timestamp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
