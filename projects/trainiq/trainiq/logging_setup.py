"""
trainiq.logging_setup — Feature 0.4

Split between a concise user-facing sync summary and a verbose diagnostic
file (Milestone 4 §8). Uses loguru per the Decision Matrix 11.2 recommendation.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

_configured = False

DEFAULT_LOG_DIR: Path = Path.home() / "Library" / "Logs" / "TrainIQ"


def configure(log_dir: Path) -> None:
    """Idempotent — safe to call more than once (e.g. in tests)."""
    global _configured
    logger.remove()  # clear default handler

    log_dir.mkdir(parents=True, exist_ok=True)

    # User-facing summary: concise, INFO+ only, no stack traces.
    logger.add(
        log_dir / "summary.log",
        level="INFO",
        rotation="1 week",
        retention="8 weeks",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        filter=lambda record: record["extra"].get("audience") != "diagnostic",
    )

    # Diagnostic: everything, with full context, for troubleshooting.
    logger.add(
        log_dir / "diagnostic.log",
        level="DEBUG",
        rotation="1 week",
        retention="8 weeks",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
        backtrace=True,
        diagnose=True,
    )

    _configured = True


def summary_logger():
    return logger.bind(audience="summary")


def diagnostic_logger():
    return logger.bind(audience="diagnostic")
