"""Versioned contract attachments and immutable file links.

Revision ID: m20d0e000002
"""
from alembic import op
import sqlalchemy as sa


revision = "m20d0e000002"
down_revision = "m10d0e000001"
branch_labels = None
depends_on = None


_IMMUTABLE_TABLES = (
    "file_object",
    "contact_attachment",
    "agent_run_file",
    "contract_attachment",
)


def upgrade():
    op.create_table(
        "contract_attachment",
        sa.Column("contract_subject_id", sa.String(36), sa.ForeignKey("business_subject.id"), nullable=False),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("file_object.id"), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("previous_id", sa.String(36), sa.ForeignKey("contract_attachment.id")),
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("contract_subject_id", "document_id", "version"),
        sa.UniqueConstraint("contract_subject_id", "file_id"),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("source_kind IN ('ELECTRONIC','PAPER_SCAN','OTHER')"),
    )
    op.create_index("ix_contract_attachment_contract_subject_id", "contract_attachment", ["contract_subject_id"])
    op.create_index("ix_contract_attachment_file_id", "contract_attachment", ["file_id"])
    op.execute("""
        CREATE OR REPLACE FUNCTION protect_attachment_fact() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'File originals and attachment versions are immutable';
        END $$
    """)
    for table in _IMMUTABLE_TABLES:
        op.execute(sa.text(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}'))
        op.execute(sa.text(
            f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} '
            'FOR EACH ROW EXECUTE FUNCTION protect_attachment_fact()'
        ))


def downgrade():
    for table in _IMMUTABLE_TABLES:
        op.execute(sa.text(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}'))
    op.drop_index("ix_contract_attachment_file_id", table_name="contract_attachment")
    op.drop_index("ix_contract_attachment_contract_subject_id", table_name="contract_attachment")
    op.drop_table("contract_attachment")
    op.execute("DROP FUNCTION IF EXISTS protect_attachment_fact()")
