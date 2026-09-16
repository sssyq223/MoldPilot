"""outsource change negotiations

Revision ID: b7e4f2a9c310
Revises: a6d3e8f4b120
Create Date: 2026-09-16 16:05:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "b7e4f2a9c310"
down_revision = "a6d3e8f4b120"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outsource_change_negotiation",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("contract_subject_id", sa.String(length=36), nullable=True),
        sa.Column("contact_case_id", sa.String(length=36), nullable=True),
        sa.Column("contact_task_id", sa.String(length=36), nullable=True),
        sa.Column("customer_quote_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("supplier_quote_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("negotiated_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("schedule_impact_days", sa.Integer(), nullable=False),
        sa.Column("task_impact_summary", sa.Text(), nullable=False),
        sa.Column("requires_contract_change", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("customer_evidence", sa.Text(), nullable=False),
        sa.Column("supplier_evidence", sa.Text(), nullable=False),
        sa.Column("negotiation_evidence", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("source_system", sa.String(length=20), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("customer_quote_amount IS NULL OR customer_quote_amount >= 0", name="outsource_change_customer_amount_nonnegative"),
        sa.CheckConstraint("supplier_quote_amount IS NULL OR supplier_quote_amount >= 0", name="outsource_change_supplier_amount_nonnegative"),
        sa.CheckConstraint("negotiated_amount IS NULL OR negotiated_amount >= 0", name="outsource_change_negotiated_amount_nonnegative"),
        sa.CheckConstraint("schedule_impact_days >= 0", name="outsource_change_schedule_impact_nonnegative"),
        sa.CheckConstraint("status IN ('DRAFT','NEGOTIATING','AGREED','APPROVED','CANCELLED')", name="outsource_change_negotiation_status"),
        sa.CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name="outsource_change_negotiation_source_system"),
        sa.ForeignKeyConstraint(["approved_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["contact_case_id"], ["contact_case.id"]),
        sa.ForeignKeyConstraint(["contact_task_id"], ["contact_task.id"]),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "supplier_id", "source_ref", name="outsource_change_negotiation_unique_source"),
    )
    op.create_index(op.f("ix_outsource_change_negotiation_project_id"), "outsource_change_negotiation", ["project_id"], unique=False)
    op.create_index(op.f("ix_outsource_change_negotiation_supplier_id"), "outsource_change_negotiation", ["supplier_id"], unique=False)
    op.create_index(op.f("ix_outsource_change_negotiation_contract_subject_id"), "outsource_change_negotiation", ["contract_subject_id"], unique=False)
    op.create_index(op.f("ix_outsource_change_negotiation_contact_case_id"), "outsource_change_negotiation", ["contact_case_id"], unique=False)
    op.create_index(op.f("ix_outsource_change_negotiation_contact_task_id"), "outsource_change_negotiation", ["contact_task_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_outsource_change_negotiation_contact_task_id"), table_name="outsource_change_negotiation")
    op.drop_index(op.f("ix_outsource_change_negotiation_contact_case_id"), table_name="outsource_change_negotiation")
    op.drop_index(op.f("ix_outsource_change_negotiation_contract_subject_id"), table_name="outsource_change_negotiation")
    op.drop_index(op.f("ix_outsource_change_negotiation_supplier_id"), table_name="outsource_change_negotiation")
    op.drop_index(op.f("ix_outsource_change_negotiation_project_id"), table_name="outsource_change_negotiation")
    op.drop_table("outsource_change_negotiation")
