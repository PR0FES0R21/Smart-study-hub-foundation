from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics import service
from studyhub.academics.models import (
    AcademicAudit,
    AcademicPeriod,
    CoordinationGroup,
    TeachingAssignment,
)
from studyhub.academics.schemas import (
    AssignmentCreate,
    AssignmentRead,
    AssignmentStatus,
    AuditRead,
    CoordinatorChange,
    GroupCreate,
    GroupRead,
    PeriodCreate,
    PeriodRead,
)
from studyhub.api.dependencies import Principal, administrator, database, educator
from studyhub.models import Membership, User

router = APIRouter(prefix="/api/v1", tags=["academics"])


@router.post("/academic-periods", response_model=PeriodRead, status_code=201)
async def create_period(
    data: PeriodCreate, p: Principal = Depends(administrator), db: AsyncSession = Depends(database)
):
    period = AcademicPeriod(school_id=p.school_id, **data.model_dump())
    db.add(period)
    await db.flush()
    service.audit(db, p, "period.created", period.id)
    await db.commit()
    return period


@router.get("/academic-periods", response_model=list[PeriodRead])
async def periods(
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return (
        await db.scalars(
            select(AcademicPeriod)
            .where(AcademicPeriod.school_id == p.school_id)
            .order_by(AcademicPeriod.year_start.desc(), AcademicPeriod.semester.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.post("/academic-periods/{period_id}/close", response_model=PeriodRead)
async def close_period(
    period_id: UUID, p: Principal = Depends(administrator), db: AsyncSession = Depends(database)
):
    period = await service.open_period(db, p.school_id, period_id, exclusive=True)
    period.status = "closed"
    service.audit(db, p, "period.closed", period.id)
    await db.commit()
    return period


@router.post("/teaching-assignments", response_model=AssignmentRead, status_code=201)
async def create_assignment(
    data: AssignmentCreate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.create_assignment(db, p, data)


@router.get("/teaching-assignments", response_model=list[AssignmentRead])
async def assignments(
    period_id: UUID,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    query = select(TeachingAssignment).where(
        TeachingAssignment.school_id == p.school_id, TeachingAssignment.period_id == period_id
    )
    if p.membership.role != "admin":
        query = query.where(TeachingAssignment.teacher_id == p.user.id)
    return (
        await db.scalars(query.order_by(TeachingAssignment.id).limit(limit).offset(offset))
    ).all()


@router.put("/teaching-assignments/{assignment_id}/status", response_model=AssignmentRead)
async def assignment_status(
    assignment_id: UUID,
    data: AssignmentStatus,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.assignment_status(db, p, assignment_id, data.is_active)


@router.post("/coordination-groups", response_model=GroupRead, status_code=201)
async def create_group(
    data: GroupCreate, p: Principal = Depends(administrator), db: AsyncSession = Depends(database)
):
    return await service.create_group(db, p, data)


@router.get("/coordination-groups", response_model=list[GroupRead])
async def groups(
    period_id: UUID,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    query = select(CoordinationGroup).where(
        CoordinationGroup.school_id == p.school_id, CoordinationGroup.period_id == period_id
    )
    if p.membership.role != "admin":
        query = query.where(
            service.teaching_scope(
                p.school_id,
                CoordinationGroup.period_id,
                CoordinationGroup.subject_id,
                CoordinationGroup.grade_level,
                p.user.id,
            )
        )
    return (
        await db.scalars(query.order_by(CoordinationGroup.id).limit(limit).offset(offset))
    ).all()


@router.get("/coordination-groups/{group_id}", response_model=GroupRead)
async def group_detail(
    group_id: UUID, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    return await service.group_access(db, p, group_id)


@router.put("/coordination-groups/{group_id}/coordinator", response_model=GroupRead)
async def change_coordinator(
    group_id: UUID,
    data: CoordinatorChange,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.change_coordinator(db, p, group_id, data)


@router.get("/coordination-groups/{group_id}/teachers")
async def group_teachers(
    group_id: UUID,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    group = await service.group_access(db, p, group_id)
    rows = (
        await db.execute(
            select(User.id, User.full_name)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.school_id == p.school_id,
                Membership.role == "teacher",
                Membership.is_active.is_(True),
                User.is_active.is_(True),
                service.teaching_scope(
                    p.school_id, group.period_id, group.subject_id, group.grade_level, User.id
                ),
            )
            .order_by(User.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        {"id": row.id, "full_name": row.full_name, "is_coordinator": row.id == group.coordinator_id}
        for row in rows
    ]


@router.get("/academic-audit", response_model=list[AuditRead])
async def audit_entries(
    resource_id: UUID | None = None,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    query = select(AcademicAudit).where(AcademicAudit.school_id == p.school_id)
    if resource_id:
        query = query.where(AcademicAudit.resource_id == resource_id)
    return (
        await db.scalars(
            query.order_by(AcademicAudit.created_at.desc(), AcademicAudit.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
