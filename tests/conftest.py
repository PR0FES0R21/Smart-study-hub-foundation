import os
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from studyhub.core.config import Settings
from studyhub.core.security import password_hasher
from studyhub.models import ExternalIdentity, Membership, School, User


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    if os.getenv("STUDYHUB_RUN_INTEGRATION") != "1":
        pytest.skip("Set STUDYHUB_RUN_INTEGRATION=1; requires local compose infrastructure")
    base = Settings()
    name = f"studyhub_{uuid4().hex[:10]}_test"
    url = make_url(base.sync_migration_url).set(drivername="postgresql+asyncpg").set(database=name)
    config = base.model_copy(
        update={
            "database_url": SecretStr(url.render_as_string(hide_password=False)),
            "environment": "test",
            "migration_database_url": None,
            "namespace": f"studyhub:test:{uuid4().hex}",
        }
    )
    admin_engine = create_engine(
        make_url(base.sync_migration_url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    runtime_role = f"{name}_app"
    runtime_password = secrets.token_hex(24)
    with admin_engine.connect() as conn:
        conn.execute(text(f"CREATE ROLE {runtime_role} LOGIN PASSWORD '{runtime_password}'"))
        conn.execute(text(f'CREATE DATABASE "{name}"'))  # Generated identifier, never user input.
    env = {
        **os.environ,
        "STUDYHUB_DATABASE_URL": config.database_url.get_secret_value(),
        "STUDYHUB_MIGRATION_DATABASE_URL": config.database_url.get_secret_value(),
        "STUDYHUB_NAMESPACE": config.namespace,
        "STUDYHUB_ENVIRONMENT": "test",
        "STUDYHUB_METRICS_TOKEN": "test-metrics-token-012345678901234567890123456789",
    }
    processes, handles = [], []
    log_dir = tmp_path_factory.mktemp("process-logs")
    oidc_port = free_port()
    api_ports = [free_port(), free_port()]
    oidc_url = f"http://127.0.0.1:{oidc_port}"
    env.update(
        {
            "STUDYHUB_OIDC_ISSUER": oidc_url,
            "STUDYHUB_OIDC_CLIENT_ID": "studyhub-test",
            "STUDYHUB_OIDC_REDIRECT_URI": f"http://127.0.0.1:{api_ports[0]}/api/v1/auth/sso/callback",
            "TEST_OIDC_ISSUER": oidc_url,
        }
    )
    engine = create_engine(config.sync_database_url)
    school_ids = {}
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            check=True,
            capture_output=True,
        )
        for args in (("check",), ("downgrade", "base"), ("upgrade", "head"), ("check",)):
            subprocess.run(
                [sys.executable, "-m", "alembic", *args], env=env, check=True, capture_output=True
            )
        with Session(engine) as db:
            for role in ("admin", "teacher", "student"):
                db.add(
                    User(
                        email=f"{role}@example.org",
                        full_name=role,
                        is_platform_admin=role == "admin",
                        password_hash=password_hasher.hash("correct-password-123"),
                    )
                )
            db.flush()
            users = {u.full_name: u for u in db.query(User).all()}
            db.add(
                ExternalIdentity(
                    issuer=oidc_url, subject="admin-subject", user_id=users["admin"].id
                )
            )
            for level in ("SMP", "SMA", "SMK"):
                school = School(code=f"TEST_{level}", name=f"Test {level}", level=level)
                db.add(school)
                db.flush()
                school_ids[level] = str(school.id)
                for role, user in users.items():
                    db.add(Membership(school_id=school.id, user_id=user.id, role=role))
            db.commit()
        with engine.begin() as conn:
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {runtime_role}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {runtime_role}"
                )
            )
        runtime_url = make_url(config.database_url.get_secret_value()).set(
            username=runtime_role, password=runtime_password
        )
        config = config.model_copy(
            update={"database_url": SecretStr(runtime_url.render_as_string(hide_password=False))}
        )
        env["STUDYHUB_DATABASE_URL"] = config.database_url.get_secret_value()
        env.pop("STUDYHUB_MIGRATION_DATABASE_URL", None)
        urls = []
        handle = (log_dir / "oidc.log").open("w")
        handles.append(handle)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "tests.fake_oidc:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(oidc_port),
                    "--no-access-log",
                ],
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
        )
        for index in range(2):
            port = api_ports[index]
            handle = (log_dir / f"api-{index}.log").open("w")
            handles.append(handle)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "studyhub.main:create_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--no-access-log",
                        "--no-proxy-headers",
                    ],
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                )
            )
            urls.append(f"http://127.0.0.1:{port}")
        for component in ("worker", "beat"):
            handle = (log_dir / f"{component}.log").open("w")
            handles.append(handle)
            command = [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "studyhub.worker.celery_app:celery_app",
                component,
                "--loglevel=WARNING",
            ]
            command += (
                ["--concurrency=1"] if component == "worker" else [f"--schedule={log_dir / 'beat'}"]
            )
            processes.append(
                subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT)
            )
        with httpx.Client(timeout=5, trust_env=False) as client:
            deadline = time.monotonic() + 30
            for url in urls:
                while time.monotonic() < deadline:
                    try:
                        if client.get(f"{url}/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if any(p.poll() is not None for p in processes):
                        pytest.fail(f"Service exited; inspect {log_dir}")
                    time.sleep(0.1)
                else:
                    pytest.fail(f"Services not ready; inspect {log_dir}")
        yield {
            "urls": urls,
            "settings": config,
            "engine": engine,
            "env": env,
            "logs": log_dir,
            "schools": school_ids,
            "oidc_url": oidc_url,
            "role": runtime_role,
        }
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for handle in handles:
            handle.close()
        engine.dispose()
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            conn.execute(text(f'DROP ROLE "{runtime_role}"'))
        admin_engine.dispose()


@pytest.fixture
def client(stack):
    with httpx.Client(
        base_url=stack["urls"][0],
        headers={"X-School-ID": stack["schools"]["SMK"]},
        timeout=10,
        trust_env=False,
    ) as client:
        yield client


def login(client, role="admin"):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": f"{role}@example.org", "password": "correct-password-123"},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="session")
def admin_token(stack):
    with httpx.Client(base_url=stack["urls"][0], trust_env=False) as client:
        return login(client)["access_token"]


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}
