"""
trainiq.export — issue #71: Markdown/JSON summaries for the Grok coach.

The coach (an external LLM) reads files from the private `trainiq-data`
repo through its own GitHub connector and has no other view of this
project's data — this package is the whole of that view. Pure, read-only
functions over the SQLite DB build each document as a string; `run_export`
is the thin writer that puts those strings on disk.

Determinism (AC 11/12): every renderer is anchored to an explicit `as_of`
date (never wall-clock "now") and every window/ordering is a pure function
of the DB's contents plus that date — the same DB and the same `as_of`
always produce byte-identical files.

Dedup (AC 8): `primary_activity_ids()` is read once here and threaded
through every renderer, so a linked Peloton/Strava pair counts once
everywhere, not once per file.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from sqlite3 import Connection
from typing import Optional

from trainiq.dedup.detector import primary_activity_ids
from trainiq.export import last_done, load, performance, profile, recent, weight
from trainiq.export.data import resolve_as_of


def run_export(
    conn: Connection,
    out_dir: Path,
    config_path: Path,
    as_of: Optional[date] = None,
) -> list[Path]:
    """Writes all six Markdown files (plus JSON companions for recent/load/
    weight) into `out_dir`, creating it if absent. Returns the paths
    written, in the fixed order below — not the order a filesystem listing
    would happen to return them in."""
    out_dir.mkdir(parents=True, exist_ok=True)
    primary_ids = primary_activity_ids(conn)
    resolved_as_of = resolve_as_of(conn, as_of)

    written: list[Path] = []

    written.append(_write(out_dir, "profile.md", profile.render(conn, config_path, resolved_as_of)))

    recent_md, recent_json = recent.render(conn, primary_ids, resolved_as_of)
    written.append(_write(out_dir, "recent.md", recent_md))
    written.append(_write(out_dir, "recent.json", recent_json))

    written.append(_write(out_dir, "last_done.md", last_done.render(conn, primary_ids, resolved_as_of)))

    load_md, load_json = load.render(conn, primary_ids, resolved_as_of)
    written.append(_write(out_dir, "load.md", load_md))
    written.append(_write(out_dir, "load.json", load_json))

    weight_md, weight_json = weight.render(conn, config_path, resolved_as_of)
    written.append(_write(out_dir, "weight.md", weight_md))
    written.append(_write(out_dir, "weight.json", weight_json))

    written.append(_write(out_dir, "performance.md", performance.render(conn, primary_ids, resolved_as_of)))

    return written


def _write(out_dir: Path, name: str, content: str) -> Path:
    path = out_dir / name
    path.write_text(content, encoding="utf-8")
    return path
