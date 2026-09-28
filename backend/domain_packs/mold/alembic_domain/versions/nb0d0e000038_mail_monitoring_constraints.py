"""Align mail ledger JSON types and the UID idempotency constraint."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "nb0d0e000038"
down_revision = "na0d0e000037"
branch_labels = None
depends_on = None


def _jsonb():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    for table, column in (
        ("mail_monitor_account", "allowed_senders"),
        ("mail_monitor_account", "keywords"),
        ("mail_message", "detail_json"),
    ):
        op.alter_column(
            table,
            column,
            existing_type=sa.JSON(),
            type_=_jsonb(),
            existing_nullable=False,
            postgresql_using=f"{column}::jsonb",
        )


def downgrade():
    for table, column in (
        ("mail_monitor_account", "allowed_senders"),
        ("mail_monitor_account", "keywords"),
        ("mail_message", "detail_json"),
    ):
        op.alter_column(
            table,
            column,
            existing_type=_jsonb(),
            type_=sa.JSON(),
            existing_nullable=False,
            postgresql_using=f"{column}::json",
        )
