"""Complete domain columns that were added after the remote split head.

The project carried a second local migration line before it was rebased onto
the remote split repository.  This single idempotent revision keeps the
post-rebase database contract explicit and is safe for databases where the
remote alignment migration already created a subset of these fields.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mp0d0e000026"
down_revision = "mo0d0e000025"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return bool(op.get_bind().execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :table "
        "AND column_name = :column"
    ), {"table": table, "column": column}).first())


def _has_constraint(table: str, name: str) -> bool:
    return bool(op.get_bind().execute(sa.text(
        "SELECT 1 FROM pg_constraint "
        "WHERE conrelid = to_regclass(current_schema() || '.' || :table) "
        "AND conname = :name"
    ), {"table": table, "name": name}).first())


def _add_column(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def upgrade():
    _add_column("bid_intake_revision", sa.Column("customer_model_number", sa.String(200)))
    _add_column("bid_intake_revision", sa.Column("customer_material_number", sa.String(200)))

    _add_column("contract_detail", sa.Column("signed_date", sa.Date(), nullable=True))
    _add_column("contract_detail", sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"))
    _add_column("contract_business_terms", sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"))
    _add_column("contract_business_terms", sa.Column("attachment_selection", postgresql.JSONB(), nullable=True))
    _add_column("contract_receipt_evidence", sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"))
    _add_column("contract_settlement_allocation", sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"))

    if _has_constraint("contract_settlement_allocation", "contract_settlement_allocation_unique_record"):
        op.drop_constraint("contract_settlement_allocation_unique_record", "contract_settlement_allocation", type_="unique")
    if not _has_constraint("contract_settlement_allocation", "contract_settlement_allocation_unique_record"):
        op.create_unique_constraint(
            "contract_settlement_allocation_unique_record",
            "contract_settlement_allocation",
            ["target_contract_id", "material_version", "record_type", "source_record_id"],
        )

    _add_column(
        "customer_acceptance_record",
        sa.Column("previous_acceptance_id", sa.String(36), nullable=True),
    )
    if not _has_constraint("customer_acceptance_record", "fk_customer_acceptance_previous"):
        op.create_foreign_key(
            "fk_customer_acceptance_previous",
            "customer_acceptance_record",
            "customer_acceptance_record",
            ["previous_acceptance_id"],
            ["id"],
        )
    if not _has_constraint("customer_acceptance_record", "uq_customer_acceptance_previous"):
        op.create_unique_constraint("uq_customer_acceptance_previous", "customer_acceptance_record", ["previous_acceptance_id"])
    if not _has_constraint("customer_acceptance_record", "customer_acceptance_previous_recheck"):
        op.create_check_constraint(
            "customer_acceptance_previous_recheck",
            "customer_acceptance_record",
            "previous_acceptance_id IS NULL OR (acceptance_type = 'RECHECK' AND previous_acceptance_id <> id)",
        )

    _add_column("payment_stage", sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"))
    _add_column("payment_stage", sa.Column("condition_profile", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    _add_column("payment_stage", sa.Column("condition_evidence_map", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    _add_column("payment_stage", sa.Column("special_approval_reference", sa.String(160), nullable=True))

    _add_column("supplier_deduction_settlement", sa.Column("previous_deduction_id", sa.String(36), nullable=True))
    _add_column("supplier_deduction_settlement", sa.Column("customer_acceptance_id", sa.String(36), nullable=True))
    if not _has_constraint("supplier_deduction_settlement", "fk_supplier_deduction_previous"):
        op.create_foreign_key("fk_supplier_deduction_previous", "supplier_deduction_settlement", "supplier_deduction_settlement", ["previous_deduction_id"], ["id"])
    if not _has_constraint("supplier_deduction_settlement", "fk_supplier_deduction_acceptance"):
        op.create_foreign_key("fk_supplier_deduction_acceptance", "supplier_deduction_settlement", "customer_acceptance_record", ["customer_acceptance_id"], ["id"])
    if not _has_constraint("supplier_deduction_settlement", "uq_supplier_deduction_previous"):
        op.create_unique_constraint("uq_supplier_deduction_previous", "supplier_deduction_settlement", ["previous_deduction_id"])


def downgrade():
    raise RuntimeError("Post-origin domain alignment is append-only and is not safely reversible")
