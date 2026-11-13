"""Background LLM jobs: reply classification and chatbot answers (Task 12a).

The executor never calls the LLM inside a webhook request. With `Deps.jobs` set, a Classify or
BotReply action only inserts a Job row (JobQueue.enqueue). After the request commits, the route
runs each new job in a FastAPI BackgroundTask (run_job); the worker tick (Task 12) calls run_due
for anything the request did not get to, retries and crash recovery.

run_job is safe to call twice and concurrently: a conditional UPDATE claims the job, and only the
caller whose UPDATE changed the row runs it. Logs carry lead / job ids and exception type names
only, never prompt, reply or message text; so does Job.last_error.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .actions import Action, Alert, Deps, StartTakeover, execute, record_integration_error
from .chatbot import takeover_active
from .models import Event, Job, Lead
from .states import TERMINAL

logger = logging.getLogger(__name__)

KINDS = ("classify", "bot_reply")
MAX_ATTEMPTS = 3
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5))  # after the 1st and the 2nd failure
STALE_AFTER = timedelta(minutes=10)
DEFAULT_GRACE = timedelta(minutes=2)
MAX_CHAIN = 2  # classify -> bot_reply -> (nothing else enqueues today)
ERROR_MAX = 200
_TERMINAL_VALUES = frozenset(s.value for s in TERMINAL)
_ENQUEUED = "outreach_jobs_enqueued"

SessionFactory = Callable[[], Session]
NowFn = Callable[[], datetime]


# ====================================================================== enqueue

def dedupe_key(kind: str, lead: Lead, event: Optional[Event], now: datetime) -> str:
    if kind == "classify":
        if event is None:
            raise ValueError("classify job needs an event")
        return f"classify:{event.id}"
    if event is not None:
        return f"bot:{lead.id}:{event.id}"
    return f"bot:{lead.id}:at:{now.isoformat()}"  # no inbound event (not produced by today's rules)


class JobQueue:
    """Deps.jobs: store a job instead of calling the LLM. Stateless and thread-safe; the ids a
    session enqueued are kept on that session (session.info), so they are per request."""

    def enqueue(self, session: Session, lead: Lead, kind: str, event: Optional[Event],
                now: datetime) -> Optional[int]:
        """Insert the job in a savepoint. Returns its id, or None if this dedupe_key already exists."""
        if kind not in KINDS:
            raise ValueError(f"unknown job kind: {kind}")
        job = Job(lead_id=lead.id, kind=kind, event_id=event.id if event is not None else None,
                  dedupe_key=dedupe_key(kind, lead, event, now), status="pending", attempts=0,
                  run_after=now, created_at=now)
        try:
            with session.begin_nested():
                session.add(job)
        except IntegrityError:
            logger.info("lead %s: %s job already queued", lead.id, kind)
            return None
        session.info.setdefault(_ENQUEUED, []).append(job.id)
        logger.info("lead %s: %s job %s queued", lead.id, kind, job.id)
        return job.id


def take_enqueued(session: Session) -> List[int]:
    """The ids this session enqueued since the last call (call right after commit)."""
    return list(session.info.pop(_ENQUEUED, None) or [])


def discard_enqueued(session: Session) -> None:
    """Forget ids from a transaction that was rolled back."""
    session.info.pop(_ENQUEUED, None)


# ====================================================================== run one job

def _claim(session_factory: SessionFactory, job_id: int, now: datetime) -> bool:
    """Atomic claim: one conditional UPDATE in its own transaction. On Postgres a concurrent
    claimer blocks on the row lock and then re-checks status='pending' (0 rows); SQLite
    serialises writers. Exactly one caller sees rowcount 1."""
    s = session_factory()
    try:
        res = s.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "pending", Job.run_after <= now)
            .values(status="running", started_at=now, attempts=Job.attempts + 1)
            .execution_options(synchronize_session=False))
        won = res.rowcount == 1
        s.commit()
        return won
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _obsolete_reason(session: Session, job: Job, lead: Optional[Lead], event: Optional[Event]) -> Optional[str]:
    if lead is None:
        return "lead gone"
    if lead.state in _TERMINAL_VALUES:
        return "lead terminal"
    if job.kind == "classify" and event is None:
        return "event gone"
    if job.kind == "bot_reply":
        if lead.wa_human_owner or takeover_active(lead):
            return "a person has the chat"
        newer = session.scalar(
            select(Job.id).where(Job.lead_id == job.lead_id, Job.kind == "bot_reply", Job.id > job.id,
                                 Job.status.in_(("pending", "running", "done"))).limit(1))
        if newer is not None:
            return "newer message"  # the newer job answers with the whole history
    return None


def _run_claimed(s: Session, job_id: int, deps: Deps, now: datetime) -> str:
    """Re-check, call the LLM port, execute its actions and finish the job: one transaction."""
    job = s.get(Job, job_id)
    if job is None:  # pragma: no cover - deleted between claim and load
        return "skipped"
    lead = s.get(Lead, job.lead_id)
    event = s.get(Event, job.event_id) if job.event_id is not None else None
    reason = _obsolete_reason(s, job, lead, event)
    result = "obsolete"
    if reason is not None:
        logger.info("job %s (%s, lead %s) not run: %s", job.id, job.kind, job.lead_id, reason)
    else:
        assert lead is not None
        actions: List[Action]
        if job.kind == "classify":
            if deps.classify is None:
                raise RuntimeError("classifier not wired")
            assert event is not None
            actions = deps.classify(s, lead, event, now)
        else:
            if deps.bot_reply is None:
                raise RuntimeError("chatbot not wired")
            actions = deps.bot_reply(s, lead, now)
        if actions:
            execute(s, lead, actions, deps, now, event, _depth=1)
        result = "done"
    job.status = "done"
    job.finished_at = now
    s.commit()  # alerts post to Teams after this commit (alerter hook)
    return result


def _give_up(s: Session, job: Job, deps: Deps, now: datetime, what: str) -> None:
    """The job will not run again: make sure a person sees the reply / the chat."""
    record_integration_error(s, job.lead_id, f"lead {job.lead_id}: {job.kind} job {job.id} failed "
                                             f"({what})", now)
    lead = s.get(Lead, job.lead_id)
    if lead is None:
        return
    if job.kind == "classify":
        execute(s, lead, [Alert("unsure", f"reply from lead {lead.id} could not be classified — "
                                          "please read it")], deps, now)
    elif lead.state not in _TERMINAL_VALUES and not lead.wa_human_owner and not takeover_active(lead):
        execute(s, lead, [StartTakeover("bot_error")], deps, now)  # the lead is not left unanswered


def _on_failure(s: Session, job_id: int, deps: Deps, now: datetime, exc: BaseException) -> str:
    """Fresh transaction after the rollback: back off and retry, or give up after MAX_ATTEMPTS."""
    error = type(exc).__name__[:ERROR_MAX]
    try:
        job = s.get(Job, job_id)
        if job is None:  # pragma: no cover
            return "failed"
        job.last_error = error
        if job.attempts < MAX_ATTEMPTS:
            job.status = "pending"
            job.run_after = now + BACKOFF[min(max(job.attempts, 1), len(BACKOFF)) - 1]
            s.commit()
            logger.warning("job %s (%s, lead %s) attempt %s failed: %s; retry at %s", job.id, job.kind,
                           job.lead_id, job.attempts, error, job.run_after.isoformat())
            return "retry"
        job.status = "failed"
        job.finished_at = now
        _give_up(s, job, deps, now, f"{MAX_ATTEMPTS} attempts, last error {error}")
        s.commit()
        logger.error("job %s (%s, lead %s) failed after %s attempts: %s", job.id, job.kind, job.lead_id,
                     job.attempts, error)
        return "failed"
    except Exception as exc2:  # noqa: BLE001 - the stale-job reset in run_due recovers it
        s.rollback()
        logger.error("job %s: recording the failure failed: %s", job_id, type(exc2).__name__)
        return "error"


def run_job(session_factory: SessionFactory, job_id: int, deps: Deps, now_fn: NowFn,
            *, _chain: int = 0) -> str:
    """Run one job if it is pending and due. Never raises. Returns:
    "skipped" (not claimed: already taken, not due or unknown), "obsolete" (claimed but no longer
    needed: lead gone / terminal, a person has the chat, a newer bot job), "done", "retry",
    "failed" or "error" (could not even record the outcome; run_due's stale reset recovers it).
    Jobs that this job's actions enqueue (classify -> bot_reply) run right after it."""
    try:
        now = now_fn()
        if not _claim(session_factory, job_id, now):
            return "skipped"
    except Exception as exc:  # noqa: BLE001 - a background task must never raise
        logger.error("job %s: claim failed: %s", job_id, type(exc).__name__)
        return "error"
    chained: List[int] = []
    s = session_factory()
    try:
        try:
            result = _run_claimed(s, job_id, deps, now)
            chained = take_enqueued(s)
        except Exception as exc:  # noqa: BLE001 - any failure: roll back, retry later
            s.rollback()
            discard_enqueued(s)
            result = _on_failure(s, job_id, deps, now, exc)
    finally:
        s.close()
    if result == "done":
        logger.info("job %s done", job_id)
    if _chain < MAX_CHAIN:
        for cid in chained:
            run_job(session_factory, cid, deps, now_fn, _chain=_chain + 1)
    return result


# ====================================================================== worker entry point

def _reset_stale(s: Session, deps: Deps, now: datetime) -> None:
    """Crash recovery: a job "running" for over STALE_AFTER goes back to pending (or, out of
    attempts, to failed with the same give-up handling as run_job)."""
    cutoff = now - STALE_AFTER
    stale = list(s.scalars(select(Job).where(Job.status == "running", Job.started_at < cutoff)
                           .order_by(Job.id)))
    for job in stale:
        if job.attempts >= MAX_ATTEMPTS:
            job.status, job.finished_at, job.last_error = "failed", now, "Stale"
            _give_up(s, job, deps, now, "stale after the last attempt")
            logger.error("job %s (%s, lead %s) stale after %s attempts", job.id, job.kind, job.lead_id,
                         job.attempts)
        else:
            job.status, job.run_after = "pending", now
            logger.warning("job %s (%s, lead %s) stale, back to pending", job.id, job.kind, job.lead_id)
    s.commit()


def run_due(session_factory: SessionFactory, deps: Deps, now_fn: NowFn, *, limit: int = 20,
            grace: timedelta = DEFAULT_GRACE) -> int:
    """Run pending jobs that are due and older than `grace` (the request's BackgroundTask gets
    the first chance), oldest first, at most `limit`. Returns how many were run (claimed)."""
    now = now_fn()
    s = session_factory()
    try:
        _reset_stale(s, deps, now)
        ids = list(s.scalars(
            select(Job.id).where(Job.status == "pending", Job.run_after <= now, Job.created_at <= now - grace)
            .order_by(Job.created_at, Job.id).limit(limit)))
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    ran = 0
    for job_id in ids:
        if run_job(session_factory, job_id, deps, now_fn) != "skipped":
            ran += 1
    return ran
