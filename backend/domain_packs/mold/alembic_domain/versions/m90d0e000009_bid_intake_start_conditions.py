"""Customer process/start conditions frozen into bid intake revisions.

Revision ID: m90d0e000009
"""
from alembic import op
import sqlalchemy as sa


revision = "m90d0e000009"
down_revision = "m80d0e000008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bid_intake_revision",
        sa.Column("customer_process_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "bid_intake_revision",
        sa.Column("customer_process_confirmation_evidence", sa.Text()),
    )
    op.create_check_constraint(
        "bid_intake_revision_process_confirmation",
        "bid_intake_revision",
        "(customer_process_confirmed AND "
        "customer_process_confirmation_evidence IS NOT NULL AND "
        "length(trim(customer_process_confirmation_evidence)) > 0) OR "
        "(NOT customer_process_confirmed AND "
        "customer_process_confirmation_evidence IS NULL)",
    )
    op.alter_column("bid_intake_revision", "customer_process_confirmed", server_default=None)
    op.add_column(
        "bid_intake_lifecycle_link",
        sa.Column("source_revision_id", sa.String(length=36)),
    )
    op.create_foreign_key(
        "bid_intake_lifecycle_link_source_revision_id_fkey",
        "bid_intake_lifecycle_link",
        "bid_intake_revision",
        ["source_revision_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "bid_intake_lifecycle_link_source_revision_id_fkey",
        "bid_intake_lifecycle_link",
        type_="foreignkey",
    )
    op.drop_column("bid_intake_lifecycle_link", "source_revision_id")
    op.drop_constraint(
        "bid_intake_revision_process_confirmation",
        "bid_intake_revision",
        type_="check",
    )
    op.drop_column("bid_intake_revision", "customer_process_confirmation_evidence")
    op.drop_column("bid_intake_revision", "customer_process_confirmed")
