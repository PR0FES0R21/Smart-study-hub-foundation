from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.api.dependencies import (
    Principal,
    administrator,
    current_user,
    database,
    platform_administrator,
    school_context,
)
from studyhub.models import ExpertiseProgram, Membership, School, SchoolClass, User
from studyhub.schemas import (
    ClassCreate,
    ClassRead,
    MembershipCreate,
    ProgramCreate,
    ProgramRead,
    SchoolCreate,
    SchoolRead,
)

router = APIRouter(prefix="/api/v1", tags=["schools"])


@router.post("/schools", response_model=SchoolRead, status_code=201)
async def create_school(
    payload: SchoolCreate,
    principal: Principal = Depends(platform_administrator),
    db: AsyncSession = Depends(database),
):
    school = School(**payload.model_dump())
    db.add(school)
    await db.flush()
    db.add(Membership(school_id=school.id, user_id=principal.user.id, role="admin"))
    await db.commit()
    return school


@router.get("/schools")
async def my_schools(
    principal: Principal = Depends(current_user),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    rows = (
        await db.execute(
            select(School, Membership.role)
            .join(Membership)
            .where(
                Membership.user_id == principal.user.id,
                Membership.is_active.is_(True),
                School.is_active.is_(True),
            )
            .order_by(School.code)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        {**SchoolRead.model_validate(school).model_dump(), "role": role} for school, role in rows
    ]


@router.get("/schools/current")
async def current_school(principal: Principal = Depends(school_context)):
    level = principal.school.level
    return {
        **SchoolRead.model_validate(principal.school).model_dump(),
        "role": principal.membership.role,
        "capabilities": {
            "expertise_programs": level == "SMK",
            "grade_levels": [7, 8, 9]
            if level == "SMP"
            else [10, 11, 12, 13]
            if level == "SMK"
            else [10, 11, 12],
        },
    }


@router.post("/schools/current/memberships", status_code=201)
async def add_membership(
    payload: MembershipCreate,
    principal: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    if not await db.get(User, payload.user_id):
        raise HTTPException(404, "User not found")
    db.add(Membership(school_id=principal.school_id, **payload.model_dump()))
    await db.commit()
    return {"school_id": principal.school_id, **payload.model_dump()}


@router.get("/expertise-programs", response_model=list[ProgramRead])
async def programs(
    principal: Principal = Depends(school_context),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return (
        await db.scalars(
            select(ExpertiseProgram)
            .where(ExpertiseProgram.school_id == principal.school_id)
            .order_by(ExpertiseProgram.code)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.post("/expertise-programs", response_model=ProgramRead, status_code=201)
async def create_program(
    payload: ProgramCreate,
    principal: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    if principal.school.level != "SMK":
        raise HTTPException(422, "Expertise programs are supported only for SMK")
    program = ExpertiseProgram(school_id=principal.school_id, **payload.model_dump())
    db.add(program)
    await db.commit()
    return program


@router.get("/classes", response_model=list[ClassRead])
async def classes(
    principal: Principal = Depends(school_context),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return (
        await db.scalars(
            select(SchoolClass)
            .where(SchoolClass.school_id == principal.school_id)
            .order_by(SchoolClass.name)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.post("/classes", response_model=ClassRead, status_code=201)
async def create_class(
    payload: ClassCreate,
    principal: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    level = principal.school.level
    grades = range(7, 10) if level == "SMP" else range(10, 14) if level == "SMK" else range(10, 13)
    if payload.grade_level not in grades or (payload.program_id is not None and level != "SMK"):
        raise HTTPException(422, "Grade or expertise program is incompatible with school level")
    if payload.program_id and not await db.scalar(
        select(ExpertiseProgram.id).where(
            ExpertiseProgram.id == payload.program_id,
            ExpertiseProgram.school_id == principal.school_id,
        )
    ):
        raise HTTPException(404, "Expertise program not found")
    classroom = SchoolClass(
        school_id=principal.school_id, school_level=level, **payload.model_dump()
    )
    db.add(classroom)
    await db.commit()
    return classroom
