"""formal-start materials and contract receipt dates

Revision ID: mb0d0e000011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mb0d0e000011"
down_revision = "ma0d0e000010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contract_receipt_evidence",
        sa.Column("contract_subject_id", sa.String(length=36), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("recorded_by", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("contract_subject_id"),
    )
    op.execute(
        """
        CREATE FUNCTION protect_contract_receipt_evidence() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'contract receipt evidence is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER contract_receipt_evidence_immutable "
        "BEFORE UPDATE OR DELETE ON contract_receipt_evidence "
        "FOR EACH ROW EXECUTE FUNCTION protect_contract_receipt_evidence()"
    )
    op.create_table(
        "internal_start_snapshot",
        sa.Column("start_subject_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("bid_intake_revision_id", sa.String(length=36), nullable=False),
        sa.Column(
            "linked_business",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("expected_contract_date", sa.Date(), nullable=True),
        sa.Column("frozen_by", sa.String(length=36), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bid_intake_revision_id"], ["bid_intake_revision.id"]),
        sa.ForeignKeyConstraint(["frozen_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["start_subject_id"], ["business_subject.id"]),
        sa.PrimaryKeyConstraint("start_subject_id"),
    )
    op.create_index(
        "ix_internal_start_snapshot_project_id",
        "internal_start_snapshot",
        ["project_id"],
    )
    op.create_index(
        "ix_internal_start_snapshot_bid_intake_revision_id",
        "internal_start_snapshot",
        ["bid_intake_revision_id"],
    )
    op.execute(
        """
        CREATE FUNCTION protect_internal_start_snapshot() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'internal start snapshots are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER internal_start_snapshot_immutable "
        "BEFORE UPDATE OR DELETE ON internal_start_snapshot "
        "FOR EACH ROW EXECUTE FUNCTION protect_internal_start_snapshot()"
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS internal_start_snapshot_immutable "
        "ON internal_start_snapshot"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_internal_start_snapshot()")
    op.drop_index(
        "ix_internal_start_snapshot_bid_intake_revision_id",
        table_name="internal_start_snapshot",
    )
    op.drop_index(
        "ix_internal_start_snapshot_project_id",
        table_name="internal_start_snapshot",
    )
    op.drop_table("internal_start_snapshot")
    op.execute(
        "DROP TRIGGER IF EXISTS contract_receipt_evidence_immutable "
        "ON contract_receipt_evidence"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_contract_receipt_evidence()")
    op.drop_table("contract_receipt_evidence")
