import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from studyhub.core.config import get_settings
from studyhub.models import Job, Outbox, School, Subject
from studyhub.worker.celery_app import celery_app
from studyhub.worker.runtime import get_resources

logger = logging.getLogger(__name__)


@celery_app.task(name="studyhub.reconcile_jobs")
def reconcile_jobs():
    """Repair pending jobs if a broker loses a previously acknowledged message."""
    _, sessions, _, _ = get_resources()
    with sessions() as db:
        school_ids = db.scalars(select(School.id)).all()
    for school_id in school_ids:
        with sessions(info={"school_id": str(school_id)}) as db, db.begin():
            jobs = db.scalars(
                select(Job)
                .where(
                    Job.school_id == school_id,
                    Job.status == "pending",
                    Job.created_at < datetime.now(UTC) - timedelta(minutes=1),
                )
                .limit(50)
                .with_for_update(skip_locked=True)
            ).all()
            for job in jobs:
                pending = db.scalar(
                    select(Outbox.id)
                    .where(
                        Outbox.school_id == school_id,
                        Outbox.kind == "job.requested",
                        Outbox.payload["job_id"].astext == str(job.id),
                        Outbox.delivered_at.is_(None),
                    )
                    .limit(1)
                )
                if not pending:
                    db.add(
                        Outbox(
                            school_id=school_id,
                            kind="job.requested",
                            payload={"job_id": str(job.id)},
                        )
                    )


@celery_app.task(
    name="studyhub.subject_report",
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def subject_report(job_id: str, school_id: str):
    school_id = UUID(school_id)
    _, sessions, _, _ = get_resources()
    with sessions(info={"school_id": str(school_id)}) as db, db.begin():
        job = db.scalar(
            select(Job).where(Job.id == UUID(job_id), Job.school_id == school_id).with_for_update()
        )
        if not job or job.status == "completed":
            return  # At-least-once delivery; repeated execution has no extra side effects.
        job.result = {
            "subject_count": db.scalar(
                select(func.count()).select_from(Subject).where(Subject.school_id == school_id)
            )
        }
        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        db.add(
            Outbox(
                school_id=school_id,
                kind="user.event",
                payload={
                    "user_id": str(job.owner_id),
                    "type": "job.completed",
                    "data": {"job_id": str(job.id)},
                },
            )
        )


@celery_app.task(name="studyhub.dispatch_outbox")
def dispatch_outbox():
    settings = get_settings()
    _, sessions, control, cache = get_resources()
    sent = 0
    with sessions.begin() as db:
        rows = db.scalars(
            select(Outbox)
            .where(Outbox.delivered_at.is_(None), Outbox.available_at <= datetime.now(UTC))
            .order_by(Outbox.created_at)
            .limit(10)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            try:
                if row.kind == "job.requested":
                    celery_app.send_task(
                        "studyhub.subject_report",
                        args=[row.payload["job_id"], str(row.school_id)],
                        task_id=str(row.id),
                    )
                elif row.kind == "subjects.changed":
                    cache.incr(
                        f"{settings.namespace}:cache:v1:school:{row.school_id}:subjects:generation"
                    )
                elif row.kind == "user.event":
                    control.publish(
                        f"{settings.namespace}:events:v1",
                        json.dumps(
                            {
                                **row.payload,
                                "event_id": str(row.id),
                                "school_id": str(row.school_id),
                                "version": 1,
                            }
                        ),
                    )
                else:
                    raise ValueError("Unknown outbox kind")
                row.delivered_at = datetime.now(UTC)
                sent += 1
            except Exception as exc:
                row.attempts += 1
                row.available_at = datetime.now(UTC) + timedelta(
                    seconds=min(2 ** min(row.attempts, 8), 300)
                )
                logger.warning(
                    "outbox_delivery_retry",
                    extra={
                        "event_id": str(row.id),
                        "school_id": str(row.school_id),
                        "error_type": type(exc).__name__,
                    },
                )
            db.flush()
    return sent
