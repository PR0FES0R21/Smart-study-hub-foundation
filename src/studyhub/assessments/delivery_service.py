from datetime import UTC, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics.enrollment_service import locked_student
from studyhub.academics.models import AcademicPeriod, TeachingAssignment
from studyhub.academics.service import audit, record
from studyhub.academics.student_models import StudentEnrollment, StudentProfile
from studyhub.assessments.delivery_models import ExamAttempt, ExamParticipant
from studyhub.assessments.delivery_schemas import AnswerBatch, EssayGradeBatch, ExamSchedule
from studyhub.assessments.grading import completion_event, finalize
from studyhub.assessments.models import ClassExam, ModuleVersion
from studyhub.assessments.schemas import RevisionInput
from studyhub.core.principal import Principal
from studyhub.models import Membership, User


async def db_now(db: AsyncSession):
    # clock_timestamp, unlike transaction_timestamp, advances while waiting for locks.
    return await db.scalar(select(func.clock_timestamp()))


async def period_guard(
    db: AsyncSession, school_id: UUID, period_id: UUID, *, exclusive=False, must_be_open=True
):
    period = await db.scalar(
        select(AcademicPeriod)
        .where(AcademicPeriod.id == period_id, AcademicPeriod.school_id == school_id)
        .with_for_update(read=not exclusive)
        .execution_options(populate_existing=True)
    )
    if period is None:
        raise HTTPException(404, "Academic period not found")
    if must_be_open and period.status != "open":
        raise HTTPException(409, "Academic period is closed")
    return period


def revision_matches(row, expected: int):
    if row.revision != expected:
        raise HTTPException(409, "Resource changed; reload before saving")


async def teacher_exam(
    db: AsyncSession,
    p: Principal,
    exam_id: UUID,
    *,
    exclusive=False,
    must_be_open=True,
    roster_lock=False,
) -> ClassExam:
    exam = await record(db, ClassExam, p.school_id, exam_id)
    await period_guard(
        db, p.school_id, exam.period_id, exclusive=roster_lock, must_be_open=must_be_open
    )
    exam = await db.scalar(
        select(ClassExam)
        .where(ClassExam.id == exam_id, ClassExam.school_id == p.school_id)
        .with_for_update(read=not exclusive)
        .execution_options(populate_existing=True)
    )
    assignment = await record(db, TeachingAssignment, p.school_id, exam.assignment_id)
    if p.membership.role != "admin":
        assigned = await db.scalar(
            select(TeachingAssignment.id)
            .where(
                TeachingAssignment.school_id == p.school_id,
                TeachingAssignment.period_id == exam.period_id,
                TeachingAssignment.class_id == exam.class_id,
                TeachingAssignment.subject_id == assignment.subject_id,
                TeachingAssignment.teacher_id == p.user.id,
                TeachingAssignment.is_active.is_(True),
            )
            .limit(1)
        )
        if p.membership.role != "teacher" or assigned is None:
            raise HTTPException(
                403, "An active teaching assignment for this class and subject is required"
            )
    return exam


async def schedule_exam(db: AsyncSession, p: Principal, exam_id: UUID, data: ExamSchedule):
    exam = await teacher_exam(db, p, exam_id, exclusive=True)
    revision_matches(exam, data.expected_revision)
    if exam.state != "draft":
        raise HTTPException(409, "Published schedules are frozen")
    period = await record(db, AcademicPeriod, p.school_id, exam.period_id)
    if not (
        period.starts_on
        <= data.starts_at.astimezone(UTC).date()
        <= data.ends_at.astimezone(UTC).date()
        <= period.ends_on
    ):
        raise HTTPException(422, "Exam dates must be inside the academic period")
    exam.starts_at, exam.ends_at, exam.duration_minutes = (
        data.starts_at,
        data.ends_at,
        data.duration_minutes,
    )
    exam.revision += 1
    audit(db, p, "exam.rescheduled", exam.id, revision=exam.revision)
    await db.commit()
    return exam


