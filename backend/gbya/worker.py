"""Experiment worker process.

Scaffold only (T0.1): the process starts, logs and idles. Job claiming, heartbeats and
resumable execution from the ``jobs`` table are implemented in T3.5.
"""

from __future__ import annotations

import signal
import time
from types import FrameType

from gbya.config import get_settings
from gbya.logging import configure_logging, get_logger

_running = True


def _stop(_signum: int, _frame: FrameType | None) -> None:
    global _running
    _running = False


def main() -> None:
    settings = get_settings()
    configure_logging("worker", settings.resolve(settings.log_dir), dev=settings.env == "dev")
    log = get_logger("gbya.worker")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker_started", note="job execution arrives in T3.5; idling")
    while _running:
        time.sleep(settings.worker_heartbeat_s)
    log.info("worker_stopped")


if __name__ == "__main__":
    main()
