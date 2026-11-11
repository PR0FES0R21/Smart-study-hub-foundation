"""Synchronous expiry reconciler; no dependency on async API sessions or routes."""

from sqlalchemy import func, select

from studyhub.academics.models import AcademicPeriod
from studyhub.academics.student_models import StudentProfile
from studyhub.assessments.delivery_models import ExamAttempt, ExamParticipant
from studyhub.assessments.grading import completion_event, finalize
from studyhub.assessments.models import ClassExam, ModuleVersion
from studyhub.models import School
from studyhub.worker.celery_app import celery_app
from studyhub.worker.runtime import get_resources


@celery_app.task(name="studyhub.expire_exam_attempts")
def expire_exam_attempts():
    _, sessions, _, _ = get_resources()
    cursor, completed = None, 0
    while True:
        with sessions() as db:
            query = select(School.id).order_by(School.id).limit(100)
            if cursor:
                query = query.where(School.id > cursor)
            schools = db.scalars(query).all()
        if not schools:
            return completed
        cursor = schools[-1]
        for school_id in schools:
            with sessions(info={"school_id": str(school_id)}) as db:
                cutoff = db.scalar(select(func.clock_timestamp()))
                rows = db.execute(
                    select(ExamAttempt.id, ExamParticipant.exam_id, ExamParticipant.period_id)
                    .join(ExamParticipant, ExamParticipant.id == ExamAttempt.participant_id)
                    .where(
                        ExamAttempt.school_id == school_id,
                        ExamAttempt.status == "in_progress",
                        ExamAttempt.deadline_at <= cutoff,
                    )
                    .order_by(ExamAttempt.deadline_at)
                    .limit(100)
                ).all()
            for attempt_id, exam_id, period_id in rows:
                with sessions(info={"school_id": str(school_id)}) as db, db.begin():
                    # Match API lock order. SKIP LOCKED lets another worker/API finish the attempt.
                    db.scalar(
                        select(AcademicPeriod)
                        .where(
                            AcademicPeriod.id == period_id, AcademicPeriod.school_id == school_id
                        )
                        .with_for_update(read=True)
                    )
                    exam = db.scalar(
                        select(ClassExam)
                        .where(ClassExam.id == exam_id, ClassExam.school_id == school_id)
                        .with_for_update(read=True)
                    )
                    attempt = db.scalar(
                        select(ExamAttempt)
                        .where(ExamAttempt.id == attempt_id, ExamAttempt.school_id == school_id)
                        .with_for_update(skip_locked=True)
                    )
                    now = db.scalar(select(func.clock_timestamp()))
                    if not attempt or attempt.status != "in_progress" or attempt.deadline_at > now:
                        continue
                    version = db.get(ModuleVersion, exam.module_version_id)
                    participant = db.get(ExamParticipant, attempt.participant_id)
                    student = db.get(StudentProfile, participant.student_id)
                    if finalize(attempt, version.snapshot, now, "timeout"):
                        db.add(completion_event(attempt, student.user_id))
                        completed += 1
