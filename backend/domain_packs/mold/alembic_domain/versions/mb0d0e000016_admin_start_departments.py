"""add admin start department dispatch and receipt tracking

Revision ID: mb0d0e000016
Revises: mb0d0e000015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mb0d0e000016"
down_revision = "mb0d0e000015"
branch_labels = None
depends_on = None
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _identity_columns():
    return (
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade():
    op.create_table(
        "admin_start_department_ack",
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("department_key", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("notified_by", sa.String(length=36), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acked_by", sa.String(length=36), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ack_note", sa.Text(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["draft_id"], ["admin_start_notice_draft.id"]),
        sa.ForeignKeyConstraint(["notified_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["acked_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "department_key", name="admin_start_department_ack_unique"),
        sa.CheckConstraint(
            "status IN ('SENT','ACKNOWLEDGED')",
            name="admin_start_department_ack_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="admin_start_department_ack_row_version_positive"),
        sa.CheckConstraint(
            "(status = 'SENT' AND acked_at IS NULL AND acked_by IS NULL) "
            "OR (status = 'ACKNOWLEDGED' AND acked_at IS NOT NULL AND acked_by IS NOT NULL)",
            name="admin_start_department_ack_receipt_consistency",
        ),
    )
    op.create_index(
        "ix_admin_start_department_ack_draft_id",
        "admin_start_department_ack", ["draft_id"],
    )
    op.drop_constraint("admin_start_notice_draft_status", "admin_start_notice_draft", type_="check")
    op.create_check_constraint(
        "admin_start_notice_draft_status",
        "admin_start_notice_draft",
        "status IN ('ADMIN_PENDING_INPUT','ADMIN_CONFIRMED','DEPARTMENTS_NOTIFIED',"
        "'READY_FOR_CONTRACT_MATCH','REJECTED','NEEDS_REVIEW')",
    )


def downgrade():
    op.drop_constraint("admin_start_notice_draft_status", "admin_start_notice_draft", type_="check")
    op.create_check_constraint(
        "admin_start_notice_draft_status",
        "admin_start_notice_draft",
        "status IN ('ADMIN_PENDING_INPUT','ADMIN_CONFIRMED','READY_FOR_CONTRACT_MATCH','REJECTED','NEEDS_REVIEW')",
    )
    op.drop_index("ix_admin_start_department_ack_draft_id", table_name="admin_start_department_ack")
    op.drop_table("admin_start_department_ack")
