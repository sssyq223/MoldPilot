"""Repair columns introduced by the contract material workflow on older test schemas."""

from alembic import op
import sqlalchemy as sa


revision = "ms0d0e000029"
down_revision = "mr0d0e000028"
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
    if not _has_column(connection, "payment_stage", "sequence"):
        op.add_column("payment_stage", sa.Column("sequence", sa.Integer(), nullable=True, server_default="1"))
    if not _has_column(connection, "payment_stage", "ratio"):
        op.add_column("payment_stage", sa.Column("ratio", sa.Numeric(9, 6), nullable=True))
    if not _has_column(connection, "payment_stage", "term_days"):
        op.add_column("payment_stage", sa.Column("term_days", sa.Integer(), nullable=True))
    if not _has_column(connection, "contract_attachment", "intake_file_id"):
        op.add_column("contract_attachment", sa.Column("intake_file_id", sa.String(36), nullable=True))
    if not _has_column(connection, "contract_attachment", "role"):
        op.add_column("contract_attachment", sa.Column("role", sa.String(30), nullable=True))


def downgrade() -> None:
    pass