async def publish_exam(db: AsyncSession, p: Principal, exam_id: UUID, data: RevisionInput):
    # A short period-exclusive transaction freezes the roster against enrollment changes.
    exam = await teacher_exam(db, p, exam_id, exclusive=True, roster_lock=True)
    revision_matches(exam, data.expected_revision)
    if exam.state != "draft" or exam.ends_at <= await db_now(db):
        raise HTTPException(409, "Only a draft exam with a future closing time may be published")
    if await db.scalar(
        select(ClassExam.id)
        .where(
            ClassExam.school_id == p.school_id,
            ClassExam.module_version_id == exam.module_version_id,
            ClassExam.results_released_at.is_not(None),
        )
        .limit(1)
    ):
        raise HTTPException(
            409,
            "This package already has released results; publish a new module version for a new exam",
        )
    rows = (
        await db.execute(
            select(StudentEnrollment, StudentProfile)
            .join(StudentProfile, StudentProfile.id == StudentEnrollment.student_id)
            .join(User, User.id == StudentProfile.user_id)
            .join(
                Membership, (Membership.user_id == User.id) & (Membership.school_id == p.school_id)
            )
            .where(
                StudentEnrollment.school_id == p.school_id,
                StudentEnrollment.period_id == exam.period_id,
                StudentEnrollment.class_id == exam.class_id,
                StudentEnrollment.state == "active",
                StudentProfile.status == "active",
                Membership.role == "student",
                Membership.is_active.is_(True),
                User.is_active.is_(True),
            )
            .order_by(StudentEnrollment.id)
            .limit(1001)
        )
    ).all()
    if not rows or len(rows) > 1000:
        raise HTTPException(422, "Publication requires 1-1000 eligible enrolled students")
    db.add_all(
        [
            ExamParticipant(
                school_id=p.school_id,
                exam_id=exam.id,
                student_id=student.id,
                enrollment_id=enrollment.id,
                period_id=exam.period_id,
                class_id=exam.class_id,
            )
            for enrollment, student in rows
        ]
    )
    exam.state, exam.revision = "published", exam.revision + 1
    audit(db, p, "exam.published", exam.id, participants=len(rows), revision=exam.revision)
    await db.commit()
    return exam


async def owned_participant(db: AsyncSession, p: Principal, exam_id: UUID) -> ExamParticipant:
    participant = await db.scalar(
        select(ExamParticipant)
        .join(StudentProfile, StudentProfile.id == ExamParticipant.student_id)
        .where(
            ExamParticipant.school_id == p.school_id,
            ExamParticipant.exam_id == exam_id,
            StudentProfile.user_id == p.user.id,
        )
    )
    if participant is None:
        raise HTTPException(404, "Exam participant not found")
    return participant


