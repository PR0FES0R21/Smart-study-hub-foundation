"""Exercise collaboration against PostgreSQL RLS and two independent API processes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from studyhub.academics.models import AcademicAudit, CoordinationGroup
from studyhub.core.security import access_token
from studyhub.models import AuthSession, Membership, School, User

pytestmark = pytest.mark.integration

QUESTION = {
    "kind": "multiple_choice",
    "prompt": "Berapakah 2 + 2?",
    "options": [{"key": "A", "text": "4"}, {"key": "B", "text": "5"}],
    "correct_option": "A",
}


@pytest.fixture
def school_case(stack, client):
    ids, headers = {}, {}
    school_id, other_school = uuid4(), uuid4()
    with Session(stack["engine"]) as db, db.begin():
        db.add_all(
            [
                School(id=school_id, code=uuid4().hex[:20], name="Collaboration SMK", level="SMK"),
                School(id=other_school, code=uuid4().hex[:20], name="Other SMK", level="SMK"),
            ]
        )
        db.flush()
        for name, role in (
            ("admin", "admin"),
            ("a", "teacher"),
            ("b", "teacher"),
            ("c", "teacher"),
            ("student", "student"),
        ):
            user = User(email=f"{uuid4().hex}@example.org", full_name=name, password_hash=None)
            db.add(user)
            db.flush()
            session = AuthSession(
                user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=1)
            )
            db.add(session)
            for school in (school_id, other_school):
                db.add(Membership(school_id=school, user_id=user.id, role=role))
            db.flush()
            ids[name] = str(user.id)
            headers[name] = {
                "Authorization": "Bearer " + access_token(stack["settings"], user.id, session.id),
                "X-School-ID": str(school_id),
            }

    def post(path, payload, actor="admin", status=201):
        response = client.post("/api/v1" + path, headers=headers[actor], json=payload)
        assert response.status_code == status, response.text
        return response.json()

    period = post(
        "/academic-periods",
        {"year_start": 2026, "semester": 1, "starts_on": "2026-07-01", "ends_on": "2026-12-31"},
    )
    subject = post("/subjects", {"code": "MATH", "name": "Matematika"})
    classes = []
    for code in ("TKJ", "TP", "TBSM"):
        program = post("/expertise-programs", {"code": code, "name": code})
        classes.append(
            post(
                "/classes",
                {"name": "12 " + code + " 2", "grade_level": 12, "program_id": program["id"]},
            )
        )
    assignments = {}
    for teacher, classroom in zip(("a", "b", "c"), classes, strict=True):
        assignments[teacher] = post(
            "/teaching-assignments",
            {
                "period_id": period["id"],
                "class_id": classroom["id"],
                "subject_id": subject["id"],
                "teacher_id": ids[teacher],
            },
        )
    group = post(
        "/coordination-groups",
        {
            "period_id": period["id"],
            "subject_id": subject["id"],
            "grade_level": 12,
            "coordinator_id": ids["a"],
        },
    )
    return {
        "ids": ids,
        "h": headers,
        "period": period,
        "subject": subject,
        "classes": classes,
        "assignments": assignments,
        "group": group,
        "post": post,
        "school_id": str(school_id),
        "other_school": str(other_school),
    }


def approved_question(client, case, content=None, author="b"):
    q = case["post"](
        "/questions", {"group_id": case["group"]["id"], "content": content or QUESTION}, author
    )
    q = case["post"](
        f"/questions/{q['id']}/submit", {"expected_revision": q["revision"]}, author, 200
    )
    return case["post"](
        f"/questions/{q['id']}/review",
        {"expected_revision": q["revision"], "decision": "approved"},
        "a",
        200,
    )


def module_draft(client, case, q):
    module = case["post"](
        "/exam-modules", {"group_id": case["group"]["id"], "title": "UAS Matematika 12"}, "a"
    )
    response = client.put(
        f"/api/v1/exam-modules/{module['id']}",
        headers=case["h"]["a"],
        json={
            "expected_revision": module["revision"],
            "title": module["title"],
            "items": [{"question_id": q["id"], "question_revision": q["revision"], "points": 10}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def published(client, case):
    q = approved_question(client, case)
    module = module_draft(client, case, q)
    version = case["post"](
        f"/exam-modules/{module['id']}/publish", {"expected_revision": module["revision"]}, "a"
    )
    return q, module, version


def race(stack, requests):
    barrier = Barrier(len(requests))

    def call(index, request):
        method, path, headers, payload = request
        with httpx.Client(base_url=stack["urls"][index % 2], trust_env=False, timeout=15) as api:
            barrier.wait(timeout=10)
            return api.request(method, "/api/v1" + path, headers=headers, json=payload)

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        return list(pool.map(lambda args: call(*args), enumerate(requests)))


def test_shared_package_across_programs_and_immutable_versions(client, school_case):
    c = school_case
    q, module, version = published(client, c)
    assert version["snapshot"]["total_points"] == 10
    assert version["snapshot"]["items"][0]["content"]["correct_option"] == "A"
    for teacher in ("a", "b"):
        exam = c["post"](
            "/class-exams",
            {
                "module_version_id": version["id"],
                "assignment_id": c["assignments"][teacher]["id"],
                "starts_at": "2026-11-15T08:00:00+07:00",
                "ends_at": "2026-11-15T10:00:00+07:00",
            },
            teacher,
        )
        assert exam["module_version_id"] == version["id"]
    changed = {**QUESTION, "prompt": "Revised question", "correct_option": "B"}
    response = client.put(
        f"/api/v1/questions/{q['id']}",
        headers=c["h"]["b"],
        json={"expected_revision": q["revision"], "content": changed},
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["status"] == "draft"
    # Old selection has not been re-reviewed. It must not silently publish changed content.
    assert (
        client.post(
            f"/api/v1/exam-modules/{module['id']}/publish",
            headers=c["h"]["a"],
            json={"expected_revision": module["revision"] + 1},
        ).status_code
        == 409
    )
    submitted = c["post"](
        f"/questions/{q['id']}/submit", {"expected_revision": edited["revision"]}, "b", 200
    )
    reviewed = c["post"](
        f"/questions/{q['id']}/review",
        {"expected_revision": submitted["revision"], "decision": "approved"},
        "a",
        200,
    )
    updated = client.put(
        f"/api/v1/exam-modules/{module['id']}",
        headers=c["h"]["a"],
        json={
            "expected_revision": module["revision"] + 1,
            "title": module["title"],
            "items": [
                {"question_id": q["id"], "question_revision": reviewed["revision"], "points": 20}
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    v2 = c["post"](
        f"/exam-modules/{module['id']}/publish",
        {"expected_revision": updated.json()["revision"]},
        "a",
    )
    assert v2["version"] == 2 and v2["checksum"] != version["checksum"]
    old = client.get(f"/api/v1/module-versions/{version['id']}", headers=c["h"]["b"])
    assert old.json() == version
    exams = client.get(
        "/api/v1/class-exams", headers=c["h"]["b"], params={"period_id": c["period"]["id"]}
    ).json()
    assert len(exams) == 1 and exams[0]["module_version_id"] == version["id"]
    versions = client.get(
        f"/api/v1/exam-modules/{module['id']}/versions", headers=c["h"]["b"]
    ).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert all("snapshot" not in v for v in versions)
    # Same teacher can teach several subjects/classes; team teaching is allowed too.
    extra = c["post"](
        "/teaching-assignments",
        {
            "period_id": c["period"]["id"],
            "class_id": c["classes"][0]["id"],
            "subject_id": c["subject"]["id"],
            "teacher_id": c["ids"]["b"],
        },
    )
    duplicate = client.post(
        "/api/v1/class-exams",
        headers=c["h"]["b"],
        json={
            "module_version_id": version["id"],
            "assignment_id": extra["id"],
            "starts_at": "2026-11-15T08:00:00Z",
            "ends_at": "2026-11-15T09:00:00Z",
        },
    )
    assert duplicate.status_code == 409


def test_permissions_tenant_isolation_and_question_privacy(client, school_case):
    c = school_case
    q, module, version = published(client, c)
    assert client.get(f"/api/v1/questions/{q['id']}", headers=c["h"]["c"]).status_code == 403
    assert (
        client.get(
            "/api/v1/questions", params={"group_id": c["group"]["id"]}, headers=c["h"]["c"]
        ).json()
        == []
    )
    assert (
        client.get(f"/api/v1/exam-modules/{module['id']}", headers=c["h"]["b"]).status_code == 403
    )
    assert (
        client.post(
            f"/api/v1/exam-modules/{module['id']}/publish",
            headers=c["h"]["b"],
            json={"expected_revision": 3},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/questions/{q['id']}/review",
            headers=c["h"]["b"],
            json={"expected_revision": q["revision"], "decision": "approved"},
        ).status_code
        == 403
    )
    # A school administrator can supervise, but cannot forge a teacher's authorship/review.
    assert (
        client.put(
            f"/api/v1/questions/{q['id']}",
            headers=c["h"]["admin"],
            json={"expected_revision": q["revision"], "content": QUESTION},
        ).status_code
        == 403
    )
    for path in (
        f"/questions/{q['id']}",
        f"/module-versions/{version['id']}",
        f"/coordination-groups/{c['group']['id']}",
    ):
        assert client.get("/api/v1" + path, headers=c["h"]["student"]).status_code == 403
        assert (
            client.get(
                "/api/v1" + path, headers={**c["h"]["a"], "X-School-ID": c["other_school"]}
            ).status_code
            == 404
        )
    foreign_subject = client.post(
        "/api/v1/subjects",
        headers={**c["h"]["admin"], "X-School-ID": c["other_school"]},
        json={"code": "OTHER", "name": "Other"},
    ).json()
    data = {
        "period_id": c["period"]["id"],
        "class_id": c["classes"][0]["id"],
        "subject_id": foreign_subject["id"],
        "teacher_id": c["ids"]["a"],
    }
    assert (
        client.post("/api/v1/teaching-assignments", headers=c["h"]["admin"], json=data).status_code
        == 404
    )
    assert (
        client.post("/api/v1/teaching-assignments", headers=c["h"]["a"], json=data).status_code
        == 403
    )
    assert client.get("/api/v1/academic-audit", headers=c["h"]["b"]).status_code == 403


def test_replacement_revokes_scope_preserves_work_and_checks_eligibility(client, school_case):
    c = school_case
    q = approved_question(client, c)
    module = module_draft(client, c, q)
    path = f"/api/v1/coordination-groups/{c['group']['id']}/coordinator"
    response = client.put(
        path,
        headers=c["h"]["admin"],
        json={"expected_revision": 1, "coordinator_id": c["ids"]["student"]},
    )
    assert response.status_code == 422
    # Losing the last teaching assignment while appointed is an explicit conflict.
    assert (
        client.put(
            f"/api/v1/teaching-assignments/{c['assignments']['a']['id']}/status",
            headers=c["h"]["admin"],
            json={"is_active": False},
        ).status_code
        == 409
    )
    response = client.put(
        path,
        headers=c["h"]["admin"],
        json={"expected_revision": 1, "coordinator_id": c["ids"]["b"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == c["group"]["id"]
    assert (
        client.put(
            path,
            headers=c["h"]["admin"],
            json={"expected_revision": 1, "coordinator_id": c["ids"]["c"]},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/exam-modules/{module['id']}/publish",
            headers=c["h"]["a"],
            json={"expected_revision": module["revision"]},
        ).status_code
        == 403
    )
    version = c["post"](
        f"/exam-modules/{module['id']}/publish", {"expected_revision": module["revision"]}, "b"
    )
    assert version["snapshot"]["items"][0]["author_id"] == c["ids"]["b"]
    assert (
        client.put(
            path, headers=c["h"]["admin"], json={"expected_revision": 2, "coordinator_id": None}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/exam-modules",
            headers=c["h"]["b"],
            json={"group_id": c["group"]["id"], "title": "Denied"},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/teaching-assignments/{c['assignments']['b']['id']}/status",
            headers=c["h"]["admin"],
            json={"is_active": False},
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/module-versions/{version['id']}", headers=c["h"]["b"]).status_code
        == 403
    )
    history = client.get(
        "/api/v1/academic-audit", params={"resource_id": c["group"]["id"]}, headers=c["h"]["admin"]
    ).json()
    assert len(history) == 3
    assert history[0]["details"]["coordinator_id"] is None


def test_concurrent_question_edits_and_publish_conflict(stack, client, school_case):
    c = school_case
    q = c["post"]("/questions", {"group_id": c["group"]["id"], "content": QUESTION}, "b")
    requests = [
        (
            "PUT",
            f"/questions/{q['id']}",
            c["h"]["b"],
            {"expected_revision": 1, "content": {**QUESTION, "prompt": prompt}},
        )
        for prompt in ("First edit", "Second edit")
    ]
    results = race(stack, requests)
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    q = next(r.json() for r in results if r.status_code == 200)
    q = c["post"](f"/questions/{q['id']}/submit", {"expected_revision": q["revision"]}, "b", 200)
    q = c["post"](
        f"/questions/{q['id']}/review",
        {"expected_revision": q["revision"], "decision": "approved"},
        "a",
        200,
    )
    module = module_draft(client, c, q)
    request = (
        "POST",
        f"/exam-modules/{module['id']}/publish",
        c["h"]["a"],
        {"expected_revision": module["revision"]},
    )
    results = race(stack, [request, request])
    assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]


def test_concurrent_unique_coordination_and_assignment_changes(stack, client, school_case):
    c = school_case
    # A new grade-13 group validates SMK's fourth year and concurrent uniqueness.
    classroom = c["post"]("/classes", {"name": "13 TKJ", "grade_level": 13})
    c["post"](
        "/teaching-assignments",
        {
            "period_id": c["period"]["id"],
            "class_id": classroom["id"],
            "subject_id": c["subject"]["id"],
            "teacher_id": c["ids"]["a"],
        },
    )
    request = (
        "POST",
        "/coordination-groups",
        c["h"]["admin"],
        {
            "period_id": c["period"]["id"],
            "subject_id": c["subject"]["id"],
            "grade_level": 13,
            "coordinator_id": c["ids"]["a"],
        },
    )
    results = race(stack, [request, request])
    assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
    # Existing group remains scoped to grade 12, including teachers in all three programs.
    teachers = client.get(
        f"/api/v1/coordination-groups/{c['group']['id']}/teachers", headers=c["h"]["b"]
    ).json()
    assert {t["id"] for t in teachers} == {c["ids"][k] for k in ("a", "b", "c")}
    assert sum(t["is_coordinator"] for t in teachers) == 1
    # The same account is only coordinator in its appointed scopes.
    other = c["post"]("/subjects", {"code": "INDO", "name": "Bahasa Indonesia"})
    c["post"](
        "/teaching-assignments",
        {
            "period_id": c["period"]["id"],
            "class_id": c["classes"][0]["id"],
            "subject_id": other["id"],
            "teacher_id": c["ids"]["b"],
        },
    )
    group2 = c["post"](
        "/coordination-groups",
        {
            "period_id": c["period"]["id"],
            "subject_id": other["id"],
            "grade_level": 12,
            "coordinator_id": c["ids"]["b"],
        },
    )
    assert (
        client.post(
            "/api/v1/exam-modules",
            headers=c["h"]["a"],
            json={"group_id": group2["id"], "title": "Wrong subject"},
        ).status_code
        == 403
    )
    groups = client.get(
        "/api/v1/coordination-groups", headers=c["h"]["a"], params={"period_id": c["period"]["id"]}
    ).json()
    assert len(groups) == 2 and all(group["id"] != group2["id"] for group in groups)
    assignments = client.get(
        "/api/v1/teaching-assignments", headers=c["h"]["b"], params={"period_id": c["period"]["id"]}
    ).json()
    assert len(assignments) == 2 and all(a["teacher_id"] == c["ids"]["b"] for a in assignments)


def test_review_states_closed_period_and_mismatched_exam(client, school_case):
    c = school_case
    q = c["post"](
        "/questions",
        {
            "group_id": c["group"]["id"],
            "content": {
                "kind": "essay",
                "prompt": "Jelaskan pecahan",
                "rubric": "Ketepatan konsep",
            },
        },
        "b",
    )
    q = c["post"](f"/questions/{q['id']}/submit", {"expected_revision": q["revision"]}, "b", 200)
    assert (
        client.put(
            f"/api/v1/questions/{q['id']}",
            headers=c["h"]["b"],
            json={"expected_revision": q["revision"], "content": QUESTION},
        ).status_code
        == 409
    )
    q = c["post"](
        f"/questions/{q['id']}/review",
        {"expected_revision": q["revision"], "decision": "rejected", "note": "Lengkapi rubrik"},
        "a",
        200,
    )
    q = c["post"](f"/questions/{q['id']}/submit", {"expected_revision": q["revision"]}, "b", 200)
    q = c["post"](
        f"/questions/{q['id']}/review",
        {"expected_revision": q["revision"], "decision": "approved"},
        "a",
        200,
    )
    module = module_draft(client, c, q)
    version = c["post"](
        f"/exam-modules/{module['id']}/publish", {"expected_revision": module["revision"]}, "a"
    )
    assert (
        client.post(
            "/api/v1/class-exams",
            headers=c["h"]["b"],
            json={
                "module_version_id": version["id"],
                "assignment_id": c["assignments"]["a"]["id"],
                "starts_at": "2026-11-15T08:00:00Z",
                "ends_at": "2026-11-15T09:00:00Z",
            },
        ).status_code
        == 403
    )
    c["post"](f"/academic-periods/{c['period']['id']}/close", None, status=200)
    assert (
        client.post(
            "/api/v1/questions",
            headers=c["h"]["b"],
            json={"group_id": c["group"]["id"], "content": QUESTION},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/exam-modules/{module['id']}/publish",
            headers=c["h"]["a"],
            json={"expected_revision": module["revision"] + 1},
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/v1/module-versions/{version['id']}", headers=c["h"]["b"]).status_code
        == 200
    )


def test_rls_composite_fks_and_immutable_records(stack, client, school_case):
    c = school_case
    q, module, version = published(client, c)
    runtime = create_engine(stack["settings"].sync_database_url)
    tables = (
        "academic_periods",
        "teaching_assignments",
        "coordination_groups",
        "academic_audit",
        "questions",
        "exam_modules",
        "module_versions",
        "class_exams",
    )
    try:
        with runtime.begin() as conn:
            for table in tables:
                assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0
            conn.execute(
                text("SELECT set_config('app.school_id', :school, true)"),
                {"school": c["other_school"]},
            )
            for table in tables:
                assert (
                    conn.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE school_id=:school"),
                        {"school": c["school_id"]},
                    )
                    == 0
                )
        for table in ("module_versions", "academic_audit"):
            for operation in ("UPDATE", "DELETE"):
                with pytest.raises(DBAPIError), runtime.begin() as conn:
                    conn.execute(
                        text("SELECT set_config('app.school_id', :school, true)"),
                        {"school": c["school_id"]},
                    )
                    sql = (
                        f"UPDATE {table} SET school_id=school_id WHERE school_id=:school"
                        if operation == "UPDATE"
                        else f"DELETE FROM {table} WHERE school_id=:school"
                    )
                    conn.execute(text(sql), {"school": c["school_id"]})
        # Even a privileged connection cannot insert a cross-tenant question reference.
        with pytest.raises(DBAPIError), stack["engine"].begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO questions(id,school_id,group_id,author_id,content) VALUES (:id,:school,:group,:author,'{}'::jsonb)"
                ),
                {
                    "id": uuid4(),
                    "school": c["other_school"],
                    "group": c["group"]["id"],
                    "author": c["ids"]["b"],
                },
            )
        with pytest.raises(DBAPIError), runtime.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.school_id', :school, true)"),
                {"school": c["other_school"]},
            )
            conn.execute(
                text(
                    "INSERT INTO academic_periods(id,school_id,year_start,semester,starts_on,ends_on) VALUES (:id,:school,2027,1,'2027-07-01','2027-12-31')"
                ),
                {"id": uuid4(), "school": c["school_id"]},
            )
    finally:
        runtime.dispose()
    with Session(stack["engine"]) as db:
        logs = db.scalars(
            select(AcademicAudit).where(AcademicAudit.school_id == UUID(c["school_id"]))
        ).all()
        assert logs and all(
            "content" not in log.details and "correct_option" not in log.details for log in logs
        )
        assert db.get(CoordinationGroup, UUID(c["group"]["id"])).coordinator_id == UUID(
            c["ids"]["a"]
        )


def test_question_selection_rejects_other_groups_and_unapproved_revisions(client, school_case):
    c = school_case
    q = approved_question(client, c)
    other_subject = c["post"]("/subjects", {"code": "OTHER", "name": "Other subject"})
    c["post"](
        "/teaching-assignments",
        {
            "period_id": c["period"]["id"],
            "class_id": c["classes"][0]["id"],
            "subject_id": other_subject["id"],
            "teacher_id": c["ids"]["a"],
        },
    )
    other_group = c["post"](
        "/coordination-groups",
        {
            "period_id": c["period"]["id"],
            "subject_id": other_subject["id"],
            "grade_level": 12,
            "coordinator_id": c["ids"]["a"],
        },
    )
    other_question = c["post"](
        "/questions", {"group_id": other_group["id"], "content": QUESTION}, "a"
    )
    module = c["post"](
        "/exam-modules", {"group_id": c["group"]["id"], "title": "Selection checks"}, "a"
    )
    assert (
        client.post(
            f"/api/v1/exam-modules/{module['id']}/publish",
            headers=c["h"]["a"],
            json={"expected_revision": 1},
        ).status_code
        == 422
    )
    for item, expected in ((other_question, 422), ({**q, "revision": 1}, 409)):
        response = client.put(
            f"/api/v1/exam-modules/{module['id']}",
            headers=c["h"]["a"],
            json={
                "title": "Selection",
                "expected_revision": 1,
                "items": [
                    {"question_id": item["id"], "question_revision": item["revision"], "points": 10}
                ],
            },
        )
        assert response.status_code == expected, response.text
    saved = client.get(f"/api/v1/exam-modules/{module['id']}", headers=c["h"]["a"]).json()
    assert saved["revision"] == 1 and saved["items"] == []
    assert (
        client.get(
            "/api/v1/questions", headers=c["h"]["b"], params={"group_id": other_group["id"]}
        ).status_code
        == 403
    )


def test_inactive_membership_revokes_coordinator_and_period_roles(stack, client, school_case):
    c = school_case
    with Session(stack["engine"]) as db, db.begin():
        member = db.get(Membership, (UUID(c["school_id"]), UUID(c["ids"]["a"])))
        member.is_active = False
    response = client.post(
        "/api/v1/exam-modules",
        headers=c["h"]["a"],
        json={"group_id": c["group"]["id"], "title": "Denied"},
    )
    assert response.status_code == 403
    response = client.put(
        f"/api/v1/coordination-groups/{c['group']['id']}/coordinator",
        headers=c["h"]["admin"],
        json={"coordinator_id": c["ids"]["b"], "expected_revision": 1},
    )
    assert response.status_code == 200, response.text
    for level, invalid_grade in (("SMP", 13), ("SMA", 13)):
        school_id = uuid4()
        with Session(stack["engine"]) as db, db.begin():
            db.add(School(id=school_id, code=uuid4().hex[:20], name=level, level=level))
            db.flush()
            db.add(Membership(school_id=school_id, user_id=UUID(c["ids"]["admin"]), role="admin"))
        headers = {**c["h"]["admin"], "X-School-ID": str(school_id)}
        period = client.post(
            "/api/v1/academic-periods",
            headers=headers,
            json={
                "year_start": 2026,
                "semester": 1,
                "starts_on": "2026-07-01",
                "ends_on": "2026-12-31",
            },
        ).json()
        subject = client.post(
            "/api/v1/subjects", headers=headers, json={"code": "M", "name": "Math"}
        ).json()
        result = client.post(
            "/api/v1/coordination-groups",
            headers=headers,
            json={
                "period_id": period["id"],
                "subject_id": subject["id"],
                "grade_level": invalid_grade,
                "coordinator_id": c["ids"]["b"],
            },
        )
        assert result.status_code == 422, result.text
