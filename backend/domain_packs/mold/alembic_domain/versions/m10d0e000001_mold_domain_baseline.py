"""MoldPilot domain baseline

Revision ID: m10d0e000001
"""
from hashlib import sha256
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision = "m10d0e000001"
down_revision = None
branch_labels = ("mold_domain",)
depends_on = None

_SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "mold_domain_baseline.sql"
_SQL_SHA256 = "55fd341943748d42691b8124bfc09d1c5213f14d98322f7ae78196e7639ee8ce"
_TABLES = (
    "customer", "material", "mold", "project", "supplier", "warehouse",
    "business_subject", "contact_case", "erp_identity", "logistics_route", "project_mold", "project_profile",
    "project_role_config", "project_role_member", "purchase_request", "risk_analysis_result", "risk_policy", "stock_balance",
    "assembly_detail", "assembly_execution", "business_decision_detail", "contact_record", "contact_resolution", "contact_task",
    "contract_detail", "customer_delivery_signature", "design_detail", "engineering_change_detail", "erp_operation", "logistics_quote",
    "pause_record", "payment_confirmation", "payment_stage", "plan_department_confirmation", "plan_detail", "plan_task",
    "price_detail", "project_closure_case", "project_pause_detail", "purchase_order", "purchase_request_line", "stock_movement",
    "trial_detail", "trial_result", "change_impact", "contact_attachment", "contract_signing_record", "customer_acceptance_record",
    "customer_receipt_confirmation", "design_item", "finance_correction_detail", "outsource_change_negotiation", "pause_task_shift", "payment_request_detail",
    "project_closure_detail", "project_closure_item", "purchase_order_line", "supplier_deduction_settlement", "supplier_material_handoff", "supplier_progress_policy",
    "supplier_progress_report", "task_dependency", "delivery_exception", "order_price_snapshot", "project_closure_item_revision", "supplier_material_verification",
    "supplier_shipment", "goods_receipt", "receipt_inspection",
)


def _statements():
    source = _SQL_PATH.read_text(encoding="utf-8")
    if sha256(source.encode()).hexdigest() != _SQL_SHA256:
        raise RuntimeError("Mold domain baseline SQL checksum mismatch")
    return [part.strip() for part in source.split(";\n\n") if part.strip()]


def upgrade():
    bind = op.get_bind()
    installed = set(bind.execute(sa.text("""
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = current_schema()
    """)).scalars())
    existing = installed.intersection(_TABLES)
    if existing:
        missing = sorted(set(_TABLES) - installed)
        if missing:
            raise RuntimeError(
                "Cannot adopt a partial mold domain schema; missing: " + ", ".join(missing)
            )
        return
    for statement in _statements():
        op.execute(sa.text(statement))


def downgrade():
    for table in reversed(_TABLES):
        op.drop_table(table)
