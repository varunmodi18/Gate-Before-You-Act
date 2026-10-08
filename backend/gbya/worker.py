"""Experiment worker process (plan §C.3 "Jobs", §F.1, T3.5).

Polls the ``jobs`` table, claims the oldest queued job (or one whose heartbeat is older than
``worker_stale_s``), and executes its run's unfinished work units (``gbya.experiments.runner``).
While a job runs, a heartbeat thread refreshes ``jobs.heartbeat_at`` every
``worker_heartbeat_s``; a killed worker's job therefore becomes re-claimable after the stale
interval, and the next worker redoes only the unfinished units. Cancelling a run stops the job
after the units in flight.

The worker also writes ``<data_dir>/worker.heartbeat`` (JSON) on every poll for ``/health``.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
from pathlib import Path
from types import FrameType

from sqlalchemy.orm import Session, sessionmaker

from gbya.config import Settings, get_settings
from gbya.experiments.runner import Deps, claim_job, execute_run, finish_job, heartbeat
from gbya.llm.factory import make_client
from gbya.llm.tokens import default_counter
from gbya.logging import configure_logging, get_logger
from gbya.store.db import make_engine, make_sessionmaker, session_scope
from gbya.store.models import Run, utcnow

_running = True
log = get_logger("gbya.worker")


def _stop(_signum: int, _frame: FrameType | None) -> None:
    global _running
    _running = False


def heartbeat_file(settings: Settings) -> Path:
    return settings.resolve(settings.data_dir) / "worker.heartbeat"


def _beat_file(settings: Settings, worker_id: str, job: int | None) -> None:
    path = heartbeat_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"worker": worker_id, "at": utcnow().isoformat(), "job": job}))
    tmp.replace(path)


def _run_status(factory: sessionmaker[Session], run_id: int) -> str | None:
    with session_scope(factory) as s:
        run = s.get(Run, run_id)
        return run.status if run else None


def work_one(factory: sessionmaker[Session], deps: Deps, worker_id: str) -> bool:
    """Claim and execute one job; False when there was nothing to claim."""
    settings = deps.settings
    job = claim_job(factory, worker_id, settings.worker_stale_s)
    if job is None:
        return False
    log.info("job_claimed", job=job.id, run=job.run_id, worker=worker_id)
    stop_beat = threading.Event()
    lost = threading.Event()

    def beat() -> None:
        while not stop_beat.wait(settings.worker_heartbeat_s):
            if not heartbeat(factory, job.id, worker_id):
                lost.set()
                return
            _beat_file(settings, worker_id, job.id)

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        status = execute_run(
            factory, job.run_id, deps,
            should_stop=lambda: not _running or lost.is_set()
            or _run_status(factory, job.run_id) == "cancelled",
        )  # fmt: skip
    except Exception as exc:
        log.error("job_failed", job=job.id, run=job.run_id, error=str(exc))
        finish_job(factory, job.id, "failed")
        with session_scope(factory) as s:
            run = s.get(Run, job.run_id)
            if run is not None:
                run.status = "failed"
        return True
    finally:
        stop_beat.set()
        thread.join()
    if lost.is_set():
        log.warning("job_lost", job=job.id, run=job.run_id)
        return True
    if not _running and status == "cancelled":  # shut down mid-run: leave it re-claimable
        with session_scope(factory) as s:
            run = s.get(Run, job.run_id)
            if run is not None and run.status == "cancelled":
                run.status = "queued"
        finish_job(factory, job.id, "queued")
        return True
    finish_job(factory, job.id, {"completed": "done"}.get(status, status))
    log.info("job_finished", job=job.id, run=job.run_id, status=status)
    return True


def main() -> None:
    settings = get_settings()
    configure_logging("worker", settings.resolve(settings.log_dir), dev=settings.env == "dev")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    deps = Deps(settings=settings, client=make_client(settings), counter=default_counter())
    log.info("worker_started", worker=worker_id, llm_backend=settings.llm_backend)
    while _running:
        _beat_file(settings, worker_id, None)
        if not work_one(factory, deps, worker_id):
            time.sleep(min(2.0, settings.worker_heartbeat_s))
    log.info("worker_stopped", worker=worker_id)


if __name__ == "__main__":
    main()
