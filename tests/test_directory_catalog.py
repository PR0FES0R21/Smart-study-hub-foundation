"""Directory/catalog permissions, history preservation and concurrent writes."""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from studyhub.academics.people_models import MemberProfile
from studyhub.models import Membership, User
from tests import test_academic_collaboration as collaboration
from tests import test_student_delivery as delivery
from tests.test_academic_collaboration import race
from tests.test_student_delivery import create_exam

school_case = collaboration.school_case
delivery_case = delivery.delivery_case
pytestmark = pytest.mark.integration


def test_calendar_wait_does_not_block_school_foreign_key_checks(stack, client, school_case):
    c = school_case
    period = c["period"]
    payload = {k: period[k] for k in ("year_start", "semester", "starts_on", "ends_on")}
    payload["expected_revision"] = 1

    def calendar_edit():
        with httpx.Client(base_url=stack["urls"][0], trust_env=False, timeout=10) as api:
            return api.put(
                f"/api/v1/academic-periods/{period['id']}", headers=c["h"]["admin"], json=payload
            )

    with ThreadPoolExecutor(max_workers=1) as executor, stack["engine"].connect() as conn:
        tx = conn.begin()
        future = None
        try:
            pid = conn.scalar(text("SELECT pg_backend_pid()"))
            conn.execute(
                text("SELECT id FROM academic_periods WHERE id=:id FOR SHARE"), {"id": period["id"]}
            )
            future = executor.submit(calendar_edit)
            deadline = time.monotonic() + 5
            while not conn.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND :pid=ANY(pg_blocking_pids(pid)))"
                ),
                {"pid": pid},
            ):
                assert time.monotonic() < deadline, (
                    "Calendar request never acquired its school lock"
                )
                time.sleep(0.02)
            # The calendar request is waiting for our period lock. Its school
            # lock must allow FK inserts from this independent request to finish.
            with httpx.Client(base_url=stack["urls"][1], trust_env=False, timeout=3) as api:
                response = api.post(
                    "/api/v1/teachers/register", headers=c["h"]["admin"], json=account()
                )
                assert response.status_code == 201, response.text
        finally:
            tx.rollback()
        assert future.result(timeout=5).status_code == 409  # referenced period


def request(client, c, method, path, data=None, *, actor="admin", status=200, headers=None):
    response = client.request(method, "/api/v1" + path, headers=headers or c["h"][actor], json=data)
    assert response.status_code == status, response.text
    return response.json() if status != 204 else None


def account(name="New member"):
    return {
        "full_name": name,
        "email": f"{uuid4().hex}@example.org",
        "password": "strong-test-password-123",
    }


def auth(client, c, data):
    response = client.post("/api/v1/auth/login", json={k: data[k] for k in ("email", "password")})
    assert response.status_code == 200, response.text
    return {
        "Authorization": "Bearer " + response.json()["access_token"],
        "X-School-ID": c["school_id"],
    }


def test_student_registration_is_atomic_and_school_data_stays_local(stack, client, school_case):
    c = school_case
    data = {
        **account("Siswa Baru"),
        "graduation_grade": 12,
        "nisn": "0012345678",
        "student_number": "S001",
        "birth_date": "2010-03-02",
        "admitted_on": "2026-07-01",
    }
    student = request(client, c, "POST", "/students/register", data, status=201)
    assert student["nisn"] == "0012345678" and student["member"]["revision"] == 1
    headers = auth(client, c, data)
    request(client, c, "GET", "/student/profile", headers=headers)
    request(client, c, "GET", "/students", actor="a", status=403)
    request(client, c, "GET", "/students", headers=headers, status=403)
    for forbidden in ({"is_platform_admin": True}, {"role": "admin"}):
        request(client, c, "POST", "/students/register", {**data, **forbidden}, status=422)
    # Duplicate NISN/email never creates an orphan or links an existing global identity.
    failed = {**data, "email": f"{uuid4().hex}@example.org"}
    request(client, c, "POST", "/students/register", failed, status=409)
    request(
        client,
        c,
        "POST",
        "/school-admins",
        {k: data[k] for k in ("email", "password", "full_name")},
        status=409,
    )
    with Session(stack["engine"]) as db:
        assert db.scalar(select(User.id).where(User.email == failed["email"])) is None
        assert db.get(User, UUID(student["user_id"])).is_platform_admin is False
    # One login can have different personal information in two schools.
    existing = c["post"]("/students", {"user_id": c["ids"]["student"], "graduation_grade": 12})
    path = f"/students/{existing['id']}"
    edited = request(
        client,
        c,
        "PUT",
        path,
        {
            "expected_revision": 1,
            "full_name": "Local school name",
            "graduation_grade": 12,
            "address": "PRIVATE ADDRESS",
        },
    )
    assert edited["member"]["full_name"] == "Local school name"
    other = {**c["h"]["admin"], "X-School-ID": c["other_school"]}
    request(client, c, "GET", path, headers=other, status=404)
    request(
        client,
        c,
        "PUT",
        path,
        {"expected_revision": 2, "full_name": "Intruder", "graduation_grade": 12},
        headers=other,
        status=404,
    )
    with Session(stack["engine"]) as db:
        assert db.get(User, UUID(c["ids"]["student"])).full_name == "student"
    audits = request(client, c, "GET", "/academic-audit?limit=100")
    assert "PRIVATE ADDRESS" not in str(audits) and data["password"] not in str(audits)


