import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from studyhub.core.config import Settings

pytestmark = pytest.mark.integration


def test_single_school_backfill_and_lossy_downgrade_guard():
    if os.getenv("STUDYHUB_RUN_INTEGRATION") != "1":
        pytest.skip("Integration infrastructure required")
    base = make_url(Settings().sync_migration_url)
    name = f"studyhub_migration_{uuid4().hex[:8]}_test"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = create_engine(base.set(database=name))
    env = {
        **os.environ,
        "STUDYHUB_DATABASE_URL": base.set(
            database=name, drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False),
        "STUDYHUB_MIGRATION_DATABASE_URL": base.set(
            database=name, drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False),
    }

    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=env, capture_output=True, check=check
        )

    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        migrate("upgrade", "2601891cc10a")
        user_id, subject_id, job_id, event_id = [uuid4() for _ in range(4)]
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users(id,email,full_name,password_hash,role,is_active) VALUES (:id,'legacy@example.org','Legacy','test-hash','admin',true)"
                ),
                {"id": user_id},
            )
            conn.execute(
                text("INSERT INTO subjects(id,code,name) VALUES (:id,'OLD','Legacy subject')"),
                {"id": subject_id},
            )
            conn.execute(
                text(
                    "INSERT INTO jobs(id,owner_id,idempotency_key,status) VALUES (:id,:owner,'legacy-job-key','pending')"
                ),
                {"id": job_id, "owner": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO outbox(id,kind,payload,attempts) VALUES (:id,'job.requested',CAST(:payload AS jsonb),0)"
                ),
                {"id": event_id, "payload": json.dumps({"job_id": str(job_id)})},
            )
        migrate("upgrade", "head")
        with engine.connect() as conn:
            school_id = conn.execute(
                text("SELECT id FROM schools WHERE code='LEGACY'")
            ).scalar_one()
            assert (
                conn.execute(
                    text("SELECT school_id FROM subjects WHERE id=:id"), {"id": subject_id}
                ).scalar_one()
                == school_id
            )
            assert (
                conn.execute(
                    text("SELECT school_id FROM jobs WHERE id=:id"), {"id": job_id}
                ).scalar_one()
                == school_id
            )
            assert (
                conn.execute(
                    text("SELECT school_id FROM outbox WHERE id=:id"), {"id": event_id}
                ).scalar_one()
                == school_id
            )
            assert (
                conn.execute(
                    text("SELECT is_platform_admin FROM users WHERE id=:id"), {"id": user_id}
                ).scalar_one()
                is True
            )
            assert (
                conn.execute(
                    text("SELECT role FROM memberships WHERE user_id=:id"), {"id": user_id}
                ).scalar_one()
                == "admin"
            )
        migrate("downgrade", "2601891cc10a")
        migrate("upgrade", "head")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO schools(id,code,name,level) VALUES (:id,'SECOND','Second','SMP')"
                ),
                {"id": uuid4()},
            )
        assert migrate("downgrade", "2601891cc10a", check=False).returncode != 0
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM schools")).scalar_one() == 2
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
