"""Student workflow, enrollment transitions, deadline enforcement and tenant isolation."""

import time
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from celery import Celery
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from studyhub.academics.student_models import StudentEnrollment
from studyhub.assessments.delivery_models import ExamAttempt
from studyhub.core.security import access_token
from studyhub.models import AuthSession, Membership, Outbox, User
from tests import test_academic_collaboration as collaboration
from tests.test_academic_collaboration import approved_question, module_draft, race

school_case = collaboration.school_case

pytestmark = pytest.mark.integration


def clock(stack):
    with stack["engine"].connect() as conn:
        return conn.scalar(select(func.clock_timestamp()))


def wait_until(stack, deadline):
    remaining = (deadline - clock(stack)).total_seconds()
    if remaining > 0:
        assert remaining < 10
        time.sleep(remaining + 0.05)


@pytest.fixture
def delivery_case(client, school_case):
    c = school_case
    c["profile"] = c["post"](
        "/students",
        {"user_id": c["ids"]["student"], "student_number": "NIS001", "graduation_grade": 12},
    )
    c["enrollment"] = c["post"](
        "/student-enrollments",
        {
            "student_id": c["profile"]["id"],
            "period_id": c["period"]["id"],
            "class_id": c["classes"][0]["id"],
        },
    )
    return c


def create_exam(stack, client, c, *, essay=False, seconds=600, future=False, publish=True):
    q = approved_question(client, c)
    module = module_draft(client, c, q)
    essay_q = None
    if essay:
        essay_q = approved_question(
            client,
            c,
            {
                "kind": "essay",
                "prompt": "Jelaskan langkahnya",
                "rubric": "PRIVATE RUBRIC must never reach student",
            },
        )
        response = client.put(
            f"/api/v1/exam-modules/{module['id']}",
            headers=c["h"]["a"],
            json={
                "expected_revision": module["revision"],
                "title": "Mixed exam",
                "items": [
                    {"question_id": q["id"], "question_revision": q["revision"], "points": 10},
                    {
                        "question_id": essay_q["id"],
                        "question_revision": essay_q["revision"],
                        "points": 5,
                    },
                ],
            },
        )
        assert response.status_code == 200, response.text
        module = response.json()
    version = c["post"](
        f"/exam-modules/{module['id']}/publish", {"expected_revision": module["revision"]}, "a"
    )
    now = clock(stack)
    exam = c["post"](
        "/class-exams",
        {
            "module_version_id": version["id"],
            "assignment_id": c["assignments"]["a"]["id"],
            "starts_at": (
                now + timedelta(seconds=30) if future else now - timedelta(seconds=30)
            ).isoformat(),
            "ends_at": (now + timedelta(seconds=seconds)).isoformat(),
            "duration_minutes": 1,
        },
        "a",
    )
    if publish:
        exam = c["post"](
            f"/class-exams/{exam['id']}/publish", {"expected_revision": exam["revision"]}, "a", 200
        )
    return exam, q, essay_q, version


def start(client, c, exam):
    return c["post"](f"/student/exams/{exam['id']}/attempt", None, "student", 200)


