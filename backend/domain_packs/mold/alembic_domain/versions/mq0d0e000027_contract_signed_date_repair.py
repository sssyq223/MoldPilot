"""Repair the contract signed-date column on databases stamped at the split head."""
from alembic import op
import sqlalchemy as sa


revision = "mq0d0e000027"
down_revision = "mp0d0e000026"
branch_labels = None
depends_on = None


def upgrade():
    exists = op.get_bind().execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'contract_detail' "
        "AND column_name = 'signed_date'"
    )).first()
    if not exists:
        op.add_column("contract_detail", sa.Column("signed_date", sa.Date(), nullable=True))


def downgrade():
    # The column is part of the current contract material model and is kept
    # during rollback to avoid erasing recorded signing evidence.
    pass
