"""
Project-wide architectural invariant tests — properties that must hold
regardless of which epic or slice touches the code next. Distinct from
per-module tests: these scan the actual repository source, not just one
module's behavior, so a future change anywhere in the codebase that
violates the invariant fails loudly rather than silently.

IMPORTANT CAVEAT, worth stating explicitly rather than implying more than
is true: these are static-analysis guardrails, not a mathematical proof of
single-writer uniqueness. They catch a second `INSERT INTO` statement or a
second direct call to `build_canonical_record()` appearing anywhere in the
source tree — the accidental-shortcut failure mode this rule exists to
prevent. They do NOT catch a future abstraction layer or helper function
that preserves the same semantics through indirection the static check
doesn't follow (e.g. a new module that wraps `_upsert_normalized_activity`
under a different name, or calls it via `getattr`). That's a real,
structural limit of this kind of check, not a bug in it — worth keeping
these tests specifically because they raise the bar well above "relies on
developer discipline alone," not because they make circumvention
impossible. A code reviewer noticing a new abstraction layer touching
these tables is still the actual backstop.

ARCH-PEL-002 (Architect decision): the original rule — "only
sync/engine.py" — is now "only explicitly authorized ingestion entry
points." This is a deliberate widening, not a weakening: the invariant
still fails closed for anything not in the named set below. Adding a
future third entry point (a Strava CSV importer, say) requires touching
THIS list explicitly, in a reviewed change — never inferred from a
filename pattern or a directory membership check.
"""

from __future__ import annotations

import ast
from pathlib import Path

TRAINIQ_ROOT = Path(__file__).resolve().parent.parent / "trainiq"

# ARCH-PEL-002: the exact, explicit set of modules authorized to write to
# normalized_activities/weigh_ins and to call build_canonical_record().
# Each entry is a (parent_dir_name, file_name) pair, matched precisely —
# not a substring/directory-membership check, so a new file dropped into
# csv_import/ (e.g. a future strava_csv.py) is NOT automatically
# authorized just by being in the same package.
_AUTHORIZED_INGESTION_ENTRY_POINTS: frozenset[tuple[str, str]] = frozenset({
    ("sync", "engine.py"),
    ("csv_import", "peloton_csv.py"),
})


def _all_python_files():
    return [p for p in TRAINIQ_ROOT.rglob("*.py") if "__pycache__" not in str(p)]


def _is_authorized_entry_point(path: Path) -> bool:
    return (path.parent.name, path.name) in _AUTHORIZED_INGESTION_ENTRY_POINTS


def test_only_authorized_entry_points_write_to_normalized_activities_or_weigh_ins():
    """Chief Architect's explicit rule after Epic 6 slice 5, extended by
    ARCH-PEL-002: Connector/Importer -> normalize() ->
    build_canonical_record() -> persist() must be the only path into
    these two tables, and only through one of the explicitly authorized
    entry points above. If a future addition writes to either table
    directly outside this set, taxonomy/confidence/training-load
    computation could silently be skipped for those records — exactly the
    kind of divergence this test exists to catch immediately, not
    whenever someone happens to notice inconsistent data."""
    offending: list[str] = []
    for path in _all_python_files():
        if _is_authorized_entry_point(path):
            continue
        with open(path) as f:
            contents = f.read()
        if "INSERT INTO normalized_activities" in contents or "INSERT INTO weigh_ins" in contents:
            offending.append(str(path.relative_to(TRAINIQ_ROOT)))
    assert offending == [], f"Found an unauthorized write path to canonical tables in: {offending}"


def test_only_authorized_entry_points_call_build_canonical_record():
    """The other half of the same invariant, extended by ARCH-PEL-002:
    build_canonical_record() itself must only ever be invoked from an
    explicitly authorized ingestion entry point — if some future code
    called it directly and persisted the result some other way, the
    single-writer guarantee above wouldn't actually mean what it appears
    to mean. Callers are compared against the EXACT authorized set, not a
    package-membership check — an unauthorized caller fails this test
    regardless of which directory it happens to live in."""
    callers: list[str] = []
    for path in _all_python_files():
        if path.name == "engine.py" and path.parent.name == "normalization":
            continue  # the definition itself, not a call site
        with open(path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "build_canonical_record":
                callers.append(path)

    unauthorized = [str(p.relative_to(TRAINIQ_ROOT)) for p in callers if not _is_authorized_entry_point(p)]
    assert unauthorized == [], (
        f"build_canonical_record() called from an unauthorized entry point: {unauthorized}. "
        f"Authorized entry points: {sorted('/'.join(e) for e in _AUTHORIZED_INGESTION_ENTRY_POINTS)}"
    )
    # Also confirm both currently-authorized entry points actually exist as
    # real call sites — this test should fail loudly if one silently stops
    # calling the function at all, not just if an unauthorized one starts.
    found = {(p.parent.name, p.name) for p in callers}
    assert found == _AUTHORIZED_INGESTION_ENTRY_POINTS, (
        f"Expected calls from exactly {_AUTHORIZED_INGESTION_ENTRY_POINTS}, found calls from {found}"
    )
