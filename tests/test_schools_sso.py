import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from studyhub.models import Membership, User
from tests.conftest import login

pytestmark = pytest.mark.integration


def scoped(headers, stack, level):
    return {**headers, "X-School-ID": stack["schools"][level]}


def test_subjects_cache_jobs_and_roles_are_school_scoped(stack, client, admin_headers):
    a, b = scoped(admin_headers, stack, "SMP"), scoped(admin_headers, stack, "SMA")
    initial_b = client.get("/api/v1/subjects/statistics", headers=b).json()["subject_count"]
    created = client.post(
        "/api/v1/subjects", headers=a, json={"code": "SAME_CODE", "name": "School A"}
    )
    assert created.status_code == 201, created.text
    subject_id = created.json()["id"]
    assert client.get(f"/api/v1/subjects/{subject_id}", headers=b).status_code == 404
    assert (
        client.put(
            f"/api/v1/subjects/{subject_id}", headers=b, json={"code": "HACK", "name": "Not mine"}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/subjects/{subject_id}", headers=b).status_code == 404
    assert (
        client.post(
            "/api/v1/subjects", headers=b, json={"code": "SAME_CODE", "name": "School B"}
        ).status_code
        == 201
    )

    assert (
        client.get("/api/v1/subjects/statistics", headers=b).json()["subject_count"]
        == initial_b + 1
    )
    key = str(uuid4())
    job_a = client.post("/api/v1/jobs/subject-report", headers={**a, "Idempotency-Key": key})
    job_b = client.post("/api/v1/jobs/subject-report", headers={**b, "Idempotency-Key": key})
    assert job_a.status_code == job_b.status_code == 202
    assert job_a.json()["id"] != job_b.json()["id"]
    assert client.get("/api/v1/jobs/" + job_a.json()["id"], headers=b).status_code == 404
    # Worker must count only the selected school even when the same owner belongs to both.
    for job, headers in ((job_a, a), (job_b, b)):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = client.get("/api/v1/jobs/" + job.json()["id"], headers=headers).json()
            if result["status"] == "completed":
                expected = client.get("/api/v1/subjects/statistics", headers=headers).json()
                assert result["result"] == expected
                break
            time.sleep(0.1)
        else:
            pytest.fail("School-scoped job did not complete")
    with Session(stack["engine"]) as db:
        teacher = db.scalar(select(User).where(User.full_name == "teacher"))
        membership = db.get(Membership, (stack["schools"]["SMP"], teacher.id))
        membership.role = "admin"
        db.commit()
    teacher_headers = {"Authorization": "Bearer " + login(client, "teacher")["access_token"]}
    members = client.get("/api/v1/users", headers=scoped(teacher_headers, stack, "SMP")).json()
    assert next(member for member in members if member["full_name"] == "teacher")["role"] == "admin"
    assert (
        client.post(
            "/api/v1/subjects",
            headers=scoped(teacher_headers, stack, "SMK"),
            json={"code": "TEACHER", "name": "Denied"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/subjects",
            headers=scoped(teacher_headers, stack, "SMP"),
            json={"code": "TEACHER", "name": "Allowed"},
        ).status_code
        == 201
    )


def test_level_specific_programs_and_class_constraints(stack, client, admin_headers):
    for level in ("SMP", "SMA"):
        headers = scoped(admin_headers, stack, level)
        assert (
            client.get("/api/v1/schools/current", headers=headers).json()["capabilities"][
                "expertise_programs"
            ]
            is False
        )
        assert (
            client.post(
                "/api/v1/expertise-programs",
                headers=headers,
                json={"code": "RPL", "name": "Software"},
            ).status_code
            == 422
        )
    smk = scoped(admin_headers, stack, "SMK")
    program = client.post(
        "/api/v1/expertise-programs", headers=smk, json={"code": "RPL", "name": "Software"}
    )
    assert program.status_code == 201, program.text
    program_id = program.json()["id"]
    assert (
        client.post(
            "/api/v1/classes",
            headers=smk,
            json={"name": "X RPL", "grade_level": 10, "program_id": program_id},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/classes", headers=smk, json={"name": "XIII RPL", "grade_level": 13}
        ).status_code
        == 201
    )
    smp = scoped(admin_headers, stack, "SMP")
    assert (
        client.post(
            "/api/v1/classes", headers=smp, json={"name": "VII A", "grade_level": 7}
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/classes", headers=smp, json={"name": "Invalid", "grade_level": 10}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/classes",
            headers=smp,
            json={"name": "Invalid", "grade_level": 7, "program_id": program_id},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/classes",
            headers=scoped(admin_headers, stack, "SMA"),
            json={"name": "Invalid", "grade_level": 13},
        ).status_code
        == 422
    )
    # Bypass HTTP validation using a superuser connection: database constraints must still reject it.
    with pytest.raises(DBAPIError), stack["engine"].begin() as conn:
        conn.execute(
            text(
                "INSERT INTO expertise_programs(id,school_id,school_level,code,name) VALUES (:id,:school,'SMK','INVALID','Invalid')"
            ),
            {"id": uuid4(), "school": stack["schools"]["SMP"]},
        )
    with pytest.raises(DBAPIError), stack["engine"].begin() as conn:
        conn.execute(
            text(
                "INSERT INTO school_classes(id,school_id,school_level,name,grade_level,program_id) VALUES (:id,:school,'SMP','Invalid',7,:program)"
            ),
            {"id": uuid4(), "school": stack["schools"]["SMP"], "program": program_id},
        )


def test_rls_denies_unscoped_queries_and_cross_school_writes(stack):
    engine = create_engine(stack["settings"].sync_database_url)
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM subjects")).scalar() == 0
            conn.execute(
                text("SELECT set_config('app.school_id', :school, true)"),
                {"school": stack["schools"]["SMP"]},
            )
            assert set(conn.execute(text("SELECT school_id FROM subjects")).scalars()) == {
                UUID(stack["schools"]["SMP"])
            }
        # A new transaction on a pooled connection must not inherit the previous tenant.
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM subjects")).scalar() == 0
        with pytest.raises(DBAPIError), engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.school_id', :school, true)"),
                {"school": stack["schools"]["SMP"]},
            )
            conn.execute(
                text(
                    "INSERT INTO subjects(id,school_id,code,name) VALUES (:id,:school,'LEAK','Leak')"
                ),
                {"id": uuid4(), "school": stack["schools"]["SMA"]},
            )
    finally:
        engine.dispose()


def test_platform_admin_cannot_bypass_school_membership(stack, client, admin_headers):
    school = client.post(
        "/api/v1/schools",
        headers=admin_headers,
        json={"code": "ISOLATED", "name": "Isolated", "level": "SMP"},
    )
    assert school.status_code == 201, school.text
    school_id = school.json()["id"]
    with Session(stack["engine"]) as db:
        user = db.scalar(select(User).where(User.is_platform_admin.is_(True)))
        db.delete(db.get(Membership, (school_id, user.id)))
        db.commit()
    assert (
        client.get(
            "/api/v1/subjects", headers={**admin_headers, "X-School-ID": school_id}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/subjects", headers={**admin_headers, "X-School-ID": str(uuid4())}
        ).status_code
        == 403
    )


def begin_sso(client, mode=None):
    response = client.get("/api/v1/auth/sso/start")
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    if mode:
        parsed = urlsplit(location)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        location = urlunsplit(parsed._replace(query=urlencode({**query, "mode": mode})))
    authorized = client.get(location)
    assert authorized.status_code == 302
    return authorized.headers["location"]


def test_oidc_code_pkce_login_and_state_replay(client, stack):
    callback = begin_sso(client)
    response = client.get(callback)
    assert response.status_code == 200, response.text
    assert "provider-token" not in response.text
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert client.get("/api/v1/users/me", headers=headers).json()["email"] == "admin@example.org"
    assert client.get(callback).status_code == 401
    for log in stack["logs"].glob("api-*.log"):
        assert parse_qs(urlsplit(callback).query)["code"][0] not in log.read_text()


@pytest.mark.parametrize("mode", ["nonce", "audience", "issuer", "unlinked", "expired", "unsigned"])
def test_oidc_rejects_invalid_claims_and_email_auto_link(client, mode):
    assert client.get(begin_sso(client, mode)).status_code == 401


def test_oidc_binds_state_to_browser(client):
    callback = begin_sso(client)
    client.cookies.clear()
    assert client.get(callback).status_code == 401
