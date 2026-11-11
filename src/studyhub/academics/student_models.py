"""Enrollment history and explicit, administrator-approved student progression."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from studyhub.academics.models import SchoolRecord
from studyhub.infrastructure.database import Base


class StudentProfile(SchoolRecord, Base):
    __tablename__ = "student_profiles"
    __table_args__ = (
        UniqueConstraint("id", "school_id"),
        UniqueConstraint("school_id", "user_id"),
        UniqueConstraint("school_id", "student_number", name="uq_student_number"),
        UniqueConstraint("school_id", "nisn", name="uq_student_nisn"),
        CheckConstraint("nisn IS NULL OR nisn ~ '^[0-9]{10}$'", name="nisn"),
        ForeignKeyConstraint(
            ["school_id", "user_id"], ["memberships.school_id", "memberships.user_id"]
        ),
        ForeignKeyConstraint(
            ["school_id", "school_level"],
            ["schools.id", "schools.level"],
            name="fk_student_school_level",
        ),
        CheckConstraint(
            "(school_level='SMP' AND graduation_grade=9) OR (school_level='SMA' AND graduation_grade=12) OR (school_level='SMK' AND graduation_grade IN (12,13))",
            name="graduation_grade",
        ),
        CheckConstraint("status IN ('active','graduated','withdrawn')", name="status"),
        CheckConstraint(
            "(status = 'graduated') = (graduated_on IS NOT NULL)", name="graduation_date"
        ),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    student_number: Mapped[str | None] = mapped_column(String(32))
    nisn: Mapped[str | None] = mapped_column(String(10))
    admitted_on: Mapped[date | None] = mapped_column(Date)
    previous_school: Mapped[str | None] = mapped_column(String(200))
    school_level: Mapped[str] = mapped_column(String(3))
    graduation_grade: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    graduated_on: Mapped[date | None] = mapped_column(Date)


class StudentEnrollment(SchoolRecord, Base):
    __tablename__ = "student_enrollments"
    __table_args__ = (
        UniqueConstraint(
            "id", "school_id", "student_id", "period_id", "class_id", name="uq_enrollment_scope"
        ),
        UniqueConstraint("school_id", "student_id", "period_id"),
        ForeignKeyConstraint(
            ["student_id", "school_id"], ["student_profiles.id", "student_profiles.school_id"]
        ),
        ForeignKeyConstraint(
            ["period_id", "school_id"], ["academic_periods.id", "academic_periods.school_id"]
        ),
        ForeignKeyConstraint(
            ["class_id", "school_id"], ["school_classes.id", "school_classes.school_id"]
        ),
        CheckConstraint(
            "state IN ('active','continued','promoted','repeated','graduated','withdrawn')",
            name="state",
        ),
        CheckConstraint("(state = 'active') = (ended_on IS NULL)", name="end_date"),
        CheckConstraint("revision > 0", name="revision"),
        Index(
            "uq_enrollment_active",
            "school_id",
            "student_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_enrollment_roster", "school_id", "period_id", "class_id", "state"),
        Index("ix_enrollment_class", "class_id", "school_id"),
    )
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    ended_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
