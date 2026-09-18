"""supplier deduction settlements

Revision ID: a6d3e8f4b120
Revises: 9a5c2e7f1d40
Create Date: 2026-09-16 15:35:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "a6d3e8f4b120"
down_revision = "9a5c2e7f1d40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "supplier_deduction_settlement",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("contract_subject_id", sa.String(length=36), nullable=True),
        sa.Column("contact_case_id", sa.String(length=36), nullable=True),
        sa.Column("contact_task_id", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("responsibility", sa.String(length=40), nullable=False),
        sa.Column("deduction_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("settlement_reference", sa.String(length=120), nullable=True),
        sa.Column("responsibility_evidence", sa.Text(), nullable=False),
        sa.Column("settlement_evidence", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("settled_by", sa.String(length=36), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_system", sa.String(length=20), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("responsibility IN ('CUSTOMER','SUPPLIER','INTERNAL','SHARED','UNKNOWN')", name="supplier_deduction_responsibility"),
        sa.CheckConstraint("status IN ('PROPOSED','RESPONSIBILITY_CONFIRMED','SETTLED','CANCELLED')", name="supplier_deduction_status"),
        sa.CheckConstraint("deduction_amount >= 0", name="supplier_deduction_nonnegative"),
        sa.CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name="supplier_deduction_source_system"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["contact_case_id"], ["contact_case.id"]),
        sa.ForeignKeyConstraint(["contact_task_id"], ["contact_task.id"]),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["settled_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "supplier_id", "reason", "source_ref", name="supplier_deduction_settlement_unique_source"),
    )
    op.create_index(op.f("ix_supplier_deduction_settlement_project_id"), "supplier_deduction_settlement", ["project_id"], unique=False)
    op.create_index(op.f("ix_supplier_deduction_settlement_supplier_id"), "supplier_deduction_settlement", ["supplier_id"], unique=False)
    op.create_index(op.f("ix_supplier_deduction_settlement_contract_subject_id"), "supplier_deduction_settlement", ["contract_subject_id"], unique=False)
    op.create_index(op.f("ix_supplier_deduction_settlement_contact_case_id"), "supplier_deduction_settlement", ["contact_case_id"], unique=False)
    op.create_index(op.f("ix_supplier_deduction_settlement_contact_task_id"), "supplier_deduction_settlement", ["contact_task_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_supplier_deduction_settlement_contact_task_id"), table_name="supplier_deduction_settlement")
    op.drop_index(op.f("ix_supplier_deduction_settlement_contact_case_id"), table_name="supplier_deduction_settlement")
    op.drop_index(op.f("ix_supplier_deduction_settlement_contract_subject_id"), table_name="supplier_deduction_settlement")
    op.drop_index(op.f("ix_supplier_deduction_settlement_supplier_id"), table_name="supplier_deduction_settlement")
    op.drop_index(op.f("ix_supplier_deduction_settlement_project_id"), table_name="supplier_deduction_settlement")
    op.drop_table("supplier_deduction_settlement")
