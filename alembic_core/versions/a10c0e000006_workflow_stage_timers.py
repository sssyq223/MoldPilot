"""add durable workflow stage timers

Revision ID: a10c0e000006
Revises: a10c0e000005
"""
from alembic import op
import sqlalchemy as sa


revision = "a10c0e000006"
down_revision = "a10c0e000005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "wf_timer",
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("node_key", sa.String(length=80), nullable=False),
        sa.Column("timer_key", sa.String(length=20), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=80), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["approval_instance.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id", "stage_index", "timer_key", "schedule_version"),
        sa.CheckConstraint("timer_key IN ('REMINDER','DUE')", name="wf_timer_key"),
        sa.CheckConstraint(
            "status IN ('SCHEDULED','FIRED','CANCELLED','STALE','FAILED')",
            name="wf_timer_status",
        ),
    )
    op.create_index("ix_wf_timer_instance_id", "wf_timer", ["instance_id"])
    op.create_index("ix_wf_timer_due_at", "wf_timer", ["due_at"])
    op.create_index("ix_wf_timer_claim", "wf_timer", ["status", "due_at", "lease_until"])


def downgrade():
    op.drop_index("ix_wf_timer_claim", table_name="wf_timer")
    op.drop_index("ix_wf_timer_due_at", table_name="wf_timer")
    op.drop_index("ix_wf_timer_instance_id", table_name="wf_timer")
    op.drop_table("wf_timer")
