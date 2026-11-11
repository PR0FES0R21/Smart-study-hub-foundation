"""School directory and academic catalog"""

import sqlalchemy as sa
from alembic import op

revision = "b60a9c12de73"
down_revision = "a51d8e70bc92"
branch_labels = None
depends_on = None

TABLES = ("academic_years", "member_profiles", "teacher_profiles", "class_offerings")
EXISTING_RLS = ("academic_periods", "expertise_programs", "school_classes", "student_profiles")


def upgrade():
    op.create_table(
        "academic_years",
        sa.Column("year_start", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("revision > 0", name=op.f("ck_academic_years_revision")),
        sa.CheckConstraint("year_start BETWEEN 2000 AND 2200", name=op.f("ck_academic_years_year")),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_academic_years_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academic_years")),
        sa.UniqueConstraint("school_id", "year_start", name=op.f("uq_academic_years_school_id")),
    )
    op.create_table(
        "member_profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("gender", sa.String(length=8), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("birth_place", sa.String(length=120), nullable=True),
        sa.Column("phone_number", sa.String(length=32), nullable=True),
        sa.Column("address", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "gender IS NULL OR gender IN ('male','female')", name=op.f("ck_member_profiles_gender")
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "user_id"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_member_profiles_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_member_profiles_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member_profiles")),
        sa.UniqueConstraint("school_id", "user_id", name=op.f("uq_member_profiles_school_id")),
    )
    op.create_table(
        "teacher_profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("employee_number", sa.String(length=32), nullable=True),
        sa.Column("nip", sa.String(length=18), nullable=True),
        sa.Column("employment_type", sa.String(length=16), nullable=True),
        sa.Column("qualification", sa.String(length=120), nullable=True),
        sa.Column("joined_on", sa.Date(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "employment_type IS NULL OR employment_type IN ('permanent','contract','honorary')",
            name=op.f("ck_teacher_profiles_employment_type"),
        ),
        sa.CheckConstraint(
            "nip IS NULL OR nip ~ '^[0-9]{18}$'", name=op.f("ck_teacher_profiles_nip")
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "user_id"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_teacher_profiles_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_teacher_profiles_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teacher_profiles")),
        sa.UniqueConstraint("school_id", "employee_number", name="uq_teacher_employee_number"),
        sa.UniqueConstraint("school_id", "nip", name="uq_teacher_nip"),
        sa.UniqueConstraint("school_id", "user_id", name=op.f("uq_teacher_profiles_school_id")),
    )
    op.create_table(
        "class_offerings",
        sa.Column("period_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("homeroom_teacher_id", sa.UUID(), nullable=True),
        sa.Column("room", sa.String(length=64), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("revision > 0", name=op.f("ck_class_offerings_revision")),
        sa.ForeignKeyConstraint(
            ["class_id", "school_id"],
            ["school_classes.id", "school_classes.school_id"],
            name=op.f("fk_class_offerings_class_id_school_classes"),
        ),
        sa.ForeignKeyConstraint(
            ["period_id", "school_id"],
            ["academic_periods.id", "academic_periods.school_id"],
            name=op.f("fk_class_offerings_period_id_academic_periods"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "homeroom_teacher_id"],
            ["memberships.school_id", "memberships.user_id"],
            name=op.f("fk_class_offerings_school_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], name=op.f("fk_class_offerings_school_id_schools")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_class_offerings")),
        sa.UniqueConstraint(
            "school_id", "period_id", "class_id", name=op.f("uq_class_offerings_school_id")
        ),
    )
    op.create_index("ix_offering_class", "class_offerings", ["class_id", "school_id"], unique=False)
    op.create_index(
        "ix_offering_homeroom",
        "class_offerings",
        ["school_id", "homeroom_teacher_id"],
        unique=False,
    )
    op.add_column(
        "academic_periods", sa.Column("revision", sa.Integer(), server_default="1", nullable=False)
    )
    # Backfill before installing the composite year FK. Temporarily lift FORCE
    # for the migration owner only; runtime remains a separate non-owner role.
    op.execute("ALTER TABLE academic_periods NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        INSERT INTO academic_years(id,school_id,year_start)
        SELECT gen_random_uuid(), school_id, year_start
        FROM (SELECT DISTINCT school_id, year_start FROM academic_periods) periods
    """)
    op.execute("ALTER TABLE academic_periods FORCE ROW LEVEL SECURITY")
    op.execute("""
        INSERT INTO member_profiles(id,school_id,user_id,full_name)
        SELECT gen_random_uuid(), m.school_id, m.user_id, u.full_name
        FROM memberships m JOIN users u ON u.id = m.user_id
    """)
    op.execute("""
        INSERT INTO teacher_profiles(id,school_id,user_id)
        SELECT gen_random_uuid(), school_id, user_id FROM memberships WHERE role = 'teacher'
    """)
    op.create_foreign_key(
        op.f("fk_academic_periods_school_id_academic_years"),
        "academic_periods",
        "academic_years",
        ["school_id", "year_start"],
        ["school_id", "year_start"],
    )
    op.add_column(
        "expertise_programs", sa.Column("description", sa.String(length=1000), nullable=True)
    )
    op.add_column(
        "expertise_programs",
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "memberships", sa.Column("revision", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "school_classes", sa.Column("revision", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("student_profiles", sa.Column("nisn", sa.String(length=10), nullable=True))
    op.add_column("student_profiles", sa.Column("admitted_on", sa.Date(), nullable=True))
    op.add_column(
        "student_profiles", sa.Column("previous_school", sa.String(length=200), nullable=True)
    )
    op.create_unique_constraint("uq_student_nisn", "student_profiles", ["school_id", "nisn"])
    for table in (*EXISTING_RLS[:3], "memberships"):
        op.create_check_constraint(op.f(f"ck_{table}_revision"), table, "revision > 0")
    op.create_check_constraint(
        op.f("ck_student_profiles_nisn"), "student_profiles", "nisn IS NULL OR nisn ~ '^[0-9]{10}$'"
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY school_isolation ON {table} USING (school_id = nullif(current_setting('app.school_id', true), '')::uuid) WITH CHECK (school_id = nullif(current_setting('app.school_id', true), '')::uuid)"
        )


def downgrade():
    for table in (*TABLES, *EXISTING_RLS):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    # Default backfills are reconstructable; reject actual v0.6 data loss.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM class_offerings)
        OR EXISTS (SELECT 1 FROM academic_years y WHERE description IS NOT NULL OR revision <> 1
                   OR NOT EXISTS (SELECT 1 FROM academic_periods p WHERE p.school_id=y.school_id AND p.year_start=y.year_start))
        OR EXISTS (SELECT 1 FROM member_profiles p JOIN users u ON u.id=p.user_id
                   WHERE p.full_name IS DISTINCT FROM u.full_name OR gender IS NOT NULL OR birth_date IS NOT NULL
                   OR birth_place IS NOT NULL OR phone_number IS NOT NULL OR address IS NOT NULL)
        OR EXISTS (SELECT 1 FROM teacher_profiles WHERE employee_number IS NOT NULL OR nip IS NOT NULL
                   OR employment_type IS NOT NULL OR qualification IS NOT NULL OR joined_on IS NOT NULL)
        OR EXISTS (SELECT 1 FROM student_profiles WHERE nisn IS NOT NULL OR admitted_on IS NOT NULL OR previous_school IS NOT NULL)
        OR EXISTS (SELECT 1 FROM memberships WHERE revision <> 1)
        OR EXISTS (SELECT 1 FROM academic_periods WHERE revision <> 1)
        OR EXISTS (SELECT 1 FROM school_classes WHERE revision <> 1)
        OR EXISTS (SELECT 1 FROM expertise_programs WHERE revision <> 1 OR description IS NOT NULL)
        THEN RAISE EXCEPTION 'Refusing lossy directory/catalog downgrade'; END IF;
    END $$""")
    for table in EXISTING_RLS:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for table in (*EXISTING_RLS[:3], "memberships"):
        op.drop_constraint(op.f(f"ck_{table}_revision"), table, type_="check")
    op.drop_constraint(op.f("ck_student_profiles_nisn"), "student_profiles", type_="check")
    op.drop_constraint("uq_student_nisn", "student_profiles", type_="unique")
    op.drop_column("student_profiles", "previous_school")
    op.drop_column("student_profiles", "admitted_on")
    op.drop_column("student_profiles", "nisn")
    op.drop_column("school_classes", "revision")
    op.drop_column("memberships", "revision")
    op.drop_column("expertise_programs", "revision")
    op.drop_column("expertise_programs", "description")
    op.drop_constraint(
        op.f("fk_academic_periods_school_id_academic_years"), "academic_periods", type_="foreignkey"
    )
    op.drop_column("academic_periods", "revision")
    op.drop_index("ix_offering_homeroom", table_name="class_offerings")
    op.drop_index("ix_offering_class", table_name="class_offerings")
    op.drop_table("class_offerings")
    op.drop_table("teacher_profiles")
    op.drop_table("member_profiles")
    op.drop_table("academic_years")
