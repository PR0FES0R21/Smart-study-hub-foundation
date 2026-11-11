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


def test_directory_backfills_existing_accounts_and_years_and_guards_downgrade():
    if os.getenv("STUDYHUB_RUN_INTEGRATION") != "1":
        pytest.skip("Integration infrastructure required")
    base = make_url(Settings().sync_migration_url)
    name = f"studyhub_directory_{uuid4().hex[:8]}_test"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = create_engine(base.set(database=name))
    url = base.set(database=name, drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    env = {**os.environ, "STUDYHUB_DATABASE_URL": url, "STUDYHUB_MIGRATION_DATABASE_URL": url}

    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=env, capture_output=True, check=check
        )

    ids = {key: uuid4() for key in ("school", "user", "period")}
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        migrate("upgrade", "a51d8e70bc92")
        with engine.begin() as conn:
            for statement in (
                "INSERT INTO schools(id,code,name,level) VALUES (:school,'OLD_DIRECTORY','Old directory','SMP')",
                "INSERT INTO users(id,email,full_name,is_active) VALUES (:user,'old-directory@example.org','Original teacher',true)",
                "INSERT INTO memberships(school_id,user_id,role,is_active) VALUES (:school,:user,'teacher',false)",
                "INSERT INTO academic_periods(id,school_id,year_start,semester,starts_on,ends_on) VALUES (:period,:school,2026,1,'2026-07-01','2026-12-31')",
            ):
                conn.execute(text(statement), ids)
        migrate("upgrade", "head")
        migrate("check")
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT full_name FROM member_profiles WHERE school_id=:school AND user_id=:user"
                    ),
                    ids,
                )
                == "Original teacher"
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM teacher_profiles WHERE school_id=:school AND user_id=:user"
                    ),
                    ids,
                )
                == 1
            )
            assert conn.execute(
                text(
                    "SELECT is_active,revision FROM memberships WHERE school_id=:school AND user_id=:user"
                ),
                ids,
            ).one() == (False, 1)
            assert (
                conn.scalar(
                    text("SELECT year_start FROM academic_years WHERE school_id=:school"), ids
                )
                == 2026
            )
        migrate("downgrade", "a51d8e70bc92")
        migrate("upgrade", "head")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE member_profiles SET phone_number='+628123456789' WHERE school_id=:school AND user_id=:user"
                ),
                ids,
            )
        failed = migrate("downgrade", "a51d8e70bc92", check=False)
        assert (
            failed.returncode != 0
            and b"Refusing lossy directory/catalog downgrade" in failed.stderr
        )
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "b60a9c12de73"
            assert (
                conn.scalar(
                    text(
                        "SELECT phone_number FROM member_profiles WHERE school_id=:school AND user_id=:user"
                    ),
                    ids,
                )
                == "+628123456789"
            )
            assert (
                conn.scalar(
                    text("SELECT relforcerowsecurity FROM pg_class WHERE relname='member_profiles'")
                )
                is True
            )
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


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


