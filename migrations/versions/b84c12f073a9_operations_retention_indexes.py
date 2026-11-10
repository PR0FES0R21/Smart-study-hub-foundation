"""Index bounded operational retention queries."""

import sqlalchemy as sa
from alembic import op

revision = "b84c12f073a9"
down_revision = "d34211278578"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_index(
        "ix_outbox_delivered_at",
        "outbox",
        ["delivered_at"],
        postgresql_where=sa.text("delivered_at IS NOT NULL"),
    )


def downgrade():
    op.drop_index("ix_outbox_delivered_at", table_name="outbox")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
