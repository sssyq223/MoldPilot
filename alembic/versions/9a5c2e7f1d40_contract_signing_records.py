"""contract signing records

Revision ID: 9a5c2e7f1d40
Revises: 8e4b1c6d2a90
Create Date: 2026-09-16 15:05:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "9a5c2e7f1d40"
down_revision = "8e4b1c6d2a90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contract_signing_record",
        sa.Column("contract_subject_id", sa.String(length=36), nullable=False),
        sa.Column("template_name", sa.String(length=150), nullable=False),
        sa.Column("signing_method", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("signed_date", sa.Date(), nullable=True),
        sa.Column("signed_file_id", sa.String(length=36), nullable=True),
        sa.Column("signed_file_title", sa.String(length=200), nullable=False),
        sa.Column("supplier_signer", sa.String(length=120), nullable=False),
        sa.Column("buyer_reviewer_id", sa.String(length=36), nullable=True),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_system", sa.String(length=20), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=True),
        sa.Column("recorded_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("signing_method IN ('MANUAL','OFFLINE_FILE','IMPORT','ERP','OTHER')", name="contract_signing_method"),
        sa.CheckConstraint("status IN ('DRAFT','UNDER_REVIEW','SIGNED','REJECTED','CANCELLED')", name="contract_signing_status"),
        sa.CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name="contract_signing_source_system"),
        sa.ForeignKeyConstraint(["approved_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["buyer_reviewer_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["signed_file_id"], ["file_object.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_subject_id", "status", "source_ref", name="contract_signing_record_unique_source"),
    )
    op.create_index(op.f("ix_contract_signing_record_contract_subject_id"), "contract_signing_record", ["contract_subject_id"], unique=False)
    op.create_index(op.f("ix_contract_signing_record_signed_file_id"), "contract_signing_record", ["signed_file_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_contract_signing_record_signed_file_id"), table_name="contract_signing_record")
    op.drop_index(op.f("ix_contract_signing_record_contract_subject_id"), table_name="contract_signing_record")
    op.drop_table("contract_signing_record")