def save(client, c, attempt, question, value="A"):
    response = client.put(
        f"/api/v1/student/attempts/{attempt['id']}/answers",
        headers=c["h"]["student"],
        json={
            "expected_revision": attempt["revision"],
            "answers": [{"question_id": question["id"], "value": value}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def assert_no_solutions(response):
    body = str(response)
    assert all(
        word not in body
        for word in (
            "correct_option",
            "rubric",
            "PRIVATE RUBRIC",
            "graded_by",
            "author_id",
            "grading",
        )
    )


def test_start_autosave_submit_are_idempotent_and_race_safe(stack, client, delivery_case):
    c = delivery_case
    exam, q, _, _ = create_exam(stack, client, c)
    req = ("POST", f"/student/exams/{exam['id']}/attempt", c["h"]["student"], None)
    responses = race(stack, [req, req])
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    attempt = responses[0].json()
    assert_no_solutions(attempt)
    assert attempt["result"] is None
    reqs = [
        (
            "PUT",
            f"/student/attempts/{attempt['id']}/answers",
            c["h"]["student"],
            {
                "expected_revision": attempt["revision"],
                "answers": [{"question_id": q["id"], "value": answer}],
            },
        )
        for answer in ("A", "B")
    ]
    responses = race(stack, reqs)
    assert sorted(r.status_code for r in responses) == [200, 409]
    attempt = next(r.json() for r in responses if r.status_code == 200)
    request = (
        "POST",
        f"/student/attempts/{attempt['id']}/submit",
        c["h"]["student"],
        {"expected_revision": attempt["revision"]},
    )
    responses = race(stack, [request, request])
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json()["revision"] == responses[1].json()["revision"]
    result = responses[0].json()
    assert result["status"] == "graded" and result["result"] is None
    assert_no_solutions(result)
    assert (
        client.put(
            f"/api/v1/student/attempts/{attempt['id']}/answers",
            headers=c["h"]["student"],
            json={
                "expected_revision": result["revision"],
                "answers": [{"question_id": q["id"], "value": "A"}],
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/class-exams/{exam['id']}/release-results",
            headers=c["h"]["a"],
            json={"expected_revision": exam["revision"]},
        ).status_code
        == 409
    )
    with stack["engine"].connect() as conn:
        assert (
            conn.scalar(
                select(func.count())
                .select_from(ExamAttempt)
                .where(ExamAttempt.school_id == UUID(c["school_id"]))
            )
            == 1
        )
        events = conn.scalars(
            select(Outbox.payload).where(
                Outbox.school_id == UUID(c["school_id"]), Outbox.kind == "user.event"
            )
        ).all()
        assert len([event for event in events if event.get("type") == "exam.submitted"]) == 1


def test_essay_grading_and_explicit_release_hide_scores_and_keys(stack, client, delivery_case):
    c = delivery_case
    exam, q, essay, _ = create_exam(stack, client, c, essay=True, seconds=4)
    attempt = save(client, c, start(client, c, exam), q)
    attempt = save(client, c, attempt, essay, "Langkah penyelesaian siswa")
    attempt = c["post"](
        f"/student/attempts/{attempt['id']}/submit",
        {"expected_revision": attempt["revision"]},
        "student",
        200,
    )
    assert attempt["status"] == "submitted" and attempt["result"] is None
    assert_no_solutions(attempt)
    endpoint = f"/api/v1/exam-attempts/{attempt['id']}/essay-grades"
    assert (
        client.put(
            endpoint,
            headers=c["h"]["b"],
            json={
                "expected_revision": attempt["revision"],
                "grades": [{"question_id": essay["id"], "points": "2.50"}],
            },
        ).status_code
        == 403
    )
    assert (
        client.put(
            endpoint,
            headers=c["h"]["a"],
            json={
                "expected_revision": attempt["revision"],
                "grades": [{"question_id": essay["id"], "points": "6"}],
            },
        ).status_code
        == 422
    )
    graded = client.put(
        endpoint,
        headers=c["h"]["a"],
        json={
            "expected_revision": attempt["revision"],
            "grades": [{"question_id": essay["id"], "points": "2.50"}],
        },
    )
    assert graded.status_code == 200, graded.text
    assert Decimal(graded.json()["score"]) == Decimal("12.50")
    hidden = client.get(
        f"/api/v1/student/attempts/{attempt['id']}", headers=c["h"]["student"]
    ).json()
    assert hidden["result"] is None
    wait_until(stack, datetime.fromisoformat(exam["ends_at"]))
    released = c["post"](
        f"/class-exams/{exam['id']}/release-results",
        {"expected_revision": exam["revision"]},
        "a",
        200,
    )
    assert released["results_released_at"]
    result = client.get(
        f"/api/v1/student/attempts/{attempt['id']}", headers=c["h"]["student"]
    ).json()
    assert result["result"] == {"score": "12.50", "total_points": 15, "percentage": "83.33"}
    assert_no_solutions(result)
    assert (
        client.put(
            endpoint,
            headers=c["h"]["a"],
            json={
                "expected_revision": graded.json()["revision"],
                "grades": [{"question_id": essay["id"], "points": "5"}],
            },
        ).status_code
        == 409
    )


def test_deadline_rejects_late_answers_and_keeps_last_saved_answer(stack, client, delivery_case):
    c = delivery_case
    exam, q, _, _ = create_exam(stack, client, c, seconds=2)
    attempt = save(client, c, start(client, c, exam), q)
    assert datetime.fromisoformat(attempt["deadline_at"]) <= datetime.fromisoformat(exam["ends_at"])
    wait_until(stack, datetime.fromisoformat(attempt["deadline_at"]))
    late = client.put(
        f"/api/v1/student/attempts/{attempt['id']}/answers",
        headers=c["h"]["student"],
        json={
            "expected_revision": attempt["revision"],
            "answers": [{"question_id": q["id"], "value": "B"}],
        },
    )
    assert late.status_code == 409
    result = client.get(
        f"/api/v1/student/attempts/{attempt['id']}", headers=c["h"]["student"]
    ).json()
    assert result["answers"] == {q["id"]: "A"}
    assert result["status"] == "graded" and result["submitted_reason"] == "timeout"
    teacher = client.get(f"/api/v1/exam-attempts/{attempt['id']}", headers=c["h"]["a"]).json()
    assert Decimal(teacher["score"]) == 10


def test_real_celery_expiry_finishes_disconnected_student_once(stack, client, delivery_case):
    c = delivery_case
    exam, q, _, _ = create_exam(stack, client, c, seconds=2)
    attempt = save(client, c, start(client, c, exam), q)
    wait_until(stack, datetime.fromisoformat(attempt["deadline_at"]))
    producer = Celery("expiry-test", broker=stack["settings"].broker_url.get_secret_value())
    try:
        for _ in range(2):
            producer.send_task(
                "studyhub.expire_exam_attempts",
                queue=f"{stack['settings'].namespace}:tasks",
                ignore_result=True,
            )
        end = time.monotonic() + 10
        while time.monotonic() < end:
            with Session(stack["engine"]) as db:
                current = db.get(ExamAttempt, UUID(attempt["id"]))
                if current.status == "graded":
                    assert current.submitted_reason == "timeout" and current.score == 10
                    break
            time.sleep(0.05)
        else:
            pytest.fail("Worker did not finalize disconnected attempt")
    finally:
        producer.close()


def test_roster_access_and_tenant_isolation(stack, client, delivery_case):
    c = delivery_case
    exam, q, _, version = create_exam(stack, client, c, future=True)
    assert (
        client.post(
            f"/api/v1/student/exams/{exam['id']}/attempt", headers=c["h"]["student"]
        ).status_code
        == 409
    )
    assert (
        client.get(
            f"/api/v1/module-versions/{version['id']}", headers=c["h"]["student"]
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/class-exams/{exam['id']}/participants", headers=c["h"]["b"]
        ).status_code
        == 403
    )
    metadata = client.get("/api/v1/student/exams", headers=c["h"]["student"]).json()
    assert len(metadata) == 1 and metadata[0]["id"] == exam["id"]
    roster = client.get(
        f"/api/v1/class-exams/{exam['id']}/participants", headers=c["h"]["a"]
    ).json()
    assert roster[0]["full_name"] == "student" and roster[0]["student_number"] == "NIS001"
    assert "email" not in roster[0]

    assert "questions" not in metadata[0]
    assert_no_solutions(metadata)
    foreign = {**c["h"]["student"], "X-School-ID": c["other_school"]}
    assert client.get("/api/v1/student/exams", headers=foreign).json() == []
    assert (
        client.post(f"/api/v1/student/exams/{exam['id']}/attempt", headers=foreign).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/academic-periods/{c['period']['id']}/close", headers=c["h"]["admin"]
        ).status_code
        == 409
    )
    # Late enrollment is not silently appended to a published roster.
    now = clock(stack)
    with Session(stack["engine"]) as db, db.begin():
        user = User(email=f"{uuid4()}@example.org", full_name="Late student")
        db.add(user)
        db.flush()
        db.add(Membership(school_id=UUID(c["school_id"]), user_id=user.id, role="student"))
        session = AuthSession(user_id=user.id, expires_at=now + timedelta(hours=1))
        db.add(session)
        db.flush()
        late_id = str(user.id)
        late_headers = {
            "X-School-ID": c["school_id"],
            "Authorization": "Bearer " + access_token(stack["settings"], user.id, session.id),
        }
    late_profile = c["post"]("/students", {"user_id": late_id, "graduation_grade": 12})
    c["post"](
        "/student-enrollments",
        {
            "student_id": late_profile["id"],
            "period_id": c["period"]["id"],
            "class_id": c["classes"][0]["id"],
        },
    )
    assert client.get("/api/v1/student/exams", headers=late_headers).json() == []
    assert (
        client.post(f"/api/v1/student/exams/{exam['id']}/attempt", headers=late_headers).status_code
        == 404
    )


def test_enrollment_cannot_change_during_running_exam_and_answers_are_database_protected(
    stack, client, delivery_case
):
    c = delivery_case
    exam, q, _, _ = create_exam(stack, client, c)
    attempt = start(client, c, exam)
    change = {
        "expected_revision": 1,
        "action": "withdrawn",
        "effective_on": clock(stack).date().isoformat(),
    }
    path = f"/student-enrollments/{c['enrollment']['id']}/transition"
    assert client.post("/api/v1" + path, headers=c["h"]["admin"], json=change).status_code == 409
    attempt = c["post"](
        f"/student/attempts/{attempt['id']}/submit",
        {"expected_revision": attempt["revision"]},
        "student",
        200,
    )
    changed = c["post"](path, change, status=200)
    assert changed["student"]["status"] == "withdrawn"
    assert (
        client.post(
            f"/api/v1/student/exams/{exam['id']}/attempt", headers=c["h"]["student"]
        ).status_code
        == 409
    )
    assert (
        client.get(
            f"/api/v1/student/attempts/{attempt['id']}", headers=c["h"]["student"]
        ).status_code
        == 200
    )
    runtime = create_engine(stack["settings"].sync_database_url)
    try:
        for sql, params in (
            (
                "UPDATE exam_attempts SET answers='{}'::jsonb || jsonb_build_object(:question,'B') WHERE id=:id",
                {"id": attempt["id"], "question": q["id"]},
            ),
            (
                "UPDATE exam_attempts SET deadline_at=deadline_at + interval '1 hour' WHERE id=:id",
                {"id": attempt["id"]},
            ),
            (
                "UPDATE student_enrollments SET state='active', ended_on=NULL WHERE id=:id",
                {"id": c["enrollment"]["id"]},
            ),
            (
                "UPDATE class_exams SET ends_at=ends_at + interval '1 hour' WHERE id=:id",
                {"id": exam["id"]},
            ),
        ):
            with pytest.raises(DBAPIError), runtime.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.school_id', :school, true)"),
                    {"school": c["school_id"]},
                )
                conn.execute(text(sql), params)
        with runtime.begin() as conn:
            for table in (
                "student_profiles",
                "student_enrollments",
                "exam_participants",
                "exam_attempts",
            ):
                assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        with pytest.raises(DBAPIError), stack["engine"].begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO student_enrollments(id,school_id,student_id,period_id,class_id) VALUES (:id,:school,:student,:period,:class)"
                ),
                {
                    "id": uuid4(),
                    "school": c["other_school"],
                    "student": c["profile"]["id"],
                    "period": c["period"]["id"],
                    "class": c["classes"][0]["id"],
                },
            )
    finally:
        runtime.dispose()


