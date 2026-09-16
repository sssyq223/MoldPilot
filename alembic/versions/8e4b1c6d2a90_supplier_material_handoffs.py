"""supplier material handoff evidence

Revision ID: 8e4b1c6d2a90
Revises: 7d2a4c9e1b30
Create Date: 2026-09-16 14:35:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "8e4b1c6d2a90"
down_revision = "7d2a4c9e1b30"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "supplier_material_handoff",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("contract_subject_id", sa.String(length=36), nullable=True),
        sa.Column("file_id", sa.String(length=36), nullable=True),
        sa.Column("document_title", sa.String(length=200), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("approval_status", sa.String(length=30), nullable=False),
        sa.Column("provided_date", sa.Date(), nullable=False),
        sa.Column("provided_to", sa.String(length=150), nullable=False),
        sa.Column("handoff_channel", sa.String(length=40), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_system", sa.String(length=20), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=True),
        sa.Column("provided_by", sa.String(length=36), nullable=False),
        sa.Column("verified_by", sa.String(length=36), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("document_type IN ('CUSTOMER_MATERIAL','DESIGN_DRAWING','TECHNICAL_SPEC','QUALITY_STANDARD','OTHER')", name="supplier_material_handoff_document_type"),
        sa.CheckConstraint("approval_status IN ('DRAFT','APPROVED','REVOKED')", name="supplier_material_handoff_approval_status"),
        sa.CheckConstraint("handoff_channel IN ('MANUAL','EMAIL','IMPORT','ERP','OTHER')", name="supplier_material_handoff_channel"),
        sa.CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name="supplier_material_handoff_source_system"),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["file_object.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["provided_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.ForeignKeyConstraint(["verified_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "supplier_id", "document_title", "provided_date", "source_ref", name="supplier_material_handoff_unique_source"),
    )
    op.create_index(op.f("ix_supplier_material_handoff_project_id"), "supplier_material_handoff", ["project_id"], unique=False)
    op.create_index(op.f("ix_supplier_material_handoff_supplier_id"), "supplier_material_handoff", ["supplier_id"], unique=False)
    op.create_index(op.f("ix_supplier_material_handoff_contract_subject_id"), "supplier_material_handoff", ["contract_subject_id"], unique=False)
    op.create_index(op.f("ix_supplier_material_handoff_file_id"), "supplier_material_handoff", ["file_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_supplier_material_handoff_file_id"), table_name="supplier_material_handoff")
    op.drop_index(op.f("ix_supplier_material_handoff_contract_subject_id"), table_name="supplier_material_handoff")
    op.drop_index(op.f("ix_supplier_material_handoff_supplier_id"), table_name="supplier_material_handoff")
    op.drop_index(op.f("ix_supplier_material_handoff_project_id"), table_name="supplier_material_handoff")
    op.drop_table("supplier_material_handoff")
