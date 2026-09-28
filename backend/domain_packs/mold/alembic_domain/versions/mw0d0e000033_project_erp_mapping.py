"""Store a human-confirmed Agent project to ERP project mapping."""

from alembic import op
import sqlalchemy as sa


revision = "mw0d0e000033"
down_revision = "mv0d0e000032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_erp_mapping",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("erp_project_code", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.String(length=300), nullable=False),
        sa.Column("source_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=36), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="CONFIRMED"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="project_erp_mapping_project_unique"),
        sa.UniqueConstraint("erp_project_code", name="project_erp_mapping_erp_code_unique"),
        sa.CheckConstraint("status IN ('CONFIRMED','REVOKED')", name="project_erp_mapping_status"),
    )
    op.create_index("ix_project_erp_mapping_project_id", "project_erp_mapping", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_erp_mapping_project_id", table_name="project_erp_mapping")
    op.drop_table("project_erp_mapping")