@pytest.mark.parametrize("final_grade", [12, 13])
def test_semester_continuation_promotion_and_smk_graduation_preserve_history(
    client, school_case, final_grade
):
    c = school_case
    profile = c["post"](
        "/students", {"user_id": c["ids"]["student"], "graduation_grade": final_grade}
    )
    first_class = c["post"]("/classes", {"name": "First grade", "grade_level": final_grade - 1})
    final_class = c["post"]("/classes", {"name": "Graduating grade", "grade_level": final_grade})
    periods = []
    for year, semester, starts, ends in (
        (2024, 1, "2024-07-01", "2024-12-31"),
        (2024, 2, "2025-01-01", "2025-06-30"),
        (2025, 1, "2025-07-01", "2025-12-31"),
        (2025, 2, "2026-01-01", "2026-06-30"),
    ):
        periods.append(
            c["post"](
                "/academic-periods",
                {"year_start": year, "semester": semester, "starts_on": starts, "ends_on": ends},
            )
        )
    enrollment = c["post"](
        "/student-enrollments",
        {"student_id": profile["id"], "period_id": periods[0]["id"], "class_id": first_class["id"]},
    )
    for index, (action, classroom) in enumerate(
        (("continued", first_class), ("promoted", final_class), ("continued", final_class))
    ):
        result = c["post"](
            f"/student-enrollments/{enrollment['id']}/transition",
            {
                "expected_revision": 1,
                "action": action,
                "effective_on": periods[index]["ends_on"],
                "target_period_id": periods[index + 1]["id"],
                "target_class_id": classroom["id"],
            },
            status=200,
        )
        assert result["previous"]["state"] == action
        assert result["previous"]["id"] != result["current"]["id"]
        enrollment = result["current"]
    result = c["post"](
        f"/student-enrollments/{enrollment['id']}/transition",
        {"expected_revision": 1, "action": "graduated", "effective_on": "2026-06-30"},
        status=200,
    )
    assert result["current"] is None and result["student"]["status"] == "graduated"
    assert result["student"]["graduated_on"] == "2026-06-30"
    history = client.get("/api/v1/student/enrollments", headers=c["h"]["student"]).json()
    assert len(history) == 4
    assert [item["state"] for item in history] == [
        "continued",
        "promoted",
        "continued",
        "graduated",
    ]
    assert (
        history[0]["class_id"] == first_class["id"] and history[-1]["class_id"] == final_class["id"]
    )
    assert (
        client.post(
            "/api/v1/student-enrollments",
            headers=c["h"]["admin"],
            json={
                "student_id": profile["id"],
                "period_id": c["period"]["id"],
                "class_id": final_class["id"],
            },
        ).status_code
        == 409
    )


