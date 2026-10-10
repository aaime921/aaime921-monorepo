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

import json
from datetime import date
from pathlib import Path
from sqlite3 import Connection
from typing import Optional

from trainiq.dedup.detector import primary_activity_ids
from trainiq.export import classes, last_done, load, performance, profile, recent, weight
from trainiq.export.classes import ClassCatalog
from trainiq.export.data import resolve_as_of
from trainiq.logging_setup import diagnostic_logger


def run_export(
    conn: Connection,
    out_dir: Path,
    config_path: Path,
    as_of: Optional[date] = None,
    catalog: Optional[ClassCatalog] = None,
) -> list[Path]:
    """Writes all seven Markdown files (plus JSON companions for
    recent/load/weight/peloton_classes) into `out_dir`, creating it if
    absent. Returns the paths
    written, in the fixed order below — not the order a filesystem listing
    would happen to return them in."""
    out_dir.mkdir(parents=True, exist_ok=True)
    primary_ids = primary_activity_ids(conn)
    resolved_as_of = resolve_as_of(conn, as_of)

    written: list[Path] = []

    written.append(
        _write(out_dir, "profile.md", profile.render(conn, config_path, resolved_as_of, primary_ids))
    )

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

    # Issue #72: appended last, after every #71 file is written, inside its
    # own try/except — a crash building the class catalog (network call,
    # not a pure function like the renderers above) must never take down
    # the rest of the export (AC 7). `classes.build()` already degrades
    # expected failures (no catalog, auth/rate-limit errors, a malformed
    # response) to a well-formed "unavailable" result on its own; this is
    # the outer safety net for anything genuinely unexpected.
    try:
        classes_md, classes_json = classes.render(conn, primary_ids, resolved_as_of, catalog)
    except Exception as exc:  # noqa: BLE001 - outer resilience boundary, see above
        diagnostic_logger().warning(f"classes export failed unexpectedly: {exc}")
        classes_md = "\n".join(["# Peloton class candidates", f"As of: {resolved_as_of.isoformat()}", "", "Class catalog unavailable.", ""])
        classes_json = json.dumps(
            {"as_of": resolved_as_of.isoformat(), "status": "unavailable", "reason": str(exc),
             "sections": [], "unmatched_tags": [], "not_shown": []},
            sort_keys=True, indent=2,
        )
    written.append(_write(out_dir, "peloton_classes.md", classes_md))
    written.append(_write(out_dir, "peloton_classes.json", classes_json))

    return written


def _write(out_dir: Path, name: str, content: str) -> Path:
    path = out_dir / name
    path.write_text(content, encoding="utf-8")
    return path
