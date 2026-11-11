from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics.people_models import MemberProfile
from studyhub.academics.student_models import StudentProfile
from studyhub.api.dependencies import Principal, database, educator, student
from studyhub.assessments import delivery_service as service
from studyhub.assessments.delivery_models import ExamAttempt, ExamParticipant
from studyhub.assessments.delivery_schemas import (
    AnswerBatch,
    AttemptRead,
    AttemptState,
    EssayGradeBatch,
    ExamSchedule,
    ParticipantRead,
    StudentExam,
    TeacherAttempt,
    TeacherAttemptDetail,
)
from studyhub.assessments.models import ClassExam, ModuleVersion
from studyhub.assessments.schemas import ClassExamRead, RevisionInput
from studyhub.models import User

router = APIRouter(prefix="/api/v1", tags=["exam delivery"])


@router.put("/class-exams/{exam_id}/schedule", response_model=ClassExamRead)
async def schedule(
    exam_id: UUID,
    data: ExamSchedule,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    return await service.schedule_exam(db, p, exam_id, data)


@router.post("/class-exams/{exam_id}/publish", response_model=ClassExamRead)
async def publish(
    exam_id: UUID,
    data: RevisionInput,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    return await service.publish_exam(db, p, exam_id, data)


@router.get("/class-exams/{exam_id}/participants", response_model=list[ParticipantRead])
async def participants(
    exam_id: UUID,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    await service.teacher_exam(db, p, exam_id, must_be_open=False)
    columns = [
        getattr(ExamParticipant, name)
        for name in ParticipantRead.model_fields
        if name not in {"full_name", "student_number"}
    ]
    query = (
        select(
            *columns,
            func.coalesce(MemberProfile.full_name, User.full_name).label("full_name"),
            StudentProfile.student_number,
        )
        .join(StudentProfile, StudentProfile.id == ExamParticipant.student_id)
        .join(User, User.id == StudentProfile.user_id)
        .outerjoin(
            MemberProfile,
            (MemberProfile.user_id == User.id)
            & (MemberProfile.school_id == StudentProfile.school_id),
        )
        .where(ExamParticipant.school_id == p.school_id, ExamParticipant.exam_id == exam_id)
        .order_by(ExamParticipant.id)
        .limit(limit)
        .offset(offset)
    )
    return (await db.execute(query)).mappings().all()


@router.get("/class-exams/{exam_id}/attempts", response_model=list[TeacherAttempt])
async def attempts(
    exam_id: UUID,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    await service.teacher_exam(db, p, exam_id, must_be_open=False)
    return (
        await db.scalars(
            select(ExamAttempt)
            .join(ExamParticipant, ExamParticipant.id == ExamAttempt.participant_id)
            .where(ExamAttempt.school_id == p.school_id, ExamParticipant.exam_id == exam_id)
            .order_by(ExamAttempt.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.get("/exam-attempts/{attempt_id}", response_model=TeacherAttemptDetail)
async def teacher_attempt(
    attempt_id: UUID, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    attempt, _, _ = await service.teacher_attempt_context(db, p, attempt_id)
    await db.commit()
    return attempt


@router.put("/exam-attempts/{attempt_id}/essay-grades", response_model=TeacherAttemptDetail)
async def grade(
    attempt_id: UUID,
    data: EssayGradeBatch,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    return await service.grade_essays(db, p, attempt_id, data)


@router.post("/class-exams/{exam_id}/release-results", response_model=ClassExamRead)
async def release(
    exam_id: UUID,
    data: RevisionInput,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    return await service.release_results(db, p, exam_id, data)


@router.get("/student/exams", response_model=list[StudentExam])
async def student_exams(
    p: Principal = Depends(student),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    # Fetch only public metadata; the answer-bearing snapshot is never loaded for a list.
    query = (
        select(
            ClassExam.id,
            ModuleVersion.snapshot["title"].astext.label("title"),
            ClassExam.starts_at,
            ClassExam.ends_at,
            ClassExam.duration_minutes,
            ClassExam.results_released_at,
        )
        .join(ModuleVersion, ModuleVersion.id == ClassExam.module_version_id)
        .join(ExamParticipant, ExamParticipant.exam_id == ClassExam.id)
        .join(StudentProfile, StudentProfile.id == ExamParticipant.student_id)
        .where(
            ClassExam.school_id == p.school_id,
            ClassExam.state == "published",
            StudentProfile.user_id == p.user.id,
        )
    )
    return (
        (
            await db.execute(
                query.order_by(ClassExam.starts_at, ClassExam.id).limit(limit).offset(offset)
            )
        )
        .mappings()
        .all()
    )


@router.post("/student/exams/{exam_id}/attempt", response_model=AttemptRead)
async def start(
    exam_id: UUID, p: Principal = Depends(student), db: AsyncSession = Depends(database)
):
    return await service.start_attempt(db, p, exam_id)


@router.get("/student/attempts/{attempt_id}", response_model=AttemptRead)
async def read(
    attempt_id: UUID, p: Principal = Depends(student), db: AsyncSession = Depends(database)
):
    return await service.read_attempt(db, p, attempt_id)


@router.put("/student/attempts/{attempt_id}/answers", response_model=AttemptState)
async def save(
    attempt_id: UUID,
    data: AnswerBatch,
    p: Principal = Depends(student),
    db: AsyncSession = Depends(database),
):
    return await service.save_answers(db, p, attempt_id, data)


@router.post("/student/attempts/{attempt_id}/submit", response_model=AttemptState)
async def submit(
    attempt_id: UUID,
    data: RevisionInput,
    p: Principal = Depends(student),
    db: AsyncSession = Depends(database),
):
    return await service.submit_attempt(db, p, attempt_id, data)
