"""add scoped human approval proxy delegation

Revision ID: b10e6c8a3d42
Revises: f9d5a7c3e621
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b10e6c8a3d42"
down_revision = "f9d5a7c3e621"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "approval_proxy_delegation",
        sa.Column("principal_user_id", sa.String(length=36), nullable=False),
        sa.Column("proxy_user_id", sa.String(length=36), nullable=False),
        sa.Column("process_key", sa.String(length=80), nullable=False),
        sa.Column("node_key", sa.String(length=80), nullable=False),
        sa.Column("allowed_decisions", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=36), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("principal_user_id <> proxy_user_id", name="approval_proxy_distinct_users"),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["principal_user_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["proxy_user_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["revoked_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("principal_user_id", "proxy_user_id", "process_key", "node_key"),
    )
    op.create_index("ix_approval_proxy_delegation_principal_user_id", "approval_proxy_delegation", ["principal_user_id"])
    op.create_index("ix_approval_proxy_delegation_proxy_user_id", "approval_proxy_delegation", ["proxy_user_id"])
    op.create_index("ix_approval_proxy_lookup", "approval_proxy_delegation", ["proxy_user_id", "process_key", "node_key", "active"])


def downgrade():
    op.drop_index("ix_approval_proxy_lookup", table_name="approval_proxy_delegation")
    op.drop_index("ix_approval_proxy_delegation_proxy_user_id", table_name="approval_proxy_delegation")
    op.drop_index("ix_approval_proxy_delegation_principal_user_id", table_name="approval_proxy_delegation")
    op.drop_table("approval_proxy_delegation")