def test_student_update_deactivate_restore_and_history_guards(stack, client, delivery_case):
    c = delivery_case
    profile = c["profile"]
    path = f"/students/{profile['id']}"
    payload = {
        "full_name": "Updated student",
        "student_number": "NIS002",
        "graduation_grade": 12,
        "expected_revision": 1,
    }
    responses = race(stack, [("PUT", path, c["h"]["admin"], payload)] * 2)
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    request(
        client,
        c,
        "PUT",
        path,
        {**payload, "expected_revision": 2, "graduation_grade": 13},
        status=409,
    )
    request(
        client,
        c,
        "PUT",
        path,
        {**payload, "expected_revision": 2, "status": "graduated"},
        status=422,
    )
    request(client, c, "DELETE", path + "?expected_revision=2", status=204)
    request(client, c, "GET", "/student/profile", actor="student", status=403)
    assert request(client, c, "GET", "/students?active=true") == []
    disabled = request(client, c, "GET", path)
    assert disabled["status"] == "active" and disabled["member"]["revision"] == 3
    assert disabled["member"]["membership_active"] is False
    # Membership deactivation doesn't erase local history or another school's access.
    other_student = {**c["h"]["student"], "X-School-ID": c["other_school"]}
    request(client, c, "GET", "/schools/current", headers=other_student)
    request(client, c, "POST", path + "/restore", {"expected_revision": 3})
    history = request(client, c, "GET", "/student/enrollments", actor="student")
    assert history[0]["id"] == c["enrollment"]["id"]


def test_deactivation_racing_exam_start_preserves_running_attempt(stack, client, delivery_case):
    c = delivery_case
    exam, _, _, _ = create_exam(stack, client, c)
    replies = race(
        stack,
        [
            ("POST", f"/student/exams/{exam['id']}/attempt", c["h"]["student"], None),
            (
                "DELETE",
                f"/students/{c['profile']['id']}?expected_revision=1",
                c["h"]["admin"],
                None,
            ),
        ],
    )
    codes = [r.status_code for r in replies]
    assert codes == [200, 409] or (codes[0] in {403, 409} and codes[1] == 204), [
        r.text for r in replies
    ]


def test_teacher_crud_and_coordinator_homeroom_guards(client, school_case):
    c = school_case
    data = {
        **account("Teacher"),
        "employee_number": "T01",
        "nip": "001234567890123456",
        "employment_type": "permanent",
        "qualification": "S1 Pendidikan",
        "joined_on": "2024-07-01",
    }
    teacher = request(client, c, "POST", "/teachers/register", data, status=201)
    path = f"/teachers/{teacher['id']}"
    assert teacher["member"]["role"] == "teacher"
    assert len(request(client, c, "GET", "/teachers")) == 1
    update = {k: v for k, v in data.items() if k not in {"email", "password"}}
    teacher = request(
        client, c, "PUT", path, {**update, "full_name": "Teacher Edited", "expected_revision": 1}
    )
    assert teacher["member"]["full_name"] == "Teacher Edited"
    offering = request(
        client,
        c,
        "POST",
        "/class-offerings",
        {
            "period_id": c["period"]["id"],
            "class_id": c["classes"][0]["id"],
            "homeroom_teacher_id": teacher["user_id"],
            "room": "Lab 1",
        },
        status=201,
    )
    request(client, c, "DELETE", path + "?expected_revision=2", status=409)
    offering_path = f"/class-offerings/{offering['id']}"
    request(
        client,
        c,
        "PUT",
        offering_path,
        {"expected_revision": 1, "homeroom_teacher_id": None, "room": "Lab 2"},
    )
    assert (
        request(client, c, "GET", f"/class-offerings?period_id={c['period']['id']}")[0]["room"]
        == "Lab 2"
    )
    request(client, c, "DELETE", offering_path + "?expected_revision=2", status=204)
    request(client, c, "DELETE", path + "?expected_revision=2", status=204)
    request(client, c, "POST", path + "/restore", {"expected_revision": 3})
    coordinator = request(client, c, "POST", "/teachers", {"user_id": c["ids"]["a"]}, status=201)
    request(
        client,
        c,
        "DELETE",
        f"/teachers/{coordinator['id']}?expected_revision={coordinator['member']['revision']}",
        status=409,
    )
    request(client, c, "POST", "/teachers", {"user_id": c["ids"]["student"]}, status=404)
    request(client, c, "GET", "/teachers", actor="a", status=403)


