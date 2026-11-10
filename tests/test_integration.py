import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from celery import Celery
from sqlalchemy import select
from sqlalchemy.orm import Session
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from studyhub.models import Job, User
from tests.conftest import login

pytestmark = pytest.mark.integration


def test_auth_rotation_replay_and_logout(client):
    tokens = login(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/v1/users/me", headers=headers).json()["is_platform_admin"] is True
    renewed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert renewed.status_code == 200
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {renewed.json()['access_token']}"},
        ).status_code
        == 401
    )
    fresh = login(client)
    headers = {"Authorization": f"Bearer {fresh['access_token']}"}
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401


def test_subject_crud_unique_constraint_and_cache(stack, client, admin_headers):
    initial = client.get("/api/v1/subjects/statistics", headers=admin_headers).json()[
        "subject_count"
    ]
    data = {"code": "MATH", "name": "Mathematics"}
    response = client.post("/api/v1/subjects", headers=admin_headers, json=data)
    assert response.status_code == 201, response.text
    subject_id = response.json()["id"]
    assert client.post("/api/v1/subjects", headers=admin_headers, json=data).status_code == 409
    assert (
        client.get("/api/v1/subjects/statistics", headers=admin_headers).json()["subject_count"]
        == initial + 1
    )
    data["name"] = "Advanced Mathematics"
    assert (
        client.put(f"/api/v1/subjects/{subject_id}", headers=admin_headers, json=data).json()[
            "name"
        ]
        == data["name"]
    )
    assert client.get(f"/api/v1/subjects/{subject_id}", headers=admin_headers).status_code == 200
    assert client.delete(f"/api/v1/subjects/{subject_id}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/v1/subjects/{subject_id}", headers=admin_headers).status_code == 404
    audit = [
        json.loads(line)
        for line in (stack["logs"] / "api-0.log").read_text().splitlines()
        if line.startswith("{")
    ]
    assert any(
        row.get("action") == "subject_deleted"
        and row.get("resource_id") == subject_id
        and row.get("school_id") == stack["schools"]["SMK"]
        and row.get("actor_id")
        for row in audit
    )


def test_authorization_validation_and_password_redaction(client, admin_headers):
    student = login(client, "student")
    headers = {"Authorization": f"Bearer {student['access_token']}"}
    assert (
        client.post(
            "/api/v1/subjects", headers=headers, json={"code": "BIO", "name": "Biology"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/users", headers=headers).status_code == 403
    assert client.get("/api/v1/subjects").status_code == 401
    secret = "do-not-echo-this-password"
    bad = client.post("/api/v1/auth/login", json={"email": "bad", "password": secret})
    assert bad.status_code == 422 and secret not in bad.text
    assert (
        client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={
                "email": "user@example.org",
                "full_name": "User",
                "password": "long-enough-password",
                "role": "admin",
            },
        ).status_code
        == 422
    )
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "email": "user@example.org",
            "full_name": "User",
            "password": "long-enough-password",
            "role": "teacher",
        },
    )
    assert created.status_code == 201 and "password" not in created.text
    assert client.get("/api/v1/subjects?limit=100000", headers=admin_headers).status_code == 422


async def test_real_celery_cross_process_websocket_and_idempotency(stack, client, admin_headers):
    ticket = client.post("/api/v1/auth/ws-ticket", headers=admin_headers).json()["ticket"]
    ws_url = stack["urls"][1].replace("http://", "ws://") + f"/api/v1/ws?ticket={ticket}"
    async with connect(ws_url, origin="http://localhost:3000", proxy=None) as websocket:
        key = str(uuid4())
        headers = {**admin_headers, "Idempotency-Key": key}
        job = client.post("/api/v1/jobs/subject-report", headers=headers)
        assert job.status_code == 202, job.text
        job_id = job.json()["id"]
        assert client.post("/api/v1/jobs/subject-report", headers=headers).json()["id"] == job_id
        async with asyncio.timeout(25):
            while True:
                event = json.loads(await websocket.recv())
                if event["type"] == "job.completed" and event["data"]["job_id"] == job_id:
                    assert event["version"] == 1 and event["event_id"]
                    assert event["request_id"] == job.headers["X-Request-ID"]
                    break
        status = client.get(f"/api/v1/jobs/{job_id}", headers=admin_headers)
        assert status.json()["status"] == "completed"
        with Session(stack["engine"]) as db:
            assert len(db.scalars(select(Job).where(Job.id == job_id)).all()) == 1
    with pytest.raises(InvalidStatus):
        async with connect(ws_url, proxy=None):
            pytest.fail("Ticket was replayed")
    assert ticket not in (stack["logs"] / "api-1.log").read_text()
    assert job.headers["X-Request-ID"] in (stack["logs"] / "worker.log").read_text()
    other = login(client, "teacher")
    assert (
        client.get(
            f"/api/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {other['access_token']}"}
        ).status_code
        == 404
    )


def test_login_rate_limit_and_generic_credentials_error(client):
    for _ in range(8):
        response = client.post(
            "/api/v1/auth/login", json={"email": "missing@example.org", "password": "wrong"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password"
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "missing@example.org", "password": "wrong"}
        ).status_code
        == 429
    )


def test_body_limit_including_chunked_upload(client):
    assert client.post("/api/v1/auth/login", content=b"x" * 1_048_577).status_code == 413

    def chunks():
        yield b" " * 700_000
        yield b" " * 700_000

    assert client.post("/api/v1/auth/login", content=chunks()).status_code == 413


def test_reconciliation_repairs_job_without_broker_message(stack, client, admin_headers):
    with Session(stack["engine"]) as db:
        owner = db.scalar(select(User).where(User.is_platform_admin.is_(True)))
        job = Job(
            school_id=stack["schools"]["SMK"],
            owner_id=owner.id,
            idempotency_key=str(uuid4()),
            created_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        db.add(job)
        db.commit()
        job_id = str(job.id)
    sender = Celery("integration-sender", broker=stack["settings"].broker_url.get_secret_value())
    try:
        sender.send_task("studyhub.reconcile_jobs", queue=f"{stack['settings'].namespace}:tasks")
    finally:
        sender.close()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}", headers=admin_headers)
        if response.json()["status"] == "completed":
            assert isinstance(response.json()["result"]["subject_count"], int)
            break
        time.sleep(0.1)
    else:
        pytest.fail("Orphan job was not reconciled")
