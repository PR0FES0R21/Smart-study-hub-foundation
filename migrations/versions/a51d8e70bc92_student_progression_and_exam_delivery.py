"""student progression and exam delivery"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a51d8e70bc92"
down_revision = "f47b2a91cd30"
branch_labels = None
depends_on = None

TABLES = ("student_profiles", "student_enrollments", "exam_participants", "exam_attempts")


def upgrade():
    op.create_unique_constraint(
        "uq_teaching_assignment_period",
        "teaching_assignments",
        ["id", "school_id", "class_id", "period_id"],
    )
    op.add_column("class_exams", sa.Column("period_id", sa.UUID(), nullable=True))
    op.add_column(
        "class_exams",
        sa.Column("duration_minutes", sa.Integer(), server_default="60", nullable=False),
    )
    op.add_column(
        "class_exams",
        sa.Column("state", sa.String(length=16), server_default="draft", nullable=False),
    )
    op.add_column(
        "class_exams", sa.Column("revision", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "class_exams", sa.Column("results_released_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("ALTER TABLE class_exams NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE class_exams e SET period_id = a.period_id FROM teaching_assignments a WHERE e.assignment_id = a.id AND e.school_id = a.school_id"
    )
    op.alter_column("class_exams", "period_id", nullable=False)
    op.execute("ALTER TABLE class_exams FORCE ROW LEVEL SECURITY")
    op.create_unique_constraint(
        "uq_class_exam_scope", "class_exams", ["id", "school_id", "class_id", "period_id"]
    )
    op.create_foreign_key(
        "fk_class_exam_assignment_period",
        "class_exams",
        "teaching_assignments",
        ["assignment_id", "school_id", "class_id", "period_id"],
        ["id", "school_id", "class_id", "period_id"],
    )
    op.create_check_constraint(
        op.f("ck_class_exams_duration"), "class_exams", "duration_minutes BETWEEN 1 AND 360"
    )
    op.create_check_constraint(
        op.f("ck_class_exams_state"), "class_exams", "state IN ('draft','published')"
    )
    op.create_check_constraint(op.f("ck_class_exams_revision"), "class_exams", "revision > 0")
    op.create_table(
        "student_profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("student_number", sa.String(length=32), nullable=True),
        sa.Column("school_level", sa.String(length=3), nullable=False),
        sa.Column("graduation_grade", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("graduated_on", sa.Date(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(school_level='SMP' AND graduation_grade=9) OR (school_level='SMA' AND graduation_grade=12) OR (school_level='SMK' AND graduation_grade IN (12,13))",
            name=op.f("ck_student_profiles_graduation_grade"),
        ),
        sa.CheckConstraint(
            "(status = 'graduated') = (graduated_on IS NOT NULL)",
            name=op.f("ck_student_profiles_graduation_date"),
        ),
        sa.CheckConstraint(
            "status IN ('active','graduated','withdrawn')", name=op.f("ck_student_profiles_status")
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "school_level"],
            ["schools.id", "schools.level"],
            name="fk_student_school_level",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "user_id"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_student_profiles_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_student_profiles_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_student_profiles")),
        sa.UniqueConstraint("id", "school_id", name=op.f("uq_student_profiles_id")),
        sa.UniqueConstraint("school_id", "student_number", name="uq_student_number"),
        sa.UniqueConstraint("school_id", "user_id", name=op.f("uq_student_profiles_school_id")),
    )
    op.create_table(
        "student_enrollments",
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("period_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(state = 'active') = (ended_on IS NULL)", name=op.f("ck_student_enrollments_end_date")
        ),
        sa.CheckConstraint(
            "state IN ('active','continued','promoted','repeated','graduated','withdrawn')",
            name=op.f("ck_student_enrollments_state"),
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_student_enrollments_revision")),
        sa.ForeignKeyConstraint(
            ["class_id", "school_id"],
            ["school_classes.id", "school_classes.school_id"],
            name=op.f("fk_student_enrollments_class_id_school_classes"),
        ),
        sa.ForeignKeyConstraint(
            ["period_id", "school_id"],
            ["academic_periods.id", "academic_periods.school_id"],
            name=op.f("fk_student_enrollments_period_id_academic_periods"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_student_enrollments_school_id_schools")
        ),
        sa.ForeignKeyConstraint(
            ["student_id", "school_id"],
            ["student_profiles.id", "student_profiles.school_id"],
            name=op.f("fk_student_enrollments_student_id_student_profiles"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_student_enrollments")),
        sa.UniqueConstraint(
            "id", "school_id", "student_id", "period_id", "class_id", name="uq_enrollment_scope"
        ),
        sa.UniqueConstraint(
            "school_id", "student_id", "period_id", name=op.f("uq_student_enrollments_school_id")
        ),
    )
    op.create_index(
        "ix_enrollment_class", "student_enrollments", ["class_id", "school_id"], unique=False
    )
    op.create_index(
        "ix_enrollment_roster",
        "student_enrollments",
        ["school_id", "period_id", "class_id", "state"],
        unique=False,
    )
    op.create_index(
        "uq_enrollment_active",
        "student_enrollments",
        ["school_id", "student_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_table(
        "exam_participants",
        sa.Column("exam_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("period_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["enrollment_id", "school_id", "student_id", "period_id", "class_id"],
            [
                "student_enrollments.id",
                "student_enrollments.school_id",
                "student_enrollments.student_id",
                "student_enrollments.period_id",
                "student_enrollments.class_id",
            ],
            name=op.f("fk_exam_participants_enrollment_id_student_enrollments"),
        ),
        sa.ForeignKeyConstraint(
            ["exam_id", "school_id", "class_id", "period_id"],
            [
                "class_exams.id",
                "class_exams.school_id",
                "class_exams.class_id",
                "class_exams.period_id",
            ],
            name=op.f("fk_exam_participants_exam_id_class_exams"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_exam_participants_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exam_participants")),
        sa.UniqueConstraint("exam_id", "student_id", name=op.f("uq_exam_participants_exam_id")),
        sa.UniqueConstraint("id", "school_id", name=op.f("uq_exam_participants_id")),
    )
    op.create_index(
        "ix_participants_enrollment",
        "exam_participants",
        ["enrollment_id", "school_id"],
        unique=False,
    )
    op.create_index(
        "ix_participants_student",
        "exam_participants",
        ["school_id", "student_id", "exam_id"],
        unique=False,
    )
    op.create_table(
        "exam_attempts",
        sa.Column("participant_id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_reason", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="in_progress", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "answers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "grading",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("total_points", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("pending_essays", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(status = 'in_progress' AND submitted_at IS NULL AND submitted_reason IS NULL AND score IS NULL) OR (status IN ('submitted','graded') AND submitted_at IS NOT NULL AND submitted_reason IN ('manual','timeout') AND score IS NOT NULL)",
            name=op.f("ck_exam_attempts_submission"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(answers) = 'object' AND jsonb_typeof(grading) = 'object'",
            name=op.f("ck_exam_attempts_payload"),
        ),
        sa.CheckConstraint(
            "status != 'graded' OR pending_essays = 0", name=op.f("ck_exam_attempts_graded")
        ),
        sa.CheckConstraint(
            "status IN ('in_progress','submitted','graded')", name=op.f("ck_exam_attempts_status")
        ),
        sa.CheckConstraint("deadline_at > started_at", name=op.f("ck_exam_attempts_deadline")),
        sa.CheckConstraint(
            "revision > 0 AND pending_essays >= 0 AND total_points > 0",
            name=op.f("ck_exam_attempts_counters"),
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= total_points)",
            name=op.f("ck_exam_attempts_score"),
        ),
        sa.ForeignKeyConstraint(
            ["participant_id", "school_id"],
            ["exam_participants.id", "exam_participants.school_id"],
            name=op.f("fk_exam_attempts_participant_id_exam_participants"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_exam_attempts_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exam_attempts")),
        sa.UniqueConstraint("participant_id", name=op.f("uq_exam_attempts_participant_id")),
    )
    op.create_index(
        "ix_attempts_expiry",
        "exam_attempts",
        ["school_id", "deadline_at"],
        unique=False,
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY school_isolation ON {table} USING (school_id = nullif(current_setting('app.school_id', true), '')::uuid) WITH CHECK (school_id = nullif(current_setting('app.school_id', true), '')::uuid)"
        )
    op.execute("""
        CREATE FUNCTION protect_enrollment_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Enrollment history cannot be deleted' USING ERRCODE = '23514';
            END IF;
            IF OLD.state <> 'active' OR
               ROW(NEW.id,NEW.school_id,NEW.student_id,NEW.period_id,NEW.class_id,NEW.created_at)
               IS DISTINCT FROM ROW(OLD.id,OLD.school_id,OLD.student_id,OLD.period_id,OLD.class_id,OLD.created_at) THEN
                RAISE EXCEPTION 'Enrollment history is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER preserve_enrollment BEFORE UPDATE OR DELETE ON student_enrollments FOR EACH ROW EXECUTE FUNCTION protect_enrollment_history()"
    )
    op.execute("""
        CREATE FUNCTION protect_exam_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Exam attempts cannot be deleted' USING ERRCODE = '23514';
            END IF;
            IF ROW(NEW.id,NEW.school_id,NEW.participant_id,NEW.started_at,NEW.deadline_at,NEW.total_points)
               IS DISTINCT FROM ROW(OLD.id,OLD.school_id,OLD.participant_id,OLD.started_at,OLD.deadline_at,OLD.total_points) THEN
                RAISE EXCEPTION 'Attempt identity and timing are immutable' USING ERRCODE = '23514';
            END IF;
            IF OLD.status <> 'in_progress' AND
               (NEW.status = 'in_progress' OR ROW(NEW.answers,NEW.submitted_at,NEW.submitted_reason)
                IS DISTINCT FROM ROW(OLD.answers,OLD.submitted_at,OLD.submitted_reason)) THEN
                RAISE EXCEPTION 'Submitted answers are immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER preserve_attempt BEFORE UPDATE OR DELETE ON exam_attempts FOR EACH ROW EXECUTE FUNCTION protect_exam_attempt()"
    )
    op.execute("""
        CREATE FUNCTION protect_published_class_exam() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.state = 'published' THEN
                    RAISE EXCEPTION 'Published exams cannot be deleted' USING ERRCODE = '23514';
                END IF;
                RETURN OLD;
            END IF;
            IF ROW(NEW.id,NEW.school_id,NEW.period_id,NEW.class_id,NEW.assignment_id,NEW.module_version_id)
               IS DISTINCT FROM ROW(OLD.id,OLD.school_id,OLD.period_id,OLD.class_id,OLD.assignment_id,OLD.module_version_id) OR
               (OLD.state = 'published' AND ROW(NEW.state,NEW.starts_at,NEW.ends_at,NEW.duration_minutes)
                IS DISTINCT FROM ROW(OLD.state,OLD.starts_at,OLD.ends_at,OLD.duration_minutes)) OR
               (OLD.results_released_at IS NOT NULL AND NEW.results_released_at IS DISTINCT FROM OLD.results_released_at) THEN
                RAISE EXCEPTION 'Published exam scope and timing are immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER preserve_exam BEFORE UPDATE OR DELETE ON class_exams FOR EACH ROW EXECUTE FUNCTION protect_published_class_exam()"
    )

    op.execute("""
        CREATE FUNCTION protect_exam_roster() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Published participant rosters are immutable' USING ERRCODE = '23514';
        END $$
    """)
    op.execute(
        "CREATE TRIGGER preserve_roster BEFORE UPDATE OR DELETE ON exam_participants FOR EACH ROW EXECUTE FUNCTION protect_exam_roster()"
    )


