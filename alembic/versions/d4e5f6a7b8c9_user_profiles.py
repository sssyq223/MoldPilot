"""create user profile storage

Revision ID: d4e5f6a7b8c9
Revises: c9d2e5f7a814
Create Date: 2026-09-16 16:55:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c9d2e5f7a814"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_user_profile",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade():
    op.drop_table("app_user_profile")
