"""logistics quote supersession history

Revision ID: a9d5e1f7c842
Revises: f8c4d2a6b731
Create Date: 2026-09-17 15:45:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "a9d5e1f7c842"
down_revision = "f8c4d2a6b731"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "logistics_quote",
        sa.Column("supersedes_quote_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "logistics_quote_supersedes_quote_id_fkey",
        "logistics_quote",
        "logistics_quote",
        ["supersedes_quote_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "logistics_quote_supersedes_quote_id_fkey",
        "logistics_quote",
        type_="foreignkey",
    )
    op.drop_column("logistics_quote", "supersedes_quote_id")
