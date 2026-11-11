"""Enrollment history and explicit, administrator-approved student progression."""

import uuid

from fastapi import HTTPException
from sqlalchemy import (
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.academics.models import AcademicPeriod
from studyhub.academics.service import audit, open_period, record
from studyhub.academics.student_models import StudentEnrollment, StudentProfile
from studyhub.academics.student_schemas import EnrollmentCreate, EnrollmentTransition, StudentCreate
from studyhub.core.principal import Principal
from studyhub.models import Membership, SchoolClass, User


async def create_student(
    db: AsyncSession, p: Principal, data: StudentCreate, *, commit: bool = True
) -> StudentProfile:
    eligible = await db.scalar(
        select(User.id)
        .join(Membership)
        .where(
            User.id == data.user_id,
            User.is_active.is_(True),
            Membership.school_id == p.school_id,
            Membership.role == "student",
            Membership.is_active.is_(True),
        )
        .with_for_update(read=True)
    )
    if eligible is None:
        raise HTTPException(422, "An active student account in this school is required")
    valid = {9} if p.school.level == "SMP" else {12} if p.school.level == "SMA" else {12, 13}
    if data.graduation_grade not in valid:
        raise HTTPException(422, "Graduation grade is incompatible with school level")
    student = StudentProfile(
        school_id=p.school_id, school_level=p.school.level, **data.model_dump()
    )
    db.add(student)
    await db.flush()
    audit(db, p, "student.created", student.id)
    if commit:
        await db.commit()
    return student


async def locked_student(
    db: AsyncSession, school_id: uuid.UUID, student_id: uuid.UUID, *, shared=False
) -> StudentProfile:
    student = await db.scalar(
        select(StudentProfile)
        .where(StudentProfile.id == student_id, StudentProfile.school_id == school_id)
        .with_for_update(read=shared)
        .execution_options(populate_existing=True)
    )
    if student is None:
        raise HTTPException(404, "Student not found")
    if student.status != "active":
        raise HTTPException(409, "Student is no longer active")
    membership = await db.scalar(
        select(Membership.user_id)
        .join(User)
        .where(
            Membership.school_id == school_id,
            Membership.user_id == student.user_id,
            Membership.role == "student",
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
        .with_for_update(read=True, of=Membership)
    )
    if membership is None:
        raise HTTPException(409, "Student membership is inactive")
    return student


async def enroll_student(
    db: AsyncSession, p: Principal, data: EnrollmentCreate
) -> StudentEnrollment:
    await open_period(db, p.school_id, data.period_id)
    student = await locked_student(db, p.school_id, data.student_id)
    classroom = await record(db, SchoolClass, p.school_id, data.class_id, lock=True)
    if classroom.grade_level > student.graduation_grade:
        raise HTTPException(422, "Class exceeds the student's graduation grade")
    if await db.scalar(
        select(StudentEnrollment.id)
        .where(
            StudentEnrollment.school_id == p.school_id, StudentEnrollment.student_id == student.id
        )
        .limit(1)
    ):
        raise HTTPException(409, "Use an enrollment transition to preserve history")
    enrollment = StudentEnrollment(school_id=p.school_id, **data.model_dump())
    db.add(enrollment)
    await db.flush()
    audit(db, p, "student.enrolled", enrollment.id, student_id=str(student.id))
    await db.commit()
    return enrollment


async def transition_student(
    db: AsyncSession, p: Principal, enrollment_id: uuid.UUID, data: EnrollmentTransition
):
    from studyhub.assessments.delivery_models import ExamAttempt, ExamParticipant

    source = await record(db, StudentEnrollment, p.school_id, enrollment_id)
    ids = {source.period_id}
    if data.target_period_id:
        ids.add(data.target_period_id)
    # Deterministic period order, then profile: compatible with starts and roster publication.
    periods = (
        await db.scalars(
            select(AcademicPeriod)
            .where(AcademicPeriod.school_id == p.school_id, AcademicPeriod.id.in_(ids))
            .order_by(AcademicPeriod.id)
            .with_for_update(read=True)
        )
    ).all()
    if len(periods) != len(ids):
        raise HTTPException(404, "Academic period not found")
    by_id = {period.id: period for period in periods}
    student = await locked_student(db, p.school_id, source.student_id)
    await db.refresh(source, with_for_update=True)
    if source.state != "active" or source.revision != data.expected_revision:
        raise HTTPException(409, "Enrollment changed; reload before saving")
    now = await db.scalar(select(func.clock_timestamp()))
    period = by_id[source.period_id]
    classroom = await record(db, SchoolClass, p.school_id, source.class_id)
    if data.effective_on > now.date() or data.effective_on < period.starts_on:
        raise HTTPException(422, "Effective date must be within the student's elapsed enrollment")
    if data.action != "withdrawn" and data.effective_on < period.ends_on:
        raise HTTPException(422, "Complete the source semester before continuing or graduating")
    if await db.scalar(
        select(ExamAttempt.id)
        .join(ExamParticipant, ExamParticipant.id == ExamAttempt.participant_id)
        .where(
            ExamAttempt.school_id == p.school_id,
            ExamParticipant.student_id == student.id,
            ExamAttempt.status == "in_progress",
            ExamAttempt.deadline_at > now,
        )
        .limit(1)
    ):
        raise HTTPException(409, "Finish the student's running exam before changing enrollment")
    target = None
    if data.target_period_id:
        next_period = by_id[data.target_period_id]
        next_class = await record(db, SchoolClass, p.school_id, data.target_class_id, lock=True)
        if (
            next_period.status != "open"
            or next_period.starts_on <= period.ends_on
            or data.effective_on > next_period.starts_on
        ):
            raise HTTPException(422, "Target must be a later open period")
        if data.action == "continued":
            valid = (
                period.semester == 1
                and next_period.semester == 2
                and next_period.year_start == period.year_start
                and next_class.grade_level == classroom.grade_level
            )
        else:
            delta = 1 if data.action == "promoted" else 0
            valid = (
                period.semester == 2
                and next_period.semester == 1
                and next_period.year_start == period.year_start + 1
                and next_class.grade_level == classroom.grade_level + delta
            )
        if not valid or next_class.grade_level > student.graduation_grade:
            raise HTTPException(422, "Invalid semester or grade progression")
        target = StudentEnrollment(
            school_id=p.school_id,
            student_id=student.id,
            period_id=next_period.id,
            class_id=next_class.id,
        )
    elif data.action == "graduated":
        if period.semester != 2 or classroom.grade_level != student.graduation_grade:
            raise HTTPException(422, "Graduation requires the final grade and second semester")
        student.status, student.graduated_on = "graduated", data.effective_on
    else:
        student.status = "withdrawn"
    source.state, source.ended_on = data.action, data.effective_on
    source.revision += 1
    await db.flush()  # Release the one-active-enrollment key before creating the successor.
    if target:
        db.add(target)
        await db.flush()
    audit(
        db,
        p,
        "student." + data.action,
        source.id,
        student_id=str(student.id),
        target_enrollment_id=str(target.id) if target else None,
        effective_on=str(data.effective_on),
    )
    await db.commit()
    return {"previous": source, "current": target, "student": student}
