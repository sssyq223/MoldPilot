"""Repair the admin-start department acknowledgement table on stamped databases."""

from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa


revision = "mu0d0e000031"
down_revision = "mt0d0e000030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    exists = connection.execute(sa.text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = 'admin_start_department_ack'
    """)).scalar()
    if not exists:
        module = runpy.run_path(str(Path(__file__).with_name("mb0d0e000016_admin_start_departments.py")))
        module["upgrade"]()


def downgrade() -> None:
    pass
