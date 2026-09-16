"""logistics routes and quotes

Revision ID: 4b9c2d7e8f10
Revises: d2f0a9b1c3e4
Create Date: 2026-09-16 12:45:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "4b9c2d7e8f10"
down_revision = "d2f0a9b1c3e4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "logistics_route",
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("route_code", sa.String(length=80), nullable=False),
        sa.Column("origin", sa.String(length=200), nullable=False),
        sa.Column("destination", sa.String(length=200), nullable=False),
        sa.Column("carrier_name", sa.String(length=150), nullable=False),
        sa.Column("vehicle_type", sa.String(length=80), nullable=False),
        sa.Column("transport_mode", sa.String(length=40), nullable=False),
        sa.Column("price_unit", sa.String(length=40), nullable=False),
        sa.Column("tax_mode", sa.String(length=30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("transport_mode IN ('TRUCK','EXPRESS','SEA','AIR','RAIL','OTHER')", name="logistics_route_transport_mode"),
        sa.CheckConstraint("tax_mode IN ('TAX_INCLUDED','TAX_EXCLUDED','UNKNOWN')", name="logistics_route_tax_mode"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("route_code"),
    )
    op.create_index(op.f("ix_logistics_route_project_id"), "logistics_route", ["project_id"], unique=False)
    op.create_table(
        "logistics_quote",
        sa.Column("route_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=True),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("settlement_for_project_id", sa.String(length=36), nullable=True),
        sa.Column("quote_evidence", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("unit_price >= 0", name="logistics_quote_nonnegative_price"),
        sa.CheckConstraint("valid_to >= valid_from", name="logistics_quote_valid_range"),
        sa.CheckConstraint("status IN ('DRAFT','SUBMITTED','EFFECTIVE','EXPIRED','CANCELLED')", name="logistics_quote_status"),
        sa.ForeignKeyConstraint(["approved_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["route_id"], ["logistics_route.id"]),
        sa.ForeignKeyConstraint(["settlement_for_project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_logistics_quote_route_id"), "logistics_quote", ["route_id"], unique=False)
    op.create_index(op.f("ix_logistics_quote_settlement_for_project_id"), "logistics_quote", ["settlement_for_project_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_logistics_quote_settlement_for_project_id"), table_name="logistics_quote")
    op.drop_index(op.f("ix_logistics_quote_route_id"), table_name="logistics_quote")
    op.drop_table("logistics_quote")
    op.drop_index(op.f("ix_logistics_route_project_id"), table_name="logistics_route")
    op.drop_table("logistics_route")
