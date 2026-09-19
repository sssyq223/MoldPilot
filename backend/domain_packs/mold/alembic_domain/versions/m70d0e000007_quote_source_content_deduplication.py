"""Deduplicate quotation inbound files by source identity and content.

Revision ID: m70d0e000007
"""
from alembic import op


revision = "m70d0e000007"
down_revision = "m60d0e000006"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "quote_inbound_record_unique_source_file",
        "quote_inbound_record",
        type_="unique",
    )
    op.create_unique_constraint(
        "quote_inbound_record_unique_source_content",
        "quote_inbound_record",
        ["project_id", "source_kind", "source_ref", "content_sha256"],
    )


def downgrade():
    op.drop_constraint(
        "quote_inbound_record_unique_source_content",
        "quote_inbound_record",
        type_="unique",
    )
    op.create_unique_constraint(
        "quote_inbound_record_unique_source_file",
        "quote_inbound_record",
        ["project_id", "source_kind", "source_ref", "file_id"],
    )
