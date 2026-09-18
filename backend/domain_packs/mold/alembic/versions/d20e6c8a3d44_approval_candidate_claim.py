"""add candidate claim records for single-owner approval tasks

Revision ID: d20e6c8a3d44
Revises: c10e6c8a3d43
"""
from alembic import op
import sqlalchemy as sa


revision = "d20e6c8a3d44"
down_revision = "c10e6c8a3d43"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "approval_candidate",
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("seat_id", sa.String(length=36), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["approval_instance.id"]),
        sa.ForeignKeyConstraint(["seat_id"], ["approval_seat.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id", "stage_index", "user_id"),
        sa.UniqueConstraint("seat_id"),
        sa.CheckConstraint(
            "(status = 'AVAILABLE' AND seat_id IS NULL AND claimed_at IS NULL) OR "
            "(status = 'CLAIMED' AND seat_id IS NOT NULL AND claimed_at IS NOT NULL) OR "
            "(status = 'CLOSED' AND seat_id IS NULL AND claimed_at IS NULL)",
            name="approval_candidate_state_shape",
        ),
    )
    op.create_index("ix_approval_candidate_instance_id", "approval_candidate", ["instance_id"])
    op.create_index("ix_approval_candidate_user_id", "approval_candidate", ["user_id"])
    op.create_index(
        "ix_approval_candidate_user_status", "approval_candidate", ["user_id", "status"]
    )


def downgrade():
    op.drop_index("ix_approval_candidate_user_status", table_name="approval_candidate")
    op.drop_index("ix_approval_candidate_user_id", table_name="approval_candidate")
    op.drop_index("ix_approval_candidate_instance_id", table_name="approval_candidate")
    op.drop_table("approval_candidate")
