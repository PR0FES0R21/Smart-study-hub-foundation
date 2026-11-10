from uuid import uuid4

import pytest
from pydantic import ValidationError

from studyhub.academics.schemas import CoordinatorChange, PeriodCreate
from studyhub.assessments.schemas import (
    ClassExamCreate,
    ModuleUpdate,
    QuestionContent,
    QuestionReview,
)


@pytest.mark.parametrize(
    "content",
    [
        {
            "kind": "multiple_choice",
            "prompt": "Q",
            "options": [{"key": "A", "text": "a"}],
            "correct_option": "A",
        },
        {
            "kind": "multiple_choice",
            "prompt": "Q",
            "options": [{"key": "A", "text": "a"}, {"key": "A", "text": "b"}],
            "correct_option": "A",
        },
        {
            "kind": "multiple_choice",
            "prompt": "Q",
            "options": [{"key": "A", "text": "a"}, {"key": "B", "text": "b"}],
            "correct_option": "C",
        },
        {"kind": "essay", "prompt": "Q", "rubric": "  "},
        {"kind": "essay", "prompt": "Q", "rubric": "Rubric", "correct_option": "A"},
        {"kind": "essay", "prompt": "  ", "rubric": "Rubric"},
        {"kind": "essay", "prompt": "Q", "rubric": "Rubric", "author_id": str(uuid4())},
    ],
)
def test_question_content_rejects_inconsistent_answers_and_mass_assignment(content):
    with pytest.raises(ValidationError):
        QuestionContent.model_validate(content)


def test_module_selection_limits_duplicates_and_stale_write_inputs():
    item = {"question_id": uuid4(), "question_revision": 1, "points": 5}
    for items in ([item, item], [item] * 201, [{**item, "points": -1}]):
        with pytest.raises(ValidationError):
            ModuleUpdate(expected_revision=1, title="Test", items=items)
    with pytest.raises(ValidationError):
        CoordinatorChange(coordinator_id=uuid4())
    with pytest.raises(ValidationError):
        QuestionReview(expected_revision=1, decision="rejected", note=" ")


@pytest.mark.parametrize(
    "dates",
    [
        {"starts_at": "2026-11-15T08:00:00", "ends_at": "2026-11-15T09:00:00"},
        {"starts_at": "2026-11-15T10:00:00Z", "ends_at": "2026-11-15T09:00:00Z"},
    ],
)
def test_exam_requires_timezone_and_ordered_dates(dates):
    with pytest.raises(ValidationError):
        ClassExamCreate(module_version_id=uuid4(), assignment_id=uuid4(), **dates)


def test_period_dates_match_year_and_semester():
    for data in (
        {"year_start": 2026, "semester": 3, "starts_on": "2026-07-01", "ends_on": "2026-12-31"},
        {"year_start": 2026, "semester": 1, "starts_on": "2026-07-01", "ends_on": "2026-06-30"},
        {"year_start": 2026, "semester": 1, "starts_on": "2028-07-01", "ends_on": "2028-12-31"},
    ):
        with pytest.raises(ValidationError):
            PeriodCreate.model_validate(data)
