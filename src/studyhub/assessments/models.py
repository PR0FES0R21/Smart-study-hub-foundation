"""Collaborative question authoring and immutable published exam packages."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from studyhub.academics.models import SchoolRecord
from studyhub.infrastructure.database import Base


class Question(SchoolRecord, Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("id", "school_id"),
        ForeignKeyConstraint(
            ["group_id", "school_id"], ["coordination_groups.id", "coordination_groups.school_id"]
        ),
        ForeignKeyConstraint(
            ["school_id", "author_id"], ["memberships.school_id", "memberships.user_id"]
        ),
        CheckConstraint("status IN ('draft','submitted','approved','rejected')", name="status"),
        CheckConstraint("revision > 0", name="revision"),
        CheckConstraint("jsonb_typeof(content) = 'object'", name="content"),
        Index("ix_questions_group_status", "group_id", "school_id", "status", "id"),
        Index("ix_questions_author", "school_id", "author_id"),
    )
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    content: Mapped[dict] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    review_note: Mapped[str | None] = mapped_column(String(2000))


class ExamModule(SchoolRecord, Base):
    __tablename__ = "exam_modules"
    __table_args__ = (
        UniqueConstraint("id", "school_id", "group_id"),
        ForeignKeyConstraint(
            ["group_id", "school_id"], ["coordination_groups.id", "coordination_groups.school_id"]
        ),
        CheckConstraint("revision > 0 AND published_count >= 0", name="revision"),
        CheckConstraint(
            "jsonb_typeof(items) = 'array' AND jsonb_array_length(items) <= 200", name="items"
        ),
        Index("ix_exam_modules_group", "group_id", "school_id"),
    )
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(200))
    items: Mapped[list] = mapped_column(JSONB, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    published_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ModuleVersion(SchoolRecord, Base):
    __tablename__ = "module_versions"
    __table_args__ = (
        UniqueConstraint("id", "school_id"),
        UniqueConstraint("module_id", "version"),
        ForeignKeyConstraint(
            ["module_id", "school_id", "group_id"],
            ["exam_modules.id", "exam_modules.school_id", "exam_modules.group_id"],
        ),
        ForeignKeyConstraint(
            ["school_id", "published_by"], ["memberships.school_id", "memberships.user_id"]
        ),
        CheckConstraint("version > 0", name="version"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="snapshot"),
        Index("ix_module_versions_group", "school_id", "group_id"),
        Index("ix_module_versions_publisher", "school_id", "published_by"),
    )
    module_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    published_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ClassExam(SchoolRecord, Base):
    __tablename__ = "class_exams"
    __table_args__ = (
        UniqueConstraint("id", "school_id", "class_id", "period_id", name="uq_class_exam_scope"),
        ForeignKeyConstraint(
            ["assignment_id", "school_id", "class_id", "period_id"],
            [
                "teaching_assignments.id",
                "teaching_assignments.school_id",
                "teaching_assignments.class_id",
                "teaching_assignments.period_id",
            ],
            name="fk_class_exam_assignment_period",
        ),
        CheckConstraint("duration_minutes BETWEEN 1 AND 360", name="duration"),
        CheckConstraint("state IN ('draft','published')", name="state"),
        CheckConstraint("revision > 0", name="revision"),
        UniqueConstraint("module_version_id", "class_id"),
        ForeignKeyConstraint(
            ["module_version_id", "school_id"], ["module_versions.id", "module_versions.school_id"]
        ),
        ForeignKeyConstraint(
            ["assignment_id", "school_id", "class_id"],
            [
                "teaching_assignments.id",
                "teaching_assignments.school_id",
                "teaching_assignments.class_id",
            ],
        ),
        ForeignKeyConstraint(
            ["school_id", "created_by"], ["memberships.school_id", "memberships.user_id"]
        ),
        CheckConstraint("ends_at > starts_at", name="dates"),
        Index("ix_class_exams_assignment", "assignment_id", "school_id", "class_id"),
        Index("ix_class_exams_creator", "school_id", "created_by"),
    )
    module_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    assignment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    state: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    results_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
