"""replace mold-specific approval foreign keys with generic resource references

Revision ID: c4e7a2b91d60
Revises: a9d5e1f7c842
"""
from alembic import op
import sqlalchemy as sa


revision = "c4e7a2b91d60"
down_revision = "a9d5e1f7c842"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("approval_instance", sa.Column("resource_type", sa.String(80), nullable=True))
    op.add_column("approval_instance", sa.Column("resource_id", sa.String(36), nullable=True))
    op.execute("""
        UPDATE approval_instance
        SET resource_type = CASE
                WHEN request_id IS NOT NULL THEN 'purchase_request'
                ELSE 'business_subject'
            END,
            resource_id = COALESCE(request_id, subject_id)
    """)
    op.alter_column("approval_instance", "resource_type", nullable=False)
    op.alter_column("approval_instance", "resource_id", nullable=False)

    op.drop_constraint("approval_one_subject", "approval_instance", type_="check")
    op.drop_constraint("approval_instance_request_id_fkey", "approval_instance", type_="foreignkey")
    op.drop_constraint("fk_approval_subject", "approval_instance", type_="foreignkey")
    op.drop_constraint(
        "approval_instance_request_id_revision_round_no_key",
        "approval_instance",
        type_="unique",
    )
    op.drop_constraint("uq_approval_subject_round", "approval_instance", type_="unique")

    op.create_index(
        "ix_approval_instance_resource_type", "approval_instance", ["resource_type"]
    )
    op.create_index(
        "ix_approval_instance_resource_id", "approval_instance", ["resource_id"]
    )
    op.create_unique_constraint(
        "uq_approval_resource_round",
        "approval_instance",
        ["resource_type", "resource_id", "revision", "round_no"],
    )
    op.create_check_constraint(
        "approval_resource_type_required", "approval_instance", "resource_type <> ''"
    )
    op.create_check_constraint(
        "approval_resource_id_required", "approval_instance", "resource_id <> ''"
    )
    op.drop_column("approval_instance", "request_id")
    op.drop_column("approval_instance", "subject_id")

    # Valid resource types belong to the active domain-pack contract, not the
    # generic host schema.  The service validates them before persistence.
    op.drop_constraint(
        "material_binding_resource_type_check", "material_binding", type_="check"
    )


def downgrade():
    connection = op.get_bind()
    unknown = connection.scalar(sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM approval_instance
            WHERE resource_type NOT IN ('purchase_request', 'business_subject')
        )
    """))
    if unknown:
        raise RuntimeError(
            "Approval resources from another domain pack exist; downgrade is unsafe"
        )

    op.add_column("approval_instance", sa.Column("request_id", sa.String(36), nullable=True))
    op.add_column("approval_instance", sa.Column("subject_id", sa.String(36), nullable=True))
    op.execute("""
        UPDATE approval_instance
        SET request_id = CASE WHEN resource_type = 'purchase_request' THEN resource_id END,
            subject_id = CASE WHEN resource_type = 'business_subject' THEN resource_id END
    """)
    op.create_foreign_key(
        "approval_instance_request_id_fkey",
        "approval_instance", "purchase_request", ["request_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_approval_subject",
        "approval_instance", "business_subject", ["subject_id"], ["id"],
    )
    op.create_unique_constraint(
        "approval_instance_request_id_revision_round_no_key",
        "approval_instance", ["request_id", "revision", "round_no"],
    )
    op.create_unique_constraint(
        "uq_approval_subject_round",
        "approval_instance", ["subject_id", "revision", "round_no"],
    )
    op.create_check_constraint(
        "approval_one_subject",
        "approval_instance",
        "(request_id IS NULL) <> (subject_id IS NULL)",
    )

    op.drop_constraint("approval_resource_id_required", "approval_instance", type_="check")
    op.drop_constraint("approval_resource_type_required", "approval_instance", type_="check")
    op.drop_constraint("uq_approval_resource_round", "approval_instance", type_="unique")
    op.drop_index("ix_approval_instance_resource_id", table_name="approval_instance")
    op.drop_index("ix_approval_instance_resource_type", table_name="approval_instance")
    op.drop_column("approval_instance", "resource_id")
    op.drop_column("approval_instance", "resource_type")
    op.create_check_constraint(
        "material_binding_resource_type_check",
        "material_binding",
        "resource_type IN ('purchase_request','business_subject')",
    )
