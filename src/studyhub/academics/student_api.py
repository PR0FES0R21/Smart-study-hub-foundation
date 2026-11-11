from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics import enrollment_service as service
from studyhub.academics import people_service
from studyhub.academics import student_schemas as schemas
from studyhub.academics.people_schemas import StudentDetail
from studyhub.academics.student_models import StudentEnrollment, StudentProfile
from studyhub.api.dependencies import Principal, administrator, database, student

router = APIRouter(prefix="/api/v1", tags=["student enrollment"])


@router.post("/students", response_model=StudentDetail, status_code=201)
async def create_student(
    data: schemas.StudentCreate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    profile = await service.create_student(db, p, data)
    return await people_service.directory(db, p.school_id, "student", entity_id=profile.id)


@router.get("/students", response_model=list[StudentDetail])
async def list_students(
    active: bool | None = None,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return await people_service.directory(
        db, p.school_id, "student", active=active, limit=limit, offset=offset
    )


@router.get("/student/profile", response_model=schemas.StudentRead)
async def own_profile(p: Principal = Depends(student), db: AsyncSession = Depends(database)):
    profile = await db.scalar(
        select(StudentProfile).where(
            StudentProfile.school_id == p.school_id,
            StudentProfile.user_id == p.user.id,
        )
    )
    if profile is None:
        raise HTTPException(404, "Student profile not found")
    return profile


@router.post("/student-enrollments", response_model=schemas.EnrollmentRead, status_code=201)
async def enroll(
    data: schemas.EnrollmentCreate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.enroll_student(db, p, data)


@router.get("/student-enrollments", response_model=list[schemas.EnrollmentRead])
async def enrollments(
    period_id: UUID,
    student_id: UUID | None = None,
    class_id: UUID | None = None,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    query = select(StudentEnrollment).where(
        StudentEnrollment.school_id == p.school_id,
        StudentEnrollment.period_id == period_id,
    )
    if student_id:
        query = query.where(StudentEnrollment.student_id == student_id)
    if class_id:
        query = query.where(StudentEnrollment.class_id == class_id)
    return (
        await db.scalars(query.order_by(StudentEnrollment.id).limit(limit).offset(offset))
    ).all()


@router.get("/student/enrollments", response_model=list[schemas.EnrollmentRead])
async def own_enrollments(
    p: Principal = Depends(student),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return (
        await db.scalars(
            select(StudentEnrollment)
            .join(
                StudentProfile,
                StudentProfile.id == StudentEnrollment.student_id,
            )
            .where(
                StudentEnrollment.school_id == p.school_id,
                StudentProfile.user_id == p.user.id,
            )
            .order_by(StudentEnrollment.created_at, StudentEnrollment.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.post(
    "/student-enrollments/{enrollment_id}/transition", response_model=schemas.TransitionRead
)
async def transition(
    enrollment_id: UUID,
    data: schemas.EnrollmentTransition,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.transition_student(db, p, enrollment_id, data)
