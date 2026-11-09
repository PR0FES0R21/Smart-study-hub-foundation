"""schools memberships and OIDC identities"""

import sqlalchemy as sa
from alembic import op

revision = "d34211278578"
down_revision = "2601891cc10a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schools",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("level", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("level IN ('SMP','SMA','SMK')", name=op.f("ck_schools_level")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schools")),
        sa.UniqueConstraint("code", name=op.f("uq_schools_code")),
        sa.UniqueConstraint("id", "level", name=op.f("uq_schools_id")),
    )
    op.create_table(
        "expertise_programs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.Column("school_level", sa.String(length=3), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.CheckConstraint("school_level = 'SMK'", name=op.f("ck_expertise_programs_smk_only")),
        sa.ForeignKeyConstraint(
            ["school_id", "school_level"],
            ["schools.id", "schools.level"],
            name=op.f("fk_expertise_programs_school_id_schools"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expertise_programs")),
        sa.UniqueConstraint("id", "school_id", name=op.f("uq_expertise_programs_id")),
        sa.UniqueConstraint("school_id", "code", name=op.f("uq_expertise_programs_school_id")),
    )
    op.create_table(
        "external_identities",
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_external_identities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("issuer", "subject", name=op.f("pk_external_identities")),
    )
    op.create_index(
        op.f("ix_external_identities_user_id"), "external_identities", ["user_id"], unique=False
    )
    op.create_table(
        "memberships",
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "role IN ('admin','teacher','student')", name=op.f("ck_memberships_role")
        ),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.id"],
            name=op.f("fk_memberships_school_id_schools"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("school_id", "user_id", name=op.f("pk_memberships")),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"], unique=False)
    op.create_table(
        "school_classes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.Column("school_level", sa.String(length=3), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("grade_level", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "(school_level = 'SMP' AND grade_level BETWEEN 7 AND 9 AND program_id IS NULL) OR (school_level = 'SMA' AND grade_level BETWEEN 10 AND 12 AND program_id IS NULL) OR (school_level = 'SMK' AND grade_level BETWEEN 10 AND 13)",
            name=op.f("ck_school_classes_grade_and_program"),
        ),
        sa.ForeignKeyConstraint(
            ["program_id", "school_id"],
            ["expertise_programs.id", "expertise_programs.school_id"],
            name=op.f("fk_school_classes_program_id_expertise_programs"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "school_level"],
            ["schools.id", "schools.level"],
            name=op.f("fk_school_classes_school_id_schools"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_school_classes")),
        sa.UniqueConstraint("school_id", "name", name=op.f("uq_school_classes_school_id")),
    )
    op.create_index(
        "ix_school_classes_program_id_school_id",
        "school_classes",
        ["program_id", "school_id"],
        unique=False,
    )
    # Preserve v0.1 single-school data in an explicit legacy SMK tenant.
    op.execute(
        "INSERT INTO schools(id,code,name,level) SELECT '00000000-0000-0000-0000-000000000001', 'LEGACY', 'Legacy school: review level before use', 'SMK' WHERE EXISTS (SELECT 1 FROM users) OR EXISTS (SELECT 1 FROM subjects) OR EXISTS (SELECT 1 FROM outbox)"
    )
    op.execute(
        "INSERT INTO memberships(school_id,user_id,role) SELECT '00000000-0000-0000-0000-000000000001', id, role FROM users"
    )
    op.add_column("jobs", sa.Column("school_id", sa.UUID(), nullable=True))
    op.execute("UPDATE jobs SET school_id = '00000000-0000-0000-0000-000000000001'")
    op.alter_column("jobs", "school_id", nullable=False)
    op.drop_index(op.f("ix_jobs_owner_created"), table_name="jobs")
    op.drop_index(op.f("uq_jobs_owner_idempotency"), table_name="jobs")
    op.create_index(
        "ix_jobs_school_owner_created",
        "jobs",
        ["school_id", "owner_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_jobs_school_owner_idempotency",
        "jobs",
        ["school_id", "owner_id", "idempotency_key"],
        unique=True,
    )
    op.drop_constraint(op.f("fk_jobs_owner_id_users"), "jobs", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_jobs_school_id_memberships"),
        "jobs",
        "memberships",
        ["school_id", "owner_id"],
        ["school_id", "user_id"],
    )
    op.add_column("outbox", sa.Column("school_id", sa.UUID(), nullable=True))
    op.execute("UPDATE outbox SET school_id = '00000000-0000-0000-0000-000000000001'")
    op.alter_column("outbox", "school_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_outbox_school_id_schools"),
        "outbox",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column("subjects", sa.Column("school_id", sa.UUID(), nullable=True))
    op.execute("UPDATE subjects SET school_id = '00000000-0000-0000-0000-000000000001'")
    op.alter_column("subjects", "school_id", nullable=False)
    op.drop_constraint(op.f("uq_subjects_code"), "subjects", type_="unique")
    op.create_unique_constraint(op.f("uq_subjects_school_id"), "subjects", ["school_id", "code"])
    op.create_foreign_key(
        op.f("fk_subjects_school_id_schools"),
        "subjects",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "users",
        sa.Column(
            "is_platform_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.alter_column("users", "password_hash", existing_type=sa.VARCHAR(length=255), nullable=True)
    op.execute("UPDATE users SET is_platform_admin = (role = 'admin')")
    op.drop_column("users", "role")
    for table in ("subjects", "jobs", "expertise_programs", "school_classes"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY school_isolation ON {table} USING (school_id = nullif(current_setting('app.school_id', true), '')::uuid) WITH CHECK (school_id = nullif(current_setting('app.school_id', true), '')::uuid)"
        )


def downgrade():
    connection = op.get_bind()
    unsafe = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM schools WHERE code <> 'LEGACY') OR EXISTS (SELECT 1 FROM external_identities) OR EXISTS (SELECT 1 FROM users WHERE password_hash IS NULL) OR EXISTS (SELECT 1 FROM school_classes) OR EXISTS (SELECT 1 FROM expertise_programs)"
        )
    ).scalar()
    if unsafe:
        raise RuntimeError(
            "Refusing lossy multi-school downgrade; export/restore explicitly instead"
        )
    for table in ("subjects", "jobs", "expertise_programs", "school_classes"):
        op.execute(f"DROP POLICY school_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.add_column("users", sa.Column("role", sa.VARCHAR(length=16), nullable=True))
    op.execute(
        "UPDATE users SET role = COALESCE((SELECT role FROM memberships WHERE user_id=users.id LIMIT 1), 'student')"
    )
    op.alter_column("users", "role", nullable=False)
    op.create_check_constraint(
        op.f("ck_users_role"), "users", "role IN ('admin', 'teacher', 'student')"
    )
    op.alter_column("users", "password_hash", existing_type=sa.VARCHAR(length=255), nullable=False)
    op.drop_column("users", "is_platform_admin")
    op.drop_constraint(op.f("fk_subjects_school_id_schools"), "subjects", type_="foreignkey")
    op.drop_constraint(op.f("uq_subjects_school_id"), "subjects", type_="unique")
    op.create_unique_constraint(
        op.f("uq_subjects_code"), "subjects", ["code"], postgresql_nulls_not_distinct=False
    )
    op.drop_column("subjects", "school_id")
    op.drop_constraint(op.f("fk_outbox_school_id_schools"), "outbox", type_="foreignkey")
    op.drop_column("outbox", "school_id")
    op.drop_constraint(op.f("fk_jobs_school_id_memberships"), "jobs", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_jobs_owner_id_users"), "jobs", "users", ["owner_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_index("uq_jobs_school_owner_idempotency", table_name="jobs")
    op.drop_index("ix_jobs_school_owner_created", table_name="jobs")
    op.create_index(
        op.f("uq_jobs_owner_idempotency"), "jobs", ["owner_id", "idempotency_key"], unique=True
    )
    op.create_index(op.f("ix_jobs_owner_created"), "jobs", ["owner_id", "created_at"], unique=False)
    op.drop_column("jobs", "school_id")
    op.drop_index("ix_school_classes_program_id_school_id", table_name="school_classes")
    op.drop_table("school_classes")
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_external_identities_user_id"), table_name="external_identities")
    op.drop_table("external_identities")
    op.drop_table("expertise_programs")
    op.drop_table("schools")
