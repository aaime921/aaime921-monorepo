"""
Tests for scripts/renormalize_peloton_distance.py's `--unit` argument
(issue #45). Confirms argparse's `choices`/`required` actually enforce the
script's never-guess-a-unit contract, rather than assuming argparse's
default behavior. The rest of the script talks to a real on-disk database
and is exercised through trainiq.normalization.renormalize's own test
suite (test_renormalize.py) plus manual runs against the BO's real
database, per its own module docstring.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "renormalize_peloton_distance.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("renormalize_peloton_distance", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script_module()


def test_unit_argument_is_required(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["renormalize_peloton_distance.py"])
    with pytest.raises(SystemExit):
        script.main()


def test_unit_argument_rejects_values_outside_mi_km(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv",
        ["renormalize_peloton_distance.py", "--db-path", str(tmp_path / "test.db"), "--unit", "furlongs"],
    )
    with pytest.raises(SystemExit):
        script.main()
