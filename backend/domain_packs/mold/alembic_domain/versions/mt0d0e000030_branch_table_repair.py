"""Repair databases stamped at the merged head without branch-owned tables.

Some legacy Agent databases were stamped at ``mo0d0e000025`` after the branch
merge but never received the bid/start/local-change tables from revisions
014-017.  Replaying those idempotent revision bodies here restores the schema
without changing business rows.  Fresh installs already have the tables and
therefore skip each replay.
"""

from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa


revision = "mt0d0e000030"
down_revision = "ms0d0e000029"
branch_labels = None
depends_on = None


def _has_table(connection, table_name: str) -> bool:
    return bool(connection.execute(sa.text("""
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = :table_name
    """), {"table_name": table_name}).scalar())


def _replay(filename: str) -> None:
    module = runpy.run_path(str(Path(__file__).with_name(filename)))
    module["upgrade"]()


def upgrade() -> None:
    connection = op.get_bind()
    if not _has_table(connection, "document_intake"):
        _replay("mb0d0e000013_sales_contract_pdf_intake.py")
    if not _has_table(connection, "bid_notice_match"):
        _replay("mb0d0e000014_bid_start_workflow.py")
    if not _has_table(connection, "admin_start_notice_draft"):
        _replay("mb0d0e000015_admin_start_workflow.py")
    if not _has_table(connection, "start_notice_department_ack"):
        _replay("mb0d0e000016_admin_start_departments.py")
    if not _has_table(connection, "local_change_intake"):
        _replay("mb0d0e000017_local_change_intake.py")


def downgrade() -> None:
    # Repair is append-only; removing these tables would discard local facts.
    pass