def test_admin_crud_and_simultaneous_deactivation_leave_one_active(stack, client, school_case):
    c = school_case
    path = f"/school-admins/{c['ids']['admin']}"
    request(client, c, "DELETE", path + "?expected_revision=1", status=409)
    data = account("Second admin")
    other = request(client, c, "POST", "/school-admins", data, status=201)
    headers = auth(client, c, data)
    edited = request(
        client,
        c,
        "PUT",
        f"/school-admins/{other['user_id']}",
        {
            "expected_revision": 1,
            "full_name": "School administrator",
            "phone_number": "+628123456789",
        },
    )
    assert edited["revision"] == 2 and edited["role"] == "admin"
    assert len(request(client, c, "GET", "/school-admins")) == 2
    responses = race(
        stack,
        [
            (
                "DELETE",
                f"/school-admins/{other['user_id']}?expected_revision=2",
                c["h"]["admin"],
                None,
            ),
            ("DELETE", path + "?expected_revision=1", headers, None),
        ],
    )
    assert sorted(r.status_code for r in responses) in ([204, 403], [204, 409]), [
        r.text for r in responses
    ]
    with Session(stack["engine"]) as db:
        members = db.scalars(
            select(Membership).where(
                Membership.school_id == UUID(c["school_id"]),
                Membership.role == "admin",
                Membership.is_active.is_(True),
            )
        ).all()
        assert len(members) == 1
        assert db.get(User, UUID(other["user_id"])).is_platform_admin is False


def test_class_program_crud_and_used_class_structure_is_fixed(client, school_case):
    c = school_case
    program = c["post"](
        "/expertise-programs", {"code": "NEW", "name": "New", "description": "Program baru"}
    )
    program_path = f"/expertise-programs/{program['id']}"
    program = request(
        client, c, "PUT", program_path, {"code": "NEW2", "name": "Renamed", "expected_revision": 1}
    )
    assert request(client, c, "GET", program_path)["revision"] == 2
    classroom = c["post"](
        "/classes", {"name": "10 New", "grade_level": 10, "program_id": program["id"]}
    )
    class_path = f"/classes/{classroom['id']}"
    classroom = request(
        client,
        c,
        "PUT",
        class_path,
        {"name": "11 New", "grade_level": 11, "program_id": program["id"], "expected_revision": 1},
    )
    assert request(client, c, "GET", class_path)["grade_level"] == 11
    request(client, c, "DELETE", program_path + "?expected_revision=2", status=409)
    request(client, c, "DELETE", class_path + "?expected_revision=2", status=204)
    request(client, c, "DELETE", program_path + "?expected_revision=2", status=204)
    used = c["classes"][0]
    used_path = f"/classes/{used['id']}"
    request(
        client,
        c,
        "PUT",
        used_path,
        {
            "name": used["name"],
            "grade_level": 11,
            "program_id": used["program_id"],
            "expected_revision": 1,
        },
        status=409,
    )
    request(client, c, "DELETE", used_path + "?expected_revision=1", status=409)
    request(client, c, "DELETE", f"/subjects/{c['subject']['id']}", status=409)
    request(
        client,
        c,
        "GET",
        used_path,
        headers={**c["h"]["admin"], "X-School-ID": c["other_school"]},
        status=404,
    )


def test_class_edit_races_enrollment_without_changing_historical_grade(stack, client, school_case):
    c = school_case
    profile = c["post"]("/students", {"user_id": c["ids"]["student"], "graduation_grade": 12})
    classroom = c["post"]("/classes", {"name": "11 Class", "grade_level": 11})
    responses = race(
        stack,
        [
            (
                "POST",
                "/student-enrollments",
                c["h"]["admin"],
                {
                    "student_id": profile["id"],
                    "class_id": classroom["id"],
                    "period_id": c["period"]["id"],
                },
            ),
            (
                "PUT",
                f"/classes/{classroom['id']}",
                c["h"]["admin"],
                {"expected_revision": 1, "name": "13 Class", "grade_level": 13},
            ),
        ],
    )
    assert [r.status_code for r in responses] in ([201, 409], [422, 200]), [
        r.text for r in responses
    ]


