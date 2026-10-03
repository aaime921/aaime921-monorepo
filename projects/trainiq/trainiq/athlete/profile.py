"""
trainiq.athlete.profile — shared AthleteProfile shape

This is NOT Epic 7 (Athlete Knowledge Model / Feature 7.1). It's a minimal
plumbing move: the AthleteProfile dataclass previously lived inside
trainiq.synthetic_dataset (Feature 0.7), which meant any production code
needing the shape (Epic 6's training-load engine) would have had to import
from a test-fixture-generation module — backwards, and a real dependency
smell. Moved here so both the synthetic dataset generator and the real
Normalization Engine import the same definition from a neutral location.

No persistence, no SQLite table, no initial-setup collection flow — that
remains Epic 7's job (Feature 7.1), untouched and unanticipated by this
move. This is only the dataclass shape itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AthleteProfile:
    """Persistent facts only — per ADR-020. Everything else is derived."""
    sex: Optional[str] = None
    date_of_birth: Optional[str] = None
    resting_hr: Optional[int] = None
    max_hr: Optional[int] = None
    ftp_watts: Optional[int] = None
