"""
trainiq.export.fmt — formatting helpers shared by every export renderer
(issue #71).

Byte-stable by construction (AC 11/12 determinism): fixed decimal
precision, no locale-dependent formatting, `None` always renders the same
literal (`-`) rather than being invented as 0 or omitted.
"""

from __future__ import annotations

from typing import Optional, Sequence


def fmt(value, nd: int = 1) -> str:
    """`None` -> `-` (never fabricated, per the BA doc's AC 3). A float
    renders at a fixed `nd` decimals; everything else (int, str) via
    `str()`."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{nd}f}"
    return str(value)


def fmt_pace(seconds_per_km: Optional[float]) -> str:
    """`m:ss /km`, or `-` for `None`/non-positive input."""
    if seconds_per_km is None or seconds_per_km <= 0:
        return "-"
    total_seconds = round(seconds_per_km)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d} /km"


def md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """A GitHub-flavored Markdown table. Callers format every cell to a
    string first (via `fmt`/`fmt_pace`) — this function only lays out the
    table, it does no value formatting of its own."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
