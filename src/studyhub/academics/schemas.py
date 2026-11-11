from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    school_id: UUID


class PeriodCreate(Input):
    year_start: int = Field(ge=2000, le=2200)
    semester: int = Field(ge=1, le=2)
    starts_on: date
    ends_on: date

    @model_validator(mode="after")
    def dates(self):
        if self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        if not (self.year_start <= self.starts_on.year <= self.ends_on.year <= self.year_start + 1):
            raise ValueError("Dates must be inside the academic year")
        return self


class PeriodRead(Read, PeriodCreate):
    status: str
    revision: int


class AssignmentCreate(Input):
    period_id: UUID
    class_id: UUID
    subject_id: UUID
    teacher_id: UUID


class AssignmentRead(Read, AssignmentCreate):
    is_active: bool


class AssignmentStatus(Input):
    is_active: bool


class GroupCreate(Input):
    period_id: UUID
    subject_id: UUID
    grade_level: int = Field(ge=7, le=13)
    coordinator_id: UUID


class CoordinatorChange(Input):
    coordinator_id: UUID | None
    expected_revision: int = Field(ge=1)


class GroupRead(Read):
    period_id: UUID
    subject_id: UUID
    grade_level: int
    coordinator_id: UUID | None
    revision: int


class AuditRead(Read):
    actor_id: UUID
    action: str
    resource_id: UUID
    details: dict
    created_at: datetime