async def student_exam_guard(
    db: AsyncSession, p: Principal, participant: ExamParticipant, *, writing=False
):
    exam = await record(db, ClassExam, p.school_id, participant.exam_id)
    await period_guard(db, p.school_id, exam.period_id, must_be_open=writing)
    if writing:
        await locked_student(db, p.school_id, participant.student_id, shared=True)
        enrollment = await record(db, StudentEnrollment, p.school_id, participant.enrollment_id)
        if enrollment.state != "active":
            raise HTTPException(409, "Enrollment is no longer active")
    exam = await db.scalar(
        select(ClassExam)
        .where(ClassExam.id == exam.id, ClassExam.school_id == p.school_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if exam.state != "published":
        raise HTTPException(409, "Exam is not published")
    return exam


def student_view(
    attempt: ExamAttempt, exam: ClassExam, version: ModuleVersion, now, *, include_questions=True
):
    # Explicit whitelist. Never serialize the module or grading JSON to student clients.
    result = None
    if exam.results_released_at is not None and attempt.status == "graded":
        result = {
            "score": attempt.score,
            "total_points": attempt.total_points,
            "percentage": (attempt.score * 100 / attempt.total_points).quantize(Decimal("0.01")),
        }
    view = {
        "id": attempt.id,
        "school_id": attempt.school_id,
        "exam_id": exam.id,
        "status": attempt.status,
        "revision": attempt.revision,
        "started_at": attempt.started_at,
        "deadline_at": attempt.deadline_at,
        "submitted_at": attempt.submitted_at,
        "submitted_reason": attempt.submitted_reason,
        "server_time": now,
        "result": result,
    }

    if include_questions:
        view.update(
            {
                "questions": [
                    {
                        "id": item["question_id"],
                        "kind": item["content"]["kind"],
                        "prompt": item["content"]["prompt"],
                        "options": item["content"].get("options", []),
                        "points": item["points"],
                    }
                    for item in version.snapshot["items"]
                ],
                "answers": attempt.answers,
            }
        )
    return view


async def start_attempt(db: AsyncSession, p: Principal, exam_id: UUID):
    participant = await owned_participant(db, p, exam_id)
    exam = await student_exam_guard(db, p, participant, writing=True)
    await db.refresh(participant, with_for_update=True)
    attempt = await db.scalar(
        select(ExamAttempt)
        .where(ExamAttempt.participant_id == participant.id, ExamAttempt.school_id == p.school_id)
        .with_for_update()
    )
    version = await record(db, ModuleVersion, p.school_id, exam.module_version_id)
    now = await db_now(db)
    if attempt is None:
        if not (exam.starts_at <= now < exam.ends_at):
            raise HTTPException(409, "Exam is outside its opening window")
        attempt = ExamAttempt(
            school_id=p.school_id,
            participant_id=participant.id,
            started_at=now,
            deadline_at=min(now + timedelta(minutes=exam.duration_minutes), exam.ends_at),
            total_points=version.snapshot["total_points"],
        )
        db.add(attempt)
        await db.flush()
        audit(db, p, "exam.started", attempt.id, exam_id=str(exam.id))
    elif attempt.status == "in_progress" and now >= attempt.deadline_at:
        finalize(attempt, version.snapshot, now, "timeout")
        db.add(completion_event(attempt, p.user.id))
    await db.commit()
    return student_view(attempt, exam, version, now)


async def own_attempt_context(db: AsyncSession, p: Principal, attempt_id: UUID, *, writing=False):
    attempt = await db.scalar(
        select(ExamAttempt)
        .join(ExamParticipant, ExamParticipant.id == ExamAttempt.participant_id)
        .join(StudentProfile, StudentProfile.id == ExamParticipant.student_id)
        .where(
            ExamAttempt.id == attempt_id,
            ExamAttempt.school_id == p.school_id,
            StudentProfile.user_id == p.user.id,
        )
    )
    if attempt is None:
        raise HTTPException(404, "Attempt not found")
    participant = await record(db, ExamParticipant, p.school_id, attempt.participant_id)
    exam = await student_exam_guard(db, p, participant, writing=writing)
    # Refresh under the lock, so autosaves from another API process cannot be overwritten.
    await db.refresh(attempt, with_for_update=True)
    version = await record(db, ModuleVersion, p.school_id, exam.module_version_id)
    return attempt, exam, version


async def read_attempt(db: AsyncSession, p: Principal, attempt_id: UUID):
    attempt, exam, version = await own_attempt_context(db, p, attempt_id)
    now = await db_now(db)
    if attempt.status == "in_progress" and now >= attempt.deadline_at:
        finalize(attempt, version.snapshot, now, "timeout")
        db.add(completion_event(attempt, p.user.id))
    await db.commit()
    return student_view(attempt, exam, version, now)


async def save_answers(db: AsyncSession, p: Principal, attempt_id: UUID, data: AnswerBatch):
    attempt, exam, version = await own_attempt_context(db, p, attempt_id, writing=True)
    now = await db_now(db)
    if attempt.status != "in_progress":
        raise HTTPException(409, "Submitted answers are immutable")
    if now >= attempt.deadline_at:
        finalize(attempt, version.snapshot, now, "timeout")
        db.add(completion_event(attempt, p.user.id))
        await db.commit()
        raise HTTPException(409, "Deadline passed; last saved answers have been submitted")
    revision_matches(attempt, data.expected_revision)
    items = {item["question_id"]: item for item in version.snapshot["items"]}
    answers = dict(attempt.answers)
    for answer in data.answers:
        key = str(answer.question_id)
        if key not in items:
            raise HTTPException(422, "Question does not belong to this exam")
        content = items[key]["content"]
        if (
            answer.value is not None
            and content["kind"] == "multiple_choice"
            and answer.value not in {o["key"] for o in content["options"]}
        ):
            raise HTTPException(422, "Invalid answer option")
        if answer.value:
            answers[key] = answer.value
        else:
            answers.pop(key, None)
    attempt.answers = answers
    attempt.revision += 1
    await db.commit()
    return student_view(attempt, exam, version, now, include_questions=False)


async def submit_attempt(db: AsyncSession, p: Principal, attempt_id: UUID, data: RevisionInput):
    attempt, exam, version = await own_attempt_context(db, p, attempt_id, writing=True)
    now = await db_now(db)
    if attempt.status == "in_progress":
        reason = "timeout" if now >= attempt.deadline_at else "manual"
        if reason == "manual":
            revision_matches(attempt, data.expected_revision)
        finalize(attempt, version.snapshot, now, reason)
        db.add(completion_event(attempt, p.user.id))
        audit(db, p, "exam.submitted", attempt.id, reason=reason)
    await db.commit()
    return student_view(attempt, exam, version, now, include_questions=False)


async def teacher_attempt_context(db: AsyncSession, p: Principal, attempt_id: UUID):
    attempt = await record(db, ExamAttempt, p.school_id, attempt_id)
    participant = await record(db, ExamParticipant, p.school_id, attempt.participant_id)
    exam = await teacher_exam(db, p, participant.exam_id, must_be_open=False)
    await db.refresh(attempt, with_for_update=True)
    version = await record(db, ModuleVersion, p.school_id, exam.module_version_id)
    now = await db_now(db)
    if attempt.status == "in_progress" and now >= attempt.deadline_at:
        finalize(attempt, version.snapshot, now, "timeout")
        student = await record(db, StudentProfile, p.school_id, participant.student_id)
        db.add(completion_event(attempt, student.user_id))
    return attempt, exam, version


async def grade_essays(db: AsyncSession, p: Principal, attempt_id: UUID, data: EssayGradeBatch):
    attempt, exam, version = await teacher_attempt_context(db, p, attempt_id)
    if exam.results_released_at is not None or attempt.status == "in_progress":
        raise HTTPException(409, "Grade only submitted attempts before result release")
    revision_matches(attempt, data.expected_revision)
    items = {item["question_id"]: item for item in version.snapshot["items"]}
    grades = dict(attempt.grading)
    for grade in data.grades:
        key = str(grade.question_id)
        item = items.get(key)
        if not item or item["content"]["kind"] != "essay" or not attempt.answers.get(key):
            raise HTTPException(422, "Only answered essay questions accept manual grades")
        if grade.points > item["points"]:
            raise HTTPException(422, "Awarded points exceed the question maximum")
        grades[key] = {"points": str(grade.points), "source": "manual", "graded_by": str(p.user.id)}
    attempt.grading = grades
    attempt.pending_essays = sum(value["points"] is None for value in grades.values())
    attempt.score = sum(
        (Decimal(value["points"]) for value in grades.values() if value["points"] is not None),
        Decimal(0),
    )
    attempt.status = "submitted" if attempt.pending_essays else "graded"
    attempt.revision += 1
    audit(
        db,
        p,
        "exam.graded",
        attempt.id,
        revision=attempt.revision,
        question_ids=[str(g.question_id) for g in data.grades],
    )
    await db.commit()
    return attempt


async def release_results(db: AsyncSession, p: Principal, exam_id: UUID, data: RevisionInput):
    exam = await teacher_exam(db, p, exam_id, exclusive=True, must_be_open=False)
    revision_matches(exam, data.expected_revision)
    now = await db_now(db)
    if exam.state != "published" or now < exam.ends_at or exam.results_released_at is not None:
        raise HTTPException(409, "Results may be released once after the exam window closes")
    if await db.scalar(
        select(ClassExam.id)
        .where(
            ClassExam.school_id == p.school_id,
            ClassExam.module_version_id == exam.module_version_id,
            ClassExam.state == "published",
            ClassExam.ends_at > now,
        )
        .limit(1)
    ):
        raise HTTPException(
            409, "Wait until every published class exam using this package has closed"
        )
    unfinished = await db.scalar(
        select(ExamAttempt.id)
        .join(ExamParticipant, ExamParticipant.id == ExamAttempt.participant_id)
        .where(
            ExamParticipant.exam_id == exam.id,
            ExamAttempt.school_id == p.school_id,
            ExamAttempt.status != "graded",
        )
        .limit(1)
    )
    if unfinished:
        raise HTTPException(
            409, "Finalize expired attempts and grade remaining essays before releasing results"
        )
    exam.results_released_at, exam.revision = now, exam.revision + 1
    audit(db, p, "exam.results_released", exam.id, revision=exam.revision)
    await db.commit()
    return exam
