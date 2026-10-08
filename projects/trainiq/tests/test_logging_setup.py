"""
Tests for Feature 0.4 — Logging.

DoD being verified: a full sync run produces both a human-readable summary
and a diagnostic file with rotation/retention active.
"""

from pathlib import Path

import pytest

from trainiq import logging_setup


def test_configure_creates_both_log_files(tmp_path):
    logging_setup.configure(tmp_path)
    logging_setup.summary_logger().info("Sync completed: 12 activities")
    logging_setup.diagnostic_logger().debug("HTTP GET /activities -> 200")

    for handler in logging_setup.logger._core.handlers.values():
        handler._sink._file.flush() if hasattr(handler._sink, "_file") else None

    assert (tmp_path / "summary.log").exists()
    assert (tmp_path / "diagnostic.log").exists()


def test_summary_log_excludes_diagnostic_only_messages(tmp_path):
    logging_setup.configure(tmp_path)
    logging_setup.summary_logger().info("User-facing message")
    logging_setup.diagnostic_logger().debug("Verbose internal detail")

    summary_content = (tmp_path / "summary.log").read_text()
    diagnostic_content = (tmp_path / "diagnostic.log").read_text()

    assert "User-facing message" in summary_content
    assert "Verbose internal detail" not in summary_content
    assert "Verbose internal detail" in diagnostic_content


def test_configure_is_idempotent(tmp_path):
    logging_setup.configure(tmp_path)
    logging_setup.configure(tmp_path)  # must not raise or duplicate handlers
    logging_setup.summary_logger().info("single message")
    content = (tmp_path / "summary.log").read_text()
    assert content.count("single message") == 1


def test_configured_logging_keeps_training_load_unknown_off_console(tmp_path, capsys):
    """Issue #44 AC1/AC6: once logging is configured (removing loguru's
    default stderr handler), repeated "training_load unknown" diagnostic
    messages — one per re-normalized record — must not reach the console,
    however many records are processed in a run."""
    from trainiq.normalization.load import compute_training_load

    logging_setup.configure(tmp_path)

    for _ in range(5):
        result = compute_training_load({"duration_s": 1800}, profile=None)
        assert result.method.name == "UNKNOWN"

    captured = capsys.readouterr()
    assert "training_load unknown" not in captured.out
    assert "training_load unknown" not in captured.err


def test_renormalize_script_configures_logging_as_first_action(monkeypatch, tmp_path):
    """Thin guard so a future edit to the re-normalize script can't
    silently drop the configure() call again: asserts main() calls
    trainiq.logging_setup.configure() before doing anything else that would
    require a real database."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import renormalize_strava_unofficial as script

    configure_calls = []

    class _StopAfterConfigure(Exception):
        pass

    def _fake_configure(log_dir):
        configure_calls.append(log_dir)
        raise _StopAfterConfigure  # short-circuit before the real DB open

    monkeypatch.setattr(script, "configure", _fake_configure)

    db_path = tmp_path / "trainiq.db"
    monkeypatch.setattr(sys, "argv", ["renormalize_strava_unofficial.py", "--db-path", str(db_path)])

    with pytest.raises(_StopAfterConfigure):
        script.main()

    assert configure_calls, "main() must call logging_setup.configure() before anything else"
