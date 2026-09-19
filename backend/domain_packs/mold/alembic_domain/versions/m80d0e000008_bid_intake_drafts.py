"""Stable, versioned bid intake drafts and lifecycle links.

Revision ID: m80d0e000008
"""
from alembic import op
import sqlalchemy as sa


revision = "m80d0e000008"
down_revision = "m70d0e000007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bid_intake_case",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bid_intake_case_project_id", "bid_intake_case", ["project_id"], unique=True)

    op.create_table(
        "bid_intake_revision",
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_revision_id", sa.String(length=36)),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("customer_classification", sa.String(length=30), nullable=False),
        sa.Column("classification_evidence", sa.Text(), nullable=False),
        sa.Column("classification_confirmed_by", sa.String(length=36), nullable=False),
        sa.Column("customer_company", sa.String(length=200), nullable=False),
        sa.Column("customer_contact", sa.String(length=200), nullable=False),
        sa.Column("customer_mold_number", sa.String(length=120)),
        sa.Column("customer_model_or_material", sa.String(length=200)),
        sa.Column("project_name_snapshot", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2)),
        sa.Column("currency", sa.String(length=3)),
        sa.Column("our_recipient", sa.String(length=200), nullable=False),
        sa.Column("external_order_number", sa.String(length=120)),
        sa.Column("external_start_date", sa.Date()),
        sa.Column("customer_due_date", sa.Date()),
        sa.Column("matched_quotation_subject_id", sa.String(length=36)),
        sa.Column("historical_mold_number", sa.String(length=120)),
        sa.Column("historical_relation_kind", sa.String(length=30)),
        sa.Column("match_result", sa.String(length=30), nullable=False),
        sa.Column("match_evidence", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="bid_intake_revision_version_positive"),
        sa.CheckConstraint("amount IS NULL OR amount > 0", name="bid_intake_revision_amount_positive"),
        sa.CheckConstraint(
            "(amount IS NULL AND currency IS NULL) OR (amount IS NOT NULL AND currency IS NOT NULL)",
            name="bid_intake_revision_amount_currency_pair",
        ),
        sa.CheckConstraint(
            "source_kind IN ('UPLOAD','EMAIL','CUSTOMER_PLATFORM','OTHER')",
            name="bid_intake_revision_source_kind",
        ),
        sa.CheckConstraint(
            "customer_classification IN ('HISENSE','HAIER','OTHER')",
            name="bid_intake_revision_customer_classification",
        ),
        sa.CheckConstraint(
            "match_result IN ('MATCHED','PARTIAL','UNMATCHED','MANUAL')",
            name="bid_intake_revision_match_result",
        ),
        sa.CheckConstraint(
            "historical_relation_kind IS NULL OR historical_relation_kind IN ('BACKUP','REFERENCE')",
            name="bid_intake_revision_historical_relation_kind",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["bid_intake_case.id"]),
        sa.ForeignKeyConstraint(["classification_confirmed_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["matched_quotation_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["previous_revision_id"], ["bid_intake_revision.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "source_fingerprint", name="bid_intake_revision_unique_fingerprint"),
        sa.UniqueConstraint("case_id", "version", name="bid_intake_revision_case_version"),
    )
    op.create_index("ix_bid_intake_revision_case_id", "bid_intake_revision", ["case_id"])

    op.create_table(
        "bid_intake_attachment",
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('BID_NOTICE','EXTERNAL_START_NOTICE','CONTRACT_REFERENCE','MOLD_IMAGE','OTHER')",
            name="bid_intake_attachment_role",
        ),
        sa.ForeignKeyConstraint(["file_id"], ["file_object.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["bid_intake_revision.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "file_id", "role", name="bid_intake_attachment_unique"),
    )
    op.create_index("ix_bid_intake_attachment_file_id", "bid_intake_attachment", ["file_id"])
    op.create_index("ix_bid_intake_attachment_revision_id", "bid_intake_attachment", ["revision_id"])

    op.create_table(
        "bid_intake_lifecycle_link",
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("link_kind", sa.String(length=30), nullable=False),
        sa.Column("linked_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "link_kind IN ('ACCEPTANCE','REJECTION','INTERNAL_START')",
            name="bid_intake_lifecycle_link_kind",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["bid_intake_case.id"]),
        sa.ForeignKeyConstraint(["linked_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["business_subject.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "link_kind", "subject_id", name="bid_intake_lifecycle_link_unique"),
        sa.UniqueConstraint("subject_id"),
    )
    op.create_index("ix_bid_intake_lifecycle_link_case_id", "bid_intake_lifecycle_link", ["case_id"])

    op.execute("""
        CREATE FUNCTION protect_bid_intake_evidence() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Bid intake evidence is append-only';
        END $$ LANGUAGE plpgsql
    """)
    for table in (
        "bid_intake_case", "bid_intake_revision", "bid_intake_attachment",
        "bid_intake_lifecycle_link",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION protect_bid_intake_evidence()"
        )


def downgrade():
    for table in (
        "bid_intake_lifecycle_link", "bid_intake_attachment", "bid_intake_revision",
        "bid_intake_case",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS protect_bid_intake_evidence()")
    op.drop_index("ix_bid_intake_lifecycle_link_case_id", table_name="bid_intake_lifecycle_link")
    op.drop_table("bid_intake_lifecycle_link")
    op.drop_index("ix_bid_intake_attachment_revision_id", table_name="bid_intake_attachment")
    op.drop_index("ix_bid_intake_attachment_file_id", table_name="bid_intake_attachment")
    op.drop_table("bid_intake_attachment")
    op.drop_index("ix_bid_intake_revision_case_id", table_name="bid_intake_revision")
    op.drop_table("bid_intake_revision")
    op.drop_index("ix_bid_intake_case_project_id", table_name="bid_intake_case")
    op.drop_table("bid_intake_case")
