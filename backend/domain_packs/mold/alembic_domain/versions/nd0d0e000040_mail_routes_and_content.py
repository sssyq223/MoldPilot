"""Add mailbox/folder routes and durable mail content metadata."""
import sqlalchemy as sa
from alembic import op

revision = "nd0d0e000040"
down_revision = "nc0d0e000039"
branch_labels = None
depends_on = None


def _identity():
    return [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "mail_monitor_route",
        *_identity(),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("mail_monitor_account.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("folder", sa.String(length=255), nullable=False, server_default="INBOX"),
        sa.Column("direction", sa.String(length=20), nullable=False, server_default="INBOX"),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("matcher", sa.JSON(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notify_inbox", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("archive", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("account_id", "name", name="uq_mail_monitor_route_name"),
        sa.CheckConstraint(
            "category IN ('QUOTATION','BID_AWARDED','CONSTRUCTION_START','PROJECT_KICKOFF')",
            name="ck_mail_monitor_route_category",
        ),
        sa.CheckConstraint("direction IN ('INBOX','SENT','CUSTOM')", name="ck_mail_monitor_route_direction"),
    )
    op.create_index("ix_mail_monitor_route_account_id", "mail_monitor_route", ["account_id"])

    with op.batch_alter_table("mail_monitor_cursor") as batch:
        batch.drop_constraint("uq_mail_monitor_cursor_account", type_="unique")
        batch.add_column(sa.Column("folder", sa.String(length=255), nullable=False, server_default="INBOX"))
        batch.create_unique_constraint("uq_mail_monitor_cursor_account_folder", ["account_id", "folder"])

    with op.batch_alter_table("mail_message") as batch:
        batch.add_column(sa.Column("route_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("folder", sa.String(length=255), nullable=False, server_default="INBOX"))
        batch.add_column(sa.Column("direction", sa.String(length=20), nullable=False, server_default="INBOX"))
        batch.add_column(sa.Column("category", sa.String(length=40), nullable=False, server_default=""))
        batch.add_column(sa.Column("category_status", sa.String(length=30), nullable=False, server_default="UNMATCHED"))
        batch.add_column(sa.Column("plain_body", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("html_body", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("raw_file_object_id", sa.String(length=36), nullable=False, server_default=""))
        batch.create_foreign_key("fk_mail_message_route", "mail_monitor_route", ["route_id"], ["id"])
        batch.create_index("ix_mail_message_route_id", ["route_id"])

    with op.batch_alter_table("mail_document") as batch:
        batch.add_column(sa.Column("preview_status", sa.String(length=30), nullable=False, server_default="AVAILABLE"))
        batch.add_column(sa.Column("extracted_text", sa.Text(), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("mail_document") as batch:
        batch.drop_column("extracted_text")
        batch.drop_column("preview_status")
    with op.batch_alter_table("mail_message") as batch:
        batch.drop_index("ix_mail_message_route_id")
        batch.drop_constraint("fk_mail_message_route", type_="foreignkey")
        for column in ("raw_file_object_id", "html_body", "plain_body", "category_status", "category", "direction", "folder", "route_id"):
            batch.drop_column(column)
    with op.batch_alter_table("mail_monitor_cursor") as batch:
        batch.drop_constraint("uq_mail_monitor_cursor_account_folder", type_="unique")
        batch.drop_column("folder")
        batch.create_unique_constraint("uq_mail_monitor_cursor_account", ["account_id"])
    op.drop_index("ix_mail_monitor_route_account_id", table_name="mail_monitor_route")
    op.drop_table("mail_monitor_route")

