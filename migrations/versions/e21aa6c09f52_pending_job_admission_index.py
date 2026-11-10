"""Index job admission and revoke pre-fix sessions for passwordless accounts."""

import sqlalchemy as sa
from alembic import op

revision = "e21aa6c09f52"
down_revision = "b84c12f073a9"
branch_labels = None
depends_on = None


def upgrade():
    # Local and SSO sessions share a table, so all existing sessions for these
    # accounts must be revoked once. Legitimate SSO users can authenticate again.
    op.execute(
        "UPDATE auth_sessions SET revoked_at = now() "
        "WHERE revoked_at IS NULL AND user_id IN "
        "(SELECT id FROM users WHERE password_hash IS NULL OR password_hash = '')"
    )
    op.create_index(
        "ix_jobs_pending_school",
        "jobs",
        ["school_id"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade():
    # Never reactivate sessions that may have been issued through the old bypass.
    op.drop_index("ix_jobs_pending_school", table_name="jobs")
