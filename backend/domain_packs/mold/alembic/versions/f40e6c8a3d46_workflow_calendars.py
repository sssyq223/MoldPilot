"""add versioned workflow calendars

Revision ID: f40e6c8a3d46
Revises: e30e6c8a3d45
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f40e6c8a3d46"
down_revision = "e30e6c8a3d45"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_calendar",
        sa.Column("calendar_key", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("package_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("published_by", sa.String(length=36), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["published_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("calendar_key", "version"),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED','RETIRED')", name="workflow_calendar_status"),
    )


def downgrade():
    op.drop_table("workflow_calendar")
