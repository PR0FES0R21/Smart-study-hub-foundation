"""Enrollment history and explicit, administrator-approved student progression."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from studyhub.academics.schemas import Input, Read


class StudentCreate(Input):
    user_id: uuid.UUID
    student_number: str | None = Field(default=None, min_length=1, max_length=32)
    graduation_grade: int = Field(ge=9, le=13)


class StudentRead(Read, StudentCreate):
    status: str
    graduated_on: date | None


class EnrollmentCreate(Input):
    student_id: uuid.UUID
    period_id: uuid.UUID
    class_id: uuid.UUID


class EnrollmentRead(Read, EnrollmentCreate):
    state: str
    revision: int
    ended_on: date | None
    created_at: datetime


class EnrollmentTransition(Input):
    expected_revision: int = Field(ge=1)
    action: Literal["continued", "promoted", "repeated", "graduated", "withdrawn"]
    effective_on: date
    target_period_id: uuid.UUID | None = None
    target_class_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def destination(self):
        needs_target = self.action in {"continued", "promoted", "repeated"}
        if needs_target and (self.target_period_id is None or self.target_class_id is None):
            raise ValueError(
                "A continuation, promotion or repetition requires a target period and class"
            )
        if not needs_target and (
            self.target_period_id is not None or self.target_class_id is not None
        ):
            raise ValueError("Graduation/withdrawal cannot have a target enrollment")
        return self


class TransitionRead(Input):
    previous: EnrollmentRead
    current: EnrollmentRead | None
    student: StudentRead
