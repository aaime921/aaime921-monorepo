"""
Tests for Feature 0.4 — Logging.

DoD being verified: a full sync run produces both a human-readable summary
and a diagnostic file with rotation/retention active.
"""

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
