from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import SQLColumnExpression
from sqlalchemy.sql.selectable import Exists

from studyhub.academics.models import (
    AcademicAudit,
    AcademicPeriod,
    CoordinationGroup,
    TeachingAssignment,
)
from studyhub.academics.schemas import AssignmentCreate, CoordinatorChange, GroupCreate
from studyhub.core.principal import Principal
from studyhub.infrastructure.database import Base
from studyhub.models import Membership, SchoolClass, Subject, User

ScopeID = UUID | SQLColumnExpression[UUID]


def check_revision(row, expected: int) -> None:
    if row.revision != expected:
        raise HTTPException(409, "Resource changed; reload before saving")


def audit(db: AsyncSession, p: Principal, action: str, resource_id: UUID, **details) -> None:
    # IDs and state changes only: never log prompts, answers or authentication material.
    db.add(
        AcademicAudit(
            school_id=p.school_id,
            actor_id=p.user.id,
            action=action,
            resource_id=resource_id,
            details=details,
        )
    )


async def record[Record: Base](
    db: AsyncSession, model: type[Record], school_id: UUID, record_id: UUID, *, lock: bool = False
) -> Record:
    query = select(model).where(model.school_id == school_id, model.id == record_id)
    if lock:
        query = query.with_for_update(read=True).execution_options(populate_existing=True)
    row = await db.scalar(query)
    if row is None:
        raise HTTPException(404, "Resource not found")
    return row


async def open_period(
    db: AsyncSession, school_id: UUID, period_id: UUID, *, exclusive: bool = False
) -> AcademicPeriod:
    # Lock order: period -> group -> question/module. Closing a period or changing
    # assignments waits for current edits; subsequent edits see the new state.
    period = await db.scalar(
        select(AcademicPeriod)
        .where(AcademicPeriod.id == period_id, AcademicPeriod.school_id == school_id)
        .with_for_update(read=not exclusive)
        .execution_options(populate_existing=True)
    )
    if period is None:
        raise HTTPException(404, "Academic period not found")
    if period.status != "open":
        raise HTTPException(409, "Academic period is closed")
    return period


def teaching_scope(
    school_id: ScopeID,
    period_id: ScopeID,
    subject_id: ScopeID,
    grade_level: int | SQLColumnExpression[int],
    teacher_id: ScopeID,
) -> Exists:
    return exists(
        select(TeachingAssignment.id)
        .join(
            SchoolClass,
            (SchoolClass.id == TeachingAssignment.class_id)
            & (SchoolClass.school_id == TeachingAssignment.school_id),
        )
        .where(
            TeachingAssignment.school_id == school_id,
            TeachingAssignment.period_id == period_id,
            TeachingAssignment.subject_id == subject_id,
            TeachingAssignment.teacher_id == teacher_id,
            TeachingAssignment.is_active.is_(True),
            SchoolClass.grade_level == grade_level,
        )
    )


async def eligible_teacher(db: AsyncSession, school_id: UUID, teacher_id: UUID) -> None:
    # Share-lock membership and user rows until commit, preventing deactivation
    # from racing with a permission-sensitive write.
    row = (
        await db.execute(
            select(Membership, User)
            .join(User)
            .where(
                Membership.school_id == school_id,
                Membership.user_id == teacher_id,
                Membership.role == "teacher",
                Membership.is_active.is_(True),
                User.is_active.is_(True),
            )
            .with_for_update(read=True)
        )
    ).first()
    if not row:
        raise HTTPException(422, "An active teacher in this school is required")


async def require_teaching(db: AsyncSession, group: CoordinationGroup, teacher_id: UUID) -> None:
    if not await db.scalar(
        select(
            teaching_scope(
                group.school_id, group.period_id, group.subject_id, group.grade_level, teacher_id
            )
        )
    ):
        raise HTTPException(403, "No active teaching assignment for this subject and grade")


async def group_access(
    db: AsyncSession,
    p: Principal,
    group_id: UUID,
    *,
    coordinator: bool = False,
    write: bool = False,
) -> CoordinationGroup:
    group = await record(db, CoordinationGroup, p.school_id, group_id)
    if write:
        await open_period(db, p.school_id, group.period_id)
        # Refresh after lock acquisition: a previous SELECT can be stale.
        group = await db.scalar(
            select(CoordinationGroup)
            .where(CoordinationGroup.id == group_id, CoordinationGroup.school_id == p.school_id)
            .with_for_update(read=not coordinator)
            .execution_options(populate_existing=True)
        )
    if coordinator:
        if group.coordinator_id != p.user.id or p.membership.role != "teacher":
            raise HTTPException(403, "The appointed coordinator is required")
        await eligible_teacher(db, p.school_id, p.user.id)
        await require_teaching(db, group, p.user.id)
    elif p.membership.role != "admin":
        await eligible_teacher(db, p.school_id, p.user.id)
        await require_teaching(db, group, p.user.id)
    return group


