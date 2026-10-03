import argparse
import sys


def greet(name: str | None, shout: bool = False) -> str:
    """Return the greeting string for `name` (or the "world" default).

    If `shout` is True, the returned string is upper-cased.
    """
    greeting = f"Hello, {name or 'world'}!"
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
    args = parser.parse_args(argv)
    print(greet(args.name, shout=args.shout))
    return 0


if __name__ == "__main__":
    sys.exit(main())
