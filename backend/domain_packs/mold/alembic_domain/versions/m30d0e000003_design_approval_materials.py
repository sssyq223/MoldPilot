"""ERP design-order evidence and immutable approval attachments.

Revision ID: m30d0e000003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "m30d0e000003"
down_revision = "m20d0e000002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("design_detail", sa.Column("source_system", sa.String(80)))
    op.add_column("design_detail", sa.Column("source_resource_type", sa.String(80)))
    op.add_column("design_detail", sa.Column("source_resource_id", sa.String(160)))
    op.add_column("design_detail", sa.Column("source_resource_version", sa.String(200)))
    op.add_column("design_detail", sa.Column("source_as_of", sa.DateTime(timezone=True)))
    op.add_column("design_detail", sa.Column("source_snapshot_hash", sa.String(64)))
    op.add_column("design_detail", sa.Column("source_summary", postgresql.JSONB(astext_type=sa.Text())))
    op.add_column("design_detail", sa.Column("source_snapshot", postgresql.JSONB(astext_type=sa.Text())))
    op.create_index("ix_design_detail_source_resource", "design_detail", ["source_system", "source_resource_type", "source_resource_id"])
    op.create_table(
        "design_attachment",
        sa.Column("design_subject_id", sa.String(36), sa.ForeignKey("business_subject.id"), nullable=False),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("file_object.id"), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("previous_id", sa.String(36), sa.ForeignKey("design_attachment.id")),
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("design_subject_id", "document_id", "version"),
        sa.UniqueConstraint("design_subject_id", "file_id"),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("source_kind IN ('CHAT_UPLOAD','ERP_EXPORT','OTHER')"),
    )
    op.create_index("ix_design_attachment_design_subject_id", "design_attachment", ["design_subject_id"])
    op.create_index("ix_design_attachment_file_id", "design_attachment", ["file_id"])
    op.execute(
        "CREATE TRIGGER design_attachment_immutable BEFORE UPDATE OR DELETE ON design_attachment "
        "FOR EACH ROW EXECUTE FUNCTION protect_attachment_fact()"
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS design_attachment_immutable ON design_attachment")
    op.drop_index("ix_design_attachment_file_id", table_name="design_attachment")
    op.drop_index("ix_design_attachment_design_subject_id", table_name="design_attachment")
    op.drop_table("design_attachment")
    op.drop_index("ix_design_detail_source_resource", table_name="design_detail")
    for column in (
        "source_snapshot", "source_summary", "source_snapshot_hash", "source_as_of",
        "source_resource_version", "source_resource_id", "source_resource_type", "source_system",
    ):
        op.drop_column("design_detail", column)
