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
from typing import Callable, List, Optional, Tuple

from sqlalchemy import event as sa_event
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, SessionTransaction

from .actions import Action, Alert, Deps, StartTakeover, execute, record_integration_error
from .chatbot import takeover_active
from .models import Event, Job, Lead
from .states import TERMINAL
from .whatsapp.sender import phone_suppressed, window_open

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
_HOOKED = "outreach_jobs_hooked"

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


def _within(txn: Optional[SessionTransaction], ended: SessionTransaction) -> bool:
    while txn is not None:
        if txn is ended:
            return True
        txn = txn.parent
    return False


def _on_soft_rollback(session: Session, previous: SessionTransaction) -> None:
    """Same pruning as the alert hook: an outer rollback drops every id, a savepoint rollback only
    the ids enqueued inside that savepoint."""
    pending: List[Tuple[int, Optional[SessionTransaction]]] = session.info.get(_ENQUEUED) or []
    if not pending:
        return
    if not previous.nested:
        pending.clear()
    else:
        pending[:] = [p for p in pending if not _within(p[1], previous)]


def _hook(session: Session) -> None:
    if not session.info.get(_HOOKED):
        sa_event.listen(session, "after_soft_rollback", _on_soft_rollback)
        session.info[_HOOKED] = True


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
        _hook(session)
        session.info.setdefault(_ENQUEUED, []).append((job.id, session.get_nested_transaction()))
        logger.info("lead %s: %s job %s queued", lead.id, kind, job.id)
        return job.id


def take_enqueued(session: Session) -> List[int]:
    """The ids this session enqueued and still holds since the last call (call right after commit)."""
    return [job_id for job_id, _txn in (session.info.pop(_ENQUEUED, None) or [])]


def discard_enqueued(session: Session) -> None:
    """Forget ids from a transaction that was rolled back."""
    session.info.pop(_ENQUEUED, None)


# ====================================================================== run one job
# Ownership: a run owns a job while status='running' AND attempts=<the attempt it claimed>. A stale
# reset or a re-claim changes one of the two, so every write that finishes a run is a conditional
# UPDATE on both; rowcount 0 means another run owns the job now and this one backs off
# ("lost_claim") without touching it.

class _LostClaim(Exception):
    pass


def _claim(session_factory: SessionFactory, job_id: int, now: datetime) -> Optional[int]:
    """Atomic claim: one conditional UPDATE in its own transaction. On Postgres a concurrent
    claimer blocks on the row lock and then re-checks status='pending' (0 rows); SQLite
    serialises writers. Exactly one caller wins; it gets the attempt number it claimed."""
    s = session_factory()
    try:
        res = s.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "pending", Job.run_after <= now)
            .values(status="running", started_at=now, attempts=Job.attempts + 1)
            .execution_options(synchronize_session=False))
        attempt: Optional[int] = None
        if res.rowcount == 1:
            attempt = s.scalar(select(Job.attempts).where(Job.id == job_id))  # our own write
        s.commit()
        return attempt
    finally:
        _close(s)


