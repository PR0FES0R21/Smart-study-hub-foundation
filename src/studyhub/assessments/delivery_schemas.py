from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from studyhub.academics.schemas import Input, Read
from studyhub.assessments.schemas import Choice, RevisionInput


class ExamSchedule(RevisionInput):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    duration_minutes: int = Field(ge=1, le=360)

    @model_validator(mode="after")
    def dates(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class StudentExam(BaseModel):
    id: UUID
    title: str
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    results_released_at: datetime | None


class Answer(Input):
    question_id: UUID
    value: str | None = Field(max_length=8000)


class AnswerBatch(RevisionInput):
    answers: list[Answer] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [a.question_id for a in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate question IDs are not allowed")
        return self


class StudentQuestion(BaseModel):
    id: UUID
    kind: Literal["multiple_choice", "essay"]
    prompt: str
    options: list[Choice]
    points: int


class StudentResult(BaseModel):
    score: Decimal
    total_points: int
    percentage: Decimal


class AttemptState(Read):
    exam_id: UUID
    status: str
    revision: int
    started_at: datetime
    deadline_at: datetime
    submitted_at: datetime | None
    submitted_reason: str | None
    server_time: datetime
    result: StudentResult | None


class AttemptRead(AttemptState):
    questions: list[StudentQuestion]
    answers: dict[str, str]


class EssayGrade(Input):
    question_id: UUID
    points: Decimal = Field(ge=0, le=1000, decimal_places=2, allow_inf_nan=False)


class EssayGradeBatch(RevisionInput):
    grades: list[EssayGrade] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [g.question_id for g in self.grades]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate question IDs are not allowed")
        return self


class TeacherAttempt(Read):
    participant_id: UUID
    status: str
    revision: int
    started_at: datetime
    deadline_at: datetime
    submitted_at: datetime | None
    submitted_reason: str | None
    score: Decimal | None
    total_points: int
    pending_essays: int


class TeacherAttemptDetail(TeacherAttempt):
    answers: dict[str, str]
    grading: dict


class ParticipantRead(Read):
    full_name: str
    student_number: str | None
    student_id: UUID
    enrollment_id: UUID
    exam_id: UUID
    class_id: UUID
    period_id: UUID
