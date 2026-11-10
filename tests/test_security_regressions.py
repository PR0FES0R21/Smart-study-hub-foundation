import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from studyhub.core.security import password_hasher
from studyhub.infrastructure.cache import Cache
from studyhub.infrastructure.database import create_database
from studyhub.infrastructure.events import EventHub
from studyhub.models import Job, Membership, Outbox, School, User


def test_sso_only_platform_admin_rejects_local_password_login(stack, client):
    with Session(stack["engine"]) as db, db.begin():
        user = User(
            email="audit-sso@example.org",
            full_name="Audit SSO",
            password_hash=None,
            is_platform_admin=True,
        )
        db.add(user)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "audit-sso@example.org", "password": "not-a-real-user-password"},
    )
    assert response.status_code == 401


def test_school_admin_cannot_attach_unrelated_account(stack, client):
    with Session(stack["engine"]) as db, db.begin():
        admin = User(
            email="audit-tenant-admin@example.org",
            full_name="Tenant admin",
            password_hash=password_hasher.hash("audit-strong-password"),
            is_platform_admin=False,
        )
        victim = User(
            email="audit-other-school@example.org",
            full_name="Other school user",
            password_hash=None,
        )
        db.add_all([admin, victim])
        db.flush()
        db.add_all(
            [
                Membership(user_id=admin.id, school_id=UUID(stack["schools"]["SMP"]), role="admin"),
                Membership(
                    user_id=victim.id, school_id=UUID(stack["schools"]["SMA"]), role="student"
                ),
            ]
        )
        victim_id = str(victim.id)
    token = client.post(
        "/api/v1/auth/login",
        json={"email": "audit-tenant-admin@example.org", "password": "audit-strong-password"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "X-School-ID": stack["schools"]["SMP"]}
    linked = client.post(
        "/api/v1/schools/current/memberships",
        headers=headers,
        json={"user_id": victim_id, "role": "student"},
    )
    assert linked.status_code == 403
    users = client.get("/api/v1/users", headers=headers).json()
    assert all(row["id"] != victim_id for row in users)


async def test_evicted_generation_cannot_reuse_stale_cache(stack):
    redis = Redis.from_url(stack["settings"].cache_url.get_secret_value(), decode_responses=True)
    namespace = stack["settings"].namespace + ":audit"
    school = uuid4()
    cache = Cache(redis, namespace)
    generation_key = f"{cache.prefix}:school:{school}:subjects:generation"
    try:
        await cache.invalidate_subjects(school)
        assert await cache.subject_statistics(
            AsyncMock(return_value={"subject_count": 1}), school_id=school
        ) == {"subject_count": 1}
        await cache.invalidate_subjects(school)
        await redis.delete(generation_key)
        await cache.invalidate_subjects(school)
        compute = AsyncMock(return_value={"subject_count": 3})
        result = await cache.subject_statistics(compute, school_id=school)
        assert result == {"subject_count": 3}
        compute.assert_awaited_once()
    finally:
        keys = [key async for key in redis.scan_iter(match=f"{namespace}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def test_binary_websocket_frame_closes_with_1003(monkeypatch):
    from contextlib import asynccontextmanager

    from starlette.websockets import WebSocket

    import studyhub.api.websocket as endpoint

    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr(endpoint, "load_principal", AsyncMock(return_value=object()))
    monkeypatch.setattr(endpoint, "bind_school", AsyncMock())
    hub = EventHub(AsyncMock(), "audit", AsyncMock())
    state = SimpleNamespace(
        settings=SimpleNamespace(allowed_origins=[], namespace="audit"),
        hub=hub,
        sessions=session,
        control=SimpleNamespace(
            getdel=AsyncMock(
                return_value=json.dumps(
                    {"user_id": str(uuid4()), "session_id": str(uuid4()), "school_id": str(uuid4())}
                )
            )
        ),
    )
    messages = iter([{"type": "websocket.connect"}, {"type": "websocket.receive", "bytes": b"x"}])

    async def receive():
        return next(messages)

    send = AsyncMock()
    ws = WebSocket(
        {
            "type": "websocket",
            "headers": [],
            "query_string": b"ticket=" + b"x" * 48,
            "app": SimpleNamespace(state=state),
        },
        receive,
        send,
    )
    await endpoint.websocket_endpoint(ws)
    assert any(call.args[0].get("code") == 1003 for call in send.await_args_list)
    assert not hub.connections


def test_event_delivery_visits_only_recipient_connections():
    class CountingDict(dict):
        visited = 0

        def values(self):
            for value in super().values():
                self.visited += 1
                yield value

    hub = EventHub(AsyncMock(), "audit", AsyncMock())
    hub.connections = CountingDict()
    for i in range(2500):
        hub.connect(str(i), "school")
    hub.connections.visited = 0
    hub.deliver({"user_id": "0", "school_id": "school", "version": 1})
    assert hub.connections.visited == 0
    assert sum(c.queue.qsize() for c in dict.values(hub.connections)) == 1


def test_backup_upload_manifest_does_not_break_restore(stack, tmp_path, monkeypatch):
    from tests.test_operations import test_real_encrypted_backup_and_postgres_restore

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "manifest.json").write_text('{"application":"ordinary-upload"}')
    monkeypatch.setenv("STUDYHUB_BACKUP_UPLOADS_DIR", str(uploads))
    test_real_encrypted_backup_and_postgres_restore(stack, tmp_path)


def test_failed_recovery_notification_is_retried(tmp_path, monkeypatch):
    from ops import watch

    monkeypatch.setattr(watch, "check", lambda: [])
    monkeypatch.setenv("STUDYHUB_WATCH_STATE_DIR", str(tmp_path))
    (tmp_path / "watch.json").write_text(
        json.dumps({"failures": ["api_unreachable"], "notified_at": 0})
    )
    hook = tmp_path / "hook"
    hook.write_text("https://notify.example.invalid/")
    monkeypatch.setenv("STUDYHUB_OPS_WEBHOOK_URL_FILE", str(hook))
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(True)
        raise OSError("temporary notification outage")

    monkeypatch.setattr(watch, "urlopen", unavailable)
    watch.main()
    watch.main()
    assert len(calls) == 2


def test_slow_websockets_remain_counted_until_closed():
    hub = EventHub(AsyncMock(), "audit", AsyncMock())
    connections = [hub.connect("user", "school") for _ in range(3)]
    for connection in connections:
        for _ in range(64):
            connection.queue.put_nowait({})
    hub.deliver({"user_id": "user", "school_id": "school", "version": 1})
    with pytest.raises(ValueError):
        hub.connect("user", "school")
    assert all(c.queue.get_nowait() is None for c in connections)
    for c in connections:
        hub.disconnect(c)
    assert not hub.connections and (not hub.by_target) and (not hub.user_counts)
    hub.connect("user", "school")


def test_unique_job_keys_have_admission_limit(stack, client):
    with Session(stack["engine"]) as db, db.begin():
        user = User(
            email="audit-jobs@example.org",
            full_name="Audit Jobs",
            password_hash=password_hasher.hash("correct-password-123"),
        )
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, school_id=UUID(stack["schools"]["SMK"]), role="teacher"))
    token = client.post(
        "/api/v1/auth/login",
        json={"email": "audit-jobs@example.org", "password": "correct-password-123"},
    ).json()["access_token"]
    keys = [str(uuid4()) for _ in range(25)]
    responses = [
        client.post(
            "/api/v1/jobs/subject-report",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
        )
        for key in keys
    ]
    assert [response.status_code for response in responses] == [202] * 20 + [429] * 5
    retried = client.post(
        "/api/v1/jobs/subject-report",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": keys[0]},
    )
    assert retried.status_code == 202
    assert retried.json()["id"] == responses[0].json()["id"]