def _finish(s: Session, job_id: int, attempt: int, **values: object) -> None:
    """Conditional write of a run's outcome; raises _LostClaim if this run no longer owns the job."""
    res = s.execute(
        update(Job).where(Job.id == job_id, Job.status == "running", Job.attempts == attempt)
        .values(**values).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise _LostClaim()


def _owns(s: Session, job_id: int, attempt: int) -> bool:
    """Lock the job row (FOR UPDATE on Postgres: a stale reset now waits for our commit; SQLite
    ignores it and serialises writers) and confirm this run still owns it."""
    row = s.execute(select(Job.status, Job.attempts).where(Job.id == job_id).with_for_update()).first()
    return row is not None and row[0] == "running" and row[1] == attempt


def _newer_bot_job(s: Session, job: Job) -> bool:
    return s.scalar(
        select(Job.id).where(Job.lead_id == job.lead_id, Job.kind == "bot_reply", Job.id > job.id,
                             Job.status.in_(("pending", "running", "done"))).limit(1)) is not None


def _skip_reason(session: Session, job: Job, lead: Optional[Lead], event: Optional[Event],
                 now: datetime) -> Optional[str]:
    """Why this job needs no LLM call any more (None = run it)."""
    if lead is None:
        return "lead_gone"
    if lead.state in _TERMINAL_VALUES:
        return "terminal"
    if job.kind == "classify" and event is None:
        return "event_gone"
    if job.kind == "bot_reply":
        if lead.wa_human_owner:
            return "human_owner"
        if takeover_active(lead):
            return "takeover"
        # the answer could not be sent anyway: do not spend an LLM call on it
        if not lead.consent_whatsapp or not lead.phone:
            return "no_consent"
        if phone_suppressed(session, lead.phone):
            return "phone_suppressed"
        if not window_open(lead, now):
            return "window_closed"
        if _newer_bot_job(session, job):
            return "newer_message"  # the newer job answers with the whole history
    return None


def _run_claimed(s: Session, job_id: int, attempt: int, deps: Deps, now: datetime) -> str:
    """Re-check, call the LLM port, confirm ownership, execute its actions and finish the job:
    one transaction, committed here."""
    job = s.get(Job, job_id)
    if job is None:  # pragma: no cover - deleted between claim and load
        return "skipped"
    lead = s.get(Lead, job.lead_id)
    event = s.get(Event, job.event_id) if job.event_id is not None else None
    reason = _skip_reason(s, job, lead, event, now)
    if reason is not None:
        logger.info("job %s (%s, lead %s) not run: %s", job_id, job.kind, job.lead_id, reason)
        _finish(s, job_id, attempt, status="done", finished_at=now, last_error=f"skipped:{reason}"[:ERROR_MAX])
        s.commit()
        return "obsolete"
    assert lead is not None
    kind, lead_id = job.kind, job.lead_id
    actions: List[Action]
    if kind == "classify":
        if deps.classify is None:
            raise RuntimeError("classifier not wired")
        assert event is not None
        actions = deps.classify(s, lead, event, now)
    else:
        if deps.bot_reply is None:
            raise RuntimeError("chatbot not wired")
        actions = deps.bot_reply(s, lead, now)
    # The LLM call may have taken a while: confirm the job is still ours before anything is applied
    # or sent, and that no newer message arrived meanwhile.
    if not _owns(s, job_id, attempt):
        raise _LostClaim()
    if kind == "bot_reply" and _newer_bot_job(s, job):
        s.rollback()  # discard the answer (and anything the port wrote)
        logger.info("job %s (bot_reply, lead %s) superseded by a newer message", job_id, lead_id)
        _finish(s, job_id, attempt, status="done", finished_at=now, last_error="superseded")
        s.commit()
        return "obsolete"
    if actions:
        # External sends happen inside execute(). If a later step of this transaction fails, the DB
        # work rolls back and the job is retried, so a message that already went out can be sent
        # again on the retry: providers give no transactional send, and a lost reply is worse.
        execute(s, lead, actions, deps, now, event, _depth=1)
    _finish(s, job_id, attempt, status="done", finished_at=now)
    s.commit()  # alerts post to Teams after this commit (alerter hook)
    return "done"


def _give_up(s: Session, job_id: int, kind: str, lead_id: int, deps: Deps, now: datetime, what: str) -> None:
    """The job will not run again: make sure a person sees the reply / the chat."""
    record_integration_error(s, lead_id, f"lead {lead_id}: {kind} job {job_id} failed ({what})", now)
    lead = s.get(Lead, lead_id)
    if lead is None:
        return
    if kind == "classify":
        execute(s, lead, [Alert("unsure", f"reply from lead {lead.id} could not be classified — "
                                          "please read it")], deps, now)
    elif lead.state not in _TERMINAL_VALUES and not lead.wa_human_owner and not takeover_active(lead):
        execute(s, lead, [StartTakeover("bot_error")], deps, now)  # the lead is not left unanswered


def _on_failure(s: Session, job_id: int, attempt: int, deps: Deps, now: datetime, exc: BaseException) -> str:
    """Fresh transaction after the rollback: back off and retry, or give up after MAX_ATTEMPTS.
    Only while this run still owns the job: a job that is done or re-claimed is left alone."""
    error = type(exc).__name__[:ERROR_MAX]
    row = s.execute(select(Job.kind, Job.lead_id).where(Job.id == job_id)).first()
    if row is None:  # pragma: no cover
        return "failed"
    kind, lead_id = row
    if attempt < MAX_ATTEMPTS:
        run_after = now + BACKOFF[min(attempt, len(BACKOFF)) - 1]
        _finish(s, job_id, attempt, status="pending", run_after=run_after, last_error=error)
        s.commit()
        logger.warning("job %s (%s, lead %s) attempt %s failed: %s; retry at %s", job_id, kind, lead_id,
                       attempt, error, run_after.isoformat())
        return "retry"
    _finish(s, job_id, attempt, status="failed", finished_at=now, last_error=error)
    _give_up(s, job_id, kind, lead_id, deps, now, f"{MAX_ATTEMPTS} attempts, last error {error}")
    s.commit()
    logger.error("job %s (%s, lead %s) failed after %s attempts: %s", job_id, kind, lead_id, attempt, error)
    return "failed"


def _close(s: Session) -> None:
    try:
        s.close()
    except Exception as exc:  # noqa: BLE001 - never let cleanup raise
        logger.error("job session close failed: %s", type(exc).__name__)


def _rollback(s: Session) -> None:
    s.rollback()
    discard_enqueued(s)


def run_job(session_factory: SessionFactory, job_id: int, deps: Deps, now_fn: NowFn,
            *, _chain: int = 0) -> str:
    """Run one job if it is pending and due. Never raises. Returns:
    "skipped" (not claimed: already taken, not due or unknown), "obsolete" (claimed but no longer
    needed: lead gone / terminal, WhatsApp not usable, a person has the chat, a newer bot job),
    "done", "retry", "failed", "lost_claim" (another run took the job over meanwhile; nothing of
    this run was kept) or "error" (could not run or record it; run_due's stale reset recovers it).
    Jobs that this job's actions enqueue (classify -> bot_reply) run right after it."""
    try:
        return _run_job(session_factory, job_id, deps, now_fn, _chain)
    except Exception as exc:  # noqa: BLE001 - a background task must never raise
        logger.error("job %s: run failed: %s", job_id, type(exc).__name__)
        return "error"


def _run_job(session_factory: SessionFactory, job_id: int, deps: Deps, now_fn: NowFn, chain: int) -> str:
    now = now_fn()
    attempt = _claim(session_factory, job_id, now)
    if attempt is None:
        return "skipped"
    chained: List[int] = []
    s = session_factory()
    try:
        try:
            result = _run_claimed(s, job_id, attempt, deps, now)
            chained = take_enqueued(s)
        except _LostClaim:
            _rollback(s)
            result = "lost_claim"
        except Exception as exc:  # noqa: BLE001 - any failure: roll back, retry later
            _rollback(s)
            try:
                result = _on_failure(s, job_id, attempt, deps, now, exc)
            except _LostClaim:
                _rollback(s)
                result = "lost_claim"
    finally:
        _close(s)
    if result == "lost_claim":
        logger.warning("job %s: attempt %s lost its claim to another run; result discarded", job_id, attempt)
    elif result == "done":
        logger.info("job %s done", job_id)
    if chain < MAX_CHAIN:
        for cid in chained:
            run_job(session_factory, cid, deps, now_fn, _chain=chain + 1)
    return result


# ====================================================================== worker entry point

def _stale_jobs(s: Session, cutoff: datetime) -> List[Tuple[int, int]]:
    return [(r[0], r[1]) for r in s.execute(
        select(Job.id, Job.attempts).where(Job.status == "running", Job.started_at < cutoff).order_by(Job.id))]


def _reset_stale(s: Session, deps: Deps, now: datetime) -> None:
    """Crash recovery: a job "running" for over STALE_AFTER goes back to pending (or, out of
    attempts, to failed with the same give-up handling as run_job). Each reset is a conditional
    UPDATE on the attempt seen, so a run that finishes meanwhile keeps its result."""
    cutoff = now - STALE_AFTER
    candidates = _stale_jobs(s, cutoff)
    s.commit()
    for job_id, attempt in candidates:
        try:
            out_of_attempts = attempt >= MAX_ATTEMPTS
            values = (dict(status="failed", finished_at=now, last_error="Stale") if out_of_attempts
                      else dict(status="pending", run_after=now))
            res = s.execute(
                update(Job).where(Job.id == job_id, Job.status == "running", Job.started_at < cutoff,
                                  Job.attempts == attempt)
                .values(**values).execution_options(synchronize_session=False))
            if res.rowcount != 1:
                s.rollback()
                continue
            row = s.execute(select(Job.kind, Job.lead_id).where(Job.id == job_id)).one()
            if out_of_attempts:
                _give_up(s, job_id, row[0], row[1], deps, now, "stale after the last attempt")
                logger.error("job %s (%s, lead %s) stale after %s attempts", job_id, row[0], row[1], attempt)
            else:
                logger.warning("job %s (%s, lead %s) stale, back to pending", job_id, row[0], row[1])
            s.commit()
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop recovery of the rest
            s.rollback()
            logger.error("job %s: stale reset failed: %s", job_id, type(exc).__name__)


RAN = frozenset({"obsolete", "done", "retry", "failed", "lost_claim"})


def run_due(session_factory: SessionFactory, deps: Deps, now_fn: NowFn, *, limit: int = 20,
            grace: timedelta = DEFAULT_GRACE, after_each: Optional[Callable[[], None]] = None) -> int:
    """Run pending jobs that are due and older than `grace` (the request's BackgroundTask gets
    the first chance), oldest first, at most `limit`. Returns how many were claimed and run
    (not "skipped" or "error"); one failing job never stops the batch."""
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
        _close(s)
    ran = 0
    for job_id in ids:
        try:
            result = run_job(session_factory, job_id, deps, now_fn)
        except Exception as exc:  # noqa: BLE001 - run_job never raises; belt and braces for the batch
            logger.error("job %s: run crashed: %s", job_id, type(exc).__name__)
            continue
        if result in RAN:
            ran += 1
        if after_each is not None:
            try:
                after_each()  # the worker renews its lock lease: LLM jobs can be slow
            except Exception as exc:  # noqa: BLE001 - never stop the batch for it
                logger.warning("run_due after_each failed: %s", type(exc).__name__)
    return ran
