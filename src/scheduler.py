"""Cron scheduler - runs the pipeline on a daily schedule."""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class CronStatus:
    """Tracks the cron job state for the UI."""

    is_running: bool = False
    last_run: datetime | None = None
    last_result: str = ""
    last_duration: float = 0.0
    cards_count: int = 0
    error: str = ""
    last_digest: dict | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "is_running": self.is_running,
                "last_run": self.last_run.isoformat() if self.last_run else None,
                "last_result": self.last_result,
                "last_duration": self.last_duration,
                "cards_count": self.cards_count,
                "error": self.error,
                "has_digest": self.last_digest is not None,
            }


cron_status = CronStatus()

_scheduler: BackgroundScheduler | None = None


def _run_pipeline_job() -> None:
    """Execute the full pipeline as a cron job."""
    with cron_status._lock:
        cron_status.is_running = True
        cron_status.error = ""

    t0 = time.time()
    logger.info("Cron job started")

    try:
        from src.main import (
            _step_filter,
            _step_generate,
            _step_scan,
            _step_send,
            _step_verify,
        )

        raw_signals = _step_scan()
        filtered = _step_filter(raw_signals)
        verified = _step_verify(filtered)
        digest = _step_generate(verified, signals_scanned=len(raw_signals))
        _step_send(digest)

        elapsed = time.time() - t0

        from src.web import _digest_to_dict

        with cron_status._lock:
            cron_status.is_running = False
            cron_status.last_run = datetime.now(UTC)
            cron_status.last_result = "success"
            cron_status.last_duration = round(elapsed, 1)
            cron_status.cards_count = len(digest.cards)
            cron_status.last_digest = _digest_to_dict(digest, elapsed)

        logger.info("Cron job completed in %.1fs, %d cards", elapsed, len(digest.cards))

    except Exception as e:
        elapsed = time.time() - t0
        with cron_status._lock:
            cron_status.is_running = False
            cron_status.last_run = datetime.now(UTC)
            cron_status.last_result = "error"
            cron_status.last_duration = round(elapsed, 1)
            cron_status.error = str(e)

        logger.exception("Cron job failed after %.1fs", elapsed)


def get_next_run() -> str | None:
    """Return ISO string of the next scheduled run, or None."""
    if _scheduler is None:
        return None
    jobs = _scheduler.get_jobs()
    if not jobs:
        return None
    next_time = jobs[0].next_run_time
    return next_time.isoformat() if next_time else None


def start_scheduler() -> None:
    """Start the background scheduler with the configured cron time."""
    global _scheduler

    if _scheduler is not None:
        return

    settings = get_settings()

    _scheduler = BackgroundScheduler()
    trigger = CronTrigger(
        hour=settings.cron_hour,
        minute=settings.cron_minute,
        timezone=settings.cron_timezone,
    )
    _scheduler.add_job(
        _run_pipeline_job,
        trigger=trigger,
        id="daily_pipeline",
        name="Daily VIBE-SERCH pipeline",
        replace_existing=True,
    )
    _scheduler.start()

    next_run = get_next_run()
    logger.info(
        "Scheduler started: daily at %02d:%02d %s, next run: %s",
        settings.cron_hour,
        settings.cron_minute,
        settings.cron_timezone,
        next_run,
    )


def stop_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
