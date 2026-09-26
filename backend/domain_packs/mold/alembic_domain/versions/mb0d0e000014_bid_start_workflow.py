"""add bid to start notice workflow facts

Revision ID: mb0d0e000014
Revises: mb0d0e000013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mb0d0e000014"
down_revision = "mb0d0e000013"
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
        "bid_notice_match",
        sa.Column("confirmation_event_id", sa.String(length=36), nullable=False),
        sa.Column("source_file_id", sa.String(length=36), nullable=False),
        sa.Column("inbound_record_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("project_version", sa.Integer(), nullable=True),
        sa.Column("bid_intake_case_id", sa.String(length=36), nullable=True),
        sa.Column("bid_intake_revision_id", sa.String(length=36), nullable=True),
        sa.Column("evidence_snapshot", JSON_TYPE, nullable=False),
        sa.Column("match_evidence", sa.Text(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("matched_by", sa.String(length=36), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint(
            "status IN ('PENDING_MATCH','MATCHED','INTAKE_CONFIRMED','REJECTED')",
            name="bid_notice_match_status",
        ),
        sa.CheckConstraint(
            "project_id IS NULL OR project_version IS NOT NULL",
            name="bid_notice_match_project_version",
        ),
        sa.CheckConstraint("row_version >= 1", name="bid_notice_match_row_version_positive"),
        sa.ForeignKeyConstraint(["confirmation_event_id"], ["audit_event.id"]),
        sa.ForeignKeyConstraint(["source_file_id"], ["file_object.id"]),
        sa.ForeignKeyConstraint(["inbound_record_id"], ["document_intake.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["bid_intake_case_id"], ["bid_intake_case.id"]),
        sa.ForeignKeyConstraint(["bid_intake_revision_id"], ["bid_intake_revision.id"]),
        sa.ForeignKeyConstraint(["matched_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("confirmation_event_id"),
    )
    op.create_index("ix_bid_notice_match_source_file_id", "bid_notice_match", ["source_file_id"])
    op.create_index("ix_bid_notice_match_inbound_record_id", "bid_notice_match", ["inbound_record_id"])
    op.create_index("ix_bid_notice_match_project_id", "bid_notice_match", ["project_id"])

    op.create_table(
        "start_notice",
        sa.Column("bid_match_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("bid_intake_revision_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=35), nullable=False),
        sa.Column("material_snapshot", JSON_TYPE, nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("version >= 1", name="start_notice_version_positive"),
        sa.CheckConstraint(
            "status IN ('DRAFT','DEPARTMENT_REVIEW','PROJECT_ACCEPTED','FULL_OUTSOURCE_ACCEPTED','REJECTED','RETURNED')",
            name="start_notice_status",
        ),
        sa.ForeignKeyConstraint(["bid_match_id"], ["bid_notice_match.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["bid_intake_revision_id"], ["bid_intake_revision.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bid_match_id", "version", name="start_notice_match_version"),
    )
    op.create_index("ix_start_notice_bid_match_id", "start_notice", ["bid_match_id"])
    op.create_index("ix_start_notice_project_id", "start_notice", ["project_id"])

    op.create_table(
        "start_notice_department_ack",
        sa.Column("start_notice_id", sa.String(length=36), nullable=False),
        sa.Column("start_notice_version", sa.Integer(), nullable=False),
        sa.Column("department_key", sa.String(length=60), nullable=False),
        sa.Column("department_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("handoff_status", sa.String(length=20), nullable=False),
        sa.Column("recipient_snapshot", JSON_TYPE, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("responded_by", sa.String(length=36), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint("start_notice_version >= 1", name="start_notice_ack_version_positive"),
        sa.CheckConstraint(
            "status IN ('PENDING','ACCEPTED','RETURNED','NEED_INFO')",
            name="start_notice_department_ack_status",
        ),
        sa.CheckConstraint(
            "handoff_status IN ('QUEUED','UNASSIGNED')",
            name="start_notice_department_handoff_status",
        ),
        sa.CheckConstraint(
            "(handoff_status = 'QUEUED' AND event_id IS NOT NULL) OR "
            "(handoff_status = 'UNASSIGNED' AND event_id IS NULL)",
            name="start_notice_department_handoff_event",
        ),
        sa.ForeignKeyConstraint(["start_notice_id"], ["start_notice.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["outbox_event.id"]),
        sa.ForeignKeyConstraint(["responded_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "start_notice_id", "start_notice_version", "department_key",
            name="start_notice_department_ack_unique",
        ),
    )
    op.create_index("ix_start_notice_department_ack_notice", "start_notice_department_ack", ["start_notice_id"])

    op.create_table(
        "project_start_decision",
        sa.Column("start_notice_id", sa.String(length=36), nullable=False),
        sa.Column("start_notice_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=35), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("department_snapshot", JSON_TYPE, nullable=False),
        sa.Column("decided_by", sa.String(length=36), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("start_notice_version >= 1", name="project_start_decision_version_positive"),
        sa.CheckConstraint(
            "decision IN ('PROJECT_ACCEPTED','FULL_OUTSOURCE_ACCEPTED','REJECTED','RETURNED')",
            name="project_start_decision_value",
        ),
        sa.ForeignKeyConstraint(["start_notice_id"], ["start_notice.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "start_notice_id", "start_notice_version", name="project_start_decision_notice_version",
        ),
    )
    op.create_index("ix_project_start_decision_notice", "project_start_decision", ["start_notice_id"])

    op.create_table(
        "post_start_binding",
        sa.Column("project_decision_id", sa.String(length=36), nullable=False),
        sa.Column("start_notice_id", sa.String(length=36), nullable=False),
        sa.Column("start_notice_version", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=30), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("target_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("mold_snapshot", JSON_TYPE, nullable=False),
        sa.Column("bound_by", sa.String(length=36), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("start_notice_version >= 1", name="post_start_binding_notice_version_positive"),
        sa.CheckConstraint("target_version >= 1", name="post_start_binding_target_version_positive"),
        sa.CheckConstraint(
            "target_type IN ('SALES_CONTRACT','ACCOUNTING_CHECKLIST')",
            name="post_start_binding_target_type",
        ),
        sa.ForeignKeyConstraint(["project_decision_id"], ["project_start_decision.id"]),
        sa.ForeignKeyConstraint(["start_notice_id"], ["start_notice.id"]),
        sa.ForeignKeyConstraint(["bound_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "target_type", "target_id", "target_version", name="post_start_binding_target_version",
        ),
    )
    op.create_index("ix_post_start_binding_decision", "post_start_binding", ["project_decision_id"])
    op.create_index("ix_post_start_binding_notice", "post_start_binding", ["start_notice_id"])


def downgrade():
    op.drop_index("ix_post_start_binding_notice", table_name="post_start_binding")
    op.drop_index("ix_post_start_binding_decision", table_name="post_start_binding")
    op.drop_table("post_start_binding")
    op.drop_index("ix_project_start_decision_notice", table_name="project_start_decision")
    op.drop_table("project_start_decision")
    op.drop_index("ix_start_notice_department_ack_notice", table_name="start_notice_department_ack")
    op.drop_table("start_notice_department_ack")
    op.drop_index("ix_start_notice_project_id", table_name="start_notice")
    op.drop_index("ix_start_notice_bid_match_id", table_name="start_notice")
    op.drop_table("start_notice")
    op.drop_index("ix_bid_notice_match_project_id", table_name="bid_notice_match")
    op.drop_index("ix_bid_notice_match_inbound_record_id", table_name="bid_notice_match")
    op.drop_index("ix_bid_notice_match_source_file_id", table_name="bid_notice_match")
    op.drop_table("bid_notice_match")
