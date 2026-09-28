"""Align the mapping table with the ORM by removing a redundant index."""

from alembic import op
import sqlalchemy as sa


revision = "mx0d0e000034"
down_revision = "mw0d0e000033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    exists = op.get_bind().execute(sa.text("""
        SELECT 1 FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename = 'project_erp_mapping'
          AND indexname = 'ix_project_erp_mapping_project_id'
    """)).scalar()
    if exists:
        op.drop_index("ix_project_erp_mapping_project_id", table_name="project_erp_mapping")


def downgrade() -> None:
    pass
