"""Structured logging configuration."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

import structlog

from src.config.settings import MonitoringConfig


def configure_logging(
    log_level: str = "INFO",
    logs_path: str | None = None,
    monitoring: MonitoringConfig | None = None,
) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    if logs_path:
        log_dir = Path(logs_path)
        log_dir.mkdir(parents=True, exist_ok=True)
        max_bytes = monitoring.error_log_max_bytes if monitoring else 5_000_000
        backup_count = monitoring.error_log_backup_count if monitoring else 3
        file_handler = RotatingFileHandler(
            log_dir / "errors.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.ERROR)
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(file_handler)
    # Avoid leaking signed query strings / low-level wire traces from third-party libs.
    # We emit our own safe REST logs via `rest_request` / `rest_response`.
    for noisy_logger in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            level
        ),
    )
