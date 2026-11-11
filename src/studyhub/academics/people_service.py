"""Atomic school directory writes. Authentication identities remain globally owned."""

import asyncio
from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics import people_schemas as schemas
from studyhub.academics.enrollment_service import create_student
from studyhub.academics.models import AcademicPeriod, ClassOffering, CoordinationGroup
from studyhub.academics.people_models import MemberProfile, TeacherProfile
from studyhub.academics.service import audit, check_revision
from studyhub.academics.student_models import StudentEnrollment, StudentProfile
from studyhub.academics.student_schemas import StudentCreate, StudentFields, StudentRead
from studyhub.core.principal import Principal
from studyhub.core.security import password_hasher
from studyhub.models import Membership, School, User


def fields(data, schema) -> dict:
    return {key: getattr(data, key) for key in schema.model_fields}


def member_read(member: Membership, user: User, profile: MemberProfile | None) -> dict:
    personal = (
        fields(profile, schemas.PersonWrite)
        if profile
        else schemas.PersonWrite(full_name=user.full_name).model_dump()
    )
    return {
        **personal,
        "school_id": member.school_id,
        "user_id": user.id,
        "email": user.email,
        "role": member.role,
        "membership_active": member.is_active,
        "account_active": user.is_active,
        "revision": member.revision,
    }


def directory_query(school_id: UUID, role: str):
    query = (
        select(Membership, User, MemberProfile)
        .join(User, User.id == Membership.user_id)
        .outerjoin(
            MemberProfile,
            (MemberProfile.school_id == Membership.school_id)
            & (MemberProfile.user_id == Membership.user_id),
        )
        .where(Membership.school_id == school_id, Membership.role == role)
    )
    model = StudentProfile if role == "student" else TeacherProfile if role == "teacher" else None
    if model:
        query = query.add_columns(model).join(
            model, (model.school_id == Membership.school_id) & (model.user_id == Membership.user_id)
        )
    return query, model


def directory_read(row, role: str) -> dict:
    member, user, profile = row[:3]
    result = member_read(member, user, profile)
    if role == "admin":
        return result
    entity = row[3]
    data = (
        StudentRead.model_validate(entity).model_dump()
        if role == "student"
        else {
            **fields(entity, schemas.TeacherFields),
            "id": entity.id,
            "school_id": entity.school_id,
            "user_id": entity.user_id,
        }
    )
    return {**data, "member": result}


async def directory(
    db: AsyncSession,
    school_id: UUID,
    role: str,
    *,
    entity_id: UUID | None = None,
    active: bool | None = None,
    limit: int = 20,
    offset: int = 0,
):
    query, model = directory_query(school_id, role)
    if entity_id:
        query = query.where(model.id == entity_id if model else Membership.user_id == entity_id)
        row = (await db.execute(query)).first()
        if row is None:
            raise HTTPException(404, "School member not found")
        return directory_read(row, role)
    if active is not None:
        query = query.where(Membership.is_active.is_(active))
    rows = (await db.execute(query.order_by(User.id).limit(limit).offset(offset))).all()
    return [directory_read(row, role) for row in rows]


