"""add sales contract PDF intake and OCR review

Revision ID: mb0d0e000013
Revises: mb0d0e000012
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mb0d0e000013"
down_revision = "mb0d0e000012"
branch_labels = None
depends_on = None


JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _identity_columns():
    return (
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade():
    op.add_column("contract_detail", sa.Column("signed_date", sa.Date(), nullable=True))
    op.add_column("contract_detail", sa.Column("external_order_number", sa.String(length=120), nullable=True))
    op.add_column("payment_stage", sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("payment_stage", sa.Column("ratio", sa.Numeric(precision=9, scale=6), nullable=True))
    op.add_column("payment_stage", sa.Column("term_days", sa.Integer(), nullable=True))
    op.create_check_constraint("payment_stage_sequence", "payment_stage", "sequence >= 1")
    op.drop_constraint("payment_stage_amount_check", "payment_stage", type_="check")
    op.create_check_constraint("payment_stage_amount_positive", "payment_stage", "amount > 0")
    op.create_check_constraint("payment_stage_ratio", "payment_stage", "ratio IS NULL OR (ratio > 0 AND ratio <= 1)")
    op.create_check_constraint("payment_stage_term_days", "payment_stage", "term_days IS NULL OR term_days >= 0")

    op.create_table(
        "document_intake",
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("request_key", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "status IN ('UPLOADED','PRECLASSIFYING','AWAITING_TYPE_CONFIRMATION','CLASSIFIED_ARCHIVED','FULL_OCR_QUEUED','FULL_OCR_PROCESSING','OCR_FAILED','AWAITING_FIELD_CONFIRMATION','READY_FOR_DRAFT','CONTRACT_DRAFT_CREATED')",
            name="document_intake_status",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversation.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("created_by", "request_key", name="uq_document_intake_request"),
    )
    op.create_index("ix_document_intake_conversation_id", "document_intake", ["conversation_id"])
    op.create_index("ix_document_intake_created_by", "document_intake", ["created_by"])

    op.create_table(
        "contract_intake_group",
        sa.Column("intake_id", sa.String(length=36), nullable=False),
        sa.Column("group_key", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("project_version", sa.Integer(), nullable=True),
        sa.Column("customer_id", sa.String(length=36), nullable=True),
        sa.Column("contract_subject_id", sa.String(length=36), nullable=True),
        sa.Column("duplicate_of_contract_id", sa.String(length=36), nullable=True),
        sa.Column("relation_type", sa.String(length=30), nullable=True),
        sa.Column("relation_target_contract_id", sa.String(length=36), nullable=True),
        sa.Column("relation_reason", sa.Text(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_warnings", JSON_TYPE, nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "status IN ('UPLOADED','PRECLASSIFYING','AWAITING_TYPE_CONFIRMATION','CLASSIFIED_ARCHIVED','FULL_OCR_QUEUED','FULL_OCR_PROCESSING','OCR_FAILED','AWAITING_FIELD_CONFIRMATION','READY_FOR_DRAFT','CONTRACT_DRAFT_CREATED')",
            name="contract_intake_group_status",
        ),
        sa.CheckConstraint("project_version IS NULL OR project_version >= 1", name="contract_intake_project_version"),
        sa.CheckConstraint(
            "relation_type IS NULL OR relation_type IN ('NEW','DUPLICATE','REVISION','SUPPLEMENT','REPLACEMENT')",
            name="contract_intake_relation_type",
        ),
        sa.CheckConstraint(
            "(relation_type IS NULL) OR (relation_type = 'NEW' AND relation_target_contract_id IS NULL) OR (relation_type <> 'NEW' AND relation_target_contract_id IS NOT NULL)",
            name="contract_intake_relation_target",
        ),
        sa.ForeignKeyConstraint(["intake_id"], ["document_intake.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["duplicate_of_contract_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["relation_target_contract_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("intake_id", "group_key", name="uq_contract_intake_group_key"),
        sa.UniqueConstraint("contract_subject_id"),
    )
    op.create_index("ix_contract_intake_group_intake_id", "contract_intake_group", ["intake_id"])
    op.create_index("ix_contract_intake_group_project_id", "contract_intake_group", ["project_id"])

    op.create_table(
        "document_intake_file",
        sa.Column("intake_id", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("contract_group_id", sa.String(length=36), nullable=True),
        sa.Column("suggested_type", sa.String(length=40), nullable=True),
        sa.Column("suggested_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("confirmed_type", sa.String(length=40), nullable=True),
        sa.Column("confirmed_role", sa.String(length=30), nullable=True),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint(
            "suggested_type IS NULL OR suggested_type IN ('BID_NOTICE','CUSTOMER_START_NOTICE','SALES_CONTRACT','MOLD_DRAWING','OTHER')",
            name="document_suggested_type",
        ),
        sa.CheckConstraint(
            "confirmed_type IS NULL OR confirmed_type IN ('BID_NOTICE','CUSTOMER_START_NOTICE','SALES_CONTRACT','MOLD_DRAWING','OTHER')",
            name="document_confirmed_type",
        ),
        sa.CheckConstraint(
            "confirmed_role IS NULL OR confirmed_role IN ('MAIN','ATTACHMENT','STAMP_PAGE','PAYMENT_TERMS','OTHER')",
            name="document_confirmed_role",
        ),
        sa.CheckConstraint(
            "suggested_confidence IS NULL OR (suggested_confidence >= 0 AND suggested_confidence <= 1)",
            name="document_suggested_confidence",
        ),
        sa.ForeignKeyConstraint(["intake_id"], ["document_intake.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["file_object.id"]),
        sa.ForeignKeyConstraint(["contract_group_id"], ["contract_intake_group.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_id"),
    )
    op.create_index("ix_document_intake_file_intake_id", "document_intake_file", ["intake_id"])
    op.create_index("ix_document_intake_file_contract_group_id", "document_intake_file", ["contract_group_id"])

    op.create_table(
        "document_recognized_page",
        sa.Column("intake_file_id", sa.String(length=36), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("pipeline_version", sa.String(length=120), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("blocks", JSON_TYPE, nullable=False),
        sa.Column("average_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("page_number >= 1", name="document_recognized_page_number"),
        sa.CheckConstraint(
            "source_kind IN ('TEXT_LAYER','PADDLE_OCR','HYBRID')",
            name="document_recognized_page_source",
        ),
        sa.CheckConstraint(
            "average_confidence IS NULL OR (average_confidence >= 0 AND average_confidence <= 1)",
            name="document_recognized_page_confidence",
        ),
        sa.ForeignKeyConstraint(["intake_file_id"], ["document_intake_file.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "intake_file_id", "page_number", "source_sha256", "pipeline_version",
            name="uq_document_recognized_page_version",
        ),
    )
    op.create_index(
        "ix_document_recognized_page_intake_file_id",
        "document_recognized_page",
        ["intake_file_id"],
    )

    op.create_table(
        "document_ocr_job",
        sa.Column("intake_file_id", sa.String(length=36), nullable=False),
        sa.Column("phase", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("last_error", sa.String(length=120), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint("phase IN ('PRECLASSIFY','FULL_CONTRACT')", name="document_ocr_phase"),
        sa.CheckConstraint("status IN ('QUEUED','PROCESSING','RETRY_WAIT','SUCCEEDED','FAILED')", name="document_ocr_status"),
        sa.CheckConstraint("attempts >= 0", name="document_ocr_attempts"),
        sa.ForeignKeyConstraint(["intake_file_id"], ["document_intake_file.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("intake_file_id", "phase", name="uq_document_ocr_job_phase"),
    )
    op.create_index("ix_document_ocr_job_intake_file_id", "document_ocr_job", ["intake_file_id"])
    op.create_index("ix_document_ocr_job_status", "document_ocr_job", ["status"])
    op.create_index("ix_document_ocr_claim", "document_ocr_job", ["status", "retry_at", "lease_until"])

    op.create_table(
        "document_extracted_field",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("row_key", sa.String(length=80), nullable=False),
        sa.Column("field_key", sa.String(length=80), nullable=False),
        sa.Column("raw_value", JSON_TYPE, nullable=False),
        sa.Column("normalized_value", JSON_TYPE, nullable=False),
        sa.Column("source_block_ids", JSON_TYPE, nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("bbox", JSON_TYPE, nullable=True),
        sa.Column("confirmed_value", JSON_TYPE, nullable=True),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint("scope IN ('HEADER','MOLD','PAYMENT')", name="document_field_scope"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="document_field_confidence"),
        sa.CheckConstraint("page_number >= 1", name="document_field_page"),
        sa.ForeignKeyConstraint(["job_id"], ["document_ocr_job.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "scope", "row_key", "field_key", name="uq_document_extracted_field"),
    )
    op.create_index("ix_document_extracted_field_job_id", "document_extracted_field", ["job_id"])

    op.create_table(
        "contract_intake_mold_match",
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("row_key", sa.String(length=80), nullable=False),
        sa.Column("customer_mold_number", sa.String(length=100), nullable=True),
        sa.Column("machine_model", sa.String(length=120), nullable=True),
        sa.Column("material_number", sa.String(length=120), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("mold_id", sa.String(length=36), nullable=True),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint("amount IS NULL OR amount > 0", name="contract_intake_mold_amount"),
        sa.ForeignKeyConstraint(["group_id"], ["contract_intake_group.id"]),
        sa.ForeignKeyConstraint(["mold_id"], ["mold.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "row_key", name="uq_contract_intake_mold_row"),
    )
    op.create_index("ix_contract_intake_mold_match_group_id", "contract_intake_mold_match", ["group_id"])
    op.create_index("ix_contract_intake_mold_match_mold_id", "contract_intake_mold_match", ["mold_id"])

    op.add_column("contract_attachment", sa.Column("intake_file_id", sa.String(length=36), nullable=True))
    op.add_column("contract_attachment", sa.Column("role", sa.String(length=30), nullable=True))
    op.create_foreign_key(
        "fk_contract_attachment_intake_file",
        "contract_attachment", "document_intake_file", ["intake_file_id"], ["id"],
    )
    op.create_unique_constraint(
        "uq_contract_attachment_intake_file", "contract_attachment", ["intake_file_id"],
    )
    op.create_check_constraint(
        "contract_attachment_intake_role", "contract_attachment",
        "role IS NULL OR role IN ('MAIN','ATTACHMENT','STAMP_PAGE','PAYMENT_TERMS','OTHER')",
    )

    op.create_table(
        "contract_mold_line",
        sa.Column("contract_subject_id", sa.String(length=36), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("mold_id", sa.String(length=36), nullable=False),
        sa.Column("customer_mold_number", sa.String(length=100), nullable=True),
        sa.Column("machine_model", sa.String(length=120), nullable=True),
        sa.Column("material_number", sa.String(length=120), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("source_row_key", sa.String(length=80), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("line_no >= 1", name="contract_mold_line_number"),
        sa.CheckConstraint("amount IS NULL OR amount > 0", name="contract_mold_line_amount"),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["mold_id"], ["mold.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_subject_id", "line_no", name="uq_contract_mold_line_no"),
    )
    op.create_index("ix_contract_mold_line_contract_subject_id", "contract_mold_line", ["contract_subject_id"])
    op.create_index("ix_contract_mold_line_mold_id", "contract_mold_line", ["mold_id"])

    op.create_table(
        "contract_relation",
        sa.Column("source_contract_id", sa.String(length=36), nullable=False),
        sa.Column("target_contract_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=36), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("source_contract_id <> target_contract_id", name="contract_relation_not_self"),
        sa.CheckConstraint("relation_type IN ('DUPLICATE','REVISION','SUPPLEMENT','REPLACEMENT')", name="contract_relation_type"),
        sa.ForeignKeyConstraint(["source_contract_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["target_contract_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_contract_id", "target_contract_id", "relation_type", name="uq_contract_relation"),
    )
    op.create_index("ix_contract_relation_source_contract_id", "contract_relation", ["source_contract_id"])
    op.create_index("ix_contract_relation_target_contract_id", "contract_relation", ["target_contract_id"])


def downgrade():
    op.drop_index("ix_contract_relation_target_contract_id", table_name="contract_relation")
    op.drop_index("ix_contract_relation_source_contract_id", table_name="contract_relation")
    op.drop_table("contract_relation")
    op.drop_index("ix_contract_mold_line_mold_id", table_name="contract_mold_line")
    op.drop_index("ix_contract_mold_line_contract_subject_id", table_name="contract_mold_line")
    op.drop_table("contract_mold_line")
    op.drop_constraint("contract_attachment_intake_role", "contract_attachment", type_="check")
    op.drop_constraint("uq_contract_attachment_intake_file", "contract_attachment", type_="unique")
    op.drop_constraint("fk_contract_attachment_intake_file", "contract_attachment", type_="foreignkey")
    op.drop_column("contract_attachment", "role")
    op.drop_column("contract_attachment", "intake_file_id")
    op.drop_index("ix_contract_intake_mold_match_mold_id", table_name="contract_intake_mold_match")
    op.drop_index("ix_contract_intake_mold_match_group_id", table_name="contract_intake_mold_match")
    op.drop_table("contract_intake_mold_match")
    op.drop_index("ix_document_extracted_field_job_id", table_name="document_extracted_field")
    op.drop_table("document_extracted_field")
    op.drop_index("ix_document_ocr_claim", table_name="document_ocr_job")
    op.drop_index("ix_document_ocr_job_status", table_name="document_ocr_job")
    op.drop_index("ix_document_ocr_job_intake_file_id", table_name="document_ocr_job")
    op.drop_table("document_ocr_job")
    op.drop_index(
        "ix_document_recognized_page_intake_file_id",
        table_name="document_recognized_page",
    )
    op.drop_table("document_recognized_page")
    op.drop_index("ix_document_intake_file_contract_group_id", table_name="document_intake_file")
    op.drop_index("ix_document_intake_file_intake_id", table_name="document_intake_file")
    op.drop_table("document_intake_file")
    op.drop_index("ix_contract_intake_group_project_id", table_name="contract_intake_group")
    op.drop_index("ix_contract_intake_group_intake_id", table_name="contract_intake_group")
    op.drop_table("contract_intake_group")
    op.drop_index("ix_document_intake_created_by", table_name="document_intake")
    op.drop_index("ix_document_intake_conversation_id", table_name="document_intake")
    op.drop_table("document_intake")
    op.drop_constraint("payment_stage_term_days", "payment_stage", type_="check")
    op.drop_constraint("payment_stage_ratio", "payment_stage", type_="check")
    op.drop_constraint("payment_stage_amount_positive", "payment_stage", type_="check")
    op.create_check_constraint("payment_stage_amount_check", "payment_stage", "amount > 0")
    op.drop_constraint("payment_stage_sequence", "payment_stage", type_="check")
    op.drop_column("payment_stage", "term_days")
    op.drop_column("payment_stage", "ratio")
    op.drop_column("payment_stage", "sequence")
    op.drop_column("contract_detail", "external_order_number")
    op.drop_column("contract_detail", "signed_date")