def test_repetition_and_concurrent_transitions_require_one_successor(stack, client, school_case):
    c = school_case
    profile = c["post"]("/students", {"user_id": c["ids"]["student"], "graduation_grade": 13})
    old = c["post"](
        "/academic-periods",
        {"year_start": 2024, "semester": 2, "starts_on": "2025-01-01", "ends_on": "2025-06-30"},
    )
    target = c["post"](
        "/academic-periods",
        {"year_start": 2025, "semester": 1, "starts_on": "2025-07-01", "ends_on": "2025-12-31"},
    )
    enrollment = c["post"](
        "/student-enrollments",
        {"student_id": profile["id"], "period_id": old["id"], "class_id": c["classes"][0]["id"]},
    )
    path = f"/student-enrollments/{enrollment['id']}/transition"
    assert (
        client.post(
            "/api/v1" + path,
            headers=c["h"]["admin"],
            json={"expected_revision": 1, "action": "graduated", "effective_on": "2025-06-30"},
        ).status_code
        == 422
    )
    payload = {
        "expected_revision": 1,
        "action": "repeated",
        "effective_on": "2025-06-30",
        "target_period_id": target["id"],
        "target_class_id": c["classes"][1]["id"],
    }
    responses = race(stack, [("POST", path, c["h"]["admin"], payload)] * 2)
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    with Session(stack["engine"]) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(StudentEnrollment)
                .where(
                    StudentEnrollment.student_id == UUID(profile["id"]),
                    StudentEnrollment.state == "active",
                )
            )
            == 1
        )


