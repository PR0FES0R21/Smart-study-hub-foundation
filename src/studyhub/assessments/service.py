import hashlib
import json
from datetime import UTC
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics.models import AcademicPeriod, TeachingAssignment
from studyhub.academics.service import audit, eligible_teacher, group_access, record
from studyhub.assessments.models import ClassExam, ExamModule, ModuleVersion, Question
from studyhub.assessments.schemas import (
    ClassExamCreate,
    ModuleCreate,
    ModuleItem,
    ModuleUpdate,
    QuestionContent,
    QuestionCreate,
    QuestionReview,
    QuestionUpdate,
    RevisionInput,
)
from studyhub.core.principal import Principal
from studyhub.models import SchoolClass


def require_revision(row: Question | ExamModule, expected: int) -> None:
    if row.revision != expected:
        raise HTTPException(409, "Resource changed; reload before saving")


def require_author(question: Question, p: Principal) -> None:
    if question.author_id != p.user.id or p.membership.role != "teacher":
        raise HTTPException(403, "Only the question author may perform this action")


async def create_question(db: AsyncSession, p: Principal, data: QuestionCreate) -> Question:
    if p.membership.role != "teacher":
        raise HTTPException(403, "Teacher role required")
    await group_access(db, p, data.group_id, write=True)
    question = Question(
        school_id=p.school_id,
        group_id=data.group_id,
        author_id=p.user.id,
        content=data.content.model_dump(mode="json"),
    )
    db.add(question)
    await db.flush()
    audit(db, p, "question.created", question.id, group_id=str(question.group_id))
    await db.commit()
    return question


