"""customer receipt confirmations

Revision ID: c9d2e5f7a814
Revises: b7e4f2a9c310
Create Date: 2026-09-16 14:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c9d2e5f7a814"
down_revision = "b7e4f2a9c310"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_receipt_confirmation",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("contract_subject_id", sa.String(length=36), nullable=False),
        sa.Column("stage_id", sa.String(length=36), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=20), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="customer_receipt_amount_positive"),
        sa.CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name="customer_receipt_source_system"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["stage_id"], ["payment_stage.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
        sa.UniqueConstraint("project_id", "source_system", "source_ref", name="customer_receipt_unique_source"),
    )
    op.create_index(op.f("ix_customer_receipt_confirmation_project_id"), "customer_receipt_confirmation", ["project_id"], unique=False)
    op.create_index(op.f("ix_customer_receipt_confirmation_contract_subject_id"), "customer_receipt_confirmation", ["contract_subject_id"], unique=False)
    op.create_index(op.f("ix_customer_receipt_confirmation_stage_id"), "customer_receipt_confirmation", ["stage_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_customer_receipt_confirmation_stage_id"), table_name="customer_receipt_confirmation")
    op.drop_index(op.f("ix_customer_receipt_confirmation_contract_subject_id"), table_name="customer_receipt_confirmation")
    op.drop_index(op.f("ix_customer_receipt_confirmation_project_id"), table_name="customer_receipt_confirmation")
    op.drop_table("customer_receipt_confirmation")
