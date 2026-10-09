"""
In-process job scheduler.

WHY THIS EXISTS AT ALL

Nothing in this application could happen on a schedule. Every background
task is a FastAPI `BackgroundTasks` callback, which only ever runs because
a request arrived — so a reminder, a nightly evaluation, a weekly digest
or a sweep of abandoned calls was structurally impossible, not merely
unimplemented. This deployment is SQL Server **Express**: no SQL Agent, no
cron on the box, no separate worker service. A scheduler therefore has to
live inside the API process.

WHY NOT A SCHEDULING LIBRARY

APScheduler (and friends) keep their timers in memory. Run two uvicorn
workers and each gets its own scheduler, so every clinic receives two of
every reminder — silently, because nothing errors. The library would not
have solved the problem this module is really about.

What actually prevents a double-run is a row: `ScheduledJobRun` has a
UNIQUE constraint on (job_name, run_key), and a job is claimed by
INSERTing that row. Two processes racing the same tick both attempt it;
exactly one commits and the other takes an IntegrityError and moves on.
The database is the leader election, which needs no Redis, no advisory
lock and no new dependency. Once you have that, the scheduler itself is
the loop below.

    Registered jobs run whether or not a request ever arrives, and at most
    once per bucket no matter how many processes are running.

DESIGN NOTES WORTH KEEPING

* **`run_key` is a time bucket, not a timestamp.** A job with a 300s
  interval gets keys like "2026-09-06T17:05". Re-ticking inside the same
  bucket — after a restart, a slow tick, a clock nudge — is a no-op
  instead of a second send.

* **A failing job must not stop the loop.** Every job is wrapped; the run
  row is marked `failed` with the exception text and the next job still
  runs. This mirrors the philosophy in `notification_service` and
  `audit_service`: side machinery never takes down the thing it serves.

* **Each job gets its own Session**, opened and closed inside the wrapper,
  so one job's rollback cannot poison another's transaction.

* **Rows are kept.** "Did the 09:00 job actually run?" is worth being able
  to answer later, and the table is tiny.
"""

import asyncio
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import instance, models
from .config import settings
from .database import SessionLocal

logger = logging.getLogger("scheduler")

# A job returns a short human-readable line describing what it did, which
# is stored on the run row. Returning None is fine.
JobFn = Callable[[Session], Optional[str]]


@dataclass(frozen=True)
class Job:
    name: str
    interval_seconds: int
    fn: JobFn

    def run_key(self, now: datetime) -> str:
        """
        The bucket this moment belongs to.

        Truncating epoch seconds to the interval means every process
        computes the same key for the same window without coordinating.
        """
        bucket = int(now.timestamp()) // max(1, self.interval_seconds)
        return f"{bucket}"


_REGISTRY: list[Job] = []
_task: Optional[asyncio.Task] = None


def register(name: str, interval_seconds: int, fn: JobFn) -> None:
    """Add a job. Registering the same name twice replaces the first."""
    global _REGISTRY
    _REGISTRY = [j for j in _REGISTRY if j.name != name]
    _REGISTRY.append(Job(name=name, interval_seconds=interval_seconds, fn=fn))


def registered_jobs() -> list[Job]:
    return list(_REGISTRY)


def _claim(db: Session, job: Job, run_key: str) -> Optional[models.ScheduledJobRun]:
    """
    Try to become the one process that runs this job for this bucket.

    Returns the claimed row, or None if someone else already holds it.
    The UNIQUE constraint does the deciding — this is the whole
    multi-process safety story, so it must stay an INSERT race and not
    become a SELECT-then-INSERT, which would have a window between them.
    """
    row = models.ScheduledJobRun(
        job_name=job.name, run_key=run_key, status="running",
        started_at=datetime.utcnow(), instance_id=instance.INSTANCE_ID,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        _note_whoever_won(db, job, run_key)
        return None
    db.refresh(row)
    return row


def _note_whoever_won(db: Session, job: Job, run_key: str) -> None:
    """
    Read back who holds the claim we just lost, for GAP-T1 detection.

    This is NOT the SELECT the docstring above forbids. That prohibition
    is about the *claim*, which must stay a bare INSERT race with no
    window between a check and a write. This runs after the INSERT has
    already failed, decides nothing, and changes no outcome -- the caller
    returns None either way. Deleting it would lose the detection and fix
    nothing.

    Losing a claim is not by itself interesting: a process restarting
    inside the same bucket races itself, which is exactly what `run_key`
    bucketing is for. `instance.report_peer` filters on that, and only
    speaks up for an id that is not ours.
    """
    try:
        held = (
            db.query(models.ScheduledJobRun)
            .filter(
                models.ScheduledJobRun.job_name == job.name,
                models.ScheduledJobRun.run_key == run_key,
            )
            .first()
        )
    except Exception:  # pragma: no cover - diagnostics must never break a tick
        db.rollback()
        return
    if held is not None:
        # `started_at` is what tells a concurrent process from this
        # one's own previous incarnation after a restart -- see
        # instance.report_peer, which learned that from a false positive.
        instance.report_peer(
            held.instance_id or "", job.name, claimed_at=held.started_at
        )


def run_due_jobs(now: Optional[datetime] = None) -> list[str]:
    """
    One tick. Claims and runs every job whose bucket has not been taken.

    Synchronous and dependency-free so it can be called directly from a
    test without an event loop — which is also how the tests below pin
    the double-run behaviour.

    Returns the names of jobs actually executed by *this* caller.
    """
    now = now or datetime.utcnow()
    executed: list[str] = []

    for job in list(_REGISTRY):
        db = SessionLocal()
        try:
            claimed = _claim(db, job, job.run_key(now))
            if claimed is None:
                continue  # another process (or an earlier tick) has this bucket

            try:
                detail = job.fn(db)
                claimed.status = "succeeded"
                claimed.detail = detail
                executed.append(job.name)
                if detail:
                    logger.info(f"job {job.name}: {detail}")
            except Exception as exc:  # noqa: BLE001 - one bad job must not stop the rest
                db.rollback()
                logger.exception(f"scheduled job {job.name} failed")
                claimed = db.get(models.ScheduledJobRun, claimed.id)
                if claimed is not None:
                    claimed.status = "failed"
                    claimed.detail = f"{type(exc).__name__}: {exc}"[:2000]
            finally:
                if claimed is not None:
                    claimed.finished_at = datetime.utcnow()
                    try:
                        db.commit()
                    except Exception:  # noqa: BLE001
                        db.rollback()
                        logger.error(
                            "could not record the outcome of job %s:\n%s",
                            job.name, traceback.format_exc(),
                        )
        finally:
            db.close()

    return executed


async def _loop() -> None:
    tick = max(5, settings.scheduler_tick_seconds)
    logger.info(
        "scheduler started (tick=%ss, jobs=%s)",
        tick, [j.name for j in _REGISTRY] or "none",
    )
    while True:
        try:
            # The jobs are synchronous SQLAlchemy work, so they go to a
            # thread rather than blocking the event loop the API serves
            # requests on.
            await asyncio.to_thread(run_due_jobs)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must outlive any single tick
            logger.exception("scheduler tick failed")
        await asyncio.sleep(tick)


def start() -> None:
    """Begin ticking. No-op when disabled or already running."""
    global _task
    if not settings.scheduler_enabled:
        logger.info("scheduler disabled (SCHEDULER_ENABLED=false)")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    """Cancel the loop. Safe to call when it was never started."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _task = None
    logger.info("scheduler stopped")
