"""record template-scoped approval return targets

Revision ID: a10c0e000004
Revises: a10c0e000003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a10c0e000004"
down_revision = "a10c0e000003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "approval_action",
        sa.Column(
            "decision_context",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("approval_action", "decision_context", server_default=None)


def downgrade():
    op.drop_column("approval_action", "decision_context")
