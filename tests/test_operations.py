import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from celery import Celery
from prometheus_client import generate_latest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from ops.backup.backup import Backup, OperationError, database_env
from studyhub.core.logging import JsonFormatter, RedactCredentials
from studyhub.main import create_app
from studyhub.models import AuthSession, Outbox, RefreshToken, User
from tests.test_unit import settings


def test_logs_remove_credentials_and_raw_exception_parameters():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactCredentials())
    handler.setFormatter(JsonFormatter())
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        "",
        1,
        'Bearer abc.secret.token password="do-not-show" postgresql://user:private@db/studyhub ?code=oauthsecret',
        (),
        None,
    )
    handler.handle(record)
    value = stream.getvalue()
    assert all(
        secret not in value
        for secret in ("abc.secret.token", "do-not-show", "private", "oauthsecret")
    )
    try:
        raise ValueError("secret SQL parameters")
    except ValueError:
        record = logging.LogRecord(
            "test", logging.ERROR, "", 1, "SQL secret SQL parameters", (), sys.exc_info()
        )
        handler.handle(record)
    assert "secret SQL parameters" not in stream.getvalue()
    assert "ValueError" in stream.getvalue()


async def test_unhandled_errors_are_generic_and_request_id_is_present():
    app = create_app(settings())

    @app.get("/unexpected")
    async def unexpected():
        raise ValueError("private-database-value")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/unexpected")
    assert response.status_code == 500
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "private-database-value" not in response.text
    assert b'status="500"' in generate_latest(app.state.metrics.registry)


