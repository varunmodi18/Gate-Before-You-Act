"""Structured logging (plan §G): JSON lines to ``logs/<name>.log``, pretty console in dev."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import structlog


def configure_logging(name: str, log_dir: Path | None = None, *, dev: bool = True) -> None:
    """Configure structlog. Context such as ``run_id`` is added with ``bind_contextvars``."""
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    handlers: list[logging.Handler] = []
    console = logging.StreamHandler()
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer()
            if dev
            else structlog.processors.JSONRenderer(),
            foreign_pre_chain=shared,
        )
    )
    handlers.append(console)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fileh = logging.handlers.RotatingFileHandler(
            log_dir / f"{name}.log", maxBytes=10_000_000, backupCount=5
        )
        fileh.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(), foreign_pre_chain=shared
            )
        )
        handlers.append(fileh)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(logging.INFO)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
