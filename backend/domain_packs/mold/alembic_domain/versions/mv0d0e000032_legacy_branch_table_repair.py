"""Repair stamped databases missing the document-intake branch tables."""

from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa


revision = "mv0d0e000032"
down_revision = "mu0d0e000031"
branch_labels = None
depends_on = None


def _has_table(connection, table_name: str) -> bool:
    return bool(connection.execute(sa.text("""
        SELECT 1 FROM information_schema.tables
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
    if not _has_table(connection, "admin_start_department_ack"):
        _replay("mb0d0e000016_admin_start_departments.py")
    if not _has_table(connection, "local_change_intake"):
        _replay("mb0d0e000017_local_change_intake.py")


def downgrade() -> None:
    pass
