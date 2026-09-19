"""Versioned customer quotations, inbound evidence and feedback.

Revision ID: m60d0e000006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "m60d0e000006"
down_revision = "m50d0e000005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "quote_inbound_record",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("received_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('UPLOAD','EMAIL','CUSTOMER_PLATFORM','OTHER')",
            name="quote_inbound_record_source_kind",
        ),
        sa.ForeignKeyConstraint(["file_id"], ["file_object.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["received_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "source_kind", "source_ref", "file_id",
            name="quote_inbound_record_unique_source_file",
        ),
    )
    op.create_index("ix_quote_inbound_record_project_id", "quote_inbound_record", ["project_id"])
    op.create_index("ix_quote_inbound_record_file_id", "quote_inbound_record", ["file_id"])

    op.create_table(
        "quotation_detail",
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("previous_id", sa.String(length=36)),
        sa.Column("quotation_number", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("preliminary_execution_mode", sa.String(length=30), nullable=False),
        sa.Column("quoted_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("promised_delivery_date", sa.Date(), nullable=False),
        sa.Column("payment_terms", sa.Text(), nullable=False),
        sa.Column("cost_amount", sa.Numeric(precision=18, scale=2)),
        sa.Column("cost_evidence", sa.Text(), nullable=False),
        sa.Column("process_analysis", sa.Text(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("duration_evidence", sa.Text(), nullable=False),
        sa.Column("supplier_quote_amount", sa.Numeric(precision=18, scale=2)),
        sa.Column("supplier_delivery_date", sa.Date()),
        sa.Column("supplier_requirements", sa.Text()),
        sa.Column("supplier_quote_evidence", sa.Text()),
        sa.Column("customer_company_snapshot", sa.String(length=200), nullable=False),
        sa.Column("customer_contact_snapshot", sa.String(length=200), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("source_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("version > 0", name="quotation_version_positive"),
        sa.CheckConstraint("quoted_amount > 0", name="quotation_amount_positive"),
        sa.CheckConstraint("cost_amount IS NULL OR cost_amount >= 0", name="quotation_cost_nonnegative"),
        sa.CheckConstraint(
            "supplier_quote_amount IS NULL OR supplier_quote_amount >= 0",
            name="quotation_supplier_amount_nonnegative",
        ),
        sa.CheckConstraint("duration_days > 0", name="quotation_duration_positive"),
        sa.CheckConstraint(
            "preliminary_execution_mode IN ('INTERNAL','FULL_OUTSOURCE')",
            name="quotation_execution_mode",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["previous_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["business_subject.id"]),
        sa.PrimaryKeyConstraint("subject_id"),
        sa.UniqueConstraint("quotation_number", "version", name="quotation_number_version_unique"),
    )

    op.create_table(
        "quotation_source_link",
        sa.Column("quotation_subject_id", sa.String(length=36), nullable=False),
        sa.Column("inbound_record_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["inbound_record_id"], ["quote_inbound_record.id"]),
        sa.ForeignKeyConstraint(["quotation_subject_id"], ["business_subject.id"]),
        sa.PrimaryKeyConstraint("quotation_subject_id", "inbound_record_id"),
    )

    op.create_table(
        "quotation_feedback",
        sa.Column("quotation_subject_id", sa.String(length=36), nullable=False),
        sa.Column("feedback_type", sa.String(length=30), nullable=False),
        sa.Column("feedback_date", sa.Date(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("recorded_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "feedback_type IN ('ACCEPTED','REJECTED','REVISION_REQUESTED','NO_RESPONSE','OTHER')",
            name="quotation_feedback_type",
        ),
        sa.ForeignKeyConstraint(["quotation_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "quotation_subject_id", "source_ref", name="quotation_feedback_unique_source"
        ),
    )
    op.create_index(
        "ix_quotation_feedback_quotation_subject_id", "quotation_feedback",
        ["quotation_subject_id"],
    )

    op.execute("""
        CREATE FUNCTION protect_quotation_evidence() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Quotation evidence is immutable; append a new version or feedback';
        END $$ LANGUAGE plpgsql
    """)
    for table in (
        "quote_inbound_record", "quotation_detail", "quotation_source_link", "quotation_feedback",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION protect_quotation_evidence()"
        )


def downgrade():
    for table in (
        "quotation_feedback", "quotation_source_link", "quotation_detail", "quote_inbound_record",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS protect_quotation_evidence()")
    op.drop_index("ix_quotation_feedback_quotation_subject_id", table_name="quotation_feedback")
    op.drop_table("quotation_feedback")
    op.drop_table("quotation_source_link")
    op.drop_table("quotation_detail")
    op.drop_index("ix_quote_inbound_record_file_id", table_name="quote_inbound_record")
    op.drop_index("ix_quote_inbound_record_project_id", table_name="quote_inbound_record")
    op.drop_table("quote_inbound_record")
