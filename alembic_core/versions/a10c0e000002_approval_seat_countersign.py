"""add generic approval-seat countersign metadata

Revision ID: a10c0e000002
Revises: a10c0e000001
"""
from alembic import op
import sqlalchemy as sa


revision = "a10c0e000002"
down_revision = "a10c0e000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("approval_seat", sa.Column("parent_seat_id", sa.String(length=36), nullable=True))
    op.add_column("approval_seat", sa.Column("countersign_timing", sa.String(length=10), nullable=True))
    op.add_column("approval_seat", sa.Column("countersign_initiated_by", sa.String(length=36), nullable=True))
    op.add_column("approval_seat", sa.Column("countersign_reason", sa.Text(), nullable=True))
    op.add_column("approval_seat", sa.Column(
        "countersign_sequence", sa.Integer(), nullable=False, server_default="0"
    ))
    op.create_foreign_key(
        "fk_approval_seat_parent_seat", "approval_seat", "approval_seat",
        ["parent_seat_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_approval_seat_countersign_initiator", "approval_seat", "app_user",
        ["countersign_initiated_by"], ["id"],
    )
    op.create_index("ix_approval_seat_parent_seat_id", "approval_seat", ["parent_seat_id"])
    op.create_check_constraint(
        "approval_seat_countersign_shape",
        "approval_seat",
        "(parent_seat_id IS NULL AND countersign_timing IS NULL "
        "AND countersign_initiated_by IS NULL AND countersign_reason IS NULL "
        "AND countersign_sequence = 0) OR "
        "(parent_seat_id IS NOT NULL AND countersign_timing IN ('PRE','POST') "
        "AND countersign_initiated_by IS NOT NULL AND countersign_reason IS NOT NULL "
        "AND countersign_sequence > 0)",
    )
    op.alter_column("approval_seat", "countersign_sequence", server_default=None)


def downgrade():
    op.drop_constraint("approval_seat_countersign_shape", "approval_seat", type_="check")
    op.drop_index("ix_approval_seat_parent_seat_id", table_name="approval_seat")
    op.drop_constraint("fk_approval_seat_countersign_initiator", "approval_seat", type_="foreignkey")
    op.drop_constraint("fk_approval_seat_parent_seat", "approval_seat", type_="foreignkey")
    op.drop_column("approval_seat", "countersign_sequence")
    op.drop_column("approval_seat", "countersign_reason")
    op.drop_column("approval_seat", "countersign_initiated_by")
    op.drop_column("approval_seat", "countersign_timing")
    op.drop_column("approval_seat", "parent_seat_id")
