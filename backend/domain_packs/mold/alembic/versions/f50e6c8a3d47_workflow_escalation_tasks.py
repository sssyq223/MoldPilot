"""add workflow escalation tasks

Revision ID: f50e6c8a3d47
Revises: f40e6c8a3d46
"""
from alembic import op
import sqlalchemy as sa


revision = "f50e6c8a3d47"
down_revision = "f40e6c8a3d46"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_escalation_task",
        sa.Column("timer_id", sa.String(length=36), nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("node_key", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(length=40), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["approval_instance.id"]),
        sa.ForeignKeyConstraint(["timer_id"], ["wf_timer.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("timer_id", "user_id"),
        sa.CheckConstraint("status IN ('OPEN','CLOSED','STALE')", name="workflow_escalation_status"),
    )
    op.create_index("ix_workflow_escalation_task_timer_id", "workflow_escalation_task", ["timer_id"])
    op.create_index("ix_workflow_escalation_task_instance_id", "workflow_escalation_task", ["instance_id"])
    op.create_index("ix_workflow_escalation_task_user_id", "workflow_escalation_task", ["user_id"])
    op.create_index("ix_workflow_escalation_open", "workflow_escalation_task", ["status", "user_id", "created_at"])


def downgrade():
    op.drop_index("ix_workflow_escalation_open", table_name="workflow_escalation_task")
    op.drop_index("ix_workflow_escalation_task_user_id", table_name="workflow_escalation_task")
    op.drop_index("ix_workflow_escalation_task_instance_id", table_name="workflow_escalation_task")
    op.drop_index("ix_workflow_escalation_task_timer_id", table_name="workflow_escalation_task")
    op.drop_table("workflow_escalation_task")
