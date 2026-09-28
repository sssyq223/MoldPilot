"""Repair contract external order number on databases stamped before origin alignment."""

from alembic import op
import sqlalchemy as sa


revision = "mr0d0e000028"
down_revision = "mq0d0e000027"
branch_labels = None
depends_on = None


def _has_column(connection, table_name: str, column_name: str) -> bool:
    return bool(connection.execute(sa.text("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = :table_name
          AND column_name = :column_name
    """), {"table_name": table_name, "column_name": column_name}).scalar())


def upgrade() -> None:
    connection = op.get_bind()
    if not _has_column(connection, "contract_detail", "external_order_number"):
        op.add_column(
            "contract_detail",
            sa.Column("external_order_number", sa.String(length=120), nullable=True),
        )


def downgrade() -> None:
    # Forward repair is intentionally append-only. Existing deployments may
    # already contain business data in this column.
    pass
