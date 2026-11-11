from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from studyhub.academics.models import TeachingAssignment
from studyhub.academics.service import group_access, record
from studyhub.api.dependencies import Principal, database, educator
from studyhub.assessments import service
from studyhub.assessments.models import ClassExam, ExamModule, ModuleVersion, Question
from studyhub.assessments.schemas import (
    ClassExamCreate,
    ClassExamRead,
    ModuleCreate,
    ModuleDetail,
    ModuleRead,
    ModuleUpdate,
    QuestionCreate,
    QuestionRead,
    QuestionReview,
    QuestionUpdate,
    RevisionInput,
    VersionRead,
    VersionSummary,
)

router = APIRouter(prefix="/api/v1", tags=["exam collaboration"])


@router.post("/questions", response_model=QuestionRead, status_code=201)
async def create_question(
    data: QuestionCreate, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    return await service.create_question(db, p, data)


@router.get("/questions", response_model=list[QuestionRead])
async def questions(
    group_id: UUID,
    status: Literal["draft", "submitted", "approved", "rejected"] | None = None,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0, le=10000),
):
    group = await group_access(db, p, group_id)
    query = select(Question).where(Question.school_id == p.school_id, Question.group_id == group_id)
    if p.membership.role != "admin" and p.user.id != group.coordinator_id:
        query = query.where(Question.author_id == p.user.id)
    if status:
        query = query.where(Question.status == status)
    return (await db.scalars(query.order_by(Question.id).limit(limit).offset(offset))).all()


@router.get("/questions/{question_id}", response_model=QuestionRead)
async def question(
    question_id: UUID, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    return await service.question_access(db, p, question_id)


@router.put("/questions/{question_id}", response_model=QuestionRead)
async def edit_question(
    question_id: UUID,
    data: QuestionUpdate,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    return await service.update_question(db, p, question_id, data)


@router.post("/questions/{question_id}/submit", response_model=QuestionRead)
async def submit_question(
    question_id: UUID,
    data: RevisionInput,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    return await service.submit_question(db, p, question_id, data)


@router.post("/questions/{question_id}/review", response_model=QuestionRead)
async def review_question(
    question_id: UUID,
    data: QuestionReview,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    return await service.review_question(db, p, question_id, data)


@router.post("/exam-modules", response_model=ModuleDetail, status_code=201)
async def create_module(
    data: ModuleCreate, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    return await service.create_module(db, p, data)


@router.get("/exam-modules", response_model=list[ModuleRead])
async def modules(
    group_id: UUID,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    await group_access(db, p, group_id)
    return (
        await db.scalars(
            select(ExamModule)
            .where(ExamModule.school_id == p.school_id, ExamModule.group_id == group_id)
            .order_by(ExamModule.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.get("/exam-modules/{module_id}", response_model=ModuleDetail)
async def module_detail(
    module_id: UUID, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    module = await record(db, ExamModule, p.school_id, module_id)
    group = await group_access(db, p, module.group_id)
    if p.membership.role != "admin" and group.coordinator_id != p.user.id:
        raise HTTPException(403, "Draft modules are private to the coordinator")
    return module


@router.put("/exam-modules/{module_id}", response_model=ModuleDetail)
async def edit_module(
    module_id: UUID,
    data: ModuleUpdate,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    return await service.update_module(db, p, module_id, data)


@router.post("/exam-modules/{module_id}/publish", response_model=VersionRead, status_code=201)
async def publish_module(
    module_id: UUID,
    data: RevisionInput,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    return await service.publish_module(db, p, module_id, data)


@router.get("/exam-modules/{module_id}/versions", response_model=list[VersionSummary])
async def versions(
    module_id: UUID,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    module = await record(db, ExamModule, p.school_id, module_id)
    await group_access(db, p, module.group_id)
    # Metadata only: listing many full packages would unnecessarily load answer payloads.
    columns = [getattr(ModuleVersion, field) for field in VersionSummary.model_fields]
    return (
        (
            await db.execute(
                select(*columns)
                .where(ModuleVersion.school_id == p.school_id, ModuleVersion.module_id == module_id)
                .order_by(ModuleVersion.version.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .mappings()
        .all()
    )


@router.get("/module-versions/{version_id}", response_model=VersionRead)
async def version_detail(
    version_id: UUID, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    version = await record(db, ModuleVersion, p.school_id, version_id)
    await group_access(db, p, version.group_id)
    return version


@router.post("/class-exams", response_model=ClassExamRead, status_code=201)
async def create_exam(
    data: ClassExamCreate, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    return await service.create_class_exam(db, p, data)


@router.get("/class-exams", response_model=list[ClassExamRead])
async def exams(
    period_id: UUID,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    query = (
        select(ClassExam)
        .join(
            TeachingAssignment,
            (ClassExam.assignment_id == TeachingAssignment.id)
            & (ClassExam.school_id == TeachingAssignment.school_id),
        )
        .where(ClassExam.school_id == p.school_id, TeachingAssignment.period_id == period_id)
    )
    if p.membership.role != "admin":
        teaching = aliased(TeachingAssignment)
        query = query.where(
            exists(
                select(teaching.id).where(
                    teaching.school_id == p.school_id,
                    teaching.period_id == ClassExam.period_id,
                    teaching.class_id == ClassExam.class_id,
                    teaching.subject_id == TeachingAssignment.subject_id,
                    teaching.teacher_id == p.user.id,
                    teaching.is_active.is_(True),
                )
            )
        )
    return (
        await db.scalars(
            query.order_by(ClassExam.starts_at, ClassExam.id).limit(limit).offset(offset)
        )
    ).all()