async def create_assignment(
    db: AsyncSession, p: Principal, data: AssignmentCreate
) -> TeachingAssignment:
    await open_period(db, p.school_id, data.period_id, exclusive=True)
    await record(db, SchoolClass, p.school_id, data.class_id, lock=True)
    await record(db, Subject, p.school_id, data.subject_id)
    await eligible_teacher(db, p.school_id, data.teacher_id)
    assignment = TeachingAssignment(school_id=p.school_id, **data.model_dump())
    db.add(assignment)
    await db.flush()
    audit(db, p, "teaching_assignment.created", assignment.id)
    await db.commit()
    return assignment


async def assignment_status(
    db: AsyncSession, p: Principal, assignment_id: UUID, active: bool
) -> TeachingAssignment:
    assignment = await record(db, TeachingAssignment, p.school_id, assignment_id)
    await open_period(db, p.school_id, assignment.period_id, exclusive=True)
    await db.refresh(assignment)
    if active:
        await eligible_teacher(db, p.school_id, assignment.teacher_id)
    assignment.is_active = active
    await db.flush()
    if not active:
        classroom = await record(db, SchoolClass, p.school_id, assignment.class_id)
        group = await db.scalar(
            select(CoordinationGroup).where(
                CoordinationGroup.school_id == p.school_id,
                CoordinationGroup.period_id == assignment.period_id,
                CoordinationGroup.subject_id == assignment.subject_id,
                CoordinationGroup.grade_level == classroom.grade_level,
                CoordinationGroup.coordinator_id == assignment.teacher_id,
            )
        )
        if group and not await db.scalar(
            select(
                teaching_scope(
                    p.school_id,
                    group.period_id,
                    group.subject_id,
                    group.grade_level,
                    assignment.teacher_id,
                )
            )
        ):
            raise HTTPException(
                409, "Replace or revoke the coordinator before removing their last assignment"
            )
    audit(db, p, "teaching_assignment.status", assignment.id, is_active=active)
    await db.commit()
    return assignment


async def create_group(db: AsyncSession, p: Principal, data: GroupCreate) -> CoordinationGroup:
    await open_period(db, p.school_id, data.period_id, exclusive=True)
    await record(db, Subject, p.school_id, data.subject_id)
    grades = (
        range(7, 10)
        if p.school.level == "SMP"
        else range(10, 14)
        if p.school.level == "SMK"
        else range(10, 13)
    )
    if data.grade_level not in grades:
        raise HTTPException(422, "Grade is incompatible with school level")
    await eligible_teacher(db, p.school_id, data.coordinator_id)
    group = CoordinationGroup(
        school_id=p.school_id, school_level=p.school.level, **data.model_dump()
    )
    await require_teaching(db, group, data.coordinator_id)
    db.add(group)
    await db.flush()
    audit(db, p, "coordinator.appointed", group.id, coordinator_id=str(data.coordinator_id))
    await db.commit()
    return group


async def change_coordinator(
    db: AsyncSession, p: Principal, group_id: UUID, data: CoordinatorChange
) -> CoordinationGroup:
    group = await record(db, CoordinationGroup, p.school_id, group_id)
    await open_period(db, p.school_id, group.period_id, exclusive=True)
    await db.refresh(group)
    if group.revision != data.expected_revision:
        raise HTTPException(409, "Coordination changed; reload before saving")
    if data.coordinator_id is not None:
        await eligible_teacher(db, p.school_id, data.coordinator_id)
        await require_teaching(db, group, data.coordinator_id)
    previous = str(group.coordinator_id) if group.coordinator_id else None
    group.coordinator_id = data.coordinator_id
    group.revision += 1
    audit(
        db,
        p,
        "coordinator.changed",
        group.id,
        previous=previous,
        coordinator_id=str(data.coordinator_id) if data.coordinator_id else None,
        revision=group.revision,
    )
    await db.commit()
    return group
