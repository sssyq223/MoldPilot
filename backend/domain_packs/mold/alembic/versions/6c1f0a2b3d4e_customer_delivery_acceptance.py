"""customer delivery signatures and acceptance records

Revision ID: 6c1f0a2b3d4e
Revises: 4b9c2d7e8f10
Create Date: 2026-09-16 13:35:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "6c1f0a2b3d4e"
down_revision = "4b9c2d7e8f10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_delivery_signature",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("logistics_route_id", sa.String(length=36), nullable=True),
        sa.Column("shipment_reference", sa.String(length=120), nullable=False),
        sa.Column("signed_date", sa.Date(), nullable=False),
        sa.Column("signer_name", sa.String(length=120), nullable=False),
        sa.Column("sign_status", sa.String(length=30), nullable=False),
        sa.Column("move_type", sa.String(length=40), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sign_status IN ('SIGNED','REJECTED','PENDING')", name="customer_delivery_signature_status"),
        sa.CheckConstraint("move_type IN ('DELIVERY','MOLD_TRANSFER','RETURN','OTHER')", name="customer_delivery_signature_move_type"),
        sa.ForeignKeyConstraint(["logistics_route_id"], ["logistics_route.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "shipment_reference", "signed_date", name="customer_delivery_signature_unique_ref_date"),
    )
    op.create_index(op.f("ix_customer_delivery_signature_project_id"), "customer_delivery_signature", ["project_id"], unique=False)
    op.create_table(
        "customer_acceptance_record",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("signature_id", sa.String(length=36), nullable=True),
        sa.Column("acceptance_type", sa.String(length=30), nullable=False),
        sa.Column("result", sa.String(length=30), nullable=False),
        sa.Column("accepted_date", sa.Date(), nullable=False),
        sa.Column("issue_description", sa.Text(), nullable=False),
        sa.Column("responsibility", sa.String(length=40), nullable=False),
        sa.Column("corrective_due_date", sa.Date(), nullable=True),
        sa.Column("contact_case_id", sa.String(length=36), nullable=True),
        sa.Column("supplier_id", sa.String(length=36), nullable=True),
        sa.Column("deduction_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("schedule_impact_days", sa.Integer(), nullable=False),
        sa.Column("contract_change_required", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("acceptance_type IN ('INITIAL','RECHECK')", name="customer_acceptance_type"),
        sa.CheckConstraint("result IN ('PASSED','FAILED','CONDITIONALLY_PASSED')", name="customer_acceptance_result"),
        sa.CheckConstraint("responsibility IN ('CUSTOMER','SUPPLIER','INTERNAL','SHARED','UNKNOWN')", name="customer_acceptance_responsibility"),
        sa.CheckConstraint("deduction_amount IS NULL OR deduction_amount >= 0", name="customer_acceptance_deduction_nonnegative"),
        sa.CheckConstraint("schedule_impact_days >= 0", name="customer_acceptance_schedule_impact_nonnegative"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["contact_case_id"], ["contact_case.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["signature_id"], ["customer_delivery_signature.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_customer_acceptance_record_project_id"), "customer_acceptance_record", ["project_id"], unique=False)
    op.create_index(op.f("ix_customer_acceptance_record_signature_id"), "customer_acceptance_record", ["signature_id"], unique=False)
    op.create_index(op.f("ix_customer_acceptance_record_contact_case_id"), "customer_acceptance_record", ["contact_case_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_customer_acceptance_record_contact_case_id"), table_name="customer_acceptance_record")
    op.drop_index(op.f("ix_customer_acceptance_record_signature_id"), table_name="customer_acceptance_record")
    op.drop_index(op.f("ix_customer_acceptance_record_project_id"), table_name="customer_acceptance_record")
    op.drop_table("customer_acceptance_record")
    op.drop_index(op.f("ix_customer_delivery_signature_project_id"), table_name="customer_delivery_signature")
    op.drop_table("customer_delivery_signature")