async def locked_member(db: AsyncSession, school_id: UUID, user_id: UUID, role: str):
    row = (
        await db.execute(
            select(Membership, User)
            .join(User)
            .where(
                Membership.school_id == school_id,
                Membership.user_id == user_id,
                Membership.role == role,
            )
            # NO KEY UPDATE still conflicts with eligibility's SHARE lock while
            # allowing foreign-key checks from audits and historical records.
            .with_for_update(of=Membership, key_share=True)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "School member not found")
    return row


async def write_person(db: AsyncSession, member: Membership, data: schemas.PersonWrite):
    profile = await db.scalar(
        select(MemberProfile).where(
            MemberProfile.school_id == member.school_id, MemberProfile.user_id == member.user_id
        )
    )
    if profile is None:
        profile = MemberProfile(school_id=member.school_id, user_id=member.user_id)
        db.add(profile)
    for key, value in fields(data, schemas.PersonWrite).items():
        setattr(profile, key, value)


async def new_account(
    db: AsyncSession, p: Principal, data: schemas.AccountRegister, role: str
) -> User:
    # A duplicate global email is a conflict, never an implicit account link/reset.
    user = User(
        email=str(data.email).lower(),
        full_name=data.full_name,
        password_hash=await asyncio.to_thread(password_hasher.hash, data.password),
    )
    db.add(user)
    await db.flush()
    member = Membership(school_id=p.school_id, user_id=user.id, role=role)
    db.add(member)
    await db.flush()
    await write_person(db, member, data)
    audit(db, p, "member.created", user.id, role=role)
    return user


async def register(db: AsyncSession, p: Principal, data, role: str):
    user = await new_account(db, p, data, role)
    if role == "student":
        validate_person_dates(data, data.admitted_on)
        entity = await create_student(
            db, p, StudentCreate(user_id=user.id, **fields(data, StudentFields)), commit=False
        )
    elif role == "teacher":
        validate_person_dates(data, data.joined_on)
        entity = TeacherProfile(
            school_id=p.school_id, user_id=user.id, **fields(data, schemas.TeacherFields)
        )
        db.add(entity)
    else:
        entity = user
    await db.flush()
    await db.commit()
    return await directory(db, p.school_id, role, entity_id=entity.id)


async def create_teacher(db: AsyncSession, p: Principal, data: schemas.TeacherCreate):
    member, user = await locked_member(db, p.school_id, data.user_id, "teacher")
    if not member.is_active or not user.is_active:
        raise HTTPException(422, "An active teacher account in this school is required")
    teacher = TeacherProfile(school_id=p.school_id, **data.model_dump())
    db.add(teacher)
    await db.flush()
    member.revision += 1
    audit(db, p, "teacher.created", teacher.id)
    await db.commit()
    return await directory(db, p.school_id, "teacher", entity_id=teacher.id)


def validate_person_dates(data, start: date | None) -> None:
    if data.birth_date and start and start < data.birth_date:
        raise HTTPException(422, "Admission or employment date cannot precede birth")


async def update(db: AsyncSession, p: Principal, role: str, entity_id: UUID, data):
    entity = None
    if role != "admin":
        model = StudentProfile if role == "student" else TeacherProfile
        entity = await db.scalar(
            select(model)
            .where(model.school_id == p.school_id, model.id == entity_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if entity is None:
            raise HTTPException(404, "School member not found")
    user_id = entity.user_id if entity else entity_id
    member, _ = await locked_member(db, p.school_id, user_id, role)
    check_revision(member, data.expected_revision)
    if role == "student":
        valid = {9} if p.school.level == "SMP" else {12} if p.school.level == "SMA" else {12, 13}
        if data.graduation_grade not in valid:
            raise HTTPException(422, "Graduation grade is incompatible with school level")
        if data.graduation_grade != entity.graduation_grade and (
            entity.status != "active"
            or await db.scalar(
                select(StudentEnrollment.id)
                .where(
                    StudentEnrollment.school_id == p.school_id,
                    StudentEnrollment.student_id == entity.id,
                )
                .limit(1)
            )
        ):
            raise HTTPException(409, "Graduation track is fixed after enrollment")
        validate_person_dates(data, data.admitted_on)
        values = fields(data, StudentFields)
    elif role == "teacher":
        validate_person_dates(data, data.joined_on)
        values = fields(data, schemas.TeacherFields)
    else:
        values = {}
    for key, value in values.items():
        setattr(entity, key, value)
    await write_person(db, member, data)
    member.revision += 1
    audit(db, p, role + ".updated", entity_id, revision=member.revision)
    await db.commit()
    return await directory(db, p.school_id, role, entity_id=entity_id)


async def set_active(
    db: AsyncSession, p: Principal, role: str, entity_id: UUID, expected: int, active: bool
):
    entity = None
    if role == "admin":
        # Serialize the last-admin check, including two admins disabling each other.
        await db.scalar(
            select(School).where(School.id == p.school_id).with_for_update(key_share=True)
        )
        actor_active = await db.scalar(
            select(Membership.is_active).where(
                Membership.school_id == p.school_id,
                Membership.user_id == p.user.id,
                Membership.role == "admin",
            )
        )
        if not actor_active:
            raise HTTPException(403, "Administrator membership is no longer active")
    else:
        model = StudentProfile if role == "student" else TeacherProfile
        # Profile -> membership: the same order as student starts/enrollment writes.
        entity = await db.scalar(
            select(model)
            .where(model.school_id == p.school_id, model.id == entity_id)
            .with_for_update()
        )
        if entity is None:
            raise HTTPException(404, "School member not found")
    user_id = entity.user_id if entity else entity_id
    member, user = await locked_member(db, p.school_id, user_id, role)
    check_revision(member, expected)
    if member.is_active == active:
        return
    if active and not user.is_active:
        raise HTTPException(409, "Global account is disabled; contact the platform administrator")
    if not active and role == "admin":
        other = await db.scalar(
            select(Membership.user_id)
            .join(User)
            .where(
                Membership.school_id == p.school_id,
                Membership.role == "admin",
                Membership.is_active.is_(True),
                Membership.user_id != user_id,
                User.is_active.is_(True),
            )
            .limit(1)
        )
        if other is None:
            raise HTTPException(409, "Keep at least one active school administrator")
    if not active and role == "teacher":
        for model, column in (
            (CoordinationGroup, CoordinationGroup.coordinator_id),
            (ClassOffering, ClassOffering.homeroom_teacher_id),
        ):
            assigned = await db.scalar(
                select(model.id)
                .join(AcademicPeriod, AcademicPeriod.id == model.period_id)
                .where(
                    model.school_id == p.school_id,
                    column == user_id,
                    AcademicPeriod.status == "open",
                )
                .limit(1)
            )
            if assigned:
                raise HTTPException(
                    409, "Reassign or revoke current coordinator/homeroom duties first"
                )
    if not active and role == "student":
        from studyhub.assessments.delivery_models import ExamAttempt, ExamParticipant

        running = await db.scalar(
            select(ExamAttempt.id)
            .join(ExamParticipant, ExamParticipant.id == ExamAttempt.participant_id)
            .where(
                ExamAttempt.school_id == p.school_id,
                ExamParticipant.student_id == entity.id,
                ExamAttempt.status == "in_progress",
                ExamAttempt.deadline_at > func.clock_timestamp(),
            )
            .limit(1)
        )
        if running:
            raise HTTPException(409, "Finish the student's running exam before deactivating access")
    member.is_active = active
    member.revision += 1
    audit(
        db,
        p,
        "member.restored" if active else "member.deactivated",
        user_id,
        role=role,
        revision=member.revision,
    )
    await db.commit()
