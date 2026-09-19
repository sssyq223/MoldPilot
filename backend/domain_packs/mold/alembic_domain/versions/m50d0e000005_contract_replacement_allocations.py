"""Contract replacement/addition lineage and settlement allocations.

Revision ID: m50d0e000005
"""
from alembic import op
import sqlalchemy as sa


revision = "m50d0e000005"
down_revision = "m40d0e000004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "contract_detail",
        sa.Column("relation_type", sa.String(length=20), nullable=False, server_default="ORIGINAL"),
    )
    op.add_column("contract_detail", sa.Column("settlement_allocation_evidence", sa.Text()))
    op.execute(
        "UPDATE contract_detail SET relation_type='REPLACEMENT', "
        "settlement_allocation_evidence='历史迁移：替代关系已存在，收付款分配待财务核对' "
        "WHERE replaces_id IS NOT NULL"
    )
    op.create_check_constraint(
        "contract_relation_type",
        "contract_detail",
        "relation_type IN ('ORIGINAL','REPLACEMENT','ADDITION')",
    )
    op.create_check_constraint(
        "contract_relation_fields",
        "contract_detail",
        "(relation_type = 'ORIGINAL' AND replaces_id IS NULL AND settlement_allocation_evidence IS NULL) "
        "OR (relation_type = 'ADDITION' AND replaces_id IS NOT NULL AND settlement_allocation_evidence IS NULL) "
        "OR (relation_type = 'REPLACEMENT' AND replaces_id IS NOT NULL AND settlement_allocation_evidence IS NOT NULL)",
    )
    op.alter_column("contract_detail", "relation_type", server_default=None)

    op.create_table(
        "contract_settlement_allocation",
        sa.Column("target_contract_id", sa.String(length=36), nullable=False),
        sa.Column("source_contract_id", sa.String(length=36), nullable=False),
        sa.Column("target_stage_id", sa.String(length=36), nullable=False),
        sa.Column("record_type", sa.String(length=30), nullable=False),
        sa.Column("source_record_id", sa.String(length=36), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount <> 0", name="contract_settlement_allocation_nonzero"),
        sa.CheckConstraint(
            "record_type IN ('CUSTOMER_RECEIPT','SUPPLIER_PAYMENT')",
            name="contract_settlement_allocation_record_type",
        ),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["source_contract_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["target_contract_id"], ["business_subject.id"]),
        sa.ForeignKeyConstraint(["target_stage_id"], ["payment_stage.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "target_contract_id", "record_type", "source_record_id",
            name="contract_settlement_allocation_unique_record",
        ),
    )
    op.create_index(
        "ix_contract_settlement_allocation_target_contract_id",
        "contract_settlement_allocation", ["target_contract_id"], unique=False,
    )
    op.create_index(
        "ix_contract_settlement_allocation_source_contract_id",
        "contract_settlement_allocation", ["source_contract_id"], unique=False,
    )
    op.create_index(
        "ix_contract_settlement_allocation_target_stage_id",
        "contract_settlement_allocation", ["target_stage_id"], unique=False,
    )
    op.create_index(
        "ix_contract_settlement_allocation_source_record_id",
        "contract_settlement_allocation", ["source_record_id"], unique=False,
    )
    op.execute("""
        CREATE FUNCTION protect_contract_settlement_allocation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Contract settlement allocations are immutable; append a correction';
        END $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER contract_settlement_allocation_immutable "
        "BEFORE UPDATE OR DELETE ON contract_settlement_allocation "
        "FOR EACH ROW EXECUTE FUNCTION protect_contract_settlement_allocation()"
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS contract_settlement_allocation_immutable "
        "ON contract_settlement_allocation"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_contract_settlement_allocation()")
    for name in (
        "ix_contract_settlement_allocation_source_record_id",
        "ix_contract_settlement_allocation_target_stage_id",
        "ix_contract_settlement_allocation_source_contract_id",
        "ix_contract_settlement_allocation_target_contract_id",
    ):
        op.drop_index(name, table_name="contract_settlement_allocation")
    op.drop_table("contract_settlement_allocation")
    op.drop_constraint("contract_relation_fields", "contract_detail", type_="check")
    op.drop_constraint("contract_relation_type", "contract_detail", type_="check")
    op.drop_column("contract_detail", "settlement_allocation_evidence")
    op.drop_column("contract_detail", "relation_type")
