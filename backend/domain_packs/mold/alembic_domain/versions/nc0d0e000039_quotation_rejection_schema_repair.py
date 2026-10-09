"""Merge domain heads and align quotation rejection with the business model.

The earlier split migration installed a different, project-based rejection
table. Preserve that table as an archive instead of inventing business subjects
for historical rows. New decisions use the subject-based model.
"""

from alembic import op
import sqlalchemy as sa


revision = "nc0d0e000039"
down_revision = ("mb0d0e000018", "nb0d0e000038")
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("quotation_rejection")}
    if "subject_id" not in columns:
        archive = "quotation_rejection_legacy"
        if inspector.has_table(archive):
            raise RuntimeError("Quotation rejection archive already exists; review before migrating")
        # Renaming keeps historical rows and their original foreign keys intact.
        # Rename the PK constraint too, since its backing index shares the schema
        # namespace with the new table's primary key.
        primary_key = inspector.get_pk_constraint("quotation_rejection")["name"]
        op.rename_table("quotation_rejection", archive)
        if primary_key:
            quote = connection.dialect.identifier_preparer.quote
            op.execute(sa.text(
                f"ALTER TABLE {quote(archive)} RENAME CONSTRAINT {quote(primary_key)} "
                f"TO {quote(archive + '_pkey')}"
            ))
        op.create_table(
            "quotation_rejection",
            sa.Column("subject_id", sa.String(36), nullable=False),
            sa.Column("quotation_subject_id", sa.String(36), nullable=False),
            sa.Column("rejection_category", sa.String(30), nullable=False),
            sa.Column("rejection_reason", sa.Text(), nullable=False),
            sa.Column("decision_date", sa.Date(), nullable=False),
            sa.Column("decided_by", sa.String(36), nullable=False),
            sa.Column("evidence", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
            sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
            sa.CheckConstraint(
                "rejection_category IN ('PRICE_TOO_LOW', 'TIMELINE_IMPOSSIBLE', "
                "'TECHNICAL_DIFFICULTY', 'CAPACITY_SHORTAGE', 'CUSTOMER_CREDIT', "
                "'MATERIAL_SHORTAGE', 'RESOURCE_CONFLICT', 'PROFIT_MARGIN_LOW', 'OTHER')",
                name="quotation_rejection_category_check",
            ),
            sa.ForeignKeyConstraint(["subject_id"], ["business_subject.id"]),
            sa.ForeignKeyConstraint(["quotation_subject_id"], ["business_subject.id"]),
            sa.ForeignKeyConstraint(["decided_by"], ["app_user.id"]),
            sa.PrimaryKeyConstraint("subject_id"),
        )
        op.create_index(
            "ix_quotation_rejection_quotation_subject_id", "quotation_rejection",
            ["quotation_subject_id"],
        )

    change_columns = {
        column["name"] for column in sa.inspect(connection).get_columns("engineering_change_detail")
    }
    if "is_minor_change" not in change_columns:
        op.add_column(
            "engineering_change_detail",
            sa.Column("is_minor_change", sa.Integer(), server_default="0", nullable=False),
        )
    checks = {
        constraint["name"] for constraint in sa.inspect(connection).get_check_constraints(
            "engineering_change_detail"
        )
    }
    if "engineering_change_is_minor_change" not in checks:
        op.create_check_constraint(
            "engineering_change_is_minor_change", "engineering_change_detail",
            "is_minor_change IN (0, 1)",
        )


def downgrade():
    raise RuntimeError("This repair preserves business history; use a forward migration")
