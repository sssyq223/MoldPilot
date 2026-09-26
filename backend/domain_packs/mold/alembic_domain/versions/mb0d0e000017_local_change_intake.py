"""add local engineering change intake records

Revision ID: mb0d0e000017
Revises: mb0d0e000016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mb0d0e000017"
down_revision = "mb0d0e000016"
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
        "local_change_intake",
        sa.Column("number", sa.String(length=80), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("original_mold_id", sa.String(length=36), nullable=True),
        sa.Column("mold_mode", sa.String(length=20), nullable=False),
        sa.Column("customer_mold_number", sa.String(length=160), nullable=True),
        sa.Column("classification", sa.String(length=20), nullable=False),
        sa.Column("execution_mode", sa.String(length=20), nullable=False),
        sa.Column("charge_status", sa.String(length=20), nullable=False),
        sa.Column("contract_status", sa.String(length=20), nullable=False),
        sa.Column("execution_scope", sa.Text(), nullable=False),
        sa.Column("customer_basis", sa.Text(), nullable=False),
        sa.Column("acceptance_status", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("request_key", sa.String(length=36), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("latest_snapshot", JSON_TYPE, nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["original_mold_id"], ["mold.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("created_by", "request_key", name="uq_local_change_intake_request"),
        sa.CheckConstraint("mold_mode IN ('EXISTING','NEW_EXTERNAL')", name="local_change_mold_mode"),
        sa.CheckConstraint(
            "(mold_mode = 'EXISTING' AND original_mold_id IS NOT NULL) OR "
            "(mold_mode = 'NEW_EXTERNAL' AND original_mold_id IS NULL)",
            name="local_change_mold_reference",
        ),
        sa.CheckConstraint("classification IN ('CUSTOMER','INTERNAL','OUTSOURCE')", name="local_change_classification"),
        sa.CheckConstraint("execution_mode IN ('INTERNAL','OUTSOURCE')", name="local_change_execution_mode"),
        sa.CheckConstraint("charge_status IN ('CHARGED','FREE','PENDING')", name="local_change_charge_status"),
        sa.CheckConstraint("contract_status IN ('NONE','REQUIRED','AVAILABLE','PENDING')", name="local_change_contract_status"),
        sa.CheckConstraint("acceptance_status IN ('PENDING','ACCEPTED','REJECTED')", name="local_change_acceptance_status"),
    )
    op.create_index("ix_local_change_intake_project_id", "local_change_intake", ["project_id"])
    op.create_index("ix_local_change_intake_original_mold_id", "local_change_intake", ["original_mold_id"])
    op.create_index("ix_local_change_intake_number", "local_change_intake", ["number"], unique=True)

    op.create_table(
        "local_change_customer_mold_history",
        sa.Column("change_id", sa.String(length=36), nullable=False),
        sa.Column("customer_mold_number", sa.String(length=160), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("recorded_by", sa.String(length=36), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["change_id"], ["local_change_intake.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_local_change_customer_mold_history_change_id",
        "local_change_customer_mold_history",
        ["change_id"],
    )

    op.create_table(
        "local_change_association",
        sa.Column("change_id", sa.String(length=36), nullable=False),
        sa.Column("association_type", sa.String(length=30), nullable=False),
        sa.Column("target_id", sa.String(length=80), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["change_id"], ["local_change_intake.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "association_type IN ('PROJECT','MOLD','CONTRACT','DOCUMENT')",
            name="local_change_association_type",
        ),
    )
    op.create_index("ix_local_change_association_change_id", "local_change_association", ["change_id"])


def downgrade():
    op.drop_index("ix_local_change_association_change_id", table_name="local_change_association")
    op.drop_table("local_change_association")
    op.drop_index(
        "ix_local_change_customer_mold_history_change_id",
        table_name="local_change_customer_mold_history",
    )
    op.drop_table("local_change_customer_mold_history")
    op.drop_index("ix_local_change_intake_number", table_name="local_change_intake")
    op.drop_index("ix_local_change_intake_original_mold_id", table_name="local_change_intake")
    op.drop_index("ix_local_change_intake_project_id", table_name="local_change_intake")
    op.drop_table("local_change_intake")
