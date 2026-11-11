from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics.models import (
    AcademicPeriod,
    AcademicYear,
    ClassOffering,
    CoordinationGroup,
    TeachingAssignment,
)
from studyhub.academics.service import audit, check_revision, eligible_teacher, open_period, record
from studyhub.academics.student_models import StudentEnrollment
from studyhub.core.principal import Principal
from studyhub.models import ExpertiseProgram, School, SchoolClass


async def locked(db: AsyncSession, model, school_id: UUID, entity_id: UUID, *, shared=False):
    item = await db.scalar(
        select(model)
        .where(model.school_id == school_id, model.id == entity_id)
        .with_for_update(read=shared)
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise HTTPException(404, "Resource not found")
    return item


async def calendar_lock(db: AsyncSession, school_id: UUID):
    # Calendar changes are infrequent. Serialize across years so concurrent
    # adjacent-year writes cannot introduce overlapping semesters.
    # NO KEY UPDATE lets concurrent academic writes verify their school FK;
    # FOR UPDATE here would invert period/profile -> school audit lock order.
    await db.scalar(select(School.id).where(School.id == school_id).with_for_update(key_share=True))


async def ensure_year(db: AsyncSession, school_id: UUID, year_start: int):
    await db.execute(
        insert(AcademicYear)
        .values(school_id=school_id, year_start=year_start)
        .on_conflict_do_nothing(index_elements=["school_id", "year_start"])
    )


async def validate_calendar(db: AsyncSession, school_id: UUID, data, exclude: UUID | None = None):
    query = select(AcademicPeriod).where(AcademicPeriod.school_id == school_id)
    if exclude:
        query = query.where(AcademicPeriod.id != exclude)
    # At most 402 semesters per school (bounded year range + unique key).
    periods = (await db.scalars(query)).all()
    for other in periods:
        earlier = (data.year_start, data.semester) < (other.year_start, other.semester)
        if (earlier and data.ends_on >= other.starts_on) or (
            not earlier and data.starts_on <= other.ends_on
        ):
            raise HTTPException(409, "Semesters must be ordered and their dates must not overlap")


async def create_period(db: AsyncSession, p: Principal, data):
    await calendar_lock(db, p.school_id)
    await validate_calendar(db, p.school_id, data)
    # Preserve the v0.4 API: posting a semester can also create its year container.
    await ensure_year(db, p.school_id, data.year_start)
    period = AcademicPeriod(school_id=p.school_id, **data.model_dump())
    db.add(period)
    await db.flush()
    audit(db, p, "period.created", period.id)
    await db.commit()
    return period


async def period_used(db: AsyncSession, school_id: UUID, period_id: UUID) -> bool:
    return bool(
        await db.scalar(
            select(
                or_(
                    *[
                        exists().where(model.school_id == school_id, model.period_id == period_id)
                        for model in (
                            TeachingAssignment,
                            CoordinationGroup,
                            StudentEnrollment,
                            ClassOffering,
                        )
                    ]
                )
            )
        )
    )


async def update_period(db: AsyncSession, p: Principal, entity_id: UUID, data):
    await calendar_lock(db, p.school_id)
    period = await locked(db, AcademicPeriod, p.school_id, entity_id)
    check_revision(period, data.expected_revision)
    if (period.year_start, period.semester) != (data.year_start, data.semester):
        raise HTTPException(
            409, "Year and semester identity cannot be changed; create a new period"
        )
    if period.status != "open" or await period_used(db, p.school_id, entity_id):
        raise HTTPException(409, "A closed or referenced semester cannot be rescheduled")
    await validate_calendar(db, p.school_id, data, exclude=entity_id)
    period.starts_on, period.ends_on = data.starts_on, data.ends_on
    period.revision += 1
    audit(db, p, "period.updated", entity_id, revision=period.revision)
    await db.commit()
    return period


async def delete_period(db: AsyncSession, p: Principal, entity_id: UUID, expected: int):
    await calendar_lock(db, p.school_id)
    period = await locked(db, AcademicPeriod, p.school_id, entity_id)
    check_revision(period, expected)
    if await period_used(db, p.school_id, entity_id):
        raise HTTPException(409, "Semester has academic history; close it instead")
    await db.delete(period)
    audit(db, p, "period.deleted", entity_id)
    await db.commit()


async def validate_class(db: AsyncSession, p: Principal, data):
    level = p.school.level
    grades = range(7, 10) if level == "SMP" else range(10, 14) if level == "SMK" else range(10, 13)
    if data.grade_level not in grades or (data.program_id is not None and level != "SMK"):
        raise HTTPException(422, "Grade or expertise program is incompatible with school level")
    if data.program_id:
        await record(db, ExpertiseProgram, p.school_id, data.program_id)


async def update_class(db: AsyncSession, p: Principal, entity_id: UUID, data):
    classroom = await locked(db, SchoolClass, p.school_id, entity_id)
    check_revision(classroom, data.expected_revision)
    await validate_class(db, p, data)
    used = await db.scalar(
        select(
            or_(
                *[
                    exists().where(model.school_id == p.school_id, model.class_id == entity_id)
                    for model in (TeachingAssignment, StudentEnrollment, ClassOffering)
                ]
            )
        )
    )
    if used and (classroom.grade_level, classroom.program_id) != (
        data.grade_level,
        data.program_id,
    ):
        raise HTTPException(409, "Grade/program is fixed after class use; create another class")
    for key, value in data.model_dump(exclude={"expected_revision"}).items():
        setattr(classroom, key, value)
    classroom.revision += 1
    audit(db, p, "class.updated", entity_id, revision=classroom.revision)
    await db.commit()
    return classroom


async def delete_catalog(
    db: AsyncSession, p: Principal, model, entity_id: UUID, expected: int, kind: str
):
    item = await locked(db, model, p.school_id, entity_id)
    check_revision(item, expected)
    # Composite RESTRICT foreign keys also protect against concurrent inserts.
    await db.delete(item)
    audit(db, p, kind + ".deleted", entity_id)
    await db.commit()


async def save_offering(db: AsyncSession, p: Principal, data, entity_id: UUID | None = None):
    if entity_id:
        item = await record(db, ClassOffering, p.school_id, entity_id)
        await open_period(db, p.school_id, item.period_id)
        item = await locked(db, ClassOffering, p.school_id, entity_id)
        check_revision(item, data.expected_revision)
        item.revision += 1
    else:
        await open_period(db, p.school_id, data.period_id)
        await locked(db, SchoolClass, p.school_id, data.class_id, shared=True)
        item = ClassOffering(
            school_id=p.school_id, period_id=data.period_id, class_id=data.class_id
        )
        db.add(item)
    if data.homeroom_teacher_id:
        await eligible_teacher(db, p.school_id, data.homeroom_teacher_id)
    item.homeroom_teacher_id, item.room = data.homeroom_teacher_id, data.room
    await db.flush()
    audit(db, p, "class_offering.saved", item.id, revision=item.revision)
    await db.commit()
    return item
