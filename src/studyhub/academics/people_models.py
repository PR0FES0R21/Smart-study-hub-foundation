"""School-owned personal data, separate from the shared authentication identity."""

import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from studyhub.academics.models import SchoolRecord
from studyhub.infrastructure.database import Base


class MemberProfile(SchoolRecord, Base):
    __tablename__ = "member_profiles"
    __table_args__ = (
        UniqueConstraint("school_id", "user_id"),
        ForeignKeyConstraint(
            ["school_id", "user_id"], ["memberships.school_id", "memberships.user_id"]
        ),
        CheckConstraint("gender IS NULL OR gender IN ('male','female')", name="gender"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    full_name: Mapped[str] = mapped_column(String(120))
    gender: Mapped[str | None] = mapped_column(String(8))
    birth_date: Mapped[date | None] = mapped_column(Date)
    birth_place: Mapped[str | None] = mapped_column(String(120))
    phone_number: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(String(1000))


class TeacherProfile(SchoolRecord, Base):
    __tablename__ = "teacher_profiles"
    __table_args__ = (
        UniqueConstraint("school_id", "user_id"),
        UniqueConstraint("school_id", "employee_number", name="uq_teacher_employee_number"),
        UniqueConstraint("school_id", "nip", name="uq_teacher_nip"),
        ForeignKeyConstraint(
            ["school_id", "user_id"], ["memberships.school_id", "memberships.user_id"]
        ),
        CheckConstraint("nip IS NULL OR nip ~ '^[0-9]{18}$'", name="nip"),
        CheckConstraint(
            "employment_type IS NULL OR employment_type IN ('permanent','contract','honorary')",
            name="employment_type",
        ),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    employee_number: Mapped[str | None] = mapped_column(String(32))
    nip: Mapped[str | None] = mapped_column(String(18))
    employment_type: Mapped[str | None] = mapped_column(String(16))
    qualification: Mapped[str | None] = mapped_column(String(120))
    joined_on: Mapped[date | None] = mapped_column(Date)
