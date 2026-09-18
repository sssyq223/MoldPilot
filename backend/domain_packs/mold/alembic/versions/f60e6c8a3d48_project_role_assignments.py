"""add project-scoped workflow role assignments

Revision ID: f60e6c8a3d48
Revises: f50e6c8a3d47
"""
from alembic import op
import sqlalchemy as sa


revision = "f60e6c8a3d48"
down_revision = "f50e6c8a3d47"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_role_config",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "project_role_member",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("role_key", sa.String(length=60), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "role_key", "user_id"),
    )
    op.create_index("ix_project_role_member_project_id", "project_role_member", ["project_id"])
    op.create_index("ix_project_role_member_role_key", "project_role_member", ["role_key"])
    op.create_index("ix_project_role_member_user_id", "project_role_member", ["user_id"])
    op.create_index(
        "ix_project_role_member_lookup", "project_role_member", ["project_id", "role_key"]
    )


def downgrade():
    op.drop_index("ix_project_role_member_lookup", table_name="project_role_member")
    op.drop_index("ix_project_role_member_user_id", table_name="project_role_member")
    op.drop_index("ix_project_role_member_role_key", table_name="project_role_member")
    op.drop_index("ix_project_role_member_project_id", table_name="project_role_member")
    op.drop_table("project_role_member")
    op.drop_table("project_role_config")
