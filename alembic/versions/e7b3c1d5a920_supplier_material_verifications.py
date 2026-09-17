"""supplier material verification evidence

Revision ID: e7b3c1d5a920
Revises: d4f6a8c2e913
Create Date: 2026-09-17 14:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "e7b3c1d5a920"
down_revision = "d4f6a8c2e913"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "supplier_material_verification",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("contract_subject_id", sa.String(length=36), nullable=False),
        sa.Column("handoff_id", sa.String(length=36), nullable=False),
        sa.Column("response_file_id", sa.String(length=36), nullable=True),
        sa.Column("response_date", sa.Date(), nullable=False),
        sa.Column("result", sa.String(length=30), nullable=False),
        sa.Column("supplier_contact", sa.String(length=150), nullable=False),
        sa.Column("response_channel", sa.String(length=40), nullable=False),
        sa.Column("response_summary", sa.Text(), nullable=False),
        sa.Column("follow_up_due_date", sa.Date(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_system", sa.String(length=20), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("recorded_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("result IN ('RECEIVED','ACCEPTED','NEEDS_CLARIFICATION','REJECTED')", name="supplier_material_verification_result"),
        sa.CheckConstraint("response_channel IN ('MANUAL','EMAIL','IMPORT','ERP','OTHER')", name="supplier_material_verification_channel"),
        sa.CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name="supplier_material_verification_source_system"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["handoff_id"], ["supplier_material_handoff.id"]),
        sa.ForeignKeyConstraint(["response_file_id"], ["file_object.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("handoff_id", "source_ref", name="supplier_material_verification_unique_source"),
    )
    op.create_index(op.f("ix_supplier_material_verification_project_id"), "supplier_material_verification", ["project_id"], unique=False)
    op.create_index(op.f("ix_supplier_material_verification_supplier_id"), "supplier_material_verification", ["supplier_id"], unique=False)
    op.create_index(op.f("ix_supplier_material_verification_contract_subject_id"), "supplier_material_verification", ["contract_subject_id"], unique=False)
    op.create_index(op.f("ix_supplier_material_verification_handoff_id"), "supplier_material_verification", ["handoff_id"], unique=False)
    op.create_index(op.f("ix_supplier_material_verification_response_file_id"), "supplier_material_verification", ["response_file_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_supplier_material_verification_response_file_id"), table_name="supplier_material_verification")
    op.drop_index(op.f("ix_supplier_material_verification_handoff_id"), table_name="supplier_material_verification")
    op.drop_index(op.f("ix_supplier_material_verification_contract_subject_id"), table_name="supplier_material_verification")
    op.drop_index(op.f("ix_supplier_material_verification_supplier_id"), table_name="supplier_material_verification")
    op.drop_index(op.f("ix_supplier_material_verification_project_id"), table_name="supplier_material_verification")
    op.drop_table("supplier_material_verification")