def test_year_semester_crud_calendar_conflicts_and_history_guards(stack, client, school_case):
    c = school_case
    years = request(client, c, "GET", "/academic-years")
    assert years[0]["label"] == "2026/2027"
    year = c["post"]("/academic-years", {"year_start": 2030, "description": "Planning"})
    path = f"/academic-years/{year['id']}"
    year = request(
        client,
        c,
        "PUT",
        path,
        {"expected_revision": 1, "year_start": 2030, "description": "Approved calendar"},
    )
    assert request(client, c, "GET", path)["revision"] == 2
    data = {"year_start": 2030, "semester": 1, "starts_on": "2030-07-01", "ends_on": "2030-12-31"}
    period = c["post"]("/academic-periods", data)
    period_path = f"/academic-periods/{period['id']}"
    request(client, c, "DELETE", path + "?expected_revision=2", status=409)
    updated = request(
        client, c, "PUT", period_path, {**data, "starts_on": "2030-07-02", "expected_revision": 1}
    )
    assert request(client, c, "GET", period_path)["starts_on"] == "2030-07-02"
    assert updated["revision"] == 2
    request(client, c, "DELETE", period_path + "?expected_revision=2", status=204)
    request(client, c, "DELETE", path + "?expected_revision=2", status=204)
    used = c["period"]
    request(
        client,
        c,
        "PUT",
        f"/academic-periods/{used['id']}",
        {k: used[k] for k in data} | {"expected_revision": 1},
        status=409,
    )
    request(client, c, "DELETE", f"/academic-periods/{used['id']}?expected_revision=1", status=409)
    replies = race(
        stack,
        [
            (
                "POST",
                "/academic-periods",
                c["h"]["admin"],
                {
                    "year_start": 2030,
                    "semester": 2,
                    "starts_on": "2031-01-01",
                    "ends_on": "2031-06-30",
                },
            ),
            (
                "POST",
                "/academic-periods",
                c["h"]["admin"],
                {
                    "year_start": 2031,
                    "semester": 1,
                    "starts_on": "2031-05-01",
                    "ends_on": "2031-12-31",
                },
            ),
        ],
    )
    assert sorted(r.status_code for r in replies) == [201, 409], [r.text for r in replies]


def test_new_tables_enforce_rls_and_composite_foreign_keys(stack, client, school_case):
    c = school_case
    teacher = request(client, c, "POST", "/teachers/register", account(), status=201)
    request(
        client,
        c,
        "POST",
        "/class-offerings",
        {
            "period_id": c["period"]["id"],
            "class_id": c["classes"][0]["id"],
            "homeroom_teacher_id": teacher["user_id"],
        },
        status=201,
    )
    runtime = create_engine(stack["settings"].sync_database_url)
    try:
        with runtime.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.school_id', :school, true)"),
                {"school": c["other_school"]},
            )
            for table in (
                "member_profiles",
                "teacher_profiles",
                "academic_years",
                "class_offerings",
            ):
                assert (
                    conn.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE school_id=:school"),
                        {"school": c["school_id"]},
                    )
                    == 0
                )
                assert (
                    conn.scalar(
                        text("SELECT relforcerowsecurity FROM pg_class WHERE relname=:table"),
                        {"table": table},
                    )
                    is True
                )
        with pytest.raises(DBAPIError), stack["engine"].begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO class_offerings(id,school_id,period_id,class_id) VALUES (:id,:school,:period,:class)"
                ),
                {
                    "id": uuid4(),
                    "school": c["other_school"],
                    "period": c["period"]["id"],
                    "class": c["classes"][0]["id"],
                },
            )
        with pytest.raises(DBAPIError), runtime.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.school_id', :school, true)"),
                {"school": c["other_school"]},
            )
            conn.execute(
                text(
                    "INSERT INTO member_profiles(id,school_id,user_id,full_name) VALUES (:id,:school,:user,'Forbidden')"
                ),
                {"id": uuid4(), "school": c["school_id"], "user": c["ids"]["student"]},
            )
    finally:
        runtime.dispose()
    with Session(stack["engine"]) as db:
        assert (
            db.scalar(
                select(MemberProfile.full_name).where(
                    MemberProfile.user_id == UUID(teacher["user_id"])
                )
            )
            == "New member"
        )


@pytest.mark.parametrize("level,grade,invalid", [("SMP", 7, 10), ("SMA", 10, 13)])
def test_update_preserves_school_level_rules(stack, client, admin_headers, level, grade, invalid):
    headers = {**admin_headers, "X-School-ID": stack["schools"][level]}
    created = client.post(
        "/api/v1/classes", headers=headers, json={"name": uuid4().hex, "grade_level": grade}
    )
    assert created.status_code == 201, created.text
    classroom = created.json()
    for data in ({"grade_level": invalid}, {"grade_level": grade, "program_id": str(uuid4())}):
        response = client.put(
            f"/api/v1/classes/{classroom['id']}",
            headers=headers,
            json={"name": classroom["name"], "expected_revision": 1, **data},
        )
        assert response.status_code == 422, response.text
