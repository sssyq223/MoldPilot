"""logistics route and quote governance

Revision ID: f8c4d2a6b731
Revises: e7b3c1d5a920
Create Date: 2026-09-17 15:20:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "f8c4d2a6b731"
down_revision = "e7b3c1d5a920"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("logistics_route", sa.Column("source_ref", sa.String(length=120), nullable=True))
    op.add_column("logistics_route", sa.Column("confirmed_by", sa.String(length=36), nullable=True))
    op.add_column("logistics_route", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("logistics_route", sa.Column("weight_kg", sa.Numeric(18, 3), nullable=True))
    op.add_column("logistics_route", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("logistics_route", sa.Column("valid_to", sa.Date(), nullable=True))
    op.create_foreign_key("logistics_route_confirmed_by_fkey", "logistics_route", "app_user", ["confirmed_by"], ["id"])
    op.create_check_constraint("logistics_route_positive_weight", "logistics_route", "weight_kg IS NULL OR weight_kg > 0")
    op.create_check_constraint(
        "logistics_route_valid_range", "logistics_route",
        "valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from",
    )
    op.create_unique_constraint("logistics_route_unique_source", "logistics_route", ["source_ref"])

    op.add_column("logistics_quote", sa.Column("pricing_method", sa.String(length=30), server_default="LEGACY", nullable=False))
    op.add_column("logistics_quote", sa.Column("comparison_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("logistics_quote", sa.Column("source_ref", sa.String(length=120), nullable=True))
    op.add_column("logistics_quote", sa.Column("created_by", sa.String(length=36), nullable=True))
    op.add_column("logistics_quote", sa.Column("comparison_summary", sa.Text(), nullable=True))
    op.add_column("logistics_quote", sa.Column("reconciliation_basis", sa.Text(), nullable=True))
    op.create_foreign_key("logistics_quote_created_by_fkey", "logistics_quote", "app_user", ["created_by"], ["id"])
    op.create_check_constraint(
        "logistics_quote_pricing_method",
        "logistics_quote",
        "pricing_method IN ('LEGACY','FIXED_ROUTE','COMPETITIVE','NEGOTIATED','SINGLE_SOURCE')",
    )
    op.create_check_constraint("logistics_quote_comparison_count_nonnegative", "logistics_quote", "comparison_count >= 0")
    op.create_unique_constraint("logistics_quote_unique_source", "logistics_quote", ["route_id", "source_ref"])


def downgrade():
    op.drop_constraint("logistics_quote_unique_source", "logistics_quote", type_="unique")
    op.drop_constraint("logistics_quote_comparison_count_nonnegative", "logistics_quote", type_="check")
    op.drop_constraint("logistics_quote_pricing_method", "logistics_quote", type_="check")
    op.drop_constraint("logistics_quote_created_by_fkey", "logistics_quote", type_="foreignkey")
    op.drop_column("logistics_quote", "reconciliation_basis")
    op.drop_column("logistics_quote", "comparison_summary")
    op.drop_column("logistics_quote", "created_by")
    op.drop_column("logistics_quote", "source_ref")
    op.drop_column("logistics_quote", "comparison_count")
    op.drop_column("logistics_quote", "pricing_method")

    op.drop_constraint("logistics_route_unique_source", "logistics_route", type_="unique")
    op.drop_constraint("logistics_route_valid_range", "logistics_route", type_="check")
    op.drop_constraint("logistics_route_positive_weight", "logistics_route", type_="check")
    op.drop_constraint("logistics_route_confirmed_by_fkey", "logistics_route", type_="foreignkey")
    op.drop_column("logistics_route", "valid_to")
    op.drop_column("logistics_route", "valid_from")
    op.drop_column("logistics_route", "weight_kg")
    op.drop_column("logistics_route", "confirmed_at")
    op.drop_column("logistics_route", "confirmed_by")
    op.drop_column("logistics_route", "source_ref")
