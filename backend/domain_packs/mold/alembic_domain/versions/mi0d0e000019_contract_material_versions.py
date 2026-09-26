"""Align mold domain contract materials with shared database head.

Revision ID: mi0d0e000019
Revises: mb0d0e000012

Shared databases may already be stamped at this revision. Upgrade is written
to be idempotent for those columns/constraints so a fresh checkout can catch up
without rewriting already-applied schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "mi0d0e000019"
down_revision = "mb0d0e000012"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table "
            "AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).fetchall()
    return bool(rows)


def _has_constraint(table: str, name: str) -> bool:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conrelid = to_regclass(:table) AND conname = :name"
        ),
        {"table": f"public.{table}", "name": name},
    ).fetchall()
    return bool(rows)


def upgrade():
    jsonb = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    if not _has_column("contract_detail", "signed_date"):
        op.add_column("contract_detail", sa.Column("signed_date", sa.Date(), nullable=True))
    if not _has_column("contract_detail", "external_order_number"):
        op.add_column(
            "contract_detail",
            sa.Column("external_order_number", sa.String(length=120), nullable=True),
        )
    if not _has_column("contract_detail", "material_version"):
        op.add_column(
            "contract_detail",
            sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.alter_column("contract_detail", "material_version", server_default=None)

    if not _has_column("contract_receipt_evidence", "material_version"):
        op.add_column(
            "contract_receipt_evidence",
            sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.execute("ALTER TABLE contract_receipt_evidence DROP CONSTRAINT contract_receipt_evidence_pkey")
        op.create_primary_key(
            "contract_receipt_evidence_pkey",
            "contract_receipt_evidence",
            ["material_version", "contract_subject_id"],
        )
        op.alter_column("contract_receipt_evidence", "material_version", server_default=None)

    if not _has_column("contract_business_terms", "attachment_selection"):
        op.add_column("contract_business_terms", sa.Column("attachment_selection", jsonb, nullable=True))
    if not _has_column("contract_business_terms", "material_version"):
        op.add_column(
            "contract_business_terms",
            sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.execute("ALTER TABLE contract_business_terms DROP CONSTRAINT contract_business_terms_pkey")
        op.create_primary_key(
            "contract_business_terms_pkey",
            "contract_business_terms",
            ["material_version", "contract_subject_id"],
        )
        op.alter_column("contract_business_terms", "material_version", server_default=None)

    if not _has_column("payment_stage", "sequence"):
        op.add_column(
            "payment_stage",
            sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        )
    if not _has_column("payment_stage", "ratio"):
        op.add_column("payment_stage", sa.Column("ratio", sa.Numeric(9, 6), nullable=True))
    if not _has_column("payment_stage", "term_days"):
        op.add_column("payment_stage", sa.Column("term_days", sa.Integer(), nullable=True))
    if not _has_column("payment_stage", "material_version"):
        op.add_column(
            "payment_stage",
            sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.alter_column("payment_stage", "material_version", server_default=None)
    if not _has_column("payment_stage", "condition_profile"):
        op.add_column(
            "payment_stage",
            sa.Column("condition_profile", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        )
        op.alter_column("payment_stage", "condition_profile", server_default=None)
    if not _has_column("payment_stage", "condition_evidence_map"):
        op.add_column(
            "payment_stage",
            sa.Column(
                "condition_evidence_map",
                jsonb,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
        op.alter_column("payment_stage", "condition_evidence_map", server_default=None)
    if not _has_column("payment_stage", "special_approval_reference"):
        op.add_column(
            "payment_stage",
            sa.Column("special_approval_reference", sa.String(length=160), nullable=True),
        )
    if not _has_constraint("payment_stage", "payment_stage_sequence"):
        op.create_check_constraint("payment_stage_sequence", "payment_stage", "sequence >= 1")
    if not _has_constraint("payment_stage", "payment_stage_ratio"):
        op.create_check_constraint(
            "payment_stage_ratio",
            "payment_stage",
            "ratio IS NULL OR (ratio > 0 AND ratio <= 1)",
        )
    if not _has_constraint("payment_stage", "payment_stage_term_days"):
        op.create_check_constraint(
            "payment_stage_term_days",
            "payment_stage",
            "term_days IS NULL OR term_days >= 0",
        )

    if not _has_column("contract_settlement_allocation", "material_version"):
        op.add_column(
            "contract_settlement_allocation",
            sa.Column("material_version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.alter_column("contract_settlement_allocation", "material_version", server_default=None)
        if _has_constraint(
            "contract_settlement_allocation", "contract_settlement_allocation_unique_record"
        ):
            op.drop_constraint(
                "contract_settlement_allocation_unique_record",
                "contract_settlement_allocation",
                type_="unique",
            )
        op.create_unique_constraint(
            "contract_settlement_allocation_unique_record",
            "contract_settlement_allocation",
            ["target_contract_id", "material_version", "record_type", "source_record_id"],
        )

    op.alter_column(
        "internal_start_snapshot",
        "bid_intake_revision_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )


def downgrade():
    raise NotImplementedError("Shared-database material alignment is not safely reversible")
