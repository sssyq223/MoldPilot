"""supplier progress reports for full outsource context

Revision ID: 7d2a4c9e1b30
Revises: 6c1f0a2b3d4e
Create Date: 2026-09-16 14:05:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "7d2a4c9e1b30"
down_revision = "6c1f0a2b3d4e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "supplier_progress_report",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("contract_subject_id", sa.String(length=36), nullable=True),
        sa.Column("plan_task_id", sa.String(length=36), nullable=True),
        sa.Column("stage_key", sa.String(length=80), nullable=False),
        sa.Column("stage_name", sa.String(length=150), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=True),
        sa.Column("next_due_date", sa.Date(), nullable=True),
        sa.Column("issue_summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_system", sa.String(length=20), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=True),
        sa.Column("reported_by", sa.String(length=36), nullable=False),
        sa.Column("followed_by", sa.String(length=36), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('ON_TRACK','AT_RISK','BLOCKED','DONE','REWORK')", name="supplier_progress_report_status"),
        sa.CheckConstraint("progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100", name="supplier_progress_report_progress_range"),
        sa.CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name="supplier_progress_report_source_system"),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["followed_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["plan_task_id"], ["plan_task.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["reported_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "supplier_id", "stage_key", "report_date", "source_ref", name="supplier_progress_report_unique_source"),
    )
    op.create_index(op.f("ix_supplier_progress_report_project_id"), "supplier_progress_report", ["project_id"], unique=False)
    op.create_index(op.f("ix_supplier_progress_report_supplier_id"), "supplier_progress_report", ["supplier_id"], unique=False)
    op.create_index(op.f("ix_supplier_progress_report_contract_subject_id"), "supplier_progress_report", ["contract_subject_id"], unique=False)
    op.create_index(op.f("ix_supplier_progress_report_plan_task_id"), "supplier_progress_report", ["plan_task_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_supplier_progress_report_plan_task_id"), table_name="supplier_progress_report")
    op.drop_index(op.f("ix_supplier_progress_report_contract_subject_id"), table_name="supplier_progress_report")
    op.drop_index(op.f("ix_supplier_progress_report_supplier_id"), table_name="supplier_progress_report")
    op.drop_index(op.f("ix_supplier_progress_report_project_id"), table_name="supplier_progress_report")
    op.drop_table("supplier_progress_report")
