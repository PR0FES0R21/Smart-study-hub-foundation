"""School-scoped academic periods, teaching assignments and coordination groups."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from studyhub.infrastructure.database import Base


class SchoolRecord:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"))


class AcademicPeriod(SchoolRecord, Base):
    __tablename__ = "academic_periods"
    __table_args__ = (
        UniqueConstraint("id", "school_id"),
        UniqueConstraint("school_id", "year_start", "semester"),
        CheckConstraint("semester IN (1,2)", name="semester"),
        CheckConstraint("year_start BETWEEN 2000 AND 2200", name="year"),
        CheckConstraint("ends_on >= starts_on", name="dates"),
        CheckConstraint("status IN ('open','closed')", name="status"),
    )
    year_start: Mapped[int] = mapped_column(Integer)
    semester: Mapped[int] = mapped_column(Integer)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")


class TeachingAssignment(SchoolRecord, Base):
    __tablename__ = "teaching_assignments"
    __table_args__ = (
        UniqueConstraint("id", "school_id", "class_id", name="uq_teaching_assignment_class"),
        UniqueConstraint("id", "school_id"),
        UniqueConstraint(
            "school_id",
            "period_id",
            "class_id",
            "subject_id",
            "teacher_id",
            name="uq_teaching_assignment_scope",
        ),
        ForeignKeyConstraint(
            ["period_id", "school_id"], ["academic_periods.id", "academic_periods.school_id"]
        ),
        ForeignKeyConstraint(
            ["class_id", "school_id"], ["school_classes.id", "school_classes.school_id"]
        ),
        ForeignKeyConstraint(["subject_id", "school_id"], ["subjects.id", "subjects.school_id"]),
        ForeignKeyConstraint(
            ["school_id", "teacher_id"], ["memberships.school_id", "memberships.user_id"]
        ),
        Index("ix_teaching_scope", "school_id", "period_id", "subject_id", "teacher_id"),
        Index("ix_teaching_class", "class_id", "school_id"),
        Index("ix_teaching_teacher", "school_id", "teacher_id"),
        Index("ix_teaching_subject", "subject_id", "school_id"),
    )
    period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    teacher_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class CoordinationGroup(SchoolRecord, Base):
    __tablename__ = "coordination_groups"
    __table_args__ = (
        UniqueConstraint("id", "school_id"),
        UniqueConstraint(
            "school_id", "period_id", "subject_id", "grade_level", name="uq_coordination_scope"
        ),
        ForeignKeyConstraint(
            ["period_id", "school_id"], ["academic_periods.id", "academic_periods.school_id"]
        ),
        ForeignKeyConstraint(["subject_id", "school_id"], ["subjects.id", "subjects.school_id"]),
        ForeignKeyConstraint(
            ["school_id", "coordinator_id"], ["memberships.school_id", "memberships.user_id"]
        ),
        ForeignKeyConstraint(
            ["school_id", "school_level"],
            ["schools.id", "schools.level"],
            name="fk_coordination_school_level",
        ),
        CheckConstraint(
            "(school_level = 'SMP' AND grade_level BETWEEN 7 AND 9) OR (school_level = 'SMA' AND grade_level BETWEEN 10 AND 12) OR (school_level = 'SMK' AND grade_level BETWEEN 10 AND 13)",
            name="grade",
        ),
        CheckConstraint("revision > 0", name="revision"),
        Index("ix_coordination_coordinator", "school_id", "coordinator_id"),
        Index("ix_coordination_subject", "subject_id", "school_id"),
    )
    period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    school_level: Mapped[str] = mapped_column(String(3))
    grade_level: Mapped[int] = mapped_column(Integer)
    coordinator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class AcademicAudit(SchoolRecord, Base):
    __tablename__ = "academic_audit"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "actor_id"], ["memberships.school_id", "memberships.user_id"]
        ),
        Index("ix_academic_audit_school_created", "school_id", "created_at", "id"),
        Index("ix_academic_audit_actor", "school_id", "actor_id"),
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
