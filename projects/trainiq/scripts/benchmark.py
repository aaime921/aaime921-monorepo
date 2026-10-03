#!/usr/bin/env python3
"""
scripts/benchmark.py — Pre-RC performance baseline

Per the Product Owner's explicit request: not optimization, a baseline.
Run this before v1.0.0 and again after any future change that might
plausibly affect performance, so a regression is something this script can
actually catch, not something noticed anecdotally months later.

HONEST CAVEAT, stated once here rather than left implicit in every number
below: absolute timings from this development sandbox do NOT represent
real-world performance on a user's Mac, over a real network, against real
provider APIs. What IS meaningful and worth tracking run-over-run:
  - relative throughput (records/sec) for CPU-bound work (normalization,
    SQLite writes) that doesn't depend on network conditions,
  - the shape of how each measurement scales with N (10 -> 100 -> 1000),
  - regressions between one run of this script and the next, on the same
    machine.
Do not quote these absolute numbers as "TrainIQ syncs N activities in Xs
in production" — network-bound work (real HTTP calls to Strava/Peloton/
Eufy) is entirely absent from this benchmark by construction, since it
uses in-memory mock connectors, the same fakes the test suite uses.

Usage:
    python3 scripts/benchmark.py
    python3 scripts/benchmark.py --out docs/benchmarks/2026-07-04.md
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import sqlite3
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

# Silence expected diagnostic noise for the duration of this benchmark run.
# The "bench" provider has no taxonomy mapping and (for the no-profile runs)
# no athlete_profile — both correctly log a WARNING/INFO line per activity
# per Constitution Principle 1 (never silently guess). That's exactly the
# right production behavior, but at N=1000 it produces thousands of
# expected, uninteresting lines that would otherwise drown the actual
# report below. Only this script's own output should be suppressed here —
# production logging behavior itself is untouched.
logger.remove()
logger.add(sys.stderr, level="CRITICAL")

from trainiq.athlete.profile import AthleteProfile
from trainiq.connectors.base import CapabilityTier, Connector
from trainiq.normalization.engine import build_canonical_record
from trainiq.storage import schema
from trainiq.sync.engine import SynchronizationEngine


class BenchmarkConnector(Connector):
    """In-memory only — no network, no real provider. Exists specifically
    to isolate TrainIQ's own CPU/DB-bound work from anything network-bound,
    per this script's stated scope."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def __init__(self, provider: str, count: int):
        super().__init__(provider)
        self._activities = [
            {
                "external_id": str(i),
                "start_time": f"2026-01-{(i % 28) + 1:02d}T07:00:00+00:00",
                "duration_s": 1800 + (i % 5) * 300,
                "discipline_raw": "Ride" if i % 3 else "Run",
                "avg_hr": 130 + (i % 40),
                "avg_power": 150 + (i % 100) if i % 2 == 0 else None,
                "max_hr": 170 + (i % 20),
                "distance_m": 10000.0 + i * 37,
                "calories": 300 + (i % 200),
            }
            for i in range(count)
        ]

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        return list(self._activities)

    def normalize(self, raw: dict) -> dict:
        return raw


def _timed(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return result, elapsed


def benchmark_migration(tmp_dir: Path) -> dict:
    db_path = tmp_dir / "bench_migration.db"
    _, elapsed = _timed(schema.migrate, db_path)
    return {"fresh_migration_s": round(elapsed, 4), "schema_version": schema.CURRENT_SCHEMA_VERSION}


def benchmark_startup(tmp_dir: Path) -> dict:
    db_path = tmp_dir / "bench_startup.db"

    def _startup():
        conn = schema.open_db(db_path)
        engine = SynchronizationEngine(conn)
        conn.close()
        return engine

    _, elapsed = _timed(_startup)
    return {"cold_startup_s": round(elapsed, 4)}


def benchmark_normalization_throughput(n: int) -> dict:
    """Isolates build_canonical_record() alone — no DB, no connector,
    no sync engine — the purest measure of normalization CPU cost."""
    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190, ftp_watts=250)
    connector = BenchmarkConnector("bench", n)
    activities = connector._activities

    start = time.perf_counter()
    for raw in activities:
        build_canonical_record("bench", connector.record_kind, raw, profile)
    elapsed = time.perf_counter() - start

    return {
        "n": n,
        "total_s": round(elapsed, 4),
        "records_per_sec": round(n / elapsed, 1) if elapsed > 0 else None,
    }


def benchmark_sqlite_write_throughput(tmp_dir: Path, n: int) -> dict:
    """Isolates raw INSERT OR REPLACE throughput into raw_activities,
    independent of any normalization work."""
    db_path = tmp_dir / f"bench_sqlite_{n}.db"
    conn = schema.open_db(db_path)

    start = time.perf_counter()
    for i in range(n):
        conn.execute(
            "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            ("bench", str(i), json.dumps({"i": i}), datetime.now(timezone.utc).isoformat()),
        )
    conn.commit()
    elapsed = time.perf_counter() - start
    conn.close()

    return {
        "n": n,
        "total_s": round(elapsed, 4),
        "writes_per_sec": round(n / elapsed, 1) if elapsed > 0 else None,
    }


