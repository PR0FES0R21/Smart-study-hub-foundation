"""academic coordination and versioned exam collaboration"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f47b2a91cd30"
down_revision = "e21aa6c09f52"
branch_labels = None
depends_on = None

TABLES = (
    "academic_periods",
    "teaching_assignments",
    "coordination_groups",
    "academic_audit",
    "questions",
    "exam_modules",
    "module_versions",
    "class_exams",
)


def upgrade():
    op.create_unique_constraint(
        "uq_school_classes_id_school", "school_classes", ["id", "school_id"]
    )
    op.create_unique_constraint("uq_subjects_id_school", "subjects", ["id", "school_id"])
    op.create_table(
        "academic_periods",
        sa.Column("year_start", sa.Integer(), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("status IN ('open','closed')", name=op.f("ck_academic_periods_status")),
        sa.CheckConstraint("ends_on >= starts_on", name=op.f("ck_academic_periods_dates")),
        sa.CheckConstraint("semester IN (1,2)", name=op.f("ck_academic_periods_semester")),
        sa.CheckConstraint(
            "year_start BETWEEN 2000 AND 2200", name=op.f("ck_academic_periods_year")
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_academic_periods_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academic_periods")),
        sa.UniqueConstraint("id", "school_id", name=op.f("uq_academic_periods_id")),
        sa.UniqueConstraint(
            "school_id", "year_start", "semester", name=op.f("uq_academic_periods_school_id")
        ),
    )
    op.create_table(
        "academic_audit",
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "actor_id"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_academic_audit_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_academic_audit_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academic_audit")),
    )
    op.create_index(
        "ix_academic_audit_actor", "academic_audit", ["school_id", "actor_id"], unique=False
    )
    op.create_index(
        "ix_academic_audit_school_created",
        "academic_audit",
        ["school_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "coordination_groups",
        sa.Column("period_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("school_level", sa.String(length=3), nullable=False),
        sa.Column("grade_level", sa.Integer(), nullable=False),
        sa.Column("coordinator_id", sa.UUID(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(school_level = 'SMP' AND grade_level BETWEEN 7 AND 9) OR (school_level = 'SMA' AND grade_level BETWEEN 10 AND 12) OR (school_level = 'SMK' AND grade_level BETWEEN 10 AND 13)",
            name=op.f("ck_coordination_groups_grade"),
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_coordination_groups_revision")),
        sa.ForeignKeyConstraint(
            ["period_id", "school_id"],
            ["academic_periods.id", "academic_periods.school_id"],
            name=op.f("fk_coordination_groups_period_id_academic_periods"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "coordinator_id"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_coordination_groups_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "school_level"],
            ["schools.id", "schools.level"],
            name="fk_coordination_school_level",
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_coordination_groups_school_id_schools")
        ),
        sa.ForeignKeyConstraint(
            ["subject_id", "school_id"],
            ["subjects.id", "subjects.school_id"],
            name=op.f("fk_coordination_groups_subject_id_subjects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coordination_groups")),
        sa.UniqueConstraint("id", "school_id", name=op.f("uq_coordination_groups_id")),
        sa.UniqueConstraint(
            "school_id", "period_id", "subject_id", "grade_level", name="uq_coordination_scope"
        ),
    )
    op.create_index(
        "ix_coordination_coordinator",
        "coordination_groups",
        ["school_id", "coordinator_id"],
        unique=False,
    )
    op.create_index(
        "ix_coordination_subject", "coordination_groups", ["subject_id", "school_id"], unique=False
    )
    op.create_table(
        "exam_modules",
        sa.Column("group_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("published_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(items) = 'array' AND jsonb_array_length(items) <= 200",
            name=op.f("ck_exam_modules_items"),
        ),
        sa.CheckConstraint(
            "revision > 0 AND published_count >= 0", name=op.f("ck_exam_modules_revision")
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "school_id"],
            ["coordination_groups.id", "coordination_groups.school_id"],
            name=op.f("fk_exam_modules_group_id_coordination_groups"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_exam_modules_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exam_modules")),
        sa.UniqueConstraint("id", "school_id", "group_id", name=op.f("uq_exam_modules_id")),
    )
    op.create_index(
        "ix_exam_modules_group", "exam_modules", ["group_id", "school_id"], unique=False
    )
    op.create_table(
        "questions",
        sa.Column("group_id", sa.UUID(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("review_note", sa.String(length=2000), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("jsonb_typeof(content) = 'object'", name=op.f("ck_questions_content")),
        sa.CheckConstraint(
            "status IN ('draft','submitted','approved','rejected')",
            name=op.f("ck_questions_status"),
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_questions_revision")),
        sa.ForeignKeyConstraint(
            ["group_id", "school_id"],
            ["coordination_groups.id", "coordination_groups.school_id"],
            name=op.f("fk_questions_group_id_coordination_groups"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "author_id"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_questions_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_questions_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_questions")),
        sa.UniqueConstraint("id", "school_id", name=op.f("uq_questions_id")),
    )
    op.create_index("ix_questions_author", "questions", ["school_id", "author_id"], unique=False)
    op.create_index(
        "ix_questions_group_status",
        "questions",
        ["group_id", "school_id", "status", "id"],
        unique=False,
    )
    op.create_table(
        "teaching_assignments",
        sa.Column("period_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("teacher_id", sa.UUID(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["class_id", "school_id"],
            ["school_classes.id", "school_classes.school_id"],
            name=op.f("fk_teaching_assignments_class_id_school_classes"),
        ),
        sa.ForeignKeyConstraint(
            ["period_id", "school_id"],
            ["academic_periods.id", "academic_periods.school_id"],
            name=op.f("fk_teaching_assignments_period_id_academic_periods"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "teacher_id"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_teaching_assignments_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_teaching_assignments_school_id_schools")
        ),
        sa.ForeignKeyConstraint(
            ["subject_id", "school_id"],
            ["subjects.id", "subjects.school_id"],
            name=op.f("fk_teaching_assignments_subject_id_subjects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teaching_assignments")),
        sa.UniqueConstraint("id", "school_id", "class_id", name="uq_teaching_assignment_class"),
        sa.UniqueConstraint("id", "school_id", name=op.f("uq_teaching_assignments_id")),
        sa.UniqueConstraint(
            "school_id",
            "period_id",
            "class_id",
            "subject_id",
            "teacher_id",
            name="uq_teaching_assignment_scope",
        ),
    )
    op.create_index(
        "ix_teaching_class", "teaching_assignments", ["class_id", "school_id"], unique=False
    )
    op.create_index(
        "ix_teaching_scope",
        "teaching_assignments",
        ["school_id", "period_id", "subject_id", "teacher_id"],
        unique=False,
    )
    op.create_index(
        "ix_teaching_subject", "teaching_assignments", ["subject_id", "school_id"], unique=False
    )
    op.create_index(
        "ix_teaching_teacher", "teaching_assignments", ["school_id", "teacher_id"], unique=False
    )
    op.create_table(
        "module_versions",
        sa.Column("module_id", sa.UUID(), nullable=False),
        sa.Column("group_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("published_by", sa.UUID(), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name=op.f("ck_module_versions_snapshot")
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_module_versions_version")),
        sa.ForeignKeyConstraint(
            ["module_id", "school_id", "group_id"],
            ["exam_modules.id", "exam_modules.school_id", "exam_modules.group_id"],
            name=op.f("fk_module_versions_module_id_exam_modules"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "published_by"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_module_versions_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_module_versions_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_module_versions")),
        sa.UniqueConstraint("id", "school_id", name=op.f("uq_module_versions_id")),
        sa.UniqueConstraint("module_id", "version", name=op.f("uq_module_versions_module_id")),
    )
    op.create_index(
        "ix_module_versions_group", "module_versions", ["school_id", "group_id"], unique=False
    )
    op.create_index(
        "ix_module_versions_publisher",
        "module_versions",
        ["school_id", "published_by"],
        unique=False,
    )
    op.create_table(
        "class_exams",
        sa.Column("module_version_id", sa.UUID(), nullable=False),
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_class_exams_dates")),
        sa.ForeignKeyConstraint(
            ["assignment_id", "school_id", "class_id"],
            [
                "teaching_assignments.id",
                "teaching_assignments.school_id",
                "teaching_assignments.class_id",
            ],
            name=op.f("fk_class_exams_assignment_id_teaching_assignments"),
        ),
        sa.ForeignKeyConstraint(
            ["module_version_id", "school_id"],
            ["module_versions.id", "module_versions.school_id"],
            name=op.f("fk_class_exams_module_version_id_module_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "created_by"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_class_exams_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_class_exams_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_class_exams")),
        sa.UniqueConstraint(
            "module_version_id", "class_id", name=op.f("uq_class_exams_module_version_id")
        ),
    )
    op.create_index(
        "ix_class_exams_assignment",
        "class_exams",
        ["assignment_id", "school_id", "class_id"],
        unique=False,
    )
    op.create_index(
        "ix_class_exams_creator", "class_exams", ["school_id", "created_by"], unique=False
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY school_isolation ON {table} "
            "USING (school_id = nullif(current_setting('app.school_id', true), '')::uuid) "
            "WITH CHECK (school_id = nullif(current_setting('app.school_id', true), '')::uuid)"
        )
    op.execute("""
        CREATE FUNCTION reject_academic_record_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Published versions and academic audit entries are immutable'
                USING ERRCODE = '23514';
        END;
        $$
    """)
    for table in ("module_versions", "academic_audit"):
        op.execute(
            f"CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_academic_record_mutation()"
        )


def downgrade():
    # An accidental rollback must not erase authored questions or teaching history.
    # Maintenance role must own the tables or bypass RLS, as for every release.
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    for table in TABLES:
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {table} LIMIT 1) THEN
                RAISE EXCEPTION 'Refusing lossy academic downgrade: export data first';
            END IF;
        END $$""")
    for table in ("module_versions", "academic_audit"):
        op.execute(f"DROP TRIGGER immutable_record ON {table}")
    op.execute("DROP FUNCTION reject_academic_record_mutation()")

    op.drop_index("ix_class_exams_creator", table_name="class_exams")
    op.drop_index("ix_class_exams_assignment", table_name="class_exams")
    op.drop_table("class_exams")
    op.drop_index("ix_module_versions_publisher", table_name="module_versions")
    op.drop_index("ix_module_versions_group", table_name="module_versions")
    op.drop_table("module_versions")
    op.drop_index("ix_teaching_teacher", table_name="teaching_assignments")
    op.drop_index("ix_teaching_subject", table_name="teaching_assignments")
    op.drop_index("ix_teaching_scope", table_name="teaching_assignments")
    op.drop_index("ix_teaching_class", table_name="teaching_assignments")
    op.drop_table("teaching_assignments")
    op.drop_index("ix_questions_group_status", table_name="questions")
    op.drop_index("ix_questions_author", table_name="questions")
    op.drop_table("questions")
    op.drop_index("ix_exam_modules_group", table_name="exam_modules")
    op.drop_table("exam_modules")
    op.drop_index("ix_coordination_subject", table_name="coordination_groups")
    op.drop_index("ix_coordination_coordinator", table_name="coordination_groups")
    op.drop_table("coordination_groups")
    op.drop_index("ix_academic_audit_school_created", table_name="academic_audit")
    op.drop_index("ix_academic_audit_actor", table_name="academic_audit")
    op.drop_table("academic_audit")
    op.drop_table("academic_periods")

    op.drop_constraint("uq_subjects_id_school", "subjects", type_="unique")
    op.drop_constraint("uq_school_classes_id_school", "school_classes", type_="unique")
