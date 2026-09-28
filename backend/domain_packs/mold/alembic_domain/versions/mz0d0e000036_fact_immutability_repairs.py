"""Repair missing database guards on confirmed finance and deduction facts."""

from alembic import op


revision = "mz0d0e000036"
down_revision = "my0d0e000035"
branch_labels = None
depends_on = None


def upgrade():
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
    op.execute("""
    CREATE OR REPLACE FUNCTION protect_published_material_template()
    RETURNS trigger AS $$
    BEGIN
        IF OLD.status = 'PUBLISHED' THEN
            RAISE EXCEPTION 'Published material template is immutable';
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    DROP TRIGGER IF EXISTS material_template_immutable ON material_template;
    CREATE TRIGGER material_template_immutable BEFORE UPDATE OR DELETE ON material_template
    FOR EACH ROW EXECUTE FUNCTION protect_published_material_template();
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS supplier_deduction_settlement_immutable ON supplier_deduction_settlement")
    op.execute("DROP FUNCTION IF EXISTS protect_supplier_deduction_settlement()")
