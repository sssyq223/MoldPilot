"""immutable contract business terms and association snapshots

Revision ID: mb0d0e000012
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mb0d0e000012"
down_revision = "mb0d0e000011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contract_business_terms",
        sa.Column("contract_subject_id", sa.String(length=36), nullable=False),
        sa.Column("signed_date", sa.Date(), nullable=True),
        sa.Column("delivery_due_date", sa.Date(), nullable=False),
        sa.Column("payment_method", sa.String(length=200), nullable=False),
        sa.Column("customer_rule_key", sa.String(length=80), nullable=True),
        sa.Column("customer_reference_type", sa.String(length=30), nullable=False),
        sa.Column("customer_project_number", sa.String(length=120), nullable=True),
        sa.Column("customer_order_number", sa.String(length=120), nullable=True),
        sa.Column("mapping_evidence", sa.Text(), nullable=False),
        sa.Column(
            "association_snapshot",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("recorded_by", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "customer_reference_type IN "
            "('PROJECT_NUMBER','CONTRACT_NUMBER','ORDER_NUMBER',"
            "'MANUAL_CONFIRMED','SUPPLIER_CONTRACT')",
            name="contract_customer_reference_type",
        ),
        sa.ForeignKeyConstraint(["contract_subject_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("contract_subject_id"),
    )
    op.execute(
        """
        CREATE FUNCTION protect_contract_business_terms() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'contract business terms are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER contract_business_terms_immutable "
        "BEFORE UPDATE OR DELETE ON contract_business_terms "
        "FOR EACH ROW EXECUTE FUNCTION protect_contract_business_terms()"
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS contract_business_terms_immutable "
        "ON contract_business_terms"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_contract_business_terms()")
    op.drop_table("contract_business_terms")
