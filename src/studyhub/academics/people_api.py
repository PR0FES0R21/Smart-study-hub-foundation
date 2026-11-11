from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics import people_schemas as schemas
from studyhub.academics import people_service as service
from studyhub.api.dependencies import Principal, administrator, database

router = APIRouter(prefix="/api/v1", tags=["school directory"])


@router.post("/students/register", response_model=schemas.StudentDetail, status_code=201)
async def register_student(
    data: schemas.StudentRegister,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.register(db, p, data, "student")


@router.get("/students/{student_id}", response_model=schemas.StudentDetail)
async def student_detail(
    student_id: UUID, p: Principal = Depends(administrator), db: AsyncSession = Depends(database)
):
    return await service.directory(db, p.school_id, "student", entity_id=student_id)


@router.put("/students/{student_id}", response_model=schemas.StudentDetail)
async def update_student(
    student_id: UUID,
    data: schemas.StudentUpdate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.update(db, p, "student", student_id, data)


@router.delete("/students/{student_id}", status_code=204)
async def deactivate_student(
    student_id: UUID,
    expected_revision: int = Query(ge=1),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.set_active(db, p, "student", student_id, expected_revision, False)


@router.post("/students/{student_id}/restore", response_model=schemas.StudentDetail)
async def restore_student(
    student_id: UUID,
    data: schemas.Revision,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.set_active(db, p, "student", student_id, data.expected_revision, True)
    return await service.directory(db, p.school_id, "student", entity_id=student_id)


@router.post("/teachers/register", response_model=schemas.TeacherRead, status_code=201)
async def register_teacher(
    data: schemas.TeacherRegister,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.register(db, p, data, "teacher")


@router.post("/teachers", response_model=schemas.TeacherRead, status_code=201)
async def create_teacher(
    data: schemas.TeacherCreate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.create_teacher(db, p, data)


@router.get("/teachers", response_model=list[schemas.TeacherRead])
async def teachers(
    active: bool | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.directory(
        db, p.school_id, "teacher", active=active, limit=limit, offset=offset
    )


@router.get("/teachers/{teacher_id}", response_model=schemas.TeacherRead)
async def teacher_detail(
    teacher_id: UUID, p: Principal = Depends(administrator), db: AsyncSession = Depends(database)
):
    return await service.directory(db, p.school_id, "teacher", entity_id=teacher_id)


@router.put("/teachers/{teacher_id}", response_model=schemas.TeacherRead)
async def update_teacher(
    teacher_id: UUID,
    data: schemas.TeacherUpdate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.update(db, p, "teacher", teacher_id, data)


@router.delete("/teachers/{teacher_id}", status_code=204)
async def deactivate_teacher(
    teacher_id: UUID,
    expected_revision: int = Query(ge=1),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.set_active(db, p, "teacher", teacher_id, expected_revision, False)


@router.post("/teachers/{teacher_id}/restore", response_model=schemas.TeacherRead)
async def restore_teacher(
    teacher_id: UUID,
    data: schemas.Revision,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.set_active(db, p, "teacher", teacher_id, data.expected_revision, True)
    return await service.directory(db, p.school_id, "teacher", entity_id=teacher_id)


@router.post("/school-admins", response_model=schemas.MemberRead, status_code=201)
async def register_admin(
    data: schemas.AccountRegister,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.register(db, p, data, "admin")


@router.get("/school-admins", response_model=list[schemas.MemberRead])
async def admins(
    active: bool | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.directory(
        db, p.school_id, "admin", active=active, limit=limit, offset=offset
    )


@router.get("/school-admins/{user_id}", response_model=schemas.MemberRead)
async def admin_detail(
    user_id: UUID, p: Principal = Depends(administrator), db: AsyncSession = Depends(database)
):
    return await service.directory(db, p.school_id, "admin", entity_id=user_id)


@router.put("/school-admins/{user_id}", response_model=schemas.MemberRead)
async def update_admin(
    user_id: UUID,
    data: schemas.AdminUpdate,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await service.update(db, p, "admin", user_id, data)


@router.delete("/school-admins/{user_id}", status_code=204)
async def deactivate_admin(
    user_id: UUID,
    expected_revision: int = Query(ge=1),
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.set_active(db, p, "admin", user_id, expected_revision, False)


@router.post("/school-admins/{user_id}/restore", response_model=schemas.MemberRead)
async def restore_admin(
    user_id: UUID,
    data: schemas.Revision,
    p: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await service.set_active(db, p, "admin", user_id, data.expected_revision, True)
    return await service.directory(db, p.school_id, "admin", entity_id=user_id)
