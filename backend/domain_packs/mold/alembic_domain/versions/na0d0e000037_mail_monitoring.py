"""Add bounded mail monitoring accounts, cursors, message ledger and documents."""

import sqlalchemy as sa
from alembic import op


revision = "na0d0e000037"
down_revision = "mz0d0e000036"
branch_labels = None
depends_on = None


def _identity_columns():
    return [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "mail_monitor_account",
        *_identity_columns(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("folder", sa.String(length=255), nullable=False, server_default="INBOX"),
        sa.Column("transport", sa.String(length=20), nullable=False, server_default="ssl"),
        sa.Column("secret_ref", sa.String(length=255), nullable=False),
        sa.Column("allowed_senders", sa.JSON(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("lookback_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DISABLED"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("name", name="uq_mail_monitor_account_name"),
    )
    op.create_table(
        "mail_monitor_cursor",
        *_identity_columns(),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("mail_monitor_account.id"), nullable=False),
        sa.Column("uid_validity", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("last_uid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=False, server_default=""),
        sa.UniqueConstraint("account_id", name="uq_mail_monitor_cursor_account"),
    )
    op.create_table(
        "mail_message",
        *_identity_columns(),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("mail_monitor_account.id"), nullable=False),
        sa.Column("uid_validity", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("subject", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("sender", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("source_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False, server_default="RECEIVED"),
        sa.Column("error_code", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("account_id", "uid_validity", "uid", name="uq_mail_message_uid"),
    )
    op.create_index("ix_mail_message_account_id", "mail_message", ["account_id"])
    op.create_table(
        "mail_document",
        *_identity_columns(),
        sa.Column("message_id", sa.String(length=36), sa.ForeignKey("mail_message.id"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("business_type", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("classification_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("file_object_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("import_status", sa.String(length=30), nullable=False, server_default="PENDING"),
    )
    op.create_index("ix_mail_document_message_id", "mail_document", ["message_id"])


def downgrade():
    op.drop_index("ix_mail_document_message_id", table_name="mail_document")
    op.drop_table("mail_document")
    op.drop_index("ix_mail_message_account_id", table_name="mail_message")
    op.drop_table("mail_message")
    op.drop_table("mail_monitor_cursor")
    op.drop_table("mail_monitor_account")
