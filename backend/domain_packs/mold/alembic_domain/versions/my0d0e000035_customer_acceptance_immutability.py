"""Protect confirmed customer acceptance facts from in-place edits."""

from alembic import op


revision = "my0d0e000035"
down_revision = "mx0d0e000034"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION protect_customer_acceptance_record()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'customer acceptance facts are immutable';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    DROP TRIGGER IF EXISTS customer_acceptance_record_immutable
    ON customer_acceptance_record;
    CREATE TRIGGER customer_acceptance_record_immutable
    BEFORE UPDATE OR DELETE ON customer_acceptance_record
    FOR EACH ROW EXECUTE FUNCTION protect_customer_acceptance_record();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION protect_supplier_deduction_settlement()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'supplier deduction facts are immutable';
    END;
    $$ LANGUAGE plpgsql;
    DROP TRIGGER IF EXISTS supplier_deduction_settlement_immutable
    ON supplier_deduction_settlement;
    CREATE TRIGGER supplier_deduction_settlement_immutable
    BEFORE UPDATE OR DELETE ON supplier_deduction_settlement
    FOR EACH ROW EXECUTE FUNCTION protect_supplier_deduction_settlement();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION protect_business_fact()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'Confirmed business facts are immutable; append a correction record';
    END;
    $$ LANGUAGE plpgsql;
    DROP TRIGGER IF EXISTS immutable_fact ON payment_confirmation;
    CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON payment_confirmation
    FOR EACH ROW EXECUTE FUNCTION protect_business_fact();
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS customer_acceptance_record_immutable ON customer_acceptance_record")
    op.execute("DROP FUNCTION IF EXISTS protect_customer_acceptance_record()")
    op.execute("DROP TRIGGER IF EXISTS supplier_deduction_settlement_immutable ON supplier_deduction_settlement")
    op.execute("DROP FUNCTION IF EXISTS protect_supplier_deduction_settlement()")