def test_student_permissions_invalid_answers_and_scope_guard(stack, client, delivery_case):
    c = delivery_case
    exam, q, _, _ = create_exam(stack, client, c)
    attempt = start(client, c, exam)
    path = f"/api/v1/student/attempts/{attempt['id']}/answers"
    for answer in (
        {"question_id": str(uuid4()), "value": "A"},
        {"question_id": q["id"], "value": "H"},
    ):
        assert (
            client.put(
                path, headers=c["h"]["student"], json={"expected_revision": 1, "answers": [answer]}
            ).status_code
            == 422
        )
    assert (
        client.put(
            path,
            headers=c["h"]["student"],
            json={
                "expected_revision": 1,
                "answers": [{"question_id": q["id"], "value": "A"}],
                "score": 100,
            },
        ).status_code
        == 422
    )
    assert (
        client.get(f"/api/v1/exam-attempts/{attempt['id']}", headers=c["h"]["student"]).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/student/attempts/{attempt['id']}",
            headers={**c["h"]["student"], "X-School-ID": c["other_school"]},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/students",
            headers=c["h"]["a"],
            json={"user_id": c["ids"]["student"], "graduation_grade": 12},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/students",
            headers=c["h"]["admin"],
            json={"user_id": c["ids"]["a"], "graduation_grade": 12},
        ).status_code
        == 422
    )


