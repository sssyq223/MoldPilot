"""supplier progress reporting policies and structured evidence

Revision ID: d4f6a8c2e913
Revises: c9d2e5f7a814
Create Date: 2026-09-17 12:45:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d4f6a8c2e913"
down_revision = "c9d2e5f7a814"
branch_labels = None
depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
    op.add_column(
        "supplier_progress_report",
        sa.Column("evidence_items", json_type, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_table(
        "supplier_progress_policy",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("contract_subject_id", sa.String(length=36), nullable=False),
        sa.Column("plan_task_id", sa.String(length=36), nullable=True),
        sa.Column("stage_key", sa.String(length=80), nullable=False),
        sa.Column("stage_name", sa.String(length=150), nullable=False),
        sa.Column("frequency_days", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("first_due_date", sa.Date(), nullable=False),
        sa.Column("evidence_requirements", json_type, nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("frequency_days BETWEEN 1 AND 90", name="supplier_progress_policy_frequency"),
        sa.CheckConstraint("version >= 1", name="supplier_progress_policy_version_positive"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["plan_task_id"], ["plan_task.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["supplier_progress_policy.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "supplier_id", "contract_subject_id", "stage_key", "version", name="supplier_progress_policy_version"),
        sa.UniqueConstraint("project_id", "supplier_id", "contract_subject_id", "stage_key", "source_ref", name="supplier_progress_policy_unique_source"),
    )
    op.create_index(op.f("ix_supplier_progress_policy_project_id"), "supplier_progress_policy", ["project_id"], unique=False)
    op.create_index(op.f("ix_supplier_progress_policy_supplier_id"), "supplier_progress_policy", ["supplier_id"], unique=False)
    op.create_index(op.f("ix_supplier_progress_policy_contract_subject_id"), "supplier_progress_policy", ["contract_subject_id"], unique=False)
    op.create_index(op.f("ix_supplier_progress_policy_plan_task_id"), "supplier_progress_policy", ["plan_task_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_supplier_progress_policy_plan_task_id"), table_name="supplier_progress_policy")
    op.drop_index(op.f("ix_supplier_progress_policy_contract_subject_id"), table_name="supplier_progress_policy")
    op.drop_index(op.f("ix_supplier_progress_policy_supplier_id"), table_name="supplier_progress_policy")
    op.drop_index(op.f("ix_supplier_progress_policy_project_id"), table_name="supplier_progress_policy")
    op.drop_table("supplier_progress_policy")
    op.drop_column("supplier_progress_report", "evidence_items")
