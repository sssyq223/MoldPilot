"""add administrator start notice drafts and contract candidates

Revision ID: mb0d0e000015
Revises: mb0d0e000014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mb0d0e000015"
down_revision = "mb0d0e000014"
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
        "admin_start_notice_draft",
        sa.Column("source_event_id", sa.String(length=36), nullable=False),
        sa.Column("source_file_id", sa.String(length=36), nullable=False),
        sa.Column("inbound_record_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("project_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=35), nullable=False),
        sa.Column("decision", sa.String(length=35), nullable=True),
        sa.Column("material_snapshot", JSON_TYPE, nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["source_event_id"], ["audit_event.id"]),
        sa.ForeignKeyConstraint(["source_file_id"], ["file_object.id"]),
        sa.ForeignKeyConstraint(["inbound_record_id"], ["document_intake.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_event_id", name="admin_start_notice_source_event"),
        sa.CheckConstraint(
            "status IN ('ADMIN_PENDING_INPUT','ADMIN_CONFIRMED','READY_FOR_CONTRACT_MATCH','REJECTED','NEEDS_REVIEW')",
            name="admin_start_notice_draft_status",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('INTERNAL_ACCEPTED','FULL_OUTSOURCE_ACCEPTED','REJECTED')",
            name="admin_start_notice_decision",
        ),
        sa.CheckConstraint("current_revision >= 1", name="admin_start_notice_revision_positive"),
        sa.CheckConstraint("row_version >= 1", name="admin_start_notice_row_version_positive"),
        sa.CheckConstraint(
            "project_id IS NULL OR project_version IS NOT NULL",
            name="admin_start_notice_project_version",
        ),
    )
    op.create_index("ix_admin_start_notice_draft_source_file_id", "admin_start_notice_draft", ["source_file_id"])
    op.create_index("ix_admin_start_notice_draft_inbound_record_id", "admin_start_notice_draft", ["inbound_record_id"])
    op.create_index("ix_admin_start_notice_draft_project_id", "admin_start_notice_draft", ["project_id"])

    op.create_table(
        "admin_start_notice_revision",
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", JSON_TYPE, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["draft_id"], ["admin_start_notice_draft.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "revision", name="admin_start_notice_revision_unique"),
        sa.CheckConstraint("revision >= 1", name="admin_start_notice_revision_number_positive"),
    )
    op.create_index("ix_admin_start_notice_revision_draft_id", "admin_start_notice_revision", ["draft_id"])

    op.create_table(
        "start_contract_match_candidate",
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("target_type", sa.String(length=35), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("evidence_snapshot", JSON_TYPE, nullable=False),
        sa.Column("proposed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["draft_id"], ["admin_start_notice_draft.id"]),
        sa.ForeignKeyConstraint(["proposed_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("target_fingerprint", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "draft_id", "draft_revision", "target_type", "target_id", "target_version", "target_fingerprint",
            name="start_contract_match_candidate_unique",
        ),
        sa.CheckConstraint(
            "target_type IN ('SALES_CONTRACT','FULL_OUTSOURCE_CONTRACT','CONTRACT_INTAKE_GROUP')",
            name="start_contract_match_candidate_target_type",
        ),
        sa.CheckConstraint(
            "status IN ('PROPOSED','CONFIRMED','REJECTED')",
            name="start_contract_match_candidate_status",
        ),
        sa.CheckConstraint("target_version >= 1", name="start_contract_match_candidate_version_positive"),
        sa.CheckConstraint("draft_revision >= 1", name="start_contract_match_candidate_revision_positive"),
    )
    op.create_index("ix_start_contract_match_candidate_draft_id", "start_contract_match_candidate", ["draft_id"])


def downgrade():
    op.drop_index("ix_start_contract_match_candidate_draft_id", table_name="start_contract_match_candidate")
    op.drop_table("start_contract_match_candidate")
    op.drop_index("ix_admin_start_notice_revision_draft_id", table_name="admin_start_notice_revision")
    op.drop_table("admin_start_notice_revision")
    op.drop_index("ix_admin_start_notice_draft_project_id", table_name="admin_start_notice_draft")
    op.drop_index("ix_admin_start_notice_draft_inbound_record_id", table_name="admin_start_notice_draft")
    op.drop_index("ix_admin_start_notice_draft_source_file_id", table_name="admin_start_notice_draft")
    op.drop_table("admin_start_notice_draft")
