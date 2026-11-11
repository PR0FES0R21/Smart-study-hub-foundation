"""Frozen participant rosters and durable exam attempts."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from studyhub.academics.models import SchoolRecord
from studyhub.infrastructure.database import Base


class ExamParticipant(SchoolRecord, Base):
    __tablename__ = "exam_participants"
    __table_args__ = (
        UniqueConstraint("id", "school_id"),
        UniqueConstraint("exam_id", "student_id"),
        ForeignKeyConstraint(
            ["exam_id", "school_id", "class_id", "period_id"],
            [
                "class_exams.id",
                "class_exams.school_id",
                "class_exams.class_id",
                "class_exams.period_id",
            ],
        ),
        ForeignKeyConstraint(
            ["enrollment_id", "school_id", "student_id", "period_id", "class_id"],
            [
                "student_enrollments.id",
                "student_enrollments.school_id",
                "student_enrollments.student_id",
                "student_enrollments.period_id",
                "student_enrollments.class_id",
            ],
        ),
        Index("ix_participants_student", "school_id", "student_id", "exam_id"),
        Index("ix_participants_enrollment", "enrollment_id", "school_id"),
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    enrollment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))


class ExamAttempt(SchoolRecord, Base):
    __tablename__ = "exam_attempts"
    __table_args__ = (
        UniqueConstraint("participant_id"),
        ForeignKeyConstraint(
            ["participant_id", "school_id"], ["exam_participants.id", "exam_participants.school_id"]
        ),
        CheckConstraint("status IN ('in_progress','submitted','graded')", name="status"),
        CheckConstraint("deadline_at > started_at", name="deadline"),
        CheckConstraint(
            "revision > 0 AND pending_essays >= 0 AND total_points > 0", name="counters"
        ),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= total_points)", name="score"),
        CheckConstraint(
            "jsonb_typeof(answers) = 'object' AND jsonb_typeof(grading) = 'object'", name="payload"
        ),
        CheckConstraint(
            "(status = 'in_progress' AND submitted_at IS NULL AND submitted_reason IS NULL AND score IS NULL) OR (status IN ('submitted','graded') AND submitted_at IS NOT NULL AND submitted_reason IN ('manual','timeout') AND score IS NOT NULL)",
            name="submission",
        ),
        CheckConstraint("status != 'graded' OR pending_essays = 0", name="graded"),
        Index(
            "ix_attempts_expiry",
            "school_id",
            "deadline_at",
            postgresql_where=text("status = 'in_progress'"),
        ),
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_reason: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default="in_progress", server_default="in_progress"
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    answers: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    grading: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    total_points: Mapped[int] = mapped_column(Integer)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    pending_essays: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
