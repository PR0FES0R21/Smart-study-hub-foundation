"""Pure bounded grading, reused by the async API and synchronous Celery workers."""

from datetime import datetime
from decimal import Decimal

from studyhub.assessments.delivery_models import ExamAttempt
from studyhub.models import Outbox


def finalize(attempt: ExamAttempt, snapshot: dict, now: datetime, reason: str) -> bool:
    if attempt.status != "in_progress":
        return False
    grading, pending, score = {}, 0, Decimal(0)
    for item in snapshot["items"]:
        question_id = item["question_id"]
        content = item["content"]
        answer = attempt.answers.get(question_id)
        points = None
        if content["kind"] == "multiple_choice":
            points = Decimal(item["points"] if answer == content["correct_option"] else 0)
        elif not answer:
            points = Decimal(0)
        if points is None:
            pending += 1
        else:
            score += points
        grading[question_id] = {
            "points": str(points) if points is not None else None,
            "source": "automatic" if points is not None else "pending",
        }
    attempt.grading, attempt.pending_essays, attempt.score = grading, pending, score
    attempt.status = "submitted" if pending else "graded"
    attempt.submitted_at = min(now, attempt.deadline_at)
    attempt.submitted_reason = reason
    attempt.revision += 1
    return True


def completion_event(attempt: ExamAttempt, user_id) -> Outbox:
    return Outbox(
        school_id=attempt.school_id,
        kind="user.event",
        payload={
            "user_id": str(user_id),
            "type": "exam.submitted",
            "data": {"attempt_id": str(attempt.id)},
        },
    )
