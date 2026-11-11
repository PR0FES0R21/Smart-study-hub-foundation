from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from studyhub.academics.schemas import Input, Read


class Choice(Input):
    key: str = Field(pattern=r"^[A-H]$")
    text: str = Field(min_length=1, max_length=2000)


class QuestionContent(Input):
    kind: Literal["multiple_choice", "essay"]
    prompt: str = Field(min_length=1, max_length=12000)
    options: list[Choice] = Field(default_factory=list, max_length=8)
    correct_option: str | None = Field(default=None, pattern=r"^[A-H]$")
    rubric: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def valid_answer(self):
        keys = [o.key for o in self.options]
        if self.kind == "multiple_choice":
            if len(keys) < 2 or len(set(keys)) != len(keys) or self.correct_option not in keys:
                raise ValueError(
                    "Multiple choice requires 2-8 unique options and a valid correct_option"
                )
        elif self.options or self.correct_option is not None:
            raise ValueError("Essay questions cannot contain options or correct_option")
        elif not self.rubric:
            raise ValueError("Essay questions require a grading rubric")
        return self


class QuestionCreate(Input):
    group_id: UUID
    content: QuestionContent


class RevisionInput(Input):
    expected_revision: int = Field(ge=1)


class QuestionUpdate(RevisionInput):
    content: QuestionContent


class QuestionReview(RevisionInput):
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def rejection_reason(self):
        if self.decision == "rejected" and not self.note:
            raise ValueError("A rejection requires a note")
        return self


class QuestionRead(Read):
    group_id: UUID
    author_id: UUID
    content: QuestionContent
    revision: int
    status: str
    review_note: str | None


class ModuleItem(Input):
    question_id: UUID
    question_revision: int = Field(ge=1)
    points: int = Field(ge=1, le=1000)


class ModuleCreate(Input):
    group_id: UUID
    title: str = Field(min_length=1, max_length=200)


class ModuleUpdate(RevisionInput):
    title: str = Field(min_length=1, max_length=200)
    items: list[ModuleItem] = Field(max_length=200)

    @model_validator(mode="after")
    def unique_questions(self):
        ids = [item.question_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("A question may appear only once in a module")
        return self


class ModuleRead(Read):
    group_id: UUID
    title: str
    revision: int
    published_count: int


class ModuleDetail(ModuleRead):
    items: list[ModuleItem]


class PublishedItem(Input):
    question_id: UUID
    question_revision: int
    author_id: UUID
    points: int
    content: QuestionContent


class ModuleSnapshot(Input):
    title: str
    items: list[PublishedItem]
    total_points: int


class VersionSummary(Read):
    module_id: UUID
    group_id: UUID
    version: int
    checksum: str
    published_by: UUID
    published_at: datetime


class VersionRead(VersionSummary):
    snapshot: ModuleSnapshot


class ClassExamCreate(Input):
    duration_minutes: int = Field(default=60, ge=1, le=360)
    module_version_id: UUID
    assignment_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def valid_dates(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class ClassExamRead(Read, ClassExamCreate):
    period_id: UUID
    state: str
    revision: int
    results_released_at: datetime | None
    class_id: UUID
    created_by: UUID
