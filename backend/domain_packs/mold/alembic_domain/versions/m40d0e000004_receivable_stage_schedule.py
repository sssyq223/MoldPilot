"""Structured customer-receivable stage schedule.

Revision ID: m40d0e000004
"""
from alembic import op
import sqlalchemy as sa


revision = "m40d0e000004"
down_revision = "m30d0e000003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment_stage", sa.Column("ratio_percent", sa.Numeric(7, 4)))
    op.add_column("payment_stage", sa.Column("trigger_event", sa.String(length=120)))
    op.add_column("payment_stage", sa.Column("trigger_date", sa.Date()))
    op.add_column("payment_stage", sa.Column("credit_days", sa.Integer()))
    op.add_column("payment_stage", sa.Column("expected_due_date", sa.Date()))
    op.add_column("payment_stage", sa.Column("schedule_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("payment_stage", sa.Column("schedule_evidence", sa.Text()))
    op.add_column("payment_stage", sa.Column("trigger_evidence", sa.Text()))
    op.add_column("payment_stage", sa.Column("special_mark", sa.String(length=200)))
    op.create_check_constraint("payment_stage_ratio_percent", "payment_stage", "ratio_percent IS NULL OR (ratio_percent > 0 AND ratio_percent <= 100)")
    op.create_check_constraint("payment_stage_credit_days", "payment_stage", "credit_days IS NULL OR credit_days >= 0")
    op.create_check_constraint("payment_stage_due_after_trigger", "payment_stage", "trigger_date IS NULL OR expected_due_date IS NULL OR expected_due_date >= trigger_date")
    op.create_check_constraint("payment_stage_confirmed_schedule_fields", "payment_stage", "NOT schedule_confirmed OR (trigger_event IS NOT NULL AND schedule_evidence IS NOT NULL AND (credit_days IS NOT NULL OR expected_due_date IS NOT NULL))")
    op.create_check_constraint("payment_stage_trigger_evidence", "payment_stage", "trigger_date IS NULL OR trigger_evidence IS NOT NULL")
    op.alter_column("payment_stage", "schedule_confirmed", server_default=None)


def downgrade():
    op.drop_constraint("payment_stage_trigger_evidence", "payment_stage", type_="check")
    op.drop_constraint("payment_stage_confirmed_schedule_fields", "payment_stage", type_="check")
    op.drop_constraint("payment_stage_due_after_trigger", "payment_stage", type_="check")
    op.drop_constraint("payment_stage_credit_days", "payment_stage", type_="check")
    op.drop_constraint("payment_stage_ratio_percent", "payment_stage", type_="check")
    for column in (
        "special_mark", "trigger_evidence", "schedule_evidence", "schedule_confirmed",
        "expected_due_date", "credit_days", "trigger_date", "trigger_event", "ratio_percent",
    ):
        op.drop_column("payment_stage", column)