def benchmark_full_sync(tmp_dir: Path, n: int, with_profile: bool) -> dict:
    """End-to-end through the real SynchronizationEngine: authenticate,
    download, normalize, taxonomy, confidence, training load, persist,
    checkpoint — everything except a real network call."""
    db_path = tmp_dir / f"bench_full_sync_{n}_{'profile' if with_profile else 'noprofile'}.db"
    conn = schema.open_db(db_path)
    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190, ftp_watts=250) if with_profile else None
    engine = SynchronizationEngine(conn, athlete_profile=profile)
    connector = BenchmarkConnector("bench", n)

    start = time.perf_counter()
    result = engine.run_once([connector])
    elapsed = time.perf_counter() - start
    conn.close()

    return {
        "n": n,
        "with_profile": with_profile,
        "total_s": round(elapsed, 4),
        "activities_per_sec": round(n / elapsed, 1) if elapsed > 0 else None,
        "records_upserted": result.connector_results[0].records_upserted,
    }


def benchmark_memory(tmp_dir: Path, n: int) -> dict:
    """Peak memory (via tracemalloc) for a full sync of n activities —
    a coarse but reproducible signal, not a substitute for a real profiler."""
    db_path = tmp_dir / f"bench_memory_{n}.db"
    conn = schema.open_db(db_path)
    engine = SynchronizationEngine(conn)
    connector = BenchmarkConnector("bench", n)

    gc.collect()
    tracemalloc.start()
    engine.run_once([connector])
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    conn.close()

    return {"n": n, "peak_memory_kb": round(peak / 1024, 1)}


def run_all() -> dict:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tmp_dir = Path(d)
        results = {
            "meta": {
                "run_at_utc": datetime.now(timezone.utc).isoformat(),
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "trainiq_schema_version": schema.CURRENT_SCHEMA_VERSION,
                "caveat": (
                    "In-sandbox measurement using in-memory mock connectors. "
                    "No real network I/O. Absolute numbers are NOT representative "
                    "of production hardware or real provider latency — see this "
                    "script's module docstring."
                ),
            },
            "migration": benchmark_migration(tmp_dir),
            "startup": benchmark_startup(tmp_dir),
            "normalization_throughput": [
                benchmark_normalization_throughput(n) for n in (10, 100, 1000)
            ],
            "sqlite_write_throughput": [
                benchmark_sqlite_write_throughput(tmp_dir, n) for n in (10, 100, 1000)
            ],
            "full_sync_no_profile": [
                benchmark_full_sync(tmp_dir, n, with_profile=False) for n in (10, 100, 1000)
            ],
            "full_sync_with_profile": [
                benchmark_full_sync(tmp_dir, n, with_profile=True) for n in (10, 100, 1000)
            ],
            "memory": [benchmark_memory(tmp_dir, n) for n in (10, 100, 1000)],
        }
    return results


def format_report(results: dict) -> str:
    m = results["meta"]
    lines = [
        "# TrainIQ Performance Baseline",
        "",
        f"**Run at:** {m['run_at_utc']}",
        f"**Python:** {m['python_version']} | **Platform:** {m['platform']}",
        f"**Schema version:** {m['trainiq_schema_version']}",
        "",
        f"> **Caveat:** {m['caveat']}",
        "",
        "## Migration & Startup",
        "",
        f"- Fresh schema migration: {results['migration']['fresh_migration_s']}s",
        f"- Cold startup (open DB + construct SynchronizationEngine): {results['startup']['cold_startup_s']}s",
        "",
        "## Normalization Throughput (build_canonical_record, no DB)",
        "",
        "| N | Total (s) | Records/sec |",
        "|---|---|---|",
    ]
    for r in results["normalization_throughput"]:
        lines.append(f"| {r['n']} | {r['total_s']} | {r['records_per_sec']} |")

    lines += ["", "## SQLite Raw Write Throughput", "", "| N | Total (s) | Writes/sec |", "|---|---|---|"]
    for r in results["sqlite_write_throughput"]:
        lines.append(f"| {r['n']} | {r['total_s']} | {r['writes_per_sec']} |")

    lines += [
        "",
        "## Full Sync — End to End (no AthleteProfile, training_load stays Unknown)",
        "",
        "| N | Total (s) | Activities/sec | Upserted |",
        "|---|---|---|---|",
    ]
    for r in results["full_sync_no_profile"]:
        lines.append(f"| {r['n']} | {r['total_s']} | {r['activities_per_sec']} | {r['records_upserted']} |")

    lines += [
        "",
        "## Full Sync — End to End (with AthleteProfile, real TRIMP/TSS computed)",
        "",
        "| N | Total (s) | Activities/sec | Upserted |",
        "|---|---|---|---|",
    ]
    for r in results["full_sync_with_profile"]:
        lines.append(f"| {r['n']} | {r['total_s']} | {r['activities_per_sec']} | {r['records_upserted']} |")

    lines += ["", "## Peak Memory (full sync, tracemalloc)", "", "| N | Peak (KB) |", "|---|---|"]
    for r in results["memory"]:
        lines.append(f"| {r['n']} | {r['peak_memory_kb']} |")

    lines += [
        "",
        "## How to use this baseline",
        "",
        "- Compare run-over-run on the SAME machine to catch regressions — not against these absolute numbers on a different machine.",
        "- Normalization throughput and SQLite write throughput are the two numbers least affected by anything external (no I/O beyond local disk) — treat these as the most reliable regression signal.",
        "- Full-sync numbers include everything except real network I/O — a real sync against live providers will be dominated by network latency, not by anything measured here.",
    ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="Write the markdown report to this path")
    parser.add_argument("--json", type=Path, default=None, help="Also write raw results as JSON")
    args = parser.parse_args()

    results = run_all()
    report = format_report(results)

    print(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)
        print(f"\nReport written to {args.out}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2))
        print(f"Raw results written to {args.json}")


if __name__ == "__main__":
    main()
