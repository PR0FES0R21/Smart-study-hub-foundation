from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics import catalog_schemas as schemas
from studyhub.academics import catalog_service as service
from studyhub.academics.models import AcademicPeriod, AcademicYear, ClassOffering
from studyhub.academics.schemas import PeriodRead
from studyhub.academics.service import audit, check_revision, open_period, record
from studyhub.api.dependencies import Principal, administrator, database, educator, school_context
from studyhub.models import ExpertiseProgram, SchoolClass
from studyhub.schemas import ClassRead, ProgramRead

router = APIRouter(prefix="/api/v1", tags=["academic catalog"])


@router.post("/academic-years", response_model=schemas.YearRead, status_code=201)
async def create_year(
    data: schemas.YearWrite,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.calendar_lock(db, p.school_id)
    item = AcademicYear(school_id=p.school_id, **data.model_dump())
    db.add(item)
    await db.flush()
    audit(db, p, "academic_year.created", item.id)
    await db.commit()
    return item


@router.get("/academic-years", response_model=list[schemas.YearRead])
async def years(
    p: Principal = Depends(school_context),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return (
        await db.scalars(
            select(AcademicYear)
            .where(AcademicYear.school_id == p.school_id)
            .order_by(AcademicYear.year_start.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.get("/academic-years/{year_id}", response_model=schemas.YearRead)
async def year_detail(
    year_id: UUID, p: Principal = Depends(school_context), db: AsyncSession = Depends(database)
):
    return await record(db, AcademicYear, p.school_id, year_id)


@router.put("/academic-years/{year_id}", response_model=schemas.YearRead)
async def update_year(
    year_id: UUID,
    data: schemas.YearUpdate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.calendar_lock(db, p.school_id)
    item = await service.locked(db, AcademicYear, p.school_id, year_id)
    check_revision(item, data.expected_revision)
    if item.year_start != data.year_start:
        raise HTTPException(409, "Year identity is fixed; create a new academic year")
    item.description = data.description
    item.revision += 1
    audit(db, p, "academic_year.updated", item.id, revision=item.revision)
    await db.commit()
    return item


@router.delete("/academic-years/{year_id}", status_code=204)
async def delete_year(
    year_id: UUID,
    expected_revision: int = Query(ge=1),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.calendar_lock(db, p.school_id)
    await service.delete_catalog(db, p, AcademicYear, year_id, expected_revision, "academic_year")


@router.get("/academic-periods/{period_id}", response_model=PeriodRead)
async def period_detail(
    period_id: UUID, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    return await record(db, AcademicPeriod, p.school_id, period_id)


@router.put("/academic-periods/{period_id}", response_model=PeriodRead)
async def update_period(
    period_id: UUID,
    data: schemas.PeriodUpdate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.update_period(db, p, period_id, data)


@router.delete("/academic-periods/{period_id}", status_code=204)
async def delete_period(
    period_id: UUID,
    expected_revision: int = Query(ge=1),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.delete_period(db, p, period_id, expected_revision)


@router.get("/expertise-programs/{program_id}", response_model=ProgramRead)
async def program_detail(
    program_id: UUID, p: Principal = Depends(school_context), db: AsyncSession = Depends(database)
):
    return await record(db, ExpertiseProgram, p.school_id, program_id)


@router.put("/expertise-programs/{program_id}", response_model=ProgramRead)
async def update_program(
    program_id: UUID,
    data: schemas.ProgramUpdate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    item = await service.locked(db, ExpertiseProgram, p.school_id, program_id)
    check_revision(item, data.expected_revision)
    for key, value in data.model_dump(exclude={"expected_revision"}).items():
        setattr(item, key, value)
    item.revision += 1
    audit(db, p, "program.updated", item.id, revision=item.revision)
    await db.commit()
    return item


@router.delete("/expertise-programs/{program_id}", status_code=204)
async def delete_program(
    program_id: UUID,
    expected_revision: int = Query(ge=1),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.delete_catalog(db, p, ExpertiseProgram, program_id, expected_revision, "program")


@router.get("/classes/{class_id}", response_model=ClassRead)
async def class_detail(
    class_id: UUID, p: Principal = Depends(school_context), db: AsyncSession = Depends(database)
):
    return await record(db, SchoolClass, p.school_id, class_id)


@router.put("/classes/{class_id}", response_model=ClassRead)
async def update_class(
    class_id: UUID,
    data: schemas.ClassUpdate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.update_class(db, p, class_id, data)


@router.delete("/classes/{class_id}", status_code=204)
async def delete_class(
    class_id: UUID,
    expected_revision: int = Query(ge=1),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.delete_catalog(db, p, SchoolClass, class_id, expected_revision, "class")


@router.post("/class-offerings", response_model=schemas.OfferingRead, status_code=201)
async def create_offering(
    data: schemas.OfferingCreate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.save_offering(db, p, data)


@router.get("/class-offerings", response_model=list[schemas.OfferingRead])
async def offerings(
    period_id: UUID,
    p: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return (
        await db.scalars(
            select(ClassOffering)
            .where(ClassOffering.school_id == p.school_id, ClassOffering.period_id == period_id)
            .order_by(ClassOffering.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.get("/class-offerings/{offering_id}", response_model=schemas.OfferingRead)
async def offering_detail(
    offering_id: UUID, p: Principal = Depends(educator), db: AsyncSession = Depends(database)
):
    return await record(db, ClassOffering, p.school_id, offering_id)


@router.put("/class-offerings/{offering_id}", response_model=schemas.OfferingRead)
async def update_offering(
    offering_id: UUID,
    data: schemas.OfferingUpdate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.save_offering(db, p, data, offering_id)


@router.delete("/class-offerings/{offering_id}", status_code=204)
async def delete_offering(
    offering_id: UUID,
    expected_revision: int = Query(ge=1),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    item = await record(db, ClassOffering, p.school_id, offering_id)
    await open_period(db, p.school_id, item.period_id)
    await service.delete_catalog(
        db, p, ClassOffering, offering_id, expected_revision, "class_offering"
    )