async def test_pending_job_capacity_is_atomic_across_connections(stack, monkeypatch):
    from studyhub.api import routes

    school_id, owner_id = uuid4(), uuid4()
    future = datetime.now(UTC) + timedelta(days=1)
    with Session(stack["engine"]) as db, db.begin():
        db.add(School(id=school_id, code=f"RACE_{uuid4().hex[:12]}", name="Race test", level="SMP"))
        db.add(
            User(
                id=owner_id,
                email=f"{owner_id}@example.org",
                full_name="Race test",
                password_hash=None,
            )
        )
        db.flush()
        db.add(Membership(school_id=school_id, user_id=owner_id, role="teacher"))
        db.flush()
        db.add_all(
            [
                Job(
                    school_id=school_id,
                    owner_id=owner_id,
                    idempotency_key=str(uuid4()),
                    created_at=future,
                )
                for _ in range(99)
            ]
        )

    def deferred_event(**kwargs):
        # Keep workers from draining the accepted job during the admission race.
        return Outbox(available_at=future, **kwargs)

    monkeypatch.setattr(routes, "Outbox", deferred_event)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=stack["settings"],
                control=SimpleNamespace(eval=AsyncMock(return_value=1)),
            )
        )
    )
    principal = SimpleNamespace(school_id=school_id, user=SimpleNamespace(id=owner_id))
    engine, sessions = create_database(stack["settings"])

    async def submit():
        async with sessions(info={"school_id": str(school_id)}) as db:
            try:
                await routes.report_job(
                    request=request, idempotency_key=str(uuid4()), principal=principal, db=db
                )
                return 202
            except HTTPException as exc:
                return exc.status_code

    try:
        assert sorted(await asyncio.gather(*(submit() for _ in range(4)))) == [202, 429, 429, 429]
        with Session(stack["engine"]) as db:
            assert (
                db.scalar(select(func.count()).select_from(Job).where(Job.school_id == school_id))
                == 100
            )
            assert (
                db.scalar(
                    select(func.count()).select_from(Outbox).where(Outbox.school_id == school_id)
                )
                == 1
            )
    finally:
        await engine.dispose()
        with Session(stack["engine"]) as db, db.begin():
            for model in (Outbox, Job, Membership):
                db.execute(delete(model).where(model.school_id == school_id))
            db.execute(delete(School).where(School.id == school_id))
            db.execute(delete(User).where(User.id == owner_id))


async def test_upstream_json_response_has_size_budget():
    import httpx

    from studyhub.infrastructure.integrations import IntegrationClient

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"data":"'
            for _ in range(300):
                yield (b"x" * 1024)
            yield b'"}'

    async def respond(request):
        return httpx.Response(200, stream=Body())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://upstream.example.invalid/"
    ) as http:
        with pytest.raises(httpx.HTTPError, match="size limit"):
            await IntegrationClient(http).get_capabilities()