def test_peer_student_cannot_read_or_change_another_attempt(stack, client, delivery_case):
    c = delivery_case
    with Session(stack["engine"]) as db, db.begin():
        user = User(email=f"{uuid4()}@example.org", full_name="Peer student")
        db.add(user)
        db.flush()
        db.add(Membership(school_id=UUID(c["school_id"]), user_id=user.id, role="student"))
        session = AuthSession(user_id=user.id, expires_at=clock(stack) + timedelta(hours=1))
        db.add(session)
        db.flush()
        user_id = str(user.id)
        peer = {
            "X-School-ID": c["school_id"],
            "Authorization": "Bearer " + access_token(stack["settings"], user.id, session.id),
        }
    profile = c["post"]("/students", {"user_id": user_id, "graduation_grade": 12})
    c["post"](
        "/student-enrollments",
        {
            "student_id": profile["id"],
            "period_id": c["period"]["id"],
            "class_id": c["classes"][0]["id"],
        },
    )
    exam, q, _, _ = create_exam(stack, client, c)
    attempt = start(client, c, exam)
    own = client.post(f"/api/v1/student/exams/{exam['id']}/attempt", headers=peer)
    assert own.status_code == 200 and own.json()["id"] != attempt["id"]
    assert client.get(f"/api/v1/student/attempts/{attempt['id']}", headers=peer).status_code == 404
    assert (
        client.put(
            f"/api/v1/student/attempts/{attempt['id']}/answers",
            headers=peer,
            json={
                "expected_revision": attempt["revision"],
                "answers": [{"question_id": q["id"], "value": "B"}],
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/student/attempts/{attempt['id']}/submit",
            headers=peer,
            json={"expected_revision": attempt["revision"]},
        ).status_code
        == 404
    )


def test_save_waiting_for_row_lock_uses_current_server_time(stack, client, delivery_case):
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    c = delivery_case
    exam, q, _, _ = create_exam(stack, client, c, seconds=3)
    attempt = save(client, c, start(client, c, exam), q)

    def queued_save():
        with httpx.Client(base_url=stack["urls"][1], trust_env=False, timeout=10) as api:
            return api.put(
                f"/api/v1/student/attempts/{attempt['id']}/answers",
                headers=c["h"]["student"],
                json={
                    "expected_revision": attempt["revision"],
                    "answers": [{"question_id": q["id"], "value": "B"}],
                },
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with stack["engine"].begin() as connection:
            connection.execute(
                text("SELECT id FROM exam_attempts WHERE id=:id FOR UPDATE"), {"id": attempt["id"]}
            )
            future = executor.submit(queued_save)
            wait_until(stack, datetime.fromisoformat(attempt["deadline_at"]))
        response = future.result(timeout=10)
    assert response.status_code == 409, response.text
    state = client.get(
        f"/api/v1/student/attempts/{attempt['id']}", headers=c["h"]["student"]
    ).json()
    assert state["answers"] == {q["id"]: "A"} and state["submitted_reason"] == "timeout"


def test_co_teachers_can_manage_shared_class_but_other_classes_cannot(stack, client, delivery_case):
    c = delivery_case
    exam, _, _, _ = create_exam(stack, client, c)
    attempt = start(client, c, exam)
    assert (
        client.get(f"/api/v1/exam-attempts/{attempt['id']}", headers=c["h"]["b"]).status_code == 403
    )
    c["post"](
        "/teaching-assignments",
        {
            "period_id": c["period"]["id"],
            "class_id": c["classes"][0]["id"],
            "subject_id": c["subject"]["id"],
            "teacher_id": c["ids"]["b"],
        },
    )
    assert (
        client.get(f"/api/v1/exam-attempts/{attempt['id']}", headers=c["h"]["b"]).status_code == 200
    )
    exams = client.get(
        "/api/v1/class-exams", headers=c["h"]["b"], params={"period_id": c["period"]["id"]}
    ).json()
    assert [e["id"] for e in exams] == [exam["id"]]
    assert (
        client.get(f"/api/v1/exam-attempts/{attempt['id']}", headers=c["h"]["c"]).status_code == 403
    )


def test_released_shared_package_cannot_be_published_for_late_class(stack, client, delivery_case):
    c = delivery_case
    exam, _, _, version = create_exam(stack, client, c, seconds=2)
    attempt = start(client, c, exam)
    c["post"](
        f"/student/attempts/{attempt['id']}/submit",
        {"expected_revision": attempt["revision"]},
        "student",
        200,
    )
    now = clock(stack)
    late_exam = c["post"](
        "/class-exams",
        {
            "module_version_id": version["id"],
            "assignment_id": c["assignments"]["b"]["id"],
            "starts_at": (now + timedelta(seconds=10)).isoformat(),
            "ends_at": (now + timedelta(minutes=2)).isoformat(),
        },
        "b",
    )
    wait_until(stack, datetime.fromisoformat(exam["ends_at"]))
    c["post"](
        f"/class-exams/{exam['id']}/release-results",
        {"expected_revision": exam["revision"]},
        "a",
        200,
    )
    rejected = client.post(
        f"/api/v1/class-exams/{late_exam['id']}/publish",
        headers=c["h"]["b"],
        json={"expected_revision": late_exam["revision"]},
    )
    assert rejected.status_code == 409 and "released results" in rejected.text
