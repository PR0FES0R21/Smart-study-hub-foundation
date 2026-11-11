from uuid import uuid4

import pytest
from pydantic import ValidationError

from studyhub.academics.student_schemas import EnrollmentTransition
from studyhub.assessments.delivery_schemas import AnswerBatch, EssayGradeBatch, ExamSchedule


@pytest.mark.parametrize("action", ["continued", "promoted", "repeated"])
def test_progression_requires_target_class_and_period(action):
    with pytest.raises(ValidationError):
        EnrollmentTransition(expected_revision=1, action=action, effective_on="2026-06-30")


def test_graduation_cannot_create_new_enrollment():
    with pytest.raises(ValidationError):
        EnrollmentTransition(
            expected_revision=1,
            action="graduated",
            effective_on="2026-06-30",
            target_period_id=uuid4(),
            target_class_id=uuid4(),
        )


def test_bounded_answer_batches_and_grade_validation():
    item = {"question_id": uuid4(), "value": "answer"}
    for answers in ([item] * 21, [item, item], [{**item, "value": "x" * 8001}]):
        with pytest.raises(ValidationError):
            AnswerBatch(expected_revision=1, answers=answers)
    for points in ("NaN", "Infinity", "-1", "1001", "0.001"):
        with pytest.raises(ValidationError):
            EssayGradeBatch(
                expected_revision=1, grades=[{"question_id": uuid4(), "points": points}]
            )


def test_schedule_requires_timezone_duration_and_order():
    for data in (
        {
            "starts_at": "2026-11-11T08:00:00",
            "ends_at": "2026-11-11T10:00:00",
            "duration_minutes": 60,
        },
        {
            "starts_at": "2026-11-11T08:00:00Z",
            "ends_at": "2026-11-11T10:00:00Z",
            "duration_minutes": 0,
        },
        {
            "starts_at": "2026-11-11T10:00:00Z",
            "ends_at": "2026-11-11T08:00:00Z",
            "duration_minutes": 60,
        },
    ):
        with pytest.raises(ValidationError):
            ExamSchedule(expected_revision=1, **data)