def test_security_upgrade_revokes_only_passwordless_sessions_and_downgrade_preserves_revocation():
    if os.getenv("STUDYHUB_RUN_INTEGRATION") != "1":
        pytest.skip("Integration infrastructure required")
    base = make_url(Settings().sync_migration_url)
    name = f"studyhub_security_{uuid4().hex[:8]}_test"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = create_engine(base.set(database=name))
    url = base.set(database=name, drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    env = {**os.environ, "STUDYHUB_DATABASE_URL": url, "STUDYHUB_MIGRATION_DATABASE_URL": url}

    def migrate(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=env, capture_output=True, check=True
        )

    session_ids = {}
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        migrate("upgrade", "b84c12f073a9")
        with engine.begin() as conn:
            for label, password_hash in (("sso", None), ("empty", ""), ("local", "test-hash")):
                user_id, session_id = uuid4(), uuid4()
                session_ids[label] = session_id
                conn.execute(
                    text(
                        "INSERT INTO users(id,email,full_name,password_hash,is_active) VALUES (:id,:email,:label,:hash,true)"
                    ),
                    {
                        "id": user_id,
                        "email": f"{label}@example.org",
                        "label": label,
                        "hash": password_hash,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO auth_sessions(id,user_id,expires_at) VALUES (:id,:user_id,now() + interval '1 day')"
                    ),
                    {"id": session_id, "user_id": user_id},
                )
        migrate("upgrade", "head")
        with engine.connect() as conn:
            state = dict(conn.execute(text("SELECT id,revoked_at FROM auth_sessions")).all())
            assert state[session_ids["sso"]] is not None
            assert state[session_ids["empty"]] is not None
            assert state[session_ids["local"]] is None
        migrate("downgrade", "b84c12f073a9")
        with engine.connect() as conn:
            assert (
                dict(conn.execute(text("SELECT id,revoked_at FROM auth_sessions")).all()) == state
            )
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_academic_upgrade_preserves_classes_and_refuses_lossy_downgrade():
    if os.getenv("STUDYHUB_RUN_INTEGRATION") != "1":
        pytest.skip("Integration infrastructure required")
    base = make_url(Settings().sync_migration_url)
    name = f"studyhub_academic_{uuid4().hex[:8]}_test"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = create_engine(base.set(database=name))
    url = base.set(database=name, drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    env = {**os.environ, "STUDYHUB_DATABASE_URL": url, "STUDYHUB_MIGRATION_DATABASE_URL": url}

    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=env, capture_output=True, check=check
        )

    school, classroom, period = uuid4(), uuid4(), uuid4()
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        migrate("upgrade", "e21aa6c09f52")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO schools(id,code,name,level) VALUES (:id,'OLD_CLASS','Legacy school','SMK')"
                ),
                {"id": school},
            )
            conn.execute(
                text(
                    "INSERT INTO school_classes(id,school_id,school_level,name,grade_level) VALUES (:id,:school,'SMK','12 TKJ',12)"
                ),
                {"id": classroom, "school": school},
            )
        migrate("upgrade", "f47b2a91cd30")
        with engine.begin() as conn:
            assert (
                conn.scalar(text("SELECT name FROM school_classes WHERE id=:id"), {"id": classroom})
                == "12 TKJ"
            )
            conn.execute(
                text(
                    "INSERT INTO academic_periods(id,school_id,year_start,semester,starts_on,ends_on) VALUES (:id,:school,2026,1,'2026-07-01','2026-12-31')"
                ),
                {"id": period, "school": school},
            )
        failed = migrate("downgrade", "e21aa6c09f52", check=False)
        assert failed.returncode != 0 and b"Refusing lossy academic downgrade" in failed.stderr
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "f47b2a91cd30"
            assert conn.scalar(text("SELECT count(*) FROM academic_periods")) == 1
            assert (
                conn.scalar(
                    text(
                        "SELECT relforcerowsecurity FROM pg_class WHERE relname='academic_periods'"
                    )
                )
                is True
            )
            conn.execute(text("DELETE FROM academic_periods WHERE id=:id"), {"id": period})
        migrate("downgrade", "e21aa6c09f52")
        with engine.connect() as conn:
            assert (
                conn.scalar(text("SELECT name FROM school_classes WHERE id=:id"), {"id": classroom})
                == "12 TKJ"
            )
        migrate("upgrade", "f47b2a91cd30")
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_delivery_upgrade_backfills_existing_exams_and_guards_student_data():
    if os.getenv("STUDYHUB_RUN_INTEGRATION") != "1":
        pytest.skip("Integration infrastructure required")
    base = make_url(Settings().sync_migration_url)
    name = f"studyhub_delivery_{uuid4().hex[:8]}_test"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = create_engine(base.set(database=name))
    url = base.set(database=name, drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    env = {**os.environ, "STUDYHUB_DATABASE_URL": url, "STUDYHUB_MIGRATION_DATABASE_URL": url}

    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=env, capture_output=True, check=check
        )

    ids = {
        name: uuid4()
        for name in (
            "school",
            "user",
            "period",
            "subject",
            "class",
            "assignment",
            "group",
            "module",
            "version",
            "exam",
            "student",
        )
    }
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        migrate("upgrade", "f47b2a91cd30")
        statements = [
            "INSERT INTO schools(id,code,name,level) VALUES (:school,'OLD_DELIVERY','Existing school','SMK')",
            "INSERT INTO users(id,email,full_name,is_active) VALUES (:user,'existing-delivery@example.org','Existing teacher',true)",
            "INSERT INTO memberships(school_id,user_id,role,is_active) VALUES (:school,:user,'teacher',true)",
            "INSERT INTO academic_periods(id,school_id,year_start,semester,starts_on,ends_on) VALUES (:period,:school,2026,1,'2026-07-01','2026-12-31')",
            "INSERT INTO subjects(id,school_id,code,name) VALUES (:subject,:school,'M','Mathematics')",
            "INSERT INTO school_classes(id,school_id,school_level,name,grade_level) VALUES (:class,:school,'SMK','12 A',12)",
            "INSERT INTO teaching_assignments(id,school_id,period_id,class_id,subject_id,teacher_id) VALUES (:assignment,:school,:period,:class,:subject,:user)",
            "INSERT INTO coordination_groups(id,school_id,school_level,period_id,subject_id,grade_level,coordinator_id) VALUES (:group,:school,'SMK',:period,:subject,12,:user)",
            "INSERT INTO exam_modules(id,school_id,group_id,title,items) VALUES (:module,:school,:group,'Existing exam','[]')",
            "INSERT INTO module_versions(id,school_id,module_id,group_id,version,snapshot,checksum,published_by) VALUES (:version,:school,:module,:group,1,'{}','test-checksum',:user)",
            "INSERT INTO class_exams(id,school_id,module_version_id,assignment_id,class_id,created_by,starts_at,ends_at) VALUES (:exam,:school,:version,:assignment,:class,:user,'2026-11-15T08:00:00Z','2026-11-15T10:00:00Z')",
        ]
        with engine.begin() as conn:
            for sql in statements:
                conn.execute(text(sql), ids)
        migrate("upgrade", "head")
        migrate("check")
        with engine.connect() as conn:
            exam = conn.execute(
                text(
                    "SELECT period_id,state,duration_minutes,revision FROM class_exams WHERE id=:exam"
                ),
                ids,
            ).one()
            assert exam == (ids["period"], "draft", 60, 1)
        migrate("downgrade", "f47b2a91cd30")
        migrate("upgrade", "head")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO student_profiles(id,school_id,user_id,school_level,graduation_grade) VALUES (:student,:school,:user,'SMK',12)"
                ),
                ids,
            )
        failed = migrate("downgrade", "f47b2a91cd30", check=False)
        assert failed.returncode != 0 and b"Refusing lossy student/exam downgrade" in failed.stderr
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "b60a9c12de73"
            assert conn.scalar(text("SELECT count(*) FROM student_profiles")) == 1
            assert (
                conn.scalar(
                    text(
                        "SELECT relforcerowsecurity FROM pg_class WHERE relname='student_profiles'"
                    )
                )
                is True
            )
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