def downgrade():
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {table} LIMIT 1) THEN
                RAISE EXCEPTION 'Refusing lossy student/exam downgrade';
            END IF;
        END $$""")
    op.execute("ALTER TABLE class_exams NO FORCE ROW LEVEL SECURITY")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM class_exams WHERE state <> 'draft' OR revision <> 1 OR duration_minutes <> 60 OR results_released_at IS NOT NULL) THEN
            RAISE EXCEPTION 'Refusing lossy exam configuration downgrade';
        END IF;
    END $$""")
    op.execute("ALTER TABLE class_exams FORCE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER preserve_roster ON exam_participants")
    op.execute("DROP FUNCTION protect_exam_roster()")
    op.execute("DROP TRIGGER preserve_exam ON class_exams")
    op.execute("DROP TRIGGER preserve_attempt ON exam_attempts")
    op.execute("DROP TRIGGER preserve_enrollment ON student_enrollments")
    op.execute("DROP FUNCTION protect_published_class_exam()")
    op.execute("DROP FUNCTION protect_exam_attempt()")
    op.execute("DROP FUNCTION protect_enrollment_history()")
    op.drop_index(
        "ix_attempts_expiry",
        table_name="exam_attempts",
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.drop_table("exam_attempts")
    op.drop_index("ix_participants_student", table_name="exam_participants")
    op.drop_index("ix_participants_enrollment", table_name="exam_participants")
    op.drop_table("exam_participants")
    op.drop_index(
        "uq_enrollment_active",
        table_name="student_enrollments",
        postgresql_where=sa.text("state = 'active'"),
    )
    op.drop_index("ix_enrollment_roster", table_name="student_enrollments")
    op.drop_index("ix_enrollment_class", table_name="student_enrollments")
    op.drop_table("student_enrollments")
    op.drop_table("student_profiles")
    op.drop_constraint(op.f("ck_class_exams_duration"), "class_exams", type_="check")
    op.drop_constraint(op.f("ck_class_exams_state"), "class_exams", type_="check")
    op.drop_constraint(op.f("ck_class_exams_revision"), "class_exams", type_="check")
    op.drop_constraint("fk_class_exam_assignment_period", "class_exams", type_="foreignkey")
    op.drop_constraint("uq_class_exam_scope", "class_exams", type_="unique")
    op.drop_column("class_exams", "results_released_at")
    op.drop_column("class_exams", "revision")
    op.drop_column("class_exams", "state")
    op.drop_column("class_exams", "duration_minutes")
    op.drop_column("class_exams", "period_id")
    op.drop_constraint("uq_teaching_assignment_period", "teaching_assignments", type_="unique")