def test_backup_guard_uses_separate_empty_database(monkeypatch, tmp_path):
    secret = tmp_path / "database"
    secret.write_text("postgresql://backup:password@localhost/app?sslmode=disable")
    monkeypatch.setenv("STUDYHUB_BACKUP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("STUDYHUB_BACKUP_DATABASE_URL_FILE", str(secret))
    backup = Backup()
    with pytest.raises(OperationError, match="separate_database"):
        backup.verify("latest", str(secret))
    wrong = tmp_path / "wrong"
    wrong.write_text("postgresql://backup:password@localhost/production?sslmode=disable")
    with pytest.raises(OperationError, match="separate_database"):
        backup.verify("latest", str(wrong))
    env = database_env(secret)
    assert env["PGDATABASE"] == "app" and env["PGPASSWORD"] == "password"


def test_backup_process_lock_prevents_overlapping_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDYHUB_BACKUP_STATE_DIR", str(tmp_path / "state"))
    first, second = Backup(), Backup()
    with first.lock(), pytest.raises(OperationError, match="another_backup"):
        with second.lock():
            pass


def test_watch_detects_stale_backup_and_api_failure(monkeypatch, tmp_path):
    from ops import watch

    monkeypatch.setenv("STUDYHUB_WATCH_BACKUP_STATUS", str(tmp_path / "status.json"))
    monkeypatch.setenv("STUDYHUB_WATCH_MIN_FREE_FRACTION", "0")

    def unavailable(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(watch, "urlopen", unavailable)
    assert set(watch.check()) == {"api_unreachable", "backup_status_missing"}
    (tmp_path / "status.json").write_text(
        json.dumps({"last_backup_success_at": "2000-01-01T00:00:00+00:00"})
    )
    assert "backup_stale" in watch.check()


@pytest.mark.integration
def test_private_metrics_and_audit_correlation(stack, client):
    assert client.get("/internal/metrics").status_code == 401
    assert (
        client.get("/internal/metrics", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.org", "password": "correct-password-123"},
    )
    assert login.status_code == 200
    request_id = login.headers["X-Request-ID"]
    result = client.get(
        "/internal/metrics",
        headers={"Authorization": "Bearer test-metrics-token-012345678901234567890123456789"},
    )
    assert result.status_code == 200
    assert "studyhub_http_requests_total" in result.text
    assert "studyhub_outbox_pending" in result.text
    assert all(school_id not in result.text for school_id in stack["schools"].values())
    deadline = time.monotonic() + 10
    while True:
        heartbeat = float(
            next(
                line.split()[-1]
                for line in result.text.splitlines()
                if line.startswith("studyhub_dispatcher_last_success_timestamp_seconds ")
            )
        )
        if heartbeat > time.time() - 30:
            break
        assert time.monotonic() < deadline, "No successful beat/broker/worker dispatcher heartbeat"
        time.sleep(0.1)
        result = client.get(
            "/internal/metrics",
            headers={"Authorization": "Bearer test-metrics-token-012345678901234567890123456789"},
        )
    records = [
        json.loads(line)
        for line in (stack["logs"] / "api-0.log").read_text().splitlines()
        if line.startswith("{")
    ]
    assert any(
        row.get("action") == "session_created" and row.get("request_id") == request_id
        for row in records
    )
    assert login.json()["access_token"] not in (stack["logs"] / "api-0.log").read_text()


@pytest.mark.integration
def test_cleanup_preserves_pending_events_and_unexpired_replay_records(stack):
    now = datetime.now(UTC)
    old_id, live_id, delivered_id, pending_id = [uuid4() for _ in range(4)]
    digest = secrets.token_hex(32)
    with Session(stack["engine"]) as db, db.begin():
        user = db.scalar(select(User).where(User.email == "admin@example.org"))
        db.add_all(
            [
                AuthSession(id=old_id, user_id=user.id, expires_at=now - timedelta(days=31)),
                AuthSession(id=live_id, user_id=user.id, expires_at=now + timedelta(days=1)),
            ]
        )
        db.flush()
        db.add_all(
            [
                RefreshToken(digest=digest, session_id=live_id, used_at=now),
                Outbox(
                    id=delivered_id,
                    school_id=UUID(stack["schools"]["SMK"]),
                    kind="test",
                    payload={},
                    delivered_at=now - timedelta(days=15),
                ),
                Outbox(
                    id=pending_id,
                    school_id=UUID(stack["schools"]["SMK"]),
                    kind="test",
                    payload={},
                    created_at=now - timedelta(days=60),
                    available_at=now + timedelta(days=1),
                ),
            ]
        )
    sender = Celery(broker=stack["settings"].broker_url.get_secret_value())
    sender.send_task("studyhub.cleanup_operations", queue=f"{stack['settings'].namespace}:tasks")
    sender.close()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with Session(stack["engine"]) as db:
            if db.get(AuthSession, old_id) is None and db.get(Outbox, delivered_id) is None:
                assert db.get(AuthSession, live_id) is not None
                assert db.get(RefreshToken, digest) is not None
                assert db.get(Outbox, pending_id) is not None
                break
        time.sleep(0.1)
    else:
        pytest.fail("Operational cleanup was not processed by Celery")
    with stack["engine"].begin() as conn:
        conn.execute(text("DELETE FROM auth_sessions WHERE id=:id"), {"id": live_id})
        conn.execute(text("DELETE FROM outbox WHERE id=:id"), {"id": pending_id})


@pytest.mark.integration
def test_real_encrypted_backup_and_postgres_restore(stack, tmp_path):
    restic = os.environ.get("RESTIC_BIN") or shutil.which("restic")
    if not restic:
        pytest.fail("Restic is required for the integration backup/restore proof")
    container = os.environ.get(
        "STUDYHUB_TEST_POSTGRES_CONTAINER", "smart-study-foundation-postgres-1"
    )
    wrappers = tmp_path / "bin"
    wrappers.mkdir()
    env = dict(os.environ)
    # Reuse PG16 tools from the isolated PostgreSQL container; passwords never enter argv.
    for name in ("psql", "pg_dump", "pg_restore"):
        path = wrappers / name
        path.write_text(
            f"""#!{sys.executable}
import os,subprocess,sys
from pathlib import Path
env=dict(os.environ)
for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
    env.pop(key,None)
cmd=['docker','--host=unix:///var/run/docker.sock','exec','-i','-e','PGDATABASE','-e','PGUSER',{container!r},{name!r}]
args=sys.argv[1:]
if {name!r} == 'pg_dump' and '--file' in args:
    index=args.index('--file')
    destination=args[index+1]
    del args[index:index+2]
    with open(destination,'wb') as handle:
        result=subprocess.run(cmd+args,stdout=handle,env=env)
elif {name!r} == 'pg_restore' and args and Path(args[-1]).is_file():
    with open(args[-1],'rb') as handle:
        result=subprocess.run(cmd+args[:-1],stdin=handle,env=env)
else:
    result=subprocess.run(cmd+args,env=env)
sys.exit(result.returncode)
"""
        )
        path.chmod(0o700)
        env[
            {"psql": "PSQL_BIN", "pg_dump": "PG_DUMP_BIN", "pg_restore": "PG_RESTORE_BIN"}[name]
        ] = str(path)
    source_url = make_url(stack["engine"].url)
    database = source_url.database + "_restore_test"
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_text(
        source_url.set(drivername="postgresql").render_as_string(hide_password=False)
        + "?sslmode=disable"
    )
    target.write_text(
        source_url.set(drivername="postgresql", database=database).render_as_string(
            hide_password=False
        )
        + "?sslmode=disable"
    )
    repository, password = tmp_path / "repository", tmp_path / "password"
    repository.write_text(str(tmp_path / "encrypted-repository"))
    password.write_text(secrets.token_urlsafe(32))
    env.update(
        RESTIC_BIN=restic,
        RESTIC_REPOSITORY_FILE=str(repository),
        RESTIC_PASSWORD_FILE=str(password),
        STUDYHUB_BACKUP_STATE_DIR=str(tmp_path / "state"),
        STUDYHUB_BACKUP_DATABASE_URL_FILE=str(source),
        STUDYHUB_BACKUP_TAG="integration",
        STUDYHUB_BACKUP_HOST="test-host",
    )
    command = [sys.executable, "ops/backup/backup.py"]
    with stack["engine"].connect() as conn:
        expected_memberships = conn.execute(
            text("SELECT school_id, user_id, role FROM memberships ORDER BY school_id, user_id")
        ).all()
    for operation in ("init", "backup", "maintenance"):
        result = subprocess.run(command + [operation], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout
        assert source_url.password not in result.stdout + result.stderr
    # An unencrypted PG dump must not remain on disk after backup.
    assert not (tmp_path / "state" / "staging").exists()
    with stack["engine"].connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'CREATE DATABASE "{database}"'))
    try:
        result = subprocess.run(
            command + ["verify", "--target-secret", str(target)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout
        from sqlalchemy import create_engine

        restored = create_engine(source_url.set(database=database))
        try:
            with restored.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM schools")).scalar() == 3
                assert (
                    conn.execute(
                        text(
                            "SELECT school_id, user_id, role FROM memberships ORDER BY school_id, user_id"
                        )
                    ).all()
                    == expected_memberships
                )
        finally:
            restored.dispose()
        # A second restore into the populated target must refuse to overwrite it.
        result = subprocess.run(
            command + ["verify", "--target-secret", str(target)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        status = json.loads((tmp_path / "state" / "status.json").read_text())
        assert status["last_restore_verified_at"] and status["last_backup_success_at"]
    finally:
        with stack["engine"].connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