async def question_access(
    db: AsyncSession, p: Principal, question_id: UUID, *, write: bool = False, review: bool = False
) -> Question:
    question = await record(db, Question, p.school_id, question_id)
    group = await group_access(db, p, question.group_id, write=write, coordinator=review)
    if (
        not review
        and p.membership.role != "admin"
        and p.user.id not in {question.author_id, group.coordinator_id}
    ):
        raise HTTPException(403, "Question is private to its author and coordinator")
    if write:
        question = await db.scalar(
            select(Question)
            .where(Question.id == question_id, Question.school_id == p.school_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return question


async def update_question(
    db: AsyncSession, p: Principal, question_id: UUID, data: QuestionUpdate
) -> Question:
    question = await question_access(db, p, question_id, write=True)
    require_author(question, p)
    require_revision(question, data.expected_revision)
    if question.status == "submitted":
        raise HTTPException(409, "Submitted questions are locked until reviewed")
    question.content = data.content.model_dump(mode="json")
    question.revision += 1
    question.status = "draft"
    question.review_note = None
    audit(db, p, "question.edited", question.id, revision=question.revision)
    await db.commit()
    return question


async def submit_question(
    db: AsyncSession, p: Principal, question_id: UUID, data: RevisionInput
) -> Question:
    question = await question_access(db, p, question_id, write=True)
    require_author(question, p)
    require_revision(question, data.expected_revision)
    if question.status not in {"draft", "rejected"}:
        raise HTTPException(409, "Only draft or rejected questions can be submitted")
    question.status = "submitted"
    question.revision += 1
    question.review_note = None
    audit(db, p, "question.submitted", question.id, revision=question.revision)
    await db.commit()
    return question


async def review_question(
    db: AsyncSession, p: Principal, question_id: UUID, data: QuestionReview
) -> Question:
    question = await question_access(db, p, question_id, write=True, review=True)
    require_revision(question, data.expected_revision)
    if question.status != "submitted":
        raise HTTPException(409, "Only submitted questions can be reviewed")
    question.status = data.decision
    question.revision += 1
    question.review_note = data.note
    audit(
        db, p, "question.reviewed", question.id, revision=question.revision, decision=data.decision
    )
    await db.commit()
    return question


async def create_module(db: AsyncSession, p: Principal, data: ModuleCreate) -> ExamModule:
    await group_access(db, p, data.group_id, coordinator=True, write=True)
    module = ExamModule(school_id=p.school_id, **data.model_dump())
    db.add(module)
    await db.flush()
    audit(db, p, "module.created", module.id, group_id=str(module.group_id))
    await db.commit()
    return module


async def module_for_edit(
    db: AsyncSession, p: Principal, module_id: UUID, expected_revision: int
) -> ExamModule:
    module = await record(db, ExamModule, p.school_id, module_id)
    await group_access(db, p, module.group_id, coordinator=True, write=True)
    module = await db.scalar(
        select(ExamModule)
        .where(ExamModule.id == module_id, ExamModule.school_id == p.school_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    require_revision(module, expected_revision)
    return module


async def selected_questions(
    db: AsyncSession, school_id: UUID, group_id: UUID, items: list[ModuleItem]
) -> dict[UUID, Question]:
    ids = [item.question_id for item in items]
    # One bounded query, deterministic row-lock order; no N+1 lookups.
    questions = (
        await db.scalars(
            select(Question)
            .where(
                Question.school_id == school_id, Question.group_id == group_id, Question.id.in_(ids)
            )
            .order_by(Question.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    by_id = {q.id: q for q in questions}
    if len(by_id) != len(ids):
        raise HTTPException(422, "Every selected question must belong to this coordination group")
    for item in items:
        question = by_id[item.question_id]
        if question.status != "approved" or question.revision != item.question_revision:
            raise HTTPException(409, "A selected question changed or has not been approved")
    return by_id


async def update_module(
    db: AsyncSession, p: Principal, module_id: UUID, data: ModuleUpdate
) -> ExamModule:
    module = await module_for_edit(db, p, module_id, data.expected_revision)
    await selected_questions(db, p.school_id, module.group_id, data.items)
    module.title = data.title
    module.items = [item.model_dump(mode="json") for item in data.items]
    module.revision += 1
    audit(db, p, "module.edited", module.id, revision=module.revision)
    await db.commit()
    return module


async def publish_module(
    db: AsyncSession, p: Principal, module_id: UUID, data: RevisionInput
) -> ModuleVersion:
    module = await module_for_edit(db, p, module_id, data.expected_revision)
    if not module.items:
        raise HTTPException(422, "Cannot publish an empty module")
    items = [ModuleItem.model_validate(item) for item in module.items]
    questions = await selected_questions(db, p.school_id, module.group_id, items)
    snapshot = {
        "title": module.title,
        "total_points": sum(i.points for i in items),
        "items": [
            {
                **item.model_dump(mode="json"),
                "author_id": str(questions[item.question_id].author_id),
                "content": QuestionContent.model_validate(
                    questions[item.question_id].content
                ).model_dump(mode="json"),
            }
            for item in items
        ],
    }
    checksum = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    module.published_count += 1
    module.revision += 1
    version = ModuleVersion(
        school_id=p.school_id,
        module_id=module.id,
        group_id=module.group_id,
        version=module.published_count,
        snapshot=snapshot,
        checksum=checksum,
        published_by=p.user.id,
    )
    db.add(version)
    await db.flush()
    audit(
        db,
        p,
        "module.published",
        version.id,
        module_id=str(module.id),
        version=version.version,
        checksum=checksum,
    )
    await db.commit()
    return version


async def create_class_exam(db: AsyncSession, p: Principal, data: ClassExamCreate) -> ClassExam:
    version = await record(db, ModuleVersion, p.school_id, data.module_version_id)
    group = await group_access(db, p, version.group_id, write=True)
    await eligible_teacher(db, p.school_id, p.user.id)
    assignment = await record(db, TeachingAssignment, p.school_id, data.assignment_id)
    classroom = await record(db, SchoolClass, p.school_id, assignment.class_id)
    if (
        assignment.teacher_id != p.user.id
        or not assignment.is_active
        or assignment.period_id != group.period_id
        or assignment.subject_id != group.subject_id
        or classroom.grade_level != group.grade_level
    ):
        raise HTTPException(403, "This exam package does not match your teaching assignment")
    period = await record(db, AcademicPeriod, p.school_id, group.period_id)
    if not (
        period.starts_on
        <= data.starts_at.astimezone(UTC).date()
        <= data.ends_at.astimezone(UTC).date()
        <= period.ends_on
    ):
        raise HTTPException(422, "Exam dates must be inside the academic period")
    exam = ClassExam(
        school_id=p.school_id,
        class_id=assignment.class_id,
        created_by=p.user.id,
        **data.model_dump(),
    )
    db.add(exam)
    await db.flush()
    audit(
        db,
        p,
        "class_exam.created",
        exam.id,
        module_version_id=str(version.id),
        assignment_id=str(assignment.id),
    )
    await db.commit()
    return exam
